from datetime import date
from decimal import Decimal
import json
import unittest

from scripts.refresh_official_benchmarks import parse_bse, parse_nse


class OfficialBenchmarkTests(unittest.TestCase):
    def test_nse_requires_both_indexes_for_the_exact_report_date(self):
        text = ('Index Name,Index Date,Closing Index Value\n'
                'Nifty 50,05-11-2026,24536.6\n'
                'Nifty MidSmallcap 400,05-11-2026,20562.3\n')
        self.assertEqual(parse_nse(text, date(2026, 11, 5)), {
            'nifty50': Decimal('24536.6'), 'niftymidsml': Decimal('20562.3')})
        with self.assertRaises(ValueError):
            parse_nse(text, date(2026, 11, 4))
        with self.assertRaises(ValueError):
            parse_nse(text.split('Nifty MidSmallcap')[0], date(2026, 11, 5))
        with self.assertRaises(ValueError):
            parse_nse(text.replace('24536.6', 'NaN'), date(2026, 11, 5))

    def test_bse_verifies_index_identity_and_rejects_conflicting_closes(self):
        def payload(name='BSE 500', second='36345.27'):
            header = json.dumps([{'Scrip': name}])
            values = json.dumps([
                {'date': 'Thu Nov 05 2026 00:00:00', 'value': '36345.27'},
                {'date': 'Thu Nov 05 2026 00:00:00', 'value': second},
            ])
            return json.dumps(header + '#@#' + values)
        first, last = date(2026, 11, 1), date(2026, 11, 5)
        self.assertEqual(parse_bse(payload(), first, last), {
            date(2026, 11, 5): Decimal('36345.27')})
        with self.assertRaises(ValueError):
            parse_bse(payload('BSE SENSEX'), first, last)
        with self.assertRaises(ValueError):
            parse_bse(payload(second='36000'), first, last)
        with self.assertRaises(ValueError):
            parse_bse(payload(), date(2026, 10, 1), date(2026, 10, 31))


if __name__ == '__main__':
    unittest.main()
