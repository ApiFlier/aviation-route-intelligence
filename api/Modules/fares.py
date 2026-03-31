from flask import Blueprint, jsonify, request
from Classes.Database import get_db

fares_bp = Blueprint('fares', __name__)

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
    """Get fare summary - best months, carrier comparison."""
    db = get_db()
    
    # Overall avg by carrier (latest year)
    carrier_query = """
        SELECT rf.carrier_code, c.name as carrier_name,
               SUM(rf.passengers) as total_passengers,
               SUM(rf.passengers * rf.avg_fare) / SUM(rf.passengers) as weighted_avg_fare,
               AVG(rf.avg_fare_per_mile) as avg_yield
        FROM route_fares rf
        JOIN routes r ON rf.route_id = r.id
        LEFT JOIN carriers c ON rf.carrier_code = c.code
        WHERE r.origin = %s AND r.dest = %s
          AND rf.year = (SELECT MAX(year) FROM route_fares)
        GROUP BY rf.carrier_code, c.name
        ORDER BY total_passengers DESC
    """
    
    carriers = db.execute(carrier_query, (origin.upper(), dest.upper()))
    
    # Quarterly trend
    quarterly_query = """
        SELECT rf.year, rf.quarter,
               SUM(rf.passengers) as total_passengers,
               SUM(rf.passengers * rf.avg_fare) / SUM(rf.passengers) as weighted_avg_fare
        FROM route_fares rf
        JOIN routes r ON rf.route_id = r.id
        WHERE r.origin = %s AND r.dest = %s
        GROUP BY rf.year, rf.quarter
        ORDER BY rf.year, rf.quarter
    """
    
    quarterly = db.execute(quarterly_query, (origin.upper(), dest.upper()))
    
    return jsonify({
        'origin': origin.upper(),
        'dest': dest.upper(),
        'carriers': carriers,
        'quarterly': quarterly
    })
