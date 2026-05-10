#!/usr/bin/env python3
"""
FlightConn SWIM Ingestor

Optional sidecar for FAA SWIM recent flight activity ingestion.

Behavior by configuration:
  ENABLE_SWIM_INGESTOR != true  → exits 0, no connection attempted
  ENABLE_SWIM_INGESTOR=true, missing config → exits 0, logs what is missing
  ENABLE_SWIM_INGESTOR=true, SWIM_PROBE_ONLY=true → runs bounded probe, records result, exits 0
  ENABLE_SWIM_INGESTOR=true, full config, no probe flag → logs Phase 2B not ready, exits 0

  SWIM_ENABLED is also accepted for backward compatibility with swim.env users.
  ENABLE_SWIM_INGESTOR takes precedence when both are set.

Environment variables (all optional for the main app):
  ENABLE_SWIM_INGESTOR      Master switch — must be 'true' to activate (preferred)
  SWIM_ENABLED              Accepted alias for ENABLE_SWIM_INGESTOR (legacy)
  FAA_USER                  FAA SWIM account username
  FAA_PASS                  FAA SWIM account password

  Broker address — use one of:
  FAA_URL                   Full broker URL from deploy.env (e.g. tcps://host:port)
  FAA_SWIM_BROKER_URL       Alias for FAA_URL (legacy swim.env form)
  FAA_SWIM_HOST             Hostname only (alternative to URL forms)
  FAA_SWIM_PORT             Port (default 61614 for ssl, 61613 for tcp)
  FAA_SWIM_PROTOCOL         ssl (default) | tcp | tls | plain

  QUEUE_SFDPS               SWIM SFDPS queue name (from FAA)
  QUEUE_STDDS               SWIM STDDS queue name (from FAA)
  QUEUE_TFMS                SWIM TFMS queue name (from FAA)
  SWIM_CONFIG_CHECK_ONLY    If 'true', validate config and exit without connecting
  SWIM_PROBE_ONLY           If 'true', run bounded probe and exit
  SWIM_PROBE_SECONDS        Probe timeout in seconds (default 30)
  SWIM_PROBE_MAX_MESSAGES   Max messages to receive in probe (default 5)
  SWIM_SSL_VERIFY           Set 'false' to skip TLS cert check (testing only)
  SWIM_DEST_PREFIX          Queue destination prefix (default /queue/)
  SWIM_HEARTBEAT_MS         STOMP heartbeat interval in ms (default 4000)
  SWIM_LOOKBACK_DAYS        Days of history to consider current (default 30)
  SWIM_RETENTION_DAYS       Days to retain normalized flight events (default 35)
  SWIM_RAW_RETENTION_DAYS   Days to retain raw source messages (default 14)
  SWIM_LOG_LEVEL            Logging level (default INFO)
"""

import os
import sys
import logging

_QUEUE_VARS = ('QUEUE_SFDPS', 'QUEUE_STDDS', 'QUEUE_TFMS')


def _present(name: str) -> bool:
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


def _log_redacted_config(log: logging.Logger) -> None:
    configured_queues = [q for q in _QUEUE_VARS if _present(q)]
    log.info('SWIM configuration summary:')
    log.info('  SWIM_ENABLED:          true')
    if _present('FAA_URL'):
        log.info('  broker:                FAA_URL (set)')
    elif _present('FAA_SWIM_BROKER_URL'):
        log.info('  broker:                FAA_SWIM_BROKER_URL (set)')
    elif _present('FAA_SWIM_HOST'):
        log.info('  broker:                FAA_SWIM_HOST (set), PORT=%s, PROTOCOL=%s',
                 _get('FAA_SWIM_PORT', '(default)'), _get('FAA_SWIM_PROTOCOL', 'ssl'))
    else:
        log.info('  broker:                NOT SET')
    log.info('  username present:      %s', 'yes' if _present('FAA_USER') else 'no')
    log.info('  password present:      %s', 'yes' if _present('FAA_PASS') else 'no')
    log.info('  queues configured:     %s',
             ', '.join(configured_queues) if configured_queues else 'none')
    log.info('  SWIM_PROBE_ONLY:       %s', _get('SWIM_PROBE_ONLY', 'false'))
    log.info('  SWIM_LOG_LEVEL:        %s', _get('SWIM_LOG_LEVEL', 'INFO'))


