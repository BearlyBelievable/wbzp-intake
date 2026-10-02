import logging

from . import alerts, db, destinations, errors, mail, zulip_integration

logger = logging.getLogger(__name__)


def _resolve_existing_zulip_registration(email):
    status = zulip_integration.check_submission_email(email)
    match status:
        case "registered":
            db.finish_submission(email)
            logger.info("Submission received with an email that already has an account")
            return "registered"
        case "invited":
            db.finish_submission(email)
            return "invited"
    return None


def _post_zulip(body, destination):
    zulip_integration.post_to_zulip(destination.subject, body, destination.channel_id)


def _send_email(email, body, destination):
    subject = destination.subject or destinations.DEFAULT_SUBJECT
    to, *bcc = destination.address
    mail.Email(to=to, subject=subject, body=body, bcc=bcc, reply_to=email).send()


def _deliver_zulip(email, body, destination):
    outcome = _resolve_existing_zulip_registration(email)
    if outcome:
        return outcome
    _post_zulip(body, destination)
    db.await_signup(email)
    return "posted"


def _deliver_email(email, body, destination):
    _send_email(email, body, destination)
    db.finish_submission(email)
    return "posted"


DELIVERERS = {"zulip": _deliver_zulip, "email": _deliver_email}


def process(email, body, destination):
    if not db.start_submission(email):
        return "pending"
    try:
        return DELIVERERS[destination.type](email, body, destination)
    except Exception as error:
        errors.log_failure(logger, "Failed to deliver a submission", error)
        _record_failure(email, body, destination)
        return "failed"


def retry(email, body, destination):
    return DELIVERERS[destination.type](email, body, destination)


def _record_failure(email, body, destination):
    try:
        db.fail_submission(email, body, destination)
    except Exception as error:
        errors.log_failure(logger, "Failed to save a failed submission", error)
        alerts.notify_admin("submission_lost", {"email": email})
        db.finish_submission(email)
        return
    alerts.notify_admin("submission_failed", {"email": email})
