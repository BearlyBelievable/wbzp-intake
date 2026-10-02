import pytest
from app_harness import form_text


ZULIP_BODY = "\n\n".join(
    [
        "**Form:** application",
        "**What's your name or preferred alias?**\nSam Tester",
        "**What email address should we send your invite to?**\nsam@example.com",
        "**How old are you?**\n30",
        "**What's your interest in joining?**\nMember",
        "**Community guidelines**\nI agree to follow the community guidelines",
        "**Which of these are you interested in?**\n- Discussion\n- Events",
        "**Tell us a bit about yourself.**\n_(user did not specify)_",
    ]
)


def test_zulip_flow(app_instance, fake_zulip):
    response = app_instance.apply(interests=["Discussion", "Events"])

    assert response.status_code == 200
    assert response.get_json() == {"success": True}
    assert fake_zulip.posts == [
        {"type": "stream", "to": "7", "topic": "New wbzp-intake submission", "content": ZULIP_BODY}
    ]
    assert app_instance.submissions() == [{"email": "sam@example.com", "status": "awaiting_signup"}]

    repeat = app_instance.apply()
    assert repeat.status_code == 400
    assert repeat.get_json()["error"] == app_instance.message("zulip", "pending", contact_email="admin@example.com")
    assert len(fake_zulip.posts) == 1

    fake_zulip.users["sam@example.com"] = True
    assert app_instance.invoke("check-pending").exit_code == 0
    assert app_instance.submissions() == []


def test_existing_account(app_instance, fake_zulip):
    fake_zulip.users["sam@example.com"] = True

    response = app_instance.apply()

    assert response.status_code == 400
    assert response.get_json()["error"] == app_instance.message("zulip", "registered")
    assert fake_zulip.posts == []
    assert app_instance.submissions() == []


@pytest.mark.parametrize("state", ["registered", "invited"])
def test_existing_account_or_invite_through_manage_py(make_app, fake_zulip, fake_manage_py, state):
    app_instance = make_app(config={"zulip_lookup": "manage_py", "zulip_manage_py": __file__})
    fake_manage_py.statuses["sam@example.com"] = state

    response = app_instance.apply()

    assert response.status_code == 400
    assert response.get_json()["error"] == app_instance.message("zulip", state)
    assert fake_zulip.posts == []
    assert fake_zulip.lookups == 0
    assert app_instance.submissions() == []


def test_email_destination(make_app, fake_smtp, fake_zulip):
    app_instance = make_app(forms={"office_contact": form_text("office_contact")})

    response = app_instance.post(
        "office_contact", submitter_email="visitor@example.com", topic="Billing", subscribe="true", channels=["Email", "SMS"], message=""
    )

    assert response.status_code == 200
    (mail,) = fake_smtp.sent
    assert mail.recipients == ["office@example.com", "audit@example.com"]
    assert mail.message["To"] == "office@example.com"
    assert mail.message["From"] == "alerts@example.com"
    assert mail.message["Subject"] == "Office request (office_contact)"
    assert mail.message["Reply-To"] == "visitor@example.com"
    assert mail.message.get_content().strip() == "\n\n".join(
        [
            "Your email\nvisitor@example.com",
            "Topic\nBilling",
            "Subscribe to updates\nYes",
            "Channels\n- Email\n- SMS",
            "Message\n(not answered)",
        ]
    )
    assert fake_zulip.lookups == 0
    assert app_instance.submissions() == []

    assert app_instance.post("office_contact", submitter_email="visitor@example.com", topic="Billing").status_code == 200
    assert len(fake_smtp.sent) == 2


