#!/usr/bin/env python3
"""
FlightConn SWIM Ingestor

Optional sidecar for FAA SWIM recent flight activity ingestion.

Behavior by configuration (auto-detect mode — normal users):
  FAA_USER, FAA_PASS, and at least one QUEUE_* set → probe mode runs automatically
  Any of those blank or missing → exits 0, logs what is missing

Advanced override behavior (ENABLE_SWIM_INGESTOR / SWIM_ENABLED):
  ENABLE_SWIM_INGESTOR=false or SWIM_ENABLED=false → always exits 0 (explicit disable)
  ENABLE_SWIM_INGESTOR=true or SWIM_ENABLED=true → enables ingestion regardless of creds
    (used internally; normal users should rely on auto-detect instead)

  SWIM_ENABLED is accepted for backward compatibility with swim.env users.
  ENABLE_SWIM_INGESTOR takes precedence when both are set.

Environment variables (all optional for the main app):
  ENABLE_SWIM_INGESTOR      Advanced override — 'true'/'false' (preferred over SWIM_ENABLED)
  SWIM_ENABLED              Accepted alias for ENABLE_SWIM_INGESTOR (legacy)
  FAA_USER                  FAA SWIM account username
  FAA_PASS                  FAA SWIM account password

  Broker address (handled internally — override only if FAA provides a different URL):
  FAA_URL                   Full broker URL (default: tcps://ems1.swim.faa.gov:55443)
                            (FlightConn uses the same Solace PubSub+ style connection pattern as Aviation Radar.)
  FAA_SWIM_BROKER_URL       Alias for FAA_URL (legacy swim.env form)
  FAA_SWIM_HOST             Hostname only (alternative to URL forms)
  FAA_SWIM_PORT             Port (default 61614 for ssl, 61613 for tcp)
  FAA_SWIM_PROTOCOL         ssl (default) | tcp | tls | plain

  QUEUE_SFDPS               SWIM SFDPS queue name (from FAA)
  QUEUE_STDDS               SWIM STDDS queue name (from FAA)
  QUEUE_TFMS                SWIM TFMS queue name (from FAA)
  SWIM_CONFIG_CHECK_ONLY    If 'true', validate config and exit without connecting
  SWIM_PROBE_ONLY           If 'true'/'false', override probe mode (default: true)
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
import time
import signal
import threading
import argparse
from datetime import datetime, timezone

_QUEUE_VARS = ('QUEUE_SFDPS', 'QUEUE_STDDS', 'QUEUE_TFMS')

# Event to coordinate graceful shutdown
_stop_event = threading.Event()


def _signal_handler(sig, frame):
    """Handle termination signals."""
    if not _stop_event.is_set():
        logging.getLogger('swim-ingestor').info('Shutdown signal received. Gracefully stopping...')
        _stop_event.set()


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
        log.info('  broker:                default (tcps://ems1.swim.faa.gov:55443)')
    log.info('  username present:      %s', 'yes' if _present('FAA_USER') else 'no')
    log.info('  password present:      %s', 'yes' if _present('FAA_PASS') else 'no')
    log.info('  queues configured:     %s',
             ', '.join(configured_queues) if configured_queues else 'none')
    
    # Mode detection for logging
    mode = _get('SWIM_MODE', '').lower()
    probe_only_env = _get('SWIM_PROBE_ONLY', '').lower()
    
    if mode == 'probe':
        log_mode = 'probe (SWIM_MODE=probe)'
    elif mode == 'continuous':
        log_mode = 'continuous (SWIM_MODE=continuous)'
    elif probe_only_env == 'true':
        log_mode = 'probe (SWIM_PROBE_ONLY=true)'
    elif probe_only_env == 'false':
        log_mode = 'continuous (SWIM_PROBE_ONLY=false)'
    else:
        log_mode = 'continuous (auto-detected)'
    
    log.info('  SWIM_MODE:             %s', log_mode)
    log.info('  SWIM_LOG_LEVEL:        %s', _get('SWIM_LOG_LEVEL', 'INFO'))


def _validate_config(log: logging.Logger) -> list:
    """Return a list of missing required config items."""
    missing = []
    if not _present('FAA_USER'):
        missing.append('FAA_USER')
    if not _present('FAA_PASS'):
        missing.append('FAA_PASS')
    if not any(_present(q) for q in _QUEUE_VARS):
        missing.append('at least one of: ' + ', '.join(_QUEUE_VARS))
    return missing


def _run_config_check(log: logging.Logger) -> None:
    """Validate and display configuration without connecting to FAA."""
    log.info('=== SWIM configuration check (SWIM_CONFIG_CHECK_ONLY=true) ===')
    _log_redacted_config(log)

    # Show broker parse result — safe to compute, no connection made
    try:
        from connector_faa_solace import _resolve_broker
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
    max_messages   = int(_get('SWIM_PROBE_MAX_MESSAGES', '50'))

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
        from connector_faa_solace import run_probe
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
            log.info('Probe result: connected=yes, messages_received=%d, parsed=%d, inserted=%d, route_ready=%d, partial=%d, skipped=%d, parse_errors=%d',
                     probe_result.messages_received, probe_result.parsed_successfully, probe_result.inserted_or_updated,
                     probe_result.route_ready_count, probe_result.partial_count,
                     probe_result.skipped_missing_route + probe_result.skipped_unknown_type, probe_result.parse_errors)
            
            log.info('  queues: %s', ', '.join([f"{k}: {v}" for k, v in probe_result.counts_by_label.items()]))
            log.info('  message types: %s', ', '.join([f"{k}: {v}" for k, v in sorted(probe_result.message_types.items(), key=lambda x: x[1], reverse=True)[:10]]))
            log.info('  identifier tags observed: %s', ', '.join([f"{k}: {v}" for k, v in sorted(probe_result.ids_observed.items(), key=lambda x: x[1], reverse=True)]))
            
            if probe_result.skip_reason_counts:
                log.info('  skip reasons: %s', ', '.join([f"{k}: {v}" for k, v in probe_result.skip_reason_counts.items()]))
            
            verbose = _get('SWIM_VERBOSE_MESSAGES', 'false').lower() == 'true'
            sample_limit = int(_get('SWIM_MESSAGE_LOG_SAMPLE_LIMIT', '10'))
            
            for i, meta in enumerate(probe_result.messages_metadata):
                # Always log to DEBUG. Log to INFO only if verbose and under sample limit.
                msg_level = logging.INFO if (verbose and i < sample_limit) else logging.DEBUG
                log.log(
                    msg_level,
                    '  msg: queue=%s received_at=%s payload_bytes=%d '
                    'msg_type=%s parsed=%s skip_reason=%s records_extracted=%d collections=%d candidates=%d',
                    meta['queue_label'],
                    meta['received_at'],
                    meta['payload_bytes'],
                    meta.get('msg_type', 'unknown'),
                    meta.get('parsed', False),
                    meta.get('skip_reason') or 'N/A',
                    meta.get('records_extracted', 0),
                    meta.get('collections_unpacked', 0),
                    meta.get('candidates_found', 0)
                )
            if verbose and len(probe_result.messages_metadata) > sample_limit:
                log.info('  ... (further %d messages at DEBUG level only)', len(probe_result.messages_metadata) - sample_limit)
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


def _run_continuous(log: logging.Logger) -> None:
    """Run continuous ingestion mode with periodic aggregation and cleanup."""
    agg_interval   = int(_get('SWIM_AGGREGATION_INTERVAL_SECONDS', '900'))
    retention_days = int(_get('SWIM_RETENTION_DAYS', '35'))
    summary_interval = int(_get('SWIM_SUMMARY_INTERVAL_SECONDS', '60'))
    
    log.info('Starting Phase 3 continuous ingestion.')
    log.info('Aggregation interval: %ds', agg_interval)
    log.info('Summary interval: %ds', summary_interval)
    log.info('Retention period: %d days', retention_days)

    # ── Ensure schema is applied ──────────────────────────────────────────
    from db import apply_schema, cleanup_old_data, update_ingestion_run_metrics
    apply_schema()
    
    # ── Run initial aggregation ───────────────────────────────────────────
    from aggregator import run_all_aggregations
    log.info('Running initial startup aggregation...')
    run_all_aggregations()

    # ── Start ingestion run record ────────────────────────────────────────
    from db import start_ingestion_run, finish_ingestion_run
    run_id = start_ingestion_run('FAA_SWIM_CONTINUOUS')
    log.info('Continuous ingestion run started: id=%d', run_id)

    # ── Start continuous connection ───────────────────────────────────────
    from connector_faa_solace import start_continuous_ingestion
    
    try:
        result, objects = start_continuous_ingestion(_stop_event)
    except Exception as e:
        log.error('Failed to start continuous ingestion: %s: %s', type(e).__name__, e)
        finish_ingestion_run(run_id, 'failed', {}, str(e))
        return

    stunnel_proc, messaging_services, receivers, listener = objects

    last_agg_time = time.monotonic()
    last_cleanup_time = time.monotonic()
    last_summary_time = time.monotonic()
    
    last_total_recv = 0
    stall_count = 0
    STALL_THRESHOLD_MINUTES = int(_get('SWIM_STALL_THRESHOLD_MINUTES', '10'))

    verbose = _get('SWIM_VERBOSE_MESSAGES', 'false').lower() == 'true'
    sample_limit = int(_get('SWIM_MESSAGE_LOG_SAMPLE_LIMIT', '5'))
    
    try:
        while not _stop_event.is_set():
            now = time.monotonic()
            
            # Summary and DB update schedule
            if now - last_summary_time >= summary_interval:
                with listener._lock:
                    current_recv = result.messages_received
                    counts = {
                        'messages_recv':   current_recv,
                        'messages_ok':     result.parsed_successfully,
                        'messages_err':    result.parse_errors,
                        'flights_new':     result.inserted_or_updated,
                        'flights_updated': 0,
                    }
                    
                    # Stall detection
                    if current_recv == last_total_recv:
                        stall_count += 1
                        if stall_count >= STALL_THRESHOLD_MINUTES:
                            log.warning('Stall detected: No messages received in the last %d minutes.', 
                                        stall_count * summary_interval // 60)
                            # Could attempt reconnect here, but for now just warn
                    else:
                        stall_count = 0
                    
                    last_total_recv = current_recv

                    # Detailed summary
                    log.info('Status: recv=%d parsed=%d route_ready=%d partial=%d inserted=%d errors=%d',
                             result.messages_received, result.parsed_successfully,
                             result.route_ready_count, result.partial_count,
                             result.inserted_or_updated, result.parse_errors)
                    
                    log.info('  queues: %s', ', '.join([f"{k}: {v}" for k, v in result.counts_by_label.items()]))
                    
                    if result.last_message_at:
                        log.info('  last message received: %s (%ds ago)', 
                                 result.last_message_at.strftime('%Y-%m-%d %H:%M:%S'),
                                 int((datetime.now(timezone.utc) - result.last_message_at).total_seconds()))
                    else:
                        log.info('  last message received: never')

                    log.info('  message types: %s', ', '.join([f"{k}: {v}" for k, v in sorted(result.message_types.items(), key=lambda x: x[1], reverse=True)[:10]]))
                    log.info('  identifier tags observed: %s', ', '.join([f"{k}: {v}" for k, v in sorted(result.ids_observed.items(), key=lambda x: x[1], reverse=True)]))

                    if result.skip_reason_counts:
                        log.info('  skip reasons: %s', ', '.join([f"{k}: {v}" for k, v in result.skip_reason_counts.items()]))
                    
                    if verbose:
                        # Log sampled recent messages at DEBUG
                        for i, meta in enumerate(listener.messages[-sample_limit:]):
                            log.debug(
                                '  msg: queue=%s received_at=%s payload_bytes=%d '
                                'msg_type=%s parsed=%s skip_reason=%s records_extracted=%d',
                                meta['queue_label'], meta['received_at'], meta['payload_bytes'],
                                meta.get('msg_type', 'unknown'), meta.get('parsed', False),
                                meta.get('skip_reason') or 'N/A', meta.get('records_extracted', 0)
                            )

                update_ingestion_run_metrics(run_id, counts)
                last_summary_time = now

            # Aggregation schedule
            if now - last_agg_time >= agg_interval:
                log.info('Running scheduled aggregation...')
                run_all_aggregations()
                last_agg_time = now
                
            # Retention cleanup schedule (once every 12 hours)
            if now - last_cleanup_time >= 43200:
                cleanup_old_data(retention_days)
                last_cleanup_time = now

            # Wait for next check, but respond to stop_event
            _stop_event.wait(timeout=min(10, summary_interval))
            
    except Exception as e:
        log.error('Continuous loop crashed: %s: %s', type(e).__name__, str(e)[:200])
        finish_ingestion_run(run_id, 'failed', {}, str(e)[:200])
    finally:
        log.info('Shutting down continuous ingestion...')
        for r in receivers:
            try: r.terminate()
            except: pass
        for s in messaging_services:
            try: s.disconnect()
            except: pass
        stunnel_proc.terminate()
        
        # Final aggregation
        log.info('Running final shutdown aggregation...')
        try: run_all_aggregations()
        except: pass
        
        # Final counts
        with listener._lock:
            counts = {
                'messages_recv':   result.messages_received,
                'messages_ok':     result.parsed_successfully,
                'messages_err':    result.parse_errors,
                'flights_new':     result.inserted_or_updated,
                'flights_updated': 0,
            }
        finish_ingestion_run(run_id, 'completed', counts)
        log.info('Continuous run closed.')


def main() -> None:
    # ── Command line argument parsing ─────────────────────────────────────
    # This must happen before any SWIM-specific logic or config checks.
    parser = argparse.ArgumentParser(description='FlightConn SWIM Ingestor')
    subparsers = parser.add_subparsers(dest='command', help='Commands')
    
    # cleanup-duplicates command
    cleanup_parser = subparsers.add_parser('cleanup-duplicates', help='Merge duplicate flight observations')
    cleanup_parser.add_argument('--dry-run', action='store_true', default=True, help='Dry run (default)')
    cleanup_parser.add_argument('--apply', action='store_false', dest='dry_run', help='Actually apply changes')

    args = parser.parse_args()

    # ── Maintenance Commands ─────────────────────────────────────────────
    if args.command == 'cleanup-duplicates':
        log = _setup_logging()
        if not args.dry_run:
            log.warning("!!! DESTRUCTIVE MAINTENANCE WARNING !!!")
            log.warning("Operation: Merge and delete duplicate SWIM observations.")
            log.warning("Impact: This will permanently modify 'observed_flights' and 'observed_flight_events'.")
            log.warning("Requirement: You MUST have a current database backup before proceeding.")
            log.warning("Safety: Run './backup.sh' now if you have not already done so.")
            log.warning("")
            log.warning("Press Ctrl+C within 10 seconds to abort, or wait to proceed with --apply...")
            try:
                for i in range(10, 0, -1):
                    log.warning(f"  Starting in {i} seconds...")
                    time.sleep(1)
            except KeyboardInterrupt:
                log.info("Cleanup aborted by user.")
                sys.exit(0)
            log.warning("Proceeding with --apply...")

        from db import merge_duplicate_observations
        results = merge_duplicate_observations(dry_run=args.dry_run)
        log.info("Cleanup results: %s", results)
        if args.dry_run:
            log.info("Note: This was a dry-run. No data was modified. Use --apply to perform the merge.")
        sys.exit(0)

    # ── Normal Operation Setup ───────────────────────────────────────────
    log = _setup_logging()
    
    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    # ── Determine whether SWIM is enabled ────────────────────────────────
    # Advanced override: ENABLE_SWIM_INGESTOR (deploy.env) or SWIM_ENABLED (legacy).
    # ENABLE_SWIM_INGESTOR takes precedence when set to an explicit value.
    if _present('ENABLE_SWIM_INGESTOR'):
        explicit_flag = _get('ENABLE_SWIM_INGESTOR').strip().lower()
        if explicit_flag == 'false':
            log.info('SWIM ingestion explicitly disabled (ENABLE_SWIM_INGESTOR=false). Exiting.')
            sys.exit(0)
        swim_enabled = explicit_flag == 'true'
    elif _present('SWIM_ENABLED'):
        explicit_flag = _get('SWIM_ENABLED').strip().lower()
        if explicit_flag == 'false':
            log.info('SWIM ingestion explicitly disabled (SWIM_ENABLED=false). Exiting.')
            sys.exit(0)
        swim_enabled = explicit_flag == 'true'
    else:
        # Auto-detect: enable if FAA_USER + FAA_PASS + ≥1 QUEUE_* are all set
        swim_enabled = (
            _present('FAA_USER') and
            _present('FAA_PASS') and
            any(_present(q) for q in _QUEUE_VARS)
        )

    if not swim_enabled:
        log.info('SWIM ingestion is not ready. Set FAA_USER, FAA_PASS, and at least one QUEUE_* to enable.')
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

    # Determine mode: Default is continuous if credentials are OK.
    # SWIM_MODE=probe or SWIM_PROBE_ONLY=true overrides to probe mode.
    mode = _get('SWIM_MODE', '').lower()
    probe_only_env = _get('SWIM_PROBE_ONLY', '').lower()

    if mode == 'probe':
        probe_only = True
    elif mode == 'continuous':
        probe_only = False
    elif probe_only_env == 'true':
        probe_only = True
    elif probe_only_env == 'false':
        probe_only = False
    else:
        # Default for normal users with creds is continuous
        probe_only = False

    if probe_only:
        _run_probe(log)
        log.info('Probe complete. Exiting.')
        sys.exit(0)

    _run_continuous(log)
    sys.exit(0)


if __name__ == '__main__':
    main()
