"""
Route Opportunity Scoring Service

Scores existing served routes using public BTS data signals already in the
FlightConn database.  Scores are directional indicators only and do not
estimate airline profitability or guarantee route success.
"""

import math
from Classes.Database import get_db
from Services.RecentActivity import get_opportunity_recent_activity, get_batch_opportunity_recent_activity

# ── Module-level caches (populated on first request, static between reloads) ──
# Passenger-weighted average fares per route_id: {route_id: {avg_fare, avg_fare_per_mile}}
_fare_cache = None
# Carrier codes that file BTS Form 41 financial reports (established carriers)
_reporting_carriers = None

# ── Scoring weights (must sum to 1.0) ────────────────────────────────────────
_W_DEMAND          = 0.30
_W_FARE            = 0.20
_W_COMPETITION     = 0.20
_W_SERVICE         = 0.15
_W_DISTANCE        = 0.10
_W_CARRIER_CONTEXT = 0.05

# Max candidate routes fetched from DB before Python-side scoring and sort
_CANDIDATE_CAP = 1000

VALID_SORTS = {'opportunity', 'demand', 'fare_strength', 'limited_competition', 'service_pressure'}


# ── Public entry point ────────────────────────────────────────────────────────

def get_opportunities(params):
    """Fetch, score, and return ranked route opportunities.

    params keys (all optional):
        origin          str   IATA origin filter
        origin_state    str   2-letter state filter on origin airport
        dest_state      str   2-letter state filter on dest airport
        min_distance    int
        max_distance    int
        min_passengers  int
        max_carriers    int
        domestic_only   bool  default True
        sort            str   default 'opportunity'
        limit           int   default 25, max 100
    """
    global _fare_cache, _reporting_carriers
    db = get_db()

    if _fare_cache is None:
        _fare_cache = _load_fare_cache(db)
    if _reporting_carriers is None:
        _reporting_carriers = _load_reporting_carriers(db)

    sql, args = _build_query(params)
    rows = db.execute(sql, args)

    scored = [_score_route(r, _fare_cache, _reporting_carriers) for r in rows]

    sort = params.get('sort', 'opportunity')
    scored.sort(key=_sort_key(sort), reverse=True)

    limit = params.get('limit', 25)
    results = scored[:limit]

    # Batch fetch recent activity context to avoid N+1 queries.
    route_pairs = [(r['origin'], r['destination']) for r in results]
    batch_ra = get_batch_opportunity_recent_activity(route_pairs)
    
    for r in results:
        key = f"{r['origin']}-{r['destination']}"
        r['recent_activity'] = batch_ra.get(key, {"available": False})

    return {
        'routes': results,
        'meta': {
            'count': len(results),
            'limit': limit,
            'sort': sort,
            'domestic_only': params.get('domestic_only', True),
            'disclaimer': (
                'Scores are directional indicators based on publicly available U.S. aviation datasets. '
                'They do not estimate route-level financial outcomes or guarantee route success.'
            ),
        },
    }


# ── Caches ────────────────────────────────────────────────────────────────────

def _load_fare_cache(db):
    """Passenger-weighted average fares per route_id (~8K rows, static between reloads)."""
    rows = db.execute("""
        SELECT
            route_id,
            SUM(passengers * avg_fare)          / NULLIF(SUM(passengers), 0) AS wt_avg_fare,
            SUM(passengers * avg_fare_per_mile) / NULLIF(SUM(passengers), 0) AS wt_avg_fare_per_mile
        FROM route_fares
        GROUP BY route_id
    """)
    return {
        r['route_id']: {
            'avg_fare':          r['wt_avg_fare'],
            'avg_fare_per_mile': r['wt_avg_fare_per_mile'],
        }
        for r in rows
    }


def _load_reporting_carriers(db):
    """Carrier codes that file BTS Form 41 financial reports.
    Form 41 filers are large scheduled carriers, making this a data-grounded
    proxy for carrier establishment — not a judgment on route profitability.
    """
    rows = db.execute("SELECT DISTINCT carrier_code FROM carrier_financials")
    return {r['carrier_code'] for r in rows}


