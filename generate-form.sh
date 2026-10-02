#!/usr/bin/env bash
set -e

if [ "$EUID" -ne 0 ]; then
    echo "Error: This script must be run as root." >&2
    exit 1
fi

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

if [ -z "$(get_conf_value site_kind "$CONFIG_FILE")" ] || [ -z "$(get_conf_value site_root "$CONFIG_FILE")" ]; then
    echo "Error: no site configured yet. Run ./install.sh first." >&2
    exit 1
fi

ensure_venv
regenerate_files

if [ "$(get_conf_value site_kind "$CONFIG_FILE")" = "Pelican" ]; then
    echo
    echo "Rebuild your Pelican site to pick up the changes."
fi
