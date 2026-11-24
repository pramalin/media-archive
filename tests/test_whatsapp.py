import unittest
from media_archive.whatsapp import parse_header

class HeaderTests(unittest.TestCase):
    def test_header(self):
        self.assertEqual(parse_header('[10:32, 11/11/2026] Example Sender: '), {'timestamp_raw':'10:32, 11/11/2026','sender':'Example Sender'})
    def test_unknown(self):
        self.assertIsNone(parse_header('unknown')['sender'])
