import imaplib
import smtplib
import ssl

import pytest
import waitress

from intake import migrations, pending_check

SUBJECT = "Undelivered Mail Returned to Sender"
NON_BOUNCE = b"Subject: Hello\n\nJust checking in.\n"
PLAIN_BOUNCE = b"Subject: Mail delivery failed: returning message to sender\n\nThe address was refused.\n"


def bounce(*addresses):
    blocks = "".join(f"Final-Recipient: rfc822; {a}\nAction: failed\nStatus: 5.1.1\n\n" for a in addresses)
    return (
        f"Subject: {SUBJECT}\n"
        'Content-Type: multipart/report; report-type=delivery-status; boundary="XYZ"\n\n'
        "--XYZ\nContent-Type: text/plain\n\nDelivery failed.\n\n"
        "--XYZ\nContent-Type: message/delivery-status\n\n"
        f"Reporting-MTA: dns; example.com\n\n{blocks}--XYZ--\n"
    ).encode()


def alert_text(address):
    return f"An email to {address} bounced (subject: {SUBJECT}). Check that destination's address is still correct."


@pytest.fixture
def mailbox_app(make_app):
    return make_app(
        config={"bounce_imap_host": "imap.test", "bounce_imap_user": "bounces@example.com"},
        secrets={"bounce_imap_password": "imap-pass"},
    )


def test_bounce_alerts(mailbox_app, fake_imap, fake_smtp):
    fake_imap.messages = {
        b"1": bounce("one@example.com", "two@example.com"),
        b"2": NON_BOUNCE,
        b"3": bounce(),
        b"4": PLAIN_BOUNCE,
    }

    result = mailbox_app.invoke("bounce-check")

    assert result.exit_code == 0
    assert fake_imap.connections == [("imap.test", 993)]
    assert fake_imap.logins == [("bounces@example.com", "imap-pass")]
    assert fake_imap.contexts[0].verify_mode == ssl.CERT_REQUIRED
    assert fake_imap.contexts[0].check_hostname is True
    assert [mail.message.get_content().strip() for mail in fake_smtp.sent] == [
        alert_text("one@example.com"),
        alert_text("two@example.com"),
        alert_text("(address not found in the bounce message)"),
        "An email to (address not found in the bounce message) bounced (subject: Mail delivery failed: returning "
        "message to sender). Check that destination's address is still correct.",
    ]
    assert fake_imap.seen == [b"1", b"3", b"4"]
    assert set(fake_imap.fetch_parts) == {"(BODY.PEEK[])"}


def test_bounce_check_blank_port_uses_default(make_app, fake_imap, fake_smtp):
    app_instance = make_app(
        config={"bounce_imap_host": "imap.test", "bounce_imap_user": "bounces@example.com", "bounce_imap_port": ""},
        secrets={"bounce_imap_password": "imap-pass"},
    )

    result = app_instance.invoke("bounce-check")

    assert result.exit_code == 0
    assert fake_imap.connections == [("imap.test", 993)]


def test_bounce_check_folder(make_app, fake_imap, fake_smtp):
    default_folder = make_app()
    default_folder.invoke("bounce-check")
    assert fake_imap.selected == ['"INBOX"']

    fake_imap.selected.clear()
    custom = make_app(config={"bounce_imap_folder": "[Gmail]/All Mail"})
    assert custom.invoke("bounce-check").exit_code == 0
    assert fake_imap.selected == ['"[Gmail]/All Mail"']


def test_bounce_check_missing_folder(make_app, fake_imap, fake_smtp):
    app_instance = make_app(config={"bounce_imap_folder": "Bounces"})
    fake_imap.missing_folders = {"Bounces"}

    result = app_instance.invoke("bounce-check")

    assert result.exit_code == 1
    assert "Could not check the bounce mailbox imap.test:993: the folder 'Bounces' could not be opened" in result.output


