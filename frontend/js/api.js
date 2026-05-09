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

        // ── Aircraft ──────────────────────────────────────────────────
        getAircraft({ limit = 500 } = {}) {
            return _get(`/aircraft?limit=${limit}`);
        },
    };
})();
