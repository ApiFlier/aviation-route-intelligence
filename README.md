# FlightConn

A self-hosted airline route and intelligence tool built from Bureau of Transportation Statistics (BTS) public datasets. Covers route exploration, carrier analysis, fare trends, on-time performance, airline financial health, and a route opportunity screening tool — all running locally in Docker.

---

## Quick Start

```bash
git clone https://github.com/ApiFlier/aviation-route-intelligence.git flightconn
cd flightconn
chmod +x setup.sh
./setup.sh
```

```bash
git clone https://github.com/ApiFlier/aviation-route-intelligence.git flightconn
cd flightconn
cp deploy.env.example deploy.env
nano deploy.env
chmod +x setup.sh
./setup.sh
```

`setup.sh` handles everything automatically:
- Generates `.env` with random credentials (no manual config needed)
- If `deploy.env` is present, imports user-supplied values (port, SWIM credentials)
- Builds Docker containers (`flightconn-app`, `flightconn-db`)
- Creates or reuses the Docker-managed persistent volume `flightconn_mysql`
- Finds an available host port automatically (starting at 8082) and saves it to `.env`
- Seeds the database from `api/Data/db_backup.sql.gz` on first run
- Verifies all key tables and runs a live health check
- **Prints the local URL** when the app is ready

No external API keys or network access required at runtime. The optional FAA SWIM sidecar is disabled by default.

---

## What FlightConn Does

FlightConn is a route and airline intelligence tool for exploring airline routes, fares, service patterns, carrier presence, and airline health using public aviation datasets. It ships as three integrated views:

**Route Intelligence Map** (`/`) — An interactive map of the US aviation network. Search any airport to see its routes, carriers, passenger volumes, freight data, quarterly fare trends, and per-carrier on-time performance. Drill into a specific carrier on a route for delay cause breakdowns, aircraft types, and typical flight schedule tiles.

**Route Opportunity Finder** (`/opportunities/`) — A ranked, filterable view of existing routes scored on public-data signals: passenger demand, historical fare levels, competition, seat utilization, reliability, and carrier context. Useful for quickly surfacing routes that may deserve deeper review. Scores are directional indicators, not profitability estimates.

**Airline Health** (`/airline-health/`) — Carrier-level context using public financial, fleet, network, employee, and operating indicators. Helps users compare airline stability across major US carriers. It does not predict job security, route profitability, or future airline performance. Also reachable at `/career/`.

---

## Key Features

**Route Intelligence**
- Interactive airport and route map with 2,500+ US airports and layered satellite/street basemaps
- Route drill-down: passengers, freight, seats, load factor, and carrier market share
- Quarterly fare analysis from BTS DB1B data with cheapest/peak quarter highlights
- Per-carrier on-time performance with delay cause breakdowns (carrier, weather, NAS, security)
- Typical flight schedule tiles derived from historical BTS on-time records

**Route Opportunity Finder**
- Composite route opportunity score (0–100) built from six public-data components
- Filter by origin airport, origin/destination state, distance, passenger volume, and carrier count
- Component breakdown: demand, fare strength, competition gap, service pressure, distance fit, carrier context
- Per-route reasons, risks, confidence label, and data-source notes
- `GET /api/routes/opportunities` endpoint with five sort modes and full input validation

**Airline Health Dashboard**
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
- A "This Month" fare context note based on historical BTS data

Clicking a carrier on that route shows:
- Scheduled vs. performed flights, cancellation rate
- On-time %, average arrival delay, average flight time
- Delay cause bar chart (carrier / weather / NAS / security / late aircraft)
- Aircraft types flown with departure counts
- Typical weekly schedule with per-flight average delay

---

## Route Opportunity Finder

Available at `/opportunities/`, the Route Opportunity Finder ranks existing served routes using public-data signals already in the database. It is designed to help users quickly identify routes that may deserve deeper review based on historical demand, fare, competition, capacity, reliability, and carrier-context indicators.

### How scoring works

Each route receives a composite opportunity score (0–100) built from six components:

| Component | Weight | Signal |
|-----------|--------|--------|
| Demand | 30% | Annual passenger volume (T-100 traffic records) |
| Fare Strength | 20% | Passenger-weighted average fares (DB1B survey data) |
| Competition Gap | 20% | Carrier count and dominant-carrier share (T-100) |
| Service Pressure | 15% | Seat utilization and cancellation rate |
| Distance Fit | 10% | Route distance relative to the domestic sweet spot |
| Carrier Context | 5% | Whether the dominant carrier files BTS Form 41 reports |

Risk penalties are applied for missing data: no fare coverage, unknown distance, or very low passenger volume.

### What the API returns

`GET /api/routes/opportunities` returns a ranked list. Each result includes:
- Opportunity score (0–100), confidence label, and category label
- Scored component breakdown
- Key metrics: passengers, average fare, load factor, on-time %, carrier count, dominant carrier
- Reasons: what is driving the score up
- Risks: what to watch out for
- Data notes: source attribution and per-route caveats

