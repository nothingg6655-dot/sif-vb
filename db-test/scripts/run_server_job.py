"""One in-process-backend scheduled worker; never launches another scheduler."""
from pathlib import Path
import logging
import os
import subprocess
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check_runtime(browser=False):
    print('SIF_WORKER_STAGE=dependencies', flush=True)
    import psycopg, pyarrow, dotenv, pymupdf, requests, bs4, playwright.sync_api  # noqa: F401
    from ingestion.tigzig.config import DatabaseConfig
    from factsheetDataIngestion.extractor import FactsheetExtractor  # noqa: F401
    DatabaseConfig.from_env()
    if browser:
        if sys.platform != 'win32' and not shutil.which('tesseract'):
            raise RuntimeError('Raster factsheet OCR requires Tesseract')
        from playwright.sync_api import sync_playwright
        options = {'channel': 'msedge'} if sys.platform == 'win32' else {
            'args': ['--no-sandbox', '--disable-dev-shm-usage']}
        with sync_playwright() as playwright:
            instance = playwright.chromium.launch(headless=True, **options)
            instance.close()


def run(job):
    check_runtime()
    if job == 'nav':
        from ingestion.tigzig.worker import run_sync_job
        print('SIF_WORKER_STAGE=nav_source', flush=True)
        result = run_sync_job('render-internal-two-hourly', 'scheduled', recover_abandoned=True)
        if result['status'] not in ('succeeded', 'unchanged'):
            raise RuntimeError('NAV source worker did not complete')
        print('SIF_WORKER_STAGE=nav_metrics', flush=True)
        subprocess.run([os.environ.get('NODE_BINARY', 'node'),
                        str(ROOT / 'backend/dist/utils/calculate-scheme-performance.js')],
                       cwd=ROOT / 'backend', check=True)
    elif job == 'factsheets':
        from factsheetDataIngestion.extractor import FactsheetExtractor
        from factsheetDataIngestion.loader import ingest_factsheet_data
        extractor = FactsheetExtractor()
        print('SIF_WORKER_STAGE=factsheet_discovery', flush=True)
        report = extractor.run_discovery_cycle(offline=False, browser=True)
        if not report.get('Extracted Schemes'):
            raise RuntimeError('No factsheets could be extracted')
        # The ingester synchronizes identities inside its own transaction.
        print('SIF_WORKER_STAGE=factsheet_database', flush=True)
        loaded = ingest_factsheet_data()
        if loaded.status not in ('SUCCEEDED', 'PARTIAL'):
            raise RuntimeError('Factsheet database ingestion did not complete')
    else:
        raise ValueError('Unknown scheduled job')
    print('SIF_WORKER_STAGE=complete', flush=True)


if __name__ == '__main__':
    os.chdir(ROOT)
    logging.basicConfig(level=logging.INFO)
    try:
        if sys.argv[1:] == ['--check']:
            check_runtime(browser=True)
        elif len(sys.argv) == 2:
            run(sys.argv[1])
        else:
            raise ValueError('Expected --check, nav, or factsheets')
    except Exception as error:
        # The scheduler persists a safe failure message; no database secrets in logs.
        print(f'SIF_WORKER_ERROR={type(error).__name__}', file=sys.stderr, flush=True)
        raise SystemExit(1)
