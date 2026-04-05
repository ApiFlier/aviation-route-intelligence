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


@carriers_bp.route('/carriers/<code>/health')
def get_carrier_health(code):
    """Get financial health and employee data for a carrier.

    Query params:
        years: Number of years to return (default 5)
    """
    code = code.upper()
    years = min(int(request.args.get('years', 5)), 20)

    carrier = db.execute_one(
        "SELECT code, name, active FROM carriers WHERE code = %s", (code,)
    )
    if not carrier:
        return jsonify({'error': 'Carrier not found'}), 404

    employees = db.execute("""
        SELECT year, emp_total, pilots, other_flight, maintenance,
               passenger_handling, cargo_handling, general_management,
               pass_gen_svc, other_employees
        FROM carrier_employees
        WHERE carrier_code = %s
        ORDER BY year DESC
        LIMIT %s
    """, (code, years))

    financials = db.execute("""
        SELECT year, quarter,
               salaries_total, benefits_total, total_compensation,
               operating_expense, aircraft_fuel,
               op_revenue, op_profit, net_income,
               cash_position, total_assets, long_term_debt,
               current_liabilities, shareholders_equity
        FROM carrier_financials
        WHERE carrier_code = %s
        ORDER BY year DESC, quarter DESC
        LIMIT %s
    """, (code, years * 4))

    ontime = db.execute_one("""
        SELECT
            SUM(ontime_flights) as total_flights,
            SUM(ontime_on_time) as on_time,
            SUM(ontime_cancelled) as cancelled,
            SUM(ontime_delayed) as delayed_flights,
            SUM(ontime_diverted) as diverted,
            SUM(ontime_arrived) as arrived
        FROM route_carriers
        WHERE carrier_code = %s
    """, (code,))

    # Hub airports (latest year in the hub table)
    hubs = db.execute("""
        SELECT ch.hub_rank, ch.airport_code, ch.departures, ch.pct_of_total,
               a.city, a.name as airport_name
        FROM carrier_hubs ch
        LEFT JOIN airports a ON a.iata = ch.airport_code
        WHERE ch.carrier_code = %s
          AND ch.year = (SELECT MAX(year) FROM carrier_hubs WHERE carrier_code = %s)
        ORDER BY ch.hub_rank
    """, (code, code))

    # Route network (all available years)
    network = db.execute("""
        SELECT year, domestic_routes, intl_routes, total_routes
        FROM carrier_network
        WHERE carrier_code = %s
        ORDER BY year DESC
        LIMIT %s
    """, (code, years))

    # Carrier type / regional info
    attributes = db.execute_one(
        "SELECT carrier_type, feeds_to, brand FROM carrier_attributes WHERE carrier_code = %s",
        (code,)
    )

    # Fleet trend
    fleet = db.execute("""
        SELECT year, aircraft_count FROM carrier_fleet
        WHERE carrier_code = %s ORDER BY year DESC LIMIT %s
    """, (code, years))

    # Annual rollup: join financials with headcount for compensation-per-role
    # and profitability metrics
    annual = db.execute("""
        SELECT
            cf.year,
            SUM(cf.salaries_flight)      as sal_flight,
            SUM(cf.salaries_maintenance) as sal_maint,
            SUM(cf.salaries_traffic)     as sal_traffic,
            SUM(cf.salaries_management)  as sal_mgmt,
            SUM(cf.op_revenue)           as op_revenue,
            SUM(cf.op_profit)            as op_profit,
            SUM(cf.net_income)           as net_income,
            e.pilots,
            e.pass_gen_svc,
            e.other_flight,
            e.maintenance                as maint_count,
            e.passenger_handling,
            e.cargo_handling,
            e.general_management         as mgmt_count
        FROM carrier_financials cf
        JOIN carrier_employees e
            ON e.carrier_code = cf.carrier_code AND e.year = cf.year
        WHERE cf.carrier_code = %s
        GROUP BY cf.year, e.pilots, e.pass_gen_svc, e.other_flight,
                 e.maintenance, e.passenger_handling, e.cargo_handling,
                 e.general_management
        ORDER BY cf.year DESC
        LIMIT %s
    """, (code, years))

    # Build avg compensation by role (most recent year with salary data)
    avg_compensation_by_role = None
    for row in annual:
        if not (row.get('sal_flight') or row.get('sal_maint')):
            continue
        flight_n  = (row.get('pilots') or 0) + (row.get('pass_gen_svc') or 0) + (row.get('other_flight') or 0)
        maint_n   = row.get('maint_count') or 0
        traffic_n = (row.get('passenger_handling') or 0) + (row.get('cargo_handling') or 0)
        mgmt_n    = row.get('mgmt_count') or 0

        def avg(sal, n):
            return round(sal * 1000 / n) if n and sal else None

        avg_compensation_by_role = {
            'year': row['year'],
            'flight_crew':       avg(row.get('sal_flight'), flight_n),
            'maintenance':       avg(row.get('sal_maint'),  maint_n),
            'traffic_handling':  avg(row.get('sal_traffic'), traffic_n),
            'management':        avg(row.get('sal_mgmt'),   mgmt_n),
        }
        break

    # Build profitability trend (all years with income statement data)
    profitability = []
    for row in annual:
        if row.get('op_revenue') is None:
            continue
        rev = row['op_revenue'] or 0
        margin = round(row['op_profit'] / rev * 100, 1) if rev else None
        profitability.append({
            'year':                 row['year'],
            'op_revenue':           row['op_revenue'],
            'op_profit':            row['op_profit'],
            'net_income':           row['net_income'],
            'operating_margin_pct': margin,
        })

    return jsonify({
        'carrier': carrier,
        'employees': employees,
        'financials': financials,
        'ontime': ontime,
        'avg_compensation_by_role': avg_compensation_by_role,
        'profitability': profitability,
        'hubs': hubs,
        'network': network,
        'carrier_type': (attributes or {}).get('carrier_type', 'mainline'),
        'feeds_to': (attributes or {}).get('feeds_to'),
        'brand': (attributes or {}).get('brand'),
        'fleet': fleet,
    })