def test_answer_normalization(app_instance, fake_zulip):
    response = app_instance.apply(
        full_name="  Sam   *the*  Tester ",
        submitter_email="Sam@Example.COM",
        role_interest="Other",
        role_interest_details="Fixing   things",
        bio="line one is long enough\nline two\x00 here",
        unrelated_field="ignored",
    )

    assert response.status_code == 200
    content = fake_zulip.posts[0]["content"]
    assert "**What's your name or preferred alias?**\nSam \\*the\\* Tester" in content
    assert "**What email address should we send your invite to?**\nSam@Example.COM" in content
    assert "Fixing things" in content
    assert "line one is long enough\nline two here" in content
    assert "ignored" not in content
    assert app_instance.submissions() == [{"email": "sam@example.com", "status": "awaiting_signup"}]

    app_instance.apply(submitter_email="other@example.com", role_interest="Member", role_interest_details="not asked")
    assert "not asked" not in fake_zulip.posts[1]["content"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"full_name": ""},
        {"full_name": "   "},
        {"submitter_email": "not-an-email"},
        {"submitter_email": "a" * 250 + "@example.com"},
        {"age": "thirty"},
        {"age": "9" * 5000},
        {"age": "12"},
        {"age": "121"},
        {"role_interest": "Hacker"},
        {"role_interest": "Other"},
        {"agree_to_rules": ""},
        {"interests": ["Gardening"]},
        {"bio": "too short"},
        {"bio": "x" * 501},
    ],
    ids=lambda overrides: ",".join(f"{key}={str(value)[:12]}" for key, value in overrides.items()),
)
def test_invalid_answers(app_instance, fake_zulip, overrides):
    response = app_instance.apply(**overrides)

    assert response.status_code == 400
    assert response.get_json()["error"] == app_instance.message("common", "invalid_answers")
    assert fake_zulip.lookups == 0
    assert fake_zulip.posts == []
    assert app_instance.submissions() == []


@pytest.mark.parametrize(
    "answers_posted, expected",
    [
        ({"age": "15", "topic": "Sales"}, "youth@example.com"),
        ({"age": "70", "topic": "Sales"}, "senior@example.com"),
        ({"age": "30", "topic": "Press"}, "press@example.com"),
        ({"age": "30", "topic": "Legal"}, "press@example.com"),
        ({"age": "30", "topic": "Support"}, "general@example.com"),
        ({"age": "30", "topic": "Sales"}, "zulip channel 7"),
        ({"age": "30", "topic": "Sales", "channels": ["TV", "Radio"]}, "radio@example.com"),
        ({"age": "30", "topic": "Support", "channels": ["TV"]}, "general@example.com"),
        ({"age": "30"}, "zulip channel 7"),
        ({"age": "42", "topic": "Sales"}, "answer@example.com"),
        ({"age": "18", "topic": "Sales"}, "zulip channel 7"),
        ({"age": "64", "topic": "Sales"}, "zulip channel 7"),
    ],
    ids=[
        "less_than",
        "greater_than",
        "in",
        "in-second-value",
        "not_equals",
        "falls-back-to-default",
        "in-on-a-multiselect",
        "no-multiselect-match",
        "unanswered-optional-field",
        "equals",
        "lower-boundary",
        "upper-boundary",
    ],
)
def test_routing_rules(make_app, fake_smtp, fake_zulip, answers_posted, expected):
    destinations = {
        "default": {"type": "zulip", "channel_id": 7, "topic": "New wbzp-intake submission"},
        **{name: {"type": "email", "address": [f"{name}@example.com"]} for name in ("youth", "senior", "press", "general", "radio", "answer")},
    }
    app_instance = make_app(destinations=destinations, forms={"routing_rules": form_text("routing_rules")})

    response = app_instance.post("routing_rules", submitter_email="visitor@example.com", **answers_posted)

    assert response.status_code == 200
    if expected == "zulip channel 7":
        assert [post["to"] for post in fake_zulip.posts] == ["7"]
        assert fake_smtp.sent == []
    else:
        assert [mail.recipients[0] for mail in fake_smtp.sent] == [expected]
        assert fake_zulip.posts == []


def test_zulip_body_rendering(make_app, fake_zulip):
    app_instance = make_app(forms={"body_rendering": form_text("body_rendering")})

    assert app_instance.post("body_rendering", submitter_email="a_b@example.com", newsletter="false", pick="Two_words", count="-5").status_code == 200

    assert fake_zulip.posts[0]["content"] == "\n\n".join(
        [
            "**Form:** body_rendering",
            "**Your email**\na\\_b@example.com",
            "**Newsletter**\nNo",
            "**Pick**\nTwo\\_words",
            "**Count**\n-5",
        ]
    )


