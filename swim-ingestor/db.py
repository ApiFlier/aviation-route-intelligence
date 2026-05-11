"""
FlightConn SWIM Ingestor — Database helper

Minimal MySQL helper for the sidecar. Independent of the main app's
Database singleton — no shared imports, no side effects.

Uses PyMySQL (pure Python) to avoid requiring C MySQL libraries in the
sidecar image.

---
Operational SQL Profiling Queries (for manual execution):

1. Overall row counts and duplicates:
   SELECT COUNT(*) as total_rows, 
          COUNT(DISTINCT source_flight_id) as distinct_gufis,
          SUM(origin_iata IS NOT NULL AND dest_iata IS NOT NULL) as route_ready_candidate
   FROM observed_flights;

2. Rows by source/feed:
   SELECT data_source, COUNT(*) as count,
          SUM(origin_iata IS NOT NULL AND dest_iata IS NOT NULL) as route_ready
   FROM observed_flights GROUP BY data_source;

3. Identify repeated source_flight_id (GUFI) groups:
   SELECT source_flight_id, COUNT(*) as count, 
          GROUP_CONCAT(data_source) as sources,
          GROUP_CONCAT(callsign) as callsigns
   FROM observed_flights 
   GROUP BY source_flight_id 
   HAVING count > 1 
   LIMIT 20;

4. Recent activity by status:
   SELECT flight_status, COUNT(*) as count 
   FROM observed_flights 
   WHERE last_updated_at > DATE_SUB(UTC_TIMESTAMP(), INTERVAL 24 HOUR)
   GROUP BY flight_status;
---
"""

import os
import pathlib
import logging
from datetime import datetime, timezone

log = logging.getLogger('swim-ingestor.db')

# Schema file search order: Docker image path first, then host path
_SCHEMA_CANDIDATES = [
    pathlib.Path(__file__).parent / 'swim_schema.sql',
    pathlib.Path(__file__).parent.parent / 'api' / 'Data' / 'swim_schema.sql',
]

_SWIM_TABLES = (
    'swim_ingestion_runs',
    'swim_source_messages',
    'observed_flights',
    'observed_flight_events',
    'recent_route_activity',
    'recent_route_carrier_activity',
    'route_historical_recent_comparison',
)


def _cfg() -> dict:
    return {
        'host':    os.environ.get('DB_HOST', 'localhost'),
        'port':    int(os.environ.get('DB_PORT', 3306)),
        'user':    os.environ.get('DB_USER', 'flightconn'),
        'passwd':  os.environ.get('DB_PASSWORD', ''),
        'db':      os.environ.get('DB_NAME', 'flightconn'),
        'charset': 'utf8mb4',
    }


def get_connection(multi_statements: bool = False):
    """Return a new PyMySQL connection (autocommit off)."""
    import pymysql
    from pymysql.constants import CLIENT
    cfg = _cfg()
    if multi_statements:
        cfg['client_flag'] = CLIENT.MULTI_STATEMENTS
    return pymysql.connect(**cfg)


def apply_schema() -> dict:
    """
    Execute swim_schema.sql against the live database.
    Safe to run multiple times (CREATE TABLE IF NOT EXISTS).
    Uses PyMySQL multi-statement execution so SQL comment semicolons
    don't interfere with statement parsing.

    Returns:
        dict mapping table_name → 'created' | 'already_exists'
    """
    schema_path = None
    for candidate in _SCHEMA_CANDIDATES:
        if candidate.exists():
            schema_path = candidate
            break
    if schema_path is None:
        raise FileNotFoundError(
            'swim_schema.sql not found. Checked: '
            + str([str(p) for p in _SCHEMA_CANDIDATES])
        )

    sql = schema_path.read_text(encoding='utf-8')
    db_name = os.environ.get('DB_NAME', 'flightconn')

    # Snapshot which tables already exist before applying
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT table_name FROM information_schema.tables '
                'WHERE table_schema = %s AND table_name IN %s',
                (db_name, _SWIM_TABLES),
            )
            existing = {row[0] for row in cur.fetchall()}
    finally:
        conn.close()

    # Apply schema using multi-statement execution (handles comment semicolons)
    conn2 = get_connection(multi_statements=True)
    try:
        with conn2.cursor() as cur:
            cur.execute(sql)
            # Consume all result sets from multi-statement execution
            while conn2.next_result():
                pass
        conn2.commit()
    finally:
        conn2.close()

    # Snapshot which tables now exist
    conn3 = get_connection()
    try:
        with conn3.cursor() as cur:
            cur.execute(
                'SELECT table_name FROM information_schema.tables '
                'WHERE table_schema = %s AND table_name IN %s',
                (db_name, _SWIM_TABLES),
            )
            now_present = {row[0] for row in cur.fetchall()}
    finally:
        conn3.close()

    results = {}
    for t in _SWIM_TABLES:
        if t in now_present:
            results[t] = 'already_exists' if t in existing else 'created'
        else:
            results[t] = 'missing'
    return results


