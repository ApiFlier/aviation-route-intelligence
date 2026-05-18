#!/usr/bin/env bash
# scripts/troubleshoot.sh
# Diagnose and safely repair common FlightConn problems.
# Never prints secrets, queue names, or raw credentials.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_DIR/.env"
DEPLOY_ENV="$REPO_DIR/deploy.env"

# All docker compose commands need the compose file in scope
cd "$REPO_DIR"

# ── Helpers ───────────────────────────────────────────────────────────────────
bold()   { printf '\033[1m%s\033[0m' "$*"; }
green()  { printf '\033[0;32m%s\033[0m' "$*"; }
yellow() { printf '\033[0;33m%s\033[0m' "$*"; }
red()    { printf '\033[0;31m%s\033[0m' "$*"; }

ok()   { printf "  %-36s " "$1:"; green  "OK";     echo ""; }
warn() { printf "  %-36s " "$1:"; yellow "WARNING: $2"; echo ""; }
fail() { printf "  %-36s " "$1:"; red    "FAIL: $2";    echo ""; }

confirm_action() {
    local prompt="$1"
    printf "\n  %s [y/N] " "$prompt"
    local ans
    read -r ans < /dev/tty || ans=""
    [ "$ans" = "y" ] || [ "$ans" = "Y" ]
}

# ── Diagnostics ───────────────────────────────────────────────────────────────
run_diagnostics() {
    echo ""
    echo "============================================="
    bold "  FlightConn — Diagnostics"
    echo ""
    echo "============================================="
    echo ""

    ISSUES=0

    # Docker daemon
    if docker info >/dev/null 2>&1; then
        ok "Docker daemon"
    else
        fail "Docker daemon" "not running or not accessible"
        ISSUES=$((ISSUES + 1))
    fi

    # Docker Compose plugin
    if docker compose version >/dev/null 2>&1; then
        ok "Docker Compose plugin"
    else
        fail "Docker Compose plugin" "not available — update Docker Engine"
        ISSUES=$((ISSUES + 1))
    fi

    # docker-compose.yml
    if [ -f "$REPO_DIR/docker-compose.yml" ]; then
        if docker compose config --quiet 2>/dev/null; then
            ok "docker-compose.yml"
        else
            fail "docker-compose.yml" "invalid — run: docker compose config"
            ISSUES=$((ISSUES + 1))
        fi
    else
        fail "docker-compose.yml" "not found in $REPO_DIR"
        ISSUES=$((ISSUES + 1))
    fi

    # .env
    if [ -f "$ENV_FILE" ]; then
        if grep -q "APP_PORT=" "$ENV_FILE" 2>/dev/null; then
            ok ".env (contains APP_PORT)"
        else
            warn ".env" "missing APP_PORT — run ./setup.sh"
            ISSUES=$((ISSUES + 1))
        fi
    else
        fail ".env" "not found — run ./setup.sh"
        ISSUES=$((ISSUES + 1))
    fi

    # deploy.env
    if [ -f "$DEPLOY_ENV" ]; then
        ok "deploy.env (optional config present)"
    else
        ok "deploy.env (not present — optional, main app works without it)"
    fi

    # Load APP_PORT
    APP_PORT=""
    if [ -f "$ENV_FILE" ]; then
        APP_PORT=$(grep '^APP_PORT=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || true)
    fi

    # App container
    APP_STATE=$(docker inspect --format='{{.State.Status}}' "flightconn-app" 2>/dev/null || echo "absent")
    case "$APP_STATE" in
        running) ok "App container (flightconn-app)" ;;
        absent)  fail "App container" "not created — run ./setup.sh"; ISSUES=$((ISSUES + 1)) ;;
        *)       warn "App container" "state: $APP_STATE"; ISSUES=$((ISSUES + 1)) ;;
    esac

    # DB container
    DB_STATE=$(docker inspect --format='{{.State.Status}}' "flightconn-db" 2>/dev/null || echo "absent")
    DB_HEALTH=$(docker inspect --format='{{.State.Health.Status}}' "flightconn-db" 2>/dev/null || true)
    case "$DB_STATE" in
        running)
            if [ "$DB_HEALTH" = "healthy" ]; then
                ok "DB container (flightconn-db, healthy)"
            else
                warn "DB container" "running but health: ${DB_HEALTH:-unknown}"
                ISSUES=$((ISSUES + 1))
            fi
            ;;
        absent) fail "DB container" "not created — run ./setup.sh"; ISSUES=$((ISSUES + 1)) ;;
        *)      warn "DB container" "state: $DB_STATE"; ISSUES=$((ISSUES + 1)) ;;
    esac

    # SWIM ingestor
    SWIM_STATE=$(docker inspect --format='{{.State.Status}}' "flightconn-swim-ingestor" 2>/dev/null || echo "absent")
    SWIM_CONFIGURED=false
    if [ -f "$DEPLOY_ENV" ]; then
        HAS_USER=$(grep -E '^FAA_USER=.+' "$DEPLOY_ENV" 2>/dev/null || true)
        HAS_PASS=$(grep -E '^FAA_PASS=.+' "$DEPLOY_ENV" 2>/dev/null || true)
        HAS_QUEUE=false
        for q in QUEUE_SFDPS QUEUE_STDDS QUEUE_TFMS; do
            if grep -qE "^${q}=.+" "$DEPLOY_ENV" 2>/dev/null; then HAS_QUEUE=true; break; fi
        done
        if [ -n "$HAS_USER" ] && [ -n "$HAS_PASS" ] && [ "$HAS_QUEUE" = true ]; then
            SWIM_CONFIGURED=true
        fi
    fi

    if [ "$SWIM_CONFIGURED" = true ]; then
        case "$SWIM_STATE" in
            running) ok "SWIM ingestor (configured and running)" ;;
            absent)  warn "SWIM ingestor" "credentials configured but container not running — re-run ./setup.sh with swim profile"; ISSUES=$((ISSUES + 1)) ;;
            *)       warn "SWIM ingestor" "state: $SWIM_STATE"; ISSUES=$((ISSUES + 1)) ;;
        esac
    else
        if [ "$SWIM_STATE" = "running" ]; then
            ok "SWIM ingestor (running)"
        else
            ok "SWIM ingestor (not configured — optional)"
        fi
    fi

    # App health endpoint
    if [ -n "$APP_PORT" ] && [ "$APP_STATE" = "running" ]; then
        if curl -sf --max-time 6 -o /dev/null "http://localhost:${APP_PORT}/health" 2>/dev/null; then
            ok "App health endpoint (http://localhost:${APP_PORT}/health)"
        else
            fail "App health endpoint" "not responding at http://localhost:${APP_PORT}/health"
            ISSUES=$((ISSUES + 1))
        fi
    fi

    echo ""
    if [ "$ISSUES" -eq 0 ]; then
        green "  All checks passed. No issues found."; echo ""
    else
        yellow "  $ISSUES issue(s) found. See repair options below."; echo ""
    fi

    echo ""
    echo "  App URL:     http://localhost:${APP_PORT:-???}/"
    echo "  Health:      http://localhost:${APP_PORT:-???}/health"
    echo ""
}