### Filters

| Parameter | Description |
|-----------|-------------|
| `origin` | Filter by origin airport (IATA code) |
| `origin_state` | Filter by origin state (2-letter US code) |
| `dest_state` | Filter by destination state |
| `min_distance` / `max_distance` | Distance range in miles |
| `min_passengers` | Minimum annual passenger volume |
| `max_carriers` | Maximum number of operating carriers |
| `domestic_only` | Default `true`; DB1B fare coverage is US domestic |
| `sort` | `opportunity`, `demand`, `fare_strength`, `limited_competition`, `service_pressure` |
| `limit` | Number of results, default 25, max 100 |

### Scope and limitations

- **Scores are directional indicators, not profitability estimates.** Public datasets cannot confirm the financial viability of any specific route.
- **Fare signals are historical.** The fare component uses passenger-weighted averages from the DB1B Origin-Destination Survey (a 10% itinerary sample). These are not live ticket prices or current cost estimates.
- **Carrier financial data is carrier-level, not route-level.** The carrier context component uses Form 41 filing status as a proxy for established scheduled service, not as a route-level financial assessment.
- **Phase 1 scope: existing served routes only.** Unserved or hypothetical market discovery is not included in the current version.
- **International routes may have lower confidence.** DB1B fare coverage is US domestic. Routes with international endpoints score the fare component at zero and receive a risk note.
- **Not an official recommendation.** This tool does not represent official airline planning, FAA, dispatch, or operational guidance of any kind.

---

## Airline Health Module

Available at `/airline-health/` (also `/career/` for backward compatibility), the Airline Health dashboard shows every carrier that has both BTS financial and employee filings. It provides carrier-level context using public financial, fleet, network, employee, and operating indicators. Carrier health indicators are system-level signals and do not predict job security, route profitability, or future airline performance.

Cards display a 0–100 health score, employee count, quarterly compensation, and an on-time performance bar.

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

- **Backend:** Python 3.11, Flask 3.0, mysqlclient, flask-cors, gunicorn
- **Database:** MySQL 8.0
- **Frontend:** Vanilla JavaScript, Leaflet.js (interactive maps), Font Awesome (icons)
- **Infrastructure:** Docker, Docker Compose, Docker named volumes
- **Data:** Bureau of Transportation Statistics (BTS) — all public datasets

---

## Data Sources

