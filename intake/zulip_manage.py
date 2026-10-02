import os
import subprocess

from . import config
from .errors import DeliveryError

DEFAULT_MANAGE_PY_PATH = "/home/zulip/deployments/current/manage.py"
MANAGE_PY_TIMEOUT_SECONDS = 30
RESULT_PREFIX = "RESULT:"
STATUSES = ("registered", "invited", "none")

CHECK_EMAIL_SCRIPT = """
import os

from zerver.models import PreregistrationUser, UserProfile
from zerver.models.prereg_users import filter_to_valid_prereg_users

email = os.environ["CHECK_EMAIL"]

if UserProfile.objects.filter(delivery_email__iexact=email, is_active=True).exists():
    print("RESULT:registered")
elif filter_to_valid_prereg_users(
    PreregistrationUser.objects.filter(email__iexact=email), invitations_only=True
).exists():
    print("RESULT:invited")
else:
    print("RESULT:none")
"""


def manage_py_path():
    return config.read_config("zulip_manage_py", default=DEFAULT_MANAGE_PY_PATH)


def lookup_email_status(email):
    try:
        result = subprocess.run(
            [manage_py_path(), "shell"],
            input=CHECK_EMAIL_SCRIPT,
            env={**os.environ, "CHECK_EMAIL": email},
            capture_output=True,
            text=True,
            timeout=MANAGE_PY_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DeliveryError("manage_py_failed", error=error) from error
    for line in result.stdout.splitlines():
        status = line.removeprefix(RESULT_PREFIX)
        if line.startswith(RESULT_PREFIX) and status in STATUSES:
            return status
    detail = result.stderr.strip().splitlines()[-1:] or ["it printed no result"]
    raise DeliveryError("manage_py_failed", error=detail[0])