# ── SQL construction ──────────────────────────────────────────────────────────

def _build_query(params):
    conditions = ['r.passengers > 0']
    args = []

    if params.get('domestic_only', True):
        conditions.append("ao.country = 'US' AND ad.country = 'US'")

    if params.get('origin'):
        conditions.append('r.origin = %s')
        args.append(params['origin'])

    if params.get('origin_state'):
        conditions.append('ao.state = %s')
        args.append(params['origin_state'])

    if params.get('dest_state'):
        conditions.append('ad.state = %s')
        args.append(params['dest_state'])

    if params.get('min_distance') is not None:
        conditions.append('r.distance >= %s')
        args.append(params['min_distance'])

    if params.get('max_distance') is not None:
        conditions.append('r.distance <= %s AND r.distance > 0')
        args.append(params['max_distance'])

    if params.get('min_passengers') is not None:
        conditions.append('r.passengers >= %s')
        args.append(params['min_passengers'])

    if params.get('max_carriers') is not None:
        conditions.append('r.carrier_count <= %s')
        args.append(params['max_carriers'])

    where = 'WHERE ' + ' AND '.join(conditions)

    sql = f"""
        SELECT
            r.id            AS route_id,
            r.origin,
            r.dest,
            r.distance,
            r.passengers,
            r.carrier_count,
            ao.name         AS origin_name,
            ao.city         AS origin_city,
            ao.state        AS origin_state,
            ao.country      AS origin_country,
            ad.name         AS dest_name,
            ad.city         AS dest_city,
            ad.state        AS dest_state,
            ad.country      AS dest_country,
            rc_agg.total_seats,
            rc_agg.total_ontime_arrived,
            rc_agg.total_ontime_on_time,
            rc_agg.total_ontime_cancelled,
            rc_agg.total_ontime_flights,
            rc_agg.total_rc_passengers,
            rc_agg.dominant_carrier,
            rc_agg.dominant_pax
        FROM routes r
        JOIN airports ao ON r.origin = ao.iata
        JOIN airports ad ON r.dest   = ad.iata
        LEFT JOIN (
            SELECT
                route_id,
                SUM(seats)                                                       AS total_seats,
                SUM(ontime_arrived)                                              AS total_ontime_arrived,
                SUM(ontime_on_time)                                              AS total_ontime_on_time,
                SUM(ontime_cancelled)                                            AS total_ontime_cancelled,
                SUM(ontime_flights)                                              AS total_ontime_flights,
                SUM(passengers)                                                  AS total_rc_passengers,
                SUBSTRING_INDEX(
                    GROUP_CONCAT(carrier_code ORDER BY passengers DESC SEPARATOR ','),
                    ',', 1
                )                                                                AS dominant_carrier,
                MAX(passengers)                                                  AS dominant_pax
            FROM route_carriers
            GROUP BY route_id
        ) rc_agg ON rc_agg.route_id = r.id
        {where}
        ORDER BY r.passengers DESC
        LIMIT {_CANDIDATE_CAP}
    """
    return sql, args


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score_route(row, fare_cache, reporting_carriers):
    origin        = row['origin']
    dest          = row['dest']
    distance      = row.get('distance') or 0
    passengers    = row.get('passengers') or 0
    carrier_count = row.get('carrier_count') or 1

    total_seats       = row.get('total_seats') or 0
    total_arrived     = row.get('total_ontime_arrived') or 0
    total_on_time     = row.get('total_ontime_on_time') or 0
    total_cancelled   = row.get('total_ontime_cancelled') or 0
    total_flights     = row.get('total_ontime_flights') or 0
    total_rc_pax      = row.get('total_rc_passengers') or 0
    dominant_carrier  = row.get('dominant_carrier') or ''
    dominant_pax      = row.get('dominant_pax') or 0

    fare_entry        = fare_cache.get(row.get('route_id')) or {}
    avg_fare          = fare_entry.get('avg_fare')
    avg_fare_per_mile = fare_entry.get('avg_fare_per_mile')

    has_fare = avg_fare is not None and float(avg_fare) > 0

    # Derived metrics
    load_factor    = (total_rc_pax / total_seats) if total_seats > 0 else None
    cancel_rate    = (total_cancelled / total_flights) if total_flights > 0 else None
    ontime_pct     = (total_on_time / total_arrived) if total_arrived > 0 else None
    dominant_share = (dominant_pax / total_rc_pax) if total_rc_pax > 0 else None

    # Component scores
    demand_score = _score_demand(passengers)
    fare_score   = _score_fare(avg_fare, avg_fare_per_mile)
    comp_score   = _score_competition(carrier_count, dominant_share)
    svc_score    = _score_service(load_factor, cancel_rate)
    dist_score   = _score_distance(distance)
    ctx_score    = _score_carrier_context(dominant_carrier, reporting_carriers)

    # Risk penalties
    penalties, risk_notes = _risk_penalties(passengers, has_fare, distance)

    raw = (
        demand_score * _W_DEMAND +
        fare_score   * _W_FARE +
        comp_score   * _W_COMPETITION +
        svc_score    * _W_SERVICE +
        dist_score   * _W_DISTANCE +
        ctx_score    * _W_CARRIER_CONTEXT
    )
    opportunity_score = max(0, min(100, round(raw - penalties)))

    components = {
        'demand':          round(demand_score),
        'fare_strength':   round(fare_score),
        'competition_gap': round(comp_score),
        'service_gap':     round(svc_score),
        'distance_fit':    round(dist_score),
        'carrier_context': round(ctx_score),
        'risk_penalty':    -round(penalties),
    }

    data_signal = _data_signal_label(passengers, has_fare, distance)
    label       = _opportunity_label(components, carrier_count, has_fare)
    reasons     = _reasons(components, carrier_count, has_fare)
    risks       = _risks(components, has_fare, carrier_count) + risk_notes
    data_notes  = _data_notes(has_fare, row.get('origin_country'), row.get('dest_country'))
    summary     = _summary_text(origin, dest, components)

    return {
        'origin':            origin,
        'destination':       dest,
        'route':             f'{origin}-{dest}',
        'origin_name':       row.get('origin_name') or '',
        'origin_city':       row.get('origin_city') or '',
        'origin_state':      row.get('origin_state'),
        'destination_name':  row.get('dest_name') or '',
        'destination_city':  row.get('dest_city') or '',
        'destination_state': row.get('dest_state'),
        'distance':          distance or None,
        'opportunity_score': opportunity_score,
        'data_signal':       data_signal,
        'label':             label,
        'components':        components,
        'metrics': {
            'passengers':             passengers,
            'avg_fare':               round(float(avg_fare), 2) if has_fare else None,
            'avg_fare_per_mile':      round(float(avg_fare_per_mile), 4) if avg_fare_per_mile else None,
            'fare_signal_note':       'Historical DB1B survey average — not a live ticket price or current cost estimate' if has_fare else None,
            'carrier_count':          carrier_count,
            'departures_performed':   int(total_flights) if total_flights else None,
            'seats':                  int(total_seats) if total_seats else None,
            'load_factor':            round(load_factor, 2) if load_factor is not None else None,
            'ontime_pct':             round(ontime_pct * 100, 1) if ontime_pct is not None else None,
            'dominant_carrier':       dominant_carrier or None,
            'dominant_carrier_share': round(dominant_share, 2) if dominant_share is not None else None,
        },
        'summary':    summary,
        'reasons':    reasons,
        'risks':      risks,
        'data_notes': data_notes,
    }


