import dataclasses
import logging

import yaml
from flask import current_app

from . import config
from .errors import ConfigError

logger = logging.getLogger(__name__)

DESTINATION_REQUIRED_KEYS = {
    "zulip": {"channel_id", "topic"},
    "email": {"address"},
}

PLACEHOLDER_ADDRESS = "example@domain.com"

DEFAULT_SUBJECT = "New wbzp-intake submission"

DEFAULT_FILE_LINES = (
    "# Destinations are where form submissions can be sent. Edit this file",
    "# to add more options to deliver to.",
    "#",
    "# You can edit this default destination, but do not remove it or rename it,",
    "# as it's needed as a fall-back for all forms:",
    "default:",
    "    type: email",
    "    address:",
    "        - {address}",
    "    subject: {subject}",
    "#",
    "# To add a new destination, assign it a name, define its type, and",
    "# give it a subject. The currently supported destination types are email",
    "# and zulip:",
    "#",
    "# email_example:",
    "#    type: email",
    "#    address:",
    "#        - to@domain.com",
    "#        - bcc@domain.com",
    "#    subject: email subject",
    "#",
    "# zulip_example:",
    "#    type: zulip",
    "#    channel_id: the ID number of the Zulip channel to post in",
    "#    topic: the topic to post under",
    "#",
    "# Zulip destinations can also be added by running:",
    "#",
    "# sudo ./configure-zulip.sh",
    "#",
)


def default_file_text(address=PLACEHOLDER_ADDRESS):
    scalar = yaml.safe_dump(address, width=float("inf")).splitlines()[0]
    return "\n".join(DEFAULT_FILE_LINES).replace("{address}", scalar).replace("{subject}", DEFAULT_SUBJECT) + "\n"


PLACEHOLDER_DESTINATIONS = yaml.safe_load(default_file_text())


@dataclasses.dataclass
class Destination:
    type: str
    channel_id: int | None = None
    address: list | None = None
    subject: str | None = None

    @classmethod
    def from_dict(cls, data):
        return cls(
            type=data["type"],
            channel_id=data.get("channel_id"),
            address=data.get("address"),
            subject=data.get("topic") if data["type"] == "zulip" else data.get("subject"),
        )

    @classmethod
    def from_row(cls, destination_type, channel_id, to_address, bcc_addresses, subject):
        address = None
        if to_address:
            address = [to_address, *bcc_addresses.split(",")] if bcc_addresses else [to_address]
        return cls(type=destination_type, channel_id=channel_id, address=address, subject=subject)

    def to_row(self):
        to_address, bcc_addresses = None, None
        if self.address:
            to_address, *bcc = self.address
            bcc_addresses = ",".join(bcc) if bcc else None
        return self.type, self.channel_id, to_address, bcc_addresses, self.subject


def validate(destinations, path=None):
    location = f" in {path}" if path else ""
    if not isinstance(destinations, dict):
        raise ConfigError("invalid_destinations_file", location=location)
    for name, destination in destinations.items():
        required = DESTINATION_REQUIRED_KEYS.get(destination.get("type")) if isinstance(destination, dict) else None
        if required is None:
            raise ConfigError("invalid_destination_type", name=name, location=location)
        missing = required - destination.keys()
        if missing:
            raise ConfigError(
                "missing_destination_keys", name=name, location=location, missing=", ".join(sorted(missing))
            )
        if destination["type"] == "zulip":
            topic = destination["topic"]
            if not isinstance(topic, str) or not topic.strip():
                raise ConfigError("invalid_destination_topic", name=name, location=location)
        if destination["type"] == "email":
            address = destination["address"]
            if not isinstance(address, list) or not address or not all(isinstance(a, str) and a for a in address):
                raise ConfigError("invalid_destination_address", name=name, location=location)


def is_placeholder(destinations):
    default = destinations.get("default", {})
    return (
        list(destinations) == ["default"]
        and default.get("type") == "email"
        and default.get("address") == [PLACEHOLDER_ADDRESS]
    )


def email_destination_names(destinations):
    if is_placeholder(destinations):
        return []
    return [name for name, destination in destinations.items() if destination["type"] == "email"]


def read_comments(path):
    try:
        with open(path) as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = default_file_text().splitlines(keepends=True)
    is_comment = [line.startswith("#") or not line.strip() for line in lines]
    if all(is_comment):
        return "".join(lines), ""
    start = is_comment.index(False)
    end = len(lines) - is_comment[::-1].index(False)
    return "".join(lines[:start]), "".join(lines[end:])


def save(path, destinations):
    header, footer = read_comments(path)
    with open(path, "w") as f:
        f.write(header)
        yaml.safe_dump(destinations, f, default_flow_style=False, sort_keys=False, indent=4)
        f.write(footer)


def default_address(bounce_source, contact_email):
    if bounce_source and contact_email:
        return contact_email
    return PLACEHOLDER_ADDRESS


def _default_address():
    return default_address(
        config.read_config("bounce_source", default=""), config.read_config("contact_email", default="")
    )


def get_destinations():
    if "DESTINATIONS" not in current_app.config:
        path = current_app.config["DESTINATIONS_PATH"]
        try:
            destinations = config.load_yaml(path)
            missing = False
        except FileNotFoundError:
            destinations = None
            missing = True
        if not destinations:
            with open(path, "w") as f:
                f.write(default_file_text(_default_address()))
            with open(path) as f:
                destinations = yaml.safe_load(f)
            if missing:
                logger.info("%s did not exist, so the default destinations were created.", path)
            else:
                logger.warning("%s was empty, so the default destinations have been reloaded. Edit it to proceed.", path)
        validate(destinations, path)
        current_app.config["DESTINATIONS"] = destinations
    return current_app.config["DESTINATIONS"]


def default_zulip_channel_id():
    default = get_destinations().get("default")
    if default is None or default.get("type") != "zulip":
        return None
    return default["channel_id"]


def get_destination(name):
    return Destination.from_dict(get_destinations()[name])


def tag_destination(destination, form_name):
    if destination.type != "email":
        return destination
    subject = destination.subject or DEFAULT_SUBJECT
    return dataclasses.replace(destination, subject=f"{subject} ({form_name})")
