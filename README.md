# FlightConn

Flight route visualization tool using BTS (Bureau of Transportation Statistics) T-100 aviation data. Shows passenger and cargo routes between airports with carrier details, aircraft types, and operational statistics.

**Stack:** Python Flask API + MySQL + Leaflet.js frontend, containerized with Docker Compose.

---

## Quick Start (New Server Deployment)

```bash
# 1. Clone/copy project to server
cd /
git clone <repo> flightconn   # or extract zip
cd /flightconn

# 2. Add CSV data files (see "Data Files" section below)
# Place all CSV files in /flightconn/api/Data/

# 3. Start containers
docker compose up -d --build

# 4. Wait for MySQL to initialize (~30 seconds)
docker compose logs -f db   # Watch for "ready for connections", then Ctrl+C

# 5. Load data into MySQL
docker exec flightconn-api python3 -u -c "
from Classes import DataProcessor
processor = DataProcessor('Data')
processor.process_all()
"

# 6. Access the app
# Frontend: http://your-server:8082
# API: http://your-server:8083/api
```

---

## Data Files

All CSV files go in `/flightconn/api/Data/`. The T-100 data is annual, so refresh yearly.

### Required Files

| File | Source | Notes |
|------|--------|-------|
| `Master Coord.csv` | Aviation Support Tables | Airport coordinates, ~4MB |
| `Aircraft Types.csv` | Aviation Support Tables | Aircraft codes, ~37KB |
| `Carrier Decode.csv` | Aviation Support Tables | Carrier info, ~370KB |
| `T-100 Market.csv` | T-100 Market (combined) | Passengers/freight by route, ~86MB |
| `T-100 Segment.csv` | T-100 Segment (combined) | Flights/seats/aircraft by route, ~178MB |

### Download Instructions

#### 1. Aviation Support Tables (airports, aircraft, carriers)

Go to: https://www.transtats.bts.gov/Tables.asp?QO_VQ=EEE&QO_anzr=Nv4vnqvba%FDFhccb4g%FDGnoyr5765LW8fVDN

Download these lookup tables:
- **Master Coordinate** → Save as `Master Coord.csv`
- **Aircraft Types** → Save as `Aircraft Types.csv`
- **Carrier Decode** → Save as `Carrier Decode.csv`

#### 2. T-100 Market Data (passengers, freight, mail)

Go to: https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoession_VQ=GDI&QO_fu146_anzr=Nv4%20Pn44vr45

**Settings:**
- Filter Geography: All
- Filter Year: Select the year you want
- Filter Period: All months (or select all 12)

**Required columns (check these):**
- PASSENGERS
- FREIGHT  
- MAIL
- DISTANCE
- UNIQUE_CARRIER
- UNIQUE_CARRIER_NAME
- ORIGIN
- DEST

Click Download → Save as `T-100 Market.csv`

**Note:** You'll need to download BOTH domestic and international, then combine them:
- Download domestic T-100 Market
- Download international T-100 Market  
- Combine into single `T-100 Market.csv` (keep header from first file only)

#### 3. T-100 Segment Data (flights, seats, aircraft)

Go to: https://www.transtats.bts.gov/DL_SelectFields.aspx?gnoession_VQ=GDH&QO_fu146_anzr=Nv4%20Pn44vr45

**Settings:**
- Filter Geography: All
- Filter Year: Select the year you want
- Filter Period: All months

**Required columns (check these):**
- DEPARTURES_SCHEDULED
- DEPARTURES_PERFORMED
- SEATS
- AIR_TIME
- AIRCRAFT_TYPE
- UNIQUE_CARRIER
- UNIQUE_CARRIER_NAME
- ORIGIN
- DEST

Click Download → Save as `T-100 Segment.csv`

**Note:** Same as Market - download domestic + international, combine into one file.

### Combining Domestic + International Files

```bash
# For Market data
head -1 T-100_Domestic_Market.csv > T-100\ Market.csv
tail -n +2 T-100_Domestic_Market.csv >> T-100\ Market.csv
tail -n +2 T-100_International_Market.csv >> T-100\ Market.csv

# For Segment data
head -1 T-100_Domestic_Segment.csv > T-100\ Segment.csv
tail -n +2 T-100_Domestic_Segment.csv >> T-100\ Segment.csv
tail -n +2 T-100_International_Segment.csv >> T-100\ Segment.csv
```

---

## Annual Data Refresh

