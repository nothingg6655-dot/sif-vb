# Official SIF factsheet monitor

`monitor.py` checks official publishing sources for all **34 registered schemes across 17 fund houses**, downloads available factsheets, and calls the AMC-specific extractor in `app.py`. Independent directories and mirrored PDFs are excluded.

The installed Windows task, **SIF Factsheet Monitor**, runs hourly and at sign-in using `pythonw.exe`. The computer must be awake, online, and the user logged in. Polling detects a publication on the next successful check; it is not an AMC publication webhook.

## Outputs

- `output/latest/`: newest verified primary PDFs, named by fund house. Separate scheme PDFs may have different latest reporting months. Older copies are replaced as new editions are selected.
- `output/latest/extraction/<scheme name>.json`: one validated extraction per scheme, for example `DynaSIF Equity Long-Short Fund.json`. Each file contains that scheme's data under its name. Every scheme uses the same ordered top-level fields and the same manager and portfolio-row structure; fields that are not published use empty values of the documented type.
- `output/latest/manifest.json`: current PDF and extraction filenames, checksums, reporting months, covered schemes, and official source URLs.
- `output/latest/coverage.json`: every registered scheme, extraction status, reporting month, portfolio scope, warnings, official URL, and freshness verification.
- `output/monitor_state.json` and `output/monitor.log`: checks and discovery, download, and extraction errors.

There is no archive. Downloads are checked in temporary storage, which is removed after processing. Only selected current PDFs and validated scheme extractions are retained. Alternate plan evidence may remain in the scheme JSON without a separate local PDF copy.

JSON/state timestamps are UTC. Filenames use the registered **fund house** name, rather than the SIF brand. The Magnum source points to SBI, correcting the duplicated Kotak link supplied initially. quant, Union/Arthaya, and Mirae/Platinum are included.

## Run

```powershell
python -m pip install -r requirements.txt
python monitor.py --once
python monitor.py --watch --interval 3600
```

Optionally run `python package_latest.py` to package the current PDFs and the scheme extraction folder into `output/Latest Factsheets.zip`. Selection uses reporting dates inside the PDFs, so a cover labelled October may correctly be selected for September data. An older reporting month is retained if it is the newest retrievable official factsheet for that scheme.

Windows uses installed Microsoft Edge for public pages requiring JavaScript. The browser uses a fresh context without an account or personal browser profile. Bot challenges and access denials are recorded, not bypassed. Use `--no-browser` for HTTP/API discovery only.

Re-extract previously downloaded official PDFs without checking live sources:

```powershell
python monitor.py --offline --no-browser
```

Offline results explicitly have no current listing verification. Use a separate `--output-dir` to keep offline results separate from production. Exit code 0 means at least one scheme is available; it does **not** mean all 34 have downloadable factsheets. Inspect `output/latest/coverage.json` for completeness.

Install or update the hourly task:

```powershell
.\install_schedule.ps1
.\install_schedule.ps1 -IntervalMinutes 30
```

The installer updates this project's existing task and refuses to replace an unrelated task with the same name. To stop scheduled checks:

```powershell
Disable-ScheduledTask -TaskName 'SIF Factsheet Monitor'
```

For a single local PDF:

```powershell
python app.py "C:\path\to\factsheet.pdf" --output extracted_factsheet.json
```

## Discovery and freshness

`official_sources.json` is the production catalog. It contains publishing pages, public document APIs, allowed official storage prefixes, and verified document URLs. The monitor checks dated links and replacements at fixed URLs. Separate scheme PDFs are selected independently, so a September hybrid PDF does not hide an August equity PDF.

ICICI, Tata, Kotak, Franklin, Invesco, SBI, and Platinum use their public website document APIs. Platinum's Forms & Downloads catalog is checked page by page because its fund page still exposes an old factsheet. Prism's browser discovery checks up to twelve month filters, crossing fiscal years when needed; it does not stop after an empty current or previous month. Other houses use official page links, with browser discovery where applicable. Reporting dates come from the fund's financial/portfolio page, not its cover or upload month: quant's October publication reports September; Platinum's September publication reports August.

Known official URLs remain retrievable when a publishing page is blocked. Such data has **Latest Listing Verified = false** and is not represented as a confirmed latest edition. Blocked pages are retried hourly. An invalid new PDF is discarded while the last validated scheme data and PDF remain in `latest`. Hashes detect publisher revisions; scheme extraction files are reused only when their checksums, source PDF hashes, and extractor version match. Redirects outside configured official prefixes, non-PDF responses, oversized responses, and mismatched fund-house identities are rejected.

## Extraction scope and validation

Verified layouts cover Dyna, Altiva Hybrid, Altiva Equity Ex-Top 100, Arudha, Diviniti, Sapphire, RedHex, iSIF, Infinity, Magnum Hybrid, Titanium, WSIF, Summit, quant, Arthaya, Platinum, and Prism. Prism's asset holdings and futures exposures are extracted separately, with subtotal and grand-total reconciliation. Its August PDF contains covered obsolete AUM text; extraction uses the visible final correction and retains the earlier text in the evidence. Layout changes or newly available unsupported schemes are flagged for adapter work; registration alone does not enable a verified extraction.

The snapshot on **16 October 2026** contains **28 extracted schemes**, including Altiva Equity Ex-Top 100's September data, Prism's August factsheet, and Platinum's August reporting data. Six remain pending: three Apex schemes, Arthaya Equity Long-Short, Altiva Equity Long-Short, and Magnum Equity Ex-Top 100. Apex rejected requests, Edelweiss's factsheet API denied access, Arthaya currently fails portfolio-subtotal validation, and SBI's retrievable PDF contains only Magnum Hybrid. These are availability/access findings, not claims that the schemes do not exist. Inspect `coverage.json` for the latest run's results.

Each scheme includes AUM, published NAV and expense ratios where present, managers, holdings, source-page evidence, and validation checks. Cash holdings, derivatives, published net exposures, ratings, and quantities remain separate. Signed values and values below displayed precision are preserved. Holdings are reconciled to published subtotals with display-rounding tolerance.

Some AMCs publish only top positions or asset-class aggregates. The JSON states `Portfolio Scope`; it does not imply a complete security-level portfolio. quant's current PDF links out to NAV/expense data rather than printing numeric values. Unmapped charts, performance, and other sections remain in `Published Pages`, with page numbers and links; empty normalized fields are not zeros. `Field Availability` identifies fields not normalized. No OCR-only chart values or missing numeric fields are invented.

Run verification:

```powershell
python -m unittest test_app test_automation test_monitor test_extended test_prism test_altiva_equity
```

Official fixture checks cover NAV/AUM, signed derivatives, wrapped names, sector-heading exclusion, top-position limitations, and subtotal failures. Discovery checks cover independent scheme editions, spaces in URLs, official host restrictions, redirect rejection, and retention after outages.

The installed task uses `monitor.py` and `official_sources.json`. Running `automation.py` also uses the current monitor; its earlier helper functions remain available to the compatibility tests.
