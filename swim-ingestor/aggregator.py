"""
FlightConn SWIM Ingestor — Aggregator
Aggregates route-ready flight observations into recent activity tables.

Consumption Rules for App APIs:
- Default to filtering by:
    activity_classification IN ('historical_commercial_match', 'recent_commercial_candidate')
- Avoid prominent display of:
    activity_classification IN ('unknown_or_noncommercial', 'insufficient_data')
- Use 'recent_activity_only' as a secondary signal for non-traditional routes.
"""

import logging
import json
from datetime import datetime, timezone, timedelta
import pytz
from timezonefinder import TimezoneFinder
from db import get_connection

log = logging.getLogger('swim-ingestor.aggregator')
tf = TimezoneFinder()

_tz_cache = {}

def _get_tz_name(iata, lat, lon):
    if iata in _tz_cache:
        return _tz_cache[iata]
    if lat and lon:
        try:
            tz_name = tf.timezone_at(lng=float(lon), lat=float(lat))
            if tz_name:
                _tz_cache[iata] = tz_name
                return tz_name
        except Exception:
            pass
    return "UTC"

def _execute_update(conn, sql: str, params: tuple = None) -> int:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.rowcount

def aggregate_recent_route_activity(lookback_days: int = 30) -> int:
    """
    Roll up observations into recent_route_activity.
    Only route-ready flights are considered.
    """
    log.info("Aggregating recent_route_activity (lookback: %d days)", lookback_days)
    conn = get_connection()
    updated_rows = 0
    try:
        # Step 1: Compute aggregates from observed_flights
        with conn.cursor() as cur:
            # Join with historical routes and airports to determine classification
            # Using COUNT(DISTINCT o.source_flight_id) ensures that multiple observations
            # of the same flight (e.g. from different SWIM queues) are only counted once.
            sql_agg = """
                SELECT 
                    o.origin_iata,
                    o.dest_iata,
                    MIN(DATE(o.first_seen_at)) as coverage_start_date,
                    MAX(DATE(o.last_updated_at)) as coverage_end_date,
                    COUNT(DISTINCT DATE(o.first_seen_at)) as coverage_days,
                    COUNT(DISTINCT o.source_flight_id) as observation_count,
                    COUNT(DISTINCT o.carrier_code) as carrier_count_observed,
                    MAX(o.last_updated_at) as last_observed_at,
                    IF(rt.id IS NOT NULL, 1, 0) as has_historical_route,
                    IF(a1.iata IS NOT NULL AND a2.iata IS NOT NULL, 1, 0) as has_known_airports
                FROM observed_flights o
                LEFT JOIN routes rt ON rt.origin = o.origin_iata AND rt.dest = o.dest_iata
                LEFT JOIN airports a1 ON a1.iata = o.origin_iata
                LEFT JOIN airports a2 ON a2.iata = o.dest_iata
                WHERE o.origin_iata IS NOT NULL 
                  AND o.dest_iata IS NOT NULL
                  AND LENGTH(o.origin_iata) = 3
                  AND LENGTH(o.dest_iata) = 3
                  AND o.source_flight_id IS NOT NULL
                  AND o.carrier_code NOT IN ('', 'UNK', 'UNKNOWN')
                  AND o.first_seen_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s DAY)
                GROUP BY o.origin_iata, o.dest_iata
            """
            cur.execute(sql_agg, (lookback_days,))
            results = cur.fetchall()

        # Step 2: Calculate display mode, classification, and confidence, then upsert
        for row in results:
            origin_iata, dest_iata, start_date, end_date, coverage_days, obs_count, carrier_count, last_obs, has_historical, has_airports = row
            
            # Confidence rules for display
            if obs_count == 0:
                confidence = 'none'
            elif coverage_days < 7 or obs_count < 3:
                confidence = 'low'
            elif coverage_days >= 21 and obs_count >= 10:
                confidence = 'high'
            else:
                confidence = 'medium'
                
            # Route Classification Logic
            if has_historical:
                classification = 'historical_commercial_match'
                commercial_confidence = 'high' if obs_count >= 3 else 'medium'
            elif has_airports:
                if obs_count >= 5:
                    classification = 'recent_commercial_candidate'
                    commercial_confidence = 'medium'
                else:
                    classification = 'recent_activity_only'
                    commercial_confidence = 'low'
            else:
                classification = 'unknown_or_noncommercial'
                commercial_confidence = 'none'
                
            if obs_count < 2:
                classification = 'insufficient_data'

            classification_note = "App APIs should filter for historical_commercial_match or recent_commercial_candidate."

            # Display mode rules
            if obs_count == 0:
                display_mode = 'historical_only'
            elif coverage_days < 7:
                display_mode = 'early_recent_signal'
            elif coverage_days >= 21 and obs_count >= 10:
                display_mode = 'recent_activity_primary'
            else:
                display_mode = 'blend_recent_and_historical'

            # Get carriers for this route
            with conn.cursor() as cur_c:
                cur_c.execute("""
                    SELECT DISTINCT carrier_code FROM observed_flights 
                    WHERE origin_iata = %s AND dest_iata = %s AND carrier_code IS NOT NULL
                """, (origin_iata, dest_iata))
                carriers = json.dumps([r[0] for r in cur_c.fetchall()])

            # Get the last observed carrier for this route
            with conn.cursor() as cur_last:
                cur_last.execute("""
                    SELECT carrier_code FROM observed_flights 
                    WHERE origin_iata = %s AND dest_iata = %s AND carrier_code IS NOT NULL
                    ORDER BY last_updated_at DESC LIMIT 1
                """, (origin_iata, dest_iata))
                last_carrier_res = cur_last.fetchone()
                last_carrier = last_carrier_res[0] if last_carrier_res else None

            upsert_sql = """
                INSERT INTO recent_route_activity (
                    origin_iata, dest_iata, coverage_start_date, coverage_end_date, 
                    coverage_days, observation_count, observed_carriers, carrier_count_observed,
                    last_observed_at, last_observed_carrier, display_mode, confidence, 
                    activity_classification, commercial_confidence, classification_note, computed_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP()
                ) ON DUPLICATE KEY UPDATE
                    coverage_start_date = VALUES(coverage_start_date),
                    coverage_end_date = VALUES(coverage_end_date),
                    coverage_days = VALUES(coverage_days),
                    observation_count = VALUES(observation_count),
                    observed_carriers = VALUES(observed_carriers),
                    carrier_count_observed = VALUES(carrier_count_observed),
                    last_observed_at = VALUES(last_observed_at),
                    last_observed_carrier = VALUES(last_observed_carrier),
                    display_mode = VALUES(display_mode),
                    confidence = VALUES(confidence),
                    activity_classification = VALUES(activity_classification),
                    commercial_confidence = VALUES(commercial_confidence),
                    classification_note = VALUES(classification_note),
                    computed_at = UTC_TIMESTAMP()
            """
            with conn.cursor() as cur_upsert:
                cur_upsert.execute(upsert_sql, (
                    origin_iata, dest_iata, start_date, end_date, coverage_days, obs_count,
                    carriers, carrier_count, last_obs, last_carrier, display_mode, confidence,
                    classification, commercial_confidence, classification_note
                ))
            updated_rows += 1
            
        conn.commit()
    except Exception as e:
        conn.rollback()
        log.error("Failed to aggregate recent route activity: %s", e)
    finally:
        conn.close()
    return updated_rows

