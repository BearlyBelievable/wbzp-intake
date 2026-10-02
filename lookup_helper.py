import os
import re
import subprocess
import sys

MANAGE_PY_PATH = os.environ.get("WBZP_INTAKE_MANAGE_PY", "/home/zulip/deployments/current/manage.py")
TIMEOUT_SECONDS = 25
MAX_EMAIL_LENGTH = 254
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
RESULT_PREFIX = "RESULT:"
STATUSES = ("registered", "invited", "none")
FAILURE_REPLY = "error"

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


def is_valid_email(email):
    return len(email) <= MAX_EMAIL_LENGTH and EMAIL_PATTERN.match(email) is not None


def lookup(email):
    result = subprocess.run(
        [MANAGE_PY_PATH, "shell"],
        input=CHECK_EMAIL_SCRIPT,
        env={**os.environ, "CHECK_EMAIL": email},
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
    )
    for line in result.stdout.splitlines():
        status = line.removeprefix(RESULT_PREFIX)
        if line.startswith(RESULT_PREFIX) and status in STATUSES:
            return status
    detail = result.stderr.strip().splitlines()
    raise RuntimeError(detail[-1] if detail else "no result was printed")


def handle(request_line):
    email = request_line.rstrip("\r\n")
    if not is_valid_email(email):
        return FAILURE_REPLY
    try:
        return lookup(email)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"Lookup failed: {error}", file=sys.stderr)
        return FAILURE_REPLY


def main():
    request_line = sys.stdin.readline(MAX_EMAIL_LENGTH + 2)
    sys.stdout.write(handle(request_line) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
