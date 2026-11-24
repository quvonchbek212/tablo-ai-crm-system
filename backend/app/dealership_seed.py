"""Synthetic source for the separately selectable Atlas Auto workspace."""
from bisect import bisect_right
from contextlib import closing
from datetime import date, timedelta
import os
from pathlib import Path
import random
import sqlite3
import tempfile
import threading


DEMO_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "dealership.db"
START = date(2025, 1, 1)
AS_OF = date(2026, 11, 12)
_lock = threading.Lock()

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE branches(id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT NOT NULL);
CREATE TABLE employees(id INTEGER PRIMARY KEY, name TEXT NOT NULL, branch_id INTEGER REFERENCES branches, role TEXT NOT NULL);
CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT NOT NULL, customer_type TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE vehicle_models(id INTEGER PRIMARY KEY, brand TEXT NOT NULL, model TEXT NOT NULL, category TEXT NOT NULL, fuel_type TEXT NOT NULL);
CREATE TABLE vehicles(id INTEGER PRIMARY KEY, vin TEXT UNIQUE NOT NULL, model_id INTEGER REFERENCES vehicle_models,
    branch_id INTEGER REFERENCES branches, received_at TEXT NOT NULL, cost_price INTEGER, list_price INTEGER, status TEXT NOT NULL);
CREATE TABLE leads(id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers, employee_id INTEGER REFERENCES employees,
    branch_id INTEGER REFERENCES branches, model_id INTEGER REFERENCES vehicle_models, created_at TEXT, status TEXT);
CREATE TABLE sales(id INTEGER PRIMARY KEY, vehicle_id INTEGER UNIQUE REFERENCES vehicles, lead_id INTEGER UNIQUE REFERENCES leads,
    customer_id INTEGER REFERENCES customers, employee_id INTEGER REFERENCES employees, branch_id INTEGER REFERENCES branches,
    sale_date TEXT, cost_price INTEGER, sale_price INTEGER, status TEXT);
