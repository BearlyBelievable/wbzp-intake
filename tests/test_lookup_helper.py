import io
import subprocess

import pytest

import lookup_helper


def completed(stdout="", stderr=""):
    return subprocess.CompletedProcess([], 0, stdout, stderr)


@pytest.fixture
def run_helper(monkeypatch):
    calls = []

    def run(email, response):
        def fake_run(command, input, env, capture_output, text, timeout):
            calls.append({"command": command, "input": input, "email": env["CHECK_EMAIL"], "timeout": timeout})
            if isinstance(response, Exception):
                raise response
            return response

        monkeypatch.setattr(lookup_helper.subprocess, "run", fake_run)
        return lookup_helper.handle(email + "\n"), calls

    return run


@pytest.mark.parametrize("status", ["registered", "invited", "none"])
def test_helper_answers_with_the_status(run_helper, status):
    reply, calls = run_helper("a@example.com", completed(f"noise\nRESULT:{status}\n"))

    assert reply == status
    (call,) = calls
    assert call["command"] == [lookup_helper.MANAGE_PY_PATH, "shell"]
    assert call["email"] == "a@example.com"
    assert "filter_to_valid_prereg_users" in call["input"]
    assert call["timeout"] == lookup_helper.TIMEOUT_SECONDS


@pytest.mark.parametrize(
    "email",
    ["", "not-an-email", "a@b", "a b@example.com", "a@@example.com", "x" * 250 + "@example.com", "a@example.com b@example.com"],
    ids=["empty", "no-at", "no-domain-dot", "space", "double-at", "too-long", "two-addresses"],
)
def test_helper_rejects_an_invalid_address_without_running_anything(run_helper, email):
    reply, calls = run_helper(email, completed("RESULT:none\n"))

    assert reply == "error"
    assert calls == []


@pytest.mark.parametrize(
    "response",
    [
        completed("RESULT:maybe\n"),
        completed("", "Traceback\nValueError: boom\n"),
        OSError(13, "Permission denied"),
        subprocess.TimeoutExpired("manage.py", 25),
    ],
    ids=["unknown-status", "script-crashed", "cannot-run", "timeout"],
)
def test_helper_reports_failures_without_details(run_helper, capsys, response):
    reply, _ = run_helper("a@example.com", response)

    assert reply == "error"
    assert "Lookup failed" in capsys.readouterr().err


def test_main_reads_one_line_and_writes_one_line(monkeypatch, capsys):
    monkeypatch.setattr(lookup_helper.subprocess, "run", lambda *args, **kwargs: completed("RESULT:invited\n"))
    monkeypatch.setattr("sys.stdin", io.StringIO("a@example.com\nignored@example.com\n"))

    lookup_helper.main()

    assert capsys.readouterr().out == "invited\n"


def test_main_limits_how_much_it_reads(monkeypatch, capsys):
    monkeypatch.setattr(lookup_helper.subprocess, "run", lambda *args, **kwargs: completed("RESULT:none\n"))
    monkeypatch.setattr("sys.stdin", io.StringIO("a" * 5000 + "@example.com\n"))

    lookup_helper.main()

    assert capsys.readouterr().out == "error\n"
