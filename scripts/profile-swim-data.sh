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
echo ""

echo "--- Enrichment Quality & Commercial Classification ---"
comm_cand=$(run_query "SELECT COUNT(*) FROM observed_flights f JOIN observed_flight_enrichment e ON f.source_flight_id = e.source_flight_id WHERE f.origin_iata IS NOT NULL AND f.dest_iata IS NOT NULL AND f.callsign NOT REGEXP '^N[1-9][0-9]{0,4}[A-Z]{0,2}$' AND (f.carrier_code NOT IN ('XXX', 'UNK', 'UNKNOWN', '') OR e.operating_carrier_code NOT IN ('XXX', 'UNK', 'UNKNOWN', '')) AND (e.user_category = 'COMMERCIAL' OR e.flight_type = 'SCHEDULED' OR (e.aircraft_category = 'JET' AND f.carrier_code IS NOT NULL) OR (f.carrier_code IS NOT NULL AND f.carrier_code NOT IN ('XXX', 'UNK', 'UNKNOWN', '')));")

ga_private=$(run_query "SELECT COUNT(*) FROM observed_flight_enrichment e LEFT JOIN observed_flights f ON e.source_flight_id = f.source_flight_id WHERE e.user_category = 'GENERAL AVIATION' OR f.callsign REGEXP '^N[1-9][0-9]{0,4}[A-Z]{0,2}$';")

unk_carrier=$(run_query "SELECT COUNT(*) FROM observed_flights f LEFT JOIN observed_flight_enrichment e ON f.source_flight_id = e.source_flight_id WHERE (f.carrier_code IS NULL OR f.carrier_code IN ('XXX', 'UNK', 'UNKNOWN', '')) AND (e.operating_carrier_code IS NULL OR e.operating_carrier_code IN ('XXX', 'UNK', 'UNKNOWN', ''));")

route_ident=$(run_query "SELECT COUNT(*) FROM observed_flight_enrichment WHERE route_of_flight IS NOT NULL OR departure_procedure IS NOT NULL OR arrival_procedure IS NOT NULL;")

op_non_comm=$(run_query "SELECT COUNT(*) FROM observed_flight_enrichment WHERE operating_carrier_code IS NOT NULL AND user_category != 'COMMERCIAL' AND flight_type != 'SCHEDULED' AND user_category != 'GENERAL AVIATION';")

echo "Commercial route candidates: $comm_cand"
echo "GA/private indicators:       $ga_private"
echo "Unknown/XXX carrier rows:    $unk_carrier"
echo "Rows with route strings:     $route_ident"
echo "Op carrier but non-comm/GA:  $op_non_comm"
echo ""

echo "--- observed_flight_enrichment Data Quality ---"
has_op_carrier=$(run_query "SELECT COUNT(*) FROM observed_flight_enrichment WHERE operating_carrier_code IS NOT NULL;")
has_maj_carrier=$(run_query "SELECT COUNT(*) FROM observed_flight_enrichment WHERE major_carrier_code IS NOT NULL;")
has_flt_type=$(run_query "SELECT COUNT(*) FROM observed_flight_enrichment WHERE flight_type IS NOT NULL;")
has_user_cat=$(run_query "SELECT COUNT(*) FROM observed_flight_enrichment WHERE user_category IS NOT NULL;")
has_ac_cat=$(run_query "SELECT COUNT(*) FROM observed_flight_enrichment WHERE aircraft_category IS NOT NULL;")
has_route=$(run_query "SELECT COUNT(*) FROM observed_flight_enrichment WHERE route_of_flight IS NOT NULL;")
has_amended=$(run_query "SELECT COUNT(*) FROM observed_flight_enrichment WHERE route_amended = 1;")

echo "Rows with operating_carrier: $has_op_carrier"
echo "Rows with major_carrier:     $has_maj_carrier"
echo "Rows with flight_type:       $has_flt_type"
echo "Rows with user_category:     $has_user_cat"
echo "Rows with aircraft_category: $has_ac_cat"
echo "Rows with route_of_flight:   $has_route"
echo "Rows with route_amended=1:   $has_amended"
echo ""

echo "--- Timeline & Freshness ---"
earliest_seen=$(run_query "SELECT MIN(first_seen_at) FROM observed_flights;")
latest_updated=$(run_query "SELECT MAX(last_updated_at) FROM observed_flights;")
mins_since_update=$(run_query "SELECT TIMESTAMPDIFF(MINUTE, MAX(last_updated_at), UTC_TIMESTAMP()) FROM observed_flights;")

