import sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import wifi_adapters as w
class WifiAdapterTests(unittest.TestCase):
 def test_protect_primary_and_require_confirmation(self):
  with patch.object(w,'validate',side_effect=lambda x:x),patch.object(w,'adapters',return_value={'adapters':[{'device':'wlan0','mode':'managed','primary':True,'monitor_supported':True}]}):
   with self.assertRaises(ValueError):w.set_mode({'device':'wlan0','mode':'monitor','confirmed':True})
  with patch.object(w,'validate',side_effect=lambda x:x),patch.object(w,'adapters',return_value={'adapters':[{'device':'wlan1','mode':'managed','primary':False,'monitor_supported':True}]}):
   with self.assertRaises(ValueError):w.set_mode({'device':'wlan1','mode':'monitor'})
 def test_failure_restores_managed_mode(self):
  calls=[]
  def run(args,timeout=15,check=True):
   calls.append(args)
   if args[:2]==['iw','dev'] and args[-1]=='monitor':raise ValueError('test')
   return ''
  with patch.object(w,'validate',side_effect=lambda x:x),patch.object(w,'adapters',return_value={'adapters':[{'device':'wlan1','mode':'managed','primary':False,'monitor_supported':True}]}),patch.object(w,'run',side_effect=run):
   with self.assertRaises(ValueError):w.set_mode({'device':'wlan1','mode':'monitor','confirmed':True})
  self.assertIn(['nmcli','device','set','wlan1','managed','yes'],calls)
  self.assertIn(['iw','dev','wlan1','set','type','managed'],calls)
if __name__=='__main__':unittest.main()
