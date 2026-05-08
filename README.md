# FlightConn

A flight route, airline operations, fare, and airline career/health dashboard built from BTS aviation datasets. Shows route traffic, carriers, fares, on-time performance, airline workforce, financials, hubs, network size, and fleet data.

**Live site:** http://localhost:8082  
**Career page:** http://localhost:8082/career/

---

## Deploy

### Part 1 — Install Docker

```bash
sudo apt update && sudo apt upgrade -y && sudo apt install -y git curl openssl netcat-openbsd
curl -fsSL https://get.docker.com | sh && sudo usermod -aG docker $USER
```

Log out and back in, then verify:

```bash
docker --version && docker compose version
```

### Part 2 — Clone and run

```bash
git clone https://github.com/ApiFlier/aviation-route-intelligence.git ./flightconn && cd ./flightconn && chmod +x setup.sh && ./setup.sh
```

`setup.sh` will:
- Generate a `.env` file with random passwords.
- **Auto-discover** an available host port (starting at 8082) and save it as `APP_PORT`.
- Build and start the **FlightConn App** and **MySQL** containers.
- Wait for MySQL to pass its health check.
- Restore the database from `api/Data/db_backup.sql.gz` if it is empty.
- Verify the app and API are responding.

---

## Maintenance & Updates

### Rebuilding after code changes
To pull latest changes and rebuild the containers:
```bash
./update.sh
```
*Note: `update.sh` automatically creates a database backup before making any changes.*

### Database Backups
Create a manual backup at any time:
```bash
./backup.sh
```
Backups are stored in the `backups/` directory as `.sql.gz` files.

### Restoring Data
To restore from a specific backup file:
```bash
./restore.sh backups/flightconn_YYYYMMDD_HHMMSS.sql.gz
```
*Note: This requires typing `RESTORE FLIGHTCONN` to confirm and will create a pre-restore safety backup.*

### Monitoring
```bash
# View running containers
docker compose ps

# Stream logs
docker compose logs -f
```

### Stopping the App
```bash
docker compose down
```
*WARNING: Never run `docker compose down -v` unless you intentionally want to wipe all database data.*

---

## Architecture

| Component | Technology | Container Name |
|-----------|------------|----------------|
| **App / API** | Python 3.11, Flask 3.0 | `flightconn-app` |
| **Database** | MySQL 8.0 | `flightconn-db` |

- **Storage**: Database data is stored in a Docker named volume `flightconn_mysql`.
- **Port Discovery**: `setup.sh` ensures the host port is available and stable in `.env`.

---

Data sourced from the [Bureau of Transportation Statistics (BTS)](https://www.transtats.bts.gov/), U.S. Department of Transportation.
