import ast

from . import zulip_integration
from .errors import DeliveryError

ZULIP_SETTINGS_PATH = "/etc/zulip/settings.py"


def normalize_site_url(value):
    url = value.strip()
    if "://" not in url:
        url = f"https://{url}"
    return url.rstrip("/")


def suggest_site_url(bot_email):
    domain = bot_email.partition("@")[2]
    return f"https://{domain}" if domain else ""


def resolve_site_url(candidate, bot_email, bot_api_key, check_visibility=True):
    site_url = normalize_site_url(candidate)
    response = zulip_integration.get(site_url, "/server_settings", "server settings")
    try:
        settings = response.json()
    except ValueError as error:
        raise DeliveryError("not_zulip", url=site_url) from error
    if not isinstance(settings, dict):
        raise DeliveryError("not_zulip", url=site_url)
    realm_url = settings.get("realm_url") or settings.get("realm_uri")
    if not realm_url:
        raise DeliveryError("no_organization", url=site_url)
    realm_url = realm_url.rstrip("/")
    if check_visibility:
        verify_email_visibility(realm_url, bot_email, bot_api_key)
    return realm_url


def verify_email_visibility(site_url, bot_email, bot_api_key):
    members = zulip_integration.api_get(site_url, bot_email, bot_api_key, "/users", "member list").json()["members"]
    people = [member for member in members if not member["is_bot"]]
    if people and all(member.get("delivery_email") is None for member in people):
        raise DeliveryError("email_visibility", bot_email=bot_email)


def detect_channels(site_url, bot_email, bot_api_key):
    subscriptions = zulip_integration.api_get(site_url, bot_email, bot_api_key, "/users/me/subscriptions", "subscriptions").json()[
        "subscriptions"
    ]
    return [
        (f"{channel['name']} (private)" if channel["invite_only"] else channel["name"], channel["stream_id"])
        for channel in sorted(subscriptions, key=lambda channel: channel["name"].casefold())
    ]


def read_email_server(path=None):
    path = path or ZULIP_SETTINGS_PATH
    try:
        with open(path) as f:
            tree = ast.parse(f.read(), filename=path)
    except (OSError, SyntaxError) as error:
        raise DeliveryError("settings_unreadable", path=path, error=error) from error
    values = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    try:
                        values[target.id] = ast.literal_eval(node.value)
                    except ValueError:
                        continue
    if values.get("EMAIL_USE_SSL"):
        security = "ssl"
    elif values.get("EMAIL_USE_TLS"):
        security = "starttls"
    else:
        security = "none"
    return values.get("EMAIL_HOST", ""), values.get("EMAIL_PORT", ""), security
