import socket

from . import config
from .errors import DeliveryError

DEFAULT_SOCKET_PATH = "/run/wbzp-intake-lookup.sock"
LOOKUP_TIMEOUT_SECONDS = 30
MAX_REPLY_BYTES = 64
STATUSES = ("registered", "invited", "none")


def socket_path():
    return config.read_config("zulip_lookup_socket", default=DEFAULT_SOCKET_PATH)


def lookup_email_status(email):
    if "\n" in email or "\r" in email:
        raise DeliveryError("helper_failed", error="the email address is not valid")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(LOOKUP_TIMEOUT_SECONDS)
            connection.connect(socket_path())
            connection.sendall(email.encode("utf-8") + b"\n")
            with connection.makefile("rb") as reply_file:
                reply = reply_file.readline(MAX_REPLY_BYTES)
    except OSError as error:
        raise DeliveryError("helper_failed", error=error) from error
    status = reply.decode("utf-8", errors="replace").strip()
    if status not in STATUSES:
        raise DeliveryError("helper_failed", error="it sent back an unexpected answer")
    return status
