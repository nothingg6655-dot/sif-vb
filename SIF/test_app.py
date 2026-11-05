import unittest
from pathlib import Path

from app import extract_factsheet
from fund_registry import FUND_HOUSES


SOURCE_DIR = Path(r"C:\Users\shris\Aikyam\POC\DynaSIF")
DYNA_PDF = SOURCE_DIR / "Dyna_SIF_Factsheet_as_on_August_31_2026_f2e1b89cd6.pdf"
ALTIVA_PDF = SOURCE_DIR / "Altiva_Hybrid_LongShort_Fund_Portfolio_Update_September_2026_08102026093631.pdf"


class FundRegistryTests(unittest.TestCase):
    def test_all_supplied_funds_are_registered(self):
        self.assertEqual(len(FUND_HOUSES), 34)


@unittest.skipUnless(DYNA_PDF.is_file(), "DynaSIF fixture PDF is not available")
class DynaSIFExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = extract_factsheet(DYNA_PDF)

    def test_funds_and_portfolio_counts(self):
        self.assertEqual(len(self.data), 3)
        self.assertEqual(len(self.data["DynaSIF Equity Long-Short Fund"]["Portfolio"]), 97)
        self.assertEqual(len(self.data["DynaSIF Active Asset Allocator Long-Short Fund"]["Portfolio"]), 58)
        self.assertEqual(len(self.data["DynaSIF Equity Ex-Top 100 Long-Short Fund"]["Portfolio"]), 54)

    def test_published_values(self):
        equity = self.data["DynaSIF Equity Long-Short Fund"]
        self.assertEqual(equity["AUM (INR Cr)"]["Net AUM"], 407.12)
        self.assertEqual(equity["NAV"]["Direct Plan"], 10.7378)
        self.assertEqual(equity["Portfolio Totals (%)"]["Grand Total"], 100.0)
        self.assertEqual(equity["Extraction Validation"]["Status"], "passed")


@unittest.skipUnless(ALTIVA_PDF.is_file(), "Altiva fixture PDF is not available")
class AltivaExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fund = extract_factsheet(ALTIVA_PDF)["Altiva Hybrid Long-Short Fund"]

    def test_portfolio_and_subtotals(self):
        self.assertEqual(len(self.fund["Portfolio"]), 232)
        self.assertEqual(self.fund["Portfolio Totals (%)"]["Equity & Equity Related"], 39.34)
        self.assertEqual(self.fund["Portfolio Totals (%)"]["Fixed Income"], 40.99)

    def test_scheme_details(self):
        self.assertEqual(self.fund["NAV"]["Direct Plan"], 11.0476)
        self.assertEqual(self.fund["AUM (INR Cr)"]["Month End AUM"], 11297.0)
        self.assertEqual(self.fund["Debt Portfolio Quants"]["Yield to maturity (YTM)"], "8.17%")
        self.assertEqual(self.fund["Extraction Validation"]["Status"], "passed")


if __name__ == "__main__":
    unittest.main()