def start_ingestion_run(source: str) -> int:
    """
    Insert a swim_ingestion_runs row with status='running'.
    Returns the new run ID.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'INSERT INTO swim_ingestion_runs (source, status, started_at) '
                'VALUES (%s, %s, %s)',
                (
                    source,
                    'running',
                    datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
                ),
            )
            run_id = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return run_id


def upsert_observed_flight(flight_data: dict) -> bool:
    """
    Safely upsert a normalized observed_flight row.
    
    Merge Strategy:
    - If a row exists, update it.
    - Do not overwrite valid callsigns/carriers with 'UNKN'/'UNK'.
    - Do not overwrite valid origin/destination with NULL.
    - Prefer status updates that are more specific (e.g. 'completed' over 'active').
    """
    if not flight_data or not flight_data.get('source_flight_id'):
        return False
        
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            sql = '''
                INSERT INTO observed_flights (
                    source_flight_id, data_source, callsign, carrier_code,
                    origin_iata, dest_iata, flight_status, aircraft_type
                ) VALUES (
                    %(source_flight_id)s, %(data_source)s, %(callsign)s, %(carrier_code)s,
                    %(origin_iata)s, %(dest_iata)s, %(flight_status)s, %(aircraft_type)s
                ) ON DUPLICATE KEY UPDATE
                    callsign = CASE 
                        WHEN VALUES(callsign) IS NULL OR VALUES(callsign) IN ('', 'UNKN', 'UNKNOWN') THEN callsign 
                        ELSE VALUES(callsign) 
                    END,
                    carrier_code = CASE 
                        WHEN VALUES(carrier_code) IS NULL OR VALUES(carrier_code) IN ('', 'UNK', 'UNKNOWN') THEN carrier_code 
                        ELSE VALUES(carrier_code) 
                    END,
                    origin_iata = COALESCE(VALUES(origin_iata), origin_iata),
                    dest_iata = COALESCE(VALUES(dest_iata), dest_iata),
                    flight_status = CASE 
                        WHEN VALUES(flight_status) = 'unknown' THEN flight_status
                        WHEN flight_status IN ('completed', 'cancelled') THEN flight_status
                        ELSE VALUES(flight_status)
                    END,
                    aircraft_type = COALESCE(VALUES(aircraft_type), aircraft_type),
                    last_updated_at = CURRENT_TIMESTAMP
            '''
            cur.execute(sql, flight_data)
        conn.commit()
        return True
    except Exception as e:
        log.error("Failed to upsert flight %s: %s", flight_data.get('source_flight_id'), e)
        return False
def merge_duplicate_observations(dry_run: bool = True) -> dict:
    """
    Find and merge duplicate rows for the same source_flight_id.
    Consolidates partial/route-ready records into a single best-known row.
    Updates the winner's data_source to 'FAA_SWIM'.
    """
    log.info("Starting duplicate merge (dry_run=%s)", dry_run)
    conn = get_connection()
    stats = {'groups_found': 0, 'rows_merged': 0, 'rows_deleted': 0}
    
    try:
        with conn.cursor() as cur:
            # 1. Find GUFIs with more than one row
            cur.execute("""
                SELECT source_flight_id, COUNT(*) as count 
                FROM observed_flights 
                GROUP BY source_flight_id 
                HAVING count > 1
            """)
            duplicates = cur.fetchall()
            stats['groups_found'] = len(duplicates)
            
            if dry_run:
                log.info("Dry run: found %d duplicate groups. No changes made.", len(duplicates))
                return stats

            for (gufi, count) in duplicates:
                # Get all rows for this GUFI
                cur.execute("""
                    SELECT id, callsign, carrier_code, origin_iata, dest_iata, 
                           flight_status, aircraft_type, data_source, first_seen_at, last_updated_at
                    FROM observed_flights 
                    WHERE source_flight_id = %s
                    ORDER BY 
                        (origin_iata IS NOT NULL AND dest_iata IS NOT NULL) DESC,
                        (flight_status IN ('completed', 'cancelled')) DESC,
                        last_updated_at DESC
                """, (gufi,))
                rows = cur.fetchall()
                
                if not rows: continue
                
                # The first row is the "winner"
                winner_id = rows[0][0]
                winner_data = list(rows[0])
                
                # Merge data from other rows into winner
                for i in range(1, len(rows)):
                    other_data = rows[i]
                    # Merge callsign
                    if winner_data[1] in (None, '', 'UNKN', 'UNKNOWN') and other_data[1] not in (None, '', 'UNKN', 'UNKNOWN'):
                        winner_data[1] = other_data[1]
                    # Merge carrier
                    if winner_data[2] in (None, '', 'UNK', 'UNKNOWN') and other_data[2] not in (None, '', 'UNK', 'UNKNOWN'):
                        winner_data[2] = other_data[2]
                    # Merge origin
                    if winner_data[3] is None and other_data[3] is not None:
                        winner_data[3] = other_data[3]
                    # Merge dest
                    if winner_data[4] is None and other_data[4] is not None:
                        winner_data[4] = other_data[4]
                    # Merge status
                    if winner_data[5] == 'unknown' and other_data[5] != 'unknown':
                        winner_data[5] = other_data[5]
                    # Merge aircraft
                    if winner_data[6] is None and other_data[6] is not None:
                        winner_data[6] = other_data[6]
                    # Preserve earliest first_seen
                    if other_data[8] < winner_data[8]:
                        winner_data[8] = other_data[8]
                    # Preserve latest last_updated
                    if other_data[9] > winner_data[9]:
                        winner_data[9] = other_data[9]

                    # Delete the "other" row
                    cur.execute("DELETE FROM observed_flights WHERE id = %s", (other_data[0],))
                    stats['rows_deleted'] += 1
                
                # Update winner
                cur.execute("""
                    UPDATE observed_flights SET
                        callsign = %s, carrier_code = %s, origin_iata = %s, dest_iata = %s,
                        flight_status = %s, aircraft_type = %s, data_source = 'FAA_SWIM',
                        first_seen_at = %s, last_updated_at = %s
                    WHERE id = %s
                """, (winner_data[1], winner_data[2], winner_data[3], winner_data[4],
                      winner_data[5], winner_data[6], winner_data[8], winner_data[9], winner_id))
                stats['rows_merged'] += 1
                
                if stats['rows_merged'] % 100 == 0:
                    conn.commit() # Periodic commit for safety
                    
        conn.commit()
        log.info("Duplicate merge completed: %s", stats)
    except Exception as e:
        conn.rollback()
        log.error("Duplicate merge failed: %s", e)
    finally:
        conn.close()
    return stats

def cleanup_old_data(retention_days: int = 35, runs_retention_days: int = 180) -> dict:
    """
    Safely delete old records from SWIM tables in batches.
    Does NOT touch historical BTS tables.
    """
    log.info("Starting data retention cleanup (flights: %d days, runs: %d days)", 
             retention_days, runs_retention_days)
    
    conn = get_connection()
    results = {'flights_deleted': 0, 'runs_deleted': 0}
    try:
        with conn.cursor() as cur:
            # 1. Clean up old observed_flights (cascade handles events)
            # We do this in batches of 5000 to avoid long locks
            while True:
                cur.execute('''
                    DELETE FROM observed_flights 
                    WHERE last_updated_at < DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s DAY)
                    LIMIT 5000
                ''', (retention_days,))
                deleted = cur.rowcount
                results['flights_deleted'] += deleted
                if deleted < 5000:
                    break
                conn.commit()
                
            # 2. Clean up old ingestion runs
            while True:
                cur.execute('''
                    DELETE FROM swim_ingestion_runs
                    WHERE started_at < DATE_SUB(UTC_TIMESTAMP(), INTERVAL %s DAY)
                    LIMIT 1000
                ''', (runs_retention_days,))
                deleted = cur.rowcount
                results['runs_deleted'] += deleted
                if deleted < 1000:
                    break
                conn.commit()
                
        conn.commit()
        log.info("Cleanup completed: %s", results)
    except Exception as e:
        conn.rollback()
        log.error("Cleanup failed: %s", e)
    finally:
        conn.close()
    return results

def update_ingestion_run_metrics(
    run_id: int,
    counts: dict
) -> None:
    """
    Update a swim_ingestion_runs row with current metrics without closing the run.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                '''UPDATE swim_ingestion_runs SET
                       messages_recv   = %s,
                       messages_ok     = %s,
                       messages_err    = %s,
                       flights_new     = %s,
                       flights_updated = %s
                   WHERE id = %s''',
                (
                    counts.get('messages_recv', 0),
                    counts.get('messages_ok', 0),
                    counts.get('messages_err', 0),
                    counts.get('flights_new', 0),
                    counts.get('flights_updated', 0),
                    run_id,
                ),
            )
        conn.commit()
    finally:
        conn.close()

def finish_ingestion_run(
    run_id: int,
    status: str,
    counts: dict,
    error_detail: str = None,
) -> None:
    """
    Update a swim_ingestion_runs row on completion.

    Args:
        run_id:       row ID from start_ingestion_run
        status:       'completed' | 'failed'
        counts:       dict with keys messages_recv, messages_ok, messages_err,
                      flights_new, flights_updated
        error_detail: brief, redacted error string (no credentials)
    """
    if error_detail and len(error_detail) > 500:
        error_detail = error_detail[:497] + '...'

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                '''UPDATE swim_ingestion_runs SET
                       ended_at        = %s,
                       status          = %s,
                       messages_recv   = %s,
                       messages_ok     = %s,
                       messages_err    = %s,
                       flights_new     = %s,
                       flights_updated = %s,
                       error_detail    = %s
                   WHERE id = %s''',
                (
                    datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
                    status,
                    counts.get('messages_recv', 0),
                    counts.get('messages_ok', 0),
                    counts.get('messages_err', 0),
                    counts.get('flights_new', 0),
                    counts.get('flights_updated', 0),
                    error_detail,
                    run_id,
                ),
            )
        conn.commit()
    finally:
        conn.close()
