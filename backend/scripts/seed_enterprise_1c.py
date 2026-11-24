"""Generate and seed the new 1C:Enterprise + CRM corporate database."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.demo_seed import ENTERPRISE_DB_PATH, dataset_summary, seed_demo_database

def main():
    print("Generating 1C:Enterprise + CRM relational database...")
    db_path = seed_demo_database(force=True)
    print(f"Database successfully generated at: {db_path}")
    
    with sqlite3.connect(db_path) as con:
        summary = dataset_summary(con)
        print("\n=== DATASET SUMMARY ===")
        print(f"Total Tables: {summary['total_tables']}")
        print(f"Total Rows: {summary['total_rows']:,}")
        print("\nTable Breakdown:")
        for tbl, cnt in summary["tables"].items():
            print(f"  - {tbl:20}: {cnt:,} rows")

if __name__ == "__main__":
    main()
