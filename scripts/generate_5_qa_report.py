"""Print the five example analyses against the synthetic 1C enterprise database."""
from contextlib import closing
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.enterprise_questions import QUESTIONS


DATABASE = Path(__file__).resolve().parents[1] / "backend" / "data" / "enterprise_1c.db"


def print_report(*, compact=False):
    with closing(sqlite3.connect(DATABASE.as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        for number, preset in enumerate(QUESTIONS, 1):
            print(f"\nSavol {number}. {preset.question}")
            rows = connection.execute(preset.sql).fetchall()
            if not rows:
                print("Mos yozuv topilmadi.")
            for position, row in enumerate(rows, 1):
                fields = []
                for label in row.keys():
                    value = row[label]
                    if isinstance(value, (float, int)):
                        value = f"{value:,}"
                    fields.append(f"{label}: {value if value is not None else 'aniqlanmagan'}")
                separator = " | " if compact else "\n   "
                print(f"{position}. " + separator.join(fields))


if __name__ == "__main__":
    print_report()
