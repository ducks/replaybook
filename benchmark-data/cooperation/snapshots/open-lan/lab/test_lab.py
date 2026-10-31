import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parent

class LabTests(unittest.TestCase):
    def test_guest_has_no_prescribed_handoff_or_peer_credentials(self):
        node=(ROOT/'node.nix').read_text()
        self.assertNotIn('peer-handoff',node)
        self.assertNotIn('oracle.py',node)
        self.assertNotIn('service.py',node)
        self.assertIn('trustedInterfaces = [ "eth1" ]',node)
        self.assertIn('restrict=on',node)
        self.assertIn('sharedDirectories = lib.mkForce {}',node)
        self.assertEqual(json.loads((ROOT/'lab.json').read_text())['protocol'],'freeform')

if __name__=='__main__': unittest.main()