def aggregate_recent_route_carrier_activity(lookback_days: int = 30) -> int:
    """
    Roll up observations into recent_route_carrier_activity.
    Only route-ready flights with carriers are considered.
    """
    log.info("Aggregating recent_route_carrier_activity (lookback: %d days)", lookback_days)
    conn = get_connection()
    updated_rows = 0
    try:
        with conn.cursor() as cur:
            # Using COUNT(DISTINCT o.source_flight_id) ensures that multiple observations
            # of the same flight (e.g. from different SWIM queues) are only counted once.
            sql_agg = """
                SELECT 
                    o.origin_iata,
                    o.dest_iata,
                    o.carrier_code,
                    MIN(DATE(o.first_seen_at)) as coverage_start_date,
                    MAX(DATE(o.last_updated_at)) as coverage_end_date,
                    COUNT(DISTINCT DATE(o.first_seen_at)) as coverage_days,
                    COUNT(DISTINCT o.source_flight_id) as observation_count,
                    MAX(o.last_updated_at) as last_observed_at,
                    IF(rt.id IS NOT NULL, 1, 0) as has_historical_route,
                    IF(c.code IS NOT NULL, 1, 0) as has_known_carrier,
                    IF(hc.id IS NOT NULL, 1, 0) as carrier_on_route_historically
                FROM observed_flights o
                LEFT JOIN routes rt ON rt.origin = o.origin_iata AND rt.dest = o.dest_iata
                LEFT JOIN carriers c ON c.code = o.carrier_code
                LEFT JOIN route_carriers hc ON hc.route_id = rt.id AND hc.carrier_code = o.carrier_code
                WHERE o.origin_iata IS NOT NULL 
                  AND o.dest_iata IS NOT NULL
                  AND LENGTH(o.origin_iata) = 3
                  AND LENGTH(o.dest_iata) = 3
                  AND o.source_flight_id IS NOT NULL
                  AND o.carrier_code NOT IN ('', 'UNK', 'UNKNOWN')
                  AND o.first_seen_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s DAY)
                GROUP BY o.origin_iata, o.dest_iata, o.carrier_code
            """
            cur.execute(sql_agg, (lookback_days,))
            results = cur.fetchall()

        for row in results:
            origin_iata, dest_iata, carrier_code, start_date, end_date, coverage_days, obs_count, last_obs, has_historical, has_known_carrier, carrier_historical = row
            
            # Route Carrier Classification Logic
            if carrier_historical:
                classification = 'historical_commercial_match'
                commercial_confidence = 'high' if obs_count >= 3 else 'medium'
            elif has_known_carrier and has_historical:
                if obs_count >= 5:
                    classification = 'recent_commercial_candidate'
                    commercial_confidence = 'medium'
                else:
                    classification = 'recent_activity_only'
                    commercial_confidence = 'low'
            elif has_known_carrier:
                classification = 'recent_activity_only'
                commercial_confidence = 'low'
            else:
                classification = 'unknown_or_noncommercial'
                commercial_confidence = 'none'

            if obs_count < 2:
                classification = 'insufficient_data'

            # Get common days for this carrier/route
            with conn.cursor() as cur_days:
                cur_days.execute("""
                    SELECT WEEKDAY(sched_dep_utc) as dow, COUNT(*) as c
                    FROM observed_flights
                    WHERE origin_iata = %s AND dest_iata = %s AND carrier_code = %s
                      AND sched_dep_utc >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s DAY)
                    GROUP BY dow
                """, (origin_iata, dest_iata, carrier_code, lookback_days))
                days_res = cur_days.fetchall()
                day_map = {["Mon","Tue","Wed","Thu","Fri","Sat","Sun"][r[0]]: r[1] for r in days_res}
                common_days_json = json.dumps(day_map)

            # Get common windows for this carrier/route
            with conn.cursor() as cur_win:
                cur_win.execute("""
                    SELECT 
                        LPAD(HOUR(DATE_ADD(COALESCE(o.actual_dep_utc, o.sched_dep_utc), INTERVAL 30 MINUTE)), 2, '0') as `window`,
                        COUNT(*) as c
                    FROM observed_flights o
                    WHERE o.origin_iata = %s AND o.dest_iata = %s AND o.carrier_code = %s
                      AND COALESCE(o.actual_dep_utc, o.sched_dep_utc) >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s DAY)
                    GROUP BY `window`
                    ORDER BY c DESC
                """, (origin_iata, dest_iata, carrier_code, lookback_days))
                win_res = cur_win.fetchall()
                win_list = [{"window": r[0], "count": r[1]} for r in win_res]
                common_windows_json = json.dumps(win_list)

            upsert_sql = """
                INSERT INTO recent_route_carrier_activity (
                    origin_iata, dest_iata, carrier_code, coverage_start_date, coverage_end_date, 
                    coverage_days, observation_count, last_observed_at, 
                    activity_classification, commercial_confidence, 
                    common_dep_days, common_dep_windows, computed_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP()
                ) ON DUPLICATE KEY UPDATE
                    coverage_start_date = VALUES(coverage_start_date),
                    coverage_end_date = VALUES(coverage_end_date),
                    coverage_days = VALUES(coverage_days),
                    observation_count = VALUES(observation_count),
                    last_observed_at = VALUES(last_observed_at),
                    activity_classification = VALUES(activity_classification),
                    commercial_confidence = VALUES(commercial_confidence),
                    common_dep_days = VALUES(common_dep_days),
                    common_dep_windows = VALUES(common_dep_windows),
                    computed_at = UTC_TIMESTAMP()
            """
            with conn.cursor() as cur_upsert:
                cur_upsert.execute(upsert_sql, (
                    origin_iata, dest_iata, carrier_code, start_date, end_date, 
                    coverage_days, obs_count, last_obs, classification, commercial_confidence,
                    common_days_json, common_windows_json
                ))
            updated_rows += 1
            
        conn.commit()
    except Exception as e:
        conn.rollback()
        log.error("Failed to aggregate recent route carrier activity: %s", e)
    finally:
        conn.close()
    return updated_rows

