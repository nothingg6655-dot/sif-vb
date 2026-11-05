"""Command-line interface for Factsheet Data Ingestion."""
import argparse
import json
import logging
from pathlib import Path
import sys

import psycopg

from database.migrate import verify_target
from .config import DatabaseConfig, FactsheetConfig
from .extractor import FactsheetExtractor
from .loader import ingest_factsheet_data
from .strategy_manager import StrategyManager, sync_sif_identities

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s: %(message)s')
LOG = logging.getLogger('factsheet-cli')


def cmd_seed_strategies(args: argparse.Namespace) -> int:
    LOG.info("Synchronizing AMCs, canonical strategies, and scheme links...")
    summary = sync_sif_identities()
    print("\n--- SIF Identity Synchronization Summary ---")
    print(f"AMCs synchronized:          {summary.amcs_synced}")
    print(f"Aliases registered:         {summary.aliases_synced}")
    print(f"Strategies synchronized:    {summary.strategies_synced}")
    print(f"Schemes synchronized:       {summary.schemes_synced}")
    print(f"Scheme links verified:      {summary.scheme_links_synced}")
    print("--------------------------------------------\n")
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    source_dir = Path(args.dir).resolve() if args.dir else None
    LOG.info("Loading factsheets from %s", source_dir or 'default data/factsheets/latest')
    report = ingest_factsheet_data(source_dir=source_dir)
    print("\n--- Factsheet Database Ingestion Report ---")
    print(f"Ingestion Run ID:     {report.run_id}")
    print(f"Status:               {report.status}")
    print(f"Documents Ingested:   {report.documents_ingested}")
    print(f"Reports Ingested:     {report.reports_ingested}")
    print(f"Holdings Ingested:    {report.holdings_ingested}")
    print(f"Allocations Ingested: {report.allocations_ingested}")
    if report.errors:
        print(f"Errors ({len(report.errors)}):")
        for err in report.errors[:5]:
            print(f"  - {err}")
    print("-------------------------------------------\n")
    return 0 if report.status in ('SUCCEEDED', 'PARTIAL') else 1


