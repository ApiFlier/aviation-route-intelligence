#!/usr/bin/env bash
# scripts/configure-credentials.sh
# Safely update optional credentials in deploy.env.
# Never prints existing secret values.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEPLOY_ENV="$REPO_DIR/deploy.env"
DEPLOY_ENV_EXAMPLE="$REPO_DIR/deploy.env.example"

# ── Helpers ───────────────────────────────────────────────────────────────────
bold()   { printf '\033[1m%s\033[0m' "$*"; }
yellow() { printf '\033[0;33m%s\033[0m' "$*"; }
red()    { printf '\033[0;31m%s\033[0m' "$*"; }

get_field() {
    local key="$1" file="$2"
    grep -E "^${key}=" "$file" 2>/dev/null | cut -d= -f2- | tr -d '[:space:]' || true
}

set_field() {
    local key="$1" value="$2" file="$3"
    # Use awk to avoid sed special-character issues in values (|, &, \)
    if grep -qE "^${key}=" "$file" 2>/dev/null; then
        awk -v k="$key" -v v="$value" \
            'BEGIN{OFS=""} $0 ~ "^"k"=" {print k"="v; next} {print}' \
            "$file" > "${file}.tmp" && mv "${file}.tmp" "$file"
    else
        printf '%s=%s\n' "$key" "$value" >> "$file"
    fi
}

clear_field() {
    local key="$1" file="$2"
    if grep -qE "^${key}=" "$file" 2>/dev/null; then
        awk -v k="$key" \
            'BEGIN{OFS=""} $0 ~ "^"k"=" {print k"="; next} {print}' \
            "$file" > "${file}.tmp" && mv "${file}.tmp" "$file"
    fi
}

status_label() {
    local key="$1" file="$2"
    local val
    val=$(get_field "$key" "$file")
    if [ -n "$val" ]; then echo "[set]"; else echo "[not set]"; fi
}

# prompt_field: visible input (for non-secret fields)
prompt_field() {
    local label="$1" key="$2" file="$3"
    local current_status
    current_status=$(status_label "$key" "$file")
    printf "  %-20s %s  Enter new value (or press Enter to keep, type CLEAR to remove): " \
        "${label}:" "$current_status"
    local input
    read -r input < /dev/tty || input=""
    _apply_field_input "$key" "$file" "$input"
}

# prompt_secret_field: hidden input (for passwords and queue names)
prompt_secret_field() {
    local label="$1" key="$2" file="$3"
    local current_status
    current_status=$(status_label "$key" "$file")
    printf "  %-20s %s  Enter new value (hidden, Enter to keep, CLEAR to remove): " \
        "${label}:" "$current_status"
    local input
    read -rs input < /dev/tty || input=""
    echo ""  # newline after hidden input
    _apply_field_input "$key" "$file" "$input"
}

_apply_field_input() {
    local key="$1" file="$2" input="$3"
    if [ "$input" = "CLEAR" ]; then
        local cur
        cur=$(get_field "$key" "$file")
        if [ -n "$cur" ]; then
            printf "  Clear %s? This will disable any feature that depends on it. [y/N] " "$key"
            local confirm
            read -r confirm < /dev/tty || confirm=""
            if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
                clear_field "$key" "$file"
                echo "  Cleared: $key"
            else
                echo "  Kept existing value."
            fi
        else
            echo "  Already empty."
        fi
    elif [ -n "$input" ]; then
        set_field "$key" "$input" "$file"
        echo "  Updated: $key"
    else
        echo "  Kept existing value."
    fi
}

# ── Main ──────────────────────────────────────────────────────────────────────
echo "============================================="
bold "  FlightConn — Credential / Config Setup"
echo ""
echo "============================================="
echo ""
echo "  This helper updates deploy.env (optional user config)."
echo "  It never prints existing secret values."
echo "  Press Enter to keep any existing value."
echo "  Type CLEAR to remove a value (with confirmation)."
echo ""

# Create deploy.env from example if missing
if [ ! -f "$DEPLOY_ENV" ]; then
    if [ -f "$DEPLOY_ENV_EXAMPLE" ]; then
        echo "  deploy.env not found. Creating from deploy.env.example..."
        cp "$DEPLOY_ENV_EXAMPLE" "$DEPLOY_ENV"
        echo "  Created: $DEPLOY_ENV"
    else
        echo "  Creating empty deploy.env..."
        touch "$DEPLOY_ENV"
    fi
    echo ""
fi

echo "  --- FAA SWIM Credentials (optional) ---"
echo "  The main app runs fully without these."
echo "  Required only for the optional FAA SWIM recent activity sidecar."
echo "  FAA credentials are issued after a SWIM Service Access Agreement (SAA)."
echo "  See: https://www.faa.gov/air_traffic/technology/swim"
echo ""

prompt_field         "FAA_USER"   "FAA_USER"   "$DEPLOY_ENV"
prompt_secret_field  "FAA_PASS"   "FAA_PASS"   "$DEPLOY_ENV"

echo ""
echo "  --- FAA SWIM Queue Names (optional) ---"
echo "  Queue names are assigned by FAA after account setup."
echo "  At least one queue is required for SWIM ingestion to run."
echo "  Queue names are treated as credentials — input is hidden."
echo "  (SFDPS = en-route, STDDS = terminal, TFMS = flow management)"
echo ""

prompt_secret_field "QUEUE_SFDPS" "QUEUE_SFDPS" "$DEPLOY_ENV"
prompt_secret_field "QUEUE_STDDS" "QUEUE_STDDS" "$DEPLOY_ENV"
prompt_secret_field "QUEUE_TFMS"  "QUEUE_TFMS"  "$DEPLOY_ENV"

echo ""
echo "  --- SWIM Auto-Detection ---"
echo "  setup.sh and update.sh detect SWIM readiness automatically."
echo "  If FAA_USER, FAA_PASS, and at least one QUEUE_* are set, SWIM"
echo "  ingestion starts with the swim profile. No flags to set."
echo ""

# Show current status without printing values
echo "  Current credential status in deploy.env:"
for KEY in FAA_USER FAA_PASS QUEUE_SFDPS QUEUE_STDDS QUEUE_TFMS; do
    printf "    %-20s %s\n" "${KEY}:" "$(status_label "$KEY" "$DEPLOY_ENV")"
done
echo ""

# Offer to rebuild/restart so changes take effect
printf "  Apply changes now by running update.sh? [y/N] "
read -r RUN_UPDATE < /dev/tty || RUN_UPDATE=""
if [ "$RUN_UPDATE" = "y" ] || [ "$RUN_UPDATE" = "Y" ]; then
    echo ""
    echo "--- Running update.sh ---"
    "$REPO_DIR/update.sh"
else
    echo ""
    echo "  Changes saved. Run ./update.sh (or ./menu.sh option 2) when ready to apply."
fi
