import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.database import DatabaseManager


class QueryDeadlineTests(unittest.TestCase):
    def test_sqlite_deadline_interrupts_work_and_clears_handler_for_next_query(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "numbers.db"
            with closing(sqlite3.connect(path)) as connection:
                connection.execute("CREATE TABLE numbers (value INTEGER)")
                connection.executemany("INSERT INTO numbers VALUES (?)", [(n,) for n in range(10000)])
                connection.commit()
            manager = DatabaseManager()
            manager.set_connection(f"sqlite:///{path.as_posix()}")
            try:
                with patch("app.database.SQLITE_QUERY_TIMEOUT_SECONDS", 0):
                    with self.assertRaises(Exception) as raised:
                        manager.execute_query("SELECT SUM(value) AS total FROM numbers")
                self.assertIn("interrupted", str(raised.exception).lower())
                self.assertEqual(manager.execute_query("SELECT SUM(value) AS total FROM numbers"), [{"total": 49995000}])
            finally:
                manager._engine.dispose()
