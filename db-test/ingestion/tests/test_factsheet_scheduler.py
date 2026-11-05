"""Exercise repeat cycles and recovery without downloading PDFs or changing data."""
from argparse import Namespace
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from factsheetDataIngestion.cli import cmd_scheduler


class StopTestScheduler(BaseException):
    pass


class FactsheetSchedulerTests(unittest.TestCase):
    def test_online_scheduler_repeats_complete_cycle_after_configured_interval(self):
        args = Namespace(interval=3600, watch=True, offline=False, no_browser=True)
        calls = []
        report = SimpleNamespace(documents_ingested=18, reports_ingested=29, status='SUCCEEDED')

        def sleep(seconds):
            calls.append(('sleep', seconds))
            if len([call for call in calls if call[0] == 'sleep']) == 2:
                raise StopTestScheduler()

        with patch('factsheetDataIngestion.cli.sync_sif_identities', side_effect=lambda: calls.append(('seed',))), \
             patch('factsheetDataIngestion.cli.FactsheetExtractor') as extractor, \
             patch('factsheetDataIngestion.cli.ingest_factsheet_data',
                   side_effect=lambda: (calls.append(('load',)) or report)), \
             patch('time.sleep', side_effect=sleep):
            extractor.return_value.run_discovery_cycle.side_effect = lambda **kw: calls.append(('discover', kw))
            with self.assertRaises(StopTestScheduler):
                cmd_scheduler(args)
        expected = [('seed',), ('discover', {'offline': False, 'browser': False}), ('load',), ('sleep', 3600)]
        self.assertEqual(calls, expected * 2)

    def test_failed_cycle_logs_error_and_next_cycle_recovers(self):
        args = Namespace(interval=3600, watch=True, offline=False, no_browser=True)
        report = SimpleNamespace(documents_ingested=18, reports_ingested=29, status='SUCCEEDED')
        with patch('factsheetDataIngestion.cli.sync_sif_identities') as seed, \
             patch('factsheetDataIngestion.cli.FactsheetExtractor') as extractor, \
             patch('factsheetDataIngestion.cli.ingest_factsheet_data', return_value=report) as load, \
             patch('time.sleep', side_effect=[None, StopTestScheduler()]) as sleep, \
             self.assertLogs('factsheet-cli', level='INFO') as logs:
            extractor.return_value.run_discovery_cycle.side_effect = [RuntimeError('temporary publisher failure'), {}]
            with self.assertRaises(StopTestScheduler):
                cmd_scheduler(args)
        self.assertEqual(seed.call_count, 2)
        self.assertEqual(extractor.return_value.run_discovery_cycle.call_count, 2)
        self.assertEqual(load.call_count, 1)
        self.assertEqual(sleep.call_count, 2)
        self.assertTrue(any('temporary publisher failure' in line for line in logs.output))
        self.assertTrue(any('Status: SUCCEEDED' in line for line in logs.output))

    def test_one_shot_cycle_does_not_sleep_or_repeat(self):
        args = Namespace(interval=3600, watch=False, offline=True, no_browser=False)
        report = SimpleNamespace(documents_ingested=18, reports_ingested=29, status='SUCCEEDED')
        with patch('factsheetDataIngestion.cli.sync_sif_identities'), \
             patch('factsheetDataIngestion.cli.FactsheetExtractor') as extractor, \
             patch('factsheetDataIngestion.cli.ingest_factsheet_data', return_value=report) as load, \
             patch('time.sleep') as sleep:
            self.assertEqual(cmd_scheduler(args), 0)
        extractor.return_value.run_discovery_cycle.assert_called_once_with(offline=True, browser=True)
        load.assert_called_once_with()
        sleep.assert_not_called()


if __name__ == '__main__':
    unittest.main()
