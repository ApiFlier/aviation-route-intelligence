"""
FlightConn SWIM Ingestor — FAA SWIM Solace connector (Phase 2A: probe mode)

Connects to the FAA SWIM SCDS (SWIM Cloud Distribution Service) broker
using the Solace PubSub+ API and stunnel, mirroring the Aviation Radar approach.
"""

import os
import time
import socket
import logging
import threading
import subprocess
from datetime import datetime, timezone
from solace.messaging.messaging_service import MessagingService, ReconnectionAttemptListener, ReconnectionListener, ServiceEvent, ServiceInterruptionListener
from solace.messaging.resources.queue import Queue
from solace.messaging.config.retry_strategy import RetryStrategy
from solace.messaging.receiver.message_receiver import MessageHandler, InboundMessage

from normalizer import parse_swim_message, is_route_ready
from db import upsert_observed_flight

log = logging.getLogger('swim-ingestor.connector')

_QUEUE_VARS: dict[str, str] = {
    'QUEUE_SFDPS': 'SFDPS',
    'QUEUE_STDDS': 'STDDS',
    'QUEUE_TFMS':  'TFMS',
}

class ProbeResult:
    def __init__(self):
        self.connected: bool = False
        self.queues_attempted: list[str] = []
        self.messages_received: int = 0
        self.messages_metadata: list[dict] = []
        self.counts_by_label: dict[str, int] = {}
        self.errors: list[str] = []
        self.error_summary: str = ''
        
        # Phase 2B Parsing stats
        self.parsed_successfully: int = 0
        self.inserted_or_updated: int = 0
        self.route_ready_count: int = 0
        self.partial_count: int = 0
        self.skipped_missing_route: int = 0
        self.skipped_unknown_type: int = 0
        self.parse_errors: int = 0

def _resolve_broker() -> tuple:
    # Use default 55443 as in Radar
    return 'ems1.swim.faa.gov', 55443, True, 'default'

def _generate_stunnel_conf(ems1_host: str, ems2_host: str = 'ems2.swim.faa.gov', ems_port: int = 55443) -> str:
    conf_path = '/tmp/stunnel.conf'
    conf_content = f"""
foreground = yes
client = yes
verify = 0

[ems1]
accept = 127.0.0.1:55003
connect = {ems1_host}:{ems_port}
sni = {ems1_host}

[ems2]
accept = 127.0.0.1:55004
connect = {ems2_host}:{ems_port}
sni = {ems2_host}
"""
    with open(conf_path, 'w') as f:
        f.write(conf_content)
    return conf_path

class _IngestMessageHandler(MessageHandler):
    def __init__(self, label: str, listener, result: ProbeResult, max_messages: int = None):
        self._label = label
        self._listener = listener
        self._result = result
        self._max = max_messages

    def on_message(self, message: InboundMessage):
        payload = message.get_payload_as_bytes()
        payload_bytes = len(payload) if payload else 0
        
        parsed_res = parse_swim_message(self._label, payload)
        
        skip_reason = parsed_res.get('skip_reason')
        if parsed_res['success'] and not skip_reason and parsed_res.get('records'):
            # Grab the first failure reason to help debug
            failures = [r.get('skip_reason') for r in parsed_res['records'] if not r.get('success')]
            if failures:
                skip_reason = failures[0]
                
        meta = {
            'queue_label': self._label,
            'received_at': datetime.now(timezone.utc).isoformat(),
            'payload_bytes': payload_bytes,
            'content_type': 'solace',
            'msg_type': parsed_res.get('message_type', 'unknown'),
            'parsed': parsed_res['success'],
            'skip_reason': skip_reason,
            'records_extracted': len([r for r in parsed_res.get('records', []) if r.get('success')]),
            'collections_unpacked': parsed_res.get('stats', {}).get('message_collections', 0),
            'candidates_found': parsed_res.get('stats', {}).get('candidates', 0),
        }
        
        with self._listener._lock:
            self._result.messages_received += 1
            if parsed_res['success']:
                # The payload parsed successfully, process the extracted records
                for rec in parsed_res['records']:
                    if rec['success']:
                        self._result.parsed_successfully += 1
                        flight_data = rec['flight_data']
                        
                        if is_route_ready(flight_data):
                            self._result.route_ready_count += 1
                        else:
                            self._result.partial_count += 1
                            
                        if upsert_observed_flight(flight_data):
                            self._result.inserted_or_updated += 1
                    else:
                        reason = rec.get('skip_reason', '')
                        if 'Missing origin' in reason or 'Missing GUFI' in reason or 'Missing ACID' in reason or 'non-IATA' in reason:
                            self._result.skipped_missing_route += 1
                        else:
                            self._result.skipped_unknown_type += 1
            else:
                # The whole payload failed to parse
                reason = parsed_res.get('skip_reason', '')
                if 'Parse Error' in reason or 'Syntax Error' in reason:
                    self._result.parse_errors += 1
                else:
                    self._result.skipped_unknown_type += 1

            self._listener.messages.append(meta)
            # Keep only last 100 metadata items to avoid memory bloat in continuous mode
            if len(self._listener.messages) > 100:
                self._listener.messages.pop(0)

            if self._max and self._result.messages_received >= self._max:
                self._listener.done.set()

class _ProbeMessageHandler(_IngestMessageHandler):
    # Backward compatibility for run_probe
    pass

class _ProbeListener:
    def __init__(self, max_messages: int = None):
        self._max = max_messages
        self.messages: list[dict] = []
        self.errors: list[str] = []
        self.done = threading.Event()
        self._lock = threading.Lock()

