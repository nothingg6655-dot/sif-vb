"""Discover official AMC factsheets, download new editions and run the extractor."""

import argparse
import hashlib
import html
import json
import logging
import os
import re
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

import pymupdf
import requests

try:
    from .app import extract_factsheet
    from .fund_registry import FUND_HOUSES
except (ImportError, ValueError):
    from app import extract_factsheet
    from fund_registry import FUND_HOUSES

ROOT = Path(__file__).resolve().parent
LOG = logging.getLogger("factsheets")
MAX_BYTES = 50 * 1024 * 1024
MONTH_NAMES = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def filename(name):
    """Preserve the fund-house name, removing only Windows-invalid characters."""
    result = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip().rstrip(". ")
    if not result or result.upper() in {"CON", "PRN", "AUX", "NUL"}:
        raise ValueError(f"Invalid fund-house filename: {name!r}")
    return result


def reporting_period(value):
    """Read a reporting month, never a trailing upload timestamp or file hash."""
    text = re.sub(r"[_\-,/’'\s]+", " ", unquote(value)).lower()
    for month, name in enumerate(MONTH_NAMES, start=1):
        pattern = rf"\b(?:{name}|{name[:3]})(?:\s*\d{{1,2}}(?:st|nd|rd|th)?\b)?\s*(20\d{{2}}|\d{{2}})\b"
        match = re.search(pattern, text)
        if match:
            year = int(match.group(1))
            return f"{year + 2000 if year < 100 else year:04d}-{month:02d}"
    match = re.search(r"\b(20\d{2}) (0?[1-9]|1[0-2])(?: \d{1,2})?\b", text)
    return f"{int(match[1]):04d}-{int(match[2]):02d}" if match else None


@dataclass(frozen=True)
class Source:
    id: str
    fund_house: str
    page_urls: tuple
    allowed_url_prefixes: tuple
    pdf_pattern: str
    required_funds: tuple = ()

    def allows(self, url):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.username or parsed.password:
            return False
        if any(segment in {".", ".."} for segment in unquote(parsed.path).split("/")):
            return False
        for prefix in self.allowed_url_prefixes:
            allowed = urlsplit(prefix)
            if parsed.netloc.lower() == allowed.netloc.lower() and parsed.path.startswith(allowed.path):
                return True
        return False


@dataclass(frozen=True)
class Candidate:
    url: str
    page_url: str
    period: str | None
    published_at: str = ""


def load_sources(path):
    items = json.loads(Path(path).read_text(encoding="utf-8"))["sources"]
    sources = []
    for item in items:
        source = Source(
            item["id"], item["fund_house"], tuple(item["page_urls"]),
            tuple(item["allowed_url_prefixes"]), item["pdf_pattern"],
            tuple(item.get("required_funds", [])),
        )
        filename(source.fund_house)
        if source.fund_house not in FUND_HOUSES.values():
            raise ValueError(f"Unregistered fund house: {source.fund_house}")
        if not source.page_urls or not all(source.allows(url) for url in source.page_urls):
            raise ValueError(f"Publishing pages must be on allowlisted HTTPS URLs: {source.id}")
        re.compile(source.pdf_pattern)
        if any(FUND_HOUSES.get(name) != source.fund_house for name in source.required_funds):
            raise ValueError(f"Required funds must belong to {source.fund_house}.")
        sources.append(source)
    if len({s.id for s in sources}) != len(sources):
        raise ValueError("Each source must have a unique id.")
    if len({filename(s.fund_house).casefold() for s in sources}) != len(sources):
        raise ValueError("Configure one source per fund house, with all its publishing pages.")
    if not sources:
        raise ValueError("No factsheet sources configured.")
    return sources


