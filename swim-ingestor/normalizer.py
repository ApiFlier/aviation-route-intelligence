"""
FlightConn SWIM Ingestor — Normalizer
Parses FAA SWIM XML messages and extracts flight identity, route, and status.
Returns normalized data for `observed_flights` if minimum fields are present.
"""

from lxml import etree
import logging
from datetime import datetime, timezone

log = logging.getLogger('swim-ingestor.normalizer')

def _first_text(tree, xpath_expr):
    """Safely extract the first text match from an XPath query."""
    matches = tree.xpath(xpath_expr)
    if matches and hasattr(matches[0], 'text') and matches[0].text:
        return matches[0].text.strip()
    # Sometimes attributes are returned directly as strings
    if matches and isinstance(matches[0], str):
        return matches[0].strip()
    return None

def parse_swim_message(queue_label: str, payload_bytes: bytes) -> dict:
    """
    Parse a SWIM message payload.
    Returns:
        dict: {
            'success': bool,
            'skip_reason': str (if skipped),
            'flight_data': dict (if success),
            'message_type': str (detected type)
        }
    """
    result = {
        'success': False,
        'skip_reason': None,
        'flight_data': {},
        'message_type': 'unknown'
    }

    if not payload_bytes:
        result['skip_reason'] = 'Empty payload'
        return result

    payload_str = payload_bytes.decode('utf-8', errors='ignore').strip()
    if not payload_str.startswith('<'):
        result['skip_reason'] = 'Not XML payload'
        return result

    try:
        root = etree.fromstring(payload_str.encode('utf-8'))
        
        # Detect basic message type
        local_name = etree.QName(root).localname if hasattr(root, 'tag') else 'unknown'
        result['message_type'] = local_name

        # Extract GUFI (Global Unique Flight Identifier)
        gufi = _first_text(root, "//*[local-name()='gufi']")
        if not gufi:
            gufi = root.get('gufi') # Sometimes it's an attribute on the root or other element
            if not gufi:
                gufi = _first_text(root, "//@gufi")
        
        # Extract Callsign (ACID)
        acid = _first_text(root, "//*[local-name()='acid']")
        if not acid:
            acid = _first_text(root, "//*[local-name()='flightIdentification']/@aircraftIdentification")
            if not acid:
                acid = root.get('acid')

        # Extract Origin and Destination
        origin = _first_text(root, "//*[local-name()='departurePoint']//*[local-name()='locationIndicator']")
        dest = _first_text(root, "//*[local-name()='arrivalPoint']//*[local-name()='locationIndicator']")

        # Some feeds use arrArpt / depArpt
        if not origin:
            origin = _first_text(root, "//*[local-name()='depArpt']")
        if not dest:
            dest = _first_text(root, "//*[local-name()='arrArpt']")

        # Convert 4-letter ICAO to 3-letter IATA if it starts with 'K' (e.g., KSFO -> SFO)
        if origin and len(origin) == 4 and origin.startswith('K'):
            origin = origin[1:]
        if dest and len(dest) == 4 and dest.startswith('K'):
            dest = dest[1:]

        # Aircraft type
        aircraft_type = _first_text(root, "//*[local-name()='aircraftType']//*[local-name()='type']")
        
        # Status
        status = 'active'
        flight_status = _first_text(root, "//*[local-name()='flightStatus']")
        if flight_status:
            status = flight_status.lower()

        # Validate minimum required fields
        if not gufi:
            result['skip_reason'] = 'Missing GUFI (source_flight_id)'
            return result
            
        if not acid:
            result['skip_reason'] = 'Missing ACID (callsign)'
            return result

        if not origin or not dest:
            result['skip_reason'] = 'Missing origin or destination'
            return result

        # Determine Carrier Code from Callsign (usually first 3 letters if it's an ICAO callsign)
        carrier_code = ''.join([c for c in acid if c.isalpha()])[:3] if acid else None
        
        # In a real system, we'd map this 3-letter ICAO carrier code to a 2-letter IATA code 
        # (e.g. AAL -> AA). For the probe, we just store it as the carrier_code field.

        result['flight_data'] = {
            'source_flight_id': gufi,
            'callsign': acid,
            'carrier_code': carrier_code,
            'origin_iata': origin[:3],
            'dest_iata': dest[:3],
            'flight_status': status,
            'aircraft_type': aircraft_type[:10] if aircraft_type else None,
            'data_source': f'FAA_SWIM_{queue_label}'
        }
        
        result['success'] = True
        return result

    except etree.XMLSyntaxError as e:
        result['skip_reason'] = f'XML Syntax Error: {e}'
        return result
    except Exception as e:
        result['skip_reason'] = f'Parse Error: {type(e).__name__}'
        return result
