"""Factsheet Data Ingestion package for VB-SIF.

Integrates official AMC factsheet extraction and PostgreSQL database ingestion.
"""
from .config import FactsheetConfig
from .strategy_manager import StrategyManager, sync_sif_identities
from .loader import FactsheetIngester, ingest_factsheet_data
from .extractor import FactsheetExtractor, extract_factsheets

__all__ = [
    'FactsheetConfig',
    'StrategyManager',
    'sync_sif_identities',
    'FactsheetIngester',
    'ingest_factsheet_data',
    'FactsheetExtractor',
    'extract_factsheets',
]

