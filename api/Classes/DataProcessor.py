"""
DataProcessor for FlightConn
Processes BTS aviation data CSV files and loads them into MySQL.

Data Sources:
- Master Coord.csv - Airport coordinates and info
- Aircraft Types.csv - Aircraft code to name mapping  
- Carrier Decode.csv - Carrier info
- T-100 Market.csv - Passengers, freight, mail by route/carrier
- T-100 Segment.csv - Flights, seats, air time, aircraft by route/carrier
- Marketing On-Time *.csv - Flight-level on-time performance with marketing carrier
"""

import csv
import json
import os
import glob
from collections import defaultdict
from typing import Dict, Any, List
from .Database import get_db


REGIONAL_CARRIERS = {
    'MQ': {'feeds_to': 'AA',         'brand': 'American Eagle'},
    'OH': {'feeds_to': 'AA',         'brand': 'American Eagle'},
    'PT': {'feeds_to': 'AA',         'brand': 'American Eagle'},
    'ZW': {'feeds_to': 'AA',         'brand': 'American Eagle'},
    'YX': {'feeds_to': 'AA,DL,UA',   'brand': 'Republic Airways'},
    'OO': {'feeds_to': 'DL,UA,AA,AS','brand': 'SkyWest Airlines'},
    '9E': {'feeds_to': 'DL',         'brand': 'Delta Connection'},
    'QX': {'feeds_to': 'AS',         'brand': 'Alaska Horizon'},
    'YV': {'feeds_to': 'UA',         'brand': 'United Express'},
    'C5': {'feeds_to': 'UA',         'brand': 'United Express'},
    'G7': {'feeds_to': 'UA',         'brand': 'United Express'},
    '3M': {'feeds_to': 'UA',         'brand': 'United Express'},
}

CARGO_CARRIERS = {
    'FX', '5X', 'PO', 'ABX', '5Y', 'KAQ', 'KLQ',
    'L2', 'M6', 'GFQ', '8C', 'U7', 'NC', 'KD', 'WI',
}

CHARTER_CARRIERS = {
    'X9', 'N8', 'WL', 'GCA', '09Q', '27Q', '1EQ',
    '2PQ', '3EQ', 'PFQ',
}


