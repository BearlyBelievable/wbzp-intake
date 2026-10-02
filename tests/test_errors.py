import ast
import smtplib

import pytest
import requests
import yaml
from source_scan import called_name, calls_to, parsed, placeholders, source_files

from intake import errors
from intake.errors import AppError, BounceCheckError, ConfigError, DatabaseError, DeliveryError, MigrationError, TurnstileError, ValidationError


FAMILIES = {
    cls.__name__: cls
    for cls in (ConfigError, DatabaseError, MigrationError, DeliveryError, TurnstileError, BounceCheckError, ValidationError)
}


def raise_sites():
    for filename, node in calls_to(FAMILIES):
        code = node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else None
        yield filename, node.lineno, called_name(node), code, {keyword.arg for keyword in node.keywords}


def test_error_definitions():
    assert all(cls.__module__ == errors.__name__ and issubclass(cls, AppError) for cls in FAMILIES.values())

    stray_classes = [
        f"{path.name}:{node.name}"
        for path in source_files()
        for node in ast.walk(parsed(path))
        if isinstance(node, ast.ClassDef)
        and any(isinstance(base, ast.Name) and base.id in {*FAMILIES, "AppError"} for base in node.bases)
    ]
    problems = []
    sites = list(raise_sites())
    for filename, line, family, code, params in sites:
        template = FAMILIES[family].messages.get(code)
        if template is None:
            problems.append(f"{filename}:{line} {family} has no code {code!r}")
            continue
        supplied = {"reason" if name == "error" else name for name in params}
        if supplied != placeholders(template):
            problems.append(f"{filename}:{line} {family}({code!r}) supplies {sorted(supplied)}")
    used = {(family, code) for _, _, family, code, _ in sites}
    unused = [(name, code) for name, cls in FAMILIES.items() for code in cls.messages if (name, code) not in used]

    assert (stray_classes, problems, unused) == ([], [], [])
    assert len(sites) > 50


def test_error_messages_build():
    for name, cls in FAMILIES.items():
        for code, template in cls.messages.items():
            facts = {key: {"name": "sample"} if key == "field" else f"sample {key}" for key in placeholders(template)}
            error = cls(code, **facts)
            assert (error.code, error.params, str(error)) == (code, facts, template.format(**facts)), (name, code)


@pytest.mark.parametrize(
    "error, reason",
    [
        (requests.HTTPError(response=type("Response", (), {"status_code": 503})()), "the server answered HTTP 503"),
        (requests.HTTPError("no response"), "no response"),
        (requests.Timeout(), "the request timed out"),
        (requests.ConnectionError(), "the connection failed"),
        (smtplib.SMTPAuthenticationError(535, b"x"), "the server rejected the SMTP login"),
        (smtplib.SMTPRecipientsRefused({"a@b": (550, b"x")}), "the server refused the recipient address"),
        (TimeoutError("slow"), "the connection timed out"),
        (yaml.YAMLError("odd"), "odd"),
        (OSError(2, "No such file"), "No such file"),
        (ValueError("plain"), "plain"),
    ],
    ids=[
        "http-status",
        "http-without-response",
        "timeout",
        "connection",
        "smtp-login",
        "smtp-recipient",
        "socket-timeout",
        "yaml",
        "os-error",
        "anything-else",
    ],
)
def test_exception_reasons(error, reason):
    assert DeliveryError("post_failed", channel=12, error=error).params["reason"] == reason


def test_yaml_line_and_unknown_code():
    try:
        yaml.safe_load("a: [unclosed\n")
    except yaml.YAMLError as error:
        assert "(line " in ConfigError("invalid_yaml", path="f.yaml", error=error).params["reason"]
    with pytest.raises(KeyError):
        ConfigError("not_a_real_code")