# ── Component scorers ─────────────────────────────────────────────────────────

def _score_demand(passengers):
    """Log-scale demand score.
    Reference: 100 pax → ~0, 10k → ~42, 100k → ~63, 340k avg → ~74, 6M max → 100.
    """
    if passengers <= 0:
        return 0.0
    log_pax = math.log10(passengers + 1)
    return max(0.0, min(100.0, (log_pax - 2.0) / 4.78 * 100.0))


def _score_fare(avg_fare, avg_fare_per_mile):
    """Higher DB1B survey fares and per-mile yield score higher.
    Returns 0 when no fare data is available (DB1B does not cover international).
    Absolute fare: $400 = 100, $200 = 50. Yield: $0.50/mi = 100.
    Combined 55% absolute / 45% yield to reward short-haul premium routes.
    """
    if avg_fare is None or float(avg_fare) <= 0:
        return 0.0
    avg_fare = float(avg_fare)
    fare_score = min(100.0, avg_fare / 4.0)
    if avg_fare_per_mile and float(avg_fare_per_mile) > 0:
        yield_score = min(100.0, float(avg_fare_per_mile) / 0.5 * 100.0)
        return fare_score * 0.55 + yield_score * 0.45
    return fare_score


def _score_competition(carrier_count, dominant_share):
    """Fewer carriers → higher score. Adjusted for dominant carrier share.
    Based on routes.carrier_count and route_carriers aggregation.
    """
    base = {1: 90.0, 2: 65.0, 3: 42.0, 4: 22.0}.get(
        carrier_count, max(5.0, 100.0 / carrier_count)
    )
    if dominant_share is None:
        return base
    if dominant_share >= 0.90:
        return min(100.0, base + 5.0)
    if dominant_share >= 0.75:
        return min(100.0, base + 2.0)
    if dominant_share < 0.40:
        return max(0.0, base - 5.0)
    return base


