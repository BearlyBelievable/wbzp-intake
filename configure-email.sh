#!/usr/bin/env bash
set -e

if [ "$EUID" -ne 0 ]; then
    echo "Error: This script must be run as root." >&2
    exit 1
fi

if [ -z "$SUDO_USER" ]; then
    echo "Error: run this with sudo (as the user who should own the site" >&2
    echo "files), not as a direct root login. Without sudo, the app would" >&2
    echo "end up owned by and running as root." >&2
    exit 1
fi

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
source "$APP_DIR/migrate-legacy-config.sh"

migrate_legacy_config_files
ensure_config_files
ensure_venv
"$VENV_PYTHON" "$APP_DIR/destinations_cli.py" init "$DESTINATIONS_FILE" "$CONFIG_FILE"

configure_email_destination() {
    local name="$1" address subject
    ask -r -p "What email address should '$name' submissions go to? Add any BCC addresses after it, separated by commas: " address
    if [ -z "$address" ]; then
        echo "Error: a destination needs an email address." >&2
        exit 1
    fi
    "$VENV_PYTHON" "$APP_DIR/destinations_cli.py" set "$DESTINATIONS_FILE" "$name" email address "$address"

    ask -r -p "What subject should these emails have? (defaults to 'New wbzp-intake submission') " subject
    if [ -n "$subject" ]; then
        "$VENV_PYTHON" "$APP_DIR/destinations_cli.py" set "$DESTINATIONS_FILE" "$name" email subject "$subject"
    fi
}

if ! smtp_is_complete; then
    echo "Error: SMTP settings aren't set up yet. Run sudo ./install.sh to set them up." >&2
    exit 1
fi

configure_bounce_checking

name=$(first_destination_name)
if [ -z "$name" ]; then
    echo "Error: a destination needs a name." >&2
    exit 1
fi
configure_email_destination "$name"

while true; do
    ask -r -p "Configure another email destination? [y/N] " confirm
    confirmed "$confirm" || break
    ask -r -p "What should this destination be called? " name
    [ -n "$name" ] && configure_email_destination "$name"
done

echo
offer_test_email

service_installed=no
[ -f "$SERVICE_UNIT_PATH" ] && service_installed=yes
sync_service_units

echo "Done."
if [ "$service_installed" = "yes" ]; then
    echo "Changes take effect the next time the $SERVICE_NAME service starts"
    echo "(sudo systemctl restart $SERVICE_NAME)."
    if bounce_checking_enabled; then
        echo "Start the bounce check with sudo systemctl start $CHECK_BOUNCES_UNIT_NAME.timer."
    fi
fi
