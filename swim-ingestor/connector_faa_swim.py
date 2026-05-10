"""
FlightConn SWIM Ingestor — FAA SWIM connector (Phase 2A: probe mode)

Connects to the FAA SWIM SCDS (SWIM Cloud Distribution Service) broker
using STOMP over TLS and subscribes to configured queues in a bounded
probe session.

Protocol assumptions (confirmed from FAA SWIM documentation):
  - Broker:    Solace PubSub+ (FAA SCDS)
  - Protocol:  STOMP 1.1 / 1.2 over TLS
  - Port:      61614 (STOMP+TLS) or 443 (WebSTOMP — less common)
  - Auth:      STOMP CONNECT with login/passcode (FAA_USER / FAA_PASS)
  - Queue dst: /queue/<queue-name-from-faa>
  - TLS:       Required for production. Self-signed certs may need ssl_cert_reqs=CERT_NONE.
  - Heartbeat: Negotiate 4000ms send/receive

If the connection fails with an SSL certificate error, set:
  SWIM_SSL_VERIFY=false
in swim.env. This disables certificate verification (use only for initial testing).

Phase 2A only implements probe mode — bounded time / message count.
Full normalization into observed_flights is Phase 2B.
"""

import os
import ssl
import socket
import threading
import logging
from datetime import datetime, timezone

log = logging.getLogger('swim-ingestor.connector')

# Maps environment variable name → short label used in logging and reports.
# Labels are safe to log; env var values (actual queue names) are not logged.
_QUEUE_VARS: dict[str, str] = {
    'QUEUE_SFDPS': 'SFDPS',
    'QUEUE_STDDS': 'STDDS',
    'QUEUE_TFMS':  'TFMS',
}

# STOMP destination prefix. FAA/Solace queue endpoints use /queue/<name>.
# If FAA provides a different prefix (e.g. a topic path), override via
# SWIM_DEST_PREFIX env var.
_DEFAULT_DEST_PREFIX = '/queue/'


class ProbeResult:
    """Container for probe run output — no credentials or payloads."""

    def __init__(self):
        self.connected: bool = False
        self.queues_attempted: list[str] = []      # label names only
        self.messages_received: int = 0
        self.messages_metadata: list[dict] = []    # safe metadata per message
        self.counts_by_label: dict[str, int] = {}  # {label: count}
        self.errors: list[str] = []
        self.error_summary: str = ''               # brief, redacted

    def as_dict(self) -> dict:
        return {
            'connected': self.connected,
            'queues_attempted': self.queues_attempted,
            'messages_received': self.messages_received,
            'counts_by_label': self.counts_by_label,
            'messages_metadata': self.messages_metadata,
            'errors': self.errors,
            'error_summary': self.error_summary,
        }


def _parse_broker_url(url: str) -> tuple:
    """
    Parse broker URL → (host, port, use_ssl).

    Accepts:
      tcps://host:port   → TLS (FAA SWIM SCDS default scheme)
      ssl://host:port    → TLS on given port
      tcp://host:port    → plain TCP (non-production only)
      host:port          → defaults to TLS
      host               → defaults to TLS on port 61614
    """
    url = url.strip()
    use_ssl = True

    if url.startswith('tcps://'):
        url = url[7:]
    elif url.startswith('ssl://'):
        url = url[6:]
    elif url.startswith('tcp://'):
        url = url[6:]
        use_ssl = False

    if ':' in url:
        host, port_str = url.rsplit(':', 1)
        try:
            port = int(port_str)
        except ValueError:
            host = url
            port = 61614 if use_ssl else 61613
    else:
        host = url
        port = 61614 if use_ssl else 61613

    return host, port, use_ssl


def _resolve_broker() -> tuple:
    """
    Determine (host, port, use_ssl, source) from env vars.

    Priority:
      1. FAA_URL          — deploy.env preferred form (tcps://host:port)
      2. FAA_SWIM_BROKER_URL — legacy swim.env form
      3. FAA_SWIM_HOST + FAA_SWIM_PORT + FAA_SWIM_PROTOCOL — split form

    FAA_SWIM_PROTOCOL accepts: ssl (default), tls, tcps, tcp, stomp, plain
      ssl/tls/tcps → use_ssl=True,  default port 61614
      tcp/stomp/plain → use_ssl=False, default port 61613

    Returns (host, port, use_ssl, source_label).
    Raises ValueError with a human-readable message if config is insufficient.
    """
    # FAA_URL (deploy.env form) takes precedence, then legacy FAA_SWIM_BROKER_URL
    broker_url = (
        os.environ.get('FAA_URL', '').strip()
        or os.environ.get('FAA_SWIM_BROKER_URL', '').strip()
    )
    if broker_url:
        source = 'FAA_URL' if os.environ.get('FAA_URL', '').strip() else 'FAA_SWIM_BROKER_URL'
        host, port, use_ssl = _parse_broker_url(broker_url)
        return host, port, use_ssl, source

    faa_host = os.environ.get('FAA_SWIM_HOST', '').strip()
    if not faa_host:
        raise ValueError(
            'No broker address configured. Set FAA_SWIM_BROKER_URL '
            'or FAA_SWIM_HOST (with optional FAA_SWIM_PORT and FAA_SWIM_PROTOCOL).'
        )

    protocol = os.environ.get('FAA_SWIM_PROTOCOL', 'ssl').strip().lower()
    use_ssl = protocol not in ('tcp', 'stomp', 'plain')
    default_port = 61614 if use_ssl else 61613

    port_str = os.environ.get('FAA_SWIM_PORT', '').strip()
    try:
        port = int(port_str) if port_str else default_port
    except ValueError:
        raise ValueError(
            f'FAA_SWIM_PORT must be an integer, got: {port_str!r}'
        )

    return faa_host, port, use_ssl, 'FAA_SWIM_HOST/PORT/PROTOCOL'