All data is sourced from publicly available U.S. aviation datasets, primarily the [Bureau of Transportation Statistics (BTS)](https://www.transtats.bts.gov/), U.S. Department of Transportation.

Source families include historical route traffic records, fare survey data, capacity and passenger records, schedule reliability records, and carrier financial reporting data.

| Dataset | Used For |
|---------|----------|
| T-100 Segment Data | Route-level passengers, freight, flights, seats, aircraft types |
| T-100 Market Data | Carrier route network size and hub rankings |
| Marketing Carrier On-Time Performance | Per-flight on-time, delay, and cancellation records |
| DB1B Origin-Destination Survey | Quarterly average fares by route and carrier (10% itinerary sample) |
| Form 41 Schedule P-6 | Quarterly salary and benefits by employee group |
| Form 41 Schedule B-1 | Quarterly balance sheet: assets, debt, equity, cash |
| Form 41 Schedule P-10 | Annual employee headcount by role |
| Form 41 Schedule B-43 | Annual active aircraft fleet counts |

The bundled database (`api/Data/db_backup.sql.gz`) contains pre-processed data derived from these public BTS sources. The raw source CSV files are not required at runtime and are not committed to the repository.

---

## Runtime State and Persistence

- Database data lives in the Docker named volume `flightconn_mysql`.
- Data persists through container stops, removals, and image rebuilds.
- `setup.sh` seeds the database from the bundled backup only when the database is empty.
- Source files are only needed for rebuilding (`./update.sh`) or re-running `setup.sh`.
- **Never run `docker compose down -v`** unless you intentionally want to wipe all data.

---

## Deployment Notes

- **Docker-based setup.** The app runs in two containers (`flightconn-app`, `flightconn-db`) managed by Docker Compose. No bind-mounted source code is required in production — all application files are baked into the image at build time.
- **Persistent database state.** Data lives in a Docker named volume (`flightconn_mysql`) and survives container restarts and rebuilds.
- **Raw source data is not required at runtime.** The local `api/Data/faa-data/` directory contains raw CSV source files used for the initial database import. These files are not needed once the database is seeded, are excluded from Docker builds via `.dockerignore`, and should not be committed to the repository.
- **Port auto-discovery.** `setup.sh` is idempotent: re-running it updates the port if needed and rebuilds containers without wiping data. Port discovery starts at 8082 and increments until a free port is found.
- **Remote access.** For remote access, replace `localhost` in the printed URL with your server's IP or hostname.
- At the end of setup, `setup.sh` offers to delete local source files. Choosing yes removes only the source directory — the running app and Docker volumes are unaffected.
- **`deploy.env`** — optional user-supplied config (port pin, SWIM credentials). Copy `deploy.env.example` to `deploy.env`. It is ignored by Git and Docker builds. The main app runs without it.

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

## Optional FAA SWIM Recent Activity Sidecar

The core app (Route Map, Route Opportunity Finder, Airline Health) runs entirely on historical public aviation datasets. **No SWIM credentials or network access are required.** Normal setup works without any SWIM configuration.

An optional sidecar (`flightconn-swim-ingestor`) can ingest recent flight activity from the FAA System Wide Information Management (SWIM) program. `setup.sh` auto-detects whether SWIM is ready — no flags to set.

**Current status: FAA SWIM recent route activity pipeline implemented.** When FAA credentials and queues are configured, the optional sidecar can connect to FAA SWIM through Solace PubSub+, normalize recent flight activity, aggregate route-level summaries, and expose recent activity context through the backend API and UI. The core app still runs normally without SWIM, and raw message payloads are not stored or displayed.

FlightConn uses SWIM data for recent route activity context and route-pattern intelligence. It does not display live aircraft positions or replace Aviation Radar.

**The main app is not affected by whether this sidecar runs.**

### Enable via deploy.env

```bash
# 1. Copy the user config template
cp deploy.env.example deploy.env

# 2. Fill in FAA credentials — that's it. No flags to set.
#
#   FAA_USER=your-faa-username
#   FAA_PASS=your-faa-password
#   QUEUE_SFDPS=your-sfdps-queue-name   (from FAA after account setup)

# 3. Re-run setup — it detects the credentials and starts the sidecar automatically.
./setup.sh
```

`setup.sh` detects SWIM readiness automatically:
- `FAA_USER`, `FAA_PASS`, and at least one `QUEUE_*` set → SWIM sidecar starts.
- Any of those blank or missing → main app starts normally, SWIM skipped.

The broker URL defaults to `tcps://ems1.swim.faa.gov:55443` (FAA SWIM SCDS production). FlightConn uses the same Solace PubSub+ style connection pattern as Aviation Radar. FAA broker/protocol details are handled internally. Advanced protocol overrides should only be used for troubleshooting. Override with `FAA_URL=` in `deploy.env` only if FAA provides a different address for your account.

### Manual override (without setup.sh)

```bash
# Apply SWIM tables to the database (safe to run multiple times)
docker compose -f docker-compose.yml -f docker-compose.swim.yml \
    run --rm swim-ingestor python apply_schema.py

# Start SWIM sidecar alongside main app
docker compose -f docker-compose.yml -f docker-compose.swim.yml up -d
```

FAA SWIM access requires a completed [SWIM Service Access Agreement](https://www.faa.gov/air_traffic/technology/swim). Credentials must never be committed. `deploy.env` is listed in `.gitignore`. No raw message payloads are logged or stored during probe mode.

### Schema

`api/Data/swim_schema.sql` contains `CREATE TABLE IF NOT EXISTS` DDL for all SWIM tables. It does not alter existing BTS history tables and is safe to re-run.

---

## Testing

The app does not include an automated test suite. Manual validation after setup:

```bash
curl http://localhost:<PORT>/health
curl http://localhost:<PORT>/api/stats
curl http://localhost:<PORT>/api/airports?limit=5
curl http://localhost:<PORT>/api/routes/opportunities?limit=3
```

`setup.sh` performs row-count verification on all key tables and a live API health check before printing the final URL.

---

## Known Limitations

**General**
- BTS coverage is primarily U.S. domestic and international service from large certified air carriers. Smaller regional operators may have incomplete financial or on-time records.
- Fare data reflects BTS DB1B quarterly averages, not real-time pricing. Check airline sites for current fares.
- The app runs a single gunicorn process and is not designed for high-concurrency production deployment.
- On-time performance data coverage varies by carrier and year.

**Route Opportunity Finder**
- Scores are directional public-data indicators, not route profitability estimates. Public datasets cannot confirm the financial viability of any specific route.
- Fare signals are historical survey-based indicators derived from the DB1B Origin-Destination Survey. They are not live ticket prices or exact current cost estimates.
- Carrier financial data is reported at the carrier level, not the route level. The carrier context component does not assess route-level financials.
- The current version scores existing served routes only. Unserved or hypothetical market discovery is not part of this release.
- International route scoring may have lower confidence where DB1B fare coverage is limited.
- This tool does not provide official airline planning, FAA, dispatch, or operational recommendations.

---

## Roadmap / Future Work

- Unserved market discovery (hypothetical route scoring)
- Historical route trend analysis and year-over-year performance comparison
- Airport congestion and delay heatmaps
- Side-by-side carrier comparison view
- Stage length and fuel efficiency analysis
- Mobile-responsive layout improvements
- Automated BTS data refresh pipeline