def discover_candidates(markup, page_url, source):
    # AMC sites include links in HTML, JSON attributes, and Next.js flight data.
    text = html.unescape(markup).replace(r"\/", "/").replace(r"\u002F", "/")
    text = text.replace(r'\"', '"').replace(r"\u0026", "&")
    pattern = r'''(?:https://|/|\.{1,2}/)[^\s<>"'\\]+?\.pdf(?:\?[^\s<>"'\\]*)?'''
    candidates = {}
    for match in re.finditer(pattern, text, re.I):
        url = urljoin(page_url, match.group())
        if not source.allows(url) or not re.search(source.pdf_pattern, unquote(urlsplit(url).path)):
            continue
        period = reporting_period(Path(urlsplit(url).path).name)
        if not period:
            # 360 ONE uses file_name labels for some PDFs with undated filenames.
            labels = re.findall(r'"file_name"\s*:\s*"([^"\n]+)"', text[max(0, match.start() - 1200):match.start()])
            if labels:
                period = reporting_period(labels[-1])
        # Timestamped filenames and adjacent CMS metadata distinguish revisions
        # published for the same reporting month.
        published_at = ""
        timestamp = re.search(r"_(\d{14})\.pdf$", urlsplit(url).path, re.I)
        if timestamp:
            try:
                published_at = datetime.strptime(timestamp[1], "%d%m%Y%H%M%S").isoformat()
            except ValueError:
                pass
        nearby = text[match.end():match.end() + 700].split('"file_name"', 1)[0]
        metadata = re.search(r'"updatedAt"\s*:\s*"(20\d{2}-[^"\n]+)"', nearby)
        if metadata:
            published_at = metadata[1]
        candidates.setdefault(url, Candidate(url, page_url, period, published_at))
    return list(candidates.values())


