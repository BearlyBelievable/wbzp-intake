import logging

from . import config, errors, mail

logger = logging.getLogger(__name__)

ALERT_SUBJECT = "Intake form needs attention"

ALERTS = {
    "submission_failed": (
        "A submission from {email} failed to deliver due to a technical issue and has been saved locally. "
        "It will be retried automatically, or run `flask --app app check-pending` to retry now."
    ),
    "submission_lost": (
        "A submission from {email} failed to deliver and could not be saved locally, so it was lost. "
        "Check the server logs for details."
    ),
    "pending_check_partial_failure": (
        "The daily pending-submission check failed for {failed_count} of {total_count} email(s). "
        "Check the server logs for details."
    ),
    "pending_check_crashed": (
        "The daily pending-submission check crashed before finishing. Check the server logs for details."
    ),
    "submissions_still_failing": (
        "{count} failed submission(s) still couldn't be delivered on retry. Check the server logs for details."
    ),
    "bounce_detected": (
        "An email to {address} bounced (subject: {subject}). Check that destination's address is still correct."
    ),
    "bounce_check_crashed": "The bounce mailbox check crashed before finishing. Check the server logs for details.",
}


def notify_admin(key, fields=None):
    body = ALERTS[key].format(**(fields or {}))
    if config.read_config("alert_emails_enabled", default="no") != "yes":
        logger.info("Alert emails are disabled, so the %s alert was not sent.", key)
        return
    try:
        contact_email = config.read_config("contact_email")
        email = mail.Email(to=contact_email, subject=ALERT_SUBJECT, body=body)
        email.send()
    except Exception as error:
        errors.log_failure(logger, "Failed to notify the admin of a failure", error)
