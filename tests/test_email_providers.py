import json

import pytest

from intake import email_providers
from intake.mail import SMTP_SECURITY_MODES

REGION_PLACEHOLDER = "{region}"


@pytest.fixture(scope="module")
def data():
    return email_providers.load()


def test_every_provider_is_complete(data):
    flow_ids = {flow["id"] for flow in data["flows"]}
    provider_ids = [provider["id"] for provider in data["providers"]]

    assert len(provider_ids) == len(set(provider_ids))
    for provider in data["providers"]:
        assert provider["flow"] in flow_ids
        assert provider["smtp_security"] in SMTP_SECURITY_MODES
        assert isinstance(provider["smtp_port"], int)
        assert provider["smtp_host"]
        assert provider["password_prompt"]
        assert provider.get("smtp_user") or provider["login_prompt"]
        if REGION_PLACEHOLDER in provider["smtp_host"]:
            assert provider["region_prompt"]
        if provider["flow"] == "account":
            assert provider["imap_host"]


def test_every_flow_has_providers_and_a_bounce_route(data):
    for flow in data["flows"]:
        assert flow["bounces"] in ("imap", "external")
        assert (flow["bounces"] == "external") == bool(flow["bounce_note"])
        assert email_providers.providers_in_flow(data, flow["id"])


def test_lookup(data):
    assert email_providers.lookup(data, "provider", "sendgrid", "smtp_user") == "apikey"
    assert email_providers.lookup(data, "provider", "sendgrid", "from_required") == "yes"
    assert email_providers.lookup(data, "provider", "google", "from_required") == ""
    assert email_providers.lookup(data, "flow", "service", "bounces") == "external"
    with pytest.raises(KeyError):
        email_providers.lookup(data, "provider", "nowhere", "smtp_host")


def test_cli(run_cli):
    code, out, _ = run_cli("email_providers_cli.py", "flows")
    assert code == 0
    assert out.splitlines()[0].startswith("account\t")

    assert run_cli("email_providers_cli.py", "provider", "mailgun_eu", "smtp_host")[1] == "smtp.eu.mailgun.org\n"
    assert "ses\tAmazon SES" in run_cli("email_providers_cli.py", "providers", "service")[1].splitlines()
    assert run_cli("email_providers_cli.py", "provider", "nowhere", "smtp_host")[0] != 0


def test_shipped_file_is_plain_json():
    json.loads(email_providers.PROVIDERS_PATH.read_text(encoding="utf-8"))
