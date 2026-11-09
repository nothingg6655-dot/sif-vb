"""One scheduled NAV check, with durable logs even when launched by pythonw."""
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone, timedelta
import os
from pathlib import Path
import sys
import traceback
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    logs = ROOT / 'logs'
    logs.mkdir(exist_ok=True)
    today = datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime('%Y-%m-%d')
    with (logs / f'nav-sync-{today}.log').open('a', encoding='utf-8', buffering=1) as output:
        with redirect_stdout(output), redirect_stderr(output):
            try:
                from ingestion.tigzig.worker import run_sync_job
                run_sync_job('scheduled-two-hourly', 'scheduled', recover_abandoned=True)
                engine = ROOT / 'backend/dist/utils/calculate-scheme-performance.js'
                node = shutil.which('node')
                if node is None or not engine.is_file():
                    raise RuntimeError('Build the backend (npm run build) before running scheduled performance calculations.')
                # Recheck derived metrics even after an unchanged source check.
                # Conditional UPSERTs make identical metrics a zero-write operation
                # and allow recovery after a prior calculation failure.
                subprocess.run([node, str(engine)], cwd=ROOT / 'backend', check=True,
                               stdout=output, stderr=output,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
                return 0
            except Exception:
                traceback.print_exc()
                return 1


if __name__ == '__main__':
    raise SystemExit(main())