Run this each year when new T-100 data is available (typically Q2 for previous year's data):

```bash
cd /flightconn

# 1. Download new CSV files and place in /flightconn/api/Data/
#    (see download instructions above)

# 2. Reload database (truncates existing data and reloads)
docker exec flightconn-api python3 -u -c "
from Classes import DataProcessor
processor = DataProcessor('Data')
processor.process_all()
"

# Processing takes ~5-10 minutes depending on server
# You'll see progress output as it runs
```

---

## Architecture

```
/flightconn/
├── docker-compose.yml          # Main compose (frontend + api + db)
├── README.md
├── frontend/
│   ├── Dockerfile              # nginx:alpine
│   ├── nginx.conf              # Proxies /api to backend
│   ├── index.html              # Leaflet map UI
│   └── config.js               # API URL config
└── api/
    ├── docker-compose.yml      # Standalone API (api + db only)
    ├── Dockerfile              # Python 3.11
    ├── requirements.txt
    ├── main.py                 # Flask app entry point
    ├── Data/
    │   ├── schema.sql          # MySQL schema (auto-loaded on first run)
    │   └── *.csv               # Data files (not in git)
    ├── Modules/
    │   ├── airports.py         # /api/airports endpoints
    │   ├── routes.py           # /api/routes endpoints
    │   └── carriers.py         # /api/carriers, /api/aircraft
    └── Classes/
        ├── Database.py         # MySQL connection manager
        └── DataProcessor.py    # CSV to MySQL loader
```

### Containers

| Container | Port | Description |
|-----------|------|-------------|
| flightconn-frontend | 8082 | nginx serving static files, proxies /api |
| flightconn-api | 8083 | Flask REST API |
| flightconn-db | 3307 | MySQL 8.0 |

### Volumes

- `flightconn_mysql` - MySQL data persistence

---

## API Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /api/stats` | Overall statistics |
| `GET /api/airports?q=&limit=` | List/search airports |
| `GET /api/airports/{iata}` | Airport details + stats |
| `GET /api/airports/{iata}/routes` | Routes from/to airport |
| `GET /api/routes?origin=&dest=` | List/filter routes |
| `GET /api/routes/{origin}/{dest}` | Route details |
| `GET /api/routes/{origin}/{dest}/carriers` | Carriers on route |
| `GET /api/routes/top?metric=passengers` | Top routes |
| `GET /api/carriers?q=` | List/search carriers |
| `GET /api/carriers/{code}` | Carrier details |
| `GET /api/carriers/{code}/routes` | Carrier's routes |
| `GET /api/aircraft?q=` | List aircraft types |
| `GET /health` | Health check |

---

## Common Operations

### View logs
```bash
docker compose logs -f           # All containers
docker compose logs -f api       # API only
docker compose logs -f db        # Database only
```

### Restart services
```bash
docker compose restart           # All
docker compose restart api       # API only
```

### Rebuild after code changes
```bash
docker compose up -d --build
```

### Access MySQL directly
```bash
docker exec -it flightconn-db mysql -u flightconn -pflightconn flightconn

# Example queries
SELECT COUNT(*) FROM routes;
SELECT COUNT(*) FROM airports WHERE route_count > 0;
SELECT * FROM stats;
```

### Check data counts
```bash
docker exec flightconn-api python3 -c "
from Classes import get_db
db = get_db()
print('Routes:', db.execute_one('SELECT COUNT(*) as c FROM routes')['c'])
print('Airports with routes:', db.execute_one('SELECT COUNT(*) as c FROM airports WHERE route_count > 0')['c'])
print('Carriers:', db.execute_one('SELECT COUNT(DISTINCT carrier_code) as c FROM route_carriers')['c'])
"
```

### Full reset (wipe database and reload)
```bash
docker compose down -v           # Remove volumes
docker compose up -d --build     # Recreate
# Wait for db to be healthy, then reload data
docker exec flightconn-api python3 -u -c "
from Classes import DataProcessor
processor = DataProcessor('Data')
processor.process_all()
"
```

---

## Troubleshooting

### "Failed to load airport: API error: 500"
Check API logs: `docker compose logs api`
Usually a SQL query issue - look for the specific error.

### Airports missing from map
The frontend requests `limit=2500` airports. If you have more airports with routes, increase the limit in `frontend/index.html` and rebuild.

### Data not loading (stuck at cursor)
Run with unbuffered output:
```bash
docker exec flightconn-api python3 -u -c "..."
```

### MySQL connection refused
Wait for healthcheck: `docker compose logs -f db`
MySQL takes ~30 seconds to initialize on first run.

### CSV encoding errors
Ensure files are UTF-8. Convert if needed:
```bash
iconv -f ISO-8859-1 -t UTF-8 input.csv > output.csv
```

---

## MySQL Credentials

| Setting | Value |
|---------|-------|
| Host | db (internal) / localhost:3307 (external) |
| User | flightconn |
| Password | flightconn |
| Database | flightconn |
| Root Password | rootpass |

---

## Performance Notes

- Initial data load: ~5-10 minutes
- Database size: ~500MB
- Frontend loads ~2000 airports on initial view
- Route queries are indexed on passengers, freight, origin, dest

---

## License

Data sourced from Bureau of Transportation Statistics (BTS), U.S. Department of Transportation.
https://www.transtats.bts.gov/
