"""Configuration for Factsheet Data Ingestion."""
from dataclasses import dataclass, field
import os
from pathlib import Path

from ingestion.tigzig.config import DatabaseConfig

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
DEFAULT_DATA_DIR = PROJECT_ROOT / 'data' / 'factsheets'
SIF_PROJECT_ROOT = PROJECT_ROOT.parent / 'SIF'

SOURCE_CODE = 'OFFICIAL_FACTSHEET'
DATASET_NAME = 'factsheet'


@dataclass(frozen=True)
class FactsheetConfig:
    data_dir: Path = field(default_factory=lambda: DEFAULT_DATA_DIR)
    sources_config: Path = field(default_factory=lambda: PACKAGE_DIR / 'official_sources.json')
    sif_source_dir: Path = field(default_factory=lambda: SIF_PROJECT_ROOT)
    db_config: DatabaseConfig = field(default_factory=DatabaseConfig.from_env)

    @classmethod
    def from_env(cls) -> 'FactsheetConfig':
        data_dir_env = os.environ.get('FACTSHEET_DATA_DIR')
        data_dir = Path(data_dir_env).resolve() if data_dir_env else DEFAULT_DATA_DIR
        sources_env = os.environ.get('FACTSHEET_SOURCES_CONFIG')
        sources_path = Path(sources_env).resolve() if sources_env else PACKAGE_DIR / 'official_sources.json'
        if not sources_path.is_file() and (SIF_PROJECT_ROOT / 'official_sources.json').is_file():
            sources_path = SIF_PROJECT_ROOT / 'official_sources.json'

        return cls(
            data_dir=data_dir,
            sources_config=sources_path,
            sif_source_dir=SIF_PROJECT_ROOT,
            db_config=DatabaseConfig.from_env(),
        )