CREATE TABLE payments(id INTEGER PRIMARY KEY, sale_id INTEGER REFERENCES sales, payment_date TEXT, amount INTEGER, method TEXT);
CREATE INDEX payments_sale ON payments(sale_id);
CREATE INDEX sales_date ON sales(sale_date);
CREATE TABLE expenses(id INTEGER PRIMARY KEY, branch_id INTEGER REFERENCES branches, expense_date TEXT, amount INTEGER);
CREATE TABLE service_orders(id INTEGER PRIMARY KEY, vehicle_id INTEGER REFERENCES vehicles, service_date TEXT, amount INTEGER);
CREATE TABLE monthly_targets(branch_id INTEGER REFERENCES branches, month TEXT, target_units INTEGER, PRIMARY KEY(branch_id, month));
CREATE TABLE dataset_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _populate(db):
    rng = random.Random(20261112)
    cities = ["Toshkent", "Samarqand", "Buxoro", "Andijon", "Farg'ona", "Namangan", "Navoiy", "Qarshi"]
    db.executemany("INSERT INTO branches VALUES (?,?,?)", [
        (i, f"{city} filiali", city) for i, city in enumerate(cities, 1)])
    db.executemany("INSERT INTO employees VALUES (?,?,?,?)", [
        (i, f"Xodim {i:03}", (i - 1) // 12 + 1, "sales" if (i - 1) % 12 < 9 else "service")
        for i in range(1, 97)])
    passenger = {"Chevrolet": "Cobalt Onix Malibu Tracker Damas Lacetti Spark Captiva",
                 "Toyota": "Corolla Camry Prado RAV4", "BYD": "Chazor Song Yuan Han",
                 "Kia": "K5 Sportage Sorento Seltos"}
    trucks = {"Isuzu": "NPR NQR FVR FTR Forward", "MAN": "TGL TGM TGS TGX CLA",
              "Volvo": "FH FM FMX FE FL", "HOWO": "A7 T7H TX NX MAX"}
    models = []
    for catalog, category in [(passenger, "passenger"), (trucks, "truck")]:
        for brand, names in catalog.items():
            fuel = "diesel" if category == "truck" else ("hybrid" if brand == "BYD" else "petrol")
            models.extend((brand, model, category, fuel) for model in names.split())
    db.executemany("INSERT INTO vehicle_models VALUES (?,?,?,?,?)",
                   [(i, *model) for i, model in enumerate(models, 1)])

    days = (AS_OF - START).days
    customer_days = [rng.randrange(days + 1) for _ in range(6000)]
    db.executemany("INSERT INTO customers VALUES (?,?,?,?,?)", [
        (i, f"Mijoz {i:04}", rng.choice(cities), rng.choice(["individual", "business"]),
         (START + timedelta(days=day)).isoformat()) for i, day in enumerate(customer_days, 1)])
    customers_by_age = sorted(range(1, 6001), key=lambda i: customer_days[i - 1])
    # Each sale uses an already registered customer and a salesperson in the vehicle's branch.
    registration_days = sorted(customer_days)
    for i in range(1, 20001):
        branch = rng.randint(1, 8)
        employee = (branch - 1) * 12 + rng.randint(1, 9)
        model = rng.randint(1, len(models))
        day = rng.randint(max(1, registration_days[0]), days)
        event = START + timedelta(days=day)
        customer = customers_by_age[rng.randrange(bisect_right(registration_days, day))]
        won = i <= 4800
        db.execute("INSERT INTO leads VALUES (?,?,?,?,?,?,?)",
                   (i, customer, employee, branch, model, event.isoformat(),
                    "won" if won else rng.choice(["new", "contacted", "lost"])))
        if i > 8000:
            continue
        cost = rng.randint(100, 390) * (2000000 if model > 20 else 1000000)
        price = cost + rng.randint(5, 45) * 1000000
        arrived = START + timedelta(days=rng.randint(0, day))
        db.execute("INSERT INTO vehicles VALUES (?,?,?,?,?,?,?,?)",
                   (i, f"ATLAS{i:012}", model, branch, arrived.isoformat(), cost, price,
                    "sold" if won else rng.choice(["available", "reserved"])))
        if i > 5000:
            continue
        db.execute("INSERT INTO sales VALUES (?,?,?,?,?,?,?,?,?,?)",
                   (i, i, i, customer, employee, branch, event.isoformat(), cost, price, "completed" if won else "cancelled"))
        if won:
            installments = 3 + i % 2
            paid = price * rng.choice([7, 8, 10, 10]) // 10
            for part in range(installments):
                payment_day = min(AS_OF, event + timedelta(days=part * 14))
                amount = paid // installments + (paid % installments if part == installments - 1 else 0)
                db.execute("INSERT INTO payments(sale_id,payment_date,amount,method) VALUES (?,?,?,?)",
                           (i, payment_day.isoformat(), amount, rng.choice(["cash", "card", "bank_transfer"])))
    for i in range(1, 921):
        db.execute("INSERT INTO expenses VALUES (?,?,?,?)",
                   (i, rng.randint(1, 8), (START + timedelta(days=rng.randint(0, days))).isoformat(), rng.randint(1, 50) * 1000000))
    for i in range(1, 3001):
        vehicle_id = rng.randint(1, 4800)
        sold_at = date.fromisoformat(db.execute("SELECT sale_date FROM sales WHERE id=?", (vehicle_id,)).fetchone()[0])
        service_date = sold_at + timedelta(days=rng.randint(0, (AS_OF - sold_at).days))
        db.execute("INSERT INTO service_orders VALUES (?,?,?,?)", (i, vehicle_id, service_date.isoformat(), rng.randint(1, 30) * 100000))
    for month in range(23):
        label = f"{2025 + month // 12}-{1 + month % 12:02}"
        db.executemany("INSERT INTO monthly_targets VALUES (?,?,?)",
                       [(branch, label, rng.randint(20, 50)) for branch in range(1, 9)])
    metadata = {"company": "Atlas Auto Group", "synthetic": "true", "currency": "UZS",
                "start_date": START.isoformat(), "as_of": AS_OF.isoformat(), "seed": "20261112",
                "country": "UZ", "units": "vehicles", "revenue_status": "completed",
                "inventory_snapshot": AS_OF.isoformat(), "schema_version": "1"}
    db.executemany("INSERT INTO dataset_metadata VALUES (?,?)", metadata.items())


def seed_demo_database(path=DEMO_DB_PATH):
    target = Path(path)
    with _lock:
        if target.exists():
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=target.parent, suffix=".db")
        os.close(fd)
        try:
            with closing(sqlite3.connect(temp)) as db:
                db.executescript(SCHEMA)
                _populate(db)
                db.commit()
            # Publish the finished database without overwriting another worker's file.
            try:
                os.link(temp, target)
            except FileExistsError:
                pass
        finally:
            Path(temp).unlink(missing_ok=True)
    return target


def ensure_demo_database():
    return seed_demo_database(DEMO_DB_PATH)


def dataset_summary(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        tables = [name for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        counts = {name: db.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0] for name in tables}
    return {"tables": counts, "total_tables": len(counts), "total_rows": sum(counts.values())}
