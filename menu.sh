#!/usr/bin/env bash
# menu.sh — FlightConn main entry point
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$REPO_DIR/.env"
DEPLOY_ENV="$REPO_DIR/deploy.env"

# Ensure docker compose calls find docker-compose.yml regardless of cwd
cd "$REPO_DIR"

# ── Helpers ──────────────────────────────────────────────────────────────────
bold()  { printf '\033[1m%s\033[0m' "$*"; }
green() { printf '\033[0;32m%s\033[0m' "$*"; }
yellow(){ printf '\033[0;33m%s\033[0m' "$*"; }
red()   { printf '\033[0;31m%s\033[0m' "$*"; }
reset() { printf '\033[0m'; }

# ── Status helpers ────────────────────────────────────────────────────────────
docker_ok() {
    docker info >/dev/null 2>&1
}

compose_ok() {
    docker compose version >/dev/null 2>&1
}

get_app_port() {
    if [ -f "$ENV_FILE" ]; then
        grep '^APP_PORT=' "$ENV_FILE" | cut -d= -f2 | tr -d '[:space:]'
    fi
}

container_state() {
    local name="$1"
    docker inspect --format='{{.State.Status}}' "$name" 2>/dev/null || echo "absent"
}

health_check() {
    local port="$1"
    curl -sf -o /dev/null --max-time 4 "http://localhost:${port}/health" 2>/dev/null && echo "ok" || echo "fail"
}

swim_configured() {
    if [ ! -f "$DEPLOY_ENV" ]; then
        echo "not configured"
        return
    fi
    local has_user has_pass has_queue=false
    has_user=$(grep -E '^FAA_USER=.+' "$DEPLOY_ENV" 2>/dev/null || true)
    has_pass=$(grep -E '^FAA_PASS=.+' "$DEPLOY_ENV" 2>/dev/null || true)
    for q in QUEUE_SFDPS QUEUE_STDDS QUEUE_TFMS; do
        if grep -qE "^${q}=.+" "$DEPLOY_ENV" 2>/dev/null; then
            has_queue=true
            break
        fi
    done
    if [ -n "$has_user" ] && [ -n "$has_pass" ] && [ "$has_queue" = true ]; then
        echo "configured"
    elif [ -z "$has_user" ] && [ -z "$has_pass" ]; then
        echo "not configured"
    else
        echo "incomplete"
    fi
}

# ── Status banner ─────────────────────────────────────────────────────────────
print_status() {
    echo ""
    printf "  %-24s" "Docker:"
    if docker_ok; then
        if compose_ok; then
            green "available"; echo ""
        else
            yellow "available (compose missing)"; echo ""
        fi
    else
        red "not running or not installed"; echo ""
    fi

    local APP_PORT
    APP_PORT=$(get_app_port)

    printf "  %-24s" "App container:"
    local app_state
    app_state=$(container_state "flightconn-app")
    case "$app_state" in
        running) green "running"; echo "" ;;
        absent)  red "not created"; echo "" ;;
        *)       yellow "$app_state"; echo "" ;;
    esac

    printf "  %-24s" "DB container:"
    local db_state
    db_state=$(container_state "flightconn-db")
    local db_health
    db_health=$(docker inspect --format='{{.State.Health.Status}}' "flightconn-db" 2>/dev/null || true)
    case "$db_state" in
        running)
            if [ "$db_health" = "healthy" ]; then green "running (healthy)"; echo ""
            else yellow "running ($db_health)"; echo ""
            fi ;;
        absent) red "not created"; echo "" ;;
        *)      yellow "$db_state"; echo "" ;;
    esac

    local swim_state
    swim_state=$(container_state "flightconn-swim-ingestor")
    local swim_cfg
    swim_cfg=$(swim_configured)
    printf "  %-24s" "SWIM ingestor:"
    case "$swim_cfg" in
        configured)
            case "$swim_state" in
                running) green "running"; echo "" ;;
                absent)  yellow "configured but not running — run setup"; echo "" ;;
                *)       yellow "$swim_state"; echo "" ;;
            esac
            ;;
        incomplete)
            yellow "credentials incomplete (see option 3)"; echo ""
            ;;
        *)
            printf "not configured (optional — see option 3)\n"
            ;;
    esac

    if [ -n "$APP_PORT" ]; then
        printf "  %-24s" "App URL:"
        if [ "$app_state" = "running" ]; then
            local hc
            hc=$(health_check "$APP_PORT")
            if [ "$hc" = "ok" ]; then
                printf "http://localhost:%s  " "$APP_PORT"
                green "(healthy)"; echo ""
            else
                printf "http://localhost:%s  " "$APP_PORT"
                yellow "(not responding yet)"; echo ""
            fi
        else
            printf "http://localhost:%s  " "$APP_PORT"
            yellow "(app not running)"; echo ""
        fi
    else
        printf "  %-24s%s\n" "App URL:" "unknown (run setup first)"
    fi
    echo ""
}

