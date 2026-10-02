APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_AS_USER="${SUDO_USER:-$(id -un)}"
INSTANCE_DIR="$APP_DIR/instance"
SECRETS_FILE="$INSTANCE_DIR/secrets.conf"
CONFIG_FILE="$INSTANCE_DIR/config.conf"
DESTINATIONS_FILE="$INSTANCE_DIR/destinations.yaml"
SITE_CONFIG_BACKUP_DIR="$INSTANCE_DIR/backups/site-config"
DEFAULT_OUTPUT_DIR="$APP_DIR/output"
VENV_PYTHON="$APP_DIR/.venv/bin/python"
VENV_FLASK="$APP_DIR/.venv/bin/flask --app app"

SYSTEMD_DIR="${WBZP_INTAKE_SYSTEMD_DIR:-/etc/systemd/system}"
ZULIP_SETTINGS_FILE="${WBZP_INTAKE_ZULIP_SETTINGS:-/etc/zulip/settings.py}"

SERVICE_NAME=wbzp-intake
SERVICE_UNIT_PATH=$SYSTEMD_DIR/$SERVICE_NAME.service
CHECK_PENDING_UNIT_NAME=wbzp-intake-check-pending
CHECK_PENDING_SERVICE_UNIT_PATH=$SYSTEMD_DIR/$CHECK_PENDING_UNIT_NAME.service
CHECK_PENDING_TIMER_UNIT_PATH=$SYSTEMD_DIR/$CHECK_PENDING_UNIT_NAME.timer
CHECK_BOUNCES_UNIT_NAME=wbzp-intake-check-bounces
CHECK_BOUNCES_SERVICE_UNIT_PATH=$SYSTEMD_DIR/$CHECK_BOUNCES_UNIT_NAME.service
CHECK_BOUNCES_TIMER_UNIT_PATH=$SYSTEMD_DIR/$CHECK_BOUNCES_UNIT_NAME.timer

conf_section() {
    [ "$1" = "$SECRETS_FILE" ] && echo secrets || echo config
}

get_conf_value() {
    local key="$1" file="$2"
    python3 "$APP_DIR/config_cli.py" get "$file" "$(conf_section "$file")" "$key"
}

set_conf_value() {
    local key="$1" value="$2" file="$3"
    python3 "$APP_DIR/config_cli.py" set "$file" "$key" "$value"
}

remove_conf_value() {
    local key="$1" file="$2"
    python3 "$APP_DIR/config_cli.py" remove "$file" "$key"
}

ask() {
    echo >&2
    read "$@"
}

first_destination_name() {
    local name
    if [ -z "$("$VENV_PYTHON" "$APP_DIR/destinations_cli.py" names "$DESTINATIONS_FILE" | grep -x default)" ]         || [ "$("$VENV_PYTHON" "$APP_DIR/destinations_cli.py" is-placeholder "$DESTINATIONS_FILE")" = "yes" ]; then
        echo default
        return
    fi
    ask -r -p "A 'default' destination already exists. What should this destination be called? (use 'default' to replace it) " name
    echo "$name"
}

ask_with_hint() {
    local question="$1" hint="$2" var="$3"
    shift 3
    echo >&2
    echo "$question" >&2
    read -r "$@" -p "$hint" "$var"
}

ask_choice() {
    echo >&2
    echo "$1" >&2
    PS3="Enter a number: "
}

confirmed() {
    [ "$1" = "y" ] || [ "$1" = "Y" ]
}

