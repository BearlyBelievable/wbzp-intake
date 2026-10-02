import os

import pytest
from app_harness import form_text


def forms_dir(app_instance):
    return app_instance.instance.parent / "forms"


def bump(path):
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


def test_new_form_is_served_without_a_restart(make_app, fake_smtp):
    app_instance = make_app()
    assert app_instance.post("office_contact", submitter_email="a@example.com").status_code == 404

    (forms_dir(app_instance) / "office_contact.yaml").write_text(form_text("office_contact"), encoding="utf-8")

    assert app_instance.post("office_contact", submitter_email="a@example.com", topic="Billing").status_code == 200
    assert len(fake_smtp.sent) == 1


def test_edited_routing_is_used_without_a_restart(make_app, fake_zulip, fake_smtp):
    app_instance = make_app()
    assert app_instance.apply(ip="203.0.113.1").status_code == 200
    assert len(fake_zulip.posts) == 1

    path = forms_dir(app_instance) / "application.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("  - destination: default", "  - destination: office"), encoding="utf-8")
    bump(path)

    assert app_instance.apply(ip="203.0.113.2", submitter_email="other@example.com").status_code == 200
    assert len(fake_zulip.posts) == 1
    assert [mail.recipients[0] for mail in fake_smtp.sent] == ["office@example.com"]


def test_invalid_edit_keeps_the_last_valid_form(make_app, fake_zulip, caplog):
    app_instance = make_app()
    path = forms_dir(app_instance) / "application.yaml"
    path.write_text("routing_rules: [", encoding="utf-8")
    bump(path)

    assert app_instance.apply(ip="203.0.113.1").status_code == 200
    assert app_instance.apply(ip="203.0.113.2", submitter_email="other@example.com").status_code == 200

    assert len(fake_zulip.posts) == 2
    assert caplog.text.count("last valid version is still in use") == 1


def test_form_that_was_never_valid_is_not_served(make_app, caplog):
    app_instance = make_app()
    (forms_dir(app_instance) / "broken.yaml").write_text("routing_rules: [", encoding="utf-8")

    assert app_instance.post("broken", submitter_email="a@example.com").status_code == 404
    assert app_instance.post("broken", submitter_email="a@example.com").status_code == 404
    assert caplog.text.count("The broken form was changed") == 1


def test_deleted_form_stops_being_served(make_app):
    app_instance = make_app()
    (forms_dir(app_instance) / "application.yaml").unlink()

    assert app_instance.apply().status_code == 404
