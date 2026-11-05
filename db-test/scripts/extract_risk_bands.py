"""Enrich all stored latest factsheets using the shared Python risk extractor.

Preview: python -m scripts.extract_risk_bands
Publish and load: python -m scripts.extract_risk_bands --apply

Uses checksum-verified official PDFs and preserves existing portfolio extraction.
The normal factsheet scheduler uses the same enrichment on new downloads.
"""
from argparse import ArgumentParser
import copy
import hashlib
import json
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
import pymupdf

from database.migrate import verify_target
from factsheetDataIngestion.automation import output_lock, utc_now, write_json
from factsheetDataIngestion.config import DatabaseConfig, FactsheetConfig
from factsheetDataIngestion.fund_registry import FUND_HOUSES
from factsheetDataIngestion.loader import FactsheetIngester
from factsheetDataIngestion.monitor import (
    coverage, extractor_version, load_profiles, publish_latest_pdfs, save_scheme_files,
)
from factsheetDataIngestion.risk_bands import FIELD, enrich_risk_bands


def latest_reports(connection):
    return connection.cursor(row_factory=dict_row).execute('''
        SELECT lr.strategy_name,lr.report_id,d.sha256,d.local_storage_uri,
               r.raw_extraction_json AS data
        FROM public.vw_sif_strategy_latest_report lr
        JOIN public.sif_factsheet_reports r USING(report_id)
        JOIN public.sif_factsheet_documents d USING(document_id)
        ORDER BY lr.strategy_name
    ''').fetchall()


def source_pdf(row, directory):
    digest = row['sha256']
    candidates = [directory / 'archive' / (digest + '.pdf')]
    if row['local_storage_uri']:
        candidates.append(Path(row['local_storage_uri']))
    local = row['data'].get('Factsheet Source', {}).get('Local PDF')
    if local:
        candidates.append(Path(local))
    candidates.extend((directory / 'latest').glob('*.pdf'))
    for path in candidates:
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
            return path
    raise FileNotFoundError('No checksum-matching official PDF for ' + row['strategy_name'])


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Publish JSON and load the existing database tables')
    args = parser.parse_args()
    config = FactsheetConfig.from_env()
    directory = config.data_dir
    with output_lock(directory), psycopg.connect(
            **DatabaseConfig.from_env().connection_kwargs()) as connection:
        verify_target(connection)
        rows = latest_reports(connection)
        if not rows:
            raise ValueError('No eligible stored factsheets to enrich')
        original_identity = [(r['strategy_name'], r['report_id'], r['sha256'], r['data']) for r in rows]
        connection.commit()
        funds = {}
        for row in rows:
            name = row['strategy_name']
            data = copy.deepcopy(row['data'])
            path = source_pdf(row, directory)
            with pymupdf.open(path) as document:
                enrich_risk_bands(document, {name: data})
            if data['Field Availability'][FIELD] != 'PUBLISHED':
                raise ValueError('Risk band requires review: ' + name)
            # The backfill changes risk fields only; allocations and holdings
            # continue to come from the previously verified extraction.
            for key in row['data']:
                if key not in {FIELD, 'Field Availability', 'Supplementary Fields', 'Extraction Validation'}:
                    assert data[key] == row['data'][key], (name, key)
            if args.apply:
                archived = directory / 'archive' / (row['sha256'] + '.pdf')
                archived.parent.mkdir(parents=True, exist_ok=True)
                if not archived.exists():
                    archived.write_bytes(path.read_bytes())
                if hashlib.sha256(archived.read_bytes()).hexdigest() != row['sha256']:
                    raise ValueError('Archive checksum mismatch: ' + name)
                data['Factsheet Source']['Local PDF'] = str(archived.resolve())
            funds[name] = data
            print(name + ': ' + json.dumps(data[FIELD], ensure_ascii=False), flush=True)
        missing = sorted(set(FUND_HOUSES) - set(funds))
        if args.apply:
            current = latest_reports(connection)
            if original_identity != [(r['strategy_name'], r['report_id'], r['sha256'], r['data']) for r in current]:
                raise RuntimeError('Latest report changed during extraction; rerun against the new sources')
            connection.commit()
            profiles = load_profiles(config.sources_config)
            state_path = directory / 'monitor_state.json'
            state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {'sources': {}}
            report = coverage(profiles, state, funds)
            publish_latest_pdfs(directory, funds, report)
            manifest = directory / 'latest/manifest.json'
            if json.loads(manifest.read_text(encoding='utf-8'))['Errors']:
                raise RuntimeError('PDF publication failed; database was not changed')
            # The loader uses tuple rows; retain its existing transaction and
            # roll everything back if even one scheme fails ingestion.
            with psycopg.connect(**DatabaseConfig.from_env().connection_kwargs()) as loader_connection:
                ingested = FactsheetIngester(loader_connection, config).load_factsheets(
                    directory / 'latest/extraction', manifest, directory / 'latest')
                if ingested.status != 'SUCCEEDED' or ingested.reports_ingested != len(funds):
                    raise RuntimeError('Risk-band ingestion failed: ' + '; '.join(ingested.errors))
            for profile in profiles:
                house_funds = {name: data for name, data in funds.items()
                               if data['AMC / Fund House'] == profile['fund_house']}
                previous = state['sources'].setdefault(profile['id'], {})
                previous['scheme_files'] = save_scheme_files(directory, house_funds)
                previous['fund_count'] = len(house_funds)
                previous['extractor_version'] = extractor_version()
                previous['risk_band_extracted_at'] = utc_now()
            write_json(state_path, state)
            write_json(directory / 'latest/coverage.json', report)
            write_json(directory / 'latest/extracted_factsheets.json', funds)
            print('Ingestion run: ' + str(ingested.run_id))
        print(json.dumps({'registered': len(FUND_HOUSES), 'extracted': len(funds),
                          'missing_factsheets': missing, 'applied': args.apply}, indent=2))


if __name__ == '__main__':
    main()
