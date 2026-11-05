"""Bridge module allowing invocation via ingestion.factsheetDataIngestion."""
import sys
from factsheetDataIngestion.cli import main

if __name__ == '__main__':
    sys.exit(main())