sync_service_units() {
    local site_root service_user

    site_root=$(get_conf_value site_root "$CONFIG_FILE")
    if [ -z "$site_root" ]; then
        return 0
    fi

    service_user="$RUN_AS_USER"

    chown -R "$service_user:$service_user" "$APP_DIR"

    sed -e "s|__APP_ROOT__|$APP_DIR|g" -e "s|__RUN_AS_USER__|$service_user|g" \
        "$APP_DIR/deploy/$SERVICE_NAME.service" > "$SERVICE_UNIT_PATH"
    sed -e "s|__APP_ROOT__|$APP_DIR|g" -e "s|__RUN_AS_USER__|$service_user|g" \
        "$APP_DIR/deploy/$CHECK_PENDING_UNIT_NAME.service" > "$CHECK_PENDING_SERVICE_UNIT_PATH"
    cp "$APP_DIR/deploy/$CHECK_PENDING_UNIT_NAME.timer" "$CHECK_PENDING_TIMER_UNIT_PATH"
    sed -e "s|__APP_ROOT__|$APP_DIR|g" -e "s|__RUN_AS_USER__|$service_user|g" \
        "$APP_DIR/deploy/$CHECK_BOUNCES_UNIT_NAME.service" > "$CHECK_BOUNCES_SERVICE_UNIT_PATH"
    cp "$APP_DIR/deploy/$CHECK_BOUNCES_UNIT_NAME.timer" "$CHECK_BOUNCES_TIMER_UNIT_PATH"

    systemctl daemon-reload
    systemctl enable "$SERVICE_NAME" "$CHECK_PENDING_UNIT_NAME.timer"
    if bounce_checking_enabled; then
        systemctl enable "$CHECK_BOUNCES_UNIT_NAME.timer"
    else
        systemctl disable --now "$CHECK_BOUNCES_UNIT_NAME.timer" 2>/dev/null || true
    fi
}

bounce_checking_enabled() {
    [ "$(get_conf_value bounce_source "$CONFIG_FILE")" = "imap" ] \
        && [ "$("$VENV_PYTHON" "$APP_DIR/destinations_cli.py" has-email "$DESTINATIONS_FILE")" = "yes" ]
}

prompt_for_key() {
    local key="$1" label="$2" silent="$3" required="$4" file="$5" current new_value
    current=$(get_conf_value "$key" "$file")
    if [ "$silent" = "yes" ]; then
        if [ -n "$current" ]; then
            ask_with_hint "$label" "Press Enter to keep the current value: " new_value -s
        else
            ask -r -s -p "$label " new_value
        fi
        echo
    else
        if [ -n "$current" ]; then
            ask_with_hint "$label" "Press Enter to keep the current value [$current]: " new_value
        else
            ask -r -p "$label " new_value
        fi
    fi
    if [ -n "$new_value" ]; then
        set_conf_value "$key" "$new_value" "$file"
    elif [ -z "$current" ] && [ "$required" = "yes" ]; then
        echo "Error: $key is required." >&2
        exit 1
    fi
}

prompt_setting() {
    local key="$1" label="$2" silent="$3" file="$4"
    prompt_for_key "$key" "$label" "$silent" yes "$file"
}

smtp_is_complete() {
    local key
    for key in smtp_host smtp_port smtp_user; do
        [ -n "$(get_conf_value "$key" "$CONFIG_FILE")" ] || return 1
    done
    [ -n "$(get_conf_value smtp_password "$SECRETS_FILE")" ]
}

zulip_email_available() {
    grep -q "^EMAIL_HOST" "$ZULIP_SETTINGS_FILE" 2>/dev/null
}

prompt_smtp_settings() {
    prompt_for_key smtp_host "What is your SMTP server?" no yes "$CONFIG_FILE"
    prompt_for_key smtp_port "What is your SMTP port?" no yes "$CONFIG_FILE"
    prompt_for_key smtp_user "What is your SMTP login?" no yes "$CONFIG_FILE"
    prompt_for_key smtp_password "What is your SMTP password?" yes yes "$SECRETS_FILE"
    prompt_for_key smtp_from "What address should emails come from? (defaults to the SMTP login)" no no "$CONFIG_FILE"
}

choose_provider() {
    local flow="$1" ids=() names=() id name
    while IFS=$'\t' read -r id name; do
        ids+=("$id")
        names+=("$name")
    done < <("$VENV_PYTHON" "$APP_DIR/email_providers_cli.py" providers "$flow")
    ask_choice "Which one?"
    select name in "${names[@]}"; do
        if [ -n "$name" ]; then
            echo "${ids[$((REPLY - 1))]}"
            return
        fi
        echo "Please choose a number 1-${#names[@]}." >&2
        echo >&2
    done
}

