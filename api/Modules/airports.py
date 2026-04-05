"""
Airports API endpoints
"""

from flask import Blueprint, jsonify, request
from Classes.Database import get_db

airports_bp = Blueprint('airports', __name__)
db = get_db()


@airports_bp.route('/airports')
def list_airports():
    """
    List airports with optional search and pagination.
    
    Query params:
        q: Search query (searches iata, name, city)
        country: Filter by country code
        has_routes: If 'true', only return airports with routes
        limit: Max results (default 50, max 3000)
        offset: Pagination offset
    """
    q = request.args.get('q', '').strip()
    country = request.args.get('country', '').strip().upper()
    has_routes = request.args.get('has_routes', '').lower() == 'true'
    limit = min(int(request.args.get('limit', 50)), 3000)
    offset = int(request.args.get('offset', 0))
    
    conditions = []
    params = []
    
    if q:
        conditions.append("(iata LIKE %s OR name LIKE %s OR city LIKE %s)")
        search = f"%{q}%"
        params.extend([search, search, search])
    
    if country:
        conditions.append("country = %s")
        params.append(country)
    
    if has_routes:
        conditions.append("route_count > 0")
    
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    
    query = f"""
        SELECT iata, name, city, state, country, lat, lon, route_count
        FROM airports
        {where}
        ORDER BY route_count DESC
        LIMIT %s OFFSET %s
    """
    params.extend([limit, offset])
    
    results = db.execute(query, params)
    
    # Get total count
    count_query = f"SELECT COUNT(*) as total FROM airports {where}"
    total = db.execute_one(count_query, params[:-2] if params else None)['total']
    
    return jsonify({
        'airports': results,
        'total': total,
        'limit': limit,
        'offset': offset
    })


@airports_bp.route('/airports/<iata>')
def get_airport(iata):
    """Get airport details by IATA code."""
    iata = iata.upper()
    
    airport = db.execute_one(
        "SELECT * FROM airports WHERE iata = %s", (iata,)
    )
    
    if not airport:
        return jsonify({'error': 'Airport not found'}), 404
    
    # Get route statistics — outbound only (origin = iata) so counts match
    # the route_count/total_passengers/carrier_count stored in the airports table.
    # Passengers are aggregated at the route level (before joining carriers) to
    # avoid multiplying by carrier count.
    stats = db.execute_one("""
        SELECT
            COUNT(*)                      as route_count,
            SUM(r.passengers)             as total_passengers,
            SUM(r.freight)                as total_freight,
            (SELECT COUNT(DISTINCT rc.carrier_code)
             FROM route_carriers rc
             JOIN routes r2 ON rc.route_id = r2.id
             WHERE r2.origin = %s)        as carrier_count
        FROM routes r
        WHERE r.origin = %s
    """, (iata, iata))
    
    return jsonify({
        'airport': airport,
        'stats': stats
    })


@airports_bp.route('/airports/<iata>/routes')
def get_airport_routes(iata):
    """
    Get routes from/to an airport.
    
    Query params:
        direction: 'out' (from airport), 'in' (to airport), 'both' (default)
        sort: 'passengers', 'freight', 'distance' (default: passengers)
        limit: Max results (default 50, max 200)
        offset: Pagination offset
    """
    iata = iata.upper()
    direction = request.args.get('direction', 'out')
    sort = request.args.get('sort', 'passengers')
    limit = min(int(request.args.get('limit', 50)), 200)
    offset = int(request.args.get('offset', 0))
    
    # Validate sort column
    valid_sorts = {'passengers': 'r.passengers', 'freight': 'r.freight', 'distance': 'r.distance'}
    order_col = valid_sorts.get(sort, 'r.passengers')
    
    if direction == 'out':
        where = "r.origin = %s"
        params = [iata]
    elif direction == 'in':
        where = "r.dest = %s"
        params = [iata]
    else:
        where = "(r.origin = %s OR r.dest = %s)"
        params = [iata, iata]
    
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
        WHERE {where}
        ORDER BY {order_col} DESC
        LIMIT %s OFFSET %s
    """
    params.extend([limit, offset])
    
    routes = db.execute(query, params)
    
    # Get total count
    count_params = [iata] if direction in ('out', 'in') else [iata, iata]
    total = db.execute_one(
        f"SELECT COUNT(*) as total FROM routes r WHERE {where}",
        count_params
    )['total']
    
    return jsonify({
        'routes': routes,
        'total': total,
        'limit': limit,
        'offset': offset
    })