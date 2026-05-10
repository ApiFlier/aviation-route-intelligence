import unittest
from normalizer import parse_swim_message

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

if __name__ == '__main__':
    unittest.main()
