import hashlib
import http.client
import importlib.util
import io
import json
import os
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'src'))
with patch.dict(sys.modules, {'extras': SimpleNamespace()}):
    import node_features


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class NodeTest(unittest.TestCase):
    def test_transfer_exceeds_old_limit_and_is_downloadable_in_ranges(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            threads = []
            servers = []
            nodes = []

            def setup(name):
                path = root / name
                path.mkdir()
                salt = os.urandom(16)
                digest = hashlib.scrypt(b'test', salt=salt, n=16384, r=8, p=1)
                (path/'config.json').write_text(json.dumps({'salt':salt.hex(),'password_hash':digest.hex()}))
                with patch.dict(os.environ,{'PI_NODE_ROOT':str(path)}):
                    agent = load_module(REPO/'src/pi_node_agent.py',name)
                cert, key = path/'cert.pem', path/'key.pem'
                subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes',
                                '-days','1','-subj','/CN=Test','-out',str(cert),'-keyout',str(key)],
                               capture_output=True,check=True)
                server = ThreadingHTTPServer(('127.0.0.1',0),agent.Handler)
                tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                tls.load_cert_chain(cert,key)
                server.socket=tls.wrap_socket(server.socket,server_side=True)
                thread=threading.Thread(target=server.serve_forever,daemon=True)
                thread.start()
                threads.append(thread)
                servers.append(server)
                fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest().upper()
                nodes.append({'fingerprint':fingerprint,'host':'127.0.0.1','port':server.server_port})
                return agent

            try:
                left, right = setup('node_left'), setup('node_right')
                payload = b'head' + b't'*(12*1024**2) + b'tail'
                with patch.object(node_features.cluster5,'addr_ok',return_value=True):
                    with node_features.peer_session(nodes[0], 'test') as (conn, auth):
                        conn.request('POST','/api/share-upload?path=large.bin',body=payload,headers=auth)
                        self.assertEqual(conn.getresponse().status,201)
                    node_features.TRANSFERS['test'] = {'phase':'running'}
                    node_features.copy_file('test', *nodes, ('test','test'), 'large.bin', 'copy.bin')
                    self.assertEqual(node_features.TRANSFERS.pop('test')['phase'],'complete')
                    self.assertEqual(hashlib.sha256((right.SHARE/'copy.bin').read_bytes()).digest(),
                                     hashlib.sha256(payload).digest())
                    with node_features.peer_session(nodes[1], 'test') as (conn, auth):
                        conn.request('GET','/api/share-download?path=copy.bin',headers={**auth,'Range':'bytes=0-3'})
                        reply=conn.getresponse()
                        self.assertEqual(reply.status,206)
                        self.assertEqual(reply.read(),b'head')
                with self.assertRaises(ValueError):
                    right.upload_file(SimpleNamespace(rfile=io.BytesIO(b'partial')), 'interrupted', 1024)
                self.assertFalse((right.SHARE/'interrupted').exists())
                self.assertEqual(list(right.SHARE.glob('.upload-*')),[])
                (right.SHARE/'link').symlink_to(right.ROOT/'config.json')
                with self.assertRaises(OSError):
                    right.download_file(None, 'link')
                with self.assertRaises(ValueError):
                    with right.share_parent('../config.json'):
                        pass
                credential = 'd'*64
                (right.ROOT/'mcp-diagnostics.sha256').write_text(hashlib.sha256(credential.encode()).hexdigest())
                nodes[1].update(id='office', name='Test node')
                with patch.object(node_features.cluster5,'addr_ok',return_value=True), \
                     patch.object(node_features.cluster5,'get',return_value=[nodes[1]]), \
                     patch.object(right,'diagnostics',return_value={'system':{'ok':True}}):
                    snapshot = node_features.mcp_snapshot('office','host',credential)
                    self.assertEqual(snapshot['result']['system'], {'ok':True})
                    with self.assertRaises(ValueError):
                        node_features.mcp_snapshot('office','host','a'*64)
                    with self.assertRaises(ValueError):
                        node_features.mcp_snapshot('office','usb',credential)
                with patch.object(left,'attached_networks',return_value={'192.168.2.0/24':'eth0'}):
                    for invalid in ('192.168.2.0/23','192.168.3.0/26','100.64.0.0/26'):
                        with self.assertRaises(ValueError):
                            left.port_inventory(invalid)
            finally:
                for server in servers:
                    server.shutdown()
                    server.server_close()
                for thread in threads:
                    thread.join(timeout=2)

    def test_approved_lans_match_enrolled_nodes_and_flag_overlap(self):
        status = {'Peer':{'peer':{'TailscaleIPs':['100.100.0.1'],'Online':True,
                                 'PrimaryRoutes':['192.168.40.0/24','192.168.20.0/24','0.0.0.0/0']},
                          'other':{'TailscaleIPs':['100.100.0.2'],'PrimaryRoutes':['10.0.0.0/24']}}}
        routes = [{'dev':'tailscale0','table':52,'dst':'192.168.40.0/24'},
                  {'dev':'eth0','scope':'link','dst':'192.168.20.0/24'}]
        with patch.object(node_features.cluster5,'get',return_value=[{'host':'100.100.0.1','name':'office'}]), \
             patch.object(node_features,'local_cmd',side_effect=[status,{'RouteAll':True},routes]):
            result = node_features.remote_lans()
        self.assertEqual(result['routes'],[
            {'node':'office','cidr':'192.168.40.0/24','online':True,'installed':True,'overlap':False},
            {'node':'office','cidr':'192.168.20.0/24','online':True,'installed':False,'overlap':True},
        ])
        with patch.object(node_features,'remote_lans',return_value=result), \
             patch.object(node_features.subprocess,'run') as run:
            with self.assertRaises(ValueError):
                node_features.accept_remote_lans({'enabled':True})
            run.assert_not_called()

    def test_service_secret_binding_checks_password_before_persisting(self):
        node = {'id':'test-node','entry':'old','host':'100.100.0.1',
                'port':8484,'fingerprint':'A'*64}
        persist = Mock()
        save = Mock(return_value={'id':'entry','message':'saved'})
        store = SimpleNamespace(LOCK=threading.RLock(),save=save,
                                listing=lambda:{'entries':[{'id':'entry','scopes':['ai']}]})
        v = SimpleNamespace(dispatch=lambda op,d:{'password':'fixture','title':'test',
                                                    'username':'user'})
        data = {'entry':'entry','master':'fixture-master','scopes':['nodes'],'node':'test-node'}
        with patch.dict(sys.modules,{'service_secrets':store,'vault':v}), \
             patch.object(node_features.cluster5,'get',return_value=[node]), \
             patch.object(node_features.cluster5.extras,'atomic',persist,create=True):
            with patch.object(node_features,'get_peer',side_effect=ValueError('invalid')):
                with self.assertRaises(ValueError):
                    node_features.enroll_service_secret(data)
            save.assert_not_called()
            persist.assert_not_called()
            with patch.object(node_features,'get_peer',return_value={'system':{}}) as peer:
                result = node_features.enroll_service_secret(data)
            self.assertEqual(result['id'],'entry')
            peer.assert_called_once()
            self.assertEqual(node['entry'],'entry')
            self.assertEqual(save.call_args.args[0]['scopes'],['ai','nodes'])
            persist.assert_called_once()

    def test_mcp_scope_hides_other_nodes_and_rejects_unlisted_tools(self):
        mcp=load_module(REPO/'scripts/pi-node-mcp.py','node_mcp')
        backend=SimpleNamespace(status=lambda:{'nodes':[{'id':'allowed'},{'id':'blocked'}]},
                                action=lambda data:data)
        service=mcp.Gateway(backend,['allowed'])
        self.assertEqual(service.call('node_list',{}),{'nodes':[{'id':'allowed'}]})
        with self.assertRaises(ValueError):
            service.call('node_health',{'node_id':'blocked'})
        with self.assertRaises(ValueError):
            service.call('node_health',{'node_id':'allowed','password':'test'})
        with self.assertRaises(ValueError):
            service.call('scan',{'node_id':'allowed'})
        self.assertEqual(service.call('node_inventory',{'node_id':'allowed'})['task'],'observed')


if __name__ == '__main__':
    unittest.main()
