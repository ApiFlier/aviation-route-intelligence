# SWIM Field Catalog & Dashboard Mapping

This document catalogs the data fields extracted from FAA SWIM/SCDS messages (TFMS, SFDPS, STDDS) and evaluates their usefulness for FlightConn's strategic route intelligence dashboards.

**Disclaimer:** Data obtained via the SWIM Cloud Distribution Service (SCDS) is NOT for OPERATIONAL USE. All data has been pre-approved for public release by the NAS Data Release Board (NDRB). FlightConn uses this data strictly as a lagging indicator for strategic route analytics, not for live tracking.

---

## 1. TFMS (Traffic Flow Management System)

TFMS provides broad schedule and flow data, often containing scheduled vs. actual times, aircraft types, and carrier details.

| Field | Example | FlightConn Usefulness | Target Storage | Dashboard Relevance | Notes |
|-------|---------|-----------------------|----------------|---------------------|-------|
| `gufi` | TFM123 | **Store Now** | `observed_flights.source_flight_id` | All | Primary correlation ID. |
| `acid` / `aircraftId` | DAL123 | **Store Now** | `observed_flights.callsign` | All | Primary flight identity. |
| `airline` | DAL | **Store Now** | `observed_flights.carrier_code` | All | Primary carrier identity. |
| `major` | DAL | **Store Now** | `observed_flight_enrichment.major_carrier_code` | Route Activity, Carrier Health | Distinguishes operating regional vs marketing mainline. |
| `depArpt` / `departurePoint` | KATL | **Store Now** | `observed_flights.origin_iata` | All | |
| `arrArpt` / `arrivalPoint` | KLAX | **Store Now** | `observed_flights.dest_iata` | All | |
| `originalDeparture` / `igtd` | 2026-05-11T... | **Store Now** | `observed_flights.sched_dep_utc` | Carrier Health, Route Opportunity | Baseline schedule proxy. |
| `timeOfDeparture(est=false)` | 2026-05-11T... | **Store Now** | `observed_flights.actual_dep_utc` | Carrier Health | Proxy for actual departure. |
| `originalArrival` | 2026-05-11T... | **Store Now** | `observed_flights.sched_arr_utc` | Carrier Health | Baseline schedule proxy. |
| `airlineOnTime` / `airlineInTime` | 2026-05-11T... | **Store Now** | `observed_flights.actual_arr_utc` | Carrier Health | Proxy for actual arrival. |
| `aircraftModel` / `aircraftSpecification` | B738 | **Store Now** | `observed_flights.aircraft_type` | Route Activity, Route Opportunity | Capacity/equipment signal. |
| `flightStatus` | ACTIVE | **Store Now** | `observed_flights.flight_status` | Route Activity, Carrier Health | |
| `userCategory` | COMMERCIAL | **Store Now** | `observed_flight_enrichment.user_category` | Route Opportunity | Helps filter out GA flights. |
| `aircraftCategory` | JET | **Store Now** | `observed_flight_enrichment.aircraft_category` | Enrichment | |
| `routeOfFlight` / `newRouteOfFlight` | ATL..LAX | **Store Now** | `observed_flight_enrichment.route_of_flight` | Enrichment | Analysis of routing changes. |
| `dp` / `star` routeName | ... | **Store Now** | `observed_flight_enrichment.departure_procedure` | Enrichment | |
| `flightTraversalData2` (fixes/sectors) | ... | Do Not Store | N/A | None | Route geometry is out of scope. |

---

## 2. SFDPS (SWIM Flight Data Publication Service)

SFDPS provides en-route data and frequent status updates.

| Field | Example | FlightConn Usefulness | Target Storage | Dashboard Relevance | Notes |
|-------|---------|-----------------------|----------------|---------------------|-------|
| `gufi` | uuid | **Store Now** | `observed_flights.source_flight_id` | All | |
| `flightIdentification` | DAL123 | **Store Now** | `observed_flights.callsign` | All | |
| `operator/organization` | DAL | **Store Now** | `observed_flights.carrier_code` | All | |
| `departurePoint` | KATL | **Store Now** | `observed_flights.origin_iata` | All | |
| `arrivalPoint` | KLAX | **Store Now** | `observed_flights.dest_iata` | All | |
| `departure/runwayTime/actual` | 2026-05-11T... | **Store Now** | `observed_flights.actual_dep_utc` | Carrier Health | |
| `arrival/runwayTime/estimated` | 2026-05-11T... | **Store Now** | `observed_flights.sched_arr_utc` | Carrier Health | Note: This is an estimated runway time, not an official schedule. |
| `arrival/runwayTime/actual` | 2026-05-11T... | **Store Now** | `observed_flights.actual_arr_utc` | Carrier Health | |
| `icaoModelIdentifier` | A321 | **Store Now** | `observed_flights.aircraft_type` | Route Activity, Route Opportunity | |
| `flightType` | SCHEDULED | **Store Now** | `observed_flight_enrichment.flight_type` | Route Opportunity | Filters out non-commercial flights. |
| `fdpsFlightStatus` | ACTIVE | **Store Now** | `observed_flights.flight_status` | Route Activity, Carrier Health | |
| `nasRouteText` | ATL..LAX | **Store Now** | `observed_flight_enrichment.route_of_flight` | Enrichment | |
| `assignedAltitude` | 35000 | Store Later | N/A | None | Out of scope for current analytics. |
| `enRoute` (speed, lat, lon) | ... | Aviation Radar | N/A | None | Live tracking is out of scope. |

