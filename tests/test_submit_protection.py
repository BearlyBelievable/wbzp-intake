import urllib.parse

import pytest
import requests
import responses
from fake_services import TURNSTILE_URL


@pytest.mark.parametrize(
    "config, statuses",
    [
        ({}, [400] * 10 + [429]),
        ({"max_attempts_per_ip": "1"}, [400, 429, 429, 429]),
        ({"rate_limit_window_minutes": "0"}, [400, 400, 400, 400]),
    ],
    ids=["default-ten", "configured-one", "window-expired"],
)
def test_ip_rate_limit(make_app, config, statuses):
    app_instance = make_app(config=config)

    results = [app_instance.apply(full_name="") for _ in statuses]

    assert [response.status_code for response in results] == statuses
    if statuses[-1] == 429:
        assert results[-1].get_json()["error"] == app_instance.message("common", "rate_limited")


def test_ip_limit_is_per_ip(app_instance, fake_zulip):
    for _ in range(11):
        app_instance.apply(full_name="")

    assert app_instance.apply(ip="203.0.113.9").status_code == 200


def test_email_rate_limit(app_instance, fake_zulip):
    statuses = [app_instance.apply(ip=f"203.0.113.{n}").status_code for n in range(1, 5)]

    assert statuses == [200, 400, 400, 429]
    assert len(fake_zulip.posts) == 1


@pytest.mark.parametrize(
    "token, cloudflare, expected_status, rate_limit_rows",
    [
        ("good-token", {"json": {"success": True}}, 200, 1),
        ("bad-token", {"json": {"success": False}}, 400, 0),
        (None, None, 400, 0),
        ("any-token", {"body": "not json"}, 503, 0),
        ("any-token", {"body": requests.ConnectionError("down")}, 503, 0),
        ("any-token", {"json": {}}, 400, 0),
        ("any-token", {"body": RuntimeError("weird")}, 503, 0),
    ],
    ids=["verified", "rejected", "missing-token", "garbled-reply", "unreachable", "no-success-field", "unexpected-error"],
)
def test_turnstile(make_app, mock_http, fake_zulip, token, cloudflare, expected_status, rate_limit_rows):
    app_instance = make_app(secrets={"turnstile_secret": "shh"})
    if cloudflare is not None:
        mock_http.add(responses.POST, TURNSTILE_URL, **cloudflare)
    posted = {"cf-turnstile-response": token} if token else {}

    response = app_instance.apply(**posted)

    assert response.status_code == expected_status
    assert app_instance.query("SELECT COUNT(*) AS n FROM submission_attempts")[0]["n"] == rate_limit_rows
    if token:
        sent = dict(urllib.parse.parse_qsl(mock_http.calls[0].request.body))
        assert sent == {"secret": "shh", "response": token, "remoteip": "198.51.100.1"}
    else:
        assert [call.request.url for call in mock_http.calls] == []
    if expected_status == 400:
        assert response.get_json()["error"] == app_instance.message("common", "verification_failed")
    if expected_status == 503:
        assert response.get_json()["error"] == app_instance.message("common", "server_busy")


def test_clear_rate_limit(app_instance, fake_zulip):
    for _ in range(10):
        app_instance.apply(full_name="")
    app_instance.apply(ip="203.0.113.9", full_name="")
    assert app_instance.apply(full_name="").status_code == 429

    result = app_instance.invoke("clear-rate-limit", "--ip", "198.51.100.1")

    assert result.exit_code == 0
    assert "Cleared 10 rate limit record(s) for 198.51.100.1." in result.output
    assert app_instance.apply(full_name="").status_code == 400
    assert app_instance.query("SELECT COUNT(*) AS n FROM submission_attempts WHERE ip = '203.0.113.9'")[0]["n"] == 1


def test_clear_rate_limit_by_email(app_instance, fake_zulip):
    app_instance.apply(submitter_email="Sam@Example.com", ip="203.0.113.5")
    app_instance.apply(submitter_email="other@example.com", ip="203.0.113.6")
    count_for = "SELECT COUNT(*) AS n FROM submission_attempts WHERE email = ?"
    assert app_instance.query(count_for, ("sam@example.com",))[0]["n"] == 1

    result = app_instance.invoke("clear-rate-limit", "--email", "SAM@example.com")

    assert result.exit_code == 0
    assert "Cleared 1 rate limit record(s) for SAM@example.com." in result.output
    assert app_instance.query(count_for, ("sam@example.com",))[0]["n"] == 0
    assert app_instance.query(count_for, ("other@example.com",))[0]["n"] == 1


def test_clear_all_rate_limits(app_instance, fake_zulip):
    for ip in ("203.0.113.1", "203.0.113.2"):
        app_instance.apply(ip=ip, full_name="")

    result = app_instance.invoke("clear-rate-limit")

    assert result.exit_code == 0
    assert "Cleared 2 rate limit record(s)." in result.output
    assert app_instance.query("SELECT COUNT(*) AS n FROM submission_attempts")[0]["n"] == 0


def test_clear_rate_limit_rejects_both_options(app_instance):
    result = app_instance.invoke("clear-rate-limit", "--ip", "198.51.100.1", "--email", "a@example.com")

    assert result.exit_code == 2
    assert "Use only one of --ip and --email." in result.output
