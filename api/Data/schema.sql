-- FlightConn Database Schema

SET NAMES utf8mb4;
SET CHARACTER SET utf8mb4;

-- Airports table
CREATE TABLE IF NOT EXISTS airports (
    iata VARCHAR(3) PRIMARY KEY,
    name VARCHAR(255),
    city VARCHAR(255),
    state VARCHAR(10),
    country VARCHAR(10),
    lat DECIMAL(10, 6),
    lon DECIMAL(10, 6),
    wac INT,
    route_count INT DEFAULT 0,
    INDEX idx_country (country),
    INDEX idx_state (state),
    INDEX idx_route_count (route_count DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Carriers table
CREATE TABLE IF NOT EXISTS carriers (
    code VARCHAR(10) PRIMARY KEY,
    iata_code VARCHAR(5),
    name VARCHAR(255),
    airline_id INT,
    active BOOLEAN DEFAULT TRUE,
    INDEX idx_name (name),
    INDEX idx_active (active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Aircraft types table
CREATE TABLE IF NOT EXISTS aircraft (
    type_id VARCHAR(10) PRIMARY KEY,
    short_name VARCHAR(50),
    long_name VARCHAR(255),
    manufacturer VARCHAR(100),
    INDEX idx_manufacturer (manufacturer)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Routes table (aggregated route-level data)
CREATE TABLE IF NOT EXISTS routes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    origin VARCHAR(3) NOT NULL,
    dest VARCHAR(3) NOT NULL,
    distance INT DEFAULT 0,
    passengers BIGINT DEFAULT 0,
    freight BIGINT DEFAULT 0,
    mail BIGINT DEFAULT 0,
    carrier_count INT DEFAULT 0,
    UNIQUE KEY uk_route (origin, dest),
    INDEX idx_origin (origin),
    INDEX idx_dest (dest),
    INDEX idx_passengers (passengers DESC),
    INDEX idx_freight (freight DESC),
    FOREIGN KEY (origin) REFERENCES airports(iata) ON DELETE CASCADE,
    FOREIGN KEY (dest) REFERENCES airports(iata) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Route carriers table (per-carrier data on each route)
CREATE TABLE IF NOT EXISTS route_carriers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    route_id INT NOT NULL,
    carrier_code VARCHAR(10) NOT NULL,
    carrier_name VARCHAR(255),
    passengers BIGINT DEFAULT 0,
    freight BIGINT DEFAULT 0,
    mail BIGINT DEFAULT 0,
    departures_scheduled INT DEFAULT 0,
    departures_performed INT DEFAULT 0,
    seats BIGINT DEFAULT 0,
    air_time INT DEFAULT 0,
    aircraft_types JSON,
    UNIQUE KEY uk_route_carrier (route_id, carrier_code),
    INDEX idx_carrier (carrier_code),
    INDEX idx_passengers (passengers DESC),
    FOREIGN KEY (route_id) REFERENCES routes(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Stats table for caching aggregate stats
CREATE TABLE IF NOT EXISTS stats (
    stat_key VARCHAR(50) PRIMARY KEY,
    stat_value BIGINT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
