import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

from automation import (
    Candidate, OfficialClient, Source, discover_candidates, filename,
    load_sources, output_lock, pdf_period, reporting_period, run_once, sync_source,
)


SOURCE = Source("dyna", "360 ONE Asset", ("https://www.360.one/download",),
                ("https://www.360.one/", "https://s3.ap-south-1.amazonaws.com/x-web-s3.360.one/"),
                r"(?i)dyna.*factsheet")


def make_pdf(month="August", extra=""):
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((50, 50), f"Portfolio as on {month} 31, 2026\n{extra}")
        return document.tobytes()


def valid_extract(path):
    return {"DynaSIF Equity Long-Short Fund": {
        "AMC / Fund House": "360 ONE Asset", "Portfolio": [{"Instrument": "Example"}],
        "NAV": {"Direct Plan": 10.0}, "AUM (INR Cr)": {"Net AUM": 100.0},
        "Extraction Validation": {"Status": "passed", "Errors": []},
    }}


class FakeClient:
    def __init__(self, editions):
        self.editions = editions
        self.downloads = []

    def discover(self):
        return [candidate for candidate, content in self.editions]

    def download(self, candidate):
        self.downloads.append(candidate.url)
        return next(content for item, content in self.editions if item == candidate)


def edition(month="August", fixed=False, extra=""):
    url = "https://www.360.one/Dyna_factsheet.pdf" if fixed else f"https://www.360.one/Dyna_factsheet_{month}_2026.pdf"
    return Candidate(url, SOURCE.page_urls[0], None if fixed else reporting_period(month + " 2026")), make_pdf(month, extra)


