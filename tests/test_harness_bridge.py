import importlib.util
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
spec=importlib.util.spec_from_file_location('harness_bridge_test',Path(__file__).resolve().parents[1]/'src/harness_bridge.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class BridgeTests(unittest.TestCase):
 def test_serves_static_document_and_javascript(self):
  server=ThreadingHTTPServer(('127.0.0.1',0),m.Bridge);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
  try:
   base='http://127.0.0.1:'+str(server.server_port)
   with urllib.request.urlopen(base+'/',timeout=5) as r:
    self.assertEqual(r.status,200);self.assertIn('/bridge.js',r.read().decode());self.assertEqual(r.headers['Referrer-Policy'],'no-referrer')
   with urllib.request.urlopen(base+'/bridge.js',timeout=5) as r:
    self.assertIn(':3080/?token=',r.read().decode())
  finally:server.shutdown();server.server_close();thread.join()
if __name__=='__main__':unittest.main()
