import ast
import logging
import smtplib
import ssl

import pytest
import yaml
from source_scan import calls_to, placeholders

from intake import alerts, mail
from intake import destinations as destinations_module
from intake.alerts import ALERTS
from intake.errors import ConfigError, DeliveryError


def message():
    return mail.Email(to="office@example.com", subject="Subject", body="Body")


@pytest.mark.parametrize(
    "fault, value, reason",
    [
        ("connect_error", TimeoutError("slow"), "the connection timed out"),
        ("connect_error", OSError(111, "Connection refused"), "Connection refused"),
        ("login_error", smtplib.SMTPAuthenticationError(535, b"bad credentials"), "the server rejected the SMTP login"),
        ("refused", {"bad@example.com": (550, b"no such user")}, "the server refused the recipient address"),
    ],
    ids=["timeout", "refused-connection", "rejected-login", "refused-recipient"],
)
def test_send_failure(app_context, fake_smtp, fault, value, reason):
    setattr(fake_smtp, fault, value)

    with pytest.raises(DeliveryError) as caught:
        message().send()

    assert str(caught.value) == f"Could not send email to office@example.com through smtp.test:587: {reason}"


def test_own_smtp_host_defaults_to_starttls(make_app, fake_smtp):
    app_instance = make_app()

    with app_instance.app.app_context():
        message().send()

    assert (fake_smtp.ssl_connections, fake_smtp.starttls_calls) == (0, 1)


def test_from_address(make_app, fake_smtp):
    login_only = make_app()
    with login_only.app.app_context():
        message().send()
    assert fake_smtp.sent[-1].message["From"] == "alerts@example.com"

    with_from = make_app(config={"smtp_from": "forms@example.com"})
    with with_from.app.app_context():
        message().send()
    assert fake_smtp.sent[-1].message["From"] == "forms@example.com"


def test_login_that_is_not_an_address_needs_a_from_address(make_app):
    with pytest.raises(ConfigError, match="The SMTP login 'apikey' isn't an email address"):
        make_app(config={"smtp_user": "apikey"})

    make_app(config={"smtp_user": "apikey", "smtp_from": "forms@example.com"})


def test_invalid_smtp_port(make_app, fake_smtp):
    with pytest.raises(ConfigError, match="The setting 'smtp_port' has the value 'abc'"):
        make_app(config={"smtp_port": "abc"})


@pytest.mark.parametrize(
    "security, ssl_connections, starttls_calls",
    [("starttls", 0, 1), ("ssl", 1, 0), ("none", 0, 0)],
)
def test_smtp_security(make_app, fake_smtp, security, ssl_connections, starttls_calls):
    app_instance = make_app(config={"smtp_security": security})

    with app_instance.app.app_context():
        message().send()

    assert (fake_smtp.ssl_connections, fake_smtp.starttls_calls) == (ssl_connections, starttls_calls)
    assert len(fake_smtp.sent) == 1
    assert all(context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname for context in fake_smtp.contexts) is True
    assert len(fake_smtp.contexts) == ssl_connections + starttls_calls


@pytest.mark.parametrize(
    "config, ssl_connections, starttls_calls",
    [
        ({"smtp_port": "465"}, 1, 0),
        ({"smtp_port": "587"}, 0, 1),
        ({"smtp_port": "465", "smtp_security": "starttls"}, 0, 1),
    ],
    ids=["port-465-is-implicit-tls", "other-ports-use-starttls", "explicit-setting-wins"],
)
def test_default_security_follows_port(make_app, fake_smtp, config, ssl_connections, starttls_calls):
    app_instance = make_app(config=config)

    with app_instance.app.app_context():
        message().send()

    assert (fake_smtp.ssl_connections, fake_smtp.starttls_calls) == (ssl_connections, starttls_calls)


def test_invalid_smtp_security(make_app, fake_smtp):
    with pytest.raises(ConfigError, match="The setting 'smtp_security' has the value 'tls'"):
        make_app(config={"smtp_security": "tls"})


