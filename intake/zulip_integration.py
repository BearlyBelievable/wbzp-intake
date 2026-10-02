import urllib.parse

import requests

from . import config, zulip_manage
from .errors import ConfigError, DeliveryError

ZULIP_API_TIMEOUT_SECONDS = 10
UNKNOWN_USER_MESSAGE = "No such user"
LOOKUP_MODES = ("api", "manage_py")


def get(site_url, path, label, auth=None, accepted_statuses=()):
    try:
        response = requests.get(f"{site_url}/api/v1{path}", auth=auth, timeout=ZULIP_API_TIMEOUT_SECONDS)
        if response.status_code not in accepted_statuses:
            response.raise_for_status()
    except requests.RequestException as error:
        raise DeliveryError("api_failed", label=label, error=error) from error
    return response


def api_get(site_url, bot_email, bot_api_key, path, label, accepted_statuses=()):
    return get(site_url, path, label, auth=(bot_email, bot_api_key), accepted_statuses=accepted_statuses)


def _find_user(site_url, bot_email, bot_api_key, email):
    response = api_get(
        site_url,
        bot_email,
        bot_api_key,
        f"/users/{urllib.parse.quote(email, safe='')}",
        "user lookup",
        accepted_statuses=(400, 404),
    )
    if response.status_code == 200:
        return response.json()["user"]
    if response.status_code == 404 or _error_message(response) == UNKNOWN_USER_MESSAGE:
        return None
    raise DeliveryError("api_failed", label="user lookup", error=requests.HTTPError(response=response))


def _error_message(response):
    try:
        return response.json().get("msg")
    except (ValueError, AttributeError):
        return None


def lookup_email_status(site_url, bot_email, bot_api_key, email):
    user = _find_user(site_url, bot_email, bot_api_key, email)
    if user is not None and user["is_active"]:
        return "registered"
    return "none"


def read_lookup_mode():
    mode = config.read_config("zulip_lookup", default="api")
    if mode not in LOOKUP_MODES:
        raise ConfigError("invalid_setting", name="zulip_lookup", value=mode)
    return mode


def check_submission_email(email):
    if read_lookup_mode() == "manage_py":
        return zulip_manage.lookup_email_status(email)
    return lookup_email_status(
        config.read_config("zulip_site_url"),
        config.read_config("zulip_bot_email"),
        config.read_secret("zulip_bot_api_key"),
        email,
    )


def post_to_zulip(topic, body, channel):
    site_url = config.read_config("zulip_site_url")
    bot_email = config.read_config("zulip_bot_email")
    bot_api_key = config.read_secret("zulip_bot_api_key")
    try:
        response = requests.post(
            f"{site_url}/api/v1/messages",
            auth=(bot_email, bot_api_key),
            data={
                "type": "stream",
                "to": channel,
                "topic": topic,
                "content": body,
            },
            timeout=ZULIP_API_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as error:
        raise DeliveryError("post_failed", channel=channel, error=error) from error
