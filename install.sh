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

echo "App root: $APP_DIR"
echo "Running as: $RUN_AS_USER"

migrate_legacy_config_files
ensure_config_files
ensure_venv
run_legacy_config_migrations

validate_turnstile_pair() {
    local site_key secret
    site_key=$(get_conf_value turnstile_site_key "$CONFIG_FILE")
    secret=$(get_conf_value turnstile_secret "$SECRETS_FILE")
    if [ -n "$site_key" ] && [ -z "$secret" ]; then
        echo "Error: turnstile_site_key is set but turnstile_secret is not. Set both or clear both." >&2
        exit 1
    fi
    if [ -z "$site_key" ] && [ -n "$secret" ]; then
        echo "Error: turnstile_secret is set but turnstile_site_key is not. Set both or clear both." >&2
        exit 1
    fi
}

prompt_contact_email() {
    prompt_setting contact_email "What is your contact email? (shown to people with a pending submission, used for email alerts)" no "$CONFIG_FILE"
}

detect_site_confs() {
    local kind="$1" f
    case "$kind" in
        nginx)
            for f in /etc/nginx/sites-enabled/* /etc/nginx/conf.d/*.conf; do
                [ -f "$f" ] && grep -q -E 'server[[:space:]]*\{' "$f" && echo "$f"
            done
            ;;
        apache)
            for f in /etc/apache2/sites-enabled/*.conf; do
                [ -f "$f" ] && grep -q -i '<VirtualHost' "$f" && echo "$f"
            done
            ;;
        caddy)
            [ -f /etc/caddy/Caddyfile ] && echo /etc/caddy/Caddyfile
            ;;
    esac
}

resolve_site_conf() {
    local kind="$1" candidates=() override choice c

    while IFS= read -r c; do
        [ -n "$c" ] && candidates+=("$c")
    done < <(detect_site_confs "$kind")

    if [ "${#candidates[@]}" -eq 1 ]; then
        echo "Detected $kind site config: ${candidates[0]}" >&2
        ask_with_hint "What is the path to your $kind site config file?" "Press Enter to use the detected one [${candidates[0]}]: " override
        if [ -n "$override" ]; then
            echo "$override"
        else
            echo "${candidates[0]}"
        fi
        return
    fi

    if [ "${#candidates[@]}" -gt 1 ]; then
        echo "Found multiple $kind site configs:" >&2
        ask_choice "Which $kind config should I use?"
        select choice in "${candidates[@]}" "other (type a path)"; do
            [ -n "$choice" ] && break
            echo "Please choose a number 1-$((${#candidates[@]} + 1))." >&2
            echo >&2
        done
        if [ "$choice" = "other (type a path)" ]; then
            ask -r -p "What is the path to your $kind site config file? " choice
        fi
        echo "$choice"
        return
    fi

    ask -r -p "What is the path to your $kind site config file? " choice
    echo "$choice"
}

apply_reverse_proxy_config() {
    local kind="$1" snippet_file="$2" close_marker="$3" site_conf backup line_no confirm

    site_conf=$(resolve_site_conf "$kind")
    if [ ! -f "$site_conf" ]; then
        echo "Error: $site_conf not found." >&2
        return 1
    fi

    if grep -qF "127.0.0.1:8793" "$site_conf"; then
        echo "$site_conf already proxies to 127.0.0.1:8793. Skipping."
        return 0
    fi

    mkdir -p "$SITE_CONFIG_BACKUP_DIR"
    backup="$SITE_CONFIG_BACKUP_DIR/$(basename "$site_conf").bak.$(date +%s)"
    cp "$site_conf" "$backup"

    line_no=$(grep -n -F "$close_marker" "$site_conf" | tail -1 | cut -d: -f1)
    if [ -z "$line_no" ]; then
        echo "Error: couldn't find '$close_marker' in $site_conf." >&2
        return 1
    fi

    awk -v n="$line_no" -v snippet="$snippet_file" '
        NR==n { while ((getline line < snippet) > 0) print line }
        { print }
    ' "$site_conf" >"${site_conf}.tmp" && mv "${site_conf}.tmp" "$site_conf"

    echo "--- Proposed change to $site_conf ---"
    diff -u "$backup" "$site_conf" || true
    echo "--------------------------------------"
    ask -r -p "Apply this change and reload $kind? [y/N] " confirm
    if ! confirmed "$confirm"; then
        cp "$backup" "$site_conf"
        echo "Reverted. $site_conf left unchanged. Backup kept at $backup."
        return 1
    fi

    case "$kind" in
        nginx)
            if ! nginx -t; then
                cp "$backup" "$site_conf"
                echo "nginx config test failed. Restored $site_conf from backup." >&2
                return 1
            fi
            systemctl reload nginx
            ;;
        apache)
            if ! apache2ctl configtest; then
                cp "$backup" "$site_conf"
                echo "Apache config test failed. Restored $site_conf from backup." >&2
                return 1
            fi
            systemctl reload apache2
            ;;
        caddy)
            if ! caddy validate --config "$site_conf" --adapter caddyfile; then
                cp "$backup" "$site_conf"
                echo "Caddy config validation failed. Restored $site_conf from backup." >&2
                return 1
            fi
            systemctl reload caddy
            ;;
    esac

    echo "$kind reloaded. Backup of the original file kept at $backup."
}

ask_choice "Are you using Pelican for your site, or is it custom?"
select SITE_KIND in "Pelican" "Other"; do
    if [ -n "$SITE_KIND" ]; then
        break
    fi
    echo "Please choose a number 1-2." >&2
    echo >&2
done

if [ "$SITE_KIND" = "Pelican" ]; then
    ask -r -p "Generate the form files in your Pelican checkout? [y/N] " confirm
    if ! confirmed "$confirm"; then
        SITE_KIND="Other"
    fi
fi
set_conf_value site_kind "$SITE_KIND" "$CONFIG_FILE"

if [ "$SITE_KIND" = "Pelican" ]; then
    prompt_for_key site_root "What is the path to your Pelican checkout?" no yes "$CONFIG_FILE"
    SITE_ROOT=$(get_conf_value site_root "$CONFIG_FILE")
    require_absolute_path "$SITE_ROOT"

    if [ ! -f "$SITE_ROOT/pelicanconf.py" ]; then
        echo "Error: $SITE_ROOT/pelicanconf.py not found. This doesn't" >&2
        echo "look like a Pelican site checkout." >&2
        exit 1
    fi
else
    SITE_ROOT="$DEFAULT_OUTPUT_DIR"
    set_conf_value site_root "$SITE_ROOT" "$CONFIG_FILE"
fi

ask_choice "Which reverse proxy are you using?"
select PROXY_CHOICE in nginx apache caddy "I'll set this up manually"; do
    if [ -n "$PROXY_CHOICE" ]; then
        break
    fi
    echo "Please choose a number 1-4." >&2
    echo >&2
done

if [ "$PROXY_CHOICE" = "I'll set this up manually" ]; then
    echo "Skipping reverse proxy setup. See $APP_DIR/deploy/reverse-proxy/ for example configs to add by hand."
else
    close_marker='}'
    [ "$PROXY_CHOICE" = "apache" ] && close_marker='</VirtualHost>'
    if apply_reverse_proxy_config "$PROXY_CHOICE" "$APP_DIR/deploy/reverse-proxy/$PROXY_CHOICE.conf" "$close_marker"; then
        echo "Reverse proxy configured."
    else
        echo "Reverse proxy not configured automatically. See $APP_DIR/deploy/reverse-proxy/$PROXY_CHOICE.conf to add it by hand."
    fi
fi

prompt_contact_email

ask -r -p "Set up Cloudflare Turnstile to block spam and bots? Recommended. [y/N] " confirm
if confirmed "$confirm"; then
    prompt_for_key turnstile_site_key "What is your Turnstile site key?" no yes "$CONFIG_FILE"
    prompt_for_key turnstile_secret "What is your Turnstile secret key?" yes yes "$SECRETS_FILE"
else
    set_conf_value turnstile_site_key "" "$CONFIG_FILE"
    set_conf_value turnstile_secret "" "$SECRETS_FILE"
fi
validate_turnstile_pair

TURNSTILE_SITE_KEY=$(get_conf_value turnstile_site_key "$CONFIG_FILE")
if [ -n "$TURNSTILE_SITE_KEY" ]; then
    echo
    echo "Turnstile site key: $TURNSTILE_SITE_KEY"
    if [ "$SITE_KIND" = "Pelican" ]; then
        echo "Set the TURNSTILE_SITE_KEY environment variable to this before"
        echo "building the Pelican site, or change the default in pelicanconf.py."
    else
        echo "Put this in the data-sitekey attribute of your Turnstile widget."
    fi
fi

"$VENV_PYTHON" "$APP_DIR/destinations_cli.py" init "$DESTINATIONS_FILE" "$CONFIG_FILE"

smtp_wanted=no
if setup_smtp; then
    smtp_wanted=yes
fi

if [ "$smtp_wanted" = "yes" ]; then
    ask -r -p "Send alert emails for technical failures, failed deliveries, and bounces? Recommended. [y/N] " confirm
    if confirmed "$confirm"; then
        set_conf_value alert_emails_enabled yes "$CONFIG_FILE"
    else
        set_conf_value alert_emails_enabled no "$CONFIG_FILE"
    fi
    destination_choices=("Email" "Zulip" "Both" "I'll set this up manually")
else
    echo "Without SMTP settings, alert emails stay off and email destinations can't be used."
    set_conf_value alert_emails_enabled no "$CONFIG_FILE"
    destination_choices=("Zulip" "I'll set this up manually")
fi

ask_choice "Where should submissions go?"
select DESTINATION_CHOICE in "${destination_choices[@]}"; do
    if [ -n "$DESTINATION_CHOICE" ]; then
        break
    fi
    echo "Please choose a number 1-${#destination_choices[@]}." >&2
    echo >&2
done

case "$DESTINATION_CHOICE" in
    "Email")
        "$APP_DIR/configure-email.sh"
        ;;
    "Zulip")
        "$APP_DIR/configure-zulip.sh"
        ;;
    "Both")
        ask_choice "Which should be the default destination?"
        select DEFAULT_DESTINATION in "Email" "Zulip"; do
            if [ -n "$DEFAULT_DESTINATION" ]; then
                break
            fi
            echo "Please choose a number 1-2." >&2
            echo >&2
        done
        if [ "$DEFAULT_DESTINATION" = "Zulip" ]; then
            "$APP_DIR/configure-zulip.sh"
            "$APP_DIR/configure-email.sh"
        else
            "$APP_DIR/configure-email.sh"
            "$APP_DIR/configure-zulip.sh"
        fi
        ;;
    *)
        setup_scripts="./configure-zulip.sh"
        if [ "$smtp_wanted" = "yes" ]; then
            setup_scripts="./configure-email.sh or $setup_scripts"
        fi
        echo "Skipping destination setup. Run $setup_scripts when you're ready, or edit"
        echo "$DESTINATIONS_FILE by hand."
        ;;
esac

if [ "$smtp_wanted" = "yes" ] && [ "$DESTINATION_CHOICE" != "Email" ] && [ "$DESTINATION_CHOICE" != "Both" ]; then
    offer_test_email
fi

echo
regenerate_files
echo

sync_service_units

echo "The $SERVICE_NAME service and its timers are set up but not started."
echo "$CHECK_PENDING_UNIT_NAME.timer checks submissions awaiting signup against Zulip daily."
if bounce_checking_enabled; then
    echo "$CHECK_BOUNCES_UNIT_NAME.timer checks the bounce mailbox hourly."
fi
echo

if [ -z "$("$VENV_PYTHON" "$APP_DIR/destinations_cli.py" names "$DESTINATIONS_FILE")" ] \
    || [ "$("$VENV_PYTHON" "$APP_DIR/destinations_cli.py" is-placeholder "$DESTINATIONS_FILE")" = "yes" ]; then
    echo "instance/destinations.yaml has no real destinations yet. Run ./configure-email.sh or"
    echo "./configure-zulip.sh to add one, or edit $DESTINATIONS_FILE by hand."
    echo
fi

start_units="$SERVICE_NAME $CHECK_PENDING_UNIT_NAME.timer"
if bounce_checking_enabled; then
    start_units="$start_units $CHECK_BOUNCES_UNIT_NAME.timer"
fi

echo "When you're ready, start everything with:"
echo
echo "    sudo systemctl start $start_units"
echo
echo "The service upgrades the database each time it starts."
