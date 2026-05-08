// FlightConn Frontend Configuration
const CONFIG = {
    // Docker / production: API is served from the same origin
    API_URL: '/api',

    // Local development (Flask only, no Docker): uncomment and set your port
    // API_URL: 'http://localhost:8082/api',
    
    // Map settings
    DEFAULT_CENTER: [39.8, -98.5],
    DEFAULT_ZOOM: 4,
    
    // Pagination
    ROUTES_PER_PAGE: 50,
    AIRPORTS_PER_PAGE: 50,
};
