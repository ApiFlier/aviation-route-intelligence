"""
DataProcessor for FlightConn
Processes BTS aviation data CSV files and loads them into MySQL.

Data Sources:
- Master Coord.csv - Airport coordinates and info
- Aircraft Types.csv - Aircraft code to name mapping  
- Carrier Decode.csv - Carrier info
- T-100 Market.csv - Passengers, freight, mail by route/carrier
- T-100 Segment.csv - Flights, seats, air time, aircraft by route/carrier
"""

import csv
import json
import os
from collections import defaultdict
from typing import Dict, Any, List

from .Database import get_db


class DataProcessor:
    def __init__(self, data_dir: str = 'Data'):
        self.data_dir = data_dir
        self.db = get_db()
        
        # File paths
        self.files = {
            'airports': 'Master Coord.csv',
            'aircraft': 'Aircraft Types.csv',
            'carriers': 'Carrier Decode.csv',
            'market': 'T-100 Market.csv',
            'segment': 'T-100 Segment.csv',
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
                
                self.airports[code] = True  # Track valid airports
                airports.append((
                    code,
                    row.get('DISPLAY_AIRPORT_NAME', '').strip(),
                    city_name,
                    state if country == 'US' else None,
                    country,
                    lat,
                    lon,
                    self._safe_int(row.get('AIRPORT_WAC')),
                    0  # route_count updated later
                ))
        
        # Batch insert
        query = """
            INSERT INTO airports (iata, name, city, state, country, lat, lon, wac, route_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                name=VALUES(name), city=VALUES(city), state=VALUES(state),
                country=VALUES(country), lat=VALUES(lat), lon=VALUES(lon), wac=VALUES(wac)
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
                    row.get('SHORT_NAME', '').strip(),
                    row.get('LONG_NAME', '').strip(),
                    row.get('MANUFACTURER', '').strip()
                ))
        
        query = """
            INSERT INTO aircraft (type_id, short_name, long_name, manufacturer)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                short_name=VALUES(short_name), long_name=VALUES(long_name), manufacturer=VALUES(manufacturer)
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
                        not has_end_date
                    )
        
        query = """
            INSERT INTO carriers (code, iata_code, name, airline_id, active)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                iata_code=VALUES(iata_code), name=VALUES(name), airline_id=VALUES(airline_id), active=VALUES(active)
        """
        self.db.execute_many(query, list(carriers.values()))
        self.carriers = {c[0]: c[2] for c in carriers.values()}
        print(f"  Loaded {len(carriers)} carriers")
        return len(carriers)
    
    def process_market_data(self) -> None:
        """Process T-100 Market data for passengers, freight, mail."""
        print("Processing T-100 Market data...")
        path = self._get_path('market')
        
        if not os.path.exists(path):
            print(f"  ERROR: {path} not found")
            return
        
        row_count = 0
        with open(path, 'r', encoding='utf-8') as f:
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
                    route['carriers'][carrier] = {
                        'name': carrier_name,
                        'passengers': 0, 'freight': 0, 'mail': 0,
                        'departures_scheduled': 0, 'departures_performed': 0,
                        'seats': 0, 'air_time': 0, 'aircraft_types': set()
                    }
                
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
        path = self._get_path('segment')
        
        if not os.path.exists(path):
            print(f"  WARNING: {path} not found, skipping segment data")
            return
        
        row_count = 0
        with open(path, 'r', encoding='utf-8') as f:
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
                air_time = self._safe_int(row.get('AIR_TIME'))
                aircraft_type = row.get('AIRCRAFT_TYPE', '').strip()
                carrier_name = row.get('UNIQUE_CARRIER_NAME', '').strip()
                
                route = self.routes[origin][dest]
                if carrier not in route['carriers']:
                    route['carriers'][carrier] = {
                        'name': carrier_name,
                        'passengers': 0, 'freight': 0, 'mail': 0,
                        'departures_scheduled': 0, 'departures_performed': 0,
                        'seats': 0, 'air_time': 0, 'aircraft_types': set()
                    }
                
                c = route['carriers'][carrier]
                c['departures_scheduled'] += departures_scheduled
                c['departures_performed'] += departures_performed
                c['seats'] += seats
                c['air_time'] += air_time
                if aircraft_type and departures_performed > 0:
                    c['aircraft_types'].add(aircraft_type)
                
                row_count += 1
                if row_count % 100000 == 0:
                    print(f"  Processed {row_count:,} segment rows...")
        
        print(f"  Processed {row_count:,} total segment rows")
    
    def save_to_database(self) -> None:
        """Save processed route data to MySQL."""
        print("Saving routes to database...")
        
        route_count = 0
        route_carrier_count = 0
        route_counts = defaultdict(int)  # Track routes per airport
        
        for origin, destinations in self.routes.items():
            for dest, data in destinations.items():
                if data['passengers'] == 0 and data['freight'] == 0:
                    continue
                
                carrier_count = len(data['carriers'])
                
                # Insert route
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
                
                # Get route ID
                route_id = self.db.execute_one(
                    "SELECT id FROM routes WHERE origin=%s AND dest=%s", (origin, dest)
                )['id']
                
                # Insert carriers
                for carrier_code, carrier_data in data['carriers'].items():
                    aircraft_json = json.dumps(sorted(list(carrier_data['aircraft_types'])))
                    query = """
                        INSERT INTO route_carriers 
                        (route_id, carrier_code, carrier_name, passengers, freight, mail,
                         departures_scheduled, departures_performed, seats, air_time, aircraft_types)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            carrier_name=VALUES(carrier_name), passengers=VALUES(passengers),
                            freight=VALUES(freight), mail=VALUES(mail),
                            departures_scheduled=VALUES(departures_scheduled),
                            departures_performed=VALUES(departures_performed),
                            seats=VALUES(seats), air_time=VALUES(air_time),
                            aircraft_types=VALUES(aircraft_types)
                    """
                    self.db.execute_write(query, (
                        route_id, carrier_code, carrier_data['name'],
                        carrier_data['passengers'], carrier_data['freight'], carrier_data['mail'],
                        carrier_data['departures_scheduled'], carrier_data['departures_performed'],
                        carrier_data['seats'], carrier_data['air_time'], aircraft_json
                    ))
                    route_carrier_count += 1
                
                route_count += 1
                route_counts[origin] += 1
                route_counts[dest] += 1
                
                if route_count % 5000 == 0:
                    print(f"  Saved {route_count:,} routes...")
        
        # Update airport route counts
        print("Updating airport route counts...")
        for iata, count in route_counts.items():
            self.db.execute_write(
                "UPDATE airports SET route_count = %s WHERE iata = %s",
                (count, iata)
            )
        
        # Save stats
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
        
        # Load reference data
        self.load_airports()
        self.load_aircraft()
        self.load_carriers()
        
        # Process route data
        self.process_market_data()
        self.process_segment_data()
        
        # Save to database
        self.save_to_database()
        
        # Summary
        stats = self.db.execute("SELECT stat_key, stat_value FROM stats")
        print("\n" + "=" * 60)
        print("Processing Complete!")
        print("=" * 60)
        for stat in stats:
            print(f"  {stat['stat_key']}: {stat['stat_value']:,}")


if __name__ == '__main__':
    processor = DataProcessor('Data')
    processor.process_all()