def test_test_email_checks_the_folder(make_app, fake_smtp, fake_imap):
    app_instance = make_app(config={"bounce_imap_folder": "Bounces"})
    fake_imap.missing_folders = {"Bounces"}

    result = app_instance.invoke("test-email")

    assert result.exit_code == 1
    assert "the folder 'Bounces' could not be opened" in result.output


def test_bounce_check_external_source(make_app, fake_imap, fake_smtp, caplog):
    app_instance = make_app(config={"bounce_source": "external"})

    result = app_instance.invoke("bounce-check")

    assert result.exit_code == 0
    assert fake_imap.connections == []
    assert "bounce_source is not 'imap'" in caplog.text


def test_bounce_check_skipped_without_email_destinations(make_app, fake_imap, fake_smtp, caplog):
    app_instance = make_app(
        config={"alert_emails_enabled": "no"}, destinations={"default": {"type": "zulip", "channel_id": 7, "topic": "New wbzp-intake submission"}}
    )

    result = app_instance.invoke("bounce-check")

    assert result.exit_code == 0
    assert fake_imap.connections == []
    assert "No destination sends email" in caplog.text


def test_bounce_check_uses_smtp_login_by_default(make_app, fake_imap, fake_smtp):
    app_instance = make_app()

    assert app_instance.invoke("bounce-check").exit_code == 0
    assert fake_imap.connections == [("imap.test", 993)]
    assert fake_imap.logins == [("alerts@example.com", "smtp-pass")]


def test_bounce_check_logs_each_bounce(mailbox_app, fake_imap, fake_smtp, caplog):
    fake_imap.messages = {b"1": bounce("one@example.com")}

    mailbox_app.invoke("bounce-check")

    assert f"An email to one@example.com bounced (subject: {SUBJECT})." in caplog.text


def test_serve_warns_when_bounces_are_external(make_app, monkeypatch, caplog):
    monkeypatch.setattr(waitress, "serve", lambda app, host, port, threads: None)
    app_instance = make_app(config={"bounce_source": "external"})

    app_instance.invoke("serve")

    assert "this app isn't checking for bounced emails" in caplog.text


def test_bounce_check_skips(mailbox_app, fake_imap, fake_smtp, caplog):
    fake_imap.messages = {b"1": bounce("lost@example.com"), b"2": bounce("found@example.com")}
    fake_imap.unfetchable = {b"1"}

    assert mailbox_app.invoke("bounce-check").exit_code == 0
    assert [mail.message.get_content().strip() for mail in fake_smtp.sent] == [alert_text("found@example.com")]
    assert fake_imap.seen == [b"2"]

    fake_smtp.sent.clear()
    fake_imap.search_status = "NO"
    assert mailbox_app.invoke("bounce-check").exit_code == 0
    assert fake_smtp.sent == []
    assert "Failed to search the bounce mailbox: NO" in caplog.text


@pytest.mark.parametrize(
    "error, expected",
    [
        (imaplib.IMAP4.error("LOGIN failed"), "Error: Could not check the bounce mailbox imap.test:993"),
        (ConnectionRefusedError("refused"), "Error: Could not check the bounce mailbox imap.test:993"),
        (RuntimeError("weird"), None),
    ],
    ids=["login-rejected", "unreachable", "unexpected"],
)
def test_bounce_check_failure(mailbox_app, fake_imap, fake_smtp, error, expected):
    fake_imap.login_error = error

    result = mailbox_app.invoke("bounce-check")

    assert result.exit_code == 1
    if expected:
        assert expected in result.output
        assert isinstance(result.exception, SystemExit)
    else:
        assert isinstance(result.exception, RuntimeError)
    assert "The bounce mailbox check crashed before finishing" in fake_smtp.sent[0].message.get_content()


def test_test_email(app_instance, fake_smtp, fake_imap):
    result = app_instance.invoke("test-email")

    assert result.exit_code == 0
    assert "Sent a test email to admin@example.com." in result.output
    assert "Logged in to the bounce mailbox at imap.test." in result.output
    [sent] = fake_smtp.sent
    assert sent.message["Subject"] == "wbzp-intake test email"
    assert sent.recipients == ["admin@example.com"]
    assert fake_imap.logins == [("alerts@example.com", "smtp-pass")]
    assert fake_imap.seen == []