# Check if earliest_seen is empty or NULL string
if [[ -z "$earliest_seen" || "$earliest_seen" == "NULL" ]]; then earliest_seen="N/A"; fi
if [[ -z "$latest_updated" || "$latest_updated" == "NULL" ]]; then latest_updated="N/A"; fi
if [[ -z "$mins_since_update" || "$mins_since_update" == "NULL" ]]; then mins_since_update="N/A"; fi

echo "Earliest first_seen_at:   $earliest_seen"
echo "Latest last_updated_at:   $latest_updated"
echo "Mins since latest update: $mins_since_update"
echo ""

echo "--- Duplicate Trend / Upsert Health ---"
dup_groups=$(run_query "SELECT COUNT(*) FROM (SELECT source_flight_id FROM observed_flights GROUP BY source_flight_id HAVING COUNT(*) > 1) d;")
dup_rows=$(run_query "SELECT COUNT(*) FROM observed_flights WHERE source_flight_id IN (SELECT source_flight_id FROM (SELECT source_flight_id FROM observed_flights GROUP BY source_flight_id HAVING COUNT(*) > 1) d2);")
dup_route_ready=$(run_query "SELECT COUNT(*) FROM observed_flights WHERE source_flight_id IN (SELECT source_flight_id FROM (SELECT source_flight_id FROM observed_flights GROUP BY source_flight_id HAVING COUNT(*) > 1) d2) AND origin_iata IS NOT NULL AND dest_iata IS NOT NULL AND carrier_code IS NOT NULL AND carrier_code <> 'UNK';")

new_dups_15m=$(run_query "SELECT COUNT(*) FROM (SELECT source_flight_id FROM observed_flights GROUP BY source_flight_id HAVING COUNT(*) > 1 AND MIN(first_seen_at) > DATE_SUB(UTC_TIMESTAMP(), INTERVAL 15 MINUTE)) d;")
new_dups_1h=$(run_query "SELECT COUNT(*) FROM (SELECT source_flight_id FROM observed_flights GROUP BY source_flight_id HAVING COUNT(*) > 1 AND MIN(first_seen_at) > DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 HOUR)) d;")
updated_dups_15m=$(run_query "SELECT COUNT(*) FROM (SELECT source_flight_id FROM observed_flights GROUP BY source_flight_id HAVING COUNT(*) > 1 AND MAX(last_updated_at) > DATE_SUB(UTC_TIMESTAMP(), INTERVAL 15 MINUTE)) d;")
updated_dups_1h=$(run_query "SELECT COUNT(*) FROM (SELECT source_flight_id FROM observed_flights GROUP BY source_flight_id HAVING COUNT(*) > 1 AND MAX(last_updated_at) > DATE_SUB(UTC_TIMESTAMP(), INTERVAL 1 HOUR)) d;")

newest_dup_first_seen=$(run_query "SELECT MAX(first_seen_at) FROM (SELECT MIN(first_seen_at) as first_seen_at FROM observed_flights GROUP BY source_flight_id HAVING COUNT(*) > 1) d;")
newest_dup_last_updated=$(run_query "SELECT MAX(last_updated_at) FROM (SELECT MAX(last_updated_at) as last_updated_at FROM observed_flights GROUP BY source_flight_id HAVING COUNT(*) > 1) d;")

if [[ -z "$newest_dup_first_seen" || "$newest_dup_first_seen" == "NULL" ]]; then newest_dup_first_seen="N/A"; fi
if [[ -z "$newest_dup_last_updated" || "$newest_dup_last_updated" == "NULL" ]]; then newest_dup_last_updated="N/A"; fi

echo "Duplicate source_flight_id groups:   $dup_groups"
echo "Total rows in duplicate groups:      $dup_rows"
echo "Route-ready rows in duplicates:      $dup_route_ready"
echo "Groups with first_seen_at < 15m:     $new_dups_15m"
echo "Groups with first_seen_at < 1h:      $new_dups_1h"
echo "Groups with last_updated_at < 15m:   $updated_dups_15m"
echo "Groups with last_updated_at < 1h:    $updated_dups_1h"
echo "Newest first_seen_at in duplicates:  $newest_dup_first_seen"
echo "Newest last_updated_at in dups:      $newest_dup_last_updated"
echo ""