class DiscoveryTests(unittest.TestCase):
    def test_official_sources_config(self):
        self.assertEqual(len(load_sources(Path(__file__).parent / "sources.json")), 2)

    def test_reporting_month_ignores_upload_timestamp_and_accepts_compact_dates(self):
        cases = {"August_31_2026_08102026093631.pdf": "2026-08", "July31_2026": "2026-07",
                 "31st August 2026": "2026-08", "June2026": "2026-06", "August - 26": "2026-08",
                 "2026-09-30": "2026-09", "Dyna_factsheet_f2e1b89cd6.pdf": None}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(reporting_period(text), expected)

    def test_html_relative_links_and_nextjs_escaped_json(self):
        markup = r'''<a href="/Dyna_factsheet_July_2026.pdf">July</a>
          {\"file_name\":\"August - 26\",\"url\":\"https:\/\/s3.ap-south-1.amazonaws.com\/x-web-s3.360.one\/Dyna_factsheet.pdf\"}
          <a href="https://third-party.example/Dyna_factsheet_September_2026.pdf">Bad source</a>
          <a href="https://www.360.one.evil.example/Dyna_factsheet_September_2026.pdf">Fake host</a>'''
        candidates = discover_candidates(markup, SOURCE.page_urls[0], SOURCE)
        self.assertEqual(len(candidates), 2)
        self.assertEqual({c.period for c in candidates}, {"2026-07", "2026-08"})

    def test_only_the_official_s3_bucket_is_allowed(self):
        self.assertTrue(SOURCE.allows("https://s3.ap-south-1.amazonaws.com/x-web-s3.360.one/a.pdf"))
        self.assertFalse(SOURCE.allows("https://s3.ap-south-1.amazonaws.com/other-bucket/a.pdf"))
        self.assertFalse(SOURCE.allows("http://www.360.one/a.pdf"))
        self.assertFalse(SOURCE.allows("https://someone@www.360.one/a.pdf"))
        self.assertFalse(SOURCE.allows("https://s3.ap-south-1.amazonaws.com/x-web-s3.360.one/../other/a.pdf"))
        self.assertFalse(SOURCE.allows("https://s3.ap-south-1.amazonaws.com/x-web-s3.360.one/%2e%2e/other/a.pdf"))

    def test_publisher_timestamp_distinguishes_same_month_revisions(self):
        markup = '''<a href="/Dyna_factsheet_August_2026_10092026100000.pdf">Revised</a>
                    <a href="/Dyna_factsheet_August_2026_08092026100000.pdf">Original</a>'''
        candidates = discover_candidates(markup, SOURCE.page_urls[0], SOURCE)
        self.assertGreater(candidates[0].published_at, candidates[1].published_at)

    def test_redirect_is_checked_before_following(self):
        with OfficialClient(SOURCE, browser=False) as client:
            response = unittest.mock.MagicMock()
            response.__enter__.return_value = response
            response.is_redirect = True
            response.headers = {"Location": "https://unofficial.example/factsheet.pdf"}
            with patch.object(client.session, "get", return_value=response) as request:
                with self.assertRaisesRegex(ValueError, "allowlist"):
                    client.get(SOURCE.page_urls[0])
                request.assert_called_once()

    def test_invalid_pdf_response(self):
        with OfficialClient(SOURCE, browser=False) as client:
            with patch.object(client, "get", return_value=b"<html>Access denied</html>"):
                with self.assertRaisesRegex(ValueError, "Expected a PDF"):
                    client.download(edition()[0])

    def test_filename_preserves_house_name(self):
        self.assertEqual(filename("360 ONE Asset"), "360 ONE Asset")
        self.assertEqual(filename("Edelweiss Mutual Fund"), "Edelweiss Mutual Fund")


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def sync(self, editions, previous=None, extractor=valid_extract):
        return sync_source(SOURCE, self.directory, previous or {}, FakeClient(editions), extractor)

    def test_latest_month_download_and_named_outputs(self):
        client = FakeClient([edition("July"), edition("September"), edition("August")])
        result = sync_source(SOURCE, self.directory, {}, client, valid_extract)
        self.assertEqual(result["period"], "2026-09")
        self.assertEqual(len(client.downloads), 1)
        self.assertTrue((self.directory / "360 ONE Asset.pdf").is_file())
        data = json.loads((self.directory / "360 ONE Asset.json").read_text())
        provenance = data["DynaSIF Equity Long-Short Fund"]["Factsheet Source"]
        self.assertEqual(provenance["Reporting Month"], "2026-09")
        self.assertTrue(provenance["PDF URL"].endswith("September_2026.pdf"))

    def test_same_month_revision_selected_by_publisher_timestamp(self):
        older = Candidate("https://www.360.one/original.pdf", SOURCE.page_urls[0], "2026-08", "2026-09-08T10:00:00")
        newer = Candidate("https://www.360.one/revised.pdf", SOURCE.page_urls[0], "2026-08", "2026-09-10T10:00:00")
        result = self.sync([(older, make_pdf()), (newer, make_pdf(extra="Revised"))])
        self.assertEqual(result["url"], newer.url)

    def test_missing_required_fund_rejects_partial_extraction(self):
        source = Source("dyna", SOURCE.fund_house, SOURCE.page_urls, SOURCE.allowed_url_prefixes,
                        SOURCE.pdf_pattern, ("DynaSIF Equity Ex-Top 100 Long-Short Fund",))
        with self.assertRaisesRegex(ValueError, "Required funds missing"):
            sync_source(source, self.directory, {}, FakeClient([edition()]), valid_extract)
        self.assertFalse((self.directory / "360 ONE Asset.json").exists())

    def test_unreadable_reporting_month_keeps_pdf_for_inspection(self):
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((50, 50), "Unrecognized new layout")
            content = document.tobytes()
        with self.assertRaisesRegex(ValueError, "PDF saved"):
            self.sync([(edition()[0], content)])
        self.assertEqual(len(list((self.directory / "archive").rglob("*.pdf"))), 1)

    def test_identical_content_does_not_extract_again(self):
        item = edition()
        previous = self.sync([item])
        with patch("builtins.print"):
            result = self.sync([item], previous, extractor=lambda path: self.fail("Should not re-extract"))
        self.assertEqual(result["status"], "unchanged")

    def test_fixed_url_is_checked_for_new_month_and_revised_content(self):
        first = edition("August", fixed=True)
        previous = self.sync([first])
        second = edition("September", fixed=True)
        result = self.sync([edition("August"), second], previous)
        self.assertEqual(result["period"], "2026-09")
        revised = edition("September", fixed=True, extra="Publisher correction")
        correction = self.sync([revised], result)
        self.assertEqual(correction["status"], "updated")
        self.assertNotEqual(correction["sha256"], result["sha256"])
        self.assertEqual(len(list((self.directory / "archive").rglob("*.json"))), 3)

    def test_failed_validation_keeps_current_output_and_archives_download(self):
        previous = self.sync([edition()])
        original = (self.directory / "360 ONE Asset.json").read_bytes()

        def failed_extract(path):
            data = valid_extract(path)
            data["DynaSIF Equity Long-Short Fund"]["Extraction Validation"]["Status"] = "failed"
            return data

        with self.assertRaisesRegex(ValueError, "validation failed"):
            self.sync([edition("September")], previous, failed_extract)
        self.assertEqual((self.directory / "360 ONE Asset.json").read_bytes(), original)
        self.assertEqual(len(list((self.directory / "archive").rglob("*.pdf"))), 2)
        self.assertEqual(pdf_period((self.directory / "360 ONE Asset.pdf").read_bytes()), "2026-08")

    def test_wrong_fund_house_and_empty_extraction_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "No funds extracted"):
            self.sync([edition()], extractor=lambda path: {})
        with self.assertRaisesRegex(ValueError, "does not belong"):
            self.sync([edition()], extractor=lambda path: {"Altiva Hybrid Long-Short Fund": {"AMC / Fund House": "Edelweiss Mutual Fund"}})
        self.assertFalse((self.directory / "360 ONE Asset.json").exists())

    def test_older_month_and_mislabelled_month_are_rejected(self):
        previous = self.sync([edition("September")])
        with self.assertRaisesRegex(ValueError, "older than"):
            self.sync([edition("August")], previous)
        with self.assertRaisesRegex(ValueError, "disagrees"):
            self.sync([(edition("September")[0], make_pdf("August"))], previous)

    def test_missing_and_modified_outputs_are_rebuilt(self):
        item = edition()
        previous = self.sync([item])
        (self.directory / "360 ONE Asset.json").write_text("{}")
        result = self.sync([item], previous)
        self.assertEqual(result["status"], "updated")
        (self.directory / "360 ONE Asset.pdf").unlink()
        self.assertEqual(self.sync([item], result)["status"], "updated")

    def test_output_lock_rejects_overlapping_jobs(self):
        with output_lock(self.directory):
            with self.assertRaisesRegex(RuntimeError, "already using"):
                with output_lock(self.directory):
                    self.fail("Should not acquire two locks")

    def test_failure_is_recorded_and_other_sources_continue(self):
        sources = load_sources(Path(__file__).parent / "sources.json")
        factory = unittest.mock.MagicMock()
        with patch("automation.sync_source", side_effect=[RuntimeError("AMC unavailable"),
                   {"status": "unchanged", "period": "2026-09", "json_path": "example.json"}]):
            failures = run_once(sources, self.directory, client_factory=factory)
        self.assertEqual(failures, 1)
        state = json.loads((self.directory / "automation_state.json").read_text())
        self.assertEqual(state["sources"][sources[0].id]["status"], "error")
        self.assertEqual(state["sources"][sources[1].id]["status"], "unchanged")


if __name__ == "__main__":
    unittest.main()
