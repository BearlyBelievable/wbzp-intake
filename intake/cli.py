import contextlib
import logging
import sqlite3

import click
import waitress
from flask import current_app

from . import alerts, bounce_check, config, db, destinations, mail, migrations, pending_check, rate_limit
from .errors import BounceCheckError, ConfigError, DatabaseError, DeliveryError, MigrationError

logger = logging.getLogger(__name__)

MIGRATE_TARGET_HELP = "Target schema version (default: latest, {latest})"
ALREADY_UP_TO_DATE_MESSAGE = "{path} is already at schema version {version}."
MIGRATED_MESSAGE = "Migrated {path} from schema version {before} to {after}."
ROLLED_BACK_MESSAGE = "Rolled back {path} from schema version {before} to {after}."
TEST_EMAIL_SUBJECT = "wbzp-intake test email"
TEST_EMAIL_BODY = "This is a test email from wbzp-intake. If you can read it, your email settings work."
TEST_EMAIL_PURPOSE = "a test email was requested"
TEST_EMAIL_ALTERNATIVE = "run sudo ./install.sh to set them up"
TEST_EMAIL_SENT_MESSAGE = "Sent a test email to {address}."
TEST_IMAP_OK_MESSAGE = "Logged in to the bounce mailbox at {host}."
EXTERNAL_BOUNCES_WARNING = (
    "bounce_source is 'external', so this app isn't checking for bounced emails. Watch for them in your email provider."
)
CLEAR_RATE_LIMIT_CONFLICT_MESSAGE = "Use only one of --ip and --email."
CLEARED_RATE_LIMIT_MESSAGE = "Cleared {count} rate limit record(s){scope}."
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8793
SERVER_THREADS = 8


def run_migration(target_version):
    try:
        with contextlib.closing(db.connect()) as conn:
            before = migrations.current_version(conn)
            if before == 0:
                conn.execute("PRAGMA journal_mode=WAL")
            after = migrations.migrate(
                conn,
                target_version=target_version,
                parameters={"default_channel_id": destinations.default_zulip_channel_id()},
            )
    except (DatabaseError, MigrationError, sqlite3.Error, OSError) as error:
        raise click.ClickException(str(error)) from error
    return before, after


def register(app):
    @app.cli.command("bounce-check")
    def bounce_check_command():
        try:
            bounce_check.run()
        except BounceCheckError as error:
            logger.error("%s", error)
            alerts.notify_admin("bounce_check_crashed")
            raise click.ClickException(str(error)) from error
        except Exception:
            logger.exception("The bounce check crashed")
            alerts.notify_admin("bounce_check_crashed")
            raise

    @app.cli.command("check-pending")
    def check_pending_command():
        try:
            pending_check.check_awaiting_signup()
            pending_check.retry_failed_submissions()
        except DatabaseError as error:
            logger.error("%s", error)
            alerts.notify_admin("pending_check_crashed")
            raise click.ClickException(str(error)) from error
        except Exception:
            logger.exception("The daily pending-submission check crashed")
            alerts.notify_admin("pending_check_crashed")
            raise

    @app.cli.command("test-email")
    def test_email_command():
        address = config.read_config("contact_email")
        try:
            mail.require_smtp(TEST_EMAIL_PURPOSE, TEST_EMAIL_ALTERNATIVE)
            mail.Email(to=address, subject=TEST_EMAIL_SUBJECT, body=TEST_EMAIL_BODY).send()
        except (ConfigError, DeliveryError) as error:
            raise click.ClickException(str(error)) from error
        click.echo(TEST_EMAIL_SENT_MESSAGE.format(address=address))
        if bounce_check.read_bounce_source() == "imap":
            try:
                host = bounce_check.test_login()
            except (ConfigError, BounceCheckError) as error:
                raise click.ClickException(str(error)) from error
            click.echo(TEST_IMAP_OK_MESSAGE.format(host=host))

    @app.cli.command("clear-rate-limit")
    @click.option("--ip", default=None, help="Only clear the records for this IP address.")
    @click.option("--email", default=None, help="Only clear the records for this email address.")
    def clear_rate_limit_command(ip, email):
        if ip is not None and email is not None:
            raise click.UsageError(CLEAR_RATE_LIMIT_CONFLICT_MESSAGE)
        try:
            count = rate_limit.clear(ip=ip, email=email)
        except DatabaseError as error:
            raise click.ClickException(str(error)) from error
        scope = ""
        if ip is not None:
            scope = f" for {ip}"
        elif email is not None:
            scope = f" for {email}"
        click.echo(CLEARED_RATE_LIMIT_MESSAGE.format(count=count, scope=scope))

    @app.cli.command("serve")
    @click.option("--host", default=DEFAULT_HOST, show_default=True)
    @click.option("--port", default=DEFAULT_PORT, type=int, show_default=True)
    def serve_command(host, port):
        before, after = run_migration(None)
        if after != before:
            logger.info(MIGRATED_MESSAGE.format(path=current_app.config["SUBMISSIONS_DB_PATH"], before=before, after=after))
        if destinations.email_destination_names(destinations.get_destinations()) and (
            bounce_check.read_bounce_source() == "external"
        ):
            logger.warning(EXTERNAL_BOUNCES_WARNING)
        waitress.serve(app, host=host, port=port, threads=SERVER_THREADS)

    @app.cli.command("db-migrate")
    @click.option(
        "--to",
        "target_version",
        type=click.IntRange(0, len(migrations.MIGRATIONS)),
        default=None,
        metavar="VERSION",
        help=MIGRATE_TARGET_HELP.format(latest=len(migrations.MIGRATIONS)),
    )
    def db_migrate_command(target_version):
        db_path = current_app.config["SUBMISSIONS_DB_PATH"]
        before, after = run_migration(target_version)
        if after == before:
            click.echo(ALREADY_UP_TO_DATE_MESSAGE.format(path=db_path, version=after))
        elif after > before:
            click.echo(MIGRATED_MESSAGE.format(path=db_path, before=before, after=after))
        else:
            click.echo(ROLLED_BACK_MESSAGE.format(path=db_path, before=before, after=after))
