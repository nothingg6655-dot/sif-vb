"""Run worker/database tests and save a clean, portable verification log."""
from io import StringIO
import unittest

from ingestion.tigzig.config import PROJECT_ROOT


def main():
    suite = unittest.defaultTestLoader.discover(
        str(PROJECT_ROOT / 'ingestion' / 'tests'), top_level_dir=str(PROJECT_ROOT)
    )
    stream = StringIO()
    result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    report = stream.getvalue()
    destination = PROJECT_ROOT / 'docs' / 'reports' / 'python-tests.txt'
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(report, encoding='utf-8')
    print(report, end='')
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