def test_test_email_with_external_bounces(make_app, fake_smtp, fake_imap):
    app_instance = make_app(config={"bounce_source": "external"})

    result = app_instance.invoke("test-email")

    assert result.exit_code == 0
    assert "bounce mailbox" not in result.output
    assert fake_imap.connections == []


def test_test_email_smtp_failure(app_instance, fake_smtp, fake_imap):
    fake_smtp.login_error = smtplib.SMTPAuthenticationError(535, b"bad credentials")

    result = app_instance.invoke("test-email")

    assert result.exit_code == 1
    assert "the server rejected the SMTP login" in result.output
    assert fake_imap.connections == []


def test_test_email_imap_failure(app_instance, fake_smtp, fake_imap):
    fake_imap.login_error = imaplib.IMAP4.error("LOGIN failed")

    result = app_instance.invoke("test-email")

    assert result.exit_code == 1
    assert "Sent a test email" in result.output
    assert "Could not check the bounce mailbox imap.test:993" in result.output


def test_test_email_without_smtp(make_app, fake_smtp):
    app_instance = make_app(
        config={"alert_emails_enabled": "no"},
        destinations={"default": {"type": "zulip", "channel_id": 7, "topic": "New wbzp-intake submission"}},
        remove_config_keys=["smtp_host", "smtp_port", "smtp_user"],
        secrets={"smtp_password": ""},
    )

    result = app_instance.invoke("test-email")

    assert result.exit_code == 1
    assert "a test email was requested but the SMTP settings are incomplete" in result.output
    assert fake_smtp.sent == []


def test_pending_check(app_instance, fake_zulip, fake_smtp, caplog):
    for index, name in enumerate("abc"):
        assert app_instance.apply(submitter_email=f"{name}@example.com", ip=f"203.0.113.{index + 1}").status_code == 200
    fake_zulip.users["a@example.com"] = True

    assert app_instance.invoke("check-pending").exit_code == 0
    assert app_instance.submissions() == [
        {"email": "b@example.com", "status": "awaiting_signup"},
        {"email": "c@example.com", "status": "awaiting_signup"},
    ]
    assert "Checked 3 submission(s) awaiting signup, purged 1." in caplog.text

    fake_zulip.lookup_status = 500
    assert app_instance.invoke("check-pending").exit_code == 0
    assert len(app_instance.submissions()) == 2
    assert "failed for 2 of 2 email(s)" in fake_smtp.sent[-1].message.get_content()


def test_pending_check_database_error(app_instance, fake_smtp):
    app_instance.db_path.write_bytes(b"garbage" * 200)

    result = app_instance.invoke("check-pending")

    assert result.exit_code == 1
    assert "Error: Could not use the submissions database" in result.output
    assert isinstance(result.exception, SystemExit)
    assert "crashed before finishing" in fake_smtp.sent[0].message.get_content()


def test_pending_check_crash(app_instance, fake_smtp, monkeypatch):
    def explode():
        raise RuntimeError("boom")

    monkeypatch.setattr(pending_check, "retry_failed_submissions", explode)

    result = app_instance.invoke("check-pending")

    assert isinstance(result.exception, RuntimeError)
    assert "crashed before finishing" in fake_smtp.sent[0].message.get_content()


def test_db_migrate(make_app):
    app_instance = make_app(migrate=False)
    latest = len(migrations.MIGRATIONS)

    first = app_instance.invoke("db-migrate")
    assert first.exit_code == 0
    assert f"Migrated {app_instance.db_path} from schema version 0 to {latest}." in first.output
    assert app_instance.query("PRAGMA journal_mode")[0]["journal_mode"] == "wal"

    assert f"is already at schema version {latest}." in app_instance.invoke("db-migrate").output

    back = app_instance.invoke("db-migrate", "--to", "1")
    assert f"Rolled back {app_instance.db_path} from schema version {latest} to 1." in back.output

    assert f"from schema version 1 to {latest}." in app_instance.invoke("db-migrate").output
    assert list((app_instance.instance / "backups").glob("submissions.db.*.from-v*.bak"))
    assert app_instance.invoke("db-migrate", "--to", "99").exit_code == 2


