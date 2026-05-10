"""
FlightConn SWIM Ingestor — Database helper

Minimal MySQL helper for the sidecar. Independent of the main app's
Database singleton — no shared imports, no side effects.

Uses PyMySQL (pure Python) to avoid requiring C MySQL libraries in the
sidecar image.
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
    Partial updates (like status-only messages) will not overwrite
    existing non-null origin_iata, dest_iata, or aircraft_type.
    Returns True if inserted/updated, False otherwise.
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
                    callsign = COALESCE(VALUES(callsign), callsign),
                    carrier_code = COALESCE(VALUES(carrier_code), carrier_code),
                    origin_iata = COALESCE(VALUES(origin_iata), origin_iata),
                    dest_iata = COALESCE(VALUES(dest_iata), dest_iata),
                    flight_status = COALESCE(VALUES(flight_status), flight_status),
                    aircraft_type = COALESCE(VALUES(aircraft_type), aircraft_type),
                    last_updated_at = CURRENT_TIMESTAMP
            '''
            cur.execute(sql, flight_data)
        conn.commit()
        return True
    except Exception as e:
        log.error("Failed to upsert flight %s: %s", flight_data.get('source_flight_id'), e)
        return False
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
