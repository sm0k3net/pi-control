import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import files6
import remote_desktop as desktop


class DesktopAndKnowledgeTest(unittest.TestCase):
    def test_masked_rfb_frames_relay_with_a_partial_frame(self):
        payload = b'RFB 003.008\n'
        mask = b'ABCD'
        frame = bytes((0x82, 0x80 | len(payload))) + mask + bytes(
            value ^ mask[index % 4] for index, value in enumerate(payload))
        pending = bytearray(frame[:8])
        self.assertEqual(list(desktop.browser_frames(pending)), [])
        pending.extend(frame[8:])
        self.assertEqual(list(desktop.browser_frames(pending)), [(2, payload)])
        self.assertFalse(pending)
        with self.assertRaises(ValueError):
            list(desktop.browser_frames(bytearray(desktop.websocket_frame(payload))))

    def test_mac_transport_rejects_addresses_outside_tailnet(self):
        with patch.object(desktop,'execute') as run:
            with self.assertRaises(ValueError):
                desktop.mac_connection('192.168.1.1')
            run.assert_not_called()

    def test_knowledge_indexes_nested_content_and_skips_symlinks(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work) / 'Knowledge'
            root.mkdir(); (root/'Сеть').mkdir()
            (root/'Сеть/узел.md').write_text('# Pi Control\nПроверить Tailscale маршрут\n')
            unrelated = Path(work)/'outside.md'
            unrelated.write_text('Tailscale закрытый маршрут')
            (root/'link.md').symlink_to(unrelated)
            with patch.dict(files6.ROOTS,{'knowledge':root}), \
                 patch.object(files6,'mcp_knowledge',return_value='Руководство'):
                result = files6.search_knowledge({'query':'tailscale маршрут'})
            self.assertEqual(result['items'],[{'path':'Сеть/узел.md',
                                               'snippet':'Проверить Tailscale маршрут'}])


if __name__ == '__main__':
    unittest.main()