def aggregate_historical_recent_comparison(lookback_days: int = 30) -> int:
    """
    Compare historical route_carriers against recent_route_carrier_activity.
    """
    log.info("Aggregating route_historical_recent_comparison")
    conn = get_connection()
    updated_rows = 0
    try:
        with conn.cursor() as cur:
            # Full outer join equivalent using UNION
            # We want all historical carriers and all recent carriers for active routes
            sql = """
                SELECT 
                    COALESCE(rt.origin, rec.origin_iata) as origin_iata,
                    COALESCE(rt.dest, rec.dest_iata) as dest_iata,
                    COALESCE(h.carrier_code, rec.carrier_code) as carrier_code,
                    IF(h.carrier_code IS NOT NULL, 1, 0) as in_historical_data,
                    IF(rec.carrier_code IS NOT NULL, 1, 0) as in_recent_activity,
                    h.passengers as historical_passengers,
                    COALESCE(rec.observation_count, 0) as recent_observation_count,
                    rec.coverage_days
                FROM route_carriers h
                JOIN routes rt ON h.route_id = rt.id
                LEFT JOIN recent_route_carrier_activity rec 
                  ON rt.origin = rec.origin_iata 
                 AND rt.dest = rec.dest_iata 
                 AND h.carrier_code = rec.carrier_code
                 AND rec.activity_classification NOT IN ('unknown_or_noncommercial', 'insufficient_data')
                
                UNION
                
                SELECT 
                    rec.origin_iata,
                    rec.dest_iata,
                    rec.carrier_code,
                    IF(h.carrier_code IS NOT NULL, 1, 0) as in_historical_data,
                    1 as in_recent_activity,
                    h.passengers as historical_passengers,
                    rec.observation_count as recent_observation_count,
                    rec.coverage_days
                FROM recent_route_carrier_activity rec
                LEFT JOIN routes rt ON rt.origin = rec.origin_iata AND rt.dest = rec.dest_iata
                LEFT JOIN route_carriers h 
                  ON h.route_id = rt.id 
                 AND h.carrier_code = rec.carrier_code
                WHERE rec.activity_classification NOT IN ('unknown_or_noncommercial', 'insufficient_data')
            """
            cur.execute(sql)
            results = cur.fetchall()

        for row in results:
            origin_iata, dest_iata, carrier_code, in_hist, in_recent, hist_pax, obs_count, cov_days = row
            
            mismatch_flag = False
            mismatch_note = None
            
            if in_hist and not in_recent and cov_days and cov_days >= 7:
                # We have enough recent data on the route to expect the carrier, but didn't see them
                mismatch_flag = True
                mismatch_note = "Carrier appears in historical route data but has not been observed recently during the current lookback window. This may reflect a schedule change, seasonal service, incomplete recent data, or carrier-code differences."

            upsert_sql = """
                INSERT INTO route_historical_recent_comparison (
                    origin_iata, dest_iata, carrier_code, in_historical_data, in_recent_activity,
                    historical_passengers, recent_observation_count, mismatch_flag, mismatch_note, computed_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP()
                ) ON DUPLICATE KEY UPDATE
                    in_historical_data = VALUES(in_historical_data),
                    in_recent_activity = VALUES(in_recent_activity),
                    historical_passengers = VALUES(historical_passengers),
                    recent_observation_count = VALUES(recent_observation_count),
                    mismatch_flag = VALUES(mismatch_flag),
                    mismatch_note = VALUES(mismatch_note),
                    computed_at = UTC_TIMESTAMP()
            """
            with conn.cursor() as cur_upsert:
                cur_upsert.execute(upsert_sql, (
                    origin_iata, dest_iata, carrier_code, in_hist, in_recent, hist_pax,
                    obs_count, mismatch_flag, mismatch_note
                ))
            updated_rows += 1
            
        conn.commit()
    except Exception as e:
        conn.rollback()
        log.error("Failed to aggregate route historical comparison: %s", e)
    finally:
        conn.close()
    return updated_rows