setup_smtp() {
    local flow_ids=() flow_labels=() options=() id label choice index
    while IFS=$'\t' read -r id label; do
        flow_ids+=("$id")
        flow_labels+=("$label")
    done < <("$VENV_PYTHON" "$APP_DIR/email_providers_cli.py" flows)

    options=("${flow_labels[@]}")
    if zulip_email_available; then
        options+=("The same server as Zulip (you'll enter your own login)")
    fi
    options+=("Another email service (enter all settings myself)" "Skip email setup")

    ask_choice "How will this app send email?"
    select choice in "${options[@]}"; do
        if [ -n "$choice" ]; then
            break
        fi
        echo "Please choose a number 1-${#options[@]}." >&2
        echo >&2
    done
    index=$((REPLY - 1))

    if [ "$index" -lt "${#flow_ids[@]}" ]; then
        configure_smtp_provider "$(choose_provider "${flow_ids[$index]}")"
        return
    fi

    set_conf_value smtp_provider "" "$CONFIG_FILE"
    case "$choice" in
        "The same server as Zulip"*)
            copy_zulip_email_server
            prompt_for_key smtp_user "What is your SMTP login? (create one for this app in your email provider)" no yes "$CONFIG_FILE"
            prompt_for_key smtp_password "What is your SMTP password?" yes yes "$SECRETS_FILE"
            prompt_for_key smtp_from "What address should emails come from? (defaults to the SMTP login)" no no "$CONFIG_FILE"
            ;;
        "Another email service"*)
            prompt_smtp_settings
            ;;
        *)
            return 1
            ;;
    esac
}

configure_bounce_checking_manually() {
    local confirm
    ask -r -p "Check an IMAP mailbox for bounced emails? Answer no if you'll watch for bounces in your email provider instead. [y/N] " confirm
    if confirmed "$confirm"; then
        set_conf_value bounce_source imap "$CONFIG_FILE"
        prompt_for_key bounce_imap_host "What is your IMAP server?" no yes "$CONFIG_FILE"
        prompt_for_key bounce_imap_port "What is your IMAP port? (defaults to 993)" no no "$CONFIG_FILE"
        prompt_for_key bounce_imap_folder "Which folder should be checked for bounces? (defaults to INBOX)" no no "$CONFIG_FILE"
        prompt_for_key bounce_imap_user "What is your IMAP login? (defaults to the SMTP login)" no no "$CONFIG_FILE"
        prompt_for_key bounce_imap_password "What is your IMAP password? (defaults to the SMTP password)" yes no "$SECRETS_FILE"
    else
        set_conf_value bounce_source external "$CONFIG_FILE"
        echo "This app won't check for bounced emails. Watch for them in your email provider."
    fi
}

configure_bounce_checking() {
    local smtp_provider flow bounces confirm
    smtp_provider=$(get_conf_value smtp_provider "$CONFIG_FILE")
    flow=""
    bounces=""
    if [ -n "$smtp_provider" ]; then
        flow=$(provider_field "$smtp_provider" flow)
        bounces=$(flow_field "$flow" bounces)
    fi

    case "$bounces" in
        imap)
            ask -r -p "Check $(provider_field "$smtp_provider" name) for bounced emails, using the same login? [Y/n] " confirm
            if [ -z "$confirm" ] || confirmed "$confirm"; then
                set_conf_value bounce_source imap "$CONFIG_FILE"
                set_conf_value bounce_imap_host "$(provider_field "$smtp_provider" imap_host)" "$CONFIG_FILE"
                prompt_for_key bounce_imap_folder "Which folder should be checked for bounces? (defaults to INBOX)" no no "$CONFIG_FILE"
            else
                configure_bounce_checking_manually
            fi
            ;;
        external)
            set_conf_value bounce_source external "$CONFIG_FILE"
            echo "$(flow_field "$flow" bounce_note)"
            ;;
        *)
            configure_bounce_checking_manually
            ;;
    esac
}

offer_test_email() {
    local contact confirm
    contact=$(get_conf_value contact_email "$CONFIG_FILE")
    if [ -z "$contact" ]; then
        return 0
    fi
    ask -r -p "Send a test email to $contact to check your email settings? [y/N] " confirm
    if confirmed "$confirm"; then
        if ! (cd "$APP_DIR" && $VENV_FLASK test-email); then
            echo "The test failed. Run sudo ./install.sh again to change your email settings."
        fi
    fi
}

provider_field() {
    "$VENV_PYTHON" "$APP_DIR/email_providers_cli.py" provider "$1" "$2"
}

flow_field() {
    "$VENV_PYTHON" "$APP_DIR/email_providers_cli.py" flow "$1" "$2"
}