def _score_service(load_factor, cancel_rate):
    """High load factor and elevated cancellations suggest service pressure.
    Load factor from T-100 passengers/seats; cancel rate from Marketing On-Time data.
    """
    if load_factor is None:
        base = 40.0
    elif load_factor >= 0.90:
        base = 95.0
    elif load_factor >= 0.80:
        base = 78.0
    elif load_factor >= 0.70:
        base = 60.0
    elif load_factor >= 0.60:
        base = 42.0
    else:
        base = 20.0

    bonus = 0.0
    if cancel_rate is not None:
        if cancel_rate >= 0.05:
            bonus = 15.0
        elif cancel_rate >= 0.03:
            bonus = 8.0
    return min(100.0, base + bonus)


def _score_distance(distance):
    """Domestic sweet spot is 300–800 miles; very short or transcontinental routes score lower.
    Based on routes.distance from T-100 Market data (Great Circle miles).
    """
    if not distance or distance <= 0:
        return 20.0
    if distance < 100:
        return 10.0
    if distance < 200:
        return 30.0
    if distance < 400:
        return 70.0
    if distance < 800:
        return 90.0
    if distance < 1200:
        return 78.0
    if distance < 2000:
        return 62.0
    if distance < 3000:
        return 42.0
    return 22.0


def _score_carrier_context(dominant_carrier, reporting_carriers):
    """Data-availability signal based on BTS Form 41 financial reporting.
    Carriers that file Form 41 (carrier_financials table) are large enough
    to be required reporters — a proxy for established scheduled service.
    This does NOT assess individual route profitability.
    """
    if dominant_carrier and dominant_carrier.upper() in reporting_carriers:
        return 65.0
    return 42.0


# ── Risk penalties ────────────────────────────────────────────────────────────

def _risk_penalties(passengers, has_fare, distance):
    penalties = 0.0
    notes = []
    if not has_fare:
        penalties += 10.0
        notes.append('No DB1B fare data for this route; fare signal is absent from the score.')
    if not distance or distance <= 0:
        penalties += 15.0
        notes.append('Route distance is unknown; distance component is unreliable.')
    if passengers < 1_000:
        penalties += 20.0
        notes.append('Very low passenger volume weakens all demand-related signals.')
    elif passengers < 10_000:
        penalties += 8.0
    return penalties, notes


# ── Labels and text ───────────────────────────────────────────────────────────

def _data_signal_label(passengers, has_fare, distance):
    if not has_fare or not distance:
        return 'low'
    if passengers >= 100_000:
        return 'high'
    if passengers >= 10_000:
        return 'medium'
    return 'low'