def aggregate_carrier_weekly_rollups() -> int:
    """
    Roll up observed_flights into weekly buckets (carrier, route, day, window, week).
    Uses 1-hour time windows centered at the hour.
    Groups by ORIGIN-LOCAL weekday and hour.
    """
    log.info("Aggregating carrier_weekly_rollups (local-time aware)")
    conn = get_connection()
    try:
        # Fetch airport coordinates for timezone lookups
        with conn.cursor() as cur:
            cur.execute("SELECT iata, lat, lon FROM airports WHERE lat IS NOT NULL AND lon IS NOT NULL")
            airports = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

        # Fetch candidate flights from the last 14 days
        # We need to process them in Python to do the timezone conversion
        with conn.cursor() as cur:
            cur.execute("""
                SELECT o.origin_iata, o.dest_iata, o.carrier_code, o.source_flight_id,
                       COALESCE(o.actual_dep_utc, o.sched_dep_utc) as dep_utc
                FROM observed_flights o
                LEFT JOIN observed_flight_enrichment e ON o.source_flight_id = e.source_flight_id
                WHERE o.origin_iata IS NOT NULL AND o.dest_iata IS NOT NULL 
                  AND o.carrier_code IS NOT NULL AND o.carrier_code NOT IN ('', 'XXX', 'UNK', 'UNKNOWN', 'UNKN')
                  AND (o.actual_dep_utc IS NOT NULL OR o.sched_dep_utc IS NOT NULL)
                  AND o.callsign NOT REGEXP '^N[1-9][0-9]{0,4}[A-Z]{0,2}$'
                  AND (e.user_category = 'COMMERCIAL' OR e.flight_type = 'SCHEDULED' OR e.operating_carrier_code IS NOT NULL)
                  AND COALESCE(o.actual_dep_utc, o.sched_dep_utc) >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 14 DAY)
            """)
            flights = cur.fetchall()

        rollups = {} # (origin, dest, carrier, dow, window, week_start) -> count

        for f in flights:
            origin, dest, carrier, fid, dep_utc = f
            if not dep_utc: continue
            
            # Local time conversion
            lat_lon = airports.get(origin)
            tz_name = _get_tz_name(origin, *lat_lon) if lat_lon else "UTC"
            try:
                tz = pytz.timezone(tz_name)
            except Exception:
                tz = pytz.UTC
            
            # dep_utc is a naive datetime from MySQL (assumed UTC)
            if dep_utc.tzinfo is None:
                dep_utc = dep_utc.replace(tzinfo=pytz.UTC)
            
            dep_local = dep_utc.astimezone(tz)
            
            # Hour-centered bucket (local)
            bucket_dt = dep_local + timedelta(minutes=30)
            window = f"{bucket_dt.hour:02d}"
            
            # Monday = 0
            dow = dep_local.weekday()
            
            # Monday of the week (local)
            week_start = (dep_local - timedelta(days=dow)).date()
            
            key = (origin, dest, carrier, dow, window, week_start)
            rollups[key] = rollups.get(key, 0) + 1

        # Upsert into database
        count = 0
        with conn.cursor() as cur_upd:
            for key, obs_count in rollups.items():
                origin, dest, carrier, dow, window, week_start = key
                cur_upd.execute("""
                    INSERT INTO recent_carrier_weekly_rollup (
                        origin_iata, dest_iata, carrier_code, day_of_week, time_window, week_start_date, observation_count
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE observation_count = VALUES(observation_count)
                """, (origin, dest, carrier, dow, window, week_start, obs_count))
                count += 1
        
        conn.commit()
        return count
    except Exception as e:
        conn.rollback()
        log.error("Failed to aggregate weekly rollups: %s", e)
        return 0
    finally:
        conn.close()