@pytest.mark.parametrize(
    "answers_posted",
    [{"subscribe": "maybe"}, {"channels": ["Fax"]}, {"topic": "Other"}, {"topic": "Nonsense"}],
    ids=["boolean-not-true-or-false", "multiselect-outside-options", "nested-field-required", "select-outside-options"],
)
def test_choice_field_validation(make_app, fake_smtp, answers_posted):
    app_instance = make_app(forms={"office_contact": form_text("office_contact")})

    response = app_instance.post("office_contact", **{"submitter_email": "visitor@example.com", "topic": "Billing", **answers_posted})

    assert response.status_code == 400
    assert response.get_json()["error"] == app_instance.message("common", "invalid_answers")
    assert fake_smtp.sent == []


def test_boolean_nested_field(make_app, fake_smtp):
    app_instance = make_app(forms={"boolean_nested": form_text("boolean_nested")})

    assert app_instance.post("boolean_nested", submitter_email="a@example.com", consent="true").status_code == 400
    assert app_instance.post("boolean_nested", submitter_email="a@example.com", consent="true", phone="555-0100").status_code == 200
    assert app_instance.post("boolean_nested", submitter_email="b@example.com", consent="false").status_code == 200
    bodies = [mail.message.get_content() for mail in fake_smtp.sent]
    assert "Phone number\n555-0100" in bodies[0]
    assert "Phone number" not in bodies[1]


def test_multiselect_nested_field(make_app, fake_smtp):
    app_instance = make_app(forms={"multiselect_nested": form_text("multiselect_nested")})
    base = {"submitter_email": "a@example.com"}

    assert app_instance.post("multiselect_nested", **base, topics=["Other"]).status_code == 400
    assert app_instance.post("multiselect_nested", **base, topics=["Billing", "Other"], other_text="Weather").status_code == 200
    assert app_instance.post("multiselect_nested", submitter_email="b@example.com", topics=["Billing"]).status_code == 200
    bodies = [mail.message.get_content() for mail in fake_smtp.sent]
    assert "Which other topic\nWeather" in bodies[0]
    assert "Which other topic" not in bodies[1]


def test_submission_expiry(make_app, fake_zulip, fake_smtp):
    app_instance = make_app(config={"submission_expiry_days": "0"})

    assert app_instance.apply(ip="203.0.113.1").status_code == 200
    assert app_instance.apply(ip="203.0.113.2").status_code == 200
    assert len(fake_zulip.posts) == 2

    fake_zulip.post_status = 500
    assert app_instance.apply(submitter_email="b@example.com", ip="203.0.113.3").status_code == 502
    assert app_instance.apply(submitter_email="b@example.com", ip="203.0.113.4").status_code == 400


@pytest.mark.parametrize(
    "answers_posted, expected",
    [
        ({"count": "10"}, "low@example.com"),
        ({"count": "11"}, "notfifty@example.com"),
        ({"count": "100"}, "high@example.com"),
        ({"count": "50"}, "zulip channel 7"),
        ({"count": "50", "subscribe": "true"}, "subscribers@example.com"),
        ({"count": "50", "subscribe": "false"}, "zulip channel 7"),
        ({}, "zulip channel 7"),
    ],
    ids=["at-most", "not-equal", "at-least", "no-match", "boolean-true", "boolean-false", "unanswered"],
)
def test_routing_comparisons(make_app, fake_smtp, fake_zulip, answers_posted, expected):
    destinations = {
        "default": {"type": "zulip", "channel_id": 7, "topic": "New wbzp-intake submission"},
        **{name: {"type": "email", "address": [f"{name}@example.com"]} for name in ("low", "high", "notfifty", "subscribers")},
    }
    app_instance = make_app(destinations=destinations, forms={"routing_comparisons": form_text("routing_comparisons")})

    response = app_instance.post("routing_comparisons", submitter_email="visitor@example.com", **answers_posted)

    assert response.status_code == 200
    if expected == "zulip channel 7":
        assert [post["to"] for post in fake_zulip.posts] == ["7"]
        assert fake_smtp.sent == []
    else:
        assert [mail.recipients[0] for mail in fake_smtp.sent] == [expected]
