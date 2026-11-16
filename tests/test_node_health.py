import hashlib
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch
sys.modules.setdefault('extras', types.ModuleType('extras'))
spec=importlib.util.spec_from_file_location('cluster_test',Path(__file__).resolve().parents[1]/'src/cluster5.py')
mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)

class NodeHealthTests(unittest.TestCase):
 def test_pinned_reachable_and_mismatch(self):
  node={'id':'x','host':'192.168.1.5','port':8484,'fingerprint':hashlib.sha256(b'cert').hexdigest().upper()}
  class Sock:
   def getpeercert(self,binary_form):return b'cert'
  class Conn:
   sock=Sock()
   def __init__(self,*a,**kw):pass
   def connect(self):pass
   def close(self):pass
  with patch.object(mod,'get',return_value=[node]),patch.object(mod.http.client,'HTTPSConnection',Conn):
   self.assertEqual(mod.health({'id':'x'})['state'],'reachable')
   node['fingerprint']='0'*64
   self.assertEqual(mod.health({'id':'x'})['state'],'certificate_mismatch')
 def test_reject_unknown_node(self):
  with patch.object(mod,'get',return_value=[]):
   with self.assertRaises(ValueError):mod.health({'id':'not-saved'})
if __name__=='__main__':unittest.main()