NO_SMTP = {"remove_config_keys": ["smtp_host", "smtp_port", "smtp_user"], "secrets": {"smtp_password": ""}}


@pytest.mark.parametrize(
    "config, destinations, fragment",
    [
        ({"alert_emails_enabled": "yes"}, {"default": {"type": "zulip", "channel_id": 7, "topic": "New wbzp-intake submission"}}, "alert emails are turned on"),
        (
            {"alert_emails_enabled": "no"},
            {"default": {"type": "zulip", "channel_id": 7, "topic": "New wbzp-intake submission"}, "office": {"type": "email", "address": ["a@example.com"]}},
            "destination 'office' sends email",
        ),
    ],
    ids=["alerts-without-smtp", "email-destination-without-smtp"],
)
def test_smtp_required_when_used(make_app, config, destinations, fragment):
    with pytest.raises(ConfigError, match=f"because {fragment} but the SMTP settings are incomplete"):
        make_app(config=config, destinations=destinations, **NO_SMTP)


EMAIL_DESTINATIONS = {
    "default": {"type": "zulip", "channel_id": 7, "topic": "New wbzp-intake submission"},
    "office": {"type": "email", "address": ["a@example.com"]},
}


@pytest.mark.parametrize(
    "config, secrets, fragment",
    [
        ({"bounce_source": ""}, {}, "bounce_source in instance/config.conf must be 'imap'"),
        ({"bounce_source": "pigeon"}, {}, "The setting 'bounce_source' has the value 'pigeon'"),
        ({"bounce_imap_host": ""}, {}, "the IMAP settings are incomplete"),
        ({}, {"smtp_password": ""}, "the SMTP settings are incomplete"),
    ],
    ids=["no-bounce-source", "unknown-bounce-source", "imap-without-host", "no-smtp-password"],
)
def test_email_destination_needs_bounce_support(make_app, config, secrets, fragment):
    with pytest.raises(ConfigError, match=fragment):
        make_app(config={"alert_emails_enabled": "no", **config}, secrets=secrets, destinations=EMAIL_DESTINATIONS)


def test_external_bounce_source_needs_no_imap(make_app):
    make_app(
        config={"alert_emails_enabled": "no", "bounce_source": "external", "bounce_imap_host": ""},
        destinations=EMAIL_DESTINATIONS,
    )


@pytest.mark.parametrize(
    "destinations",
    [{"default": {"type": "zulip", "channel_id": 7, "topic": "New wbzp-intake submission"}}, yaml.safe_load(destinations_module.default_file_text())],
    ids=["zulip-only", "placeholder-default"],
)
def test_smtp_not_required_when_unused(make_app, destinations):
    make_app(config={"alert_emails_enabled": "no"}, destinations=destinations, **NO_SMTP)


def test_disabled_alerts(make_app, fake_smtp, caplog):
    app_instance = make_app(config={"alert_emails_enabled": "no"})

    with caplog.at_level(logging.INFO), app_instance.app.app_context():
        alerts.notify_admin("submission_failed", {"email": "<script>alert(1)</script>@example.com"})

    assert fake_smtp.sent == []
    assert "submission_failed" in caplog.text
    assert "<script>" not in caplog.text


def alert_sites():
    for filename, node in calls_to({"notify_admin"}):
        key = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
        fields = set()
        if len(node.args) > 1 and isinstance(node.args[1], ast.Dict):
            fields = {item.value for item in node.args[1].keys if isinstance(item, ast.Constant)}
        yield filename, node.lineno, key, fields


def test_alert_keys():
    sites = list(alert_sites())
    problems = [
        f"{filename}:{line} {key!r}"
        for filename, line, key, fields in sites
        if key not in ALERTS or fields != placeholders(ALERTS[key])
    ]

    assert problems == []
    assert set(ALERTS) - {key for _, _, key, _ in sites} == set()
    assert len(sites) >= 8