def _redact_error(err: str) -> str:
    """
    Remove obvious credential-like tokens from an error string.
    FAA_USER and FAA_PASS values are removed if they appear.
    """
    result = err
    for var in ('FAA_USER', 'FAA_PASS', 'FAA_SWIM_BROKER_URL',
                'QUEUE_SFDPS', 'QUEUE_STDDS', 'QUEUE_TFMS'):
        val = os.environ.get(var, '').strip()
        if val and val in result:
            result = result.replace(val, f'[{var}]')
    return result[:300]


class _ProbeListener:
    """
    STOMP connection listener for bounded probe mode.
    Logs only safe message metadata — never payload bodies.
    """

    def __init__(self, max_messages: int, label_map: dict, dest_prefix: str):
        # label_map: {'/queue/actual-queue-name': 'SFDPS'}
        self._max = max_messages
        self._label_map = label_map
        self._dest_prefix = dest_prefix
        self.messages: list[dict] = []
        self.errors: list[str] = []
        self.done = threading.Event()
        self._lock = threading.Lock()

    def on_message(self, frame) -> None:
        dest = frame.headers.get('destination', '')

        # Map destination → safe label; never log the raw destination value
        label = self._label_map.get(dest, 'UNKNOWN')

        # Payload size only — not content
        body = frame.body
        if isinstance(body, str):
            payload_bytes = len(body.encode('utf-8', errors='replace'))
        else:
            payload_bytes = len(body) if body else 0

        # Safe header fields only
        content_type = frame.headers.get('content-type', 'unknown')
        msg_type = (
            frame.headers.get('msgType')
            or frame.headers.get('JMSType')
            or frame.headers.get('NasType')
            or frame.headers.get('type')
            or 'unknown'
        )

        meta = {
            'queue_label':  label,
            'received_at':  datetime.now(timezone.utc).isoformat(),
            'payload_bytes': payload_bytes,
            'content_type': content_type[:80] if content_type else 'unknown',
            'msg_type':     str(msg_type)[:80],
        }

        with self._lock:
            self.messages.append(meta)
            if len(self.messages) >= self._max:
                self.done.set()

    def on_error(self, frame) -> None:
        brief = frame.headers.get('message', 'STOMP ERROR frame received')
        brief_lower = brief.lower()
        if any(w in brief_lower for w in
               ('auth', 'login', 'credential', 'unauthorized', 'forbidden', 'not allowed')):
            self.errors.append('Authentication/authorization failed: ' + _redact_error(str(brief)))
        else:
            self.errors.append('STOMP error: ' + _redact_error(str(brief)))
        self.done.set()

    def on_disconnected(self) -> None:
        self.done.set()

    def on_heartbeat_timeout(self) -> None:
        self.errors.append('heartbeat timeout')
        self.done.set()


