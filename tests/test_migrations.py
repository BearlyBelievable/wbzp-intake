import shutil
import sqlite3
import subprocess
import sys

import pytest
from app_harness import REPO_ROOT

from intake.errors import MigrationError
from intake.migrations import (
    BACKUPS_TO_KEEP,
    MIGRATIONS,
    current_version,
    discover_migrations,
    migrate,
    parse_migration,
    table_exists,
)

LATEST = len(MIGRATIONS)
NO_CHANNEL = {"default_channel_id": None}
FAKE_MIGRATIONS = [
    ("CREATE TABLE things (x);", "DROP TABLE things;"),
    ("CREATE TABLE more (z);", "DROP TABLE more;"),
    ("CREATE TABLE extra (w);", "DROP TABLE extra;"),
]
BROKEN_SECOND_STEP = FAKE_MIGRATIONS[:1] + [("CREATE TABLE half (x); SELECT not_valid_sql;", None)]
BREAKS_A_CHECK_CONSTRAINT = (
    "CREATE TABLE t (x INTEGER CHECK (x > 0)); "
    "PRAGMA ignore_check_constraints = ON; "
    "INSERT INTO t VALUES (-1); "
    "PRAGMA ignore_check_constraints = OFF;"
)
ORPHANS_A_FOREIGN_KEY = (
    "CREATE TABLE parent (id INTEGER PRIMARY KEY); "
    "CREATE TABLE child (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id)); "
    "INSERT INTO child VALUES (1, 99);"
)


@pytest.fixture
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "main.db")
    yield connection
    connection.close()


@pytest.fixture
def fresh_at(tmp_path):
    connections = []

    def make(version):
        connection = sqlite3.connect(tmp_path / f"fresh-{len(connections)}.db")
        connections.append(connection)
        migrate(connection, target_version=version, parameters=NO_CHANNEL)
        return connection

    yield make
    for connection in connections:
        connection.close()


def schema(connection):
    tables = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    columns = {name: connection.execute(f"PRAGMA table_info('{name}')").fetchall() for name in tables}
    indexes = sorted(
        row[0]
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'")
    )
    return columns, indexes


def recorded(connection):
    return connection.execute("SELECT version, checksum, applied_at FROM schema_migrations ORDER BY version").fetchall()


def test_shipped_migrations(conn, tmp_path, fresh_at):
    assert [version for version in range(2, LATEST + 1) if MIGRATIONS[version - 1][1] is None] == []
    assert migrate(conn, parameters=NO_CHANNEL) == LATEST
    assert schema(conn) == schema(fresh_at(LATEST))
    assert migrate(conn, parameters=NO_CHANNEL) == LATEST

    for version in range(1, LATEST + 1):
        stepped = sqlite3.connect(tmp_path / f"stepped-{version}.db")
        for step in range(1, version + 1):
            assert migrate(stepped, target_version=step, parameters=NO_CHANNEL) == step
        assert schema(stepped) == schema(fresh_at(version)), f"version {version}"
        stepped.close()

    for version in range(LATEST, 1, -1):
        assert migrate(conn, target_version=version - 1, parameters=NO_CHANNEL) == version - 1
        assert schema(conn) == schema(fresh_at(version - 1)), f"undoing version {version}"
    assert migrate(conn, parameters=NO_CHANNEL) == LATEST
    assert schema(conn) == schema(fresh_at(LATEST))
    assert any("submission_attempts" in name for name in schema(conn)[1])


@pytest.mark.parametrize("target", [-1, LATEST + 1])
def test_target_version_range(conn, target):
    with pytest.raises(ValueError, match="No such schema version"):
        migrate(conn, target_version=target)


def test_failed_step(conn):
    with pytest.raises(sqlite3.OperationalError):
        migrate(conn, migrations=BROKEN_SECOND_STEP)

    assert current_version(conn) == 1
    assert table_exists(conn, "things")
    assert not table_exists(conn, "half")
    assert [version for version, _, _ in recorded(conn)] == [1]


