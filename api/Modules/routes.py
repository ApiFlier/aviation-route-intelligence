"""
Routes API endpoints
"""

import json
from flask import Blueprint, jsonify, request
from Classes.Database import get_db
from Services.RecentActivity import get_route_recent_activity

routes_bp = Blueprint('routes', __name__)
db = get_db()


@routes_bp.route('/routes')
def list_routes():
    """List routes with optional filters."""
    origin = request.args.get('origin', '').strip().upper()
    dest = request.args.get('dest', '').strip().upper()
    min_pax = request.args.get('min_passengers', type=int)
    min_dist = request.args.get('min_distance', type=int)
    max_dist = request.args.get('max_distance', type=int)
    sort = request.args.get('sort', 'passengers')
    limit = min(int(request.args.get('limit', 50)), 500)
    offset = int(request.args.get('offset', 0))
    
    conditions = []
    params = []
    
    if origin:
        conditions.append("r.origin = %s")
        params.append(origin)
    if dest:
        conditions.append("r.dest = %s")
        params.append(dest)
    if min_pax:
        conditions.append("r.passengers >= %s")
        params.append(min_pax)
    if min_dist:
        conditions.append("r.distance >= %s")
        params.append(min_dist)
    if max_dist:
        conditions.append("r.distance <= %s")
        params.append(max_dist)
    
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    
    valid_sorts = {'passengers': 'r.passengers', 'freight': 'r.freight', 'distance': 'r.distance'}
    order_col = valid_sorts.get(sort, 'r.passengers')
    
    query = f"""
        SELECT 
            r.id, r.origin, r.dest, r.distance, r.passengers, r.freight, r.mail, r.carrier_count,
            ao.name as origin_name, ao.city as origin_city, ao.country as origin_country,
            ao.lat as origin_lat, ao.lon as origin_lon,
            ad.name as dest_name, ad.city as dest_city, ad.country as dest_country,
            ad.lat as dest_lat, ad.lon as dest_lon
        FROM routes r
        JOIN airports ao ON r.origin = ao.iata
        JOIN airports ad ON r.dest = ad.iata
        {where}
        ORDER BY {order_col} DESC
        LIMIT %s OFFSET %s
    """
    params.extend([limit, offset])
    
    routes = db.execute(query, params)
    
    count_query = f"SELECT COUNT(*) as total FROM routes r {where}"
    total = db.execute_one(count_query, params[:-2] if conditions else None)['total']
    
    return jsonify({
        'routes': routes,
        'total': total,
        'limit': limit,
        'offset': offset
    })


@routes_bp.route('/routes/<origin>/<dest>')
def get_route(origin, dest):
    """Get route details between two airports."""
    origin = origin.upper()
    dest = dest.upper()
    
    route = db.execute_one("""
        SELECT 
            r.*,
            ao.name as origin_name, ao.city as origin_city, ao.country as origin_country,
            ao.lat as origin_lat, ao.lon as origin_lon,
            ad.name as dest_name, ad.city as dest_city, ad.country as dest_country,
            ad.lat as dest_lat, ad.lon as dest_lon
        FROM routes r
        JOIN airports ao ON r.origin = ao.iata
        JOIN airports ad ON r.dest = ad.iata
        WHERE r.origin = %s AND r.dest = %s
    """, (origin, dest))
    
    if not route:
        route = db.execute_one("""
            SELECT 
                r.*,
                ao.name as origin_name, ao.city as origin_city, ao.country as origin_country,
                ao.lat as origin_lat, ao.lon as origin_lon,
                ad.name as dest_name, ad.city as dest_city, ad.country as dest_country,
                ad.lat as dest_lat, ad.lon as dest_lon
            FROM routes r
            JOIN airports ao ON r.origin = ao.iata
            JOIN airports ad ON r.dest = ad.iata
            WHERE r.origin = %s AND r.dest = %s
        """, (dest, origin))
    
    if not route:
        return jsonify({'error': 'Route not found'}), 404
    
    return jsonify({'route': route})


