import configparser
import smtplib

import requests
import yaml


def _describe(error):
    if isinstance(error, requests.HTTPError) and error.response is not None:
        return f"the server answered HTTP {error.response.status_code}"
    if isinstance(error, requests.Timeout):
        return "the request timed out"
    if isinstance(error, requests.ConnectionError):
        return "the connection failed"
    if isinstance(error, smtplib.SMTPAuthenticationError):
        return "the server rejected the SMTP login"
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        return "the server refused the recipient address"
    if isinstance(error, TimeoutError):
        return "the connection timed out"
    if isinstance(error, yaml.YAMLError):
        problem = getattr(error, "problem", None) or str(error)
        mark = getattr(error, "problem_mark", None)
        return f"{problem} (line {mark.line + 1})" if mark is not None else problem
    if isinstance(error, configparser.Error):
        return str(error).splitlines()[0]
    if isinstance(error, OSError):
        return error.strerror or str(error)
    return str(error)


class AppError(RuntimeError):
    messages = {}

    def __init__(self, code, error=None, **params):
        if error is not None:
            params["reason"] = _describe(error)
        self.code = code
        self.params = params
        super().__init__(self.messages[code].format(**params))


def log_failure(logger, what, error):
    if isinstance(error, AppError):
        logger.error("%s: %s", what, error)
    else:
        logger.error(what, exc_info=error)


class ConfigError(AppError):
    messages = {
        "missing_config_file": "{path} not found. Run install.sh to generate it.",
        "invalid_config_file": "{path} is not a valid config file: {reason}",
        "missing_setting": "The setting '{name}' is missing from instance/config.conf and instance/secrets.conf",
        "invalid_setting": "The setting '{name}' has the value {value!r}, which isn't valid",
        "manage_py_missing": (
            "zulip_lookup is 'manage_py' but {path} was not found. Set zulip_manage_py in instance/config.conf "
            "to the location of Zulip's manage.py."
        ),
        "invalid_yaml": "{path} is not valid YAML: {reason}",
        "invalid_destination_type": "Destination '{name}'{location} has an invalid or missing 'type'",
        "missing_destination_keys": "Destination '{name}'{location} is missing key(s): {missing}",
        "invalid_destination_topic": "Destination '{name}'{location} needs 'topic' to be text that isn't blank",
        "invalid_destination_address": (
            "Destination '{name}'{location} needs 'address' to be a list of email addresses "
            "with at least one entry, where the first is the recipient and the rest are BCC"
        ),
        "missing_field_name": "Field schema is missing required key 'name': {field}",
        "duplicate_field_name": "Field name '{name}' is used more than once",
        "invalid_field_type": "Field '{name}' has an invalid or missing 'type'",
        "missing_field_label": "Field '{name}' is missing required key 'label'",
        "missing_field_required": "Field '{name}' is missing required key 'required'",
        "missing_field_note": "Field '{name}' is missing required key 'note'",
        "invalid_field_note": "Field '{name}' has a 'note' that isn't a dict",
        "incomplete_field_note": "Field '{name}' must set 'type', 'title', and 'text' together in 'note'",
        "missing_field_options": "Field '{name}' must have an 'options' dict",
        "too_few_field_options": "Field '{name}' must have at least {min_count} option(s) in 'options'",
        "invalid_boolean_options": "Field '{name}' has 'options' keys other than 'true'/'false'",
        "invalid_option_value": "Field '{name}' has an option whose value isn't a dict",
        "field_too_deep": "Field '{name}' is nested more than {max_depth} levels deep",
        "invalid_destinations_file": "Destinations{location} must be a mapping of names to destination settings",
        "invalid_template": "Template must be a mapping with 'routing_rules' and 'fields' keys",
        "invalid_template_fields": "Template's 'fields' must be a list of field definitions",
        "optional_email_field": "The '{name}' field must be required",
        "smtp_required": (
            "Email can't be sent because {purpose} but the SMTP settings are incomplete. Set smtp_host, "
            "smtp_port, smtp_user, and smtp_password in instance/config.conf and instance/secrets.conf, or "
            "{alternative}."
        ),
        "bounce_source_required": (
            "Destination '{name}' sends email, so bounce_source in instance/config.conf must be 'imap' (check a "
            "mailbox for bounced emails) or 'external' (you watch for bounces in your email provider)"
        ),
        "invalid_from_address": (
            "The SMTP login '{login}' isn't an email address, so set smtp_from in instance/config.conf to the "
            "address emails should come from"
        ),
        "imap_required": (
            "bounce_source is 'imap' but the IMAP settings are incomplete. Set bounce_imap_host in "
            "instance/config.conf, and make sure a login and password are available (bounce_imap_user and "
            "bounce_imap_password, or your SMTP login and password)"
        ),
        "missing_template_routing_rules": "Template is missing required key 'routing_rules'",
        "missing_template_fields": "Template is missing required key 'fields'",
        "missing_email_field": "Template needs a field named '{name}' for the submitter's email address",
        "invalid_email_field": "The '{name}' field must be type 'email'",
        "invalid_routing_rules": "Template's 'routing_rules' must be a list with at least one rule",
        "invalid_routing_rule": (
            "Every routing rule must be a dict with a 'destination', and 'trigger_field_name' and 'condition' "
            "together or not at all"
        ),
        "missing_fallback_rule": (
            "Template's 'routing_rules' must end with a rule that has only a 'destination', for submissions "
            "no other rule matches"
        ),
        "unknown_routing_field": "Routing rule references field '{field}', which isn't in this template",
        "invalid_number_condition": (
            "Routing rule for number field '{field}' needs a 'condition' in quotes like \"<18\" or \">=12\" "
            "(using <, <=, >, >=, =, or !=)"
        ),
        "invalid_choice_condition": (
            "Routing rule for field '{field}' needs a 'condition' that is a list of answers, such as "
            "[\"Yes\", \"No\"], or [true] for a boolean field"
        ),
        "unknown_condition_answer": (
            "Routing rule for field '{field}' lists '{answer}', which isn't one of its options"
        ),
        "unknown_template_destination": (
            "Template references destination(s) not found in the destinations config: {names}"
        ),
        "missing_string": "{path} needs a text message for '{name}'",
        "malformed_string": "The message '{name}' in {path} isn't valid: {reason}",
        "wrong_string_placeholders": (
            "The message '{name}' in {path} must use these placeholders: {expected}. It uses: {found}"
        ),
    }


