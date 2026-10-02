import contextlib
import sqlite3
from datetime import datetime, timedelta, timezone

from flask import current_app

from . import config, migrations
from .destinations import Destination
from .errors import DatabaseError

IN_PROGRESS_TIMEOUT_MINUTES = 15


def connect():
    try:
        return sqlite3.connect(current_app.config["SUBMISSIONS_DB_PATH"])
    except sqlite3.Error as error:
        raise DatabaseError("failed", error=error) from error


def _require_current_schema(conn):
    version = migrations.current_version(conn)
    if version != len(migrations.MIGRATIONS):
        raise DatabaseError("schema_mismatch", version=version, needed=len(migrations.MIGRATIONS))


@contextlib.contextmanager
def get_submissions_db():
    conn = connect()
    try:
        _require_current_schema(conn)
        with conn:
            yield conn
    except sqlite3.Error as error:
        raise DatabaseError("failed", error=error) from error
    finally:
        conn.close()


def start_submission(email):
    expiry_days = config.read_config("submission_expiry_days", default=30, cast=int)
    current_time = datetime.now(tz=timezone.utc)
    cutoff = current_time - timedelta(days=expiry_days)
    stale_cutoff = current_time - timedelta(minutes=IN_PROGRESS_TIMEOUT_MINUTES)
    now = current_time.isoformat()
    with get_submissions_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM submissions WHERE email = ? AND "
            "((status = 'awaiting_signup' AND submitted_at < ?) OR (status = 'in_progress' AND submitted_at < ?))",
            (email, cutoff.isoformat(), stale_cutoff.isoformat()),
        )
        cursor = conn.execute(
            "INSERT OR IGNORE INTO submissions (email, status, submitted_at) VALUES (?, 'in_progress', ?)",
            (email, now),
        )
        return cursor.rowcount == 1


def finish_submission(email):
    with get_submissions_db() as conn:
        conn.execute("DELETE FROM submissions WHERE email = ?", (email,))


def await_signup(email):
    now = datetime.now(tz=timezone.utc).isoformat()
    with get_submissions_db() as conn:
        conn.execute(
            "UPDATE submissions SET status = 'awaiting_signup', submitted_at = ?, body = NULL, "
            "destination_type = NULL, destination_channel_id = NULL, destination_to = NULL, "
            "destination_bcc = NULL, destination_subject = NULL, failed_at = NULL WHERE email = ?",
            (now, email),
        )


def fail_submission(email, body, destination):
    destination_type, channel_id, to_address, bcc_addresses, subject = destination.to_row()
    now = datetime.now(tz=timezone.utc).isoformat()
    with get_submissions_db() as conn:
        conn.execute(
            "INSERT INTO submissions (email, status, submitted_at, body, destination_type, "
            "destination_channel_id, destination_to, destination_bcc, destination_subject, failed_at) "
            "VALUES (?, 'failed', ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(email) DO UPDATE SET status = 'failed', body = excluded.body, "
            "destination_type = excluded.destination_type, "
            "destination_channel_id = excluded.destination_channel_id, "
            "destination_to = excluded.destination_to, destination_bcc = excluded.destination_bcc, "
            "destination_subject = excluded.destination_subject, failed_at = excluded.failed_at",
            (email, now, body, destination_type, channel_id, to_address, bcc_addresses, subject, now),
        )


def list_failed_submissions():
    with get_submissions_db() as conn:
        rows = conn.execute(
            "SELECT email, body, destination_type, destination_channel_id, destination_to, "
            "destination_bcc, destination_subject FROM submissions WHERE status = 'failed'"
        ).fetchall()
    return [
        (email, body, Destination.from_row(destination_type, channel_id, to_address, bcc_addresses, subject))
        for email, body, destination_type, channel_id, to_address, bcc_addresses, subject in rows
    ]


def list_awaiting_signup():
    with get_submissions_db() as conn:
        return [row[0] for row in conn.execute("SELECT email FROM submissions WHERE status = 'awaiting_signup'")]