def test_serve_migrates_then_serves(make_app, monkeypatch):
    served = []
    monkeypatch.setattr(waitress, "serve", lambda app, host, port, threads: served.append((app, host, port, threads)))
    app_instance = make_app(migrate=False)

    result = app_instance.invoke("serve")

    assert result.exit_code == 0
    assert served == [(app_instance.app, "127.0.0.1", 8793, 8)]
    assert app_instance.query("SELECT MAX(version) AS version FROM schema_migrations")[0]["version"] == len(
        migrations.MIGRATIONS
    )

    app_instance.invoke("serve", "--host", "0.0.0.0", "--port", "9000")
    assert served[-1][1:] == ("0.0.0.0", 9000, 8)


def test_serve_does_not_start_after_failed_migration(make_app, monkeypatch):
    served = []
    monkeypatch.setattr(waitress, "serve", lambda app, host, port, threads: served.append(host))
    app_instance = make_app()
    app_instance.query("INSERT INTO schema_migrations (version, checksum, applied_at) VALUES (99, 'x', 'now')")

    result = app_instance.invoke("serve")

    assert result.exit_code == 1
    assert "migration 99 applied" in result.output
    assert served == []


def plain_message(subject):
    header = f"Subject: {subject}\n" if subject else ""
    return f"{header}\nJust a message.\n".encode()


READ_RECEIPT = (
    "Subject: Read receipt\n"
    'Content-Type: multipart/report; report-type=disposition-notification; boundary="XYZ"\n\n'
    "--XYZ\nContent-Type: text/plain\n\nRead.\n\n--XYZ--\n"
).encode()


@pytest.mark.parametrize(
    "raw, is_bounce",
    [
        (plain_message("Undelivered Mail Returned to Sender"), True),
        (plain_message("Delivery Status Notification (Failure)"), True),
        (plain_message("Returned to sender"), True),
        (plain_message("Failure notice"), True),
        (plain_message("Mail delivery failed: returning message"), True),
        (plain_message("UNDELIVERED: hello"), True),
        (plain_message("Re: lunch on friday"), False),
        (plain_message(""), False),
        (READ_RECEIPT, False),
    ],
    ids=[
        "undelivered",
        "delivery-status-notification",
        "returned-to-sender",
        "failure-notice",
        "mail-delivery-failed",
        "uppercase-subject",
        "ordinary-subject",
        "no-subject",
        "other-report-type",
    ],
)
def test_bounce_detection(mailbox_app, fake_imap, fake_smtp, raw, is_bounce):
    fake_imap.messages = {b"1": raw}

    assert mailbox_app.invoke("bounce-check").exit_code == 0

    assert len(fake_smtp.sent) == (1 if is_bounce else 0)
    assert fake_imap.seen == ([b"1"] if is_bounce else [])


def make_a_directory(app_instance):
    app_instance.db_path.unlink()
    app_instance.db_path.mkdir()


def claim_a_newer_migration(app_instance):
    app_instance.query("INSERT INTO schema_migrations VALUES (99, 'x', 'now')")


def tamper_with_an_applied_migration(app_instance):
    app_instance.query("UPDATE schema_migrations SET checksum = 'tampered' WHERE version = 1")


@pytest.mark.parametrize(
    "damage, fragment",
    [
        (make_a_directory, "Could not use the submissions database"),
        (claim_a_newer_migration, "has migration 99 applied, but this code only has"),
        (tamper_with_an_applied_migration, "was changed after it was applied"),
    ],
    ids=["unopenable", "database-ahead-of-code", "edited-migration"],
)
def test_db_migrate_errors(app_instance, damage, fragment):
    damage(app_instance)

    result = app_instance.invoke("db-migrate")

    assert result.exit_code == 1
    assert fragment in result.output
    assert isinstance(result.exception, SystemExit)
