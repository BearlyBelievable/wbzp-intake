import re

import pytest
import requests
import responses

from intake import zulip_setup
from intake.errors import DeliveryError


@pytest.mark.parametrize(
    "typed, normalized",
    [
        ("chat.example.com", "https://chat.example.com"),
        ("  http://chat.example.com/  ", "http://chat.example.com"),
        ("https://chat.example.com///", "https://chat.example.com"),
    ],
)
def test_normalize_site_url(typed, normalized):
    assert zulip_setup.normalize_site_url(typed) == normalized


@pytest.mark.parametrize(
    "bot_email, suggestion", [("bot@chat.example.com", "https://chat.example.com"), ("not-an-email", "")]
)
def test_suggest_site_url(bot_email, suggestion):
    assert zulip_setup.suggest_site_url(bot_email) == suggestion


TYPED = "zulip.example.com"


REALM = "https://chat.example.com"


def member(email, is_bot=False):
    return {"is_bot": is_bot, "delivery_email": email}


def zulip_server(mock_http, realm_key="realm_url", realm=REALM + "/", members=None):
    mock_http.add(responses.GET, f"https://{TYPED}/api/v1/server_settings", json={realm_key: realm})
    mock_http.add(
        responses.GET,
        f"{REALM}/api/v1/users",
        json={"members": [member("bot@zulip.test", True), member("iago@example.com")] if members is None else members},
    )


def resolve(**kwargs):
    return zulip_setup.resolve_site_url(TYPED, "bot@zulip.test", "bot-key", **kwargs)


def test_canonical_site_url(mock_http):
    zulip_server(mock_http)

    assert resolve() == REALM


def test_legacy_realm_uri(mock_http):
    zulip_server(mock_http, realm_key="realm_uri")

    assert resolve() == REALM


@pytest.mark.parametrize(
    "members",
    [[member("bot@zulip.test", True)], [member(None, True), member("iago@example.com"), member(None)]],
    ids=["no-other-members", "some-visible"],
)
def test_visible_email_addresses(mock_http, members):
    zulip_server(mock_http, members=members)

    assert resolve() == REALM


def test_hidden_email_addresses(mock_http):
    zulip_server(mock_http, members=[member("bot@zulip.test", True), member(None), member(None)])

    with pytest.raises(DeliveryError, match="can't see members' real email addresses"):
        resolve()


def test_visibility_is_not_checked_for_manage_py_lookups(mock_http):
    zulip_server(mock_http, members=[member(None)])

    assert resolve(check_visibility=False) == REALM


@pytest.mark.parametrize(
    "setup, message",
    [
        (
            lambda mock_http: zulip_server(mock_http, realm=""),
            "is a Zulip server but not a single organization on it",
        ),
        (
            lambda mock_http: mock_http.add(responses.GET, f"https://{TYPED}/api/v1/server_settings", body="<html>hi</html>"),
            "doesn't look like a Zulip server",
        ),
        (
            lambda mock_http: mock_http.add(responses.GET, f"https://{TYPED}/api/v1/server_settings", json=["a", "list"]),
            "doesn't look like a Zulip server",
        ),
        (
            lambda mock_http: mock_http.add(
                responses.GET, f"https://{TYPED}/api/v1/server_settings", body=requests.ConnectionError()
            ),
            "The Zulip API request failed (server settings): the connection failed",
        ),
    ],
    ids=["no-organization", "not-json", "json-list", "unreachable"],
)
def test_unusable_site_url(mock_http, setup, message):
    setup(mock_http)

    with pytest.raises(DeliveryError, match=re.escape(message)):
        resolve()


def test_detect_channels(mock_http):
    mock_http.add(
        responses.GET,
        f"{REALM}/api/v1/users/me/subscriptions",
        json={
            "subscriptions": [
                {"name": "zebra", "invite_only": False, "stream_id": 3},
                {"name": "Alpha", "invite_only": True, "stream_id": 1},
                {"name": "beta", "invite_only": False, "stream_id": 2},
            ]
        },
    )

    assert zulip_setup.detect_channels(REALM, "bot@zulip.test", "bot-key") == [
        ("Alpha (private)", 1),
        ("beta", 2),
        ("zebra", 3),
    ]


@pytest.mark.parametrize(
    "settings, expected",
    [
        ("EMAIL_HOST = 'smtp.zulip.test'\nEMAIL_PORT = 465\nEMAIL_USE_SSL = True\n", ("smtp.zulip.test", 465, "ssl")),
        ("EMAIL_HOST = 'smtp.zulip.test'\nEMAIL_PORT = 587\nEMAIL_USE_TLS = True\n", ("smtp.zulip.test", 587, "starttls")),
        ("EMAIL_HOST = 'smtp.zulip.test'\nEMAIL_PORT = 25\nEMAIL_USE_TLS = False\n", ("smtp.zulip.test", 25, "none")),
        ("EMAIL_HOST = 'smtp.zulip.test'\nOTHER = compute()\n", ("smtp.zulip.test", "", "none")),
        ("OTHER = 1\n", ("", "", "none")),
    ],
    ids=["ssl", "starttls", "plain", "port-missing-and-computed-value-ignored", "no-email-settings"],
)
def test_read_email_server(tmp_path, settings, expected):
    path = tmp_path / "settings.py"
    path.write_text(settings)

    assert zulip_setup.read_email_server(path) == expected


def test_read_email_server_unreadable(tmp_path):
    path = tmp_path / "settings.py"
    path.write_text("this is = not python (\n")

    with pytest.raises(DeliveryError, match="Could not read Zulip's settings"):
        zulip_setup.read_email_server(path)
    path.unlink()
    with pytest.raises(DeliveryError, match="Could not read Zulip's settings"):
        zulip_setup.read_email_server(path)


def test_zulip_email_cli(run_cli, tmp_path, monkeypatch):
    path = tmp_path / "settings.py"
    path.write_text("EMAIL_HOST = 'smtp.zulip.test'\nEMAIL_PORT = 465\nEMAIL_USE_SSL = True\n")
    monkeypatch.setattr(zulip_setup, "ZULIP_SETTINGS_PATH", str(path))

    assert run_cli("zulip_email_cli.py") == (0, "smtp.zulip.test|465|ssl\n", "")

    path.unlink()
    code, _, _ = run_cli("zulip_email_cli.py")
    assert code != 0
