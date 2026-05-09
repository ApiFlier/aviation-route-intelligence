"""
Route Opportunities API endpoint.

GET /api/routes/opportunities
"""

from flask import Blueprint, jsonify, request
from Services.RouteOpportunity import VALID_SORTS, get_opportunities

opportunities_bp = Blueprint('opportunities', __name__)


@opportunities_bp.route('/routes/opportunities')
def route_opportunities():
    """
    Return a ranked list of route opportunity indicators based on publicly available U.S. aviation datasets.

    Query params:
        origin          str   IATA origin code (optional)
        origin_state    str   2-letter state code (optional)
        dest_state      str   2-letter state code (optional)
        min_distance    int   miles (optional)
        max_distance    int   miles (optional)
        min_passengers  int   (optional)
        max_carriers    int   (optional)
        domestic_only   bool  default true
        sort            str   opportunity|demand|fare_strength|limited_competition|service_pressure
        limit           int   default 25, max 100
    """
    errors = []

    # -- origin (optional IATA code) ------------------------------------------
    origin = request.args.get('origin', '').strip().upper() or None
    if origin and (len(origin) != 3 or not origin.isalpha()):
        errors.append('origin must be a 3-letter IATA airport code (e.g. ATL)')

    # -- state filters ---------------------------------------------------------
    origin_state = request.args.get('origin_state', '').strip().upper() or None
    if origin_state and (len(origin_state) != 2 or not origin_state.isalpha()):
        errors.append('origin_state must be a 2-letter US state code (e.g. PA)')

    dest_state = request.args.get('dest_state', '').strip().upper() or None
    if dest_state and (len(dest_state) != 2 or not dest_state.isalpha()):
        errors.append('dest_state must be a 2-letter US state code (e.g. TX)')

    # -- numeric filters -------------------------------------------------------
    def _int_param(name, min_val=0):
        raw = request.args.get(name)
        if raw is None:
            return None, None
        try:
            val = int(raw)
        except ValueError:
            return None, f'{name} must be an integer'
        if val < min_val:
            return None, f'{name} must be >= {min_val}'
        return val, None

    min_distance, e = _int_param('min_distance', 0)
    if e:
        errors.append(e)

    max_distance, e = _int_param('max_distance', 1)
    if e:
        errors.append(e)

    if min_distance is not None and max_distance is not None and min_distance > max_distance:
        errors.append('min_distance must be <= max_distance')

    min_passengers, e = _int_param('min_passengers', 0)
    if e:
        errors.append(e)

    max_carriers, e = _int_param('max_carriers', 1)
    if e:
        errors.append(e)

    # -- sort ------------------------------------------------------------------
    sort = request.args.get('sort', 'opportunity').strip().lower()
    if sort not in VALID_SORTS:
        errors.append(f'sort must be one of: {", ".join(sorted(VALID_SORTS))}')

    # -- limit -----------------------------------------------------------------
    limit_raw = request.args.get('limit', '25')
    try:
        limit = int(limit_raw)
        if limit < 1:
            errors.append('limit must be >= 1')
            limit = 25
        else:
            limit = min(limit, 100)
    except ValueError:
        errors.append('limit must be an integer')
        limit = 25

    # -- domestic_only ---------------------------------------------------------
    domestic_raw = request.args.get('domestic_only', 'true').strip().lower()
    domestic_only = domestic_raw not in ('false', '0', 'no')

    if errors:
        return jsonify({'error': 'Invalid parameters', 'details': errors}), 400

    params = {
        'origin':         origin,
        'origin_state':   origin_state,
        'dest_state':     dest_state,
        'min_distance':   min_distance,
        'max_distance':   max_distance,
        'min_passengers': min_passengers,
        'max_carriers':   max_carriers,
        'domestic_only':  domestic_only,
        'sort':           sort,
        'limit':          limit,
    }

    result = get_opportunities(params)
    return jsonify(result)
