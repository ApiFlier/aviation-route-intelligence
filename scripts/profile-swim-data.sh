#!/usr/bin/env bash
#
# FlightConn Maintenance: Profile SWIM Data
#
# This script runs read-only queries against the flightconn-db container
# to summarize current SWIM ingestion health and data quality.
#

set -e

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "===================================================="
echo "  FlightConn SWIM Data Profile"
echo "===================================================="
echo ""

# Check Docker
if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker is not running or not accessible."
    exit 1
fi

# Check DB container
if ! docker ps --format '{{.Names}}' | grep -q "^flightconn-db$"; then
    echo "ERROR: flightconn-db container is not running."
    exit 1
fi

# Check Ingestor container
echo "--- System Status ---"
if docker ps --format '{{.Names}}' | grep -q "^flightconn-swim-ingestor$"; then
    echo "SWIM Ingestor:   RUNNING"
else
    echo "SWIM Ingestor:   STOPPED"
fi
echo "Database:        RUNNING (flightconn-db)"
echo ""

# Define MySQL query execution function
# Passes query to docker exec without exposing password in command history.
run_query() {
    local query="$1"
    docker exec flightconn-db bash -c "MYSQL_PWD=\$MYSQL_PASSWORD mysql -u flightconn flightconn -N -B -e \"$query\""
}

echo "--- Data Volume ---"
observed_count=$(run_query "SELECT COUNT(*) FROM observed_flights;")
enrichment_count=$(run_query "SELECT COUNT(*) FROM observed_flight_enrichment;")
rra_count=$(run_query "SELECT COUNT(*) FROM recent_route_activity;")
rrca_count=$(run_query "SELECT COUNT(*) FROM recent_route_carrier_activity;")
rhr_count=$(run_query "SELECT COUNT(*) FROM route_historical_recent_comparison;")

echo "observed_flights:                   $observed_count"
echo "observed_flight_enrichment:         $enrichment_count"
echo "recent_route_activity:              $rra_count"
echo "recent_route_carrier_activity:      $rrca_count"
echo "route_historical_recent_comparison: $rhr_count"
echo ""

echo "--- observed_flights Data Quality ---"
has_carrier=$(run_query "SELECT COUNT(*) FROM observed_flights WHERE carrier_code IS NOT NULL;")
has_origin=$(run_query "SELECT COUNT(*) FROM observed_flights WHERE origin_iata IS NOT NULL;")
has_dest=$(run_query "SELECT COUNT(*) FROM observed_flights WHERE dest_iata IS NOT NULL;")
route_ready=$(run_query "SELECT COUNT(*) FROM observed_flights WHERE origin_iata IS NOT NULL AND dest_iata IS NOT NULL AND carrier_code IS NOT NULL AND carrier_code <> 'UNK';")
has_times=$(run_query "SELECT COUNT(*) FROM observed_flights WHERE sched_dep_utc IS NOT NULL OR actual_dep_utc IS NOT NULL OR sched_arr_utc IS NOT NULL OR actual_arr_utc IS NOT NULL;")
has_aircraft=$(run_query "SELECT COUNT(*) FROM observed_flights WHERE aircraft_type IS NOT NULL;")
non_3_char_airports=$(run_query "SELECT COUNT(*) FROM observed_flights WHERE LENGTH(origin_iata) > 3 OR LENGTH(dest_iata) > 3;")
commercial_user=$(run_query "SELECT COUNT(*) FROM observed_flight_enrichment WHERE user_category = 'COMMERCIAL' OR flight_type = 'SCHEDULED';")

echo "Rows with carrier_code: $has_carrier"
echo "Rows with origin_iata:  $has_origin"
echo "Rows with dest_iata:    $has_dest"
echo "Route-ready rows:       $route_ready (has origin, dest, known carrier)"
echo "Rows with timestamps:   $has_times (sched/actual dep/arr)"
echo "Rows with aircraft:     $has_aircraft"
echo "Rows with 4-char ICAO:  $non_3_char_airports (e.g. non-US international)"
echo "Commercial indicators:  $commercial_user (from enrichment)"
echo ""

echo "--- Timeline ---"
earliest_seen=$(run_query "SELECT MIN(first_seen_at) FROM observed_flights;")
latest_updated=$(run_query "SELECT MAX(last_updated_at) FROM observed_flights;")

# Check if earliest_seen is empty or NULL string
if [[ -z "$earliest_seen" || "$earliest_seen" == "NULL" ]]; then earliest_seen="N/A"; fi
if [[ -z "$latest_updated" || "$latest_updated" == "NULL" ]]; then latest_updated="N/A"; fi

echo "Earliest first_seen_at:   $earliest_seen"
echo "Latest last_updated_at:   $latest_updated"
echo ""

echo "--- Duplicates ---"
dup_groups=$(run_query "SELECT COUNT(*) FROM (SELECT source_flight_id FROM observed_flights GROUP BY source_flight_id HAVING COUNT(*) > 1) d;")
dup_rows=$(run_query "SELECT COUNT(*) FROM observed_flights WHERE source_flight_id IN (SELECT source_flight_id FROM (SELECT source_flight_id FROM observed_flights GROUP BY source_flight_id HAVING COUNT(*) > 1) d2);")
dup_route_ready=$(run_query "SELECT COUNT(*) FROM observed_flights WHERE source_flight_id IN (SELECT source_flight_id FROM (SELECT source_flight_id FROM observed_flights GROUP BY source_flight_id HAVING COUNT(*) > 1) d2) AND origin_iata IS NOT NULL AND dest_iata IS NOT NULL AND carrier_code IS NOT NULL AND carrier_code <> 'UNK';")

echo "Duplicate source_flight_id groups: $dup_groups"
echo "Total rows in duplicate groups:    $dup_rows"
echo "Route-ready rows in duplicates:    $dup_route_ready"
echo ""

echo "--- recent_route_activity Freshness ---"
oldest_last_obs=$(run_query "SELECT MIN(last_observed_at) FROM recent_route_activity;")
newest_last_obs=$(run_query "SELECT MAX(last_observed_at) FROM recent_route_activity;")

if [[ -z "$oldest_last_obs" || "$oldest_last_obs" == "NULL" ]]; then oldest_last_obs="N/A"; fi
if [[ -z "$newest_last_obs" || "$newest_last_obs" == "NULL" ]]; then newest_last_obs="N/A"; fi

echo "Oldest last_observed_at: $oldest_last_obs"
echo "Newest last_observed_at: $newest_last_obs"
echo "Total routes active:     $rra_count"
echo ""

echo "--- Top 25 Route/Carrier Pairs ---"
printf "%-10s %-10s %-10s %-15s\n" "ORIGIN" "DEST" "CARRIER" "OBSERVATIONS"
echo "------------------------------------------------"
run_query "SELECT origin_iata, dest_iata, carrier_code, COUNT(*) as obs_count FROM observed_flights WHERE origin_iata IS NOT NULL AND dest_iata IS NOT NULL AND carrier_code IS NOT NULL AND carrier_code <> 'UNK' GROUP BY origin_iata, dest_iata, carrier_code ORDER BY obs_count DESC LIMIT 25;" | while IFS=$'\t' read -r org dst carr count; do
    if [[ -n "$org" ]]; then
        printf "%-10s %-10s %-10s %-15s\n" "$org" "$dst" "$carr" "$count"
    fi
done

echo ""
echo "Done."
