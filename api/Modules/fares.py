from flask import Blueprint, jsonify, request
from Classes.Database import get_db

fares_bp = Blueprint('fares', __name__)

_airports_with_fares_cache = None
_airport_routes_fares_cache = {}

@fares_bp.route('/api/routes/<origin>/<dest>/fares')
def route_fares(origin, dest):
    """Get fare data for a route by quarter."""
    db = get_db()
    
    query = """
        SELECT rf.year, rf.quarter, rf.carrier_code, 
               c.name as carrier_name,
               rf.passengers, rf.avg_fare, rf.avg_fare_per_mile
        FROM route_fares rf
        JOIN routes r ON rf.route_id = r.id
        LEFT JOIN carriers c ON rf.carrier_code = c.code
        WHERE r.origin = %s AND r.dest = %s
        ORDER BY rf.year DESC, rf.quarter DESC, rf.passengers DESC
    """
    
    results = db.execute(query, (origin.upper(), dest.upper()))
    
    return jsonify({
        'origin': origin.upper(),
        'dest': dest.upper(),
        'fares': results
    })

@fares_bp.route('/api/routes/<origin>/<dest>/fares/summary')
def route_fares_summary(origin, dest):
    """Get fare summary - last 4 available quarters, carrier comparison."""
    db = get_db()
    origin = origin.upper()
    dest = dest.upper()

    # Find the 4 most recent (year, quarter) combos with data for this route.
    # Use year*10+quarter as a sortable key so we can LIMIT cleanly.
    recent_quarters = db.execute("""
        SELECT DISTINCT rf.year, rf.quarter
        FROM route_fares rf
        JOIN routes r ON rf.route_id = r.id
        WHERE r.origin = %s AND r.dest = %s
        ORDER BY rf.year DESC, rf.quarter DESC
        LIMIT 4
    """, (origin, dest))

    if not recent_quarters:
        return jsonify({'origin': origin, 'dest': dest, 'last_year': None,
                        'carriers': [], 'seasonal': [], 'carrier_seasonal': []})

    last_year = recent_quarters[0]['year']
    yq_values = tuple(q['year'] * 10 + q['quarter'] for q in recent_quarters)
    placeholders = ','.join(['%s'] * len(yq_values))

    q_labels = {1: 'Jan–Mar', 2: 'Apr–Jun', 3: 'Jul–Sep', 4: 'Oct–Dec'}

    # Carrier avg across the 4 most recent quarters
    raw_carriers = db.execute(f"""
        SELECT rf.carrier_code, c.name as carrier_name,
               SUM(rf.passengers) as total_passengers,
               SUM(rf.passengers * rf.avg_fare) / SUM(rf.passengers) as weighted_avg_fare,
               AVG(rf.avg_fare_per_mile) as avg_yield
        FROM route_fares rf
        JOIN routes r ON rf.route_id = r.id
        LEFT JOIN carriers c ON rf.carrier_code = c.code
        WHERE r.origin = %s AND r.dest = %s
          AND (rf.year * 10 + rf.quarter) IN ({placeholders})
        GROUP BY rf.carrier_code, c.name
        ORDER BY total_passengers DESC
    """, (origin, dest, *yq_values))
    carriers = [{
        'carrier_code':  c['carrier_code'],
        'carrier_name':  c['carrier_name'],
        'avg_fare':      round(float(c['weighted_avg_fare']), 2) if c['weighted_avg_fare'] else None,
        'fare_per_mile': round(float(c['avg_yield']), 4) if c['avg_yield'] else None,
        'passengers':    int(c['total_passengers']) if c['total_passengers'] else 0,
    } for c in raw_carriers]

    # Seasonal: one row per quarter, includes year + human-readable label
    raw_seasonal = db.execute(f"""
        SELECT rf.year, rf.quarter,
               SUM(rf.passengers) as total_passengers,
               SUM(rf.passengers * rf.avg_fare) / SUM(rf.passengers) as weighted_avg_fare
        FROM route_fares rf
        JOIN routes r ON rf.route_id = r.id
        WHERE r.origin = %s AND r.dest = %s
          AND (rf.year * 10 + rf.quarter) IN ({placeholders})
        GROUP BY rf.year, rf.quarter
        ORDER BY rf.year DESC, rf.quarter DESC
    """, (origin, dest, *yq_values))
    seasonal = [{
        'year':    s['year'],
        'quarter': s['quarter'],
        'label':   f"Q{s['quarter']} {s['year']} · {q_labels[s['quarter']]}",
        'avg_fare': round(float(s['weighted_avg_fare']), 2) if s['weighted_avg_fare'] else None,
        'passengers': int(s['total_passengers']) if s['total_passengers'] else 0,
    } for s in raw_seasonal]

    # Per-carrier quarterly breakdown
    raw_cs = db.execute(f"""
        SELECT rf.carrier_code, c.name as carrier_name, rf.year, rf.quarter,
               SUM(rf.passengers) as total_passengers,
               SUM(rf.passengers * rf.avg_fare) / SUM(rf.passengers) as weighted_avg_fare
        FROM route_fares rf
        JOIN routes r ON rf.route_id = r.id
        LEFT JOIN carriers c ON rf.carrier_code = c.code
        WHERE r.origin = %s AND r.dest = %s
          AND (rf.year * 10 + rf.quarter) IN ({placeholders})
        GROUP BY rf.carrier_code, c.name, rf.year, rf.quarter
        ORDER BY rf.carrier_code, rf.year DESC, rf.quarter DESC
    """, (origin, dest, *yq_values))
    carrier_seasonal = [{
        'carrier_code': cs['carrier_code'],
        'carrier_name': cs['carrier_name'],
        'year':         cs['year'],
        'quarter':      cs['quarter'],
        'label':        f"Q{cs['quarter']} {cs['year']} · {q_labels[cs['quarter']]}",
        'avg_fare':     round(float(cs['weighted_avg_fare']), 2) if cs['weighted_avg_fare'] else None,
        'passengers':   int(cs['total_passengers']) if cs['total_passengers'] else 0,
    } for cs in raw_cs]

    return jsonify({
        'origin':          origin,
        'dest':            dest,
        'data_through':    f"Q{recent_quarters[0]['quarter']} {recent_quarters[0]['year']}",
        'carriers':        carriers,
        'seasonal':        seasonal,
        'carrier_seasonal': carrier_seasonal,
    })