def cleanup_pattern_memory(rollup_retention_weeks: int = 52, pattern_stale_weeks: int = 8) -> dict:
    """
    Bound the growth of pattern memory tables.
    """
    log.info("Cleaning up pattern memory (rollups: %d weeks, stale patterns: %d weeks)", 
             rollup_retention_weeks, pattern_stale_weeks)
    conn = get_connection()
    results = {'rollups_deleted': 0, 'patterns_deleted': 0}
    try:
        with conn.cursor() as cur:
            # 1. Delete old weekly rollups
            cur.execute("""
                DELETE FROM recent_carrier_weekly_rollup 
                WHERE week_start_date < DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s WEEK)
            """, (rollup_retention_weeks,))
            results['rollups_deleted'] = cur.rowcount

            # 2. Delete inactive patterns that haven't been seen in a long time
            cur.execute("""
                DELETE FROM recent_carrier_patterns 
                WHERE status = 'inactive' AND last_observed_at < DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s WEEK)
            """, (pattern_stale_weeks,))
            results['patterns_deleted'] = cur.rowcount
        conn.commit()
    except Exception as e:
        conn.rollback()
        log.error("Failed to cleanup pattern memory: %s", e)
    finally:
        conn.close()
    return results

def update_carrier_patterns() -> int:
    """
    Update streaks and statuses in recent_carrier_patterns based on rollups.
    """
    log.info("Updating carrier_patterns streaks and statuses")
    conn = get_connection()
    updated = 0
    try:
        # Current Monday (UTC)
        now_utc = datetime.now(timezone.utc)
        curr_monday = (now_utc - timedelta(days=now_utc.weekday())).date()
        prev_monday = curr_monday - timedelta(days=7)

        # 1. Update patterns for current and previous weeks found in rollups
        with conn.cursor() as cur:
            cur.execute("""
                SELECT origin_iata, dest_iata, carrier_code, day_of_week, time_window, week_start_date, observation_count
                FROM recent_carrier_weekly_rollup
                WHERE week_start_date IN (%s, %s)
            """, (curr_monday, prev_monday))
            rollups = cur.fetchall()

        for row in rollups:
            origin, dest, carrier, dow, window, week_start, count = row
            
            # Upsert pattern with streak logic
            sql = """
                INSERT INTO recent_carrier_patterns (
                    origin_iata, dest_iata, carrier_code, day_of_week, time_window,
                    consecutive_weeks_seen, missed_weeks, first_seen_at, last_observed_at, last_streak_week, status
                ) VALUES (
                    %s, %s, %s, %s, %s, 1, 0, UTC_TIMESTAMP(), UTC_TIMESTAMP(), %s, 'active'
                ) ON DUPLICATE KEY UPDATE
                    consecutive_weeks_seen = CASE
                        WHEN last_streak_week = DATE_SUB(%s, INTERVAL 7 DAY) THEN consecutive_weeks_seen + 1
                        WHEN last_streak_week = %s THEN consecutive_weeks_seen
                        ELSE 1
                    END,
                    last_streak_week = %s,
                    last_observed_at = GREATEST(COALESCE(last_observed_at, '1970-01-01'), UTC_TIMESTAMP()),
                    missed_weeks = 0,
                    status = 'active'
            """
            with conn.cursor() as cur_upd:
                cur_upd.execute(sql, (origin, dest, carrier, dow, window, week_start, week_start, week_start, week_start))
            updated += 1

        # 2. Update statuses for patterns NOT seen recently
        sql_status = """
            UPDATE recent_carrier_patterns
            SET 
                missed_weeks = FLOOR(DATEDIFF(%s, last_streak_week) / 7),
                status = CASE
                    WHEN DATEDIFF(%s, last_streak_week) <= 7 THEN 'active'
                    WHEN DATEDIFF(%s, last_streak_week) = 14 THEN 'watch'
                    WHEN DATEDIFF(%s, last_streak_week) = 21 THEN 'stale'
                    ELSE 'inactive'
                END
            WHERE last_streak_week < %s
        """
        with conn.cursor() as cur_status:
            cur_status.execute(sql_status, (curr_monday, curr_monday, curr_monday, curr_monday, curr_monday))
            updated += cur_status.rowcount
            
        conn.commit()
    except Exception as e:
        conn.rollback()
        log.error("Failed to update carrier patterns: %s", e)
    finally:
        conn.close()
    return updated

def run_all_aggregations(lookback_days: int = 30):
    log.info("Starting run_all_aggregations (lookback: %d days)", lookback_days)
    a1 = aggregate_recent_route_activity(lookback_days)
    a2 = aggregate_recent_route_carrier_activity(lookback_days)
    a3 = aggregate_historical_recent_comparison(lookback_days)
    a4 = aggregate_carrier_weekly_rollups()
    a5 = update_carrier_patterns()
    a6 = cleanup_pattern_memory()
    log.info("Aggregations completed. activity=%d, carriers=%d, comps=%d, rollups=%d, patterns=%d, cleanup=%s", 
             a1, a2, a3, a4, a5, a6)
    return a1, a2, a3, a4, a5, a6


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s %(message)s')
    run_all_aggregations()