from datetime import datetime, timedelta, timezone

from . import config, db


def _expire(conn):
    window_minutes = config.read_config("rate_limit_window_minutes", default=60, cast=int)
    cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=window_minutes)
    conn.execute("DELETE FROM submission_attempts WHERE attempted_at < ?", (cutoff.isoformat(),))


def _count_ip(conn, ip):
    return conn.execute(
        "SELECT COUNT(*) FROM submission_attempts WHERE ip = ?", (ip,)
    ).fetchone()[0]


def clear(ip=None, email=None):
    with db.get_submissions_db() as conn:
        if ip is not None:
            cursor = conn.execute("DELETE FROM submission_attempts WHERE ip = ?", (ip,))
        elif email is not None:
            cursor = conn.execute("DELETE FROM submission_attempts WHERE email = ?", (email.lower(),))
        else:
            cursor = conn.execute("DELETE FROM submission_attempts")
        return cursor.rowcount


def record_ip(ip):
    now = datetime.now(tz=timezone.utc).isoformat()
    with db.get_submissions_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _expire(conn)
        ip_count = _count_ip(conn, ip)
        if ip_count >= config.read_config("max_attempts_per_ip", default=10, cast=int):
            return None
        cursor = conn.execute(
            "INSERT INTO submission_attempts (ip, email, attempted_at) VALUES (?, ?, ?)",
            (ip, "", now),
        )
        return cursor.lastrowid


def record_email(attempt_id, email):
    with db.get_submissions_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        email_count = conn.execute(
            "SELECT COUNT(*) FROM submission_attempts WHERE email = ?", (email,)
        ).fetchone()[0]
        if email_count >= config.read_config("max_attempts_per_email", default=3, cast=int):
            return True
        conn.execute("UPDATE submission_attempts SET email = ? WHERE rowid = ?", (email, attempt_id))
        return False
