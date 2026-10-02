import logging

from . import answers, config, delivery, destinations, errors, forms, message_body, rate_limit, routing, strings, turnstile

logger = logging.getLogger(__name__)


def _error(status, section, key, **params):
    return status, {"error": strings.message(section, key, **params)}


def _check_ip_rate_limit(remote_addr):
    try:
        attempt_id = rate_limit.record_ip(remote_addr)
    except errors.DatabaseError as error:
        errors.log_failure(logger, "Rate limit check failed", error)
        return None, _error(503, "common", "server_busy")
    if attempt_id is None:
        return None, _error(429, "common", "rate_limited")
    return attempt_id, None


def _check_turnstile(form, remote_addr):
    turnstile_secret = config.read_secret("turnstile_secret", default="")
    if not turnstile_secret:
        return None
    token = form.get("cf-turnstile-response", "")
    if not token:
        return _error(400, "common", "verification_failed")
    try:
        verified = turnstile.verify_turnstile(turnstile_secret, token, remote_addr)
    except Exception as error:
        errors.log_failure(logger, "Turnstile verification request failed", error)
        return _error(503, "common", "server_busy")
    if not verified:
        return _error(400, "common", "verification_failed")
    return None


def _check_email_rate_limit(attempt_id, email_key):
    try:
        limited = rate_limit.record_email(attempt_id, email_key)
    except errors.DatabaseError as error:
        errors.log_failure(logger, "Rate limit check failed", error)
        return _error(503, "common", "server_busy")
    if limited:
        return _error(429, "common", "rate_limited")
    return None


def _respond_for_outcome(outcome, destination_type):
    match outcome:
        case "failed":
            return _error(502, "common", "submission_failed")
        case "registered":
            return _error(400, "zulip", "registered")
        case "invited":
            return _error(400, "zulip", "invited")
        case "pending":
            contact_email = config.read_config("contact_email")
            return _error(400, destination_type, "pending", contact_email=contact_email)

    return 200, {"success": True}


def handle(template_name, template, form, remote_addr):
    error = _check_turnstile(form, remote_addr)
    if error:
        return error

    attempt_id, error = _check_ip_rate_limit(remote_addr)
    if error:
        return error

    active_fields = answers.flatten_active(form, template["fields"])
    values, errors = answers.validate(form, active_fields)
    if errors:
        return _error(400, "common", "invalid_answers")

    email = values[forms.EMAIL_FIELD_NAME]
    email_key = email.lower()

    error = _check_email_rate_limit(attempt_id, email_key)
    if error:
        return error

    destination = destinations.tag_destination(routing.resolve(template, values), template_name)
    body = message_body.render(destination, template_name, active_fields, values)

    outcome = delivery.process(email_key, body, destination)
    return _respond_for_outcome(outcome, destination.type)
