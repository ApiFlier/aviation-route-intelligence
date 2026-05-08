# FlightConn

A self-hosted airline intelligence dashboard built from Bureau of Transportation Statistics (BTS) public datasets. Covers route maps, carrier analysis, fare trends, on-time performance, and airline financial health — all running locally in Docker.

---

## Quick Start

```bash
git clone https://github.com/ApiFlier/aviation-route-intelligence.git flightconn
cd flightconn
chmod +x setup.sh
./setup.sh
```

`setup.sh`:
- Generates `.env` automatically with random credentials (no manual config needed)
- Builds Docker containers (`flightconn-app`, `flightconn-db`)
- Creates or reuses the Docker-managed persistent volume `flightconn_mysql`
- Finds an available host port automatically (starting at 8082) and saves it to `.env`
- Seeds the database from `api/Data/db_backup.sql.gz` on first run
- Starts the app and prints the final URL

---

## What FlightConn Does

FlightConn is an airline route intelligence platform with two integrated modules:

**Route Intelligence Map** — An interactive map of the US aviation network. Search any airport to see its routes, carriers, passenger volumes, freight data, quarterly fare trends, and per-carrier on-time performance. Drill into a specific carrier on a route for delay cause breakdowns, aircraft types, and typical flight schedule tiles.

**Airline Health Dashboard** (`/career/`) — A multi-year view of every major US carrier's financial health, workforce composition, role-level compensation, fleet size, hub rankings, and route network trends — derived from BTS Form 41 filings.

---

## Key Features

- Interactive airport and route map with 2,500+ US airports and layered satellite/street basemaps
- Route drill-down: passengers, freight, seats, load factor, and carrier market share
- Quarterly fare analysis from BTS DB1B data with cheapest/peak quarter highlights
- Per-carrier on-time performance with delay cause breakdowns (carrier, weather, NAS, security)
- Typical flight schedule tiles derived from historical BTS on-time records
- Airline health scoring based on equity position and operational performance
- Workforce breakdown by role: pilots, flight attendants, maintenance, management, and more
- Annual compensation per role group derived from Form 41 P-6 salary data
- Fleet size trends, top hub airports, and domestic vs. international route splits
- Profitability trends: operating margin and net income over multiple years
- Filter and sort airlines by health score, carrier type, or employee count

---

## Route Intelligence Module

The map renders airports as scaled circles (larger = more routes) using Leaflet.js. Selecting an airport draws arcs to all destination airports, color-coded by route type (passenger, cargo, or mixed).

Clicking a route shows:
- Annual passenger and freight totals, flight count, seats, and load factor
- Airline breakdown with on-time badge and latest average fare
- Quarterly fare grid (best/peak/current quarter highlighted)
- A "This Month" fare tip based on historical BTS data

Clicking a carrier on that route shows:
- Scheduled vs. performed flights, cancellation rate
- On-time %, average arrival delay, average flight time
- Delay cause bar chart (carrier / weather / NAS / security / late aircraft)
- Aircraft types flown with departure counts
- Typical weekly schedule with per-flight average delay

---

## Airline Health Module

Available at `/career/`, the health dashboard shows every carrier that has both BTS financial and employee filings. Cards display a 0–100 health score, employee count, quarterly compensation, and an on-time performance bar.

Clicking a carrier opens a tabbed detail view:

| Tab | Contents |
|-----|----------|
| Workforce | Headcount breakdown by role, year-over-year trend chart |
| Compensation | Average annual pay per role group (pilots/FAs, maintenance, management) |
| Operations | Top hub airports, route network size, domestic/international split, fleet trend |
| Financials | Latest balance sheet, annual OpEx/compensation/fuel, operating margin, net income |
| On-Time | Flights tracked, on-time %, delayed, cancelled, diverted |

---

## Architecture

