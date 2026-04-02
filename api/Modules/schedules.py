from flask import Blueprint, jsonify
from Classes.Database import get_db

schedules_bp = Blueprint('schedules', __name__)


def _mins_to_time(mins):
    """Convert minutes from midnight to 12-hour time string, e.g. 375 → '6:15am'."""
    if mins is None:
        return None
    h = mins // 60
    m = mins % 100  # already minutes portion
    m = mins % 60
    period = 'am' if h < 12 else 'pm'
    h12 = h % 12 or 12
    return f"{h12}:{m:02d}{period}"


@schedules_bp.route('/api/routes/<origin>/<dest>/schedules')
def route_schedules(origin, dest):
    """Get typical scheduled flights for a route, grouped by day of week."""
    origin = origin.upper()
    dest = dest.upper()
    db = get_db()

    rows = db.execute("""
        SELECT carrier_code, carrier_name, flight_number, day_of_week,
               typical_dep_time, typical_arr_time, frequency, avg_delay
        FROM route_schedules
        WHERE origin = %s AND dest = %s
        ORDER BY day_of_week, typical_dep_time
    """, (origin, dest))

    day_names = {1: 'Monday', 2: 'Tuesday', 3: 'Wednesday', 4: 'Thursday',
                 5: 'Friday', 6: 'Saturday', 7: 'Sunday'}

    days_map = {}
    for row in rows:
        dow = row['day_of_week']
        if dow not in days_map:
            days_map[dow] = {
                'day': dow,
                'day_name': day_names.get(dow, f'Day {dow}'),
                'flights': []
            }
        avg_delay = float(row['avg_delay']) if row['avg_delay'] is not None else None
        days_map[dow]['flights'].append({
            'carrier_code':   row['carrier_code'],
            'carrier_name':   row['carrier_name'],
            'flight_number':  row['flight_number'],
            'dep_time':       _mins_to_time(row['typical_dep_time']),
            'arr_time':       _mins_to_time(row['typical_arr_time']),
            'dep_time_mins':  row['typical_dep_time'],
            'arr_time_mins':  row['typical_arr_time'],
            'frequency':      row['frequency'],
            'avg_delay':      avg_delay,
        })

    days = [days_map[d] for d in sorted(days_map.keys())]

    return jsonify({
        'origin':        origin,
        'dest':          dest,
        'days':          days,
        'total_flights': len(rows),
    })
