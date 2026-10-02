import sqlite3
from pathlib import Path

from fake_services import ZULIP_URL


REPO_ROOT = Path(__file__).resolve().parent.parent


FIXTURES = Path(__file__).resolve().parent / "fixtures"


DEFAULT_CONFIG = {
    "contact_email": "admin@example.com",
    "zulip_site_url": ZULIP_URL,
    "zulip_bot_email": "bot@zulip.test",
    "alert_emails_enabled": "yes",
    "smtp_host": "smtp.test",
    "smtp_port": "587",
    "smtp_user": "alerts@example.com",
    "bounce_source": "imap",
    "bounce_imap_host": "imap.test",
}


DEFAULT_SECRETS = {"zulip_bot_api_key": "bot-key", "smtp_password": "smtp-pass"}


DEFAULT_DESTINATIONS = {
    "default": {"type": "zulip", "channel_id": 7, "topic": "New wbzp-intake submission"},
    "office": {"type": "email", "address": ["office@example.com", "audit@example.com"], "subject": "Office request"},
}


APPLICATION = {
    "full_name": "Sam Tester",
    "submitter_email": "sam@example.com",
    "age": "30",
    "role_interest": "Member",
    "agree_to_rules": "I agree to follow the community guidelines",
}


def ini(section, values):
    return f"[{section}]\n" + "".join(f"{key} = {value}\n" for key, value in values.items())


def form_text(name):
    return (FIXTURES / "forms" / f"{name}.yaml").read_text(encoding="utf-8")


class AppInstance:
    def __init__(self, app, tmp_path):
        self.app = app
        self.client = app.test_client()
        self.runner = app.test_cli_runner()
        self.instance = tmp_path / "instance"
        self.db_path = self.instance / "submissions.db"

    def post(self, form="application", ip="198.51.100.1", **answers):
        return self.client.post(f"/intake/{form}", data=answers, environ_overrides={"REMOTE_ADDR": ip})

    def apply(self, ip="198.51.100.1", **overrides):
        return self.post("application", ip, **{**APPLICATION, **overrides})

    def invoke(self, *args):
        return self.runner.invoke(args=list(args))

    def query(self, sql, parameters=()):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = [dict(row) for row in conn.execute(sql, parameters)]
            conn.commit()
            return rows
        finally:
            conn.close()

    def submissions(self):
        return self.query("SELECT email, status FROM submissions ORDER BY email")

    def message(self, section, key, **params):
        return self.app.config["STRINGS"][section][key].format(**params)
