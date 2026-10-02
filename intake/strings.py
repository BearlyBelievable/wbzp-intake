from string import Formatter

from flask import current_app

from . import config
from .errors import ConfigError

NO_PLACEHOLDERS = "none"

REQUIRED_PLACEHOLDERS = {
    "common": {
        "unknown_form": set(),
        "verification_failed": set(),
        "invalid_answers": set(),
        "submission_failed": set(),
        "rate_limited": set(),
        "server_busy": set(),
        "server_error": set(),
    },
    "zulip": {
        "registered": set(),
        "invited": set(),
        "pending": {"contact_email"},
    },
    "email": {
        "pending": {"contact_email"},
    },
}


def _placeholders(text):
    return {name for _, name, _, _ in Formatter().parse(text) if name is not None}


def _describe_placeholders(names):
    if not names:
        return NO_PLACEHOLDERS
    return ", ".join("{" + name + "}" for name in sorted(names))


def validate(messages, path):
    if not isinstance(messages, dict):
        messages = {}
    for section, keys in REQUIRED_PLACEHOLDERS.items():
        group = messages.get(section)
        if not isinstance(group, dict):
            group = {}
        for key, expected in keys.items():
            name = f"{section}.{key}"
            text = group.get(key)
            if not isinstance(text, str):
                raise ConfigError("missing_string", name=name, path=path)
            try:
                found = _placeholders(text)
                if found == expected:
                    text.format(**dict.fromkeys(found, ""))
            except (ValueError, KeyError, IndexError) as error:
                raise ConfigError("malformed_string", name=name, path=path, error=error) from error
            if found != expected:
                raise ConfigError(
                    "wrong_string_placeholders",
                    name=name,
                    path=path,
                    expected=_describe_placeholders(expected),
                    found=_describe_placeholders(found),
                )


def load(path):
    messages = config.load_yaml(path)
    validate(messages, path)
    return messages


def message(section, key, **params):
    return current_app.config["STRINGS"][section][key].format(**params)
