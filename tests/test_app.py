import copy
import logging
import os
import re

import pytest
import yaml
from app_harness import REPO_ROOT

import app as app_module
from intake import forms
from intake.errors import ConfigError


def startup_error(make_app, **kwargs):
    with pytest.raises(ConfigError) as caught:
        make_app(**kwargs)
    return str(caught.value)


def make_field(**overrides):
    field = {"name": "submitter_email", "type": "email", "required": True, "label": "Email", "note": {}}
    field.update(overrides)
    return field


def make_form(**overrides):
    form = {"routing_rules": [{"destination": "default"}], "fields": [make_field()]}
    form.update(overrides)
    return form


def without(mapping, key):
    return {name: value for name, value in mapping.items() if name != key}


def with_extra_field(**overrides):
    extra = {"name": "extra", "type": "text", **overrides}
    return make_form(fields=[make_field(), make_field(**extra)])


def rule(**overrides):
    return {"destination": "default", "trigger_field_name": "submitter_email", "condition": ["a@example.com"], **overrides}


def with_rules(*rules):
    return make_form(routing_rules=[*rules, {"destination": "default"}])


def with_number_field(**rule_overrides):
    return make_form(
        fields=[make_field(), make_field(name="age", type="number")],
        routing_rules=[rule(**{"trigger_field_name": "age", "condition": "<18", **rule_overrides}), {"destination": "default"}],
    )


CONFIG_WITH_OTHER_SECTION = "[unrelated]\nsecret_looking = 1\n"

NESTED = make_field(name="nested", type="text")

