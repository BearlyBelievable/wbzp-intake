import configparser
import os
import re
import shutil
import stat
import subprocess
import sys

import pytest
from app_harness import REPO_ROOT

pytestmark = pytest.mark.skipif(
    (os.name != "posix" and not os.environ.get("WBZP_SHELL_TESTS")) or shutil.which(os.environ.get("WBZP_BASH", "bash")) is None,
    reason="the shell flows need bash on a POSIX system",
)

BASH = os.environ.get("WBZP_BASH", "bash")

COPIED_FILES = [
    "lib.sh",
    "migrate-legacy-config.sh",
    "config_cli.py",
    "destinations_cli.py",
    "email_providers_cli.py",
    "email_providers.json",
    "zulip_email_cli.py",
    "lookup_helper.py",
]

PYTHON_WRAPPER = '#!/bin/bash\n"{python}" "$@" | tr -d "\\r"\nexit ${{PIPESTATUS[0]}}\n'
LOGGING_STUB = '#!/bin/sh\necho "{name} $*" >> "$STUB_LOG"\n'

ZULIP_SETTINGS = "EMAIL_HOST = 'smtp.zulip.test'\nEMAIL_PORT = 465\nEMAIL_USE_SSL = True\n"

IF_COMPLETE = "if smtp_is_complete\nthen\n    echo yes\nelse\n    echo no\nfi"
IF_BOUNCING = "if bounce_checking_enabled\nthen\n    echo on\nelse\n    echo off\nfi"
OFFICE_EMAIL = "office: {type: email, address: [a@example.com]}\n"
ZULIP_ONLY = "default: {type: zulip, channel_id: 1, topic: Hi}\n"


