#!/usr/bin/env python3
"""
FlightConn SWIM Ingestor — Phase 1 Scaffold

Optional sidecar for FAA SWIM recent flight activity ingestion.
This phase validates configuration and exits. The live connector
is not implemented yet and will be added in Phase 2.

Environment variables (all optional for the main app):
    SWIM_ENABLED            Master switch — must be 'true' to activate
    FAA_USER                FAA SWIM account username
    FAA_PASS                FAA SWIM account password
    FAA_SWIM_BROKER_URL     Broker URL (provided after FAA SAA approval)
    QUEUE_SFDPS             SWIM SFDPS queue name
    QUEUE_STDDS             SWIM STDDS queue name
    QUEUE_TFMS              SWIM TFMS queue name
    SWIM_LOOKBACK_DAYS      Days of history to consider current (default 30)
    SWIM_RETENTION_DAYS     Days to retain normalized flight events (default 35)
    SWIM_RAW_RETENTION_DAYS Days to retain raw source messages (default 14)
    SWIM_LOG_LEVEL          Logging level (default INFO)
"""

import os
import sys
import logging

_QUEUE_VARS = ('QUEUE_SFDPS', 'QUEUE_STDDS', 'QUEUE_TFMS')


def _present(name: str) -> bool:
    """Return True if the named env var is set and non-empty."""
    return bool(os.environ.get(name, '').strip())


def _get(name: str, default: str = '') -> str:
    return os.environ.get(name, default)


def _setup_logging() -> logging.Logger:
    level_name = _get('SWIM_LOG_LEVEL', 'INFO').upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format='%(asctime)s [swim-ingestor] %(levelname)s %(message)s',
        datefmt='%Y-%m-%dT%H:%M:%SZ',
        stream=sys.stdout,
    )
    return logging.getLogger('swim-ingestor')


def main() -> None:
    log = _setup_logging()

    swim_enabled = _get('SWIM_ENABLED', 'false').strip().lower() == 'true'

    if not swim_enabled:
        log.info('SWIM ingestion is disabled (SWIM_ENABLED != true). Exiting.')
        sys.exit(0)

    # ── Redacted config summary — values are never logged ────────────────────
    configured_queues = [q for q in _QUEUE_VARS if _present(q)]
    log.info('SWIM configuration summary:')
    log.info('  SWIM_ENABLED:          true')
    log.info('  broker URL present:    %s', 'yes' if _present('FAA_SWIM_BROKER_URL') else 'no')
    log.info('  username present:      %s', 'yes' if _present('FAA_USER') else 'no')
    log.info('  password present:      %s', 'yes' if _present('FAA_PASS') else 'no')
    log.info('  queues configured:     %s',
             ', '.join(configured_queues) if configured_queues else 'none')
    log.info('  SWIM_LOOKBACK_DAYS:    %s', _get('SWIM_LOOKBACK_DAYS', '30'))
    log.info('  SWIM_RETENTION_DAYS:   %s', _get('SWIM_RETENTION_DAYS', '35'))
    log.info('  SWIM_RAW_RETENTION_DAYS: %s', _get('SWIM_RAW_RETENTION_DAYS', '14'))

    # ── Validate required config ──────────────────────────────────────────────
    missing = []
    if not _present('FAA_USER'):
        missing.append('FAA_USER')
    if not _present('FAA_PASS'):
        missing.append('FAA_PASS')
    if not configured_queues:
        missing.append('at least one of: ' + ', '.join(_QUEUE_VARS))

    if missing:
        log.warning('SWIM_ENABLED=true but required configuration is missing:')
        for item in missing:
            log.warning('  missing: %s', item)
        log.warning(
            'Set these variables in swim.env and re-run with the SWIM compose override. '
            'Exiting without connecting.'
        )
        sys.exit(0)

    # ── Phase 1: connector not implemented ───────────────────────────────────
    log.info('All required SWIM configuration is present.')
    log.info('SWIM connector not implemented yet. Phase 1 scaffold only.')
    log.info(
        'Phase 2 will implement FAA SWIM broker connection, '
        'message normalization, and database writes.'
    )
    sys.exit(0)


if __name__ == '__main__':
    main()
