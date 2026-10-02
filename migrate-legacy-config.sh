migrate_legacy_config_files() {
    local name legacy_file target
    for name in config.conf secrets.conf; do
        legacy_file="$APP_DIR/$name"
        target="$INSTANCE_DIR/$name"
        if [ -f "$legacy_file" ] && [ ! -f "$target" ]; then
            mkdir -p "$INSTANCE_DIR"
            mv "$legacy_file" "$target"
            echo "Moved $name to instance/$name."
        fi
    done
}

migrate_legacy_submissions_db() {
    local legacy_db="$APP_DIR/applications.db" db="$INSTANCE_DIR/submissions.db" suffix
    if [ -f "$legacy_db" ] && [ ! -f "$db" ]; then
        for suffix in "" -wal -shm; do
            if [ -f "$legacy_db$suffix" ]; then
                mv "$legacy_db$suffix" "$db$suffix"
            fi
        done
        echo "Moved applications.db to instance/submissions.db."
    fi
}

migrate_legacy_expiry_days() {
    local legacy_value
    legacy_value=$(get_conf_value application_expiry_days "$CONFIG_FILE")
    if [ -n "$legacy_value" ]; then
        remove_conf_value application_expiry_days "$CONFIG_FILE"
        if [ -z "$(get_conf_value submission_expiry_days "$CONFIG_FILE")" ]; then
            printf 'submission_expiry_days = %s\n' "$legacy_value" >> "$CONFIG_FILE"
        fi
        echo "Migrated application_expiry_days ($legacy_value) to submission_expiry_days."
    fi
}

migrate_legacy_email_subject() {
    local legacy_value
    legacy_value=$(get_conf_value application_email_subject "$CONFIG_FILE")
    if [ -n "$legacy_value" ]; then
        remove_conf_value application_email_subject "$CONFIG_FILE"
        if [ -z "$(get_conf_value submission_email_subject "$CONFIG_FILE")" ]; then
            printf 'submission_email_subject = %s\n' "$legacy_value" >> "$CONFIG_FILE"
        fi
        echo "Migrated application_email_subject ($legacy_value) to submission_email_subject."
    fi
}

migrate_legacy_channel_id() {
    local legacy_channel_id legacy_subject topic
    legacy_channel_id=$(get_conf_value application_channel_id "$CONFIG_FILE")
    if [ -z "$legacy_channel_id" ]; then
        return
    fi

    if [ ! -f "$DESTINATIONS_FILE" ] || [ "$("$VENV_PYTHON" "$APP_DIR/destinations_cli.py" is-placeholder "$DESTINATIONS_FILE")" = "yes" ]; then
        legacy_subject=$(get_conf_value submission_email_subject "$CONFIG_FILE")
        topic="${legacy_subject:-New application}"
        "$VENV_PYTHON" "$APP_DIR/destinations_cli.py" set "$DESTINATIONS_FILE" default zulip channel_id "$legacy_channel_id" topic "$topic"
        remove_conf_value application_channel_id "$CONFIG_FILE"
        echo "Migrated the old application_channel_id setting ($legacy_channel_id) into destinations.yaml as the 'default' destination."
        if [ -n "$legacy_subject" ]; then
            remove_conf_value submission_email_subject "$CONFIG_FILE"
            echo "Migrated the old submission_email_subject setting ($legacy_subject) into that destination's topic."
        fi
    fi
}

migrate_legacy_services() {
    local legacy_service="$SYSTEMD_DIR/web-zulip-application-form.service" unit
    if [ ! -f "$legacy_service" ]; then
        return
    fi
    for unit in web-zulip-application-form web-zulip-application-form-check-pending.timer \
        web-zulip-application-form-check-pending.service; do
        systemctl disable --now "$unit" 2>/dev/null || true
    done
    rm -f "$legacy_service" "$SYSTEMD_DIR/web-zulip-application-form-check-pending.service" \
        "$SYSTEMD_DIR/web-zulip-application-form-check-pending.timer"
    systemctl daemon-reload
    echo "Stopped and removed the old web-zulip-application-form services."
}

run_legacy_config_migrations() {
    migrate_legacy_services
    migrate_legacy_submissions_db
    migrate_legacy_expiry_days
    migrate_legacy_email_subject
    migrate_legacy_channel_id
}