def test_irreversible_downgrade(conn):
    migrations = [
        ("CREATE TABLE a (x);", "DROP TABLE a;"),
        ("CREATE TABLE b (x);", None),
        ("CREATE TABLE c (x);", "DROP TABLE c;"),
    ]
    migrate(conn, migrations=migrations)

    with pytest.raises(MigrationError) as caught:
        migrate(conn, target_version=0, migrations=migrations)

    assert caught.value.code == "irreversible"
    assert caught.value.params["version"] == 2
    assert current_version(conn) == 3
    assert all(table_exists(conn, name) for name in "abc")


def test_migration_records(conn):
    conn.execute("CREATE TABLE unrelated (x)")
    assert current_version(conn) == 0

    migrate(conn, migrations=FAKE_MIGRATIONS)
    rows = recorded(conn)
    assert [row[0] for row in rows] == [1, 2, 3]
    assert len({row[1] for row in rows}) == 3
    assert all(len(row[1]) == 64 and row[2] for row in rows)

    migrate(conn, target_version=1, migrations=FAKE_MIGRATIONS)
    assert [row[0] for row in recorded(conn)] == [1]


def test_edited_migration(conn):
    migrate(conn, migrations=FAKE_MIGRATIONS[:2])
    edited = [("CREATE TABLE things (x, extra);", "DROP TABLE things;"), *FAKE_MIGRATIONS[1:]]

    with pytest.raises(MigrationError, match="Migration 1 was changed after it was applied") as caught:
        migrate(conn, migrations=edited)

    assert (caught.value.code, caught.value.params["version"]) == ("edited_migration", 1)
    assert current_version(conn) == 2
    assert not table_exists(conn, "extra")


def test_database_ahead(conn):
    migrate(conn, migrations=FAKE_MIGRATIONS)

    with pytest.raises(MigrationError, match="migration 3 applied, but this code only has 2") as caught:
        migrate(conn, migrations=FAKE_MIGRATIONS[:2])

    assert caught.value.code == "unknown_migration"
    assert caught.value.params == {"version": 3, "known": 2}


def test_named_parameters(conn):
    migrations = [("CREATE TABLE t (x); INSERT INTO t VALUES (:value);", None)]

    with pytest.raises(sqlite3.ProgrammingError):
        migrate(conn, migrations=migrations)
    assert current_version(conn) == 0
    assert not table_exists(conn, "t")

    migrate(conn, migrations=migrations, parameters={"value": 7})
    assert conn.execute("SELECT x FROM t").fetchall() == [(7,)]


def test_statement_splitting(conn):
    script = (
        "CREATE TABLE log (note TEXT);\n"
        "CREATE TABLE t (x INTEGER);\n"
        "CREATE TRIGGER t_ai AFTER INSERT ON t BEGIN\n"
        "    INSERT INTO log VALUES ('first; second');\n"
        "    INSERT INTO log VALUES ('third');\n"
        "END;\n"
        "INSERT INTO t VALUES (1)"
    )

    migrate(conn, migrations=[(script, None)])

    assert conn.execute("SELECT note FROM log ORDER BY rowid").fetchall() == [("first; second",), ("third",)]
    assert conn.execute("SELECT x FROM t").fetchall() == [(1,)]


@pytest.mark.parametrize(
    "bad_sql, finding",
    [(BREAKS_A_CHECK_CONSTRAINT, "CHECK constraint failed"), (ORPHANS_A_FOREIGN_KEY, "foreign key violations")],
    ids=["check-constraint", "foreign-key"],
)
def test_integrity_rollback(conn, bad_sql, finding):
    with pytest.raises(MigrationError, match=finding) as caught:
        migrate(conn, migrations=[("CREATE TABLE kept (x);", None), (bad_sql, None)])

    assert (caught.value.code, caught.value.params["version"]) == ("integrity_check", 2)
    assert caught.value.params["problems"]
    assert current_version(conn) == 1
    assert table_exists(conn, "kept")
    assert not table_exists(conn, "t") and not table_exists(conn, "parent")


