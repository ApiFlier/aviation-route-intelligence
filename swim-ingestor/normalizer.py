"""
FlightConn SWIM Ingestor — Normalizer
Parses FAA SWIM XML messages and extracts flight identity, route, and status.
Returns normalized data for `observed_flights` if minimum fields are present.

SWIM/SCDS data is NOT for operational use. It provides advisory recent-activity
context only.

Extraction Matrix:
| Field            | TFMS                                     | SFDPS                                     | STDDS (Enrichment Only)           | Target Column      |
|------------------|------------------------------------------|-------------------------------------------|-----------------------------------|--------------------|
| source_flight_id | gufi                                     | gufi                                      | enhancedData/eramGufi             | source_flight_id   |
| callsign         | aircraftId, acid                         | flightIdentification/aircraftIdent...     | flightId/aircraftId, acid, etc.   | callsign           |
| carrier_code     | airline, derived from callsign           | operator/organization, derived            | derived from callsign             | carrier_code       |
| origin_iata      | depArpt, departurePoint                  | departurePoint                              | departureAirport                  | origin_iata        |
| dest_iata        | arrArpt, arrivalPoint                    | arrivalPoint                              | destinationAirport                | dest_iata          |
| sched_dep_utc    | originalDeparture, igtd                  |                                           |                                   | sched_dep_utc      |
| actual_dep_utc   | timeOfDeparture(est=false), runwayDep... | departure/runwayTime/actual               |                                   | actual_dep_utc     |
| sched_arr_utc    | originalArrival                          | arrival/runwayTime/estimated              |                                   | sched_arr_utc      |
| actual_arr_utc   | airlineOnTime, airlineInTime             | arrival/runwayTime/actual                 |                                   | actual_arr_utc     |
| aircraft_type    | aircraftModel, aircraftSpecification     | aircraftDescription/icaoModelIdent...     | aircraftType, acType              | aircraft_type      |
| flight_status    | flightStatus                             | fdpsFlightStatus                          | status                            | flight_status      |

Out of scope for FlightConn route-intelligence:
- Live position data (lat/lon, altitude, speed)
- STDDS surface/track records without route identity
- Route geometry (fixes, sectors, airways)
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
    if matches and isinstance(matches[0], str):
        return matches[0].strip()
    return None

# Mapping of ICAO (3-letter) to IATA (2-letter) carrier codes for major US airlines
_CARRIER_MAP = {
    'AAL': 'AA', 'DAL': 'DL', 'UAL': 'UA', 'SWA': 'WN', 'FFT': 'F9',
    'JBU': 'B6', 'ASA': 'AS', 'NKS': 'NK', 'HAL': 'HA', 'SKW': 'OO',
    'ENY': 'MQ', 'RPA': 'YX', 'EDV': '9E', 'PDT': 'PT', 'GJS': 'G7',
    'UCA': 'ZK', 'MXY': 'MX', 'QXE': 'QX', 'SCX': 'SY', 'G7':  'G7',
    'YX':  'YX', 'MQ':  'MQ', '9E':  '9E', 'F9':  'F9', 'B6':  'B6',
    'AS':  'AS', 'NK':  'NK', 'HA':  'HA', 'OO':  'OO', 'PT':  'PT',
    'ZK':  'ZK', 'MX':  'MX', 'QX':  'QX', 'SY':  'SY',
}

def _map_carrier(icao_code: str) -> str:
    """Map 3-letter ICAO to 2-letter IATA if known, otherwise return original."""
    if not icao_code:
        return None
    return _CARRIER_MAP.get(icao_code, icao_code)

def _map_status(raw_status: str) -> str:
    """Map arbitrary status string to allowed ENUM values."""
    if not raw_status:
        return 'active'
    s = raw_status.lower()
    if 'sched' in s: return 'scheduled'
    if 'active' in s or 'enroute' in s or 'landed' in s: return 'active'
    if 'comp' in s or 'arr' in s: return 'completed'
    if 'can' in s: return 'cancelled'
    if 'div' in s: return 'diverted'
    return 'unknown'

def _clean_time(raw_str: str) -> str:
    if not raw_str:
        return None
    # Strip T and Z to format for MySQL DATETIME
    return raw_str.replace('T', ' ').replace('Z', '')[:19]

def _extract_flight_data(queue_label: str, root: etree._Element) -> dict:
    """Attempt to extract normalized flight data from a single message element."""
    local_name = etree.QName(root).localname if hasattr(root, 'tag') else 'unknown'
    
    # GUFI lookup
    gufi = root.get('gufi')
    if not gufi: gufi = _first_text(root, ".//*[local-name()='gufi']")
    if not gufi: gufi = _first_text(root, ".//*[local-name()='eramGufi']")
    if not gufi: gufi = _first_text(root, ".//*[local-name()='sfdpsGufi']")
    if not gufi: gufi = _first_text(root, ".//@gufi")
    
    # ACID lookup
    acid = root.get('acid')
    if not acid: acid = _first_text(root, ".//*[local-name()='acid']")
    if not acid: acid = _first_text(root, ".//*[local-name()='aircraftIdentification']")
    if not acid: acid = _first_text(root, ".//*[local-name()='callSign']")
    if not acid: acid = _first_text(root, ".//*[local-name()='aircraftId']")
    if not acid: acid = _first_text(root, ".//*[local-name()='flightId']")

    # Carrier code lookup
    airline = _first_text(root, ".//*[local-name()='airline']")
    if not airline: airline = _first_text(root, ".//*[local-name()='operator']//*[local-name()='organization']//*[local-name()='name']")

    # Route extraction logic
    origin = _first_text(root, ".//*[local-name()='departurePoint']//*[local-name()='locationIndicator']")
    dest = _first_text(root, ".//*[local-name()='arrivalPoint']//*[local-name()='locationIndicator']")
    
    if not origin: origin = _first_text(root, ".//*[local-name()='departurePoint']//*[local-name()='airport']")
    if not dest: dest = _first_text(root, ".//*[local-name()='arrivalPoint']//*[local-name()='airport']")

    if not origin: origin = _first_text(root, ".//*[local-name()='departurePoint']")
    if not dest: dest = _first_text(root, ".//*[local-name()='arrivalPoint']")

    # STDDS style
    if not origin: origin = _first_text(root, ".//*[local-name()='departureAerodrome']")
    if not origin: origin = _first_text(root, ".//*[local-name()='depArpt']")
    if not origin: origin = _first_text(root, ".//*[local-name()='departureAirport']")
        
    if not dest: dest = _first_text(root, ".//*[local-name()='arrivalAerodrome']")
    if not dest: dest = _first_text(root, ".//*[local-name()='arrArpt']")
    if not dest: dest = _first_text(root, ".//*[local-name()='destinationAirport']")

    # Airport normalization
    # U.S. ICAO airports like KATL, KSFO, KJFK should normalize to ATL, SFO, JFK.
    if origin and len(origin) == 4 and origin.startswith('K'):
        origin = origin[1:]
    if dest and len(dest) == 4 and dest.startswith('K'):
        dest = dest[1:]

    # Aircraft Type
    aircraft_type = _first_text(root, ".//*[local-name()='aircraftType']//*[local-name()='type']")
    if not aircraft_type: aircraft_type = _first_text(root, ".//*[local-name()='aircraftModel']")
    if not aircraft_type: aircraft_type = _first_text(root, ".//*[local-name()='aircraftSpecification']")
    if not aircraft_type: aircraft_type = _first_text(root, ".//*[local-name()='icaoModelIdentifier']")
    if not aircraft_type: aircraft_type = _first_text(root, ".//*[local-name()='acType']")
    
    # Times
    sched_dep_utc = _first_text(root, ".//*[local-name()='originalDeparture']")
    if not sched_dep_utc: sched_dep_utc = _first_text(root, ".//*[local-name()='igtd']")
    
    actual_dep_utc = _first_text(root, ".//*[local-name()='timeOfDeparture'][@estimated='false']")
    if not actual_dep_utc: actual_dep_utc = _first_text(root, ".//*[local-name()='etd'][@etdType='ACTUAL']/@timeValue")
    if not actual_dep_utc: actual_dep_utc = _first_text(root, ".//*[local-name()='runwayDeparture']")
    if not actual_dep_utc: actual_dep_utc = _first_text(root, ".//*[local-name()='airlineOffTime']")
    if not actual_dep_utc: actual_dep_utc = _first_text(root, ".//*[local-name()='departure']//*[local-name()='runwayTime']/*[local-name()='actual']/*[local-name()='time']")
    
    sched_arr_utc = _first_text(root, ".//*[local-name()='originalArrival']")
    if not sched_arr_utc: sched_arr_utc = _first_text(root, ".//*[local-name()='arrival']//*[local-name()='runwayTime']/*[local-name()='estimated']/*[local-name()='time']")

    actual_arr_utc = _first_text(root, ".//*[local-name()='airlineOnTime']")
    if not actual_arr_utc: actual_arr_utc = _first_text(root, ".//*[local-name()='airlineInTime']")
    if not actual_arr_utc: actual_arr_utc = _first_text(root, ".//*[local-name()='arrival']//*[local-name()='runwayTime']/*[local-name()='actual']/*[local-name()='time']")

    raw_status = _first_text(root, ".//*[local-name()='flightStatus']")
    if not raw_status: raw_status = _first_text(root, ".//*[local-name()='fdpsFlightStatus']")
    if not raw_status: raw_status = _first_text(root, ".//*[local-name()='status']")
    status = _map_status(raw_status)

    debug_tags = [etree.QName(c).localname for c in root.iter() if isinstance(c, etree._Element)][:20]

    if not gufi:
        return {'success': False, 'skip_reason': f'Missing GUFI. Tags: {debug_tags}', 'message_type': local_name}
    if not acid or acid in ('UNKN', 'UNKNOWN', 'UNK'):
        return {'success': False, 'skip_reason': f'Missing or UNKN ACID. Tags: {debug_tags}', 'message_type': local_name}

    if origin and len(origin) != 3: origin = None
    if dest and len(dest) != 3: dest = None

    carrier_icao = airline if airline else (''.join([c for c in acid if c.isalpha()])[:3] if acid else None)
    carrier_code = _map_carrier(carrier_icao)
    if carrier_code in ('XXX', 'UNK', 'UNKNOWN', ''):
        carrier_code = None

    return {
        'success': True,
        'message_type': local_name,
        'flight_data': {
            'source_flight_id': gufi,
            'callsign': acid,
            'carrier_code': carrier_code,
            'origin_iata': origin[:3] if origin else None,
            'dest_iata': dest[:3] if dest else None,
            'flight_status': status,
            'aircraft_type': aircraft_type[:10] if aircraft_type else None,
            'sched_dep_utc': _clean_time(sched_dep_utc),
            'actual_dep_utc': _clean_time(actual_dep_utc),
            'sched_arr_utc': _clean_time(sched_arr_utc),
            'actual_arr_utc': _clean_time(actual_arr_utc),
            'data_source': 'FAA_SWIM'
        }
    }

def is_route_ready(flight_data: dict) -> bool:
    """
    Check if a normalized flight record is complete enough for route aggregation.
    Criteria:
    - Must have source_flight_id (GUFI)
    - Must have callsign (ACID)
    - Must have 3-letter origin_iata
    - Must have 3-letter dest_iata
    - Must have carrier_code
    - Carrier code must not be UNK or XXX
    """
    if not flight_data.get('source_flight_id') or not flight_data.get('callsign'):
        return False
    cc = flight_data.get('carrier_code')
    if not cc or cc in ('UNK', 'UNKNOWN', 'XXX'):
        return False
    orig = flight_data.get('origin_iata')
    dest = flight_data.get('dest_iata')
    if not orig or not dest or len(orig) != 3 or len(dest) != 3:
        return False
    return True

def _recursive_unpack(queue_label: str, element: etree._Element, records: list, stats: dict):
    """Recursively unpack MessageCollection or process standard elements."""
    local_name = etree.QName(element).localname if hasattr(element, 'tag') else 'unknown'
    
    envelope_tags = (
        'MessageCollection', 'messageCollection', 'message', 
        'fiOutput', 'fiMessage', 'tmiFlightDataList', 'flightData',
        'tfmDataService'
    )
    
    if local_name in envelope_tags:
        if local_name in ('MessageCollection', 'messageCollection'):
            stats['message_collections'] += 1
            
        for child in element:
            if isinstance(child, etree._Element):
                _recursive_unpack(queue_label, child, records, stats)
    else:
        stats['candidates'] += 1
        res = _extract_flight_data(queue_label, element)
        records.append(res)


def get_safe_diagnostics(root: etree._Element) -> dict:
    """Return a safe summary of the XML structure without exposing payload values."""
    local_name = etree.QName(root).localname if hasattr(root, 'tag') else 'unknown'
    children = [etree.QName(c).localname for c in root if isinstance(c, etree._Element)]
    
    # Identify which of our target identifier tags are present (names only, no values)
    known_id_tags = ['gufi', 'eramGufi', 'sfdpsGufi', 'acid', 'callSign', 'aircraftId', 'flightId', 'flightIdentification']
    found_id_tags = []
    for tag in known_id_tags:
        if root.xpath(f".//*[local-name()='{tag}']"):
            found_id_tags.append(tag)
        if root.get(tag) or root.xpath(f".//@{tag}"):
            found_id_tags.append(f"@{tag}")

    return {
        'root_tag': local_name,
        'child_tags': list(set(children))[:10],
        'found_id_tags': list(set(found_id_tags)),
        'namespaces': list(root.nsmap.keys()) if hasattr(root, 'nsmap') else []
    }

def parse_swim_message(queue_label: str, payload_bytes: bytes) -> dict:
    """
    Parse a SWIM message payload, unpacking collections recursively.
    Returns:
        dict: {
            'success': bool,
            'records': list of dicts from _extract_flight_data,
            'skip_reason': str (if global skip),
            'message_type': str (root type),
            'stats': dict,
            'diagnostics': dict
        }
    """
    result = {
        'success': False,
        'records': [],
        'skip_reason': None,
        'message_type': 'unknown',
        'stats': {'message_collections': 0, 'candidates': 0},
        'diagnostics': {}
    }

    if not payload_bytes:
        result['skip_reason'] = 'Empty payload'
        return result

    payload_str = payload_bytes.decode('utf-8', errors='ignore').strip()
    if not payload_str.startswith('<'):
        result['skip_reason'] = 'Not XML payload'
        return result

    try:
        # Use a parser that drops blanks and comments for safety
        parser = etree.XMLParser(remove_blank_text=True, remove_comments=True)
        root = etree.fromstring(payload_str.encode('utf-8'), parser)
        
        result['message_type'] = etree.QName(root).localname if hasattr(root, 'tag') else 'unknown'
        result['diagnostics'] = get_safe_diagnostics(root)

        _recursive_unpack(queue_label, root, result['records'], result['stats'])
        result['success'] = True
        return result

    except etree.XMLSyntaxError as e:
        result['skip_reason'] = f'XML Syntax Error: {e}'
        return result
    except Exception as e:
        result['skip_reason'] = f'Parse Error: {type(e).__name__}'
        return result
