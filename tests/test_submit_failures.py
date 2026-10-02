import sqlite3

from app_harness import form_text

from intake import answers


def test_outdated_schema(app_instance, fake_zulip, caplog):
    assert app_instance.invoke("db-migrate", "--to", "1").exit_code == 0

    response = app_instance.apply()

    assert response.status_code == 503
    assert response.get_json()["error"] == app_instance.message("common", "server_busy")
    assert "flask db-migrate" in caplog.text
    assert fake_zulip.lookups == 0


def test_unreadable_database(app_instance, fake_zulip):
    app_instance.db_path.write_bytes(b"this is not a sqlite database" * 100)

    response = app_instance.apply()

    assert response.status_code == 503
    assert response.get_json()["error"] == app_instance.message("common", "server_busy")


def test_zulip_failure_and_retry(app_instance, fake_zulip, fake_smtp):
    fake_zulip.post_status = 500

    response = app_instance.apply()

    assert response.status_code == 502
    assert response.get_json()["error"] == app_instance.message("common", "submission_failed")
    assert app_instance.submissions() == [{"email": "sam@example.com", "status": "failed"}]
    alert = fake_smtp.sent[0].message
    assert (alert["To"], alert["Subject"]) == ("admin@example.com", "Intake form needs attention")
    assert "sam@example.com" in alert.get_content()

    repeat = app_instance.apply()
    assert repeat.get_json()["error"] == app_instance.message("zulip", "pending", contact_email="admin@example.com")

    assert app_instance.invoke("check-pending").exit_code == 0
    assert app_instance.submissions() == [{"email": "sam@example.com", "status": "failed"}]
    assert "1 failed submission(s) still couldn't be delivered" in fake_smtp.sent[1].message.get_content()

    fake_zulip.post_status = 200
    assert app_instance.invoke("check-pending").exit_code == 0
    assert app_instance.submissions() == [{"email": "sam@example.com", "status": "awaiting_signup"}]
    assert len(fake_zulip.posts) == 1
    assert fake_zulip.posts[0]["content"].startswith("**Form:** application")


def test_email_failure_and_retry(make_app, fake_smtp, caplog):
    app_instance = make_app(forms={"office_contact": form_text("office_contact")})
    fake_smtp.connect_error = ConnectionRefusedError("smtp is down")

    response = app_instance.post("office_contact", submitter_email="visitor@example.com", topic="Billing")

    assert response.status_code == 502
    assert app_instance.submissions() == [{"email": "visitor@example.com", "status": "failed"}]
    assert fake_smtp.sent == []
    assert "Failed to notify the admin of a failure" in caplog.text

    fake_smtp.connect_error = None
    assert app_instance.invoke("check-pending").exit_code == 0
    assert [mail.recipients[0] for mail in fake_smtp.sent] == ["office@example.com"]
    assert app_instance.submissions() == []


def test_lost_submission(app_instance, fake_zulip, fake_smtp):
    fake_zulip.post_status = 500
    conn = sqlite3.connect(app_instance.db_path)
    conn.execute(
        "CREATE TRIGGER block_failed BEFORE UPDATE ON submissions WHEN NEW.status = 'failed' "
        "BEGIN SELECT RAISE(ABORT, 'disk I/O error'); END"
    )
    conn.commit()
    conn.close()

    response = app_instance.apply()

    assert response.status_code == 502
    assert app_instance.submissions() == []
    assert "could not be saved locally, so it was lost" in fake_smtp.sent[0].message.get_content()


def test_http_errors(make_app):
    app_instance = make_app(config={"max_body_bytes": "200"})

    unknown = app_instance.post("nope")
    assert unknown.status_code == 404
    assert unknown.get_json()["error"] == app_instance.message("common", "unknown_form")
    assert app_instance.client.get("/intake/application").status_code == 405
    assert app_instance.apply(bio="x" * 300).status_code == 413


def test_unexpected_error_500(app_instance, monkeypatch, caplog):
    def explode(form, fields):
        raise RuntimeError("boom")

    monkeypatch.setattr(answers, "validate", explode)

    response = app_instance.apply()

    assert response.status_code == 500
    assert response.get_json()["error"] == app_instance.message("common", "server_error")
    assert "Unhandled error while handling a request" in caplog.text
    assert "RuntimeError: boom" in caplog.text


def test_write_failure_503(app_instance, fake_zulip, caplog):
    conn = sqlite3.connect(app_instance.db_path)
    conn.execute(
        "CREATE TRIGGER block_insert BEFORE INSERT ON submissions BEGIN SELECT RAISE(ABORT, 'disk I/O error'); END"
    )
    conn.commit()
    conn.close()

    response = app_instance.apply()

    assert response.status_code == 503
    assert response.get_json()["error"] == app_instance.message("common", "server_busy")
    assert "Could not use the submissions database" in caplog.text
    assert fake_zulip.posts == []


def test_email_attempt_failure_503(app_instance, fake_zulip):
    conn = sqlite3.connect(app_instance.db_path)
    conn.execute(
        "CREATE TRIGGER block_update BEFORE UPDATE ON submission_attempts BEGIN SELECT RAISE(ABORT, 'disk I/O error'); END"
    )
    conn.commit()
    conn.close()

    response = app_instance.apply()

    assert response.status_code == 503
    assert response.get_json()["error"] == app_instance.message("common", "server_busy")
    assert fake_zulip.posts == []
    assert app_instance.submissions() == []
