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
        """Process T-100 Market data for passengers, freight, mail."""
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
        """Process T-100 Segment data for flights, seats, air time, aircraft."""
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
        
        for origin, destinations in self.routes.items():
            for dest, data in destinations.items():
                if data['passengers'] == 0 and data['freight'] == 0:
                    has_ontime = any(c.get('ontime_flights', 0) > 0 for c in data['carriers'].values())
                    if not has_ontime:
                        continue
                
                carrier_count = len(data['carriers'])
                
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
                
                for carrier_code, c in data['carriers'].items():
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
        
        stats = self.db.execute("SELECT stat_key, stat_value FROM stats")
        print("\n" + "=" * 60)
        print("Processing Complete!")
        print("=" * 60)
        for stat in stats:
            print(f"  {stat['stat_key']}: {stat['stat_value']:,}")


if __name__ == '__main__':
    processor = DataProcessor('Data')
    processor.process_all()