class DataProcessor:
    def __init__(self, data_dir: str = 'Data'):
        self.data_dir = data_dir
        self.db = get_db()
        
        # File paths
        self.files = {
            'airports': 'Support Tables - Master Coord.csv',
            'aircraft': 'Support Tables - Aircraft Types.csv',
            'carriers': 'Support Tables - Carrier Decode.csv',
        }
        
        self.patterns = {
            'market': 'Air Carrier Statistics - All Carriers - T-100 Market - *.csv',
            'segment': 'Air Carrier Statistics - All Carriers - T-100 Segment - *.csv',
            'ontime': 'On-Time Performance Data - Marketing - *.csv',
            'db1b_market': 'Origin and Destination Survey - DB1BMarket - *.csv',
        }
        
        # In-memory data during processing
        self.airports = {}
        self.aircraft = {}
        self.carriers = {}
        self.routes = defaultdict(lambda: defaultdict(lambda: {
            'distance': 0,
            'passengers': 0,
            'freight': 0,
            'mail': 0,
            'carriers': {}
        }))
    
    def _get_path(self, key: str) -> str:
        """Get full path for a data file."""
        return os.path.join(self.data_dir, self.files[key])

    def _get_files(self, key: str) -> list:
        """Get list of files matching a pattern."""
        pattern = os.path.join(self.data_dir, self.patterns[key])
        return sorted(glob.glob(pattern))
    
    def _safe_int(self, value: str, default: int = 0) -> int:
        """Safely convert to int."""
        try:
            return int(float(value)) if value else default
        except (ValueError, TypeError):
            return default
    
    def _safe_float(self, value: str, default: float = 0.0) -> float:
        """Safely convert to float."""
        try:
            return float(value) if value else default
        except (ValueError, TypeError):
            return default
    
    def load_airports(self) -> int:
        """Load airport data from Master Coord.csv into MySQL."""
        print("Loading airports from Master Coord.csv...")
        path = self._get_path('airports')
        
        if not os.path.exists(path):
            print(f"  ERROR: {path} not found")
            return 0
        
        airports = []
        with open(path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get('AIRPORT_IS_LATEST') != '1':
                    continue
                if row.get('AIRPORT_IS_CLOSED') == '1':
                    continue
                
                code = row.get('AIRPORT', '').strip()
                if not code or len(code) != 3:
                    continue
                
                lat = self._safe_float(row.get('LATITUDE'))
                lon = self._safe_float(row.get('LONGITUDE'))
                if lat == 0 and lon == 0:
                    continue
                
                city_name = row.get('DISPLAY_AIRPORT_CITY_NAME_FULL', '').strip()
                state = row.get('AIRPORT_STATE_CODE', '').strip()
                country = row.get('AIRPORT_COUNTRY_CODE_ISO', '').strip()
                airport_id = self._safe_int(row.get('AIRPORT_ID'))
                
                self.airports[code] = True
                airports.append((
                    code,
                    row.get('DISPLAY_AIRPORT_NAME', '').strip(),
                    city_name,
                    state if country == 'US' else None,
                    country,
                    lat,
                    lon,
                    self._safe_int(row.get('AIRPORT_WAC')),
                    airport_id,
                    0
                ))
        
        query = """
            INSERT INTO airports (iata, name, city, state, country, lat, lon, wac, airport_id, route_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                name=VALUES(name), city=VALUES(city), state=VALUES(state),
                country=VALUES(country), lat=VALUES(lat), lon=VALUES(lon), 
                wac=VALUES(wac), airport_id=VALUES(airport_id)
        """
        self.db.execute_many(query, airports)
        print(f"  Loaded {len(airports)} airports")
        return len(airports)
    
    def load_aircraft(self) -> int:
        """Load aircraft types into MySQL."""
        print("Loading aircraft types...")
        path = self._get_path('aircraft')
        
        if not os.path.exists(path):
            print(f"  ERROR: {path} not found")
            return 0
        
        aircraft = []
        with open(path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                type_id = row.get('AC_TYPEID', '').strip()
                if not type_id:
                    continue
                if row.get('END_DATE', '').strip():
                    continue
                
                self.aircraft[type_id] = row.get('SHORT_NAME', '').strip()
                aircraft.append((
                    type_id,
                    self._safe_int(row.get('AC_GROUP')),
                    row.get('SHORT_NAME', '').strip(),
                    row.get('LONG_NAME', '').strip(),
                    row.get('MANUFACTURER', '').strip()
                ))
        
        query = """
            INSERT INTO aircraft (type_id, aircraft_group, short_name, long_name, manufacturer)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                aircraft_group=VALUES(aircraft_group), short_name=VALUES(short_name), 
                long_name=VALUES(long_name), manufacturer=VALUES(manufacturer)
        """
        self.db.execute_many(query, aircraft)
        print(f"  Loaded {len(aircraft)} aircraft types")
        return len(aircraft)
    
    def load_carriers(self) -> int:
        """Load carriers into MySQL."""
        print("Loading carriers...")
        path = self._get_path('carriers')
        
        if not os.path.exists(path):
            print(f"  ERROR: {path} not found")
            return 0
        
        carriers = {}
        with open(path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                code = row.get('UNIQUE_CARRIER', '').strip()
                if not code:
                    continue
                
                has_end_date = bool(row.get('THRU_DATE_SOURCE', '').strip())
                if code not in carriers or not has_end_date:
                    carriers[code] = (
                        code,
                        row.get('CARRIER', '').strip(),
                        row.get('UNIQUE_CARRIER_NAME', '').strip(),
                        self._safe_int(row.get('AIRLINE_ID')),
                        row.get('CARRIER_GROUP', '').strip(),
                        self._safe_int(row.get('CARRIER_GROUP_NEW')),
                        row.get('REGION', '').strip(),
                        not has_end_date
                    )
        
        query = """
            INSERT INTO carriers (code, iata_code, name, airline_id, carrier_group, carrier_group_new, region, active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                iata_code=VALUES(iata_code), name=VALUES(name), airline_id=VALUES(airline_id),
                carrier_group=VALUES(carrier_group), carrier_group_new=VALUES(carrier_group_new),
                region=VALUES(region), active=VALUES(active)
        """
        self.db.execute_many(query, list(carriers.values()))
        self.carriers = {c[0]: c[2] for c in carriers.values()}
        print(f"  Loaded {len(carriers)} carriers")
        return len(carriers)
    
    def process_market_data(self) -> None:
        """Process T-100 Market data for passengers, freight, mail.
        Filters to CLASS='F' (scheduled service) to exclude charter operators
        and private-aviation companies that file CLASS='L' with 1-10 passengers."""
        print("Processing T-100 Market data...")
        files = self._get_files('market')

        if not files:
            print(f"  ERROR: No T-100 Market files found")
            return

        print(f"  Found {len(files)} market files")
        row_count = 0
        for filepath in files:
            print(f"  Processing {os.path.basename(filepath)}...")
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # CLASS='F' is scheduled service (all major/regional/ULCC airlines).
                    # CLASS='L' includes charter and private-jet operators alongside a few
                    # small scheduled carriers — excluding it removes the noise without
                    # meaningfully affecting route counts for the carriers we display.
                    if row.get('CLASS', '') != 'F':
                        continue

                    origin = row.get('ORIGIN', '').strip()
                    dest = row.get('DEST', '').strip()
                    carrier = row.get('UNIQUE_CARRIER', '').strip()

                    if not origin or not dest or not carrier:
                        continue
                    if origin not in self.airports or dest not in self.airports:
                        continue

                    passengers = self._safe_int(row.get('PASSENGERS'))
                    freight = self._safe_int(row.get('FREIGHT'))
                    mail = self._safe_int(row.get('MAIL'))
                    distance = self._safe_int(row.get('DISTANCE'))
                    carrier_name = row.get('UNIQUE_CARRIER_NAME', '').strip()

                    route = self.routes[origin][dest]
                    route['passengers'] += passengers
                    route['freight'] += freight
                    route['mail'] += mail
                    if distance > 0:
                        route['distance'] = distance

                    if carrier not in route['carriers']:
                        route['carriers'][carrier] = self._new_carrier_record(carrier_name)

                    c = route['carriers'][carrier]
                    c['passengers'] += passengers
                    c['freight'] += freight
                    c['mail'] += mail

                    row_count += 1
                    if row_count % 100000 == 0:
                        print(f"  Processed {row_count:,} market rows...")
        
        print(f"  Processed {row_count:,} total market rows")
    
    def process_segment_data(self) -> None:
        """Process T-100 Segment data for flights, seats, air time, aircraft.
        Applies the same CLASS='F' filter as process_market_data so that charter
        and private-jet operators don't create route_carriers rows."""
        print("Processing T-100 Segment data...")
        files = self._get_files('segment')

        if not files:
            print(f"  WARNING: No T-100 Segment files found, skipping")
            return

        print(f"  Found {len(files)} segment files")
        row_count = 0
        for filepath in files:
            print(f"  Processing {os.path.basename(filepath)}...")
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('CLASS', '') != 'F':
                        continue

                    origin = row.get('ORIGIN', '').strip()
                    dest = row.get('DEST', '').strip()
                    carrier = row.get('UNIQUE_CARRIER', '').strip()

                    if not origin or not dest or not carrier:
                        continue
                    if origin not in self.airports or dest not in self.airports:
                        continue

                    departures_scheduled = self._safe_int(row.get('DEPARTURES_SCHEDULED'))
                    departures_performed = self._safe_int(row.get('DEPARTURES_PERFORMED'))
                    seats = self._safe_int(row.get('SEATS'))
                    payload = self._safe_int(row.get('PAYLOAD'))
                    air_time = self._safe_int(row.get('AIR_TIME'))
                    ramp_time = self._safe_int(row.get('RAMP_TO_RAMP', row.get('RAMPTIME', 0)))
                    aircraft_type = row.get('AIRCRAFT_TYPE', '').strip()
                    carrier_name = row.get('UNIQUE_CARRIER_NAME', '').strip()

                    route = self.routes[origin][dest]
                    if carrier not in route['carriers']:
                        route['carriers'][carrier] = self._new_carrier_record(carrier_name)

                    c = route['carriers'][carrier]
                    c['departures_scheduled'] += departures_scheduled
                    c['departures_performed'] += departures_performed
                    c['seats'] += seats
                    c['payload'] += payload
                    c['air_time'] += air_time
                    c['ramp_time'] += ramp_time
                    if aircraft_type and departures_performed > 0:
                        if aircraft_type not in c['aircraft_types']:
                            c['aircraft_types'][aircraft_type] = 0
                        c['aircraft_types'][aircraft_type] += departures_performed

                    row_count += 1
                    if row_count % 100000 == 0:
                        print(f"  Processed {row_count:,} segment rows...")
        
        print(f"  Processed {row_count:,} total segment rows")
    
    def process_ontime_data(self) -> None:
        """Process Marketing Carrier On-Time Performance data files."""
        print("Processing Marketing Carrier On-Time Performance data...")
        
        # Find Marketing on-time CSV files (prefer over Reporting)
        pattern = os.path.join(self.data_dir, self.patterns['ontime'])
        files = glob.glob(pattern)
        
        if not files:
            # Fall back to Reporting carrier data
            pattern = os.path.join(self.data_dir, 'On Time - Reporting*.csv')
            files = glob.glob(pattern)
            if files:
                print(f"  No Marketing data found, using Reporting carrier data")
                self._process_reporting_ontime(files)
                return
            else:
                print(f"  No on-time files found")
                return
        
        print(f"  Found {len(files)} Marketing on-time data files")
        
        total_rows = 0
        for filepath in sorted(files):
            filename = os.path.basename(filepath)
            print(f"  Processing {filename}...")
            
            file_rows = 0
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    origin = row.get('ORIGIN', '').strip()
                    dest = row.get('DEST', '').strip()
                    op_carrier = row.get('OP_UNIQUE_CARRIER', '').strip()
                    mkt_carrier = row.get('MKT_UNIQUE_CARRIER', '').strip()
                    
                    if not origin or not dest or not op_carrier:
                        continue
                    if origin not in self.airports or dest not in self.airports:
                        continue
                    
                    # Get route and carrier (keyed by operating carrier)
                    route = self.routes[origin][dest]
                    if op_carrier not in route['carriers']:
                        route['carriers'][op_carrier] = self._new_carrier_record('')
                    
                    c = route['carriers'][op_carrier]
                    
                    # Store marketing carrier info
                    if mkt_carrier and not c.get('marketing_carrier'):
                        c['marketing_carrier'] = mkt_carrier
                    
                    # Track branded code share
                    branded = row.get('BRANDED_CODE_SHARE', '').strip()
                    if branded and branded != '':
                        c['branded_code_share'] = True
                    
                    # Count this flight
                    c['ontime_flights'] += 1
                    
                    # Check cancelled/diverted
                    cancelled = self._safe_int(row.get('CANCELLED'))
                    diverted = self._safe_int(row.get('DIVERTED'))
                    
                    if cancelled:
                        c['ontime_cancelled'] += 1
                        cancel_code = row.get('CANCELLATION_CODE', '').strip()
                        if cancel_code == 'A':
                            c['cancel_carrier'] += 1
                        elif cancel_code == 'B':
                            c['cancel_weather'] += 1
                        elif cancel_code == 'C':
                            c['cancel_nas'] += 1
                        elif cancel_code == 'D':
                            c['cancel_security'] += 1
                    elif diverted:
                        c['ontime_diverted'] += 1
                        # Check if diverted flight reached destination
                        if self._safe_int(row.get('DIV_REACHED_DEST')):
                            c['div_reached_dest'] += 1
                    else:
                        # Flight arrived
                        c['ontime_arrived'] += 1
                        
                        arr_del15 = self._safe_int(row.get('ARR_DEL15'))
                        if arr_del15:
                            c['ontime_delayed'] += 1
                        else:
                            c['ontime_on_time'] += 1
                        
                        # Accumulate delay minutes
                        dep_delay = self._safe_int(row.get('DEP_DELAY_NEW'))
                        arr_delay = self._safe_int(row.get('ARR_DELAY_NEW'))
                        c['delay_total_dep_mins'] += dep_delay
                        c['delay_total_arr_mins'] += arr_delay
                        
                        # Delay causes
                        c['delay_carrier_mins'] += self._safe_int(row.get('CARRIER_DELAY'))
                        c['delay_weather_mins'] += self._safe_int(row.get('WEATHER_DELAY'))
                        c['delay_nas_mins'] += self._safe_int(row.get('NAS_DELAY'))
                        c['delay_security_mins'] += self._safe_int(row.get('SECURITY_DELAY'))
                        c['delay_late_aircraft_mins'] += self._safe_int(row.get('LATE_AIRCRAFT_DELAY'))
                        
                        # Taxi times
                        c['taxi_out_total'] += self._safe_int(row.get('TAXI_OUT'))
                        c['taxi_in_total'] += self._safe_int(row.get('TAXI_IN'))
                    
                    file_rows += 1
                    if file_rows % 500000 == 0:
                        print(f"    {file_rows:,} rows...")
            
            print(f"    {file_rows:,} rows processed")
            total_rows += file_rows
        
        print(f"  Processed {total_rows:,} total on-time rows")
    
    def _process_reporting_ontime(self, files: list) -> None:
        """Fallback: Process Reporting Carrier On-Time data (no marketing carrier)."""
        total_rows = 0
        for filepath in sorted(files):
            filename = os.path.basename(filepath)
            print(f"  Processing {filename}...")
            
            file_rows = 0
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    origin = row.get('ORIGIN', '').strip()
                    dest = row.get('DEST', '').strip()
                    carrier = row.get('OP_UNIQUE_CARRIER', '').strip()
                    
                    if not origin or not dest or not carrier:
                        continue
                    if origin not in self.airports or dest not in self.airports:
                        continue
                    
                    route = self.routes[origin][dest]
                    if carrier not in route['carriers']:
                        route['carriers'][carrier] = self._new_carrier_record('')
                    
                    c = route['carriers'][carrier]
                    c['ontime_flights'] += 1
                    
                    cancelled = self._safe_int(row.get('CANCELLED'))
                    diverted = self._safe_int(row.get('DIVERTED'))
                    
                    if cancelled:
                        c['ontime_cancelled'] += 1
                        cancel_code = row.get('CANCELLATION_CODE', '').strip()
                        if cancel_code == 'A':
                            c['cancel_carrier'] += 1
                        elif cancel_code == 'B':
                            c['cancel_weather'] += 1
                        elif cancel_code == 'C':
                            c['cancel_nas'] += 1
                        elif cancel_code == 'D':
                            c['cancel_security'] += 1
                    elif diverted:
                        c['ontime_diverted'] += 1
                    else:
                        c['ontime_arrived'] += 1
                        
                        arr_del15 = self._safe_int(row.get('ARR_DEL15'))
                        if arr_del15:
                            c['ontime_delayed'] += 1
                        else:
                            c['ontime_on_time'] += 1
                        
                        c['delay_total_dep_mins'] += self._safe_int(row.get('DEP_DELAY_NEW'))
                        c['delay_total_arr_mins'] += self._safe_int(row.get('ARR_DELAY_NEW'))
                        c['delay_carrier_mins'] += self._safe_int(row.get('CARRIER_DELAY'))
                        c['delay_weather_mins'] += self._safe_int(row.get('WEATHER_DELAY'))
                        c['delay_nas_mins'] += self._safe_int(row.get('NAS_DELAY'))
                        c['delay_security_mins'] += self._safe_int(row.get('SECURITY_DELAY'))
                        c['delay_late_aircraft_mins'] += self._safe_int(row.get('LATE_AIRCRAFT_DELAY'))
                    
                    file_rows += 1
                    if file_rows % 500000 == 0:
                        print(f"    {file_rows:,} rows...")
            
            print(f"    {file_rows:,} rows processed")
            total_rows += file_rows
        
        print(f"  Processed {total_rows:,} total on-time rows")
    

    def process_db1b_data(self) -> None:
        """Process DB1B Market data for fare information."""
        print("Processing DB1B Market data...")
        files = self._get_files('db1b_market')
        
        if not files:
            print("  No DB1B Market files found, skipping fare data")
            return
        
        print(f"  Found {len(files)} DB1B Market files")
        
        # Aggregate fares in memory: {(origin, dest, carrier, year, quarter): {passengers, total_fare, distance}}
        fare_data = {}
        total_rows = 0
        
        for filepath in files:
            filename = os.path.basename(filepath)
            print(f"  Processing {filename}...")
            
            file_rows = 0
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    origin = row.get('ORIGIN', '').strip()
                    dest = row.get('DEST', '').strip()
                    carrier = row.get('REPORTING_CARRIER', '').strip()
                    
                    if not origin or not dest or not carrier:
                        continue
                    
                    year = self._safe_int(row.get('YEAR'))
                    quarter = self._safe_int(row.get('QUARTER'))
                    passengers = self._safe_float(row.get('PASSENGERS', 0))
                    fare = self._safe_float(row.get('MARKET_FARE', 0))
                    distance = self._safe_float(row.get('MARKET_DISTANCE', 0))
                    
                    if passengers <= 0 or fare <= 0:
                        continue
                    
                    key = (origin, dest, carrier, year, quarter)
                    if key not in fare_data:
                        fare_data[key] = {'passengers': 0, 'total_fare': 0, 'distance': distance}
                    
                    fare_data[key]['passengers'] += passengers
                    fare_data[key]['total_fare'] += fare * passengers
                    
                    file_rows += 1
                    if file_rows % 1000000 == 0:
                        print(f"    {file_rows:,} rows...")
            
            print(f"    {file_rows:,} rows processed")
            total_rows += file_rows
        
        print(f"  Processed {total_rows:,} total DB1B rows")
        print(f"  Saving {len(fare_data):,} fare records...")
        
        # Get route IDs and insert
        saved = 0
        for (origin, dest, carrier, year, quarter), data in fare_data.items():
            route = self.db.execute_one(
                "SELECT id FROM routes WHERE origin=%s AND dest=%s", (origin, dest)
            )
            if not route:
                continue
            
            # DB1B is 10% sample, multiply passengers by 10
            passengers = int(data['passengers'] * 10)
            avg_fare = data['total_fare'] / data['passengers'] if data['passengers'] > 0 else 0
            distance = data['distance']
            avg_fare_per_mile = avg_fare / distance if distance > 0 else 0
            
            query = """
                INSERT INTO route_fares (route_id, carrier_code, year, quarter, passengers, avg_fare, avg_fare_per_mile)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    passengers=VALUES(passengers), avg_fare=VALUES(avg_fare), avg_fare_per_mile=VALUES(avg_fare_per_mile)
            """
            self.db.execute_write(query, (route['id'], carrier, year, quarter, passengers, avg_fare, avg_fare_per_mile))
            saved += 1
            
            if saved % 10000 == 0:
                print(f"    Saved {saved:,} fare records...")
        
        print(f"  Saved {saved:,} total fare records")

    def _hhmm_to_mins(self, val: str):
        """Convert HHMM string/int to minutes from midnight. Returns None if invalid."""
        try:
            v = int(val)
            if v < 0 or v > 2400:
                return None
            h = v // 100
            m = v % 100
            if h > 23 or m > 59:
                return None
            return h * 60 + m
        except (ValueError, TypeError):
            return None

    def process_schedules_data(self) -> None:
        """Process On-Time CSV files to build route_schedules table."""
        print("Processing schedules data from On-Time files...")

        pattern = os.path.join(self.data_dir, self.patterns['ontime'])
        files = sorted(glob.glob(pattern))

        if not files:
            print("  No on-time files found for schedule processing")
            return

        # Need airports dict to filter; load from DB if not already populated
        if not self.airports:
            print("  Loading airports from DB for filtering...")
            rows = self.db.execute("SELECT iata FROM airports")
            for row in rows:
                self.airports[row['iata']] = True

        print(f"  Found {len(files)} on-time files")

        # Key: (origin, dest, carrier_code, flight_number, day_of_week)
        schedules = {}
        total_rows = 0

        for filepath in files:
            filename = os.path.basename(filepath)
            print(f"  Processing {filename}...")
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    origin = row.get('ORIGIN', '').strip()
                    dest = row.get('DEST', '').strip()
                    carrier = row.get('MKT_UNIQUE_CARRIER', '').strip()
                    flight_num = row.get('MKT_CARRIER_FL_NUM', '').strip()
                    dow = self._safe_int(row.get('DAY_OF_WEEK'))

                    if not origin or not dest or not carrier or not flight_num or not dow:
                        continue
                    if origin not in self.airports or dest not in self.airports:
                        continue

                    crs_dep = row.get('CRS_DEP_TIME', '').strip()
                    crs_arr = row.get('CRS_ARR_TIME', '').strip()
                    dep_mins = self._hhmm_to_mins(crs_dep)
                    arr_mins = self._hhmm_to_mins(crs_arr)
                    if dep_mins is None or arr_mins is None:
                        continue

                    key = (origin, dest, carrier, flight_num, dow)
                    if key not in schedules:
                        schedules[key] = {
                            'dep_sum': 0, 'arr_sum': 0, 'count': 0,
                            'delay_sum': 0.0, 'delay_count': 0,
                        }

                    s = schedules[key]
                    s['dep_sum'] += dep_mins
                    s['arr_sum'] += arr_mins
                    s['count'] += 1

                    cancelled = self._safe_int(row.get('CANCELLED'))
                    if not cancelled:
                        dep_delay = self._safe_float(row.get('DEP_DELAY') or '0')
                        s['delay_sum'] += dep_delay
                        s['delay_count'] += 1

                    total_rows += 1

        print(f"  Aggregated {len(schedules):,} combinations from {total_rows:,} rows")

        # Need carriers dict for names; load from DB if not populated
        if not self.carriers:
            rows = self.db.execute("SELECT code, name FROM carriers")
            self.carriers = {r['code']: r['name'] for r in rows}

        # Clear and repopulate
        self.db.execute_write("TRUNCATE TABLE route_schedules")

        inserted = 0
        batch = []
        for (origin, dest, carrier, flight_num, dow), s in schedules.items():
            if s['count'] < 4:
                continue
            avg_dep = round(s['dep_sum'] / s['count'])
            avg_arr = round(s['arr_sum'] / s['count'])
            avg_delay = round(s['delay_sum'] / s['delay_count'], 1) if s['delay_count'] > 0 else None
            carrier_name = self.carriers.get(carrier, '')
            batch.append((origin, dest, carrier, carrier_name, flight_num, dow, avg_dep, avg_arr, s['count'], avg_delay))

            if len(batch) >= 1000:
                self.db.execute_many("""
                    INSERT INTO route_schedules
                    (origin, dest, carrier_code, carrier_name, flight_number, day_of_week,
                     typical_dep_time, typical_arr_time, frequency, avg_delay)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    ON DUPLICATE KEY UPDATE
                        carrier_name=VALUES(carrier_name),
                        typical_dep_time=VALUES(typical_dep_time),
                        typical_arr_time=VALUES(typical_arr_time),
                        frequency=VALUES(frequency),
                        avg_delay=VALUES(avg_delay)
                """, batch)
                inserted += len(batch)
                batch = []

        if batch:
            self.db.execute_many("""
                INSERT INTO route_schedules
                (origin, dest, carrier_code, carrier_name, flight_number, day_of_week,
                 typical_dep_time, typical_arr_time, frequency, avg_delay)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    carrier_name=VALUES(carrier_name),
                    typical_dep_time=VALUES(typical_dep_time),
                    typical_arr_time=VALUES(typical_arr_time),
                    frequency=VALUES(frequency),
                    avg_delay=VALUES(avg_delay)
            """, batch)
            inserted += len(batch)

        print(f"  Inserted {inserted:,} schedule records (4+ occurrences)")

    def _new_carrier_record(self, name: str) -> dict:
        """Create a new carrier record with all fields initialized."""
        return {
            'name': name,
            'marketing_carrier': None,
            'marketing_name': None,
            'branded_code_share': False,
            # T-100 fields
            'passengers': 0, 'freight': 0, 'mail': 0,
            'departures_scheduled': 0, 'departures_performed': 0,
            'seats': 0, 'payload': 0, 'air_time': 0, 'ramp_time': 0,
            'aircraft_types': {},
            # On-time fields
            'ontime_flights': 0, 'ontime_arrived': 0,
            'ontime_on_time': 0, 'ontime_delayed': 0,
            'ontime_cancelled': 0, 'ontime_diverted': 0,
            'delay_total_dep_mins': 0, 'delay_total_arr_mins': 0,
            'delay_carrier_mins': 0, 'delay_weather_mins': 0,
            'delay_nas_mins': 0, 'delay_security_mins': 0,
            'delay_late_aircraft_mins': 0,
            'taxi_out_total': 0, 'taxi_in_total': 0,
            'cancel_carrier': 0, 'cancel_weather': 0,
            'cancel_nas': 0, 'cancel_security': 0,
            'div_reached_dest': 0,
        }
    
    def _resolve_marketing_names(self) -> None:
        """Look up marketing carrier names from carriers table."""
        print("Resolving marketing carrier names...")
        for origin, destinations in self.routes.items():
            for dest, data in destinations.items():
                for carrier_code, c in data['carriers'].items():
                    mkt = c.get('marketing_carrier')
                    if mkt and mkt in self.carriers:
                        c['marketing_name'] = self.carriers[mkt]
                    elif mkt:
                        # Marketing carrier not in our carrier list, use code
                        c['marketing_name'] = mkt
    
    def save_to_database(self) -> None:
        """Save processed route data to MySQL."""
        print("Saving routes to database...")

        # Resolve marketing carrier names first
        self._resolve_marketing_names()

        route_count = 0
        route_carrier_count = 0
        route_counts = defaultdict(int)

        # Minimum annual departures to qualify as scheduled service (~weekly).
        # T-100 data covers annual totals, so 50 ≈ one flight per week.
        # Ontime data alone is not sufficient — carriers must have real T-100 volume.
        MIN_DEPARTURES = 50

        for origin, destinations in self.routes.items():
            for dest, data in destinations.items():
                qualified = {
                    code: c for code, c in data['carriers'].items()
                    if c['departures_performed'] >= MIN_DEPARTURES
                }
                if not qualified:
                    continue

                # Route-level passenger/freight check against qualified carriers only
                if data['passengers'] == 0 and data['freight'] == 0:
                    continue

                carrier_count = len(qualified)
                
                query = """
                    INSERT INTO routes (origin, dest, distance, passengers, freight, mail, carrier_count)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        distance=VALUES(distance), passengers=VALUES(passengers),
                        freight=VALUES(freight), mail=VALUES(mail), carrier_count=VALUES(carrier_count)
                """
                self.db.execute_write(query, (
                    origin, dest, data['distance'], data['passengers'],
                    data['freight'], data['mail'], carrier_count
                ))
                
                route_id = self.db.execute_one(
                    "SELECT id FROM routes WHERE origin=%s AND dest=%s", (origin, dest)
                )['id']
                
                for carrier_code, c in qualified.items():
                    aircraft_json = json.dumps(c['aircraft_types'])
                    query = """
                        INSERT INTO route_carriers 
                        (route_id, carrier_code, carrier_name, marketing_carrier, marketing_name, branded_code_share,
                         passengers, freight, mail, departures_scheduled, departures_performed, 
                         seats, payload, air_time, ramp_time, aircraft_types,
                         ontime_flights, ontime_arrived, ontime_on_time, ontime_delayed,
                         ontime_cancelled, ontime_diverted,
                         delay_total_dep_mins, delay_total_arr_mins,
                         delay_carrier_mins, delay_weather_mins, delay_nas_mins,
                         delay_security_mins, delay_late_aircraft_mins,
                         taxi_out_total, taxi_in_total,
                         cancel_carrier, cancel_weather, cancel_nas, cancel_security,
                         div_reached_dest)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            carrier_name=VALUES(carrier_name),
                            marketing_carrier=VALUES(marketing_carrier),
                            marketing_name=VALUES(marketing_name),
                            branded_code_share=VALUES(branded_code_share),
                            passengers=VALUES(passengers), freight=VALUES(freight), mail=VALUES(mail),
                            departures_scheduled=VALUES(departures_scheduled),
                            departures_performed=VALUES(departures_performed),
                            seats=VALUES(seats), payload=VALUES(payload),
                            air_time=VALUES(air_time), ramp_time=VALUES(ramp_time),
                            aircraft_types=VALUES(aircraft_types),
                            ontime_flights=VALUES(ontime_flights),
                            ontime_arrived=VALUES(ontime_arrived),
                            ontime_on_time=VALUES(ontime_on_time),
                            ontime_delayed=VALUES(ontime_delayed),
                            ontime_cancelled=VALUES(ontime_cancelled),
                            ontime_diverted=VALUES(ontime_diverted),
                            delay_total_dep_mins=VALUES(delay_total_dep_mins),
                            delay_total_arr_mins=VALUES(delay_total_arr_mins),
                            delay_carrier_mins=VALUES(delay_carrier_mins),
                            delay_weather_mins=VALUES(delay_weather_mins),
                            delay_nas_mins=VALUES(delay_nas_mins),
                            delay_security_mins=VALUES(delay_security_mins),
                            delay_late_aircraft_mins=VALUES(delay_late_aircraft_mins),
                            taxi_out_total=VALUES(taxi_out_total),
                            taxi_in_total=VALUES(taxi_in_total),
                            cancel_carrier=VALUES(cancel_carrier),
                            cancel_weather=VALUES(cancel_weather),
                            cancel_nas=VALUES(cancel_nas),
                            cancel_security=VALUES(cancel_security),
                            div_reached_dest=VALUES(div_reached_dest)
                    """
                    self.db.execute_write(query, (
                        route_id, carrier_code, c['name'],
                        c.get('marketing_carrier'), c.get('marketing_name'),
                        c.get('branded_code_share', False),
                        c['passengers'], c['freight'], c['mail'],
                        c['departures_scheduled'], c['departures_performed'],
                        c['seats'], c['payload'], c['air_time'], c['ramp_time'],
                        aircraft_json,
                        c['ontime_flights'], c['ontime_arrived'],
                        c['ontime_on_time'], c['ontime_delayed'],
                        c['ontime_cancelled'], c['ontime_diverted'],
                        c['delay_total_dep_mins'], c['delay_total_arr_mins'],
                        c['delay_carrier_mins'], c['delay_weather_mins'],
                        c['delay_nas_mins'], c['delay_security_mins'],
                        c['delay_late_aircraft_mins'],
                        c['taxi_out_total'], c['taxi_in_total'],
                        c['cancel_carrier'], c['cancel_weather'],
                        c['cancel_nas'], c['cancel_security'],
                        c['div_reached_dest']
                    ))
                    route_carrier_count += 1
                
                route_count += 1
                route_counts[origin] += 1
                route_counts[dest] += 1
                
                if route_count % 5000 == 0:
                    print(f"  Saved {route_count:,} routes...")
        
        print("Updating airport route counts...")
        for iata, count in route_counts.items():
            self.db.execute_write(
                "UPDATE airports SET route_count = %s WHERE iata = %s",
                (count, iata)
            )
        
        self.db.execute_write(
            "INSERT INTO stats (stat_key, stat_value) VALUES (%s, %s) ON DUPLICATE KEY UPDATE stat_value=VALUES(stat_value)",
            ('total_routes', route_count)
        )
        self.db.execute_write(
            "INSERT INTO stats (stat_key, stat_value) VALUES (%s, %s) ON DUPLICATE KEY UPDATE stat_value=VALUES(stat_value)",
            ('total_airports', len(route_counts))
        )
        
        print(f"  Saved {route_count:,} routes with {route_carrier_count:,} carrier records")
    
    def process_employee_data(self) -> None:
        """Load annual employee counts by role from BTS Form 41 Schedule P-10.
        Filters to ENTITY='D' (domestic) to avoid double-counting regional/international."""
        pattern = os.path.join(self.data_dir, 'Air Carrier Financial Reports - Schedule P-10 - *.csv')
        files = sorted(glob.glob(pattern))
        if not files:
            print("  No P-10 employee files found, skipping")
            return
        print(f"Processing {len(files)} P-10 employee file(s)...")

        rows = []
        for filepath in files:
            with open(filepath, newline='', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('ENTITY', '').strip() != 'D':
                        continue
                    other = (
                        self._safe_int(row.get('TRAINEES_INTRUCTOR', 0)) +
                        self._safe_int(row.get('STATISTICAL', 0)) +
                        self._safe_int(row.get('TRAFFIC_SOLICITERS', 0)) +
                        self._safe_int(row.get('OTHER', 0)) +
                        self._safe_int(row.get('TRANSPORT_RELATED', 0))
                    )
                    rows.append((
                        row['UNIQUE_CARRIER'].strip(),
                        self._safe_int(row.get('YEAR', 0)),
                        self._safe_int(row.get('TOTAL', 0)),
                        self._safe_int(row.get('PILOTS_COPILOTS', 0)),
                        self._safe_int(row.get('OTHER_FLT_PERS', 0)),
                        self._safe_int(row.get('MAINTENANCE', 0)),
                        self._safe_int(row.get('PASSENGER_HANDLING', 0)),
                        self._safe_int(row.get('CARGO_HANDLING', 0)),
                        self._safe_int(row.get('GENERAL_MANAGE', 0)),
                        self._safe_int(row.get('PASS_GEN_SVC_ADMIN', 0)),
                        other,
                    ))

        if rows:
            self.db.execute_many("""
                INSERT INTO carrier_employees
                    (carrier_code, year, emp_total, pilots, other_flight, maintenance,
                     passenger_handling, cargo_handling, general_management, pass_gen_svc, other_employees)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    emp_total=VALUES(emp_total), pilots=VALUES(pilots),
                    other_flight=VALUES(other_flight), maintenance=VALUES(maintenance),
                    passenger_handling=VALUES(passenger_handling), cargo_handling=VALUES(cargo_handling),
                    general_management=VALUES(general_management), pass_gen_svc=VALUES(pass_gen_svc),
                    other_employees=VALUES(other_employees)
            """, rows)
            print(f"  Loaded {len(rows):,} carrier-year employee records")

    def process_financial_data(self) -> None:
        """Load quarterly salary/expense (P-6) and balance sheet (B-1) data.
        Filters to REGION='D' (domestic) to avoid double-counting.
        All monetary values stored as-is (thousands USD)."""

        # ── P-6: Salary and operating expense ────────────────────────────────
        pattern = os.path.join(self.data_dir, 'Air Carrier Financial Reports - Schedule P-6 - *.csv')
        files = sorted(glob.glob(pattern))
        print(f"Processing {len(files)} P-6 salary file(s)...")
        p6_rows = []
        for filepath in files:
            with open(filepath, newline='', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('REGION', '').strip() != 'D':
                        continue
                    sal = self._safe_float(row.get('SALARIES', 0))
                    ben = self._safe_float(row.get('BENEFITS', 0))
                    p6_rows.append((
                        row['UNIQUE_CARRIER'].strip(),
                        self._safe_int(row.get('YEAR', 0)),
                        self._safe_int(row.get('QUARTER', 0)),
                        sal,
                        self._safe_float(row.get('SALARIES_FLIGHT', 0)),
                        self._safe_float(row.get('SALARIES_MAINT', 0)),
                        self._safe_float(row.get('SALARIES_TRAFFIC', 0)),
                        self._safe_float(row.get('SALARIES_MGT', 0)),
                        self._safe_float(row.get('SALARIES_OTHER', 0)),
                        ben,
                        self._safe_float(row.get('BENEFITS_PERSONNEL', 0)),
                        self._safe_float(row.get('BENEFITS_PENSIONS', 0)),
                        sal + ben,
                        self._safe_float(row.get('OP_EXPENSE', 0)),
                        self._safe_float(row.get('AIRCRAFT_FUEL', 0)),
                    ))
        if p6_rows:
            self.db.execute_many("""
                INSERT INTO carrier_financials
                    (carrier_code, year, quarter,
                     salaries_total, salaries_flight, salaries_maintenance,
                     salaries_traffic, salaries_management, salaries_other,
                     benefits_total, benefits_personnel, benefits_pensions,
                     total_compensation, operating_expense, aircraft_fuel)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    salaries_total=VALUES(salaries_total),
                    salaries_flight=VALUES(salaries_flight),
                    salaries_maintenance=VALUES(salaries_maintenance),
                    salaries_traffic=VALUES(salaries_traffic),
                    salaries_management=VALUES(salaries_management),
                    salaries_other=VALUES(salaries_other),
                    benefits_total=VALUES(benefits_total),
                    benefits_personnel=VALUES(benefits_personnel),
                    benefits_pensions=VALUES(benefits_pensions),
                    total_compensation=VALUES(total_compensation),
                    operating_expense=VALUES(operating_expense),
                    aircraft_fuel=VALUES(aircraft_fuel)
            """, p6_rows)
            print(f"  Loaded {len(p6_rows):,} carrier-quarter P-6 records")

        # ── B-1: Balance sheet ────────────────────────────────────────────────
        pattern = os.path.join(self.data_dir, 'Air Carrier Financial Reports - Schedule B-1 - *.csv')
        files = sorted(glob.glob(pattern))
        print(f"Processing {len(files)} B-1 balance sheet file(s)...")
        b1_rows = []
        for filepath in files:
            with open(filepath, newline='', encoding='utf-8-sig') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    if row.get('REGION', '').strip() != 'D':
                        continue
                    b1_rows.append((
                        self._safe_float(row.get('CASH', 0)),
                        self._safe_float(row.get('ASSETS', 0)),
                        self._safe_float(row.get('LONG_TERM_DEBT', 0)),
                        self._safe_float(row.get('CURR_LIABILITIES', 0)),
                        self._safe_float(row.get('SH_HLD_EQUIT_NET', 0)),
                        row['UNIQUE_CARRIER'].strip(),
                        self._safe_int(row.get('YEAR', 0)),
                        self._safe_int(row.get('QUARTER', 0)),
                    ))
        if b1_rows:
            self.db.execute_many("""
                INSERT INTO carrier_financials
                    (carrier_code, year, quarter,
                     cash_position, total_assets, long_term_debt,
                     current_liabilities, shareholders_equity)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    cash_position=VALUES(cash_position),
                    total_assets=VALUES(total_assets),
                    long_term_debt=VALUES(long_term_debt),
                    current_liabilities=VALUES(current_liabilities),
                    shareholders_equity=VALUES(shareholders_equity)
            """, [(r[5], r[6], r[7], r[0], r[1], r[2], r[3], r[4]) for r in b1_rows])
            print(f"  Loaded {len(b1_rows):,} carrier-quarter B-1 records")

        # ── P-1-1 / P-1-2: Income statement ──────────────────────────────────
        # P-1-1: smaller carriers (OP_REVENUE, OP_PROFIT)
        # P-1-2: large carriers  (OP_REVENUES, OP_PROFIT_LOSS)
        income_rows = []
        for sched, rev_col, profit_col in [
            ('P-1-1', 'OP_REVENUE',  'OP_PROFIT'),
            ('P-1-2', 'OP_REVENUES', 'OP_PROFIT_LOSS'),
        ]:
            pattern = os.path.join(self.data_dir, f'Air Carrier Financial Reports - Schedule {sched} - *.csv')
            files = sorted(glob.glob(pattern))
            print(f"Processing {len(files)} {sched} income statement file(s)...")
            for filepath in files:
                with open(filepath, newline='', encoding='utf-8-sig') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        if row.get('REGION', '').strip() != 'D':
                            continue
                        income_rows.append((
                            row['UNIQUE_CARRIER'].strip(),
                            self._safe_int(row.get('YEAR', 0)),
                            self._safe_int(row.get('QUARTER', 0)),
                            self._safe_float(row.get(rev_col, 0)),
                            self._safe_float(row.get(profit_col, 0)),
                            self._safe_float(row.get('NET_INCOME', 0)),
                        ))
        if income_rows:
            self.db.execute_many("""
                INSERT INTO carrier_financials
                    (carrier_code, year, quarter,
                     op_revenue, op_profit, net_income)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    op_revenue=VALUES(op_revenue),
                    op_profit=VALUES(op_profit),
                    net_income=VALUES(net_income)
            """, income_rows)
            print(f"  Loaded {len(income_rows):,} carrier-quarter income statement records")

    def process_hub_data(self) -> None:
        """Aggregate top-8 hub airports per carrier from Q2-2025 passenger departures.
        Filters to YEAR=2025, MONTH in (4,5,6), PASSENGERS > 0, CLASS='F' to capture only
        current scheduled passenger operations, excluding cargo and charter flights."""
        pattern = os.path.join(self.data_dir, 'Air Carrier Statistics - All Carriers - T-100 Segment - *.csv')
        files = sorted(glob.glob(pattern))
        print(f"Processing {len(files)} T-100 Segment file(s) for hub data (Q2-2025 pax only)...")

        # Key is carrier only — Q2-2025 snapshot, no year dimension needed
        deps: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        for filepath in files:
            with open(filepath, newline='', encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):
                    if row.get('CLASS', '') != 'F':
                        continue
                    if self._safe_int(row.get('YEAR', 0)) != 2025:
                        continue
                    if self._safe_int(row.get('MONTH', 0)) not in (4, 5, 6):
                        continue
                    if self._safe_float(row.get('PASSENGERS', 0)) <= 0:
                        continue
                    carrier = row.get('UNIQUE_CARRIER', '').strip()
                    origin  = row.get('ORIGIN', '').strip()
                    d       = self._safe_int(row.get('DEPARTURES_PERFORMED', 0))
                    if carrier and origin and d > 0:
                        deps[carrier][origin] += d

        # Store as year=2025 (the Q2 snapshot year).
        # Only include airports with >= 12 departures in the quarter (roughly weekly service).
        rows = []
        for carrier, airports in deps.items():
            qualified = {apt: d for apt, d in airports.items() if d >= 12}
            total = sum(qualified.values())
            if not total:
                continue
            for rank, (apt, d) in enumerate(
                sorted(qualified.items(), key=lambda x: -x[1])[:8], 1
            ):
                rows.append((carrier, 2025, rank, apt, d, round(d / total * 100, 1)))

        if rows:
            self.db.execute_many("""
                INSERT INTO carrier_hubs
                    (carrier_code, year, hub_rank, airport_code, departures, pct_of_total)
                VALUES (%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    airport_code=VALUES(airport_code),
                    departures=VALUES(departures),
                    pct_of_total=VALUES(pct_of_total)
            """, rows)
            print(f"  Loaded Q2-2025 hub data for {len(deps):,} carriers")

    def process_network_data(self) -> None:
        """Count unique passenger origin-dest pairs per carrier from Q2-2025 T-100 Market.
        Filters to YEAR=2025, MONTH in (4,5,6), PASSENGERS > 0, CLASS='F' to show only
        current scheduled passenger routes, and requires >= 12 quarterly departures per
        route to exclude repositioning and charter flights."""
        pattern = os.path.join(self.data_dir, 'Air Carrier Statistics - All Carriers - T-100 Market - *.csv')
        files = sorted(glob.glob(pattern))
        print(f"Processing {len(files)} T-100 Market file(s) for network data (Q2-2025 pax only)...")

        # Track departures per (carrier, origin, dest) to apply frequency filter
        pair_deps: Dict[Any, int] = defaultdict(int)
        pair_meta: Dict[Any, dict] = {}
        for filepath in files:
            with open(filepath, newline='', encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):
                    if row.get('CLASS', '') != 'F':
                        continue
                    if self._safe_int(row.get('YEAR', 0)) != 2025:
                        continue
                    if self._safe_int(row.get('MONTH', 0)) not in (4, 5, 6):
                        continue
                    if self._safe_float(row.get('PASSENGERS', 0)) <= 0:
                        continue
                    carrier   = row.get('UNIQUE_CARRIER', '').strip()
                    origin    = row.get('ORIGIN', '').strip()
                    dest      = row.get('DEST', '').strip()
                    o_country = row.get('ORIGIN_COUNTRY', '').strip()
                    d_country = row.get('DEST_COUNTRY', '').strip()
                    if not (carrier and origin and dest):
                        continue
                    key = (carrier, origin, dest)
                    pair_deps[key] += self._safe_int(row.get('DEPARTURES_PERFORMED', 0))
                    if key not in pair_meta:
                        pair_meta[key] = {'o_country': o_country, 'd_country': d_country}

        # Only count routes with >= 12 quarterly departures (roughly weekly service)
        net: Dict[str, Any] = defaultdict(lambda: {'dom': set(), 'intl': set()})
        for (carrier, origin, dest), deps in pair_deps.items():
            if deps < 12:
                continue
            meta = pair_meta[(carrier, origin, dest)]
            pair = (origin, dest)
            bucket = net[carrier]
            if meta['o_country'] == 'US' and meta['d_country'] == 'US':
                bucket['dom'].add(pair)
            else:
                bucket['intl'].add(pair)

        rows = []
        for carrier, counts in net.items():
            dom  = len(counts['dom'])
            intl = len(counts['intl'])
            rows.append((carrier, 2025, dom, intl, dom + intl))

        if rows:
            self.db.execute_many("""
                INSERT INTO carrier_network
                    (carrier_code, year, domestic_routes, intl_routes, total_routes)
                VALUES (%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    domestic_routes=VALUES(domestic_routes),
                    intl_routes=VALUES(intl_routes),
                    total_routes=VALUES(total_routes)
            """, rows)
            print(f"  Loaded Q2-2025 network data for {len(rows):,} carriers")

    def process_fleet_data(self) -> None:
        """Count active operated aircraft per carrier/year from B-43."""
        pattern = os.path.join(self.data_dir, 'Air Carrier Financial Reports - Schedule B-43 - *.csv')
        files = sorted(glob.glob(pattern))
        print(f"Processing {len(files)} B-43 fleet file(s)...")

        fleet: Dict[Any, int] = defaultdict(int)
        for filepath in files:
            with open(filepath, newline='', encoding='utf-8-sig') as f:
                for row in csv.DictReader(f):
                    if row.get('AIRCRAFT_STATUS', '').upper() != 'O':
                        continue
                    if row.get('OPERATING_STATUS', '').upper() != 'Y':
                        continue
                    carrier = row.get('UNIQUE_CARRIER', '').strip()
                    year    = self._safe_int(row.get('YEAR', 0))
                    if carrier and year:
                        fleet[(carrier, year)] += 1

        rows = [(c, y, n) for (c, y), n in fleet.items()]
        if rows:
            self.db.execute_many("""
                INSERT INTO carrier_fleet (carrier_code, year, aircraft_count)
                VALUES (%s,%s,%s)
                ON DUPLICATE KEY UPDATE aircraft_count=VALUES(aircraft_count)
            """, rows)
            print(f"  Loaded fleet data for {len(rows):,} carrier-years")

    def process_carrier_attributes(self) -> None:
        """Load regional/cargo/charter carrier mappings (hardcoded)."""
        rows = []
        for code, info in REGIONAL_CARRIERS.items():
            rows.append((code, 'regional', info['feeds_to'], info['brand']))
        for code in CARGO_CARRIERS:
            rows.append((code, 'cargo', None, None))
        for code in CHARTER_CARRIERS:
            rows.append((code, 'charter', None, None))
        if rows:
            self.db.execute_many("""
                INSERT INTO carrier_attributes (carrier_code, carrier_type, feeds_to, brand)
                VALUES (%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    carrier_type=VALUES(carrier_type),
                    feeds_to=VALUES(feeds_to),
                    brand=VALUES(brand)
            """, rows)
            print(f"  Loaded {len(rows)} carrier attribute records")

    def build_fare_flags(self) -> None:
        """Set has_fares=True on airports that have outbound route_fares data."""
        print("Building airport fare flags...")
        self.db.execute_write("UPDATE airports SET has_fares = FALSE")
        updated = self.db.execute_write("""
            UPDATE airports a SET has_fares = TRUE
            WHERE EXISTS (
                SELECT 1 FROM routes r
                JOIN route_fares rf ON rf.route_id = r.id
                WHERE r.origin = a.iata
            )
        """)
        print(f"  Flagged {updated:,} airports with fare data")

    def process_all(self, truncate: bool = True) -> None:
        """Run full processing pipeline."""
        print("=" * 60)
        print("FlightConn Data Processor")
        print("=" * 60)

        if truncate:
            self.db.truncate_tables()

        self.load_airports()
        self.load_aircraft()
        self.load_carriers()

        self.process_market_data()
        self.process_segment_data()
        self.process_ontime_data()
        self.process_db1b_data()

        self.save_to_database()
        self.build_fare_flags()
        self.process_schedules_data()
        self.process_employee_data()
        self.process_financial_data()
        self.process_hub_data()
        self.process_network_data()
        self.process_fleet_data()
        self.process_carrier_attributes()
        
        stats = self.db.execute("SELECT stat_key, stat_value FROM stats")
        print("\n" + "=" * 60)
        print("Processing Complete!")
        print("=" * 60)
        for stat in stats:
            print(f"  {stat['stat_key']}: {stat['stat_value']:,}")


if __name__ == '__main__':
    processor = DataProcessor('Data')
    processor.process_all()
