import sqlite3

conn = sqlite3.connect('backend/data/enterprise_1c.db')
c = conn.cursor()

c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in c.fetchall()]
print("Tables in enterprise_1c.db:")
for t in tables:
    c.execute(f"PRAGMA table_info({t})")
    cols = [f"{r[1]} ({r[2]})" for r in c.fetchall()]
    print(f"\n[{t}] (rows count: {conn.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]}):")
    print("  " + ", ".join(cols))