---

## 3. STDDS (SWIM Terminal Data Distribution System)

STDDS provides high-frequency surface and terminal data. For FlightConn, STDDS is used **strictly for route identity enrichment**, not for surface movement tracking.

| Field | Example | FlightConn Usefulness | Target Storage | Dashboard Relevance | Notes |
|-------|---------|-----------------------|----------------|---------------------|-------|
| `eramGufi` | E123 | **Store Now** | `observed_flights.source_flight_id` | All | Used to correlate with TFMS/SFDPS. |
| `callsign` / `aircraftId` | DAL123 | **Store Now** | `observed_flights.callsign` | All | |
| `departureAirport` | KATL | **Store Now** | `observed_flights.origin_iata` | All | |
| `destinationAirport` | KLAX | **Store Now** | `observed_flights.dest_iata` | All | |
| `aircraftType` | B737 | **Store Now** | `observed_flights.aircraft_type` | Route Activity, Route Opportunity | |
| `status` | ACTIVE | **Store Now** | `observed_flights.flight_status` | Route Activity | |
| `positionReport` / `latitude` | ... | Aviation Radar | N/A | None | Live tracking out of scope. |
| `runway` / `taxi` movement | ... | Aviation Radar | N/A | None | Surface tracking out of scope. |
| `beaconCode` | 1234 | Do Not Store | N/A | None | |

---

## Dashboard Mapping & Analytics Strategy

### 1. Route Activity & Intelligence
**Goal:** Show active routes, carriers serving them, and recent activity frequency.
**Key Fields:** `source_flight_id`, `callsign`, `carrier_code`, `origin_iata`, `dest_iata`, `aircraft_type`, `flight_status`.
**Implementation:** Aggregated by the sidecar into `recent_route_activity`. Non-U.S. 4-letter ICAO codes are safely preserved to accurately reflect international routes.

### 2. Airline Health
**Goal:** Show operational reliability context (e.g., observed timing variance).
**Key Fields:** `sched_dep_utc`, `actual_dep_utc`, `sched_arr_utc`, `actual_arr_utc`, `flight_status`.
**Implementation:** FlightConn strictly frames these as "observed timing variance", not official DOT/airline delay records. The raw fields are stored in `observed_flights` so future aggregations can compute variance metrics. `operating_carrier_code` and `major_carrier_code` in `observed_flight_enrichment` help distinguish regional operators from mainline brands.

### 3. Route Opportunity
**Goal:** Filter routes using commercial context and recent activity frequency.
**Key Fields:** `userCategory`, `flightType`, `aircraft_type`.
**Implementation:** The enrichment table stores `userCategory` (e.g., COMMERCIAL vs GENERAL AVIATION) and `flightType` (e.g., SCHEDULED) so the aggregator can confidently filter out private jet traffic, keeping the route opportunity scores focused strictly on commercial service.

### Commercial Candidate Heuristic
FlightConn uses a heuristic to distinguish commercial airline activity from general aviation (GA) and private flights. A record is considered a **Commercial Candidate** if:
- `user_category` is `COMMERCIAL` OR `flight_type` is `SCHEDULED`.
- OR `aircraft_category` is `JET` (with a known carrier).
- AND it has valid route identity (origin/destination).
- AND it does not use a US private tail number callsign (e.g., `N12345`).
- AND the carrier is not `XXX`, `UNK`, or `UNKN`.

*Note: This "commercial candidate" flag is purely a heuristic for strategic analytics within FlightConn. It is not an official FAA or DOT operational classification.*

## Storage Plan: "Analysis-Worthy" Architecture
To prevent bloating the normalized `observed_flights` table while still capturing analysis-worthy fields, FlightConn uses a hybrid model:
1. **Core Normalized Table (`observed_flights`)**: Stores only fields directly used for route/carrier deduplication and basic aggregation (identity, airports, timing, status, basic aircraft type).
2. **Companion Enrichment Table (`observed_flight_enrichment`)**: Keyed by `source_flight_id` (1:1 relationship with `observed_flights`). Stores optional, source-specific string fields (e.g., procedures, route text, flight types, operator variants). This table is easily extensible without impacting the core UPSERT performance.

## Airport Code Model Evaluation
Currently, the columns `origin_iata` and `dest_iata` accept both 3-character IATA and 4-character ICAO codes. 
**Recommendation:** For now, the existing column names (`origin_iata` / `dest_iata`) are maintained to preserve API compatibility, but their contents are treated as a generalized `airport_code`. In a future major version, migrating to `origin_airport_code` and `dest_airport_code` (paired with `origin_code_type`) is recommended for semantic clarity.
