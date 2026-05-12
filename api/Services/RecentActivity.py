"""
Recent Activity Service
Provides read-only access to aggregated FAA SWIM data.
"""

import json
import logging
from Classes.Database import get_db


def _parse_json_list(value):
    """Safely parse a JSON array column that MySQLdb returns as a string."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except Exception:
            return []
    return []

log = logging.getLogger('api.services.recent_activity')

def get_route_recent_activity(origin, destination):
    """
    Fetch recent route activity summary for a specific route.
    """
    db = get_db()
    try:
        # Check if tables exist by attempting a lightweight query
        db.execute_one("SELECT 1 FROM recent_route_activity LIMIT 1")
    except Exception:
        return {
            "available": False,
            "display_mode": "historical_only",
            "reason": "recent_activity_not_configured"
        }

    row = db.execute_one("""
        SELECT * FROM recent_route_activity 
        WHERE origin_iata = %s AND dest_iata = %s
    """, (origin, destination))

    if not row:
        return {
            "available": False,
            "display_mode": "historical_only",
            "reason": "no_recent_activity_for_route"
        }

    # Format the response
    res = {
        "available": True,
        "origin": row['origin_iata'],
        "destination": row['dest_iata'],
        "display_mode": row['display_mode'],
        "confidence": row['confidence'],
        "activity_classification": row['activity_classification'],
        "commercial_confidence": row['commercial_confidence'],
        "coverage_days": row['coverage_days'],
        "observation_count": row['observation_count'],
        "observed_carriers": _parse_json_list(row['observed_carriers']),
        "carrier_count_observed": row['carrier_count_observed'],
        "common_dep_days": row['common_dep_days'],
        "common_dep_windows": row['common_dep_windows'],
        "last_observed_at": row['last_observed_at'].isoformat() if row['last_observed_at'] else None,
        "classification_note": row['classification_note'],
        "carrier_activity": [],
        "notes": [],
        "red_flags": []
    }

    # Fetch carrier-level activity if available
    try:
        carrier_rows = db.execute("""
            SELECT carrier_code, observation_count, avg_dep_delay_mins, avg_arr_delay_mins, cancel_count, last_observed_at, commercial_confidence
            FROM recent_route_carrier_activity
            WHERE origin_iata = %s AND dest_iata = %s
        """, (origin, destination))
        
        filtered_out = False
        for crow in carrier_rows:
            # Only include carriers with at least medium commercial confidence
            if crow['commercial_confidence'] in ('medium', 'high'):
                res['carrier_activity'].append({
                    "carrier_code": crow['carrier_code'],
                    "observation_count": crow['observation_count'],
                    "avg_observed_dep_variance_mins": float(crow['avg_dep_delay_mins']) if crow['avg_dep_delay_mins'] is not None else None,
                    "avg_observed_arr_variance_mins": float(crow['avg_arr_delay_mins']) if crow['avg_arr_delay_mins'] is not None else None,
                    "cancel_count": crow['cancel_count'],
                    "last_observed_at": crow['last_observed_at'].isoformat() if crow['last_observed_at'] else None,
                    "commercial_confidence": crow['commercial_confidence']
                })
            else:
                filtered_out = True
        
        if filtered_out:
            res['notes'].append("Low-confidence or non-commercial carrier-like signals are excluded from carrier_activity.")

    except Exception as e:
        log.warning("Could not fetch recent_route_carrier_activity: %s", e)

    # Ensure observed_carriers in the summary only includes the filtered commercial set
    res['observed_carriers'] = [c['carrier_code'] for c in res['carrier_activity']]
    res['carrier_count_observed'] = len(res['observed_carriers'])

    # Add dynamic notes based on confidence and mode
    if row['display_mode'] == 'early_recent_signal':
        res['notes'].append("Recent activity data is still building and should be interpreted as an early signal.")
        res['notes'].append("Historical route context remains the primary reference until more recent coverage is available.")
    
    if row['activity_classification'] == 'historical_commercial_match':
        res['notes'].append("Recent observations match established historical commercial patterns.")
    elif row['activity_classification'] == 'recent_commercial_candidate':
        res['notes'].append("Recent observations show significant activity on this route, suggesting a new commercial candidate.")
    
    if res['carrier_activity']:
        res['notes'].append("Carrier-level timing metrics are derived from public-release SWIM/SCDS messages and represent observed variance, not official airline schedule data.")

    # Check for carrier mismatch
    mismatch = db.execute_one("""
        SELECT 1 FROM route_historical_recent_comparison
        WHERE origin_iata = %s AND dest_iata = %s AND mismatch_flag = 1
        LIMIT 1
    """, (origin, destination))
    if mismatch:
        res['red_flags'].append("Carrier mismatch detected: Some historical carriers have not been recently observed.")
        res['notes'].append("Observed activity may reflect schedule changes, seasonality, or incomplete data.")

    return res

def get_airport_recent_activity(iata):
    """
    Fetch a summary of recent destination activity for an airport.
    """
    db = get_db()
    try:
        db.execute_one("SELECT 1 FROM recent_route_activity LIMIT 1")
    except Exception:
        return None

    summary = db.execute_one("""
        SELECT 
            COUNT(DISTINCT dest_iata) as dest_count,
            SUM(observation_count) as total_obs,
            MAX(last_observed_at) as last_seen
        FROM recent_route_activity
        WHERE origin_iata = %s
    """, (iata,))

    if not summary or summary['dest_count'] == 0:
        return None

    top_dests = db.execute("""
        SELECT dest_iata, observation_count, activity_classification
        FROM recent_route_activity
        WHERE origin_iata = %s
        ORDER BY observation_count DESC
        LIMIT 5
    """, (iata,))

    return {
        "recent_destination_count": summary['dest_count'],
        "total_observations": int(summary['total_obs']),
        "last_observed_at": summary['last_seen'].isoformat() if summary['last_seen'] else None,
        "top_recent_destinations": top_dests
    }

def get_opportunity_recent_activity(origin, destination):
    """
    Lightweight summary for route opportunity cards.
    """
    db = get_db()
    try:
        row = db.execute_one("""
            SELECT observation_count, display_mode, confidence, activity_classification
            FROM recent_route_activity
            WHERE origin_iata = %s AND dest_iata = %s
        """, (origin, destination))
    except Exception:
        return {"available": False}

    if not row:
        return {"available": False}

    # Check for carrier mismatch
    mismatch = db.execute_one("""
        SELECT 1 FROM route_historical_recent_comparison
        WHERE origin_iata = %s AND dest_iata = %s AND mismatch_flag = 1
        LIMIT 1
    """, (origin, destination))

    return {
        "available": True,
        "display_mode": row['display_mode'],
        "confidence": row['confidence'],
        "activity_classification": row['activity_classification'],
        "observation_count": row['observation_count'],
        "carrier_mismatch": bool(mismatch)
    }

def get_recent_activity_status():
    """
    Get system-wide status of SWIM ingestion and aggregation.
    """
    db = get_db()
    status = {
        "tables_available": False,
        "observed_flights_count": 0,
        "recent_route_activity_count": 0,
        "recent_route_carrier_activity_count": 0,
        "latest_ingestion_run": None,
        "system_freshness": {
            "latest_observed_update": None,
            "minutes_stale": None
        },
        "metadata": {
            "source": "FAA SWIM/SCDS public-release data",
            "operational_use": False,
            "disclaimer": "SWIM/SCDS data is not for operational use."
        }
    }
    
    try:
        status['observed_flights_count'] = db.execute_one("SELECT COUNT(*) as count FROM observed_flights")['count']
        status['recent_route_activity_count'] = db.execute_one("SELECT COUNT(*) as count FROM recent_route_activity")['count']
        status['recent_route_carrier_activity_count'] = db.execute_one("SELECT COUNT(*) as count FROM recent_route_carrier_activity")['count']
        status['tables_available'] = True
        
        latest_run = db.execute_one("""
            SELECT source, status, started_at, messages_recv 
            FROM swim_ingestion_runs 
            ORDER BY started_at DESC LIMIT 1
        """)
        if latest_run:
            status['latest_ingestion_run'] = {
                "source": latest_run['source'],
                "status": latest_run['status'],
                "started_at": latest_run['started_at'].isoformat() if latest_run['started_at'] else None,
                "messages_received": latest_run['messages_recv']
            }
            
        latest_agg = db.execute_one("SELECT MAX(computed_at) as last_agg FROM recent_route_activity")
        if latest_agg:
            status['latest_aggregation_time'] = latest_agg['last_agg'].isoformat() if latest_agg['last_agg'] else None

        freshness = db.execute_one("""
            SELECT 
                MAX(last_updated_at) as latest_update,
                TIMESTAMPDIFF(MINUTE, MAX(last_updated_at), UTC_TIMESTAMP()) as mins_stale
            FROM observed_flights
        """)
        if freshness:
            status['system_freshness']['latest_observed_update'] = freshness['latest_update'].isoformat() if freshness['latest_update'] else None
            status['system_freshness']['minutes_stale'] = freshness['mins_stale']
            
    except Exception:
        pass
        
    return status
