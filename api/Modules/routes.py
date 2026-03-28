"""
Routes API endpoints
"""

import json
from flask import Blueprint, jsonify, request
from Classes.Database import get_db

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
    
    # Parse JSON and compute derived stats
    for carrier in carriers:
        if carrier['aircraft_types']:
            carrier['aircraft_types'] = json.loads(carrier['aircraft_types'])
        else:
            carrier['aircraft_types'] = {}
        
        # Compute on-time percentage
        if carrier['ontime_arrived'] and carrier['ontime_arrived'] > 0:
            carrier['ontime_pct'] = round(carrier['ontime_on_time'] / carrier['ontime_arrived'] * 100, 1)
        else:
            carrier['ontime_pct'] = None
        
        # Compute cancellation rate
        if carrier['ontime_flights'] and carrier['ontime_flights'] > 0:
            carrier['cancel_pct'] = round(carrier['ontime_cancelled'] / carrier['ontime_flights'] * 100, 1)
        else:
            carrier['cancel_pct'] = None
        
        # Compute average arrival delay (for arrived flights)
        if carrier['ontime_arrived'] and carrier['ontime_arrived'] > 0:
            carrier['avg_arr_delay'] = round(carrier['delay_total_arr_mins'] / carrier['ontime_arrived'], 1)
        else:
            carrier['avg_arr_delay'] = None
        
        # Compute average taxi times
        if carrier['ontime_arrived'] and carrier['ontime_arrived'] > 0:
            carrier['avg_taxi_out'] = round(carrier['taxi_out_total'] / carrier['ontime_arrived'], 1)
            carrier['avg_taxi_in'] = round(carrier['taxi_in_total'] / carrier['ontime_arrived'], 1)
        else:
            carrier['avg_taxi_out'] = None
            carrier['avg_taxi_in'] = None
        
        # Load factor
        if carrier['seats'] and carrier['seats'] > 0:
            carrier['load_factor'] = round(carrier['passengers'] / carrier['seats'] * 100, 1)
        else:
            carrier['load_factor'] = None
    
    return jsonify({'carriers': carriers})


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