| Component | Technology | Container |
|-----------|------------|-----------|
| App / API | Python 3.11, Flask 3.0 | `flightconn-app` |
| Database | MySQL 8.0 | `flightconn-db` |

Flask serves the frontend as static files and exposes a REST API at `/api`. All route, carrier, fare, schedule, employee, and financial data lives in MySQL. No external services or API keys are required at runtime.

---

## Tech Stack

- **Backend:** Python 3.11, Flask 3.0, mysqlclient, flask-cors
- **Database:** MySQL 8.0
- **Frontend:** Vanilla JavaScript, Leaflet.js (interactive maps), Font Awesome (icons)
- **Infrastructure:** Docker, Docker Compose, Docker named volumes
- **Data:** Bureau of Transportation Statistics (BTS) — all public datasets

---

## Data Sources

All data is sourced from the [Bureau of Transportation Statistics (BTS)](https://www.transtats.bts.gov/), U.S. Department of Transportation.

| Dataset | Used For |
|---------|----------|
| T-100 Segment Data | Route-level passengers, freight, flights, seats, aircraft types |
| T-100 Market Data | Carrier route network size and hub rankings |
| Marketing Carrier On-Time Performance | Per-flight on-time, delay, and cancellation records |
| DB1B Origin-Destination Survey | Quarterly average fares by route and carrier |
| Form 41 Schedule P-6 | Quarterly salary and benefits by employee group |
| Form 41 Schedule B-1 | Quarterly balance sheet: assets, debt, equity, cash |
| Form 41 Schedule P-10 | Annual employee headcount by role |
| Form 41 Schedule B-43 | Annual active aircraft fleet counts |

The bundled database (`api/Data/db_backup.sql.gz`) contains pre-processed data derived from these public BTS sources.

---

## Runtime State and Persistence

- Database data lives in the Docker named volume `flightconn_mysql`.
- Data persists through container stops, removals, and image rebuilds.
- `setup.sh` seeds the database from the bundled backup only when the database is empty.
- Source files are only needed for rebuilding (`./update.sh`) or re-running `setup.sh`.
- **Never run `docker compose down -v`** unless you intentionally want to wipe all data.

---

## Maintenance

```bash
./update.sh          # Rebuild containers after code changes (auto-backups first)
./backup.sh          # Create a manual database backup → backups/
./restore.sh <file>  # Restore from a backup file (requires typed confirmation)

docker compose ps
docker compose logs -f
docker compose down
```

---

## Testing

The app does not include an automated test suite. Manual validation after setup:

```bash
curl http://localhost:<PORT>/health
curl http://localhost:<PORT>/api/stats
curl http://localhost:<PORT>/api/airports?limit=5
```

`setup.sh` performs row-count verification on all key tables and a live API health check before printing the final URL.

---

## Deployment Notes

- `setup.sh` is idempotent: re-running it on an existing installation updates the port if needed and rebuilds containers without wiping data.
- Port auto-discovery starts at 8082 and increments until a free port is found.
- The app listens on `0.0.0.0:8080` inside the container; the host port is set by `APP_PORT` in `.env`.
- For remote access, replace `localhost` in the printed URL with your server's IP or hostname.
- At the end of setup, `setup.sh` offers to delete local source files. Choosing yes removes only the source directory — the running app and Docker volumes are unaffected.

---

## Known Limitations

- BTS coverage is primarily U.S. domestic and international service from large certified air carriers. Smaller regional operators may have incomplete financial or on-time records.
- Fare data reflects BTS DB1B quarterly averages, not real-time pricing. Check airline sites for current fares.
- The app runs a single Flask process and is not designed for high-concurrency production deployment.
- On-time performance data coverage varies by carrier and year.

---

## Roadmap / Future Work

- Real-time flight status overlay via public ADS-B feeds
- Airport congestion and delay heatmaps
- Side-by-side carrier comparison view
- Stage length and fuel efficiency analysis
- Mobile-responsive layout improvements
- Automated BTS data refresh pipeline
