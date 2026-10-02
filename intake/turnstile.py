import requests

from .errors import TurnstileError


def verify_turnstile(secret, token, remote_ip):
    try:
        resp = requests.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={
                "secret": secret,
                "response": token,
                "remoteip": remote_ip,
            },
            timeout=5,
        )
        return resp.json().get("success", False)
    except ValueError as error:
        raise TurnstileError("failed", reason="the response was not valid JSON") from error
    except requests.RequestException as error:
        raise TurnstileError("failed", error=error) from error
