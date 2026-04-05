-- FlightConn Database Schema

CREATE TABLE IF NOT EXISTS airports (
    iata VARCHAR(3) PRIMARY KEY,
    name VARCHAR(255),
    city VARCHAR(255),
    state VARCHAR(2),
    country VARCHAR(3),
    lat DECIMAL(10, 6),
    lon DECIMAL(11, 6),
    wac INT,
    airport_id INT,                         -- DOT Airport ID
    route_count INT DEFAULT 0,
    has_fares BOOLEAN DEFAULT FALSE,
    INDEX idx_route_count (route_count),
    INDEX idx_has_fares (has_fares),
    INDEX idx_country (country),
    INDEX idx_airport_id (airport_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS carriers (
    code VARCHAR(10) PRIMARY KEY,
    iata_code VARCHAR(3),
    name VARCHAR(255),
    airline_id INT,
    carrier_group VARCHAR(10),              -- Carrier group code
    carrier_group_new INT,                  -- New carrier group code
    region VARCHAR(50),                     -- Operation region
    active BOOLEAN DEFAULT TRUE,
    INDEX idx_name (name),
    INDEX idx_active (active),
    INDEX idx_airline_id (airline_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS aircraft (
    type_id VARCHAR(10) PRIMARY KEY,
    aircraft_group INT,
    short_name VARCHAR(50),
    long_name VARCHAR(255),
    manufacturer VARCHAR(100)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS routes (
    id INT AUTO_INCREMENT PRIMARY KEY,
    origin VARCHAR(3) NOT NULL,
    dest VARCHAR(3) NOT NULL,
    distance INT,
    passengers BIGINT DEFAULT 0,
    freight BIGINT DEFAULT 0,
    mail BIGINT DEFAULT 0,
    carrier_count INT DEFAULT 0,
    UNIQUE KEY uk_route (origin, dest),
    INDEX idx_origin (origin),
    INDEX idx_dest (dest),
    INDEX idx_passengers (passengers DESC),
    INDEX idx_freight (freight DESC),
    FOREIGN KEY (origin) REFERENCES airports(iata),
    FOREIGN KEY (dest) REFERENCES airports(iata)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS route_carriers (
    id INT AUTO_INCREMENT PRIMARY KEY,
    route_id INT NOT NULL,
    
    -- Operating carrier (who flies the plane)
    carrier_code VARCHAR(10) NOT NULL,
    carrier_name VARCHAR(255),
    
    -- Marketing carrier (who sells the ticket) - from Marketing On-Time data
    marketing_carrier VARCHAR(10),
    marketing_name VARCHAR(255),
    branded_code_share BOOLEAN DEFAULT FALSE,
    
    -- T-100 Segment data (annual aggregates)
    passengers BIGINT DEFAULT 0,
    freight BIGINT DEFAULT 0,
    mail BIGINT DEFAULT 0,
    departures_scheduled INT DEFAULT 0,
    departures_performed INT DEFAULT 0,
    seats INT DEFAULT 0,
    payload BIGINT DEFAULT 0,               -- Available payload (pounds)
    air_time INT DEFAULT 0,                 -- Total air time (minutes)
    ramp_time INT DEFAULT 0,                -- Total ramp-to-ramp time (minutes)
    aircraft_types JSON,
    
    -- On-time performance data (annual aggregates from Marketing On-Time)
    ontime_flights INT DEFAULT 0,           -- Total flights in on-time dataset
    ontime_arrived INT DEFAULT 0,           -- Flights that arrived (not cancelled/diverted)
    ontime_on_time INT DEFAULT 0,           -- Arrived within 15 min of scheduled
    ontime_delayed INT DEFAULT 0,           -- Arrived 15+ min late
    ontime_cancelled INT DEFAULT 0,         -- Cancelled flights
    ontime_diverted INT DEFAULT 0,          -- Diverted flights
    
    -- Delay totals (minutes, for computing averages)
    delay_total_dep_mins BIGINT DEFAULT 0,  -- Total departure delay minutes
    delay_total_arr_mins BIGINT DEFAULT 0,  -- Total arrival delay minutes
    delay_carrier_mins BIGINT DEFAULT 0,    -- Delay due to carrier
    delay_weather_mins BIGINT DEFAULT 0,    -- Delay due to weather
    delay_nas_mins BIGINT DEFAULT 0,        -- Delay due to National Air System
    delay_security_mins BIGINT DEFAULT 0,   -- Delay due to security
    delay_late_aircraft_mins BIGINT DEFAULT 0, -- Delay due to late arriving aircraft
    
    -- Taxi time totals (minutes)
    taxi_out_total INT DEFAULT 0,           -- Total taxi out time
    taxi_in_total INT DEFAULT 0,            -- Total taxi in time
    
    -- Cancellation breakdown by code
    cancel_carrier INT DEFAULT 0,           -- Cancellation code A
    cancel_weather INT DEFAULT 0,           -- Cancellation code B
    cancel_nas INT DEFAULT 0,               -- Cancellation code C
    cancel_security INT DEFAULT 0,          -- Cancellation code D
    
    -- Diversion stats
    div_reached_dest INT DEFAULT 0,         -- Diverted flights that reached destination
    
    UNIQUE KEY uk_route_carrier (route_id, carrier_code),
    INDEX idx_carrier (carrier_code),
    INDEX idx_marketing (marketing_carrier),
    INDEX idx_passengers (passengers DESC),
    FOREIGN KEY (route_id) REFERENCES routes(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS stats (
    stat_key VARCHAR(50) PRIMARY KEY,
    stat_value BIGINT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Typical scheduled flights derived from On-Time Performance data
CREATE TABLE IF NOT EXISTS route_schedules (
    id INT AUTO_INCREMENT PRIMARY KEY,
    origin VARCHAR(5) NOT NULL,
    dest VARCHAR(5) NOT NULL,
    carrier_code VARCHAR(10) NOT NULL,
    carrier_name VARCHAR(100),
    flight_number VARCHAR(10),
    day_of_week TINYINT,          -- 1=Mon, 7=Sun
    typical_dep_time SMALLINT,    -- minutes from midnight (e.g. 375 = 6:15am)
    typical_arr_time SMALLINT,
    frequency INT DEFAULT 0,      -- how many times this schedule appeared in the data
    avg_delay DECIMAL(5,1),       -- average departure delay minutes
    UNIQUE KEY uk_sched (origin, dest, carrier_code, flight_number, day_of_week),
    INDEX idx_route (origin, dest)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Annual employee counts by role (from BTS Form 41 Schedule P-10)
CREATE TABLE IF NOT EXISTS carrier_employees (
    id INT AUTO_INCREMENT PRIMARY KEY,
    carrier_code VARCHAR(10) NOT NULL,
    year INT NOT NULL,
    emp_total INT DEFAULT 0,           -- TOTAL headcount
    pilots INT DEFAULT 0,              -- PILOTS_COPILOTS
    other_flight INT DEFAULT 0,        -- OTHER_FLT_PERS (flight engineers, etc.)
    maintenance INT DEFAULT 0,         -- MAINTENANCE
    passenger_handling INT DEFAULT 0,  -- PASSENGER_HANDLING
    cargo_handling INT DEFAULT 0,      -- CARGO_HANDLING
    general_management INT DEFAULT 0,  -- GENERAL_MANAGE
    pass_gen_svc INT DEFAULT 0,        -- PASS_GEN_SVC_ADMIN (incl. flight attendants)
    other_employees INT DEFAULT 0,     -- trainees, statistical, traffic solicitors, other
    UNIQUE KEY uk_carrier_year (carrier_code, year),
    INDEX idx_ce_carrier (carrier_code),
    INDEX idx_ce_year (year)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Quarterly salary/expense and balance sheet data
-- Salary/expense from BTS Form 41 Schedule P-6 (values in thousands USD)
-- Balance sheet from BTS Form 41 Schedule B-1 (values in thousands USD)
CREATE TABLE IF NOT EXISTS carrier_financials (
    id INT AUTO_INCREMENT PRIMARY KEY,
    carrier_code VARCHAR(10) NOT NULL,
    year INT NOT NULL,
    quarter TINYINT NOT NULL,
    -- Salaries (thousands USD, from P-6)
    salaries_total DECIMAL(15,2) DEFAULT NULL,
    salaries_flight DECIMAL(15,2) DEFAULT NULL,
    salaries_maintenance DECIMAL(15,2) DEFAULT NULL,
    salaries_traffic DECIMAL(15,2) DEFAULT NULL,
    salaries_management DECIMAL(15,2) DEFAULT NULL,
    salaries_other DECIMAL(15,2) DEFAULT NULL,
    -- Benefits (thousands USD, from P-6)
    benefits_total DECIMAL(15,2) DEFAULT NULL,
    benefits_personnel DECIMAL(15,2) DEFAULT NULL,
    benefits_pensions DECIMAL(15,2) DEFAULT NULL,
    -- Total compensation = salaries + benefits
    total_compensation DECIMAL(15,2) DEFAULT NULL,
    -- Operating expense (thousands USD, from P-6)
    operating_expense DECIMAL(15,2) DEFAULT NULL,
    aircraft_fuel DECIMAL(15,2) DEFAULT NULL,
    -- Income statement (thousands USD, from P-1-1)
    op_revenue DECIMAL(15,2) DEFAULT NULL,
    op_profit DECIMAL(15,2) DEFAULT NULL,
    net_income DECIMAL(15,2) DEFAULT NULL,
    -- Balance sheet (thousands USD, from B-1)
    cash_position DECIMAL(15,2) DEFAULT NULL,
    total_assets DECIMAL(15,2) DEFAULT NULL,
    long_term_debt DECIMAL(15,2) DEFAULT NULL,
    current_liabilities DECIMAL(15,2) DEFAULT NULL,
    shareholders_equity DECIMAL(15,2) DEFAULT NULL,
    UNIQUE KEY uk_carrier_quarter (carrier_code, year, quarter),
    INDEX idx_cf_carrier (carrier_code),
    INDEX idx_cf_year (year)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Top hub airports per carrier/year (from T-100 Segment)
CREATE TABLE IF NOT EXISTS carrier_hubs (
    id INT AUTO_INCREMENT PRIMARY KEY,
    carrier_code VARCHAR(10) NOT NULL,
    year INT NOT NULL,
    hub_rank TINYINT NOT NULL,
    airport_code VARCHAR(3) NOT NULL,
    departures INT DEFAULT 0,
    pct_of_total DECIMAL(5,1) DEFAULT NULL,
    UNIQUE KEY uk_hub (carrier_code, year, hub_rank),
    INDEX idx_hub_carrier (carrier_code),
    INDEX idx_hub_year (year)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Route network summary per carrier/year (from T-100 Market)
CREATE TABLE IF NOT EXISTS carrier_network (
    id INT AUTO_INCREMENT PRIMARY KEY,
    carrier_code VARCHAR(10) NOT NULL,
    year INT NOT NULL,
    domestic_routes INT DEFAULT 0,
    intl_routes INT DEFAULT 0,
    total_routes INT DEFAULT 0,
    UNIQUE KEY uk_net (carrier_code, year),
    INDEX idx_net_carrier (carrier_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Carrier type and regional relationship flags
CREATE TABLE IF NOT EXISTS carrier_attributes (
    carrier_code VARCHAR(10) PRIMARY KEY,
    carrier_type VARCHAR(20) DEFAULT 'mainline',
    feeds_to VARCHAR(50) DEFAULT NULL,
    brand VARCHAR(100) DEFAULT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Annual active fleet count (from B-43)
CREATE TABLE IF NOT EXISTS carrier_fleet (
    id INT AUTO_INCREMENT PRIMARY KEY,
    carrier_code VARCHAR(10) NOT NULL,
    year INT NOT NULL,
    aircraft_count INT DEFAULT 0,
    UNIQUE KEY uk_fleet (carrier_code, year),
    INDEX idx_fleet_carrier (carrier_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- DB1B fare data by route/carrier/quarter
CREATE TABLE IF NOT EXISTS route_fares (
    id INT AUTO_INCREMENT PRIMARY KEY,
    route_id INT NOT NULL,
    carrier_code VARCHAR(10),
    year SMALLINT,
    quarter TINYINT,
    passengers INT DEFAULT 0,
    avg_fare DECIMAL(10,2),
    avg_fare_per_mile DECIMAL(6,4),
    FOREIGN KEY (route_id) REFERENCES routes(id),
    UNIQUE KEY (route_id, carrier_code, year, quarter)
);