echo "Top 10 Duplicate Groups (by row count):"
printf "%-38s %-6s %-12s %-10s %-10s %-10s %-20s %-20s\n" "SOURCE_FLIGHT_ID" "ROWS" "CALLSIGNS" "CARRIERS" "ORIGINS" "DESTS" "MIN_FIRST_SEEN" "MAX_LAST_UPDATED"
echo "--------------------------------------------------------------------------------------------------------------------------------------------"
run_query "SELECT source_flight_id, COUNT(*) as c, SUBSTRING(GROUP_CONCAT(DISTINCT callsign), 1, 12), SUBSTRING(GROUP_CONCAT(DISTINCT carrier_code), 1, 10), SUBSTRING(GROUP_CONCAT(DISTINCT origin_iata), 1, 10), SUBSTRING(GROUP_CONCAT(DISTINCT dest_iata), 1, 10), MIN(first_seen_at), MAX(last_updated_at) FROM observed_flights GROUP BY source_flight_id HAVING c > 1 ORDER BY c DESC LIMIT 10;" | while IFS=$'\t' read -r gufi count calls carr org dst first last; do
    if [[ -n "$gufi" ]]; then
        printf "%-38s %-6s %-12s %-10s %-10s %-10s %-20s %-20s\n" "$gufi" "$count" "$calls" "$carr" "$org" "$dst" "$first" "$last"
    fi
done
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
echo "--- Pattern & Streak Memory ---"
rollup_count=$(run_query "SELECT COUNT(*) FROM recent_carrier_weekly_rollup;")
pattern_count=$(run_query "SELECT COUNT(*) FROM recent_carrier_patterns;")

recently_obs=$(run_query "SELECT COUNT(*) FROM recent_carrier_patterns WHERE status = 'active' AND consecutive_weeks_seen < 2 AND id NOT IN (SELECT p.id FROM recent_carrier_patterns p JOIN recent_carrier_weekly_rollup r ON p.origin_iata=r.origin_iata AND p.dest_iata=r.dest_iata AND p.carrier_code=r.carrier_code AND p.day_of_week=r.day_of_week AND p.time_window=r.time_window WHERE r.observation_count >= 2 AND r.week_start_date >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 30 DAY));")

early_signal=$(run_query "SELECT COUNT(DISTINCT p.id) FROM recent_carrier_patterns p JOIN recent_carrier_weekly_rollup r ON p.origin_iata=r.origin_iata AND p.dest_iata=r.dest_iata AND p.carrier_code=r.carrier_code AND p.day_of_week=r.day_of_week AND p.time_window=r.time_window WHERE p.status = 'active' AND p.consecutive_weeks_seen < 2 AND r.observation_count >= 2 AND r.week_start_date >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 30 DAY);")

early_pattern=$(run_query "SELECT COUNT(*) FROM recent_carrier_patterns WHERE status = 'active' AND consecutive_weeks_seen >= 2 AND consecutive_weeks_seen < 4;")
consistent_pattern=$(run_query "SELECT COUNT(*) FROM recent_carrier_patterns WHERE status = 'active' AND consecutive_weeks_seen >= 4;")

watch_patterns=$(run_query "SELECT COUNT(*) FROM recent_carrier_patterns WHERE status = 'watch';")
stale_patterns=$(run_query "SELECT COUNT(*) FROM recent_carrier_patterns WHERE status = 'stale';")
inactive_patterns=$(run_query "SELECT COUNT(*) FROM recent_carrier_patterns WHERE status = 'inactive';")

streak_max=$(run_query "SELECT MAX(consecutive_weeks_seen) FROM recent_carrier_patterns WHERE status = 'active';")

promoted_recurring=$(run_query "SELECT COUNT(*) FROM recent_carrier_patterns WHERE status = 'active' AND consecutive_weeks_seen >= 2;")
eligible_signals=$(run_query "SELECT COUNT(DISTINCT p.id) FROM recent_carrier_patterns p LEFT JOIN recent_carrier_weekly_rollup r ON p.origin_iata=r.origin_iata AND p.dest_iata=r.dest_iata AND p.carrier_code=r.carrier_code AND p.day_of_week=r.day_of_week AND p.time_window=r.time_window WHERE p.status = 'active' AND (p.consecutive_weeks_seen >= 2 OR (r.observation_count >= 2 AND r.week_start_date >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 30 DAY)));")

echo "Weekly rollups stored:       $rollup_count"
echo "Total pattern candidates:    $pattern_count"
echo "Recently observed only:      $recently_obs"
echo "Early signals (multi-obs):   $early_signal"
echo "Early recent patterns (2w+): $early_pattern"
echo "Consistent patterns (4w+):   $consistent_pattern"
echo "Watch patterns (missed 1w):  $watch_patterns"
echo "Stale patterns (missed 2w):  $stale_patterns"
echo "Inactive patterns (missed 3+): $inactive_patterns"
echo "UI-promoted recurring:       $promoted_recurring"
echo "Selected-route eligible:      $eligible_signals"
echo "Longest active streak:       ${streak_max:-0} weeks"
echo ""

echo "Done."
