/**
 * FlightConn API abstraction layer.
 *
 * All fetch() calls live here. To swap the transport (e.g. SQLite/WASM
 * for offline use), replace the _get() implementation and update `base`.
 */

const API = (() => {
    const base = () => CONFIG.API_URL;

    async function _get(endpoint) {
        const res = await fetch(`${base()}${endpoint}`);
        if (!res.ok) throw new Error(`API error ${res.status}: ${endpoint}`);
        return res.json();
    }

    return {
        // ── Global stats ──────────────────────────────────────────────
        getStats() {
            return _get('/stats');
        },

        // ── Airports ──────────────────────────────────────────────────
        getAirports({ q = '', country = '', hasRoutes = false, limit = 50, offset = 0 } = {}) {
            const params = new URLSearchParams();
            if (q)         params.set('q', q);
            if (country)   params.set('country', country);
            if (hasRoutes) params.set('has_routes', 'true');
            params.set('limit', limit);
            params.set('offset', offset);
            return _get(`/airports?${params}`);
        },

        getAirport(iata) {
            return _get(`/airports/${iata}`);
        },

        getAirportRoutes(iata, { direction = 'out', limit = 100, offset = 0 } = {}) {
            const params = new URLSearchParams({ direction, limit });
            if (offset) params.set('offset', offset);
            return _get(`/airports/${iata}/routes?${params}`);
        },

        // ── Routes ────────────────────────────────────────────────────
        getRoute(origin, dest) {
            return _get(`/routes/${origin}/${dest}`);
        },

        getRouteCarriers(origin, dest) {
            return _get(`/routes/${origin}/${dest}/carriers`);
        },

        getRouteFares(origin, dest) {
            return _get(`/routes/${origin}/${dest}/fares/summary`);
        },

        getRouteSchedules(origin, dest) {
            return _get(`/routes/${origin}/${dest}/schedules`);
        },

        getRecentActivityStatus() {
            return _get('/recent-activity/status');
        },

        // ── Aircraft ──────────────────────────────────────────────────
        getAircraft({ limit = 500 } = {}) {
            return _get(`/aircraft?limit=${limit}`);
        },

        // ── Route Opportunities ───────────────────────────────────
        getOpportunities({
            origin         = null,
            origin_state   = null,
            dest_state     = null,
            min_distance   = null,
            max_distance   = null,
            min_passengers = null,
            max_carriers   = null,
            domestic_only  = true,
            sort           = 'opportunity',
            limit          = 25,
        } = {}) {
            const p = new URLSearchParams();
            if (origin)                 p.set('origin', origin);
            if (origin_state)           p.set('origin_state', origin_state);
            if (dest_state)             p.set('dest_state', dest_state);
            if (min_distance != null)   p.set('min_distance', min_distance);
            if (max_distance != null)   p.set('max_distance', max_distance);
            if (min_passengers != null) p.set('min_passengers', min_passengers);
            if (max_carriers != null)   p.set('max_carriers', max_carriers);
            if (!domestic_only)         p.set('domestic_only', 'false');
            p.set('sort', sort);
            p.set('limit', limit);
            return _get(`/routes/opportunities?${p}`);
        },
    };
})();
