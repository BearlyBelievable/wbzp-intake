import logging
import os
from pathlib import Path

from flask import Flask, current_app, jsonify, request
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from intake import bounce_check, cli, config, destinations, errors, form_store, mail, strings, submit, zulip_helper, zulip_integration

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

APP_DIR = Path(__file__).resolve().parent
STRINGS_PATH = APP_DIR / "strings.yaml"
FORMS_DIR = APP_DIR / "forms"


def _warn_if_secrets_are_readable(secrets_path):
    if os.name == "posix" and secrets_path.stat().st_mode & 0o077:
        logger.warning("%s can be read by other users. Run chmod 600 on it.", secrets_path)


def _configure_from_instance(app, instance_dir):
    config_path = instance_dir / "config.conf"
    secrets_path = instance_dir / "secrets.conf"
    if not config_path.exists():
        raise errors.ConfigError("missing_config_file", path=config_path)
    if not secrets_path.exists():
        raise errors.ConfigError("missing_config_file", path=secrets_path)

    app.config.update(config.load_ini_section(config_path, "config"))
    app.config.update(config.load_ini_section(secrets_path, "secrets"))
    _warn_if_secrets_are_readable(secrets_path)
    app.config["DESTINATIONS_PATH"] = instance_dir / "destinations.yaml"
    app.config["SUBMISSIONS_DB_PATH"] = instance_dir / "submissions.db"
    max_body_bytes = app.config.get("max_body_bytes", 8192)
    try:
        app.config["MAX_CONTENT_LENGTH"] = int(max_body_bytes)
    except ValueError as error:
        raise errors.ConfigError("invalid_setting", name="max_body_bytes", value=max_body_bytes) from error


def _load_forms(app):
    app.config["FORMS_DIR"] = FORMS_DIR
    app.config["FORM_CACHE"] = form_store.load_all(FORMS_DIR)


def _require_core_settings():
    config.read_config("contact_email")
    destination_list = destinations.get_destinations()
    if any(destination["type"] == "zulip" for destination in destination_list.values()):
        config.read_config("zulip_site_url")
        config.read_config("zulip_bot_email")
        config.read_secret("zulip_bot_api_key")
        if zulip_integration.read_lookup_mode() == "helper" and not Path(zulip_helper.socket_path()).exists():
            raise errors.ConfigError("lookup_socket_missing", path=zulip_helper.socket_path())


def _require_email_support_if_used():
    if config.read_config("alert_emails_enabled", default="no") == "yes":
        mail.require_smtp("alert emails are turned on", "set alert_emails_enabled to no")
    email_names = destinations.email_destination_names(destinations.get_destinations())
    if not email_names:
        return
    name = email_names[0]
    mail.require_smtp(f"destination '{name}' sends email", f"remove '{name}' from instance/destinations.yaml")
    source = bounce_check.read_bounce_source()
    if not source:
        raise errors.ConfigError("bounce_source_required", name=name)
    if source == "imap":
        bounce_check.require_imap()


def _register_routes(app):
    @app.route("/intake/<template_name>", methods=["POST"])
    def apply(template_name):
        template = form_store.get(template_name)
        if template is None:
            return jsonify(error=strings.message("common", "unknown_form")), 404

        status, body = submit.handle(template_name, template, request.form, request.remote_addr)
        return jsonify(**body), status

    def handle_service_unavailable(error):
        logger.error("%s", error)
        return jsonify(error=strings.message("common", "server_busy")), 503

    app.register_error_handler(errors.DatabaseError, handle_service_unavailable)

    def handle_invalid_configuration(error):
        logger.error("%s", error)
        return jsonify(error=strings.message("common", "server_error")), 500

    app.register_error_handler(errors.ConfigError, handle_invalid_configuration)

    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        if isinstance(error, HTTPException):
            return error
        errors.log_failure(logger, "Unhandled error while handling a request", error)
        return jsonify(error=strings.message("common", "server_error")), 500


def create_app(instance_path=None):
    app = Flask(
        __name__,
        instance_relative_config=True,
        instance_path=str(instance_path) if instance_path is not None else None,
    )
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

    _configure_from_instance(app, Path(app.instance_path))
    app.config["STRINGS"] = strings.load(STRINGS_PATH)

    with app.app_context():
        _load_forms(app)
        _require_core_settings()
        _require_email_support_if_used()

    _register_routes(app)
    cli.register(app)

    return app


def _create_app_or_exit():
    try:
        return create_app()
    except errors.ConfigError as error:
        raise SystemExit(str(error)) from error


_instance = None


def __getattr__(name):
    global _instance
    if name != "app":
        raise AttributeError(name)
    if _instance is None:
        _instance = _create_app_or_exit()
    return _instance
