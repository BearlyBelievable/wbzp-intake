import hashlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .errors import MigrationError

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "sql"
_UP_MARKER = "-- migrate:up"
_DOWN_MARKER = "-- migrate:down"
_TRACKING_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS schema_migrations ("
    "version INTEGER PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)"
)
BACKUPS_TO_KEEP = 5


def table_exists(conn, name):
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def current_version(conn):
    if not table_exists(conn, "schema_migrations"):
        return 0
    return conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]


def parse_migration(text):
    sections = {}
    lines = None
    for line in text.splitlines():
        marker = line.strip()
        if marker in (_UP_MARKER, _DOWN_MARKER):
            if marker in sections:
                raise MigrationError("duplicate_section", marker=marker)
            lines = sections[marker] = []
        elif lines is not None:
            lines.append(line)
    up = "\n".join(sections.get(_UP_MARKER, [])).strip()
    if not up:
        raise MigrationError("missing_up", marker=_UP_MARKER)
    return up, "\n".join(sections.get(_DOWN_MARKER, [])).strip() or None


def discover_migrations(directory=_MIGRATIONS_DIR):
    numbered = []
    for path in directory.glob("*.sql"):
        number, _, description = path.stem.partition("_")
        if not (len(number) == 4 and number.isascii() and number.isdigit() and description):
            raise MigrationError("bad_filename", name=path.name)
        numbered.append((int(number), path))
    numbered.sort()
    numbers = [number for number, _ in numbered]
    if numbers != list(range(1, len(numbers) + 1)):
        raise MigrationError("numbering_gap", numbers=numbers)
    return [parse_migration(path.read_text(encoding="utf-8")) for _, path in numbered]


try:
    MIGRATIONS = discover_migrations()
except MigrationError as error:
    raise SystemExit(str(error)) from error


def _checksum(migration):
    up, down = migration
    return hashlib.sha256(f"{up}\0{down or ''}".encode()).hexdigest()


def _check_applied_migrations_unchanged(conn, migrations):
    if not table_exists(conn, "schema_migrations"):
        return
    for version, checksum in conn.execute("SELECT version, checksum FROM schema_migrations ORDER BY version"):
        if version > len(migrations):
            raise MigrationError("unknown_migration", version=version, known=len(migrations))
        if checksum != _checksum(migrations[version - 1]):
            raise MigrationError("edited_migration", version=version)


def _database_file(conn):
    return Path(conn.execute("SELECT file FROM pragma_database_list WHERE name = 'main'").fetchone()[0])


def _back_up(conn, applied_version):
    if conn.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone() is None:
        return
    path = _database_file(conn)
    backup_dir = path.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    destination = sqlite3.connect(backup_dir / f"{path.name}.{stamp}.from-v{applied_version}.bak")
    try:
        conn.backup(destination)
    finally:
        destination.close()
    for old_backup in sorted(backup_dir.glob(f"{path.name}.*.bak"))[:-BACKUPS_TO_KEEP]:
        old_backup.unlink()


def _integrity_problems(conn):
    problems = [row[0] for row in conn.execute("PRAGMA integrity_check") if row[0] != "ok"]
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        problems.append(f"foreign key violations: {violations}")
    return problems


def _statements(script):
    pieces = script.split(";")
    pending = ""
    for index, piece in enumerate(pieces):
        pending += piece
        if index == len(pieces) - 1:
            break
        pending += ";"
        if sqlite3.complete_statement(pending):
            yield pending.strip()
            pending = ""
    if pending.strip():
        yield pending.strip()


def _apply_step(conn, sql_script, resulting_version, record, parameters):
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(_TRACKING_TABLE_SQL)
        for statement in _statements(sql_script):
            conn.execute(statement, parameters)
        record(conn)
        problems = _integrity_problems(conn)
        if problems:
            raise MigrationError("integrity_check", version=resulting_version, problems=problems)
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


def _record_applied(version, migration):
    def record(conn):
        conn.execute(
            "INSERT INTO schema_migrations (version, checksum, applied_at) VALUES (?, ?, ?)",
            (version, _checksum(migration), datetime.now(tz=timezone.utc).isoformat()),
        )

    return record


def _record_undone(version):
    def record(conn):
        conn.execute("DELETE FROM schema_migrations WHERE version = ?", (version,))

    return record


def migrate(conn, target_version=None, migrations=MIGRATIONS, parameters=None):
    if parameters is None:
        parameters = {}
    if target_version is None:
        target_version = len(migrations)
    if not 0 <= target_version <= len(migrations):
        raise ValueError(f"No such schema version: {target_version}")

    _check_applied_migrations_unchanged(conn, migrations)
    applied = current_version(conn)
    if target_version > applied:
        _back_up(conn, applied)
        for version in range(applied + 1, target_version + 1):
            migration = migrations[version - 1]
            _apply_step(conn, migration[0], version, _record_applied(version, migration), parameters)
    elif target_version < applied:
        steps = [(version, migrations[version - 1][1]) for version in range(applied, target_version, -1)]
        for version, down_sql in steps:
            if down_sql is None:
                raise MigrationError("irreversible", version=version)
        _back_up(conn, applied)
        for version, down_sql in steps:
            _apply_step(conn, down_sql, version - 1, _record_undone(version), parameters)

    return current_version(conn)