class DatabaseError(AppError):
    messages = {
        "failed": "Could not use the submissions database: {reason}",
        "schema_mismatch": "The database is at schema version {version} but this code needs {needed}. Run flask db-migrate.",
    }


class MigrationError(AppError):
    messages = {
        "unknown_migration": "This database has migration {version} applied, but this code only has {known}.",
        "edited_migration": (
            "Migration {version} was changed after it was applied to this database. "
            "Add a new migration instead of editing an applied one."
        ),
        "integrity_check": (
            "Migrating to schema version {version} left the database failing its integrity "
            "checks, so it was rolled back: {problems}"
        ),
        "irreversible": "Migration {version} has no down section, so it can't be undone",
        "duplicate_section": "A migration can have only one '{marker}' section",
        "missing_up": "A migration needs a non-empty '{marker}' section",
        "bad_filename": "The migration file name '{name}' must look like 0001_short_description.sql",
        "numbering_gap": "Migration numbers must run 1, 2, 3 and so on with no gaps, found {numbers}",
    }


class DeliveryError(AppError):
    messages = {
        "api_failed": "The Zulip API request failed ({label}): {reason}",
        "email_visibility": (
            "The Zulip bot {bot_email} can't see members' real email addresses, so it can't tell who already has "
            "an account. In Zulip, set 'Who can access user email addresses' to Everyone or Members, or make "
            "the bot an administrator, then try again."
        ),
        "manage_py_failed": "Could not check Zulip's database with manage.py shell: {reason}",
        "not_zulip": "{url} doesn't look like a Zulip server",
        "no_organization": "{url} is a Zulip server but not a single organization on it. Enter the URL of one organization.",
        "settings_unreadable": "Could not read Zulip's settings at {path}: {reason}",
        "post_failed": "Could not post to Zulip channel {channel}: {reason}",
        "send_failed": "Could not send email to {to} through {host}:{port}: {reason}",
    }


class TurnstileError(AppError):
    messages = {"failed": "Could not verify the Turnstile token: {reason}"}


class BounceCheckError(AppError):
    messages = {"failed": "Could not check the bounce mailbox {host}:{port}: {reason}"}


class ValidationError(AppError):
    messages = {
        "too_long": "The answer for '{field[name]}' is longer than allowed",
        "too_short": "The answer for '{field[name]}' is shorter than allowed",
        "invalid_email": "The answer for '{field[name]}' is not a valid email address",
        "not_a_number": "The answer for '{field[name]}' is not a number",
        "below_minimum": "The answer for '{field[name]}' is below the minimum",
        "above_maximum": "The answer for '{field[name]}' is above the maximum",
        "invalid_value": "The answer for '{field[name]}' is not valid",
        "invalid_selection": "The answer for '{field[name]}' is not one of the allowed options",
    }
