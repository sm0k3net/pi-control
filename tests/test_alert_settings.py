import sys
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import alert_settings as a

class AlertTests(unittest.TestCase):
    def test_legacy_defaults_and_topic_validation(self):
        self.assertEqual(a.validate_settings({})['temperature'], 80)
        self.assertIsNone(a.topic_id(''))
        self.assertEqual(a.topic_id('345'), 345)
        for invalid in ('-1', 'abc', '0', '999999999999999999'):
            with self.assertRaises(ValueError): a.topic_id(invalid)
    def test_thresholds_and_disabled_cpu(self):
        s={'temperature':81,'disk_total':100,'disk_used':90,'memory_total':100,'memory_available':9,'cpu':96,'undervoltage_now':True}
        v=a.evaluate(s,[{'id':'agent','name':'Agent','state':'exited'}],a.validate_settings({}),lambda _:True)
        self.assertEqual(set(v),{'temp','disk','memory','power','service_agent'})
        c=a.validate_settings({'cpu_enabled':True,'cpu_percent':95,'power_enabled':False})
        self.assertIn('cpu',a.evaluate(s,[],c,lambda _:False))
        self.assertNotIn('power',a.evaluate(s,[],c,lambda _:False))
    def test_bad_thresholds_rejected(self):
        for config in ({'repeat_minutes':0}, {'temperature':121}, {'cpu_enabled':'true'}):
            with self.assertRaises(ValueError): a.validate_settings(config)

if __name__=='__main__': unittest.main()
