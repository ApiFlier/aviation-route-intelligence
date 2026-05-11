import unittest
from normalizer import parse_swim_message, is_route_ready

class TestNormalizer(unittest.TestCase):
    def test_message_collection_nested_flight(self):
        xml = b'''
        <MessageCollection>
            <message>
                <flight gufi="G123" acid="DAL123">
                    <departurePoint><locationIndicator>KATL</locationIndicator></departurePoint>
                    <arrivalPoint><locationIndicator>KLAX</locationIndicator></arrivalPoint>
                </flight>
            </message>
        </MessageCollection>
        '''
        res = parse_swim_message('SFDPS', xml)
        self.assertTrue(res['success'])
        self.assertEqual(res['stats']['candidates'], 1)
        self.assertEqual(len(res['records']), 1)
        self.assertTrue(res['records'][0]['success'])
        self.assertEqual(res['records'][0]['flight_data']['origin_iata'], 'ATL')
        self.assertEqual(res['records'][0]['flight_data']['dest_iata'], 'LAX')

    def test_tfm_data_service(self):
        xml = b'''
        <ns:tfmDataService xmlns:ns="http://example.com/tfm">
            <ns:fiOutput>
                <ns:fiMessage>
                    <ns:flight>
                        <ns:gufi>TFM456</ns:gufi>
                        <ns:aircraftId>AAL456</ns:aircraftId>
                        <ns:departurePoint><ns:airport>KDFW</ns:airport></ns:departurePoint>
                        <ns:arrivalPoint><ns:airport>KORD</ns:airport></ns:arrivalPoint>
                    </ns:flight>
                </ns:fiMessage>
            </ns:fiOutput>
        </ns:tfmDataService>
        '''
        res = parse_swim_message('TFMS', xml)
        self.assertTrue(res['success'])
        self.assertEqual(len(res['records']), 1)
        self.assertTrue(res['records'][0]['success'])
        self.assertEqual(res['records'][0]['flight_data']['origin_iata'], 'DFW')
        self.assertEqual(res['records'][0]['flight_data']['dest_iata'], 'ORD')

    def test_partial_status_update(self):
        xml = b'''
        <flight gufi="STAT789" acid="UAL789">
            <flightStatus>completed</flightStatus>
        </flight>
        '''
        res = parse_swim_message('SFDPS', xml)
        self.assertTrue(res['success'])
        self.assertEqual(len(res['records']), 1)
        self.assertTrue(res['records'][0]['success'])
        self.assertEqual(res['records'][0]['flight_data']['flight_status'], 'completed')
        self.assertIsNone(res['records'][0]['flight_data']['origin_iata'])

    def test_unsupported_stdds_metadata(self):
        xml = b'''
        <TAStatus>
            <mode>active</mode>
        </TAStatus>
        '''
        res = parse_swim_message('STDDS', xml)
        self.assertTrue(res['success'])
        self.assertEqual(len(res['records']), 1)
        self.assertFalse(res['records'][0]['success'])
        self.assertIn('Missing GUFI', res['records'][0]['skip_reason'])

    def test_tfms_flight_times(self):
        xml = b'''
        <tfmDataService>
            <flight gufi="TFM123" acid="DAL123">
                <originalDeparture>2026-05-11T12:00:00Z</originalDeparture>
                <timeOfDeparture estimated="false">2026-05-11T12:05:00Z</timeOfDeparture>
                <airlineOnTime>2026-05-11T14:30:00Z</airlineOnTime>
                <originalArrival>2026-05-11T14:35:00Z</originalArrival>
            </flight>
        </tfmDataService>
        '''
        res = parse_swim_message('TFMS', xml)
        self.assertTrue(res['success'])
        fd = res['records'][0]['flight_data']
        self.assertEqual(fd['sched_dep_utc'], '2026-05-11 12:00:00')
        self.assertEqual(fd['actual_dep_utc'], '2026-05-11 12:05:00')
        self.assertEqual(fd['actual_arr_utc'], '2026-05-11 14:30:00')
        self.assertEqual(fd['sched_arr_utc'], '2026-05-11 14:35:00')

    def test_tfms_flight_route_and_aircraft(self):
        xml = b'''
        <tfmDataService>
            <flight gufi="TFM124" acid="UAL124">
                <aircraftModel>B738</aircraftModel>
            </flight>
        </tfmDataService>
        '''
        res = parse_swim_message('TFMS', xml)
        self.assertTrue(res['success'])
        fd = res['records'][0]['flight_data']
        self.assertEqual(fd['aircraft_type'], 'B738')

    def test_sfdps_runway_times_and_aircraft(self):
        xml = b'''
        <MessageCollection>
            <flight gufi="S123">
                <flightIdentification aircraftIdentification="AAL123"/>
                <departure><runwayTime><actual><time>2026-05-11T12:10:00Z</time></actual></runwayTime></departure>
                <arrival><runwayTime><estimated><time>2026-05-11T15:10:00Z</time></estimated></runwayTime></arrival>
                <aircraftDescription><aircraftType><icaoModelIdentifier>A321</icaoModelIdentifier></aircraftType></aircraftDescription>
            </flight>
        </MessageCollection>
        '''
        res = parse_swim_message('SFDPS', xml)
        self.assertTrue(res['success'])
        fd = res['records'][0]['flight_data']
        self.assertEqual(fd['actual_dep_utc'], '2026-05-11 12:10:00')
        self.assertEqual(fd['sched_arr_utc'], '2026-05-11 15:10:00')
        self.assertEqual(fd['aircraft_type'], 'A321')

    def test_stdds_enrichment(self):
        xml = b'''
        <asdexMsg>
            <enhancedData>
                <eramGufi>E123</eramGufi>
                <callsign>SWA123</callsign>
                <departureAirport>KDAL</departureAirport>
                <destinationAirport>KHOU</destinationAirport>
                <aircraftType>B737</aircraftType>
            </enhancedData>
        </asdexMsg>
        '''
        res = parse_swim_message('STDDS', xml)
        self.assertTrue(res['success'])
        fd = res['records'][0]['flight_data']
        self.assertEqual(fd['source_flight_id'], 'E123')
        self.assertEqual(fd['callsign'], 'SWA123')
        self.assertEqual(fd['origin_iata'], 'DAL')
        self.assertEqual(fd['dest_iata'], 'HOU')
        self.assertEqual(fd['aircraft_type'], 'B737')
        self.assertTrue(is_route_ready(fd))

    def test_stdds_track_only_no_identity(self):
        xml = b'''
        <positionReport>
            <track>
                <status>ACTIVE</status>
                <mrtTime>2026-05-11T12:00:00Z</mrtTime>
            </track>
        </positionReport>
        '''
        res = parse_swim_message('STDDS', xml)
        self.assertTrue(res['success'])
        self.assertFalse(res['records'][0]['success'])
        self.assertIn('Missing GUFI', res['records'][0]['skip_reason'])

    def test_non_us_icao_normalization(self):
        xml = b'''
        <flight gufi="INTL1" acid="ACA1">
            <departurePoint><locationIndicator>CYYZ</locationIndicator></departurePoint>
            <arrivalPoint><locationIndicator>SKBO</locationIndicator></arrivalPoint>
        </flight>
        '''
        res = parse_swim_message('SFDPS', xml)
        self.assertTrue(res['success'])
        fd = res['records'][0]['flight_data']
        self.assertEqual(fd['origin_iata'], 'CYYZ')
        self.assertEqual(fd['dest_iata'], 'SKBO')
        self.assertTrue(is_route_ready(fd))

    def test_tfms_enrichment(self):
        xml = b'''
        <tfmDataService>
            <fltdMessage acid="DAL123" airline="DAL" major="DAL" msgType="departureInformation">
                <qualifiedAircraftId aircraftCategory="JET" userCategory="COMMERCIAL">
                    <gufi>TFM125</gufi>
                </qualifiedAircraftId>
                <flightStatusAndSpec>
                    <aircraftModel>B738</aircraftModel>
                </flightStatusAndSpec>
            </fltdMessage>
        </tfmDataService>
        '''
        res = parse_swim_message('TFMS', xml)
        self.assertTrue(res['success'])
        fd = res['records'][0]['flight_data']
        self.assertEqual(fd['carrier_code'], 'DL')
        enrich = fd.get('enrichment', {})
        self.assertEqual(enrich.get('operating_carrier_code'), 'DAL')
        self.assertEqual(enrich.get('major_carrier_code'), 'DAL')
        self.assertEqual(enrich.get('user_category'), 'COMMERCIAL')
        self.assertEqual(enrich.get('aircraft_category'), 'JET')

    def test_sfdps_enrichment(self):
        xml = b'''
        <MessageCollection>
            <flight gufi="S124" flightType="SCHEDULED">
                <flightIdentification aircraftIdentification="AAL124"/>
                <operator><organization><name>AAL</name></organization></operator>
                <aircraftDescription><aircraftType><icaoModelIdentifier>A321</icaoModelIdentifier></aircraftType></aircraftDescription>
            </flight>
        </MessageCollection>
        '''
        res = parse_swim_message('SFDPS', xml)
        self.assertTrue(res['success'])
        fd = res['records'][0]['flight_data']
        self.assertEqual(fd['carrier_code'], 'AA')
        enrich = fd.get('enrichment', {})
        self.assertEqual(enrich.get('operating_carrier_code'), 'AAL')
        self.assertEqual(enrich.get('flight_type'), 'SCHEDULED')

    def test_commercial_candidate_jet_carrier(self):
        from normalizer import is_commercial_route_candidate
        flight_data = {
            'callsign': 'DAL123',
            'carrier_code': 'DL',
            'origin_iata': 'ATL',
            'dest_iata': 'LAX',
            'enrichment': {
                'aircraft_category': 'JET',
                'user_category': None
            }
        }
        self.assertTrue(is_commercial_route_candidate(flight_data))

    def test_commercial_flight_vs_route_candidate(self):
        from normalizer import is_commercial_candidate, is_commercial_route_candidate
        flight_data = {
            'callsign': 'DAL123',
            'carrier_code': 'DL',
            'origin_iata': None,
            'dest_iata': None,
            'enrichment': {
                'user_category': 'COMMERCIAL'
            }
        }
        self.assertTrue(is_commercial_candidate(flight_data))
        self.assertFalse(is_commercial_route_candidate(flight_data))

    def test_unknown_operator_not_commercial(self):
        from normalizer import is_commercial_candidate
        flight_data = {
            'callsign': 'XYZ123',
            'carrier_code': 'XYZ',
            'origin_iata': 'ATL',
            'dest_iata': 'LAX',
            'enrichment': {
                'user_category': None,
                'flight_type': None
            }
        }
        # XYZ is not in KNOWN_CARRIERS and no COMMERCIAL tag
        self.assertFalse(is_commercial_candidate(flight_data))

    def test_ga_jet_not_commercial(self):
        from normalizer import is_commercial_candidate
        flight_data = {
            'callsign': 'XYZ123',
            'carrier_code': 'XYZ',
            'origin_iata': 'ATL',
            'dest_iata': 'LAX',
            'enrichment': {
                'user_category': 'GENERAL AVIATION',
                'aircraft_category': 'JET'
            }
        }
        self.assertFalse(is_commercial_candidate(flight_data))
        
    def test_xxx_unk_excluded(self):
        from normalizer import is_commercial_candidate
        for code in ['XXX', 'UNK', 'UNKNOWN', 'UNKN']:
            flight_data = {
                'callsign': 'FLT123',
                'carrier_code': code,
                'origin_iata': 'ATL',
                'dest_iata': 'LAX',
                'enrichment': {
                    'user_category': 'COMMERCIAL'
                }
            }
            self.assertFalse(is_commercial_candidate(flight_data))

    def test_tail_number_not_commercial(self):
        from normalizer import is_commercial_candidate
        flight_data = {
            'callsign': 'N789AB',
            'carrier_code': 'AA', # Mistakenly mapped or overlapping
            'origin_iata': 'DFW',
            'dest_iata': 'AUS',
            'enrichment': {}
        }
        self.assertFalse(is_commercial_candidate(flight_data))

if __name__ == '__main__':
    unittest.main()
