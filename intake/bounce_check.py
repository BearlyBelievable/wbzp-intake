import dataclasses
import email
import imaplib
import logging
import ssl
from email.message import Message

from . import alerts, config, destinations, mail
from .errors import BounceCheckError, ConfigError

logger = logging.getLogger(__name__)

BOUNCE_SOURCES = ("imap", "external")


@dataclasses.dataclass
class ImapConfig:
    host: str
    port: int
    user: str
    password: str
    folder: str

    @classmethod
    def resolve(cls):
        smtp_config = mail.SmtpConfig.resolve()
        return cls(
            host=config.read_config("bounce_imap_host", default=""),
            port=config.read_config("bounce_imap_port", default=993, cast=int),
            user=config.read_config("bounce_imap_user", default="") or smtp_config.user,
            password=config.read_secret("bounce_imap_password", default="") or smtp_config.password,
            folder=config.read_config("bounce_imap_folder", default="INBOX"),
        )


def read_bounce_source():
    source = config.read_config("bounce_source", default="")
    if source and source not in BOUNCE_SOURCES:
        raise ConfigError("invalid_setting", name="bounce_source", value=source)
    return source


def require_imap():
    imap_config = ImapConfig.resolve()
    if not all([imap_config.host, imap_config.user, imap_config.password]):
        raise ConfigError("imap_required")


def looks_like_bounce(msg: Message):
    if msg.get_content_type() == "multipart/report" and msg.get_param("report-type") == "delivery-status":
        return True
    subject = (msg.get("Subject") or "").lower()
    return any(
        keyword in subject
        for keyword in (
            "undelivered",
            "delivery status notification",
            "returned to sender",
            "failure notice",
            "mail delivery failed",
        )
    )


def extract_bounced_addresses(msg: Message):
    addresses = []
    if not msg.is_multipart():
        return addresses
    for part in msg.walk():
        if part.get_content_type() != "message/delivery-status":
            continue
        for fields in part.get_payload():
            final_recipient = fields.get("Final-Recipient", "")
            address = final_recipient.rsplit(";", 1)[-1].strip()
            if address:
                addresses.append(address)
    return addresses


def open_mailbox(imap_config):
    return imaplib.IMAP4_SSL(imap_config.host, imap_config.port, ssl_context=ssl.create_default_context())


def select_folder(imap, folder):
    quoted = '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'
    status, _ = imap.select(quoted)
    if status != "OK":
        raise imaplib.IMAP4.error(f"the folder {folder!r} could not be opened")


def test_login():
    require_imap()
    imap_config = ImapConfig.resolve()
    try:
        with open_mailbox(imap_config) as imap:
            imap.login(imap_config.user, imap_config.password)
            select_folder(imap, imap_config.folder)
    except (imaplib.IMAP4.error, OSError) as error:
        raise BounceCheckError("failed", host=imap_config.host, port=imap_config.port, error=error) from error
    return imap_config.host


def run():
    if read_bounce_source() != "imap":
        logger.info("bounce_source is not 'imap', so the bounce check was skipped.")
        return
    if not destinations.email_destination_names(destinations.get_destinations()):
        logger.info("No destination sends email, so the bounce check was skipped.")
        return

    imap_config = ImapConfig.resolve()

    found = 0
    try:
        with open_mailbox(imap_config) as imap:
            imap.login(imap_config.user, imap_config.password)
            select_folder(imap, imap_config.folder)
            status, data = imap.search(None, "UNSEEN")
            if status != "OK":
                logger.error("Failed to search the bounce mailbox: %s", status)
                return

            for message_id in data[0].split():
                status, msg_data = imap.fetch(message_id, "(BODY.PEEK[])")
                if status != "OK" or not msg_data or not msg_data[0]:
                    continue
                msg = email.message_from_bytes(msg_data[0][1])
                if not looks_like_bounce(msg):
                    continue

                found += 1
                addresses = extract_bounced_addresses(msg) or ["(address not found in the bounce message)"]
                for address in addresses:
                    logger.error("An email to %s bounced (subject: %s).", address, msg.get("Subject", ""))
                    alerts.notify_admin("bounce_detected", {"address": address, "subject": msg.get("Subject", "")})
                imap.store(message_id, "+FLAGS", "\\Seen")
    except (imaplib.IMAP4.error, OSError) as error:
        raise BounceCheckError("failed", host=imap_config.host, port=imap_config.port, error=error) from error

    logger.info("Checked the bounce mailbox, found %d bounce(s).", found)