FORM_ERRORS = {
    "template-not-a-mapping": ([1, 2], "Template must be a mapping"),
    "fields-not-a-list": (make_form(fields="oops"), "'fields' must be a list"),
    "field-not-a-mapping": (make_form(fields=["oops"]), "missing required key 'name'"),
    "optional-email-field": (make_form(fields=[make_field(required=False)]), "The 'submitter_email' field must be required"),
    "no-fields": (without(make_form(), "fields"), "missing required key 'fields'"),
    "no-email-field": (
        make_form(fields=[make_field(name="contact")]),
        "needs a field named 'submitter_email'",
    ),
    "email-field-wrong-type": (make_form(fields=[make_field(type="text")]), "The 'submitter_email' field must be type 'email'"),
    "field-without-name": (make_form(fields=[without(make_field(), "name")]), "missing required key 'name'"),
    "duplicate-name": (make_form(fields=[make_field(), make_field()]), "used more than once"),
    "bad-type": (with_extra_field(type="bogus"), "invalid or missing 'type'"),
    "no-label": (make_form(fields=[without(make_field(), "label")]), "missing required key 'label'"),
    "no-required": (make_form(fields=[without(make_field(), "required")]), "missing required key 'required'"),
    "no-note": (make_form(fields=[without(make_field(), "note")]), "missing required key 'note'"),
    "note-not-a-dict": (with_extra_field(note="oops"), "isn't a dict"),
    "note-incomplete": (with_extra_field(note={"type": "info"}), "must set 'type', 'title', and 'text'"),
    "select-without-options": (with_extra_field(type="select"), "must have an 'options' dict"),
    "select-with-one-option": (with_extra_field(type="select", options={"A": {}}), "at least 2 option"),
    "multiselect-with-no-options": (with_extra_field(type="multiselect", options={}), "at least 1 option"),
    "boolean-option-key": (with_extra_field(type="boolean", options={"Other": NESTED}), "'true'/'false'"),
    "nested-field-without-name": (
        with_extra_field(type="select", options={"A": without(NESTED, "name"), "B": {}}),
        "missing required key 'name'",
    ),
    "nested-name-collides": (
        with_extra_field(type="select", options={"A": make_field(name="extra", type="text"), "B": {}}),
        "used more than once",
    ),
    "nested-too-deep": (
        with_extra_field(
            type="select",
            options={
                "A": make_field(
                    name="one", type="select", options={"X": make_field(name="two", type="text"), "Y": {}}
                ),
                "B": {},
            },
        ),
        "nested more than",
    ),
    "option-value-not-a-dict": (with_extra_field(type="select", options={"A": "oops", "B": {}}), "isn't a dict"),
    "no-routing-rules": (without(make_form(), "routing_rules"), "missing required key 'routing_rules'"),
    "rules-not-a-list": (make_form(routing_rules="x"), "'routing_rules' must be a list"),
    "rules-empty": (make_form(routing_rules=[]), "'routing_rules' must be a list with at least one rule"),
    "rule-without-destination": (
        make_form(routing_rules=[{"trigger_field_name": "submitter_email", "condition": ["a@example.com"]}]),
        "Every routing rule must be a dict with a 'destination'",
    ),
    "rule-with-only-a-trigger": (
        make_form(routing_rules=[{"destination": "default", "trigger_field_name": "submitter_email"}]),
        "together or not at all",
    ),
    "rule-with-only-a-condition": (
        make_form(routing_rules=[{"destination": "default", "condition": ["x"]}]),
        "together or not at all",
    ),
    "no-fallback-rule": (make_form(routing_rules=[rule()]), "must end with a rule that has only a 'destination'"),
    "rule-unknown-field": (with_rules(rule(trigger_field_name="nope")), "which isn't in this template"),
    "choice-condition-not-a-list": (
        with_rules(rule(condition="a@example.com")),
        "needs a 'condition' that is a list of answers",
    ),
    "choice-condition-empty": (with_rules(rule(condition=[])), "needs a 'condition' that is a list"),
    "choice-condition-not-text": (with_rules(rule(condition=[5])), "needs a 'condition' that is a list"),
    "boolean-condition-in-quotes": (
        make_form(
            fields=[make_field(), make_field(name="agree", type="boolean")],
            routing_rules=[rule(trigger_field_name="agree", condition=["true"]), {"destination": "default"}],
        ),
        "needs a 'condition' that is a list",
    ),
    "unknown-option": (
        make_form(
            fields=[make_field(), make_field(name="pick", type="select", options={"A": {}, "B": {}})],
            routing_rules=[rule(trigger_field_name="pick", condition=["C"]), {"destination": "default"}],
        ),
        "lists 'C', which isn't one of its options",
    ),
    "number-condition-not-text": (with_number_field(condition=17), "needs a 'condition' in quotes"),
    "number-condition-no-symbol": (with_number_field(condition="older than 17"), "needs a 'condition' in quotes"),
    "number-condition-no-number": (with_number_field(condition="<abc"), "needs a 'condition' in quotes"),
    "unknown-fallback-destination": (
        make_form(routing_rules=[{"destination": "nowhere"}]),
        "not found in the destinations config: nowhere",
    ),
    "unknown-rule-destination": (
        with_rules(rule(destination="nowhere")),
        "not found in the destinations config: nowhere",
    ),
}


@pytest.mark.parametrize("name", FORM_ERRORS)
def test_invalid_form(make_app, name):
    form, fragment = FORM_ERRORS[name]

    message = startup_error(make_app, forms={"bad": yaml.safe_dump(form)})

    assert fragment in message


DESTINATION_ERRORS = {
    "not-a-mapping": (["default"], "must be a mapping of names"),
    "destination-is-text": ({"default": "email"}, "Destination 'default'"),
    "unknown-type": ({"default": {"type": "fax"}}, "Destination 'default'"),
    "zulip-without-channel-or-topic": ({"default": {"type": "zulip"}}, "is missing key(s): channel_id, topic"),
    "zulip-without-topic": ({"default": {"type": "zulip", "channel_id": 7}}, "is missing key(s): topic"),
    "zulip-blank-topic": (
        {"default": {"type": "zulip", "channel_id": 7, "topic": "  "}},
        "needs 'topic' to be text that isn't blank",
    ),
    "zulip-topic-not-text": (
        {"default": {"type": "zulip", "channel_id": 7, "topic": 5}},
        "needs 'topic' to be text that isn't blank",
    ),
    "email-without-address": ({"default": {"type": "email"}}, "is missing key(s): address"),
    "empty-address-list": ({"default": {"type": "email", "address": []}}, "needs 'address' to be a list"),
    "address-is-text": ({"default": {"type": "email", "address": "a@example.com"}}, "needs 'address' to be a list"),
    "blank-address": ({"default": {"type": "email", "address": ["a@example.com", ""]}}, "needs 'address' to be a list"),
    "not-yaml": ("default: [unclosed\n", "is not valid YAML"),
}


