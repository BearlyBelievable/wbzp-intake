import logging

from . import alerts, db, delivery, errors, zulip_integration

logger = logging.getLogger(__name__)


def _resolve_awaiting_signup(email):
    try:
        status = zulip_integration.check_submission_email(email)
    except Exception as error:
        errors.log_failure(logger, "Failed to check status for a submission awaiting signup", error)
        return "failed"
    if status in ("registered", "invited"):
        db.finish_submission(email)
        return "purged"
    return "unchanged"


def check_awaiting_signup():
    emails = db.list_awaiting_signup()

    results = [_resolve_awaiting_signup(email) for email in emails]
    purged = results.count("purged")
    failed = results.count("failed")

    logger.info("Checked %d submission(s) awaiting signup, purged %d.", len(emails), purged)

    if failed:
        alerts.notify_admin(
            "pending_check_partial_failure", {"failed_count": failed, "total_count": len(emails)}
        )


def _retry_failed_submission(email, body, destination):
    try:
        delivery.retry(email, body, destination)
    except Exception as error:
        errors.log_failure(logger, "Retry failed for a failed submission", error)
        return False
    return True


def retry_failed_submissions():
    failed = db.list_failed_submissions()

    results = [_retry_failed_submission(email, body, destination) for email, body, destination in failed]
    resolved = sum(results)
    still_failing = len(failed) - resolved

    logger.info("Retried %d failed submission(s), resolved %d.", len(failed), resolved)

    if still_failing:
        alerts.notify_admin("submissions_still_failing", {"count": still_failing})
