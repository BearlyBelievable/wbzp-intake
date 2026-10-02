import imaplib
import os
import runpy
import shutil
import smtplib
import socket
import sys
import tempfile

import pytest
import requests
import responses
import yaml

from app_harness import AppInstance, DEFAULT_CONFIG, DEFAULT_DESTINATIONS, DEFAULT_SECRETS, REPO_ROOT, ini
from fake_services import FakeLookupHelper, FakeMailbox, FakeSmtpServer, FakeZulip

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "generator"))

import app as app_module  # noqa: E402


@pytest.fixture(autouse=True)
def no_real_connections(monkeypatch):
    attempts = []

    def refuse_http(self, request, **kwargs):
        attempts.append(f"HTTP request to {request.url}")
        raise requests.ConnectionError("real network requests are not allowed in tests")

    def refuse_mail(kind):
        def refuse(*args, **kwargs):
            attempts.append(f"{kind} connection")
            raise OSError("real mail connections are not allowed in tests")

        return refuse

    monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", refuse_http)
    monkeypatch.setattr(smtplib, "SMTP", refuse_mail("SMTP"))
    monkeypatch.setattr(smtplib, "SMTP_SSL", refuse_mail("SMTP_SSL"))
    monkeypatch.setattr(imaplib, "IMAP4_SSL", refuse_mail("IMAP"))
    yield
    assert attempts == [], f"A test reached the real network: {attempts}"


@pytest.fixture
def mock_http():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mock:
        yield mock


@pytest.fixture
def fake_zulip(mock_http):
    return FakeZulip(mock_http)


@pytest.fixture
def fake_lookup_helper():
    if not hasattr(socket, "AF_UNIX"):
        pytest.skip("the lookup helper socket needs Unix sockets")
    directory = tempfile.mkdtemp(prefix="wbzp-")
    helper = FakeLookupHelper(os.path.join(directory, "lookup.sock")).start()
    yield helper
    helper.stop()
    shutil.rmtree(directory, ignore_errors=True)


@pytest.fixture
def fake_smtp(monkeypatch):
    return FakeSmtpServer().install(monkeypatch)


@pytest.fixture
def fake_imap(monkeypatch):
    return FakeMailbox().install(monkeypatch)


@pytest.fixture
def run_cli(monkeypatch, capsys):
    def invoke(script, *args):
        monkeypatch.setattr(sys, "argv", [script, *map(str, args)])
        code = 0
        try:
            runpy.run_path(str(REPO_ROOT / script), run_name="__main__")
        except SystemExit as exit_request:
            code = exit_request.code
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return invoke


@pytest.fixture
def make_app(tmp_path, monkeypatch):
    def build(
        config=None,
        secrets=None,
        destinations=None,
        forms=None,
        strings_text=None,
        migrate=True,
        remove_config_keys=(),
        remove_secret_keys=(),
    ):
        instance = tmp_path / "instance"
        instance.mkdir(exist_ok=True)
        merged_config = {**DEFAULT_CONFIG, **(config or {})}
        for key in remove_config_keys:
            merged_config.pop(key, None)
        (instance / "config.conf").write_text(ini("config", merged_config), encoding="utf-8")
        merged_secrets = {**DEFAULT_SECRETS, **(secrets or {})}
        for key in remove_secret_keys:
            merged_secrets.pop(key, None)
        (instance / "secrets.conf").write_text(ini("secrets", merged_secrets), encoding="utf-8")
        os.chmod(instance / "secrets.conf", 0o600)
        destinations = DEFAULT_DESTINATIONS if destinations is None else destinations
        if destinations is not False:
            (instance / "destinations.yaml").write_text(
                destinations if isinstance(destinations, str) else yaml.safe_dump(destinations), encoding="utf-8"
            )
        forms_dir = tmp_path / "forms"
        forms_dir.mkdir(exist_ok=True)
        (forms_dir / "application.yaml").write_text(
            (REPO_ROOT / "forms" / "application.yaml").read_text(encoding="utf-8"), encoding="utf-8"
        )
        for name, text in (forms or {}).items():
            (forms_dir / f"{name}.yaml").write_text(text, encoding="utf-8")
        monkeypatch.setattr(app_module, "FORMS_DIR", forms_dir)
        if strings_text is not None:
            strings_path = tmp_path / "strings.yaml"
            strings_path.write_text(strings_text, encoding="utf-8")
            monkeypatch.setattr(app_module, "STRINGS_PATH", strings_path)

        app = app_module.create_app(instance_path=instance)
        app.config["TESTING"] = True
        app_instance = AppInstance(app, tmp_path)
        if migrate:
            result = app_instance.invoke("db-migrate")
            assert result.exit_code == 0, result.output
        return app_instance

    return build


@pytest.fixture
def app_instance(make_app):
    return make_app()


@pytest.fixture
def app_context(app_instance):
    with app_instance.app.app_context():
        yield app_instance