@routes_bp.route('/routes/<origin>/<dest>/carriers')
def get_route_carriers(origin, dest):
    """Get carriers operating a route with on-time stats."""
    origin = origin.upper()
    dest = dest.upper()
    
    route = db.execute_one(
        "SELECT id FROM routes WHERE origin = %s AND dest = %s",
        (origin, dest)
    )
    
    if not route:
        route = db.execute_one(
            "SELECT id FROM routes WHERE origin = %s AND dest = %s",
            (dest, origin)
        )
    
    if not route:
        return jsonify({'error': 'Route not found'}), 404
    
    carriers = db.execute("""
        SELECT 
            carrier_code, carrier_name, 
            marketing_carrier, marketing_name, branded_code_share,
            passengers, freight, mail,
            departures_scheduled, departures_performed, seats, 
            payload, air_time, ramp_time, aircraft_types,
            ontime_flights, ontime_arrived, ontime_on_time, ontime_delayed,
            ontime_cancelled, ontime_diverted,
            delay_total_dep_mins, delay_total_arr_mins,
            delay_carrier_mins, delay_weather_mins, delay_nas_mins,
            delay_security_mins, delay_late_aircraft_mins,
            taxi_out_total, taxi_in_total,
            cancel_carrier, cancel_weather, cancel_nas, cancel_security,
            div_reached_dest
        FROM route_carriers
        WHERE route_id = %s
        ORDER BY passengers DESC
    """, (route['id'],))
    
    result = []
    for c in carriers:
        ac_types = json.loads(c['aircraft_types']) if c['aircraft_types'] else {}
        arrived = c['ontime_arrived'] or 0
        tracked = c['ontime_flights'] or 0

        result.append({
            # Identity
            'carrier_code':      c['carrier_code'],
            'carrier_name':      c['carrier_name'],
            'marketing_carrier': c['marketing_carrier'] if c['marketing_carrier'] != c['carrier_code'] else None,
            'marketing_name':    c['marketing_name'],
            'branded_code_share': bool(c['branded_code_share']),

            # Operations (T-100 segment data)
            'departures_scheduled': c['departures_scheduled'],
            'departures_performed': c['departures_performed'],
            'completion_rate':   round(c['departures_performed'] / c['departures_scheduled'] * 100, 1) if c['departures_scheduled'] else None,
            'seats':             c['seats'],
            'load_factor':       round(c['passengers'] / c['seats'] * 100, 1) if c['seats'] else None,
            'avg_flight_mins':   round(c['air_time'] / c['departures_performed']) if c['departures_performed'] else None,
            'avg_taxi_out_mins': round(c['taxi_out_total'] / arrived, 1) if arrived else None,
            'avg_taxi_in_mins':  round(c['taxi_in_total'] / arrived, 1) if arrived else None,

            # On-Time Performance (Marketing On-Time data)
            'ontime_pct':        round(c['ontime_on_time'] / arrived * 100, 1) if arrived else None,
            'cancel_pct':        round(c['ontime_cancelled'] / tracked * 100, 1) if tracked else None,
            'avg_arr_delay_mins': round(c['delay_total_arr_mins'] / arrived, 1) if arrived else None,
            'flights_tracked':   tracked,
            'flights_on_time':   c['ontime_on_time'],
            'flights_delayed':   c['ontime_delayed'],
            'flights_cancelled': c['ontime_cancelled'],
            'flights_diverted':  c['ontime_diverted'],
            'delay_causes': {
                'carrier_mins':      c['delay_carrier_mins'],
                'weather_mins':      c['delay_weather_mins'],
                'nas_mins':          c['delay_nas_mins'],
                'security_mins':     c['delay_security_mins'],
                'late_aircraft_mins': c['delay_late_aircraft_mins'],
            } if tracked else None,
            'cancel_causes': {
                'carrier': c['cancel_carrier'],
                'weather': c['cancel_weather'],
                'nas':     c['cancel_nas'],
                'security': c['cancel_security'],
            } if c['ontime_cancelled'] else None,

            # Traffic (T-100)
            'passengers':   c['passengers'],
            'freight_lbs':  c['freight'],
            'mail_lbs':     c['mail'],
            'payload_lbs':  c['payload'],

            # Equipment
            'aircraft_types': ac_types,
        })

    return jsonify({
        'carriers': result,
        'recent_activity_context': get_route_recent_activity(origin, dest)
    })


@routes_bp.route('/routes/top')
def get_top_routes():
    """Get top routes by various metrics."""
    metric = request.args.get('metric', 'passengers')
    limit = min(int(request.args.get('limit', 20)), 100)
    
    valid_metrics = {'passengers': 'passengers', 'freight': 'freight', 'distance': 'distance'}
    order_col = valid_metrics.get(metric, 'passengers')
    
    routes = db.execute(f"""
        SELECT 
            r.origin, r.dest, r.distance, r.passengers, r.freight, r.carrier_count,
            ao.city as origin_city, ad.city as dest_city
        FROM routes r
        JOIN airports ao ON r.origin = ao.iata
        JOIN airports ad ON r.dest = ad.iata
        ORDER BY r.{order_col} DESC
        LIMIT %s
    """, (limit,))
    
    return jsonify({'routes': routes, 'metric': metric})