def cmd_extract(args: argparse.Namespace) -> int:
    extractor = FactsheetExtractor()
    if args.pdf:
        pdf_path = Path(args.pdf).resolve()
        LOG.info("Extracting single PDF: %s", pdf_path)
        funds = extractor.extract_pdf(pdf_path)
        output_path = Path(args.output).resolve() if args.output else pdf_path.with_suffix('.json')
        output_path.write_text(json.dumps(funds, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        print(f"Extracted {len(funds)} schemes to {output_path}")
        return 0

    LOG.info("Running factsheet discovery & extraction cycle...")
    out_dir = Path(args.output_dir).resolve() if args.output_dir else None
    report = extractor.run_discovery_cycle(output_dir=out_dir, offline=args.offline, browser=not args.no_browser)
    print(f"Extracted {report.get('Extracted Schemes', 0)} / {report.get('Registered Schemes', 0)} registered schemes")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    LOG.info("Starting complete factsheet synchronization...")
    sync_sif_identities()
    source_dir = Path(args.dir).resolve() if args.dir else None
    report = ingest_factsheet_data(source_dir=source_dir)
    print("\n--- Complete Factsheet Sync Finished ---")
    print(f"Status:             {report.status}")
    print(f"Documents Ingested: {report.documents_ingested}")
    print(f"Reports Ingested:   {report.reports_ingested}")
    print(f"Holdings Ingested:  {report.holdings_ingested}")
    print(f"Allocations:       {report.allocations_ingested}")
    if report.errors:
        for error in report.errors[:5]:
            print(f"  - {error}")
    print("----------------------------------------\n")
    return 0 if report.status in ('SUCCEEDED', 'PARTIAL') else 1


def cmd_status(args: argparse.Namespace) -> int:
    cfg = DatabaseConfig.from_env()
    with psycopg.connect(**cfg.connection_kwargs(), autocommit=True) as conn:
        verify_target(conn)
        doc_count = conn.execute("SELECT count(*) FROM public.sif_factsheet_documents").fetchone()[0]
        rpt_count = conn.execute("SELECT count(*) FROM public.sif_factsheet_reports").fetchone()[0]
        strat_count = conn.execute("SELECT count(*) FROM public.sif_strategies").fetchone()[0]
        amc_count = conn.execute("SELECT count(*) FROM public.sif_amcs").fetchone()[0]
        links_count = conn.execute("SELECT count(*) FROM public.sif_strategy_scheme_links WHERE review_status='VERIFIED'").fetchone()[0]
        holdings_count = conn.execute("SELECT count(*) FROM public.sif_report_holdings").fetchone()[0]
        allocations_count = conn.execute("SELECT count(*) FROM public.sif_report_allocations").fetchone()[0]

        last_run = conn.execute("""
            SELECT run_id, status, started_at, finished_at, inserted_count, rejected_count, error_summary
            FROM public.sif_ingestion_runs
            WHERE source_code='OFFICIAL_FACTSHEET'
            ORDER BY run_id DESC LIMIT 1
        """).fetchone()

        latest_reports = conn.execute("""
            SELECT strategy_name, report_month, net_aum_cr, published_benchmark, validation_status
            FROM public.vw_sif_strategy_latest_report
            WHERE report_id IS NOT NULL
            ORDER BY report_month DESC, strategy_name
            LIMIT 10
        """).fetchall()

        print("\n================ SIF Factsheet Database Status ================")
        print(f"Registered AMCs (sif_amcs):                    {amc_count}")
        print(f"Canonical Strategies (sif_strategies):         {strat_count}")
        print(f"Verified Scheme Links (sif_strategy_links):    {links_count}")
        print(f"Factsheet Documents (sif_factsheet_documents): {doc_count}")
        print(f"Factsheet Reports (sif_factsheet_reports):     {rpt_count}")
        print(f"Portfolio Holdings (sif_report_holdings):       {holdings_count}")
        print(f"Allocations/Totals (sif_report_allocations):   {allocations_count}")
        if last_run:
            print(f"\nLast Ingestion Run: #{last_run[0]} | Status: {last_run[1]} | Inserted: {last_run[4]} | Rejected: {last_run[5]}")
            if last_run[6]:
                print(f"Errors: {last_run[6]}")

        if latest_reports:
            print(f"\nSample Latest Strategy Reports ({len(latest_reports)} shown):")
            for r in latest_reports:
                aum_str = f"{r[2]:,.2f} Cr" if r[2] is not None else "N/A"
                print(f"  - {r[0]} ({r[1]}): AUM={aum_str} | Benchmark={r[3] or 'N/A'} [{r[4]}]")
        print("==============================================================\n")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    from .verification import verify_storage
    report = verify_storage(Path(args.dir) if args.dir else None)
    print(json.dumps(report, indent=2))
    return 0 if report['status'] == 'PASSED' else 1


def cmd_scheduler(args: argparse.Namespace) -> int:
    import time
    LOG.info("Starting Factsheet Ingestion Scheduler (interval=%d seconds)...", args.interval)
    while True:
        try:
            LOG.info("Running scheduled factsheet sync cycle...")
            sync_sif_identities()
            extractor = FactsheetExtractor()
            extractor.run_discovery_cycle(offline=args.offline, browser=not args.no_browser)
            report = ingest_factsheet_data()
            LOG.info("Cycle completed: %d documents, %d reports ingested. Status: %s",
                     report.documents_ingested, report.reports_ingested, report.status)
        except Exception as exc:
            LOG.error("Scheduled factsheet cycle failed: %s", exc)

        if not args.watch:
            return 0
        LOG.info("Sleeping for %d seconds until next factsheet check...", args.interval)
        time.sleep(args.interval)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='python -m factsheetDataIngestion',
        description='SIF Factsheet Data Ingestion & Extraction for VB-SIF',
    )
    subparsers = parser.add_subparsers(dest='command', required=True)

    # seed-strategies
    sub_seed = subparsers.add_parser('seed-strategies', help='Ensure AMCs, canonical strategies, and scheme links exist')
    sub_seed.set_defaults(func=cmd_seed_strategies)

    # load
    sub_load = subparsers.add_parser('load', help='Ingest extracted factsheet JSON and PDFs into PostgreSQL')
    sub_load.add_argument('--dir', type=Path, help='Directory containing extraction/ folder and manifest.json')
    sub_load.set_defaults(func=cmd_load)

    # extract
    sub_ext = subparsers.add_parser('extract', help='Extract factsheet data from PDFs')
    sub_ext.add_argument('--pdf', type=Path, help='Path to single PDF to extract')
    sub_ext.add_argument('--output', type=Path, help='Output path for single PDF extraction')
    sub_ext.add_argument('--output-dir', type=Path, help='Output directory for discovery run')
    sub_ext.add_argument('--offline', action='store_true', default=True, help='Extract from local official PDFs')
    sub_ext.add_argument('--no-browser', action='store_true', default=True, help='Disable browser discovery')
    sub_ext.set_defaults(func=cmd_extract)

    # sync
    sub_sync = subparsers.add_parser('sync', help='Seed strategies and ingest factsheet data in one step')
    sub_sync.add_argument('--dir', type=Path, help='Source directory for extracted factsheets')
    sub_sync.set_defaults(func=cmd_sync)

    # status
    sub_status = subparsers.add_parser('status', help='Report database factsheet ingestion status and coverage')
    sub_status.set_defaults(func=cmd_status)

    sub_verify = subparsers.add_parser('verify', help='Read-only comparison of all extracted factsheets with database records')
    sub_verify.add_argument('--dir', type=Path, help='Directory containing extraction/ and source PDFs')
    sub_verify.set_defaults(func=cmd_verify)

    # scheduler
    sub_sched = subparsers.add_parser('scheduler', help='Run continuous factsheet discovery, extraction, and ingestion scheduler')
    sub_sched.add_argument('--interval', type=int, default=3600, help='Polling interval in seconds (default: 3600)')
    sub_sched.add_argument('--watch', action='store_true', default=True, help='Run continuously in watch mode')
    sub_sched.add_argument('--offline', action='store_true', default=True, help='Extract from local official PDFs')
    sub_sched.add_argument('--no-browser', action='store_true', default=True, help='Disable browser discovery')
    sub_sched.set_defaults(func=cmd_scheduler)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == '__main__':
    sys.exit(main())
