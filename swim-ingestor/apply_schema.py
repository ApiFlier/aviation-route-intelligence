#!/usr/bin/env python3
"""
FlightConn SWIM Schema Apply — standalone utility

Applies api/Data/swim_schema.sql to the configured MySQL database.
Safe to run multiple times (CREATE TABLE IF NOT EXISTS).

Usage (from repo root, with sidecar env vars set):

  # Source .env then run:
  set -a && source .env && set +a
  DB_HOST=localhost python3 swim-ingestor/apply_schema.py

  # Or via Docker Compose:
  docker compose -f docker-compose.yml -f docker-compose.swim.yml \
      run --rm swim-ingestor python apply_schema.py

This script does NOT alter existing historical BTS tables.
"""

import logging
import sys
import os

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [apply-schema] %(levelname)s %(message)s',
    datefmt='%Y-%m-%dT%H:%M:%SZ',
    stream=sys.stdout,
)
log = logging.getLogger('apply-schema')


def main() -> None:
    log.info('Applying SWIM schema to database...')
    log.info('  DB_HOST: %s', os.environ.get('DB_HOST', 'localhost'))
    log.info('  DB_NAME: %s', os.environ.get('DB_NAME', 'flightconn'))
    log.info('  DB_USER: %s', os.environ.get('DB_USER', 'flightconn'))

    from db import apply_schema

    try:
        results = apply_schema()
    except FileNotFoundError as e:
        log.error('Schema file not found: %s', e)
        sys.exit(1)
    except Exception as e:
        log.error('Schema apply failed: %s: %s', type(e).__name__, str(e)[:200])
        sys.exit(1)

    created = [t for t, s in results.items() if s == 'created']
    existing = [t for t, s in results.items() if s == 'already_exists']
    missing = [t for t, s in results.items() if s == 'missing']

    for t in created:
        log.info('  CREATED: %s', t)
    for t in existing:
        log.info('  EXISTS:  %s', t)
    for t in missing:
        log.warning('  MISSING: %s (not found after apply)', t)

    if missing:
        log.error('Some tables are missing after apply. Check SQL errors above.')
        sys.exit(1)

    log.info('Schema apply complete. %d created, %d already existed.',
             len(created), len(existing))


if __name__ == '__main__':
    main()
