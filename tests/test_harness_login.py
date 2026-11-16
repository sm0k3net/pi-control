import sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import harness_login as h
class HarnessLoginTests(unittest.TestCase):
 def test_latest_token_in_selected_log(self):
  token='A'*43
  self.assertEqual(h.token_for_invocation('startup\ndsh web: http://some-host:3080/?token='+token),token)
  with self.assertRaises(ValueError):h.token_for_invocation('nothing here')
 def test_url_and_restart_guard(self):
  ident='a'*32;token='B'*43
  def run(args,timeout=8):
   if args[0]=='systemctl':return 'ActiveState=active\nMainPID=10\nInvocationID='+ident
   if args[0]=='journalctl':return 'dsh web: http://some-host:3080/?token='+token
   if args[0]=='tailscale':return '100.100.100.100'
  with patch.object(h,'run',side_effect=run):self.assertEqual(h.login_url()['url'],'http://100.100.100.100:3080/?token='+token)
  with patch.object(h,'current_invocation',side_effect=[ident,'b'*32]),patch.object(h,'run',return_value='http://host/?token='+token):
   with self.assertRaises(ValueError):h.login_url()
if __name__=='__main__':unittest.main()
