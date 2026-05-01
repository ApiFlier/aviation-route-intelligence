#!/usr/bin/env python3
"""
FlightConn API
REST API for flight route data
"""

import os
from flask import Flask, jsonify
from flask_cors import CORS

from Modules import airports_bp, routes_bp, carriers_bp, fares_bp, schedules_bp
from Classes import get_db

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET') or os.urandom(32)
CORS(app)  # Enable CORS for frontend

# Register blueprints
app.register_blueprint(airports_bp, url_prefix='/api')
app.register_blueprint(routes_bp, url_prefix='/api')
app.register_blueprint(carriers_bp, url_prefix='/api')
app.register_blueprint(fares_bp)
app.register_blueprint(schedules_bp)


@app.route('/')
def index():
    """API info endpoint."""
    return jsonify({
        'name': 'FlightConn API',
        'version': '1.0.0',
        'endpoints': {
            'airports': '/api/airports',
            'routes': '/api/routes',
            'carriers': '/api/carriers',
            'aircraft': '/api/aircraft',
            'stats': '/api/stats'
        }
    })


@app.route('/api/stats')
def get_stats():
    """Get overall statistics."""
    db = get_db()
    
    stats = db.execute("SELECT stat_key, stat_value FROM stats")
    stats_dict = {s['stat_key']: s['stat_value'] for s in stats}
    
    # Get additional counts
    airport_count = db.execute_one("SELECT COUNT(*) as count FROM airports WHERE route_count > 0")
    carrier_count = db.execute_one("SELECT COUNT(DISTINCT carrier_code) as count FROM route_carriers")
    
    return jsonify({
        'routes': stats_dict.get('total_routes', 0),
        'airports': airport_count['count'] if airport_count else 0,
        'carriers': carrier_count['count'] if carrier_count else 0,
        'total_passengers': db.execute_one("SELECT SUM(passengers) as total FROM routes")['total'] or 0,
        'total_freight': db.execute_one("SELECT SUM(freight) as total FROM routes")['total'] or 0
    })


@app.route('/health')
def health():
    """Health check endpoint."""
    try:
        db = get_db()
        db.execute_one("SELECT 1")
        return jsonify({'status': 'healthy', 'database': 'connected'})
    except Exception as e:
        return jsonify({'status': 'unhealthy', 'error': str(e)}), 500


@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Not found'}), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify({'error': 'Internal server error'}), 500


if __name__ == '__main__':
    debug = os.getenv('API_DEBUG', 'false').lower() == 'true'
    port = int(os.getenv('PORT', 8080))
    
    print(f"[FlightConn API] Starting on port {port} (debug={debug})")
    app.run(host='0.0.0.0', port=port, debug=debug)