class OfficialClient:
    """HTTP first; a fresh, headless browser for pages needing JavaScript."""

    def __init__(self, source, browser=True, channel=None):
        self.source = source
        self.browser_enabled = browser
        self.channel = channel or ("msedge" if os.name == "nt" else "chromium")
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; SIF-Factsheet-Monitor/1.0)", "Accept": "*/*"})
        self.playwright = self.browser = self.context = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        if self.context:
            self.context.close()
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()
        self.session.close()

    def get(self, url, referer=None):
        for attempt in range(3):
            try:
                current = url
                for _ in range(6):
                    if not self.source.allows(current):
                        raise ValueError(f"URL outside the official source allowlist: {current}")
                    with self.session.get(current, headers={"Referer": referer} if referer else {},
                                          timeout=(15, 45), stream=True, allow_redirects=False) as response:
                        if response.is_redirect:
                            current = urljoin(current, response.headers["Location"])
                            continue
                        response.raise_for_status()
                        body = bytearray()
                        for chunk in response.iter_content(65536):
                            body.extend(chunk)
                            if len(body) > MAX_BYTES:
                                raise ValueError("Response exceeds the 50 MB download limit.")
                        return bytes(body)
                raise ValueError("Too many redirects.")
            except requests.RequestException as exc:
                status = exc.response.status_code if exc.response is not None else None
                if attempt == 2 or (status is not None and status < 500 and status != 429):
                    raise
                time.sleep(2 ** attempt)

    def browser_context(self):
        if not self.browser_enabled:
            raise RuntimeError("Browser discovery disabled; try again without --no-browser.")
        if not self.context:
            try:
                from playwright.sync_api import sync_playwright
            except ImportError as exc:
                raise RuntimeError("Install requirements.txt to enable JavaScript page discovery.") from exc
            self.playwright = sync_playwright().start()
            self.browser = self.playwright.chromium.launch(channel=self.channel, headless=True)
            self.context = self.browser.new_context(accept_downloads=True)
        return self.context

    def render(self, url):
        page = self.browser_context().new_page()
        try:
            response = page.goto(url, wait_until="domcontentloaded", timeout=60000)
            if not self.source.allows(page.url):
                raise ValueError(f"Publishing page redirected off the official site: {page.url}")
            if response and response.status >= 400:
                raise RuntimeError(f"Official page returned HTTP {response.status}, including in the browser: {url}")
            deadline = time.monotonic() + 15
            while True:
                markup = page.content()
                if discover_candidates(markup, url, self.source) or time.monotonic() >= deadline:
                    return markup
                page.wait_for_timeout(500)
        finally:
            page.close()

    def discover(self):
        found, errors = {}, []
        for url in self.source.page_urls:
            try:
                try:
                    markup = self.get(url).decode("utf-8", errors="replace")
                    candidates = discover_candidates(markup, url, self.source)
                except requests.RequestException:
                    candidates = []
                if not candidates:
                    markup = self.render(url)
                    candidates = discover_candidates(markup, url, self.source)
                if not candidates:
                    raise ValueError(f"No matching factsheet links found: {url}. The site's discovery adapter may need updating.")
                found.update({candidate.url: candidate for candidate in candidates})
            except Exception as exc:
                errors.append(str(exc))
        # Never silently call a stale product-page link 'latest' when the archive failed.
        if errors:
            raise RuntimeError("; ".join(errors))
        return list(found.values())

    def download(self, candidate):
        try:
            content = self.get(candidate.url, candidate.page_url)
        except requests.RequestException:
            response = self.browser_context().request.get(candidate.url,
                headers={"Referer": candidate.page_url}, timeout=60000, max_redirects=0)
            try:
                if not response.ok:
                    raise RuntimeError(f"Official PDF returned HTTP {response.status}: {candidate.url}")
                content = response.body()
            finally:
                response.dispose()
        if len(content) > MAX_BYTES or not content.lstrip().startswith(b"%PDF-"):
            raise ValueError(f"Expected a PDF, received an invalid/oversized response: {candidate.url}")
        return content


def pdf_period(content):
    with pymupdf.open(stream=content, filetype="pdf") as document:
        if document.is_encrypted:
            raise ValueError("Factsheet is password protected.")
        text = "\n".join(page.get_text("text", sort=True) for page in document)
    periods = {
        reporting_period(match[1])
        for match in re.finditer(r"Portfolio(?:\s+summary)?\s+as\s+(?:on|at)\s+([^\n]+)", text, re.I)
    } - {None}
    if len(periods) != 1:
        raise ValueError("Cannot determine a single reporting month from the PDF's portfolio dates.")
    return periods.pop()


def atomic_write(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=path.parent, prefix=".factsheet-", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        for attempt in range(5):
            try:
                os.replace(temp, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.15 * (attempt + 1))
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write_json(path, value):
    atomic_write(path, (json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8"))


@contextmanager
def output_lock(directory):
    """OS-held lock is released automatically after crashes; prevent overlapping jobs."""
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".automation.lock").open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if not stream.tell():
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another factsheet job is already using this output directory.") from exc
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def sync_source(source, directory, previous, client, extractor=extract_factsheet):
    candidates = client.discover()
    if not candidates:
        raise ValueError("No official factsheet candidates discovered.")
    newest_label = max((c.period for c in candidates if c.period), default=None)
    # Inspect every undated link too: AMCs sometimes replace a PDF at a fixed URL.
    selected = [c for c in candidates if c.period is None or c.period == newest_label]
    if len(selected) > 10:
        raise ValueError("Too many undated factsheets; add a more specific source discovery rule.")
    editions = []
    for candidate in selected:
        content = client.download(candidate)
        try:
            period = pdf_period(content)
        except Exception as exc:
            rejected = directory / "archive" / filename(source.fund_house) / "unclassified" / (hashlib.sha256(content).hexdigest()[:16] + ".pdf")
            atomic_write(rejected, content)
            raise ValueError(f"{exc} PDF saved at {rejected}") from exc
        if candidate.period and candidate.period != period:
            raise ValueError(f"Link date {candidate.period} disagrees with PDF portfolio date {period}: {candidate.url}")
        editions.append((period, candidate, content))
    period, candidate, content = max(editions, key=lambda edition: (edition[0], edition[1].published_at))
    if previous.get("period") and period < previous["period"]:
        raise ValueError(f"Official page offered {period}, older than the saved {previous['period']} factsheet; keeping existing output.")
    digest = hashlib.sha256(content).hexdigest()
    stem = filename(source.fund_house)
    pdf_path, json_path = directory / f"{stem}.pdf", directory / f"{stem}.json"
    if (digest == previous.get("sha256") and json_path.is_file() and pdf_path.is_file()
            and hashlib.sha256(pdf_path.read_bytes()).hexdigest() == digest
            and hashlib.sha256(json_path.read_bytes()).hexdigest() == previous.get("json_sha256")):
        return {**previous, "status": "unchanged", "last_checked_at": utc_now(), "error": None, "url": candidate.url}

    # Save downloaded evidence before extraction; layout failures remain inspectable.
    archive = directory / "archive" / stem / period / digest[:16]
    downloaded = archive.with_suffix(".pdf")
    atomic_write(downloaded, content)
    funds = extractor(downloaded)
    if not funds:
        raise ValueError(f"No funds extracted from {downloaded}")
    missing = set(source.required_funds) - set(funds)
    if missing:
        raise ValueError("Required funds missing from extraction: " + ", ".join(sorted(missing)))
    for name, data in funds.items():
        if FUND_HOUSES.get(name) != source.fund_house or data.get("AMC / Fund House") != source.fund_house:
            raise ValueError(f"Fund {name!r} does not belong to {source.fund_house}.")
        validation = data.get("Extraction Validation", {})
        if validation.get("Status") not in {"passed", "passed_with_warnings"}:
            raise ValueError(f"Extraction validation failed for {name}: {validation.get('Errors', [])}. PDF saved at {downloaded}")
        data["Factsheet Source"] = {
            "Publishing Page": candidate.page_url, "PDF URL": candidate.url,
            "Reporting Month": period, "Downloaded At": utc_now(), "SHA256": digest,
        }
    write_json(archive.with_suffix(".json"), funds)
    atomic_write(pdf_path, content)
    atomic_write(json_path, archive.with_suffix(".json").read_bytes())
    return {
        "status": "updated", "period": period, "sha256": digest,
        "json_sha256": hashlib.sha256(json_path.read_bytes()).hexdigest(),
        "url": candidate.url, "publishing_page": candidate.page_url,
        "pdf_path": str(pdf_path.resolve()), "json_path": str(json_path.resolve()),
        "fund_count": len(funds), "last_checked_at": utc_now(),
        "last_success_at": utc_now(), "error": None,
    }


def run_once(sources, directory, browser=True, channel=None, client_factory=OfficialClient):
    directory = Path(directory).resolve()
    with output_lock(directory):
        state_path = directory / "automation_state.json"
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"sources": {}}
        failures = 0
        for source in sources:
            previous = state["sources"].get(source.id, {})
            try:
                with client_factory(source, browser=browser, channel=channel) as client:
                    result = sync_source(source, directory, previous, client)
                LOG.info("%s: %s (%s); %s", source.fund_house, result["status"], result["period"], result["json_path"])
            except Exception as exc:
                failures += 1
                result = {**previous, "status": "error", "last_checked_at": utc_now(), "error": str(exc)}
                LOG.error("%s: %s", source.fund_house, exc)
            state["sources"][source.id] = result
            state["last_run_at"] = utc_now()
            write_json(state_path, state)
        return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="Check once (default; useful for Task Scheduler).")
    mode.add_argument("--watch", action="store_true", help="Keep checking until stopped with Ctrl+C.")
    parser.add_argument("--interval", type=int, default=3600, help="Polling interval in seconds (default: 3600).")
    parser.add_argument("--config", type=Path, default=ROOT / "sources.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output")
    parser.add_argument("--fund-house", action="append", help="Limit checks to this exact registered fund-house name; repeatable.")
    parser.add_argument("--list-sources", action="store_true")
    parser.add_argument("--no-browser", action="store_true", help="Only use HTTP discovery.")
    parser.add_argument("--browser-channel", default=None, help="Playwright channel (default: msedge on Windows, chromium elsewhere).")
    args = parser.parse_args()
    if args.interval < 60:
        parser.error("--interval must be at least 60 seconds.")
    sources = load_sources(args.config)
    if args.list_sources:
        for source in sources:
            print(f"{source.fund_house}: {', '.join(source.page_urls)}")
        print("Not yet configured: " + ", ".join(sorted(set(FUND_HOUSES.values()) - {s.fund_house for s in sources})))
        return 0
    if args.fund_house:
        missing = set(args.fund_house) - {s.fund_house for s in sources}
        if missing:
            parser.error("No configured source for: " + ", ".join(sorted(missing)))
        sources = [s for s in sources if s.fund_house in args.fund_house]
    args.output_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(args.output_dir / "automation.log", encoding="utf-8")])
    try:
        while True:
            failures = run_once(sources, args.output_dir, not args.no_browser, args.browser_channel)
            if not args.watch:
                return 1 if failures else 0
            LOG.info("Next check in %s seconds.", args.interval)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        LOG.info("Factsheet monitor stopped.")
        return 0


if __name__ == "__main__":
    try:
        from .monitor import main as monitor_main
    except (ImportError, ValueError):
        from monitor import main as monitor_main
    sys.exit(monitor_main())