configure_smtp_provider() {
    local provider="$1" host region note
    host=$(provider_field "$provider" smtp_host)
    if [[ "$host" == *"{region}"* ]]; then
        ask -r -p "$(provider_field "$provider" region_prompt) " region
        if [ -z "$region" ]; then
            echo "Error: a region is required." >&2
            exit 1
        fi
        host="${host/\{region\}/$region}"
    fi
    set_conf_value smtp_provider "$provider" "$CONFIG_FILE"
    set_conf_value smtp_host "$host" "$CONFIG_FILE"
    set_conf_value smtp_port "$(provider_field "$provider" smtp_port)" "$CONFIG_FILE"
    set_conf_value smtp_security "$(provider_field "$provider" smtp_security)" "$CONFIG_FILE"

    if [ -n "$(provider_field "$provider" smtp_user)" ]; then
        set_conf_value smtp_user "$(provider_field "$provider" smtp_user)" "$CONFIG_FILE"
    else
        prompt_for_key smtp_user "$(provider_field "$provider" login_prompt)" no yes "$CONFIG_FILE"
    fi
    prompt_for_key smtp_password "$(provider_field "$provider" password_prompt)" yes yes "$SECRETS_FILE"

    if [ "$(provider_field "$provider" from_required)" = "yes" ]; then
        prompt_for_key smtp_from "What address should emails come from?" no yes "$CONFIG_FILE"
    else
        prompt_for_key smtp_from "What address should emails come from? (defaults to the SMTP login)" no no "$CONFIG_FILE"
    fi

    note=$(provider_field "$provider" note)
    if [ -n "$note" ]; then
        echo "$note"
    fi
}

copy_zulip_email_server() {
    local server host port security
    server=$("$VENV_PYTHON" "$APP_DIR/zulip_email_cli.py" "$ZULIP_SETTINGS_FILE")
    IFS='|' read -r host port security <<< "$server"
    set_conf_value smtp_host "$host" "$CONFIG_FILE"
    set_conf_value smtp_port "$port" "$CONFIG_FILE"
    set_conf_value smtp_security "$security" "$CONFIG_FILE"
}

