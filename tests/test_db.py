from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from intake import db, rate_limit


def race(app_instance, worker, count):
    def run(index):
        with app_instance.app.app_context():
            return worker(index)

    with ThreadPoolExecutor(max_workers=count) as pool:
        return list(pool.map(run, range(count)))


def test_submission_race(app_instance):
    results = race(app_instance, lambda index: db.start_submission("racing@example.com"), 12)

    assert sorted(results) == [False] * 11 + [True]


def test_stale_in_progress_submission_expires(app_instance):
    stale = (datetime.now(tz=timezone.utc) - timedelta(minutes=db.IN_PROGRESS_TIMEOUT_MINUTES + 1)).isoformat()
    with app_instance.app.app_context():
        assert db.start_submission("crashed@example.com") is True
        assert db.start_submission("crashed@example.com") is False
    app_instance.query("UPDATE submissions SET submitted_at = ?", (stale,))

    with app_instance.app.app_context():
        assert db.start_submission("crashed@example.com") is True


def test_ip_limit_race(app_instance):
    results = race(app_instance, lambda index: rate_limit.record_ip("198.51.100.7"), 14)

    assert sum(result is not None for result in results) == 10


def test_email_limit_race(app_instance):
    with app_instance.app.app_context():
        attempt_ids = [rate_limit.record_ip(f"203.0.113.{index}") for index in range(12)]

    results = race(app_instance, lambda index: rate_limit.record_email(attempt_ids[index], "busy@example.com"), 12)

    assert sorted(results) == [False] * 3 + [True] * 9
