"""
Resolves ICAO/operator carrier codes to IATA/DOT canonical codes.

SWIM/SCDS messages use ICAO 3-letter operator codes (e.g. JIA, PDT).
BTS historical route data uses IATA/DOT 2-letter codes (e.g. OH, PT).
This module bridges the two systems for match-type classification in
route recent activity.

The canonical source of truth is the carrier_aliases DB table (swim_schema.sql).
The _FALLBACK dict mirrors the seed data and is used when the table is unavailable.
Keep both in sync with _CARRIER_MAP in swim-ingestor/normalizer.py.
"""

import logging

log = logging.getLogger('api.services.carrier_alias')

# Mirrors carrier_aliases seed in swim_schema.sql.
# (canonical_code, carrier_name) keyed by observed ICAO code.
_FALLBACK = {
    'JIA': ('OH', 'PSA Airlines Inc.'),
    'PDT': ('PT', 'Piedmont Airlines'),
    'ENY': ('MQ', 'Envoy Air'),
    'RPA': ('YX', 'Republic Airline'),
    'EDV': ('9E', 'Endeavor Air Inc.'),
    'SKW': ('OO', 'SkyWest Airlines Inc.'),
}


def get_alias_map(db):
    """
    Load the full alias map from the carrier_aliases table.
    Returns a dict: {observed_code: (canonical_code, carrier_name)}.
    Falls back to the hardcoded _FALLBACK dict if the table is unavailable.
    """
    try:
        rows = db.execute(
            "SELECT observed_code, canonical_code, carrier_name FROM carrier_aliases"
        )
        return {r['observed_code']: (r['canonical_code'], r['carrier_name']) for r in rows}
    except Exception:
        log.debug("carrier_aliases table unavailable, using fallback alias map")
        return dict(_FALLBACK)
