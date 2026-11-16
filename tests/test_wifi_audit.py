import sys,tempfile,unittest,types
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import wifi_audit as a
class WifiAuditTests(unittest.TestCase):
 def test_parse_and_csv_injection_guard(self):
  with tempfile.TemporaryDirectory() as d:
   f=Path(d)/'audit-01.csv';f.write_text('BSSID, First time seen, Last time seen, channel, Speed, Privacy, Cipher, Authentication, Power, # beacons, # IV, LAN IP, ID-length, ESSID, Key\nAA:BB:CC:DD:EE:02, first,last,6,54,WPA2,CCMP,PSK,-50,10,0,,4,=evil,\n\nStation MAC, First time seen, Last time seen, Power, # packets, BSSID, Probed ESSIDs\n12:34:56:78:90:AB,first,last,-60,4,AA:BB:CC:DD:EE:02,\n')
   ap,stations=a._read_csv(f);self.assertEqual(ap[0]['ssid'],'=evil');self.assertEqual(stations[0]['bssid'],'AA:BB:CC:DD:EE:02')
   fake={'dir':Path(d),'process':types.SimpleNamespace(poll=lambda:0)}
   with patch.object(a,'job',fake):
    import base64
    data=base64.b64decode(a.export({'kind':'csv'})['data']).decode();self.assertIn("'=evil",data)
 def test_scope_and_auth(self):
  for mac in ('FF:FF:FF:FF:FF:FF','01:23:45:67:89:AB','bad'):
   with self.assertRaises(ValueError):a._mac(mac)
  with self.assertRaises(ValueError):a.start({'authorized':False})
  with self.assertRaises(ValueError):a.deauth({'authorized':False})
 def test_only_selected_observed_client(self):
  with tempfile.TemporaryDirectory() as d:
   fake={'dir':Path(d),'process':types.SimpleNamespace(poll=lambda:None),'bssid':'AA:BB:CC:DD:EE:02','device':'wlan1','last_deauth':0}
   with patch.object(a,'job',fake):
    with self.assertRaises(ValueError):a.deauth({'authorized':True,'bssid':'AA:BB:CC:DD:EE:02','station':'12:34:56:78:90:AB','count':1})
if __name__=='__main__':unittest.main()
