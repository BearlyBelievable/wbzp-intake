import base64
import re
import subprocess

import pytest
import requests
import responses
from fake_services import ZULIP_URL

from intake import zulip_integration, zulip_manage
from intake.errors import DeliveryError


BOT_AUTH = "Basic " + base64.b64encode(b"bot@zulip.test:bot-key").decode()


EMAIL = "a@example.com"


@pytest.mark.parametrize(
    "users, lookup_status, expected",
    [
        ({EMAIL: True}, None, "registered"),
        ({}, None, "none"),
        ({}, 404, "none"),
        ({}, 400, "none"),
        ({EMAIL: False}, None, "none"),
    ],
    ids=["active-user", "unknown-user", "unknown-404", "unknown-400", "deactivated-user"],
)
def test_email_status(app_context, fake_zulip, users, lookup_status, expected):
    fake_zulip.users.update(users)
    fake_zulip.lookup_status = lookup_status

    assert zulip_integration.check_submission_email(EMAIL) == expected


def test_lookup_rejected_with_other_400(app_context, fake_zulip):
    fake_zulip.lookup_status = 400
    fake_zulip.lookup_message = "Invalid API key"

    with pytest.raises(DeliveryError) as caught:
        zulip_integration.check_submission_email(EMAIL)

    assert str(caught.value) == "The Zulip API request failed (user lookup): the server answered HTTP 400"


def test_bot_authentication(app_context, mock_http, fake_zulip):
    fake_zulip.users["a+b@example.com"] = True

    assert zulip_integration.check_submission_email("a+b@example.com") == "registered"
    zulip_integration.post_to_zulip("Topic", "Body", 7)

    lookup, post = (call.request for call in mock_http.calls)
    assert lookup.url == f"{ZULIP_URL}/api/v1/users/a%2Bb%40example.com"
    assert lookup.headers["Authorization"] == post.headers["Authorization"] == BOT_AUTH
    assert fake_zulip.posts == [{"type": "stream", "to": "7", "topic": "Topic", "content": "Body"}]


API_FAILURES = [
    ({"status": 401}, "the server answered HTTP 401"),
    ({"status": 500}, "the server answered HTTP 500"),
    ({"body": requests.ConnectionError()}, "the connection failed"),
    ({"body": requests.Timeout()}, "the request timed out"),
]


API_FAILURE_IDS = ["rejected-credentials", "server-error", "unreachable", "timeout"]


@pytest.mark.parametrize("response, reason", API_FAILURES, ids=API_FAILURE_IDS)
def test_lookup_failure(app_context, mock_http, response, reason):
    mock_http.add(responses.GET, re.compile(f"{ZULIP_URL}/api/v1/users/.*"), **response)

    with pytest.raises(DeliveryError) as caught:
        zulip_integration.check_submission_email(EMAIL)

    assert str(caught.value) == f"The Zulip API request failed (user lookup): {reason}"


@pytest.mark.parametrize("response, reason", API_FAILURES, ids=API_FAILURE_IDS)
def test_post_failure(app_context, mock_http, response, reason):
    mock_http.add(responses.POST, f"{ZULIP_URL}/api/v1/messages", **response)

    with pytest.raises(DeliveryError) as caught:
        zulip_integration.post_to_zulip("Topic", "Body", 7)

    assert str(caught.value) == f"Could not post to Zulip channel 7: {reason}"


@pytest.mark.parametrize("status", ["registered", "invited", "none"])
def test_manage_py_status(make_app, fake_zulip, fake_manage_py, status):
    app_instance = make_app(config={"zulip_lookup": "manage_py", "zulip_manage_py": __file__})
    fake_manage_py.statuses[EMAIL] = status

    with app_instance.app.app_context():
        assert zulip_integration.check_submission_email("A@Example.com") == status

    assert fake_zulip.lookups == 0
    (call,) = fake_manage_py.calls
    assert call["command"] == [__file__, "shell"]
    assert call["email"] == "A@Example.com"
    assert "filter_to_valid_prereg_users" in call["input"]


@pytest.mark.parametrize(
    "stdout, stderr, error, reason",
    [
        ("RESULT:maybe\n", "", None, "it printed no result"),
        ("", "Traceback\nValueError: boom\n", None, "ValueError: boom"),
        (None, "", OSError(13, "Permission denied"), "Permission denied"),
        (None, "", subprocess.TimeoutExpired("manage.py", 30), "timed out after 30"),
    ],
    ids=["unknown-status", "script-crashed", "cannot-run", "timeout"],
)
def test_manage_py_failure(make_app, fake_manage_py, stdout, stderr, error, reason):
    app_instance = make_app(config={"zulip_lookup": "manage_py", "zulip_manage_py": __file__})
    fake_manage_py.stdout = stdout
    fake_manage_py.stderr = stderr
    fake_manage_py.error = error

    with app_instance.app.app_context(), pytest.raises(DeliveryError, match="Could not check Zulip's database") as caught:
        zulip_integration.check_submission_email(EMAIL)

    assert reason in str(caught.value)


def test_manage_py_default_path(app_context):
    assert zulip_manage.manage_py_path() == "/home/zulip/deployments/current/manage.py"