def _validate_config(log: logging.Logger) -> list:
    """Return a list of missing required config items."""
    missing = []
    if not _present('FAA_USER'):
        missing.append('FAA_USER')
    if not _present('FAA_PASS'):
        missing.append('FAA_PASS')
    if not _present('FAA_URL') and not _present('FAA_SWIM_BROKER_URL') and not _present('FAA_SWIM_HOST'):
        missing.append(
            'FAA_URL  (or FAA_SWIM_BROKER_URL, or FAA_SWIM_HOST + FAA_SWIM_PORT + FAA_SWIM_PROTOCOL)'
        )
    if not any(_present(q) for q in _QUEUE_VARS):
        missing.append('at least one of: ' + ', '.join(_QUEUE_VARS))
    return missing


def _run_config_check(log: logging.Logger) -> None:
    """Validate and display configuration without connecting to FAA."""
    log.info('=== SWIM configuration check (SWIM_CONFIG_CHECK_ONLY=true) ===')
    _log_redacted_config(log)

    # Show broker parse result — safe to compute, no connection made
    if _present('FAA_URL') or _present('FAA_SWIM_BROKER_URL') or _present('FAA_SWIM_HOST'):
        try:
            from connector_faa_swim import _resolve_broker
            _, port, use_ssl, source = _resolve_broker()
            log.info('  broker parse:          port=%d ssl=%s (from %s)', port, use_ssl, source)
        except ValueError as e:
            log.warning('  broker parse failed:   %s', e)
        except Exception as e:
            log.warning('  broker parse error:    %s: %s', type(e).__name__, e)

    missing = _validate_config(log)
    if missing:
        log.warning('Missing required configuration:')
        for item in missing:
            log.warning('  missing: %s', item)
        log.warning('No connection will be attempted until all items are set.')
    else:
        log.info('All required SWIM configuration is present.')
        log.info('Remove SWIM_CONFIG_CHECK_ONLY=true (or set to false) to enable probe or ingestion.')
    log.info('=== End configuration check — no FAA connection was made ===')


