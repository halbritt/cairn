import unittest
from tools.csvtool import parse_record


class T(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_record(["a", "2"]), {"name": "a", "qty": 2})
