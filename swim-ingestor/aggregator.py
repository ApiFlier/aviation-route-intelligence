"""
FlightConn SWIM Ingestor — Aggregator
Aggregates route-ready flight observations into recent activity tables.
"""

import logging
import json
from datetime import datetime, timezone, timedelta
from db import get_connection

log = logging.getLogger('swim-ingestor.aggregator')

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
            sql_agg = """
                SELECT 
                    origin_iata,
                    dest_iata,
                    MIN(DATE(first_seen_at)) as coverage_start_date,
                    MAX(DATE(last_updated_at)) as coverage_end_date,
                    COUNT(DISTINCT DATE(first_seen_at)) as coverage_days,
                    COUNT(*) as observation_count,
                    COUNT(DISTINCT carrier_code) as carrier_count_observed,
                    MAX(last_updated_at) as last_observed_at
                FROM observed_flights
                WHERE origin_iata IS NOT NULL 
                  AND dest_iata IS NOT NULL
                  AND LENGTH(origin_iata) = 3
                  AND LENGTH(dest_iata) = 3
                  AND source_flight_id IS NOT NULL
                  AND first_seen_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s DAY)
                GROUP BY origin_iata, dest_iata
            """
            cur.execute(sql_agg, (lookback_days,))
            results = cur.fetchall()

        # Step 2: Calculate display mode and confidence, then upsert
        for row in results:
            origin_iata, dest_iata, start_date, end_date, coverage_days, obs_count, carrier_count, last_obs = row
            
            # Confidence rules
            if obs_count == 0:
                confidence = 'none'
            elif coverage_days < 7 or obs_count < 3:
                confidence = 'low'
            elif coverage_days >= 21 and obs_count >= 10:
                confidence = 'high'
            else:
                confidence = 'medium'
                
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
                    last_observed_at, last_observed_carrier, display_mode, confidence, computed_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP()
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
                    computed_at = UTC_TIMESTAMP()
            """
            with conn.cursor() as cur_upsert:
                cur_upsert.execute(upsert_sql, (
                    origin_iata, dest_iata, start_date, end_date, coverage_days, obs_count,
                    carriers, carrier_count, last_obs, last_carrier, display_mode, confidence
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
            sql_agg = """
                SELECT 
                    origin_iata,
                    dest_iata,
                    carrier_code,
                    MIN(DATE(first_seen_at)) as coverage_start_date,
                    MAX(DATE(last_updated_at)) as coverage_end_date,
                    COUNT(DISTINCT DATE(first_seen_at)) as coverage_days,
                    COUNT(*) as observation_count,
                    MAX(last_updated_at) as last_observed_at
                FROM observed_flights
                WHERE origin_iata IS NOT NULL 
                  AND dest_iata IS NOT NULL
                  AND LENGTH(origin_iata) = 3
                  AND LENGTH(dest_iata) = 3
                  AND source_flight_id IS NOT NULL
                  AND carrier_code IS NOT NULL
                  AND first_seen_at >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s DAY)
                GROUP BY origin_iata, dest_iata, carrier_code
            """
            cur.execute(sql_agg, (lookback_days,))
            results = cur.fetchall()

        for row in results:
            origin_iata, dest_iata, carrier_code, start_date, end_date, coverage_days, obs_count, last_obs = row
            
            upsert_sql = """
                INSERT INTO recent_route_carrier_activity (
                    origin_iata, dest_iata, carrier_code, coverage_start_date, coverage_end_date, 
                    coverage_days, observation_count, last_observed_at, computed_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, UTC_TIMESTAMP()
                ) ON DUPLICATE KEY UPDATE
                    coverage_start_date = VALUES(coverage_start_date),
                    coverage_end_date = VALUES(coverage_end_date),
                    coverage_days = VALUES(coverage_days),
                    observation_count = VALUES(observation_count),
                    last_observed_at = VALUES(last_observed_at),
                    computed_at = UTC_TIMESTAMP()
            """
            with conn.cursor() as cur_upsert:
                cur_upsert.execute(upsert_sql, (
                    origin_iata, dest_iata, carrier_code, start_date, end_date, 
                    coverage_days, obs_count, last_obs
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

def run_all_aggregations(lookback_days: int = 30):
    log.info("Starting run_all_aggregations (lookback: %d days)", lookback_days)
    a1 = aggregate_recent_route_activity(lookback_days)
    a2 = aggregate_recent_route_carrier_activity(lookback_days)
    a3 = aggregate_historical_recent_comparison(lookback_days)
    log.info("Aggregations completed. recent_route_activity=%d, recent_route_carrier_activity=%d, comparisons=%d", a1, a2, a3)
    return a1, a2, a3

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s %(message)s')
    run_all_aggregations()