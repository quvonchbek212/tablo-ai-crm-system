import sqlite3
from contextlib import closing
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.dealership_seed import AS_OF, dataset_summary, seed_demo_database


class DealershipDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.path = seed_demo_database(Path(cls.temp.name) / "demo.db")
        cls.db = sqlite3.connect(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.db.close()
        cls.temp.cleanup()

    def test_dataset_size_and_foreign_keys(self):
        self.assertGreaterEqual(dataset_summary(self.path)["total_rows"], 50000)
        self.assertEqual(self.db.execute("PRAGMA integrity_check").fetchall(), [("ok",)])
        self.assertEqual(self.db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_financial_and_inventory_consistency(self):
        queries = [
            "SELECT s.id FROM sales s JOIN vehicles v ON v.id=s.vehicle_id WHERE s.status='completed' AND (v.status!='sold' OR v.branch_id!=s.branch_id OR s.sale_date<v.received_at OR s.cost_price!=v.cost_price)",
            "SELECT p.sale_id FROM payments p JOIN sales s ON s.id=p.sale_id GROUP BY s.id HAVING SUM(p.amount)>s.sale_price OR s.status!='completed'",
            "SELECT p.id FROM payments p JOIN sales s ON s.id=p.sale_id WHERE p.payment_date<s.sale_date OR p.payment_date>'2026-11-12'",
            "SELECT s.id FROM sales s JOIN employees e ON e.id=s.employee_id WHERE e.branch_id!=s.branch_id OR e.role!='sales'",
            "SELECT s.id FROM sales s JOIN customers c ON c.id=s.customer_id WHERE s.sale_date<c.created_at",
        ]
        for query in queries:
            with self.subTest(query=query):
                self.assertEqual(self.db.execute(query).fetchall(), [])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM sales WHERE status='completed'").fetchone()[0], 4800)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM leads WHERE status='won'").fetchone()[0], 4800)
        categories = dict(self.db.execute("SELECT category,COUNT(*) FROM vehicle_models GROUP BY category"))
        self.assertGreater(categories["passenger"], 10)
        self.assertGreater(categories["truck"], 10)

    def test_idempotence_preserves_existing_database(self):
        before = self.path.stat().st_mtime_ns
        seed_demo_database(self.path)
        self.assertEqual(before, self.path.stat().st_mtime_ns)

    def test_seed_is_reproducible(self):
        other_path = seed_demo_database(Path(self.temp.name) / "second.db")
        with closing(sqlite3.connect(other_path)) as other:
            self.assertEqual(list(self.db.iterdump()), list(other.iterdump()))


if __name__ == "__main__":
    unittest.main()
