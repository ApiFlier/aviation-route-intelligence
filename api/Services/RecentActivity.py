"""
Recent Activity Service
Provides read-only access to aggregated FAA SWIM data.
"""

import json
import logging
from Classes.Database import get_db


def _parse_json_list(value):
    """Safely parse a JSON array column."""
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


def _parse_json_dict(value):
    """Safely parse a JSON object column."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}

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
        "coverage_days": row['coverage_days'],
        "observation_count": row['observation_count'],
        "observed_carriers": [],
        "carrier_activity": [],
        "recent_carrier_patterns": [],
        "notes": [],
        "red_flags": []
    }

    # Fetch carrier-level activity and match types
    try:
        carrier_rows = db.execute("""
            SELECT 
                ca.carrier_code, 
                ca.observation_count, 
                ca.coverage_days,
                ca.avg_dep_delay_mins, 
                ca.avg_arr_delay_mins, 
                ca.cancel_count, 
                ca.last_observed_at, 
                ca.commercial_confidence,
                ca.common_dep_days,
                ca.common_dep_windows,
                comp.in_historical_data,
                c.name as carrier_name
            FROM recent_route_carrier_activity ca
            LEFT JOIN route_historical_recent_comparison comp 
                ON ca.origin_iata = comp.origin_iata 
               AND ca.dest_iata = comp.dest_iata 
               AND ca.carrier_code = comp.carrier_code
            LEFT JOIN carriers c ON ca.carrier_code = c.code
            WHERE ca.origin_iata = %s AND ca.dest_iata = %s
        """, (origin, destination))
        
        for crow in carrier_rows:
            # Match type determination
            match_type = 'historical_carrier_match' if crow.get('in_historical_data') else 'possible_recent_carrier_signal'
            
            # Pattern label logic (overall for the carrier)
            obs = crow['observation_count']
            cov = crow['coverage_days']
            
            if obs >= 1 and cov >= 21: # Roughly 3 weeks
                pattern_label = "Consistent recent pattern"
            elif obs >= 3 and cov >= 7:
                pattern_label = "Consistent recent pattern"
            elif obs >= 3:
                pattern_label = "Early recent pattern"
            else:
                pattern_label = "Recently observed"

            # Parse patterns
            days = _parse_json_dict(crow.get('common_dep_days'))
            windows = _parse_json_list(crow.get('common_dep_windows'))
            
            patterns = []
            day_names = {"Mon": "Monday", "Tue": "Tuesday", "Wed": "Wednesday", "Thu": "Thursday", "Fri": "Friday", "Sat": "Saturday", "Sun": "Sunday"}
            
            # Create pattern strings using the new pattern memory tables
            pattern_rows = db.execute("""
                SELECT p.day_of_week, p.time_window, p.consecutive_weeks_seen, p.status,
                       COALESCE(SUM(r.observation_count), 0) as total_obs
                FROM recent_carrier_patterns p
                LEFT JOIN recent_carrier_weekly_rollup r
                  ON p.origin_iata = r.origin_iata AND p.dest_iata = r.dest_iata 
                 AND p.carrier_code = r.carrier_code AND p.day_of_week = r.day_of_week 
                 AND p.time_window = r.time_window
                 AND r.week_start_date >= DATE_SUB(UTC_TIMESTAMP(), INTERVAL 30 DAY)
                WHERE p.origin_iata = %s AND p.dest_iata = %s AND p.carrier_code = %s
                  AND p.status IN ('active', 'watch', 'stale')
                GROUP BY p.id
                ORDER BY p.status = 'active' DESC, p.consecutive_weeks_seen DESC, total_obs DESC
                LIMIT 5
            """, (origin, destination, crow['carrier_code']))

            highest_pattern_label = "Recently observed"

            for prow in pattern_rows:
                day_code = list(day_names.keys())[prow['day_of_week']]
                day_full = day_names[day_code]
                
                raw_window = prow['time_window']
                try:
                    hour = int(raw_window)
                    window = f"{hour:02d}:00 UTC"
                except ValueError:
                    # Fallback for any non-integer windows, though we expect only hour buckets now
                    window = f"{raw_window} UTC"

                streak = prow['consecutive_weeks_seen']
                status = prow['status']
                total_obs = int(prow['total_obs'])
                
                # Tightened pattern semantic labeling
                label = "Recently observed"
                if status == 'active':
                    if streak >= 4:
                        label = "Consistent recent pattern"
                    elif streak >= 2:
                        label = "Early recent pattern"
                    elif total_obs >= 2:
                        label = "Early signal"
                
                # Track highest label for carrier-level summary
                order = ["Consistent recent pattern", "Early recent pattern", "Early signal", "Recently observed", "Watch", "Stale"]
                if label in order:
                    if highest_pattern_label not in order or order.index(label) < order.index(highest_pattern_label):
                        highest_pattern_label = label

                streak_text = ""
                if status == 'active' and streak > 1:
                    streak_text = f" · Seen {streak} weeks in a row"
                elif status == 'watch':
                    streak_text = " · Pattern not seen recently"
                    label = "Watch"
                elif status == 'stale':
                    streak_text = " · Stale recent pattern"
                    label = "Stale"

                obs_text = ""
                if total_obs > 0:
                    obs_label = "observation" if total_obs == 1 else "observations"
                    obs_text = f" · {total_obs} {obs_label}"

                patterns.append({
                    "observed_weekday": day_full,
                    "observed_time_window": window,
                    "window_observation_count": total_obs,
                    "display_text": f"{day_full} around {window}{obs_text}{streak_text}",
                    "streak_weeks": streak,
                    "status": status,
                    "label": label
                })

            # Use highest pattern label as the overall label
            pattern_label = highest_pattern_label

            # Special label for unmatched carriers
            if match_type == 'possible_recent_carrier_signal':
                pattern_label = "Possible recent carrier signal"

            res['recent_carrier_patterns'].append({
                "carrier_code": crow['carrier_code'],
                "carrier_name": crow['carrier_name'],
                "marketing_carrier_name": None, 
                "match_type": match_type,
                "observation_count": obs,
                "coverage_days": cov,
                "last_observed_at": crow['last_observed_at'].isoformat() + " UTC" if crow['last_observed_at'] else None,
                "patterns": patterns,
                "pattern_label": pattern_label
            })

            # Maintain legacy carrier_activity for backward compatibility
            if crow.get('commercial_confidence') in ('medium', 'high'):
                res['carrier_activity'].append({
                    "carrier_code": crow['carrier_code'],
                    "observation_count": crow['observation_count'],
                    "avg_observed_dep_variance_mins": float(crow['avg_dep_delay_mins']) if crow['avg_dep_delay_mins'] is not None else None,
                    "avg_observed_arr_variance_mins": float(crow['avg_arr_delay_mins']) if crow['avg_arr_delay_mins'] is not None else None,
                    "cancel_count": crow['cancel_count'],
                    "last_observed_at": crow['last_observed_at'].isoformat() + " UTC" if crow['last_observed_at'] else None,
                })
        
    except Exception as e:
        log.warning("Could not fetch recent_route_carrier_activity: %s", e)

    # Ensure observed_carriers in the summary only includes the commercial set
    res['observed_carriers'] = [c['carrier_code'] for c in res['carrier_activity']]

    # Add dynamic notes based on mode
    if row['display_mode'] == 'early_recent_signal':
        res['notes'].append("Recent activity data is still building and should be interpreted as an early signal.")
    
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

    res = {
        "available": True,
        "display_mode": row['display_mode'],
        "data_signal": row['confidence'],
        "activity_classification": row['activity_classification'],
        "observation_count": row['observation_count'],
        "carrier_mismatch": bool(mismatch),
        "carrier_activity": []
    }

    # Fetch carrier-level activity
    try:
        carrier_rows = db.execute("""
            SELECT carrier_code, observation_count, avg_dep_delay_mins, avg_arr_delay_mins, cancel_count, commercial_confidence
            FROM recent_route_carrier_activity
            WHERE origin_iata = %s AND dest_iata = %s
        """, (origin, destination))
        
        for crow in carrier_rows:
            if crow['commercial_confidence'] in ('medium', 'high'):
                res['carrier_activity'].append({
                    "carrier_code": crow['carrier_code'],
                    "observation_count": crow['observation_count'],
                    "avg_observed_arr_variance_mins": float(crow['avg_arr_delay_mins']) if crow['avg_arr_delay_mins'] is not None else None,
                    "commercial_confidence": crow['commercial_confidence']
                })
    except Exception as e:
        log.warning("Could not fetch carrier activity for opportunities: %s", e)

    return res

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