@pytest.mark.parametrize("name", DESTINATION_ERRORS)
def test_invalid_destinations(make_app, name):
    destinations, fragment = DESTINATION_ERRORS[name]

    message = startup_error(make_app, destinations=destinations)

    assert fragment in message
    assert "destinations.yaml" in message


def test_empty_destinations_recreated(make_app, caplog):
    app_instance = make_app(destinations={})

    text = (app_instance.instance / "destinations.yaml").read_text(encoding="utf-8")

    assert "default:" in text
    assert "admin@example.com" in text
    assert "was empty, so the default destinations have been reloaded" in caplog.text


@pytest.mark.skipif(os.name != "posix", reason="file modes only apply on POSIX systems")
def test_readable_secrets_file_warns(make_app, caplog):
    app_instance = make_app()
    secrets = app_instance.instance / "secrets.conf"

    os.chmod(secrets, 0o644)
    app_module.create_app(instance_path=app_instance.instance)
    assert "can be read by other users" in caplog.text

    caplog.clear()
    os.chmod(secrets, 0o600)
    app_module.create_app(instance_path=app_instance.instance)
    assert "can be read by other users" not in caplog.text


def shipped_strings():
    return yaml.safe_load((REPO_ROOT / "strings.yaml").read_text(encoding="utf-8"))


def edited(change):
    messages = copy.deepcopy(shipped_strings())
    change(messages)
    return yaml.safe_dump(messages)


STRING_ERRORS = {
    "missing-message": (edited(lambda m: m["common"].pop("unknown_form")), "needs a text message for 'common.unknown_form'"),
    "missing-section": (edited(lambda m: m.pop("email")), "needs a text message for 'email.pending'"),
    "not-text": (edited(lambda m: m["common"].update(server_busy=5)), "needs a text message for 'common.server_busy'"),
    "extra-placeholder": (
        edited(lambda m: m["zulip"].update(pending="Write to {contact_email} or {other}")),
        "must use these placeholders: {contact_email}. It uses: {contact_email}, {other}",
    ),
    "missing-placeholder": (
        edited(lambda m: m["email"].update(pending="Contact us")),
        "must use these placeholders: {contact_email}. It uses: none",
    ),
    "stray-brace": (edited(lambda m: m["common"].update(server_busy="Busy {")), "isn't valid"),
    "bad-conversion": (edited(lambda m: m["zulip"].update(pending="Write to {contact_email!x}")), "isn't valid"),
    "not-a-mapping": ("- just\n- a list\n", "needs a text message for"),
    "not-yaml": ("common: [unclosed\n", "is not valid YAML"),
}


@pytest.mark.parametrize("name", STRING_ERRORS)
def test_invalid_strings(make_app, name):
    text, fragment = STRING_ERRORS[name]

    message = startup_error(make_app, strings_text=text)

    assert fragment in message
    assert "strings.yaml" in message


def test_custom_strings(make_app):
    def reword(messages):
        messages["common"]["invalid_answers"] = "Hmm, something looks off."
        messages["common"]["spare"] = "Unused but harmless {anything}"

    app_instance = make_app(strings_text=edited(reword))

    response = app_instance.apply(full_name="")

    assert response.get_json() == {"error": "Hmm, something looks off."}


def test_missing_instance_files(tmp_path):
    instance = tmp_path / "instance"
    instance.mkdir()

    with pytest.raises(ConfigError, match=re.escape("config.conf not found. Run install.sh")):
        app_module.create_app(instance_path=instance)

    (instance / "config.conf").write_text("[config]\n", encoding="utf-8")
    with pytest.raises(ConfigError, match=re.escape("secrets.conf not found. Run install.sh")):
        app_module.create_app(instance_path=instance)

    (instance / "secrets.conf").write_text("[secrets\nbroken", encoding="utf-8")
    with pytest.raises(ConfigError, match="secrets.conf is not a valid config file: File contains") as caught:
        app_module.create_app(instance_path=instance)
    assert "\n" not in str(caught.value)