def _opportunity_label(components, carrier_count, has_fare):
    d  = components['demand']
    f  = components['fare_strength']
    c  = components['competition_gap']
    sv = components['service_gap']

    if not has_fare:
        return 'Early Signal'
    if c >= 75 and f >= 60:
        return 'High-Fare Limited Competition'
    if d >= 70 and c >= 60:
        return 'Strong Demand, Limited Competition'
    if d >= 70 and carrier_count >= 4:
        return 'Strong Demand, Mature Market'
    if sv >= 75 and d >= 50:
        return 'Service Pressure Signal'
    if carrier_count >= 4 and d >= 40:
        return 'Competitive Market'
    if d < 35:
        return 'Thin Demand Risk'
    return 'Based on Recent Activity'


def _reasons(components, carrier_count, has_fare):
    d  = components['demand']
    f  = components['fare_strength']
    c  = components['competition_gap']
    sv = components['service_gap']
    reasons = []

    if d >= 75:
        reasons.append('Passenger demand ranks above most comparable domestic routes in historical public traffic records.')
    elif d >= 50:
        reasons.append('This route shows meaningful passenger volume in historical public traffic records.')

    if has_fare:
        if f >= 70:
            reasons.append('Historical fare survey data shows above-average fares for this route.')
        elif f >= 45:
            reasons.append('Historical fare survey data shows fares near the domestic average.')

    if c >= 85:
        reasons.append('Only one carrier currently operates this route.')
    elif c >= 60:
        reasons.append('Carrier competition appears limited on this route.')

    if sv >= 78:
        reasons.append('High seat utilization in historical capacity records suggests this route may be under pressure.')
    elif sv >= 55:
        reasons.append('Seat utilization is solid based on historical capacity and passenger records.')

    return reasons


def _risks(components, has_fare, carrier_count):
    risks = [
        'Public datasets cannot confirm the financial viability of any specific route.',
        'Existing carriers may respond competitively to new entrants.',
        'Demand may be seasonal or connection-driven rather than point-to-point.',
    ]
    c = components['competition_gap']
    d = components['demand']

    if carrier_count == 1 and c >= 70:
        risks.append('Monopoly incumbents often hold cost, slot, or loyalty advantages.')
    if carrier_count >= 4:
        risks.append('Multiple established carriers already compete here, limiting incremental opportunity.')
    if d < 35:
        risks.append('Limited passenger volume may not support additional service.')
    if not has_fare:
        risks.append('No DB1B fare data is available; the fare component is absent from this score.')
    return risks


def _data_notes(has_fare, origin_country, dest_country):
    notes = [
        'Scoring uses publicly available U.S. aviation datasets, including historical route traffic, '
        'fare, capacity, schedule reliability, and carrier financial data.',
    ]
    if has_fare:
        notes.append(
            'The fare signal is a multi-year, passenger-weighted average from U.S. DOT/BTS '
            'DB1B Origin-Destination Survey records (a 10% itinerary sample). '
            'It is a historical survey indicator, not a live ticket price or current cost estimate.'
        )
    else:
        notes.append('No DB1B fare data is available for this route; the fare component scores zero.')
    if origin_country != 'US' or dest_country != 'US':
        notes.append('DB1B fare coverage is US domestic only; international routes have no fare signal.')
    return notes


def _summary_text(origin, dest, components):
    parts = []
    if components['demand'] >= 65:
        parts.append('strong passenger demand')
    elif components['demand'] >= 40:
        parts.append('moderate demand')
    if components['fare_strength'] >= 60:
        parts.append('above-average historical fare signals')
    if components['competition_gap'] >= 70:
        parts.append('limited competition')
    elif components['competition_gap'] >= 50:
        parts.append('moderate competition')
    if parts:
        return (
            f'{origin} to {dest} may deserve further review based on '
            + ', '.join(parts) + '.'
        )
    return (
        f'{origin} to {dest} shows mixed public-data signals; '
        'review individual components for detail.'
    )


# ── Sort key ──────────────────────────────────────────────────────────────────

def _sort_key(sort):
    if sort == 'demand':
        return lambda r: r['components']['demand']
    if sort == 'fare_strength':
        return lambda r: r['components']['fare_strength']
    if sort == 'limited_competition':
        return lambda r: r['components']['competition_gap']
    if sort == 'service_pressure':
        return lambda r: r['components']['service_gap']
    return lambda r: r['opportunity_score']  # default: 'opportunity'