@fares_bp.route('/api/airports/<iata>/routes/fares')
def airport_routes_fares(iata):
    """Get outbound routes from an airport that have fare data."""
    iata = iata.upper()
    global _airport_routes_fares_cache
    if iata in _airport_routes_fares_cache:
        return _airport_routes_fares_cache[iata]

    db = get_db()

    query = """
        SELECT r.dest, a.name as dest_name, a.city as dest_city, a.lat as dest_lat, a.lon as dest_lon,
               SUM(rf.passengers) as total_passengers,
               SUM(rf.passengers * rf.avg_fare) / SUM(rf.passengers) as avg_fare
        FROM route_fares rf
        JOIN routes r ON rf.route_id = r.id
        LEFT JOIN airports a ON r.dest = a.iata
        WHERE r.origin = %s
        GROUP BY r.dest, a.name, a.city, a.lat, a.lon
        HAVING avg_fare IS NOT NULL
        ORDER BY total_passengers DESC
        LIMIT 100
    """

    results = db.execute(query, (iata,))
    response = jsonify({'origin': iata, 'routes': results})
    _airport_routes_fares_cache[iata] = response

    return response

@fares_bp.route('/api/airports/with-fares')
def airports_with_fares():
    """Get airports that have fare data."""
    global _airports_with_fares_cache
    if _airports_with_fares_cache is not None:
        return _airports_with_fares_cache

    db = get_db()

    query = """
        SELECT iata, name, city, state, country, lat, lon, route_count
        FROM airports
        WHERE has_fares = TRUE AND lat IS NOT NULL AND lon IS NOT NULL
        ORDER BY route_count DESC
        LIMIT 500
    """

    results = db.execute(query, ())
    _airports_with_fares_cache = jsonify({
        'airports': results,
        'total': len(results)
    })

    return _airports_with_fares_cache
