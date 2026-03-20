// FlightConn Frontend Configuration
// API URL - change this for different environments
const CONFIG = {
    // When using nginx proxy (production/docker)
    API_URL: '/api',
    
    // For direct API access (development), uncomment:
    // API_URL: 'http://localhost:8083/api',
    
    // Map settings
    DEFAULT_CENTER: [39.8, -98.5],
    DEFAULT_ZOOM: 4,
    
    // Pagination
    ROUTES_PER_PAGE: 50,
    AIRPORTS_PER_PAGE: 50,
};