# ── Log views ─────────────────────────────────────────────────────────────────
show_logs() {
    echo ""
    echo "  Which logs would you like to see?"
    echo "  a) App logs (last 60 lines)"
    echo "  b) DB logs (last 30 lines)"
    echo "  c) SWIM ingestor logs (last 60 lines)"
    echo "  r) Return"
    echo ""
    printf "  Enter choice: "
    read -r LOG_CHOICE < /dev/tty || LOG_CHOICE="r"
    case "$LOG_CHOICE" in
        a) echo ""; docker compose logs --tail=60 app 2>&1 || true ;;
        b) echo ""; docker compose logs --tail=30 db 2>&1 || true ;;
        c) echo ""; docker compose logs --tail=60 swim-ingestor 2>&1 || true ;;
        *) ;;
    esac
}

# ── Repair options ────────────────────────────────────────────────────────────
repair_menu() {
    echo ""
    echo "============================================="
    bold "  FlightConn — Repair Options"
    echo ""
    echo "============================================="
    echo ""
    echo "  All repair actions ask before proceeding."
    echo "  Default answer is No. No data will be deleted."
    echo ""
    echo "  1) Restart app container only"
    echo "  2) Restart app + DB containers (safe if DB is healthy)"
    echo "  3) Rebuild and restart app container (no data loss)"
    echo "  4) Show container logs"
    echo "  5) Re-run diagnostics"
    echo "  6) Run full setup (safe rerun)"
    echo "  r) Return to main menu"
    echo ""
    printf "  Enter choice: "
    read -r REPAIR_CHOICE < /dev/tty || REPAIR_CHOICE="r"

    case "$REPAIR_CHOICE" in
        1)
            if confirm_action "Restart app container (flightconn-app)?"; then
                echo ""
                docker compose restart app 2>&1 || true
                echo "  App container restarted."
                echo "  Waiting for health..."
                APP_PORT=$(grep '^APP_PORT=' "$ENV_FILE" 2>/dev/null | cut -d= -f2 | tr -d '[:space:]' || echo "8082")
                for i in $(seq 1 15); do
                    if curl -sf --max-time 4 -o /dev/null "http://localhost:${APP_PORT}/health" 2>/dev/null; then
                        echo "  App is healthy."; break
                    fi
                    sleep 2
                done
            else
                echo "  Cancelled."
            fi
            ;;
        2)
            echo ""
            yellow "  WARNING: This restarts both app and DB containers."
            echo "  Persistent data in the flightconn_mysql volume is NOT affected."
            if confirm_action "Restart app + DB containers?"; then
                echo ""
                docker compose restart db app 2>&1 || true
                echo "  Containers restarted. DB may take ~30s to become healthy."
            else
                echo "  Cancelled."
            fi
            ;;
        3)
            echo ""
            echo "  This rebuilds the app container image and restarts it."
            echo "  The database volume is NOT affected."
            if confirm_action "Rebuild and restart app container?"; then
                echo ""
                docker compose up -d --build app 2>&1 || true
                echo "  App container rebuilt and restarted."
            else
                echo "  Cancelled."
            fi
            ;;
        4)
            show_logs
            ;;
        5)
            run_diagnostics
            ;;
        6)
            echo ""
            echo "  Running ./setup.sh (safe to rerun — preserves data)."
            if confirm_action "Run ./setup.sh?"; then
                echo ""
                "$REPO_DIR/setup.sh"
            else
                echo "  Cancelled."
            fi
            ;;
        r|*)
            ;;
    esac
}

# ── Entry point ───────────────────────────────────────────────────────────────
run_diagnostics

while true; do
    echo "  Options:"
    echo "  1) View repair options"
    echo "  2) Show logs"
    echo "  3) Re-run diagnostics"
    echo "  q) Quit / return to menu"
    echo ""
    printf "  Enter choice: "
    read -r MAIN_CHOICE < /dev/tty || MAIN_CHOICE="q"
    case "$MAIN_CHOICE" in
        1) repair_menu ;;
        2) show_logs ;;
        3) run_diagnostics ;;
        q|Q|quit|exit|r) break ;;
        *) echo "  Invalid choice." ;;
    esac
    echo ""
done