def test_entry_point(tmp_path, monkeypatch):
    broken = tmp_path / "instance"
    broken.mkdir()
    real_create_app = app_module.create_app
    monkeypatch.setattr(app_module, "create_app", lambda: real_create_app(instance_path=broken))
    with pytest.raises(SystemExit, match="config.conf not found"):
        app_module._create_app_or_exit()

    built = []
    monkeypatch.setattr(app_module, "_instance", None)
    monkeypatch.setattr(app_module, "create_app", lambda: built.append("app") or "the app")
    assert (app_module.app, app_module.app) == ("the app", "the app")
    assert built == ["app"]
    with pytest.raises(AttributeError):
        app_module.nothing_here


def test_extra_config_sections(tmp_path):
    instance = tmp_path / "instance"
    instance.mkdir()
    (instance / "config.conf").write_text(
        CONFIG_WITH_OTHER_SECTION
        + "[config]\ncontact_email = a@example.com\nzulip_site_url = https://z.test\nzulip_bot_email = b@z.test\n",
        encoding="utf-8",
    )
    (instance / "secrets.conf").write_text("[secrets]\nzulip_bot_api_key = key\n", encoding="utf-8")
    (instance / "destinations.yaml").write_text("default: {type: zulip, channel_id: 1, topic: Hello}\n", encoding="utf-8")

    app = app_module.create_app(instance_path=instance)

    assert "secret_looking" not in app.config


@pytest.mark.parametrize(
    "config, expected_address",
    [({}, "admin@example.com"), ({"bounce_source": ""}, "example@domain.com")],
    ids=["bounce-source-set-uses-the-contact-email", "no-bounce-source-uses-the-placeholder"],
)
def test_missing_destinations_file(make_app, caplog, config, expected_address):
    with caplog.at_level(logging.INFO):
        app_instance = make_app(destinations=False, config=config)

    assert expected_address in (app_instance.instance / "destinations.yaml").read_text(encoding="utf-8")
    assert "did not exist, so the default destinations were created" in caplog.text
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]


@pytest.mark.parametrize(
    "kwargs, name",
    [
        ({"remove_config_keys": ["contact_email"]}, "contact_email"),
        ({"remove_config_keys": ["zulip_site_url"]}, "zulip_site_url"),
        ({"remove_config_keys": ["zulip_bot_email"]}, "zulip_bot_email"),
        ({"remove_secret_keys": ["zulip_bot_api_key"]}, "zulip_bot_api_key"),
    ],
    ids=["contact-email", "zulip-url", "zulip-bot-email", "zulip-api-key"],
)
def test_missing_core_setting(make_app, kwargs, name):
    message = startup_error(make_app, **kwargs)

    assert f"The setting '{name}' is missing" in message


def test_zulip_settings_not_needed_without_a_zulip_destination(make_app):
    make_app(
        remove_config_keys=["zulip_site_url", "zulip_bot_email"],
        remove_secret_keys=["zulip_bot_api_key"],
        destinations={"default": {"type": "email", "address": ["a@example.com"]}},
    )


def test_invalid_zulip_lookup(make_app):
    message = startup_error(make_app, config={"zulip_lookup": "database"})

    assert "The setting 'zulip_lookup' has the value 'database', which isn't valid" in message


def test_manage_py_lookup_needs_manage_py(make_app, tmp_path):
    missing = tmp_path / "manage.py"

    message = startup_error(make_app, config={"zulip_lookup": "manage_py", "zulip_manage_py": str(missing)})

    assert f"zulip_lookup is 'manage_py' but {missing} was not found" in message
    make_app(config={"zulip_lookup": "manage_py", "zulip_manage_py": __file__})


def test_invalid_body_limit(make_app):
    message = startup_error(make_app, config={"max_body_bytes": "lots"})

    assert "The setting 'max_body_bytes' has the value 'lots', which isn't valid" in message


def test_invalid_ip_limit_500(make_app, caplog):
    app_instance = make_app(config={"max_attempts_per_ip": "lots"})

    response = app_instance.apply()

    assert response.status_code == 500
    assert response.get_json()["error"] == app_instance.message("common", "server_error")
    assert "The setting 'max_attempts_per_ip' has the value 'lots'" in caplog.text