def write_executable(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class ShellApp:
    def __init__(self, tmp_path):
        self.root = tmp_path / "app"
        self.systemd = tmp_path / "systemd"
        self.bin = tmp_path / "bin"
        self.log = tmp_path / "calls.log"
        self.zulip_settings = tmp_path / "zulip-settings.py"
        self.helper_dir = tmp_path / "helper"
        self.manage_py = tmp_path / "zulip" / "manage.py"
        self.root.mkdir()
        self.systemd.mkdir()
        self.log.write_text("")
        for name in COPIED_FILES:
            shutil.copy(REPO_ROOT / name, self.root / name)
        for folder in ("intake", "deploy"):
            shutil.copytree(REPO_ROOT / folder, self.root / folder, ignore=shutil.ignore_patterns("__pycache__"))
        wrapper = PYTHON_WRAPPER.format(python=sys.executable.replace(os.sep, "/"))
        write_executable(self.root / ".venv" / "bin" / "python", wrapper)
        write_executable(self.bin / "python3", wrapper)
        for name in ("systemctl", "chown"):
            write_executable(self.bin / name, LOGGING_STUB.format(name=name))

    def run(self, script, stdin=""):
        env = {
            **os.environ,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "WBZP_INTAKE_SYSTEMD_DIR": str(self.systemd).replace(os.sep, "/"),
            "WBZP_INTAKE_ZULIP_SETTINGS": str(self.zulip_settings).replace(os.sep, "/"),
            "WBZP_INTAKE_HELPER_DIR": str(self.helper_dir).replace(os.sep, "/"),
            "SUDO_USER": "tester",
            "STUB_LOG": str(self.log).replace(os.sep, "/"),
        }
        command = "source ./lib.sh\nsource ./migrate-legacy-config.sh\n" + script
        completed = subprocess.run(
            [BASH, "-c", command], cwd=self.root, input=stdin.encode(), capture_output=True, env=env, timeout=120
        )
        completed.stdout = completed.stdout.decode()
        completed.stderr = completed.stderr.decode()
        return completed

    def setting(self, key):
        for filename, section in (("config.conf", "config"), ("secrets.conf", "secrets")):
            parser = configparser.ConfigParser()
            parser.read(self.root / "instance" / filename)
            if parser.has_option(section, key):
                return parser.get(section, key)
        return None

    def write_destinations(self, text):
        (self.root / "instance" / "destinations.yaml").write_text(text, encoding="utf-8")

    def calls(self):
        return self.log.read_text().splitlines()


@pytest.fixture
def shell_app(tmp_path):
    app = ShellApp(tmp_path)
    result = app.run("ensure_config_files")
    assert result.returncode == 0, result.stderr
    return app


def test_config_files_are_private(shell_app):
    for path, mode in (
        (shell_app.root / "instance", 0o700),
        (shell_app.root / "instance" / "secrets.conf", 0o600),
        (shell_app.root / "instance" / "config.conf", 0o600),
    ):
        assert stat.S_IMODE(path.stat().st_mode) == mode, path


@pytest.mark.parametrize(
    "provider, stdin, expected",
    [
        (
            "google",
            "me@gmail.com\napp-pass\n\n",
            {
                "smtp_host": "smtp.gmail.com",
                "smtp_port": "587",
                "smtp_security": "starttls",
                "smtp_user": "me@gmail.com",
                "smtp_password": "app-pass",
            },
        ),
        (
            "sendgrid",
            "the-api-key\nforms@example.com\n",
            {
                "smtp_host": "smtp.sendgrid.net",
                "smtp_user": "apikey",
                "smtp_password": "the-api-key",
                "smtp_from": "forms@example.com",
            },
        ),
        (
            "ses",
            "eu-west-1\nACCESSKEY\nsecret\nforms@example.com\n",
            {
                "smtp_host": "email-smtp.eu-west-1.amazonaws.com",
                "smtp_user": "ACCESSKEY",
                "smtp_password": "secret",
                "smtp_from": "forms@example.com",
            },
        ),
        (
            "mailgun_eu",
            "postmaster@mg.example.com\npw\n\n",
            {"smtp_host": "smtp.eu.mailgun.org", "smtp_user": "postmaster@mg.example.com", "smtp_from": ""},
        ),
    ],
    ids=["account", "fixed-username", "region-prompt", "optional-from"],
)
def test_provider_setup_fills_in_settings(shell_app, provider, stdin, expected):
    result = shell_app.run(f"configure_smtp_provider {provider}", stdin)

    assert result.returncode == 0, result.stderr
    for key, value in expected.items():
        assert shell_app.setting(key) == value, key
    assert shell_app.setting("smtp_provider") == provider


def test_provider_that_needs_a_from_address_insists_on_one(shell_app):
    result = shell_app.run("configure_smtp_provider sendgrid", "the-api-key\n\n")

    assert result.returncode != 0
    assert "smtp_from is required" in result.stderr


def test_smtp_is_complete(shell_app):
    assert shell_app.run(IF_COMPLETE).stdout.strip() == "no"

    shell_app.run("configure_smtp_provider google", "me@gmail.com\napp-pass\n\n")

    assert shell_app.run(IF_COMPLETE).stdout.strip() == "yes"


@pytest.mark.parametrize(
    "bounce_source, destinations, expected",
    [
        ("imap", OFFICE_EMAIL, "on"),
        ("imap", ZULIP_ONLY, "off"),
        ("external", OFFICE_EMAIL, "off"),
        ("", OFFICE_EMAIL, "off"),
    ],
    ids=["imap-with-email", "imap-without-email", "external", "unset"],
)
def test_bounce_checking_is_gated_on_use(shell_app, bounce_source, destinations, expected):
    shell_app.run(f'set_conf_value bounce_source "{bounce_source}" "$CONFIG_FILE"')
    shell_app.write_destinations(destinations)

    assert shell_app.run(IF_BOUNCING).stdout.strip() == expected


def test_bounce_setup_for_an_email_account(shell_app):
    shell_app.run('set_conf_value smtp_provider google "$CONFIG_FILE"')

    result = shell_app.run("configure_bounce_checking", "\n\n")

    assert result.returncode == 0, result.stderr
    assert shell_app.setting("bounce_source") == "imap"
    assert shell_app.setting("bounce_imap_host") == "imap.gmail.com"


def test_bounce_setup_for_a_transactional_service(shell_app):
    shell_app.run('set_conf_value smtp_provider mailgun_us "$CONFIG_FILE"')

    result = shell_app.run("configure_bounce_checking")

    assert result.returncode == 0, result.stderr
    assert shell_app.setting("bounce_source") == "external"
    assert "dashboard" in result.stdout


def test_bounce_setup_for_another_service(shell_app):
    result = shell_app.run("configure_bounce_checking", "y\nimap.example.com\n\n\n\n\n")

    assert result.returncode == 0, result.stderr
    assert shell_app.setting("bounce_source") == "imap"
    assert shell_app.setting("bounce_imap_host") == "imap.example.com"

    declined = shell_app.run("configure_bounce_checking", "n\n")
    assert declined.returncode == 0
    assert shell_app.setting("bounce_source") == "external"


@pytest.mark.parametrize(
    "stdin, expected_provider, expected_host",
    [
        ("1\n1\nme@gmail.com\napp-pass\n\n", "google", "smtp.gmail.com"),
        ("2\n3\nlogin@example.com\ntoken\nforms@example.com\n", "sendgrid", "smtp.sendgrid.net"),
        ("3\nsmtp.other.test\n2525\nme\npw\nme@other.test\n", "", "smtp.other.test"),
    ],
    ids=["account", "transactional", "another-service"],
)
def test_smtp_menu(shell_app, stdin, expected_provider, expected_host):
    result = shell_app.run("setup_smtp", stdin)

    assert result.returncode == 0, result.stderr
    assert (shell_app.setting("smtp_provider") or "") == expected_provider
    assert shell_app.setting("smtp_host") == expected_host


def test_smtp_menu_skip(shell_app):
    result = shell_app.run("setup_smtp\necho rc=$?", "4\n")

    assert "rc=1" in result.stdout
    assert not shell_app.setting("smtp_host")


def test_smtp_menu_offers_zulip_server_when_available(shell_app):
    shell_app.zulip_settings.write_text(ZULIP_SETTINGS)

    result = shell_app.run("setup_smtp", "3\nlogin@example.com\ntoken\n\n")

    assert result.returncode == 0, result.stderr
    assert "The same server as Zulip" in result.stderr
    assert shell_app.setting("smtp_host") == "smtp.zulip.test"
    assert shell_app.setting("smtp_port") == "465"
    assert shell_app.setting("smtp_security") == "ssl"
    assert shell_app.setting("smtp_user") == "login@example.com"
    assert shell_app.setting("smtp_password") == "token"


def test_legacy_config_files_are_moved_into_instance(tmp_path):
    app = ShellApp(tmp_path)
    (app.root / "config.conf").write_text("[config]\ncontact_email = old@example.com\n")
    (app.root / "secrets.conf").write_text("[secrets]\nsmtp_password = old-secret\n")

    result = app.run("migrate_legacy_config_files")

    assert result.returncode == 0, result.stderr
    assert not (app.root / "config.conf").exists()
    assert app.setting("contact_email") == "old@example.com"
    assert app.setting("smtp_password") == "old-secret"


@pytest.mark.parametrize(
    "bounce_source, bounce_call",
    [
        ("imap", "enable wbzp-intake-check-bounces.timer"),
        ("external", "disable --now wbzp-intake-check-bounces.timer"),
    ],
    ids=["bounce-checking-on", "bounce-checking-off"],
)
def test_service_units_follow_the_configuration(shell_app, bounce_source, bounce_call):
    shell_app.run(
        f'set_conf_value site_root /srv/site "$CONFIG_FILE"\nset_conf_value bounce_source "{bounce_source}" "$CONFIG_FILE"'
    )
    shell_app.write_destinations(OFFICE_EMAIL)

    result = shell_app.run("sync_service_units")

    assert result.returncode == 0, result.stderr
    unit = (shell_app.systemd / "wbzp-intake.service").read_text()
    assert "User=tester" in unit
    assert "/.venv/bin/flask --app app serve" in unit
    assert "__APP_ROOT__" not in unit
    calls = shell_app.calls()
    assert "systemctl daemon-reload" in calls
    assert "systemctl enable wbzp-intake wbzp-intake-check-pending.timer" in calls
    assert f"systemctl {bounce_call}" in calls
    assert not any(call.startswith("systemctl start") for call in calls)


def test_legacy_services_are_removed(shell_app):
    for name in ("web-zulip-application-form.service", "web-zulip-application-form-check-pending.service"):
        (shell_app.systemd / name).write_text("[Unit]\n")

    result = shell_app.run("migrate_legacy_services")

    assert result.returncode == 0, result.stderr
    assert list(shell_app.systemd.iterdir()) == []
    assert "systemctl disable --now web-zulip-application-form" in shell_app.calls()


def install_helper(shell_app, stdin=""):
    write_executable(shell_app.manage_py, "#!/bin/sh\n")
    manage_py = str(shell_app.manage_py).replace(os.sep, "/")
    shell_app.run(f'set_conf_value zulip_manage_py "{manage_py}" "$CONFIG_FILE"')
    return shell_app.run("install_lookup_helper", stdin=stdin), manage_py


def test_lookup_helper_is_installed_root_owned_with_its_units(shell_app):
    result, manage_py = install_helper(shell_app)

    assert result.returncode == 0, result.stderr
    helper = shell_app.helper_dir / "lookup_helper.py"
    assert helper.read_text() == (REPO_ROOT / "lookup_helper.py").read_text()
    assert helper.stat().st_mode & 0o777 == 0o755
    socket_unit = (shell_app.systemd / "wbzp-intake-lookup.socket").read_text()
    assert "SocketUser=tester" in socket_unit
    assert "SocketMode=0600" in socket_unit
    service_unit = (shell_app.systemd / "wbzp-intake-lookup@.service").read_text()
    assert f"Environment=WBZP_INTAKE_MANAGE_PY={manage_py}" in service_unit
    assert f"ExecStart=/usr/bin/python3 {str(helper).replace(os.sep, '/')}" in service_unit
    assert re.search(r"^User=\S+$", service_unit, re.MULTILINE)
    assert "__" not in socket_unit + service_unit
    calls = shell_app.calls()
    assert f"chown root:root {str(shell_app.helper_dir).replace(os.sep, '/')} {str(helper).replace(os.sep, '/')}" in calls
    assert "systemctl daemon-reload" in calls
    assert "systemctl enable wbzp-intake-lookup.socket" in calls
    assert not any(call.startswith("systemctl start") for call in calls)
    assert shell_app.setting("zulip_manage_py") == manage_py


def test_lookup_helper_setup_asks_where_manage_py_is_when_it_is_missing(shell_app):
    write_executable(shell_app.manage_py, "#!/bin/sh\n")
    manage_py = str(shell_app.manage_py).replace(os.sep, "/")

    result = shell_app.run("install_lookup_helper", stdin=f"/nowhere/manage.py\n{manage_py}\n")

    assert result.returncode == 0, result.stderr
    assert shell_app.setting("zulip_manage_py") == manage_py
    assert (shell_app.helper_dir / "lookup_helper.py").exists()


@pytest.mark.parametrize("mode, installed", [("helper", True), ("api", False)], ids=["helper-mode", "api-mode"])
def test_service_sync_keeps_the_lookup_helper_current(shell_app, mode, installed):
    write_executable(shell_app.manage_py, "#!/bin/sh\n")
    manage_py = str(shell_app.manage_py).replace(os.sep, "/")
    shell_app.run(
        f'set_conf_value site_root /srv/site "$CONFIG_FILE"\n'
        f'set_conf_value zulip_manage_py "{manage_py}" "$CONFIG_FILE"\n'
        f'set_conf_value zulip_lookup "{mode}" "$CONFIG_FILE"'
    )

    result = shell_app.run("sync_service_units")

    assert result.returncode == 0, result.stderr
    assert (shell_app.systemd / "wbzp-intake-lookup.socket").exists() is installed
    assert ("systemctl enable wbzp-intake-lookup.socket" in shell_app.calls()) is installed


def test_lookup_question_installs_the_helper_when_the_app_is_on_the_zulip_server(shell_app):
    write_executable(shell_app.manage_py, "#!/bin/sh\n")
    manage_py = str(shell_app.manage_py).replace(os.sep, "/")
    shell_app.run(f'set_conf_value zulip_manage_py "{manage_py}" "$CONFIG_FILE"')

    result = shell_app.run("choose_lookup_mode", stdin="y\n")

    assert result.returncode == 0, result.stderr
    assert shell_app.setting("zulip_lookup") == "helper"
    assert (shell_app.systemd / "wbzp-intake-lookup.socket").exists()


def test_lookup_question_defaults_to_the_api(shell_app):
    result = shell_app.run("choose_lookup_mode", stdin="\n")

    assert result.returncode == 0, result.stderr
    assert shell_app.setting("zulip_lookup") == "api"
    assert not (shell_app.systemd / "wbzp-intake-lookup.socket").exists()


def test_lookup_question_is_only_asked_once(shell_app):
    shell_app.run('set_conf_value zulip_lookup api "$CONFIG_FILE"')

    result = shell_app.run("choose_lookup_mode", stdin="y\n")

    assert result.returncode == 0, result.stderr
    assert shell_app.setting("zulip_lookup") == "api"
    assert "Is this app running on the Zulip server" not in result.stderr


def test_lookup_question_adds_its_settings_to_an_older_config_file(shell_app):
    config = shell_app.root / "instance" / "config.conf"
    config.write_text(
        "".join(line for line in config.read_text().splitlines(keepends=True) if not line.startswith(("zulip_lookup", "zulip_manage_py"))),
        encoding="utf-8",
    )

    result = shell_app.run("choose_lookup_mode", stdin="n" + chr(10))

    assert result.returncode == 0, result.stderr
    assert shell_app.setting("zulip_lookup") == "api"


def test_offer_to_start_starts_the_units_after_confirmation(shell_app):
    result = shell_app.run("offer_to_start 'Start them now?' first.service second.timer", stdin="y" + chr(10))

    assert result.returncode == 0, result.stderr
    assert "systemctl start first.service second.timer" in shell_app.calls()


def test_offer_to_start_does_nothing_without_confirmation(shell_app):
    result = shell_app.run("offer_to_start 'Start them now?' first.service" + chr(10) + "echo status=$?", stdin=chr(10))

    assert "status=1" in result.stdout
    assert not any(call.startswith("systemctl start") for call in shell_app.calls())


def test_restart_is_offered_when_the_service_is_running(shell_app):
    result = shell_app.run("offer_to_restart_service", stdin="y" + chr(10))

    assert result.returncode == 0, result.stderr
    assert "systemctl restart wbzp-intake" in shell_app.calls()


def test_restart_can_be_declined(shell_app):
    result = shell_app.run("offer_to_restart_service", stdin="n" + chr(10))

    assert result.returncode == 0, result.stderr
    assert "Run sudo systemctl restart wbzp-intake when you're ready." in result.stdout
    assert not any(call.startswith("systemctl restart") for call in shell_app.calls())


def test_restart_is_not_offered_when_the_service_is_stopped(shell_app):
    write_executable(shell_app.bin / "systemctl", "#!/bin/sh" + chr(10) + 'echo "systemctl $*" >> "$STUB_LOG"' + chr(10) + "exit 3" + chr(10))

    result = shell_app.run("offer_to_restart_service")

    assert result.returncode == 0, result.stderr
    assert "Changes take effect the next time the wbzp-intake service starts." in result.stdout
    assert not any(call.startswith("systemctl restart") for call in shell_app.calls())


PROXY_CONFIGS = {
    "nginx": {
        "old": "server {\n    listen 443 ssl;\n    location /apply {\n        proxy_pass http://127.0.0.1:8793;\n    }\n}\n",
        "new": "server {\n    listen 443 ssl;\n    location /intake {\n        proxy_pass http://127.0.0.1:8793;\n    }\n}\n",
        "commented": "server {\n    # location /intake {\n    #     proxy_pass http://127.0.0.1:8793;\n    # }\n}\n",
        "unrelated": "server {\n    location /application {\n        proxy_pass http://127.0.0.1:8793;\n    }\n}\n",
    },
    "apache": {
        "old": '<VirtualHost *:443>\n    ProxyPass "/apply" "http://127.0.0.1:8793/apply"\n    ProxyPassReverse "/apply" "http://127.0.0.1:8793/apply"\n</VirtualHost>\n',
        "new": '<VirtualHost *:443>\n    ProxyPass "/intake" "http://127.0.0.1:8793/intake"\n    ProxyPassReverse "/intake" "http://127.0.0.1:8793/intake"\n</VirtualHost>\n',
        "commented": '<VirtualHost *:443>\n    # ProxyPass "/intake" "http://127.0.0.1:8793/intake"\n</VirtualHost>\n',
        "unrelated": '<VirtualHost *:443>\n    ProxyPass "/application" "http://127.0.0.1:8793/application"\n</VirtualHost>\n',
    },
    "caddy": {
        "old": "example.com {\n    handle /apply* {\n        reverse_proxy 127.0.0.1:8793\n    }\n}\n",
        "new": "example.com {\n    handle /intake* {\n        reverse_proxy 127.0.0.1:8793\n    }\n}\n",
        "commented": "example.com {\n    # handle /intake* {\n    #     reverse_proxy 127.0.0.1:8793\n    # }\n}\n",
        "unrelated": "example.com {\n    handle /application* {\n        reverse_proxy 127.0.0.1:8793\n    }\n}\n",
    },
}


def route_exists(shell_app, kind, text, route):
    conf = shell_app.root / "site.conf"
    conf.write_text(text, encoding="utf-8", newline="\n")
    result = shell_app.run(f"proxy_route_exists {kind} site.conf {route} && echo yes || echo no")
    return result.stdout.strip() == "yes"


@pytest.mark.parametrize("kind", ["nginx", "apache", "caddy"])
def test_proxy_route_detection_checks_the_specific_route(shell_app, kind):
    configs = PROXY_CONFIGS[kind]

    assert route_exists(shell_app, kind, configs["new"], "/intake")
    assert not route_exists(shell_app, kind, configs["old"], "/intake")
    assert route_exists(shell_app, kind, configs["old"], "/apply")
    assert not route_exists(shell_app, kind, configs["commented"], "/intake")
    assert not route_exists(shell_app, kind, configs["unrelated"], "/apply")
    assert not route_exists(shell_app, kind, configs["unrelated"], "/intake")


@pytest.mark.parametrize("kind", ["nginx", "apache", "caddy"])
def test_old_apply_route_is_renamed_to_intake(shell_app, kind):
    conf = shell_app.root / "site.conf"
    conf.write_text(PROXY_CONFIGS[kind]["old"], encoding="utf-8", newline="\n")

    result = shell_app.run(f"rename_proxy_route {kind} site.conf /apply /intake")

    assert result.returncode == 0, result.stderr
    assert conf.read_text() == PROXY_CONFIGS[kind]["new"]
