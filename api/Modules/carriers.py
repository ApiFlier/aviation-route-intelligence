"""
Carriers API endpoints
"""

from flask import Blueprint, jsonify, request
from Classes.Database import get_db

carriers_bp = Blueprint('carriers', __name__)
db = get_db()


@carriers_bp.route('/carriers')
def list_carriers():
    """
    List carriers with optional search.
    
    Query params:
        q: Search query
        active: Filter by active status (true/false)
        limit: Max results (default 50, max 500)
        offset: Pagination offset
    """
    q = request.args.get('q', '').strip()
    active = request.args.get('active')
    limit = min(int(request.args.get('limit', 50)), 500)
    offset = int(request.args.get('offset', 0))
    
    conditions = []
    params = []
    
    if q:
        conditions.append("(code LIKE %s OR name LIKE %s)")
        search = f"%{q}%"
        params.extend([search, search])
    
    if active is not None:
        conditions.append("active = %s")
        params.append(active.lower() == 'true')
    
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    
    query = f"""
        SELECT code, iata_code, name, airline_id, active
        FROM carriers
        {where}
        ORDER BY name
        LIMIT %s OFFSET %s
    """
    params.extend([limit, offset])
    
    carriers = db.execute(query, params)
    
    # Get total
    count_query = f"SELECT COUNT(*) as total FROM carriers {where}"
    total = db.execute_one(count_query, params[:-2] if conditions else None)['total']
    
    return jsonify({
        'carriers': carriers,
        'total': total,
        'limit': limit,
        'offset': offset
    })


@carriers_bp.route('/carriers/<code>')
def get_carrier(code):
    """Get carrier details and route statistics."""
    code = code.upper()
    
    carrier = db.execute_one(
        "SELECT * FROM carriers WHERE code = %s", (code,)
    )
    
    if not carrier:
        return jsonify({'error': 'Carrier not found'}), 404
    
    # Get aggregate statistics
    stats = db.execute_one("""
        SELECT 
            COUNT(DISTINCT route_id) as route_count,
            SUM(passengers) as total_passengers,
            SUM(freight) as total_freight,
            SUM(departures_performed) as total_flights,
            SUM(seats) as total_seats
        FROM route_carriers
        WHERE carrier_code = %s
    """, (code,))
    
    return jsonify({
        'carrier': carrier,
        'stats': stats
    })


@carriers_bp.route('/carriers/<code>/routes')
def get_carrier_routes(code):
    """
    Get routes operated by a carrier.
    
    Query params:
        sort: 'passengers', 'freight', 'flights' (default: passengers)
        limit: Max results (default 50, max 200)
        offset: Pagination offset
    """
    code = code.upper()
    sort = request.args.get('sort', 'passengers')
    limit = min(int(request.args.get('limit', 50)), 200)
    offset = int(request.args.get('offset', 0))
    
    valid_sorts = {
        'passengers': 'rc.passengers',
        'freight': 'rc.freight',
        'flights': 'rc.departures_performed'
    }
    order_col = valid_sorts.get(sort, 'rc.passengers')
    
    routes = db.execute(f"""
        SELECT 
            r.origin, r.dest, r.distance,
            rc.passengers, rc.freight, rc.departures_performed as flights,
            rc.seats, rc.air_time,
            ao.city as origin_city, ad.city as dest_city
        FROM route_carriers rc
        JOIN routes r ON rc.route_id = r.id
        JOIN airports ao ON r.origin = ao.iata
        JOIN airports ad ON r.dest = ad.iata
        WHERE rc.carrier_code = %s
        ORDER BY {order_col} DESC
        LIMIT %s OFFSET %s
    """, (code, limit, offset))
    
    total = db.execute_one(
        "SELECT COUNT(*) as total FROM route_carriers WHERE carrier_code = %s",
        (code,)
    )['total']
    
    return jsonify({
        'routes': routes,
        'total': total,
        'limit': limit,
        'offset': offset
    })


@carriers_bp.route('/aircraft')
def list_aircraft():
    """
    List aircraft types.
    
    Query params:
        q: Search query
        manufacturer: Filter by manufacturer
        limit: Max results (default 50, max 500)
    """
    q = request.args.get('q', '').strip()
    manufacturer = request.args.get('manufacturer', '').strip()
    limit = min(int(request.args.get('limit', 50)), 500)
    
    conditions = []
    params = []
    
    if q:
        conditions.append("(type_id LIKE %s OR short_name LIKE %s OR long_name LIKE %s)")
        search = f"%{q}%"
        params.extend([search, search, search])
    
    if manufacturer:
        conditions.append("manufacturer LIKE %s")
        params.append(f"%{manufacturer}%")
    
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    
    aircraft = db.execute(f"""
        SELECT type_id, short_name, long_name, manufacturer
        FROM aircraft
        {where}
        ORDER BY short_name
        LIMIT %s
    """, params + [limit])
    
    return jsonify({'aircraft': aircraft})


@carriers_bp.route('/aircraft/<type_id>')
def get_aircraft(type_id):
    """Get aircraft type details."""
    aircraft = db.execute_one(
        "SELECT * FROM aircraft WHERE type_id = %s", (type_id,)
    )
    
    if not aircraft:
        return jsonify({'error': 'Aircraft type not found'}), 404
    
    return jsonify({'aircraft': aircraft})