def test_integrity_on_undo(conn):
    undo = "DROP TABLE t; " + BREAKS_A_CHECK_CONSTRAINT.replace("TABLE t", "TABLE u").replace("INTO t", "INTO u")
    migrations = [("CREATE TABLE t (x);", undo)]
    migrate(conn, migrations=migrations)

    with pytest.raises(MigrationError, match="CHECK constraint failed"):
        migrate(conn, target_version=0, migrations=migrations)

    assert current_version(conn) == 1
    assert table_exists(conn, "t") and not table_exists(conn, "u")


def open_file_db(path, version=0):
    connection = sqlite3.connect(path)
    migrate(connection, target_version=version, migrations=FAKE_MIGRATIONS)
    return connection


def backups_of(path):
    return sorted((path.parent / "backups").glob("*.bak"))


def state_of(backup):
    connection = sqlite3.connect(backup)
    try:
        tables = {
            name: connection.execute(f"SELECT * FROM {name}").fetchall()
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name != 'schema_migrations' ORDER BY name"
            )
        }
        return current_version(connection), tables
    finally:
        connection.close()


def test_backups(tmp_path):
    path = tmp_path / "s.db"
    connection = open_file_db(path)
    assert backups_of(path) == []

    migrate(connection, target_version=1, migrations=FAKE_MIGRATIONS)
    connection.execute("INSERT INTO things VALUES (7)")
    connection.commit()
    migrate(connection, target_version=3, migrations=FAKE_MIGRATIONS)
    [upgrade_backup] = backups_of(path)
    assert upgrade_backup.name.startswith("s.db.") and upgrade_backup.name.endswith(".from-v1.bak")
    assert state_of(upgrade_backup) == (1, {"things": [(7,)]})

    migrate(connection, target_version=3, migrations=FAKE_MIGRATIONS)
    assert backups_of(path) == [upgrade_backup]

    migrate(connection, target_version=2, migrations=FAKE_MIGRATIONS)
    downgrade_backup = [backup for backup in backups_of(path) if backup != upgrade_backup][0]
    assert state_of(downgrade_backup)[0] == 3
    connection.close()


def test_backup_failures(tmp_path):
    path = tmp_path / "s.db"
    connection = open_file_db(path, version=1)
    connection.execute("INSERT INTO things VALUES (7)")
    connection.commit()

    with pytest.raises(sqlite3.OperationalError):
        migrate(connection, target_version=2, migrations=BROKEN_SECOND_STEP)
    assert connection.execute("SELECT x FROM things").fetchall() == [(7,)]
    assert not table_exists(connection, "half")
    [backup] = backups_of(path)
    assert state_of(backup) == (1, {"things": [(7,)]})

    shutil.rmtree(path.parent / "backups")
    (path.parent / "backups").write_text("a file where the backup folder should be")
    with pytest.raises(OSError):
        migrate(connection, target_version=2, migrations=FAKE_MIGRATIONS)
    assert current_version(connection) == 1
    assert not table_exists(connection, "more")
    connection.close()


def test_backup_retention(tmp_path):
    path = tmp_path / "s.db"
    connection = open_file_db(path, version=1)
    seen = set()

    for round_number in range(BACKUPS_TO_KEEP + 3):
        migrate(connection, target_version=2 if round_number % 2 == 0 else 1, migrations=FAKE_MIGRATIONS)
        seen.update(backups_of(path))
    connection.close()

    assert backups_of(path) == sorted(seen)[-BACKUPS_TO_KEEP:]


def write_migration(directory, name, text="-- migrate:up\nCREATE TABLE t (x);\n"):
    (directory / name).write_text(text, encoding="utf-8")


