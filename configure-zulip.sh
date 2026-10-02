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

if ! command -v systemctl &>/dev/null; then
    echo "Error: systemctl not found. This requires a systemd-based Ubuntu or" >&2
    echo "Debian system." >&2
    exit 1
fi

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"
source "$APP_DIR/migrate-legacy-config.sh"

migrate_legacy_config_files
ensure_config_files
run_legacy_config_migrations
"$VENV_PYTHON" "$APP_DIR/destinations_cli.py" init "$DESTINATIONS_FILE" "$CONFIG_FILE"

run_zulip_api_python() {
    local code="$1" site_url="${2:-}"
    (
        export ZULIP_SITE_URL ZULIP_BOT_EMAIL ZULIP_BOT_API_KEY ZULIP_LOOKUP
        ZULIP_LOOKUP=$(get_conf_value zulip_lookup "$CONFIG_FILE")
        ZULIP_SITE_URL="${site_url:-$(get_conf_value zulip_site_url "$CONFIG_FILE")}"
        ZULIP_BOT_EMAIL=$(get_conf_value zulip_bot_email "$CONFIG_FILE")
        ZULIP_BOT_API_KEY=$(get_conf_value zulip_bot_api_key "$SECRETS_FILE")
        cd "$APP_DIR" && "$VENV_PYTHON" -c "$code"
    )
}

prompt_zulip_site_url() {
    local suggestion="$LAST_ZULIP_URL" candidate resolved
    if [ -z "$suggestion" ]; then
        suggestion=$(get_conf_value zulip_site_url "$CONFIG_FILE")
    fi
    if [ -z "$suggestion" ]; then
        suggestion=$(run_zulip_api_python "
import os
from intake.zulip_setup import suggest_site_url
print(suggest_site_url(os.environ['ZULIP_BOT_EMAIL']))
")
    fi
    if [ -n "$suggestion" ]; then
        ask_with_hint "What is your Zulip site URL?" "Press Enter to use [$suggestion]: " candidate
    else
        ask -r -p "What is your Zulip site URL? " candidate
    fi
    LAST_ZULIP_URL="${candidate:-$suggestion}"
    if resolved=$(run_zulip_api_python "
import os
from intake.errors import DeliveryError
from intake.zulip_setup import resolve_site_url
try:
    print(resolve_site_url(os.environ['ZULIP_SITE_URL'], os.environ['ZULIP_BOT_EMAIL'], os.environ['ZULIP_BOT_API_KEY'], os.environ['ZULIP_LOOKUP'] != 'helper'))
except DeliveryError as error:
    raise SystemExit(f'Error: {error}')
" "$LAST_ZULIP_URL"); then
        set_conf_value zulip_site_url "$resolved" "$CONFIG_FILE"
        echo "Connected to $resolved."
        return 0
    fi
    return 1
}

connect_to_zulip() {
    choose_lookup_mode
    while true; do
        prompt_for_key zulip_bot_email "What is the bot's email? (Personal settings > Bots)" no yes "$CONFIG_FILE"
        prompt_setting zulip_bot_api_key "What is the bot's API key?" yes "$SECRETS_FILE"
        if prompt_zulip_site_url; then
            return
        fi
        echo "Let's try those again." >&2
    done
}

choose_zulip_channel() {
    local bot_email="$1" name channel_id detected options=() ids=() choice
    local page_size=15 start=0 total end page_options=() extra=() detection_failed=no

    if [ -n "$bot_email" ]; then
        if detected=$(run_zulip_api_python "
import os
from intake.errors import DeliveryError
from intake.zulip_setup import detect_channels
try:
    channels = detect_channels(os.environ['ZULIP_SITE_URL'], os.environ['ZULIP_BOT_EMAIL'], os.environ['ZULIP_BOT_API_KEY'])
except DeliveryError as error:
    if 'incoming webhook' in str(error).lower():
        raise SystemExit(\"This bot is an Incoming webhook bot, which can't list channels.\")
    raise SystemExit(f\"Could not list the bot's channels: {error}\")
for name, channel_id in channels:
    print(f'{name}|{channel_id}')
"); then
            while IFS='|' read -r name channel_id; do
                [ -n "$channel_id" ] && options+=("$name") && ids+=("$channel_id")
            done <<< "$detected"
        else
            detection_failed=yes
        fi

        total="${#ids[@]}"
        if [ "$total" -gt 0 ]; then
            while true; do
                page_options=("${options[@]:$start:$page_size}")
                end=$((start + ${#page_options[@]}))
                extra=()
                [ "$end" -lt "$total" ] && extra+=("show more")
                extra+=("other (type a channel ID)")
                ask_choice "Which channel should these submissions go to?"
                select choice in "${page_options[@]}" "${extra[@]}"; do
                    [ -n "$choice" ] && break
                    echo "Please choose a number 1-$((${#page_options[@]} + ${#extra[@]}))." >&2
                    echo >&2
                done
                if [ "$REPLY" -le "${#page_options[@]}" ]; then
                    echo "${ids[$((start + REPLY - 1))]}"
                    return
                elif [ "$choice" = "show more" ]; then
                    start="$end"
                else
                    break
                fi
            done
            echo "If the channel you want isn't listed, add the bot to it in Zulip and run this script again, or enter its ID." >&2
        elif [ "$detection_failed" = "yes" ]; then
            echo "Enter the channel's ID instead." >&2
        else
            echo "The bot isn't subscribed to any channels yet. Add it to the channel you want in Zulip and run this script again, or enter the channel's ID." >&2
        fi
    fi
    ask -r -p "What is the channel ID? (channel's ... menu > Copy link to channel > number after channel/) " choice
    echo "$choice"
}

configure_zulip_destination() {
    local name="$1" bot_email channel_id topic="" default_topic
    bot_email=$(get_conf_value zulip_bot_email "$CONFIG_FILE")
    channel_id=$(choose_zulip_channel "$bot_email")
    if ! [[ "$channel_id" =~ ^[0-9]+$ ]]; then
        echo "Error: that isn't a numeric channel ID." >&2
        exit 1
    fi
    default_topic=$("$VENV_PYTHON" "$APP_DIR/destinations_cli.py" default-subject)
    ask -r -p "What topic should these submissions use? (defaults to '$default_topic') " topic
    topic="${topic:-$default_topic}"
    "$VENV_PYTHON" "$APP_DIR/destinations_cli.py" set "$DESTINATIONS_FILE" "$name" zulip channel_id "$channel_id" topic "$topic"
}

ensure_venv

LAST_ZULIP_URL=""
connect_to_zulip

name=$(first_destination_name)
if [ -z "$name" ]; then
    echo "Error: a destination needs a name." >&2
    exit 1
fi
configure_zulip_destination "$name"

while true; do
    ask -r -p "Configure another Zulip destination? [y/N] " confirm
    confirmed "$confirm" || break
    ask -r -p "What should this destination be called? " name
    [ -n "$name" ] && configure_zulip_destination "$name"
done

echo

echo "Done."
if [ -f "$LOOKUP_SOCKET_UNIT_PATH" ] && ! systemctl is-active --quiet "$LOOKUP_UNIT_NAME.socket"; then
    if ! offer_to_start "Do you want to start the lookup helper now?" "$LOOKUP_UNIT_NAME.socket"; then
        echo "Start it later with sudo systemctl start $LOOKUP_UNIT_NAME.socket."
    fi
fi
if [ -f "$SERVICE_UNIT_PATH" ]; then
    offer_to_restart_service
fi