@carriers_bp.route('/career/airlines')
def career_airlines():
    """Summary of major carriers for the career/health dashboard.

    Returns carriers that have both financial and employee data, with
    latest-year headcount and most-recent-quarter financial snapshot.
    """
    rows = db.execute("""
        SELECT
            c.code, c.name, c.active,
            e.year as emp_year,
            e.emp_total, e.pilots, e.maintenance,
            e.passenger_handling, e.pass_gen_svc,
            f.year as fin_year, f.quarter as fin_quarter,
            f.total_compensation, f.operating_expense, f.aircraft_fuel,
            f.cash_position, f.total_assets, f.long_term_debt,
            f.shareholders_equity,
            rc.total_passengers, rc.route_count,
            ot.total_flights, ot.on_time, ot.cancelled,
            COALESCE(ca.carrier_type, 'mainline') as carrier_type,
            ca.feeds_to,
            ca.brand
        FROM carriers c
        JOIN (
            SELECT carrier_code, MAX(year) as max_year
            FROM carrier_employees GROUP BY carrier_code
        ) latest_e ON latest_e.carrier_code = c.code
        JOIN carrier_employees e
            ON e.carrier_code = c.code AND e.year = latest_e.max_year
        JOIN (
            SELECT carrier_code,
                   MAX(year * 10 + quarter) as max_yq
            FROM carrier_financials GROUP BY carrier_code
        ) latest_f ON latest_f.carrier_code = c.code
        JOIN carrier_financials f
            ON f.carrier_code = c.code
            AND f.year * 10 + f.quarter = latest_f.max_yq
        LEFT JOIN (
            SELECT carrier_code,
                   SUM(passengers) as total_passengers,
                   COUNT(DISTINCT route_id) as route_count
            FROM route_carriers GROUP BY carrier_code
        ) rc ON rc.carrier_code = c.code
        LEFT JOIN (
            SELECT carrier_code,
                   SUM(ontime_flights) as total_flights,
                   SUM(ontime_on_time) as on_time,
                   SUM(ontime_cancelled) as cancelled
            FROM route_carriers GROUP BY carrier_code
        ) ot ON ot.carrier_code = c.code
        LEFT JOIN carrier_attributes ca ON ca.carrier_code = c.code
        WHERE c.active = 1
        ORDER BY e.emp_total DESC
    """)

    return jsonify({'airlines': rows})


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