require_absolute_path() {
    case "$1" in
        /*) ;;
        *)
            echo "Error: $1 must be an absolute path." >&2
            exit 1
            ;;
    esac
}

ensure_config_files() {
    mkdir -p "$INSTANCE_DIR"
    chmod 700 "$INSTANCE_DIR"

    if [ ! -f "$SECRETS_FILE" ]; then
        cat > "$SECRETS_FILE" <<'EOF'
[secrets]
smtp_password =
turnstile_secret =
zulip_bot_api_key =
bounce_imap_password =
EOF
    fi

    if [ ! -f "$CONFIG_FILE" ]; then
        cat > "$CONFIG_FILE" <<'EOF'
[config]
site_kind =
site_root =
zulip_site_url =
zulip_bot_email =
contact_email =
alert_emails_enabled =
smtp_provider =
smtp_host =
smtp_port =
smtp_security =
smtp_user =
smtp_from =
turnstile_site_key =
bounce_source =
bounce_imap_host =
bounce_imap_port =
bounce_imap_folder =
bounce_imap_user =

max_attempts_per_ip = 10
max_attempts_per_email = 3
rate_limit_window_minutes = 60
submission_expiry_days = 30
max_body_bytes = 8192
max_text_length = 250
max_textarea_length = 1000
EOF
    fi

    chmod 600 "$SECRETS_FILE" "$CONFIG_FILE"
}

ensure_venv() {
    if [ ! -d "$APP_DIR/.venv" ]; then
        if ! python3 -c "import ensurepip" 2>/dev/null; then
            echo "Error: Python's venv module is missing. On Ubuntu or Debian, install it with:" >&2
            echo "    sudo apt install python3-venv" >&2
            exit 1
        fi
        python3 -m venv "$APP_DIR/.venv"
    fi
    "$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
}

backup_before_upgrade() {
    local file="$1" backup
    mkdir -p "$SITE_CONFIG_BACKUP_DIR"
    backup="$SITE_CONFIG_BACKUP_DIR/$(basename "$file").bak.$(date +%s)"
    cp "$file" "$backup"
    echo "$backup"
}

upgrade_old_site() {
    local site_root="$1" template_overrides_dir="$2" backup start_line end_line confirm

    if [ -f "$site_root/data/application-limits.json" ]; then
        rm -f "$site_root/data/application-limits.json"
        echo "Removed $site_root/data/application-limits.json."
    fi

    if [ -f "$site_root/data/application-fields.json" ]; then
        rm -f "$site_root/data/application-fields.json"
        echo "Removed the old single-file $site_root/data/application-fields.json."
        echo "Form definitions now live in $APP_DIR/forms/, one file per form."
    fi

    if [ -f "$site_root/content/extra/js/application-form.js" ] || [ -f "$site_root/content/extra/css/application-form.css" ]; then
        rm -f "$site_root/content/extra/js/application-form.js" "$site_root/content/extra/css/application-form.css"
        echo "Removed the old application-form.js/application-form.css. They're now wbzp-intake-form.js/wbzp-intake-form.css."
    fi

    if [ -n "$template_overrides_dir" ] && [ -f "$template_overrides_dir/application-form.html" ]; then
        rm -f "$template_overrides_dir/application-form.html"
        echo "Removed the old $template_overrides_dir/application-form.html."
        echo "Each form template now generates its own <name>-form.html instead."
    fi

    if [ -n "$template_overrides_dir" ] && [ -f "$site_root/pelicanconf.py" ] \
        && grep -q "JINJA_GLOBALS = {'application_fields': APPLICATION_FIELDS}" "$site_root/pelicanconf.py"; then
        echo
        echo "$site_root/pelicanconf.py has an old application_fields JINJA_GLOBALS block:"
        echo
        grep -n -B2 "JINJA_GLOBALS = {'application_fields': APPLICATION_FIELDS}" "$site_root/pelicanconf.py"
        echo
        end_line=$(grep -n "JINJA_GLOBALS = {'application_fields': APPLICATION_FIELDS}" "$site_root/pelicanconf.py" | head -1 | cut -d: -f1)
        start_line=$(grep -n "^with open(os.path.join(os.path.dirname(__file__), 'data', 'application-fields.json')) as f:" "$site_root/pelicanconf.py" | head -1 | cut -d: -f1)
        if [ -n "$start_line" ] && [ -n "$end_line" ] && [ "$start_line" -le "$end_line" ]; then
            ask -r -p "Remove those lines from pelicanconf.py? [y/N] " confirm
            if confirmed "$confirm"; then
                backup=$(backup_before_upgrade "$site_root/pelicanconf.py")
                sed -i "${start_line},${end_line}d" "$site_root/pelicanconf.py"
                echo "Removed. Backup kept at $backup."
            fi
        else
            echo "Couldn't find the exact block boundaries. Remove it by hand."
        fi
    fi

    if [ -n "$template_overrides_dir" ] && [ -f "$template_overrides_dir/application.html" ] \
        && grep -q "{% macro render_field" "$template_overrides_dir/application.html"; then
        echo
        echo "$template_overrides_dir/application.html is the old full-page template."
        echo "It won't work with the generated form."
        ask -r -p "Replace it with the new starter template? [y/N] " confirm
        if confirmed "$confirm"; then
            backup=$(backup_before_upgrade "$template_overrides_dir/application.html")
            cp "$APP_DIR/generator/page-template.example.jinja" "$template_overrides_dir/application.html"
            chown "$RUN_AS_USER:$RUN_AS_USER" "$template_overrides_dir/application.html"
            echo "Replaced. Your old version is backed up at $backup. Customize"
            echo "the new starter for your theme."
        fi
    fi
}

write_form_files() {
    local templates_dir="$1" output_dir="$2" js_file="$3" css_file="$4" css_url="$5" js_url="$6"
    mkdir -p "$(dirname "$js_file")"
    cp "$APP_DIR/generator/wbzp-intake-form.js" "$js_file"
    mkdir -p "$(dirname "$css_file")"
    cp "$APP_DIR/generator/wbzp-intake-form.css" "$css_file"
    chown "$RUN_AS_USER:$RUN_AS_USER" "$js_file" "$css_file"

    mkdir -p "$output_dir"
    "$VENV_PYTHON" "$APP_DIR/generator/generate_form.py" "$templates_dir" "$output_dir" \
        --max-text-length "${max_text_length:-250}" --max-textarea-length "${max_textarea_length:-1000}" \
        --turnstile-site-key "$turnstile_site_key" --css-url "$css_url" --js-url "$js_url"
    chown -R "$RUN_AS_USER:$RUN_AS_USER" "$output_dir"
}

write_starter_page_templates() {
    local template_overrides_dir="$1" template_file name page_template_file

    for template_file in "$APP_DIR"/forms/*.yaml; do
        name=$(basename "$template_file" .yaml)
        page_template_file="$template_overrides_dir/$name.html"
        if [ ! -f "$page_template_file" ]; then
            sed "s/submission-form\.html/$name-form.html/" \
                "$APP_DIR/generator/page-template.example.jinja" > "$page_template_file"
            chown "$RUN_AS_USER:$RUN_AS_USER" "$page_template_file"
            echo "Wrote a starter $page_template_file. Customize it for your theme,"
            echo "then add 'Template: $name' to a content page's metadata."
        fi
    done
}

regenerate_files() {
    local site_kind site_root template_overrides_dir

    site_kind=$(get_conf_value site_kind "$CONFIG_FILE")
    site_root=$(get_conf_value site_root "$CONFIG_FILE")

    upgrade_old_site "$site_root" ""

    max_text_length=$(get_conf_value max_text_length "$CONFIG_FILE")
    max_textarea_length=$(get_conf_value max_textarea_length "$CONFIG_FILE")
    turnstile_site_key=$(get_conf_value turnstile_site_key "$CONFIG_FILE")

    if [ "$site_kind" != "Pelican" ]; then
        write_form_files "$APP_DIR/forms" "$site_root" "$site_root/wbzp-intake-form.js" "$site_root/wbzp-intake-form.css" /wbzp-intake-form.css /wbzp-intake-form.js
        echo "Wrote $site_root/wbzp-intake-form.js and $site_root/wbzp-intake-form.css."
        echo "Wrote a <name>-form.html per template in $site_root. Embed each one's"
        echo "contents where you want that form to appear."
        return
    fi

    if [ ! -f "$site_root/pelicanconf.py" ]; then
        echo "Error: $site_root/pelicanconf.py not found. $site_root doesn't" >&2
        echo "look like a Pelican site checkout anymore." >&2
        exit 1
    fi

    if grep -q "^STATIC_PATHS" "$site_root/pelicanconf.py"; then
        if ! grep -q "\"extra\"\|'extra'" "$site_root/pelicanconf.py"; then
            static_paths_line=$(grep -n "^STATIC_PATHS" "$site_root/pelicanconf.py" | head -1 | cut -d: -f1)
            echo "pelicanconf.py already defines STATIC_PATHS on line $static_paths_line. Add"
            echo "'extra' to it."
        fi
    else
        printf '\nSTATIC_PATHS = ["extra"]\n' >> "$site_root/pelicanconf.py"
        echo "Added STATIC_PATHS = [\"extra\"] to pelicanconf.py."
    fi

    template_overrides_dir=$(sed -n -E \
        "s/^THEME_TEMPLATES_OVERRIDES[[:space:]]*=[[:space:]]*\[[[:space:]]*['\"]([^'\"]+)['\"].*/\1/p" \
        "$site_root/pelicanconf.py" | head -1)

    if [ -z "$template_overrides_dir" ]; then
        template_overrides_dir="templates"
        printf '\nTHEME_TEMPLATES_OVERRIDES = ["templates"]\n' >> "$site_root/pelicanconf.py"
        echo "Added THEME_TEMPLATES_OVERRIDES = [\"templates\"] to pelicanconf.py."
    fi
    case "$template_overrides_dir" in
        /*) ;;
        *) template_overrides_dir="$site_root/$template_overrides_dir" ;;
    esac
    mkdir -p "$template_overrides_dir"

    upgrade_old_site "$site_root" "$template_overrides_dir"
    chown "$RUN_AS_USER:$RUN_AS_USER" "$site_root/pelicanconf.py"

    write_form_files "$APP_DIR/forms" "$template_overrides_dir" \
        "$site_root/content/extra/js/wbzp-intake-form.js" "$site_root/content/extra/css/wbzp-intake-form.css" \
        /extra/css/wbzp-intake-form.css /extra/js/wbzp-intake-form.js
    echo "Wrote $site_root/content/extra/js/wbzp-intake-form.js."
    echo "Wrote $site_root/content/extra/css/wbzp-intake-form.css."
    echo "Wrote a <name>-form.html per template in $template_overrides_dir."

    write_starter_page_templates "$template_overrides_dir"
}
