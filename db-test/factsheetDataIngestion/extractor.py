"""High-level extractor interface for SIF factsheets."""
import json
import logging
from pathlib import Path
from typing import Any

from .app import extract_factsheet
from .config import FactsheetConfig
from .monitor import load_profiles, run_once

LOG = logging.getLogger('factsheet-extractor')


class FactsheetExtractor:
    """Extracts structured factsheet data from official AMC PDFs using verified adapters."""

    def __init__(self, config: FactsheetConfig | None = None):
        self.config = config or FactsheetConfig.from_env()

    def extract_pdf(self, pdf_path: Path) -> dict[str, Any]:
        """Extract all supported schemes from a single PDF document."""
        pdf_path = Path(pdf_path).resolve()
        if not pdf_path.is_file():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")
        LOG.info("Extracting schemes from %s", pdf_path.name)
        return extract_factsheet(pdf_path)

    def extract_directory(self, pdfs_dir: Path, output_dir: Path) -> dict[str, dict[str, Any]]:
        """Extract all PDFs in a directory and save scheme JSON files."""
        pdfs_dir = Path(pdfs_dir).resolve()
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)

        all_funds: dict[str, dict[str, Any]] = {}
        for pdf_file in sorted(pdfs_dir.glob('*.pdf')):
            try:
                extracted = self.extract_pdf(pdf_file)
                all_funds.update(extracted)
                for fund_name, data in extracted.items():
                    safe_name = "".join(c if c.isalnum() or c in ' -_' else '_' for c in fund_name)
                    json_path = output_dir / f"{safe_name}.json"
                    json_path.write_text(json.dumps({fund_name: data}, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
            except Exception as exc:
                LOG.error("Failed to extract %s: %s", pdf_file.name, exc)

        return all_funds

    def run_discovery_cycle(self, output_dir: Path | None = None, offline: bool = True, browser: bool = False) -> dict[str, Any]:
        """Run monitor discovery and extraction across all registered AMC profiles."""
        out = Path(output_dir).resolve() if output_dir else self.config.data_dir
        out.mkdir(parents=True, exist_ok=True)
        profiles = load_profiles(self.config.sources_config)
        return run_once(profiles, out, browser=browser, offline=offline)


def extract_factsheets(
    pdf_path: Path | None = None,
    pdfs_dir: Path | None = None,
    output_dir: Path | None = None,
) -> dict[str, Any]:
    extractor = FactsheetExtractor()
    if pdf_path:
        return extractor.extract_pdf(pdf_path)
    if pdfs_dir and output_dir:
        return extractor.extract_directory(pdfs_dir, output_dir)
    return extractor.run_discovery_cycle(output_dir=output_dir, offline=True)