# ── Main menu ─────────────────────────────────────────────────────────────────
while true; do
    clear
    echo "============================================="
    bold "  FlightConn — Airline Route Intelligence"
    echo ""
    echo "============================================="
    print_status

    echo "  What would you like to do?"
    echo ""
    echo "  1) Set up the app for the first time"
    echo "  2) Update the app"
    echo "  3) Update API keys / credentials / optional data-source config"
    echo "  4) Check app status and links"
    echo "  5) Troubleshoot / repair common problems"
    echo "  6) Advanced tools"
    echo "  7) Quit"
    echo ""
    printf "  Enter choice [1-7]: "
    read -r CHOICE < /dev/tty || { echo ""; break; }

    case "$CHOICE" in
        1)
            echo ""
            echo "--- Running setup.sh ---"
            "$REPO_DIR/setup.sh"
            echo ""
            printf "Press Enter to return to the menu..."
            read -r _ < /dev/tty || true
            ;;
        2)
            echo ""
            echo "--- Running update.sh ---"
            "$REPO_DIR/update.sh"
            echo ""
            printf "Press Enter to return to the menu..."
            read -r _ < /dev/tty || true
            ;;
        3)
            echo ""
            echo "--- Running configure-credentials.sh ---"
            "$REPO_DIR/scripts/configure-credentials.sh"
            echo ""
            printf "Press Enter to return to the menu..."
            read -r _ < /dev/tty || true
            ;;
        4)
            echo ""
            echo "============================================="
            bold "  FlightConn — App Status & Links"
            echo ""
            echo "============================================="
            print_status
            local_port=$(get_app_port)
            if [ -n "$local_port" ]; then
                echo "  Pages:"
                echo "    Route Intelligence Map:  http://localhost:${local_port}/"
                echo "    Route Opportunity Finder: http://localhost:${local_port}/opportunities/"
                echo "    Airline Health:          http://localhost:${local_port}/airline-health/"
                echo "    System Health:           http://localhost:${local_port}/recent-activity-health/"
                echo "    API:                     http://localhost:${local_port}/api"
                echo "    Health endpoint:         http://localhost:${local_port}/health"
            fi
            echo ""
            echo "  Container state:"
            docker compose ps 2>/dev/null || echo "  (docker compose not available)"
            echo ""
            printf "Press Enter to return to the menu..."
            read -r _ < /dev/tty || true
            ;;
        5)
            echo ""
            echo "--- Running troubleshoot.sh ---"
            "$REPO_DIR/scripts/troubleshoot.sh"
            echo ""
            printf "Press Enter to return to the menu..."
            read -r _ < /dev/tty || true
            ;;
        6)
            echo ""
            echo "============================================="
            bold "  FlightConn — Advanced Tools"
            echo ""
            echo "============================================="
            echo ""
            echo "  a) Refresh baseline database backup (backup.sh)"
            echo "  b) Profile SWIM data (scripts/profile-swim-data.sh)"
            echo "  c) Clean up SWIM duplicate records (scripts/cleanup-swim-duplicates.sh)"
            echo "  d) Prune Docker build cache and dangling images"
            echo "  r) Return to main menu"
            echo ""
            printf "  Enter choice: "
            read -r ADV_CHOICE < /dev/tty || ADV_CHOICE="r"
            case "$ADV_CHOICE" in
                a)
                    echo ""
                    "$REPO_DIR/backup.sh"
                    ;;
                b)
                    echo ""
                    "$REPO_DIR/scripts/profile-swim-data.sh"
                    ;;
                c)
                    echo ""
                    "$REPO_DIR/scripts/cleanup-swim-duplicates.sh"
                    ;;
                d)
                    echo ""
                    echo "Pruning Docker build cache and dangling images..."
                    docker builder prune -af --filter "until=24h" || true
                    docker image prune -f || true
                    echo "Done."
                    ;;
                r|*)
                    ;;
            esac
            echo ""
            printf "Press Enter to return to the menu..."
            read -r _ < /dev/tty || true
            ;;
        7|q|Q|quit|exit)
            echo ""
            echo "Goodbye."
            exit 0
            ;;
        *)
            echo ""
            echo "  Invalid choice. Please enter 1-7."
            sleep 1
            ;;
    esac
done