def _run_probe(log: logging.Logger) -> None:
    """Run bounded probe mode. Records result in swim_ingestion_runs."""
    probe_seconds  = int(_get('SWIM_PROBE_SECONDS', '30'))
    max_messages   = int(_get('SWIM_PROBE_MAX_MESSAGES', '5'))

    log.info('Phase 2A probe mode: %ds window, up to %d message(s).',
             probe_seconds, max_messages)

    # ── Ensure schema is applied ──────────────────────────────────────────
    try:
        from db import apply_schema
        schema_results = apply_schema()
        for tbl, status in schema_results.items():
            if status == 'created':
                log.info('Schema: created table %s', tbl)
            elif status == 'missing':
                log.warning('Schema: table %s still missing after apply', tbl)
    except Exception as e:
        log.warning('Schema apply failed (continuing probe): %s: %s',
                    type(e).__name__, str(e)[:200])

    # ── Start ingestion run record ────────────────────────────────────────
    run_id = None
    try:
        from db import start_ingestion_run
        run_id = start_ingestion_run('FAA_SWIM_PROBE')
        log.info('Ingestion run started: id=%d', run_id)
    except Exception as e:
        log.warning('Could not start ingestion run record: %s: %s',
                    type(e).__name__, str(e)[:200])

    # ── Run probe ─────────────────────────────────────────────────────────
    probe_result = None
    try:
        from connector_faa_swim import run_probe
        probe_result = run_probe(
            probe_seconds=probe_seconds,
            max_messages=max_messages,
        )
    except ImportError as e:
        log.error('Cannot import connector: %s', e)
        log.error('Ensure stomp.py is installed: pip install stomp.py')
    except Exception as e:
        log.error('Probe raised unexpected error: %s: %s', type(e).__name__, str(e)[:200])

    # ── Log safe probe summary ────────────────────────────────────────────
    if probe_result:
        if probe_result.connected:
            log.info('Probe result: connected=yes, messages_received=%d',
                     probe_result.messages_received)
            for label, count in probe_result.counts_by_label.items():
                log.info('  queue %s: %d message(s)', label, count)
            for meta in probe_result.messages_metadata:
                log.info(
                    '  msg: queue=%s received_at=%s payload_bytes=%d '
                    'content_type=%s msg_type=%s',
                    meta['queue_label'],
                    meta['received_at'],
                    meta['payload_bytes'],
                    meta['content_type'],
                    meta['msg_type'],
                )
        else:
            log.warning('Probe result: connected=no. error=%s',
                        probe_result.error_summary or '(unknown)')
            for err in probe_result.errors:
                log.warning('  error detail: %s', err)

    # ── Finish ingestion run record ───────────────────────────────────────
    if run_id is not None:
        try:
            from db import finish_ingestion_run
            counts = {
                'messages_recv':   probe_result.messages_received if probe_result else 0,
                'messages_ok':     probe_result.messages_received if probe_result else 0,
                'messages_err':    len(probe_result.errors) if probe_result else 1,
                'flights_new':     0,
                'flights_updated': 0,
            }
            # Status rules:
            #   connected=True  → completed (0 messages is valid — queue may be quiet)
            #   connected=False → failed
            #   probe_result is None (unhandled exception) → failed
            if probe_result and probe_result.connected:
                status = 'completed'
                # Carry over any post-connect errors (e.g. STOMP ERROR frames)
                error_detail = probe_result.error_summary or None
            elif probe_result:
                status = 'failed'
                error_detail = probe_result.error_summary or 'connection failed'
            else:
                status = 'failed'
                error_detail = 'probe raised unexpected error'
            finish_ingestion_run(run_id, status, counts, error_detail)
            log.info('Ingestion run %d recorded as %s.', run_id, status)
        except Exception as e:
            log.warning('Could not finish ingestion run record: %s: %s',
                        type(e).__name__, str(e)[:200])


def main() -> None:
    log = _setup_logging()

    # ENABLE_SWIM_INGESTOR (deploy.env) takes precedence over SWIM_ENABLED (swim.env legacy)
    if _present('ENABLE_SWIM_INGESTOR'):
        swim_enabled = _get('ENABLE_SWIM_INGESTOR', 'false').strip().lower() == 'true'
    else:
        swim_enabled = _get('SWIM_ENABLED', 'false').strip().lower() == 'true'

    if not swim_enabled:
        log.info('SWIM ingestion is disabled (ENABLE_SWIM_INGESTOR/SWIM_ENABLED != true). Exiting.')
        sys.exit(0)

    config_check_only = _get('SWIM_CONFIG_CHECK_ONLY', 'false').strip().lower() == 'true'
    if config_check_only:
        _run_config_check(log)
        sys.exit(0)

    _log_redacted_config(log)

    missing = _validate_config(log)
    if missing:
        log.warning('SWIM_ENABLED=true but required configuration is missing:')
        for item in missing:
            log.warning('  missing: %s', item)
        log.warning(
            'Set these variables in deploy.env (preferred) or swim.env '
            'and re-run with the SWIM compose override. Exiting without connecting.'
        )
        sys.exit(0)

    log.info('All required SWIM configuration is present.')

    probe_only = _get('SWIM_PROBE_ONLY', 'false').strip().lower() == 'true'

    if probe_only:
        _run_probe(log)
        log.info('Probe complete. Exiting.')
        sys.exit(0)

    # Full ingestion not implemented yet
    log.info('SWIM_PROBE_ONLY is not set.')
    log.info('Full continuous ingestion is not implemented yet (Phase 2B).')
    log.info('To run a bounded probe, set SWIM_PROBE_ONLY=true.')
    sys.exit(0)


if __name__ == '__main__':
    main()