def run_probe(probe_seconds: int = 30, max_messages: int = 5) -> ProbeResult:
    """
    Connect to FAA SWIM, subscribe to configured queues, and collect
    up to max_messages messages within probe_seconds.

    Returns a ProbeResult with safe metadata only.
    No payload bodies are stored or logged.
    """
    import stomp

    result = ProbeResult()

    # ── Read config (existence only; values not logged) ───────────────────
    faa_user    = os.environ.get('FAA_USER', '').strip()
    faa_pass    = os.environ.get('FAA_PASS', '').strip()
    ssl_verify  = os.environ.get('SWIM_SSL_VERIFY', 'true').strip().lower() != 'false'
    dest_prefix = os.environ.get('SWIM_DEST_PREFIX', _DEFAULT_DEST_PREFIX)
    heartbeat_ms = int(os.environ.get('SWIM_HEARTBEAT_MS', '4000'))

    try:
        host, port, use_ssl, broker_source = _resolve_broker()
    except ValueError as e:
        result.error_summary = str(e)
        result.errors.append(result.error_summary)
        log.error('Broker config error: %s', result.error_summary)
        return result

    # Build label_map: {'/queue/actual-value': 'SFDPS'}
    label_map: dict[str, str] = {}
    subscriptions: list[tuple[str, str]] = []   # [(destination, label)]
    for var_name, label in _QUEUE_VARS.items():
        q_val = os.environ.get(var_name, '').strip()
        if q_val:
            dest = f'{dest_prefix}{q_val}'
            label_map[dest] = label
            subscriptions.append((dest, label))
            result.queues_attempted.append(label)

    if not subscriptions:
        result.error_summary = 'No queues configured (QUEUE_SFDPS/STDDS/TFMS all empty)'
        result.errors.append(result.error_summary)
        return result

    log.info('Broker: port=%d ssl=%s source=%s', port, use_ssl, broker_source)
    log.info('Queues to probe: %s', ', '.join(result.queues_attempted))

    listener = _ProbeListener(max_messages, label_map, dest_prefix)

    # ── Build SSL context ─────────────────────────────────────────────────
    ssl_context = None
    if use_ssl:
        ssl_context = ssl.create_default_context()
        if not ssl_verify:
            log.warning('SSL certificate verification is disabled (SWIM_SSL_VERIFY=false). '
                        'Use only for initial connectivity testing.')
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

    # ── Connect ───────────────────────────────────────────────────────────
    try:
        conn = stomp.Connection(
            host_and_ports=[(host, port)],
            use_ssl=use_ssl,
            ssl_context=ssl_context if use_ssl else None,
            heartbeats=(heartbeat_ms, heartbeat_ms),
            reconnect_attempts_max=0,   # Probe: no reconnect on failure
        )
        conn.set_listener('probe', listener)

        log.info('Connecting to SWIM broker...')
        conn.connect(
            login=faa_user,
            passcode=faa_pass,
            wait=True,
            headers={'client-id': 'flightconn-probe'},
        )
        result.connected = True
        log.info('Connected successfully.')

    except stomp.exception.ConnectFailedException as e:
        cause = getattr(e, '__cause__', None)
        if isinstance(cause, socket.gaierror):
            result.error_summary = 'DNS failure: cannot resolve broker hostname'
            hint = ('Check that FAA_SWIM_BROKER_URL or FAA_SWIM_HOST contains the '
                    'correct hostname from your FAA SWIM account documentation.')
        elif isinstance(cause, ssl.SSLError):
            result.error_summary = f'TLS handshake failed ({cause.reason})'
            hint = ('Try SWIM_SSL_VERIFY=false for initial connectivity testing. '
                    'Use true in production.')
        elif isinstance(cause, ConnectionRefusedError):
            result.error_summary = f'Connection refused on port {port}'
            hint = ('Confirm the port in your FAA SWIM account documentation '
                    '(default STOMP+TLS is 61614).')
        else:
            result.error_summary = (
                f'TCP connection failed: {type(cause).__name__ if cause else type(e).__name__}'
            )
            hint = 'Check network/firewall access to the broker host and port.'
        result.errors.append(_redact_error(str(e)))
        log.error('SWIM connection failed: %s', result.error_summary)
        log.error('Hint: %s', hint)
        return result

    except ssl.SSLError as e:
        result.error_summary = f'TLS error: {e.reason}'
        result.errors.append(_redact_error(str(e)))
        log.error('TLS error: %s', result.error_summary)
        log.error('Try SWIM_SSL_VERIFY=false for initial connectivity testing.')
        return result

    except Exception as e:
        err_type = type(e).__name__
        err_msg = _redact_error(str(e))
        result.error_summary = f'Connection error: {err_type}: {err_msg[:150]}'
        result.errors.append(result.error_summary)
        log.error('SWIM connection error: %s: %s', err_type, err_msg)
        log.error('Check FAA_USER/FAA_PASS credentials and broker configuration.')
        return result

    # ── Subscribe ─────────────────────────────────────────────────────────
    try:
        for i, (dest, label) in enumerate(subscriptions):
            conn.subscribe(destination=dest, id=str(i + 1), ack='auto')
            log.info('Subscribed: %s', label)
    except Exception as e:
        err_msg = _redact_error(str(e))
        result.error_summary = f'Subscribe error: {type(e).__name__}: {err_msg[:100]}'
        result.errors.append(result.error_summary)
        log.error('Subscribe failed: %s', result.error_summary)
        log.error('Check that queue names in QUEUE_* env vars match FAA-provided names.')
        try:
            conn.disconnect()
        except Exception:
            pass
        return result

    # ── Wait for messages ─────────────────────────────────────────────────
    log.info('Probe running: up to %ds or %d message(s)...', probe_seconds, max_messages)
    listener.done.wait(timeout=probe_seconds)

    # ── Disconnect ────────────────────────────────────────────────────────
    try:
        conn.disconnect()
    except Exception:
        pass

    # ── Summarize ─────────────────────────────────────────────────────────
    result.messages_received = len(listener.messages)
    result.messages_metadata = listener.messages
    result.errors.extend(listener.errors)

    for meta in listener.messages:
        label = meta['queue_label']
        result.counts_by_label[label] = result.counts_by_label.get(label, 0) + 1

    if result.messages_received == 0 and not result.errors:
        log.info('Probe complete: no messages received in %ds. '
                 'Queue may be empty or inactive during this window.', probe_seconds)
    else:
        for label, count in result.counts_by_label.items():
            log.info('  %s: %d message(s)', label, count)

    return result