def test_discovery_order(tmp_path):
    write_migration(tmp_path, "0002_b_table_2.sql", "-- migrate:up\nCREATE TABLE second (x);\n")
    write_migration(tmp_path, "0001_z.sql", "-- migrate:up\nCREATE TABLE first (x);\n")

    assert [up for up, _ in discover_migrations(tmp_path)] == ["CREATE TABLE first (x);", "CREATE TABLE second (x);"]


@pytest.mark.parametrize(
    "files, message",
    [
        ({"0001_first.sql": None, "0003_third.sql": None}, "no gaps"),
        ({"0001_first.sql": None, "later.sql": None}, "must look like"),
        ({"m0001_first.sql": None}, "must look like"),
        ({"001_first.sql": None}, "must look like"),
        ({"0001-first.sql": None}, "must look like"),
        ({"0001_.sql": None}, "must look like"),
        ({"0001.sql": None}, "must look like"),
        ({"0001_x.sql": "CREATE TABLE t (x);"}, "non-empty '-- migrate:up'"),
        ({"0001_x.sql": "-- migrate:up\nSELECT 1;\n-- migrate:up\nSELECT 2;"}, "only one"),
    ],
    ids=["gap", "no-number", "m-prefix", "short-number", "dash", "no-description", "no-underscore", "no-up", "two-ups"],
)
def test_bad_migration_files(tmp_path, files, message):
    for name, text in files.items():
        write_migration(tmp_path, name, *([text] if text else []))

    with pytest.raises(MigrationError, match=message):
        discover_migrations(tmp_path)


def test_section_markers():
    up, down = parse_migration(
        "-- migrate:up\n-- upsert the rows\nCREATE TABLE t (x);\n-- migrate:down\n-- downgrade note\nDROP TABLE t;\n"
    )

    assert up == "-- upsert the rows\nCREATE TABLE t (x);"
    assert down == "-- downgrade note\nDROP TABLE t;"
    for old_style in ["-- up", "-- migrate:up-ish", "--migrate:up"]:
        with pytest.raises(MigrationError, match="non-empty '-- migrate:up'"):
            parse_migration(f"{old_style}\nCREATE TABLE t (x);\n")


@pytest.mark.parametrize(
    "default_destination, expected_channel",
    [({"type": "zulip", "channel_id": 42, "topic": "Topic"}, 42), ({"type": "email", "address": ["a@example.com"]}, None)],
    ids=["zulip-default", "email-default"],
)
def test_held_submission_channel(make_app, default_destination, expected_channel):
    app_instance = make_app(destinations={"default": default_destination}, migrate=False)
    legacy = sqlite3.connect(app_instance.db_path)
    migrate(legacy, target_version=1, parameters=NO_CHANNEL)
    legacy.execute(
        "INSERT INTO held_applications (email, subject, body, failed_at) "
        "VALUES ('legacy@example.com', 'Old subject', 'body', '2026-01-01T00:00:00+00:00')"
    )
    legacy.commit()
    legacy.close()

    assert app_instance.invoke("db-migrate").exit_code == 0

    rows = app_instance.query("SELECT status, destination_channel_id FROM submissions WHERE email = 'legacy@example.com'")
    assert rows == [{"status": "failed", "destination_channel_id": expected_channel}]


def test_import_error_message(tmp_path):
    shutil.copytree(REPO_ROOT / "intake", tmp_path / "intake", ignore=shutil.ignore_patterns("__pycache__"))
    (tmp_path / "intake" / "sql" / "later.sql").write_text("-- migrate:up\nCREATE TABLE t (x);\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-c", "import intake.migrations"], cwd=tmp_path, capture_output=True, text=True
    )

    assert result.returncode == 1
    assert result.stderr.strip() == "The migration file name 'later.sql' must look like 0001_short_description.sql"
    assert "Traceback" not in result.stderr
