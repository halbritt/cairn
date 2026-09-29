import unittest

from surveyor.packet import CAPACITY, encode
from surveyor.schema import RESPONSE_SCHEMA

EXCERPT = "def handler(request):\n    return process(request.body)\n" * 4


class PacketTest(unittest.TestCase):
    def test_source_excerpt_retained(self):
        packet = encode(RESPONSE_SCHEMA, EXCERPT)
        self.assertLessEqual(len(packet), CAPACITY)
        self.assertIn(EXCERPT, packet)
