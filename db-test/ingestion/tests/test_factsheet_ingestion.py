"""Integration tests for Factsheet Data Ingestion in the VB-SIF domain."""
from datetime import date
from decimal import Decimal
import json
import unittest
from uuid import uuid4

import psycopg

from database.migrate import verify_target
from factsheetDataIngestion.config import FactsheetConfig
from factsheetDataIngestion.date_utils import parse_reporting_month, parse_portfolio_as_of, parse_flexible_date
from factsheetDataIngestion.fund_registry import FUND_HOUSES, TIGZIG_TO_AMC, normalize_name, slugify
from factsheetDataIngestion.strategy_manager import StrategyManager
from factsheetDataIngestion.loader import FactsheetIngester, _clean_numeric
from ingestion.tigzig.config import DatabaseConfig


class FactsheetDateUtilsTests(unittest.TestCase):
    def test_reporting_month_normalization(self):
        self.assertEqual(parse_reporting_month('2026-09'), date(2026, 9, 1))
        self.assertEqual(parse_reporting_month('September 2026'), date(2026, 9, 1))
        self.assertEqual(parse_reporting_month('30th September 2026'), date(2026, 9, 1))
        self.assertEqual(parse_reporting_month('September 30, 2026'), date(2026, 9, 1))
        self.assertEqual(parse_reporting_month('2026-09-30'), date(2026, 9, 1))
        self.assertIsNone(parse_reporting_month(''))
        self.assertIsNone(parse_reporting_month(None))

    def test_portfolio_as_of_parsing(self):
        self.assertEqual(parse_portfolio_as_of('2026-09-30'), date(2026, 9, 30))
        self.assertEqual(parse_portfolio_as_of('30th September 2026'), date(2026, 9, 30))
        self.assertEqual(parse_portfolio_as_of('September 30, 2026'), date(2026, 9, 30))
        self.assertEqual(parse_portfolio_as_of('2026-09'), date(2026, 9, 30))
        self.assertEqual(parse_portfolio_as_of('February 2024'), date(2024, 2, 29))

    def test_flexible_date_parsing(self):
        self.assertEqual(parse_flexible_date('February 25, 2026'), date(2026, 2, 25))
        self.assertEqual(parse_flexible_date('2026-02-25'), date(2026, 2, 25))


class FactsheetRegistryTests(unittest.TestCase):
    def test_36_registered_funds(self):
        self.assertEqual(len(FUND_HOUSES), 36)

    def test_18_amcs_registered(self):
        self.assertEqual(len(set(FUND_HOUSES.values())), 18)
        self.assertEqual(len(TIGZIG_TO_AMC), 18)

    def test_numeric_cleaning(self):
        self.assertEqual(_clean_numeric('1,234.56 %'), Decimal('1234.56'))
        self.assertEqual(_clean_numeric('₹ 500 Cr'), Decimal('500'))
        self.assertEqual(_clean_numeric('-0.5 %'), Decimal('-0.5'))
        self.assertIsNone(_clean_numeric('Nil'))
        self.assertIsNone(_clean_numeric(None))


class FactsheetDatabaseIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.connection = psycopg.connect(
            **DatabaseConfig.from_env().connection_kwargs(), autocommit=False
        )
        verify_target(self.connection)

    def tearDown(self):
        self.connection.rollback()
        self.connection.close()

    def test_strategy_manager_sync(self):
        manager = StrategyManager(self.connection)
        summary = manager.sync_all()
        self.assertEqual(summary.amcs_synced, len(set(FUND_HOUSES.values())))
        self.assertEqual(summary.strategies_synced, len(FUND_HOUSES))
        schemes = self.connection.execute('SELECT count(*) FROM public.sif_schemes').fetchone()[0]
        links = self.connection.execute('SELECT count(*) FROM public.sif_strategy_scheme_links').fetchone()[0]
        self.assertEqual(summary.schemes_synced, schemes)
        self.assertEqual(summary.scheme_links_synced, links)

        # Verify cross-AMC trigger safety
        amc_row = self.connection.execute("SELECT amc_id FROM public.sif_amcs LIMIT 1").fetchone()
        self.assertIsNotNone(amc_row)

    def test_factsheet_ingestion_and_views(self):
        config = FactsheetConfig.from_env()
        base_dir = config.data_dir / 'latest'
        if not (base_dir / 'extraction').is_dir():
            base_dir = config.sif_source_dir / 'output' / 'latest'

        ingester = FactsheetIngester(self.connection, config)
        report = ingester.load_factsheets(
            extraction_dir=base_dir / 'extraction',
            manifest_path=base_dir / 'manifest.json' if (base_dir / 'manifest.json').is_file() else None,
            pdfs_dir=base_dir if base_dir.is_dir() else None,
        )

        extracted = {
            name: data
            for path in (base_dir / 'extraction').glob('*.json')
            for name, data in json.loads(path.read_text(encoding='utf-8')).items()
        }
        self.assertEqual(report.status, 'SUCCEEDED')
        self.assertEqual(report.reports_ingested, len(extracted))
        self.assertEqual(report.documents_ingested, len({
            data['Factsheet Source']['SHA256'] for data in extracted.values()
        }))
        self.assertEqual(len(report.errors), 0)

        # Verify database view vw_sif_strategy_latest_report
        views = self.connection.execute("""
            SELECT count(*) FROM public.vw_sif_strategy_latest_report WHERE report_id IS NOT NULL
        """).fetchone()[0]
        self.assertGreaterEqual(views, report.reports_ingested)

        # Verify child table counts
        holdings = self.connection.execute("SELECT count(*) FROM public.sif_report_holdings").fetchone()[0]
        self.assertGreater(holdings, 1000)

        allocations = self.connection.execute("SELECT count(*) FROM public.sif_report_allocations").fetchone()[0]
        self.assertGreater(allocations, 200)

        # Verify vw_sif_source_freshness
        freshness = self.connection.execute("""
            SELECT status FROM public.sif_ingestion_runs WHERE source_code='OFFICIAL_FACTSHEET' ORDER BY run_id DESC LIMIT 1
        """).fetchone()[0]
        self.assertEqual(freshness, 'SUCCEEDED')


if __name__ == '__main__':
    unittest.main()