class ServiceEventHandler(ReconnectionListener, ReconnectionAttemptListener, ServiceInterruptionListener):
    def on_reconnected(self, e: ServiceEvent):
        log.info("Solace service reconnected: %s", e)
    def on_reconnecting(self, e: ServiceEvent):
        log.info("Solace service reconnecting...")
    def on_service_interrupted(self, e: ServiceEvent):
        log.warning("Solace service interrupted: %s", e)

def run_probe(probe_seconds: int = 30, max_messages: int = 5) -> ProbeResult:
    result = ProbeResult()

    faa_user = os.environ.get('FAA_USER', '').strip()
    faa_pass = os.environ.get('FAA_PASS', '').strip()

    host, port, use_ssl, broker_source = _resolve_broker()

    stunnel_conf = _generate_stunnel_conf(host, 'ems2.swim.faa.gov', port)
    stunnel_proc = subprocess.Popen(['stunnel4', stunnel_conf], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    # Wait for stunnel to start
    time.sleep(2)

    listener = _ProbeListener(max_messages)
    messaging_services = []
    receivers = []

    try:
        for var_name, label in _QUEUE_VARS.items():
            q_val = os.environ.get(var_name, '').strip()
            if not q_val:
                continue

            vpn_name = 'FDPS' if label == 'SFDPS' else label # SFDPS -> FDPS, STDDS, TFMS mapped to VPNs
            local_port = 55004 if label == 'TFMS' else 55003
            
            result.queues_attempted.append(label)
            
            broker_props = {
                "solace.messaging.transport.host": f"tcp://127.0.0.1:{local_port}",
                "solace.messaging.service.vpn-name": vpn_name,
                "solace.messaging.authentication.scheme.basic.username": faa_user,
                "solace.messaging.authentication.scheme.basic.password": faa_pass,
            }
            
            retry_strategy = RetryStrategy.parametrized_retry(3, 3)
            messaging_service = MessagingService.builder().from_properties(broker_props)\
                .with_reconnection_retry_strategy(retry_strategy).build()
            
            messaging_service.connect()
            messaging_services.append(messaging_service)
            
            queue = Queue.durable_exclusive_queue(q_val)
            receiver = messaging_service.create_persistent_message_receiver_builder().build(queue)
            receiver.start()
            receiver.receive_async(_IngestMessageHandler(label, listener, result, max_messages))
            receivers.append(receiver)
            log.info(f"Connected and subscribed to {label} via {vpn_name} on port {local_port}")
            
        result.connected = True
        
        listener.done.wait(timeout=probe_seconds)

    except Exception as e:
        err_msg = str(e)[:150]
        result.error_summary = f"Solace connection error: {type(e).__name__}: {err_msg}"
        result.errors.append(result.error_summary)
        log.error(f"Probe failed: {result.error_summary}")
    finally:
        for r in receivers:
            try: r.terminate()
            except: pass
        for s in messaging_services:
            try: s.disconnect()
            except: pass
        
        stunnel_proc.terminate()
        stunnel_proc.wait(timeout=5)

    result.messages_metadata = listener.messages
    result.errors.extend(listener.errors)

    for meta in listener.messages:
        lbl = meta['queue_label']
        result.counts_by_label[lbl] = result.counts_by_label.get(lbl, 0) + 1

    return result

def start_continuous_ingestion(stop_event: threading.Event) -> tuple:
    """
    Start continuous ingestion in background threads.
    Returns (ProbeResult, list of objects to terminate)
    """
    result = ProbeResult()
    result.connected = False

    faa_user = os.environ.get('FAA_USER', '').strip()
    faa_pass = os.environ.get('FAA_PASS', '').strip()

    host, port, use_ssl, broker_source = _resolve_broker()

    stunnel_conf = _generate_stunnel_conf(host, 'ems2.swim.faa.gov', port)
    stunnel_proc = subprocess.Popen(['stunnel4', stunnel_conf], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2)

    listener = _ProbeListener()
    messaging_services = []
    receivers = []

    try:
        for var_name, label in _QUEUE_VARS.items():
            q_val = os.environ.get(var_name, '').strip()
            if not q_val:
                continue

            vpn_name = 'FDPS' if label == 'SFDPS' else label
            local_port = 55004 if label == 'TFMS' else 55003
            
            result.queues_attempted.append(label)
            
            broker_props = {
                "solace.messaging.transport.host": f"tcp://127.0.0.1:{local_port}",
                "solace.messaging.service.vpn-name": vpn_name,
                "solace.messaging.authentication.scheme.basic.username": faa_user,
                "solace.messaging.authentication.scheme.basic.password": faa_pass,
            }
            
            # Continuous mode uses robust retry strategy
            retry_strategy = RetryStrategy.parametrized_retry(-1, 3000)
            messaging_service = MessagingService.builder().from_properties(broker_props)\
                .with_reconnection_retry_strategy(retry_strategy).build()
            
            handler = ServiceEventHandler()
            messaging_service.add_reconnection_listener(handler)
            messaging_service.add_reconnection_attempt_listener(handler)
            messaging_service.add_service_interruption_listener(handler)

            messaging_service.connect()
            messaging_services.append(messaging_service)
            
            queue = Queue.durable_exclusive_queue(q_val)
            receiver = messaging_service.create_persistent_message_receiver_builder().build(queue)
            receiver.start()
            receiver.receive_async(_IngestMessageHandler(label, listener, result))
            receivers.append(receiver)
            log.info(f"Continuous: subscribed to {label} via {vpn_name}")
            
        result.connected = True
        return result, (stunnel_proc, messaging_services, receivers, listener)

    except Exception as e:
        stunnel_proc.terminate()
        raise e

