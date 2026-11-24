"""Deterministic, fictional 1C:Enterprise + CRM analytics database.
Never touches live production systems. All financial, client and transaction data is synthetic.
"""
from __future__ import annotations

import calendar
from contextlib import closing
from datetime import date, timedelta
import os
from pathlib import Path
import random
import sqlite3
import tempfile
import threading

ENTERPRISE_DB_PATH = Path(__file__).resolve().parents[1] / "data" / "enterprise_1c.db"
# Keep DEMO_DB_PATH as alias pointing to enterprise_1c.db
DEMO_DB_PATH = ENTERPRISE_DB_PATH
AS_OF = date(2026, 11, 24)
START_DATE = date(2025, 1, 1)
SEED = 20261124
_LOCK = threading.Lock()

# Early copies of this fixture exposed IT products through car-sales compatibility views.
# Their row grain and units belong to the legacy UI, not to the enterprise catalog.
LEGACY_DEALERSHIP_VIEWS = frozenset({"sales", "vehicles", "vehicle_models", "leads", "customers", "employees"})


def is_enterprise_demo(engine) -> bool:
    return bool(engine is not None and engine.dialect.name == "sqlite" and engine.url.database
                and Path(engine.url.database).resolve() == ENTERPRISE_DB_PATH.resolve())

SCHEMA = """
PRAGMA foreign_keys=ON;

CREATE TABLE organizations (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    inn TEXT NOT NULL UNIQUE,
    address TEXT NOT NULL,
    phone TEXT NOT NULL
);

CREATE TABLE branches (
    id INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL REFERENCES organizations(id),
    name TEXT NOT NULL,
    code TEXT NOT NULL UNIQUE,
    region TEXT NOT NULL,
    city TEXT NOT NULL
);

CREATE TABLE departments (
    id INTEGER PRIMARY KEY,
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    name TEXT NOT NULL,
    code TEXT NOT NULL,
    head_name TEXT NOT NULL
);

CREATE TABLE managers (
    id INTEGER PRIMARY KEY,
    department_id INTEGER NOT NULL REFERENCES departments(id),
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    full_name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    phone TEXT NOT NULL,
    role TEXT NOT NULL,
    monthly_target_uzs INTEGER NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE product_categories (
    id INTEGER PRIMARY KEY,
    parent_id INTEGER REFERENCES product_categories(id),
    name TEXT NOT NULL,
    code TEXT NOT NULL UNIQUE,
    description TEXT
);

CREATE TABLE warehouses (
    id INTEGER PRIMARY KEY,
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    name TEXT NOT NULL,
    code TEXT NOT NULL UNIQUE,
    warehouse_type TEXT NOT NULL CHECK(warehouse_type IN ('central', 'retail', 'transit')),
    address TEXT NOT NULL
);

CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES product_categories(id),
    sku TEXT NOT NULL UNIQUE,
    code_1c TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    unit TEXT NOT NULL,
    cost_price INTEGER NOT NULL CHECK(cost_price > 0),
    wholesale_price INTEGER NOT NULL CHECK(wholesale_price >= cost_price),
    retail_price INTEGER NOT NULL CHECK(retail_price >= wholesale_price),
    vat_rate INTEGER NOT NULL DEFAULT 12,
    min_stock_level INTEGER NOT NULL DEFAULT 10,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE warehouse_stocks (
    id INTEGER PRIMARY KEY,
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK(quantity >= 0),
    reserved_quantity INTEGER NOT NULL DEFAULT 0 CHECK(reserved_quantity >= 0),
    last_inventory_date TEXT NOT NULL,
    UNIQUE(warehouse_id, product_id)
);

CREATE TABLE client_segments (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    discount_rate REAL NOT NULL DEFAULT 0.0,
    credit_days INTEGER NOT NULL DEFAULT 0,
    description TEXT
);

CREATE TABLE clients (
    id INTEGER PRIMARY KEY,
    segment_id INTEGER NOT NULL REFERENCES client_segments(id),
    manager_id INTEGER NOT NULL REFERENCES managers(id),
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    name TEXT NOT NULL,
    legal_name TEXT NOT NULL,
    inn TEXT NOT NULL,
    region TEXT NOT NULL,
    city TEXT NOT NULL,
    credit_limit_uzs INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active', 'prospect', 'blocked'))
);

CREATE TABLE contracts (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    organization_id INTEGER NOT NULL REFERENCES organizations(id),
    contract_number TEXT NOT NULL UNIQUE,
    contract_date TEXT NOT NULL,
    payment_term TEXT NOT NULL CHECK(payment_term IN ('prepayment_100', 'installment_30_70', 'credit_14_days', 'credit_30_days')),
    credit_limit_uzs INTEGER NOT NULL,
    currency TEXT NOT NULL DEFAULT 'UZS',
    status TEXT NOT NULL CHECK(status IN ('active', 'expired', 'terminated'))
);

CREATE TABLE crm_leads (
    id INTEGER PRIMARY KEY,
    client_id INTEGER REFERENCES clients(id),
    manager_id INTEGER NOT NULL REFERENCES managers(id),
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    source TEXT NOT NULL CHECK(source IN ('website', 'telegram', 'ads', 'exhibition', 'direct_call', 'recommendation')),
    status TEXT NOT NULL CHECK(status IN ('new', 'contacted', 'qualified', 'converted', 'lost')),
    created_at TEXT NOT NULL,
    loss_reason TEXT
);

CREATE TABLE crm_deals (
    id INTEGER PRIMARY KEY,
    lead_id INTEGER REFERENCES crm_leads(id),
    client_id INTEGER NOT NULL REFERENCES clients(id),
    manager_id INTEGER NOT NULL REFERENCES managers(id),
    contract_id INTEGER REFERENCES contracts(id),
    title TEXT NOT NULL,
    stage TEXT NOT NULL CHECK(stage IN ('negotiation', 'proposal_sent', 'contract_signing', 'payment_pending', 'won', 'lost')),
    expected_revenue INTEGER NOT NULL,
    probability_pct INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    closed_at TEXT
);

CREATE TABLE orders (
    id INTEGER PRIMARY KEY,
    deal_id INTEGER REFERENCES crm_deals(id),
    client_id INTEGER NOT NULL REFERENCES clients(id),
    contract_id INTEGER NOT NULL REFERENCES contracts(id),
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    manager_id INTEGER NOT NULL REFERENCES managers(id),
    order_number TEXT NOT NULL UNIQUE,
    order_date TEXT NOT NULL,
    total_amount INTEGER NOT NULL CHECK(total_amount >= 0),
    total_cost INTEGER NOT NULL CHECK(total_cost >= 0),
    status TEXT NOT NULL CHECK(status IN ('new', 'confirmed', 'paid', 'shipped', 'completed', 'cancelled'))
);

CREATE TABLE order_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    warehouse_id INTEGER NOT NULL REFERENCES warehouses(id),
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    unit_price INTEGER NOT NULL CHECK(unit_price > 0),
    cost_price INTEGER NOT NULL CHECK(cost_price > 0),
    discount_pct REAL NOT NULL DEFAULT 0.0,
    total_amount INTEGER NOT NULL CHECK(total_amount > 0)
);

CREATE TABLE invoices (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id),
    client_id INTEGER NOT NULL REFERENCES clients(id),
    contract_id INTEGER NOT NULL REFERENCES contracts(id),
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    invoice_number TEXT NOT NULL UNIQUE,
    invoice_date TEXT NOT NULL,
    due_date TEXT NOT NULL,
    total_amount INTEGER NOT NULL CHECK(total_amount > 0),
    total_cost INTEGER NOT NULL CHECK(total_cost > 0),
    vat_amount INTEGER NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('issued', 'signed', 'paid', 'partially_paid', 'overdue'))
);

CREATE TABLE invoice_items (
    id INTEGER PRIMARY KEY,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    quantity INTEGER NOT NULL CHECK(quantity > 0),
    unit_price INTEGER NOT NULL CHECK(unit_price > 0),
    cost_price INTEGER NOT NULL CHECK(cost_price > 0),
    total_amount INTEGER NOT NULL CHECK(total_amount > 0)
);

CREATE TABLE payments (
    id INTEGER PRIMARY KEY,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id),
    client_id INTEGER NOT NULL REFERENCES clients(id),
    contract_id INTEGER NOT NULL REFERENCES contracts(id),
    payment_number TEXT NOT NULL UNIQUE,
    payment_date TEXT NOT NULL,
    payment_method TEXT NOT NULL CHECK(payment_method IN ('bank_transfer', 'cash_desk', 'corporate_card')),
    amount INTEGER NOT NULL CHECK(amount > 0)
);

CREATE TABLE debt_settlements (
    id INTEGER PRIMARY KEY,
    client_id INTEGER NOT NULL REFERENCES clients(id),
    contract_id INTEGER NOT NULL REFERENCES contracts(id),
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    total_invoiced INTEGER NOT NULL,
    total_paid INTEGER NOT NULL,
    balance_receivable INTEGER NOT NULL,
    overdue_receivable INTEGER NOT NULL,
    overdue_days INTEGER NOT NULL,
    risk_level TEXT NOT NULL CHECK(risk_level IN ('normal', 'warning', 'critical')),
    last_reconciliation_date TEXT NOT NULL,
    UNIQUE(client_id, contract_id)
);

CREATE TABLE pnl_expenses (
    id INTEGER PRIMARY KEY,
    organization_id INTEGER NOT NULL REFERENCES organizations(id),
    branch_id INTEGER NOT NULL REFERENCES branches(id),
    department_id INTEGER NOT NULL REFERENCES departments(id),
    expense_date TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('payroll', 'marketing', 'rent_utilities', 'logistics', 'it_telecom', 'depreciation', 'other_opex')),
    description TEXT NOT NULL,
    amount INTEGER NOT NULL CHECK(amount > 0)
);

CREATE INDEX idx_orders_date ON orders(order_date);
CREATE INDEX idx_orders_branch ON orders(branch_id);
CREATE INDEX idx_orders_client ON orders(client_id);
CREATE INDEX idx_invoices_date ON invoices(invoice_date);
CREATE INDEX idx_invoices_client ON invoices(client_id);
CREATE INDEX idx_payments_date ON payments(payment_date);
CREATE INDEX idx_pnl_date ON pnl_expenses(expense_date);
"""

def seed_demo_database(path: Path | None = None, force: bool = False) -> Path:
    target = path or ENTERPRISE_DB_PATH
    with _LOCK:
        if target.exists() and not force:
            return target
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_file = tempfile.mkstemp(prefix="1c_enterprise_temp_", suffix=".db", dir=str(target.parent))
        os.close(handle)
        temp_path = Path(temp_file)
        try:
            with closing(sqlite3.connect(temp_path)) as con:
                con.execute("PRAGMA journal_mode=WAL")
                con.execute("PRAGMA synchronous=NORMAL")
                con.executescript(SCHEMA)
                _populate_data(con)
                con.commit()
            if target.exists():
                try:
                    target.unlink()
                except OSError:
                    pass
            temp_path.replace(target)
            return target
        finally:
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass

def ensure_demo_database() -> Path:
    if not ENTERPRISE_DB_PATH.exists():
        return seed_demo_database(ENTERPRISE_DB_PATH)
    return ENTERPRISE_DB_PATH

def _populate_data(con: sqlite3.Connection):
    rng = random.Random(SEED)
    
    # 1. Organizations
    con.execute("""
        INSERT INTO organizations (id, name, inn, address, phone) VALUES
        (1, 'DataTech Distribution MCHJ', '305112449', 'Toshkent sh., Mirobod t., Nukus ko''chasi, 29', '+998 71 200 44 00'),
        (2, 'ServerCloud Solutions QK', '304891234', 'Toshkent sh., Yunusobod t., Amir Temur shox ko''chasi, 107', '+998 71 202 11 22')
    """)

    # 2. Branches
    branches_data = [
        (1, 1, 'Toshkent Bosh Ofis', 'HQ-TAS', 'Toshkent shahri', 'Toshkent'),
        (2, 1, 'Samarqand IT Filiali', 'BR-SAM', 'Samarqand viloyati', 'Samarqand'),
        (3, 1, 'Farg''ona IT Filiali', 'BR-FER', 'Farg''ona viloyati', 'Farg''ona'),
        (4, 1, 'Buxoro IT Filiali', 'BR-BUX', 'Buxoro viloyati', 'Buxoro'),
        (5, 1, 'Navoiy Sanoat-IT Filiali', 'BR-NAV', 'Navoiy viloyati', 'Navoiy'),
        (6, 1, 'Andijon IT Filiali', 'BR-AND', 'Andijon viloyati', 'Andijon')
    ]
    con.executemany("INSERT INTO branches VALUES (?, ?, ?, ?, ?, ?)", branches_data)

    # 3. Departments
    dept_data = [
        (1, 1, 'Server va Data-Center Tizimlari Bo''limi', 'DEP-SRV', 'Akmal Rahimov'),
        (2, 1, 'Tarmoq va Telekommunikatsiya Bo''limi', 'DEP-NET', 'Sarvar Yusupov'),
        (3, 1, 'Kiberxavfsizlik va Dasturiy Ta''minot', 'DEP-SEC', 'Nilufar Qosimova'),
        (4, 1, 'Tender va Davlat Xaridlari Bo''limi', 'DEP-TND', 'Dilshod Normatov'),
        (5, 1, 'Moliya va Shartnomalar Nazorati', 'DEP-FIN', 'Malika Karimova'),
        (6, 2, 'Samarqand IT Savdo Bo''limi', 'DEP-SAM-S', 'Jasur Boboyev'),
        (7, 3, 'Farg''ona IT Savdo Bo''limi', 'DEP-FER-S', 'Otabek Mirzayev'),
        (8, 4, 'Buxoro IT Savdo Bo''limi', 'DEP-BUX-S', 'Rustam Saidov'),
        (9, 5, 'Navoiy IT Savdo Bo''limi', 'DEP-NAV-S', 'Nodir G''ofurov'),
        (10, 6, 'Andijon IT Savdo Bo''limi', 'DEP-AND-S', 'Bekzod Tursunov')
    ]
    con.executemany("INSERT INTO departments VALUES (?, ?, ?, ?, ?)", dept_data)

    # 4. Managers
    managers_data = [
        (1, 1, 1, 'Azizbek Karimov', 'a.karimov@datatech.uz', '+998901112233', 'Senior Enterprise IT Account Manager', 550000000, 1),
        (2, 2, 1, 'Madina Rustamova', 'm.rustamova@datatech.uz', '+998902223344', 'Network & CyberSecurity Sales Executive', 480000000, 1),
        (3, 1, 1, 'Jamshid Aliyev', 'j.aliyev@datatech.uz', '+998903334455', 'Data Center & Storage Systems Lead', 520000000, 1),
        (4, 4, 1, 'Zafar Xalilov', 'z.xalilov@datatech.uz', '+998904445566', 'Tender & Public Procurement Specialist', 610000000, 1),
        (5, 3, 1, 'Gulnoza Nazarova', 'g.nazarova@datatech.uz', '+998905556677', 'IT Solutions & Licensing Manager', 380000000, 1),
        (6, 6, 2, 'Farrux Toshev', 'f.toshev@datatech.uz', '+998906667788', 'Regional IT Sales Head (Samarqand)', 390000000, 1),
        (7, 7, 3, 'Ulug''bek Qodirov', 'u.qodirov@datatech.uz', '+998907778899', 'Regional IT Sales Head (Farg''ona)', 350000000, 1),
        (8, 8, 4, 'Sherzod Jo''rayev', 's.jurayev@datatech.uz', '+998908889900', 'Regional IT Sales Head (Buxoro)', 310000000, 1),
        (9, 9, 5, 'Ilhom Soliyev', 'i.soliyev@datatech.uz', '+998909990011', 'Regional IT Sales Head (Navoiy)', 420000000, 1),
        (10, 10, 6, 'Botir Eshmatov', 'b.eshmatov@datatech.uz', '+998901234567', 'Regional IT Sales Head (Andijon)', 280000000, 1)
    ]
    con.executemany("INSERT INTO managers VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", managers_data)

    # 5. Warehouses
    warehouses_data = [
        (1, 1, 'Toshkent Markaziy IT Ombori (Sergeli)', 'WH-TAS-01', 'central', 'Toshkent sh., Sergeli sanoat zonasi, 4'),
        (2, 1, 'Toshkent Tezkor Server Ombori (Mirobod)', 'WH-TAS-02', 'retail', 'Toshkent sh., Mirobod t., 18'),
        (3, 2, 'Samarqand IT Mintaqaviy Ombori', 'WH-SAM-01', 'central', 'Samarqand sh., Spitamen shox ko''chasi, 45'),
        (4, 3, 'Farg''ona IT Hududiy Ombori', 'WH-FER-01', 'central', 'Farg''ona sh., Marg''ilon yo''li, 12'),
        (5, 4, 'Buxoro IT Ombori', 'WH-BUX-01', 'retail', 'Buxoro sh., G''ijduvon ko''chasi, 88'),
        (6, 5, 'Navoiy Sanoat-IT Ombori', 'WH-NAV-01', 'retail', 'Navoiy sh., Janubiy sanoat zonasi, 9')
    ]
    con.executemany("INSERT INTO warehouses VALUES (?, ?, ?, ?, ?, ?)", warehouses_data)

    # 6. Product Categories (Faqat IT va Server Infratuzilmasi)
    categories = [
        (1, None, 'Server va Klaster Tizimlari', 'CAT-SRV', 'Enterprise rack va blade serverlar, hisoblash klasterlari'),
        (2, None, 'Tarmoq va Telekommunikatsiya', 'CAT-NET', 'L2/L3 boshqariladigan kommutatorlar, korporativ routerlar'),
        (3, None, 'Ma''lumotlarni Saqlash Tizimlari (SAN/NAS)', 'CAT-STO', 'Optik SAN disk massivlari, All-Flash saqlash tizimlari'),
        (4, None, 'Kiberxavfsizlik va Tarmoq Ekranlari', 'CAT-SEC', 'Next-Gen Firewall, apparat UTM xavfsizlik shlyuzlari'),
        (5, None, 'Uzluksiz Quvvat Manbalari va Server Shkaflari', 'CAT-PWR', 'On-line sanoat UPS tizimlari, 42U server shkaflari'),
        (6, None, 'Korporativ Noutbuklar va Ish Stansiyalari', 'CAT-CLI', 'B2B korporativ noutbuklar, muhandislik ish stansiyalari'),
        (7, None, 'Passiv Tarmoq va Optik Infratuzilma', 'CAT-CAB', 'Optik transiverlar, Cat6A tarmoq kabellari, patch panellar')
    ]
    con.executemany("INSERT INTO product_categories VALUES (?, ?, ?, ?, ?)", categories)

    # 7. Products (IT Nomenklatura)
    products_data = [
        (1, 1, 'SRV-DL380-G10', '1C-00101', 'HPE ProLiant DL380 Gen10 Server (2x Xeon Gold, 64GB RAM)', 'dona', 42000000, 52000000, 58000000, 12, 4, 1),
        (2, 1, 'SRV-R750-DELL', '1C-00102', 'Dell PowerEdge R750 Server (2x Xeon Silver 4314, 128GB)', 'dona', 48000000, 59000000, 66000000, 12, 3, 1),
        (3, 1, 'SRV-DL360-G10', '1C-00103', 'HPE ProLiant DL360 Gen10 1U Compact Rack Server', 'dona', 34000000, 41500000, 46000000, 12, 5, 1),
        (4, 2, 'NET-C9300-48P', '1C-00104', 'Cisco Catalyst 9300 48-Port PoE+ L3 Network Switch', 'dona', 26000000, 32000000, 36000000, 12, 8, 1),
        (5, 2, 'NET-C9200-24T', '1C-00105', 'Cisco Catalyst 9200L 24-Port Gigabit Managed Switch', 'dona', 12500000, 15800000, 17500000, 12, 10, 1),
        (6, 2, 'NET-HW-S5735', '1C-00106', 'Huawei CloudEngine S5735-L48T4X 48-Port GE Switch', 'dona', 9800000, 12400000, 14000000, 12, 12, 1),
        (7, 4, 'SEC-FORTI-100F', '1C-00107', 'Fortinet FortiGate 100F Next-Gen Firewall (NGFW)', 'dona', 31000000, 38500000, 43000000, 12, 4, 1),
        (8, 3, 'STO-MSA2062-HP', '1C-00108', 'HPE MSA 2062 16Gb FC Dual Controller SAN Storage (15TB)', 'dona', 75000000, 94000000, 105000000, 12, 2, 1),
        (9, 3, 'STO-SYN-RS3621', '1C-00109', 'Synology RackStation RS3621RPxs 12-Bay Enterprise NAS', 'dona', 28000000, 35000000, 39000000, 12, 5, 1),
        (10, 5, 'PWR-APC-10KVA', '1C-00110', 'APC Smart-UPS RT 10000VA 230V Sanoat On-Line UPS', 'dona', 38000000, 47000000, 53000000, 12, 3, 1),
        (11, 5, 'PWR-APC-3KVA', '1C-00111', 'APC Smart-UPS SRT 3000VA Rack/Tower 230V', 'dona', 14000000, 17600000, 19800000, 12, 8, 1),
        (12, 5, 'PWR-RACK-42U', '1C-00112', 'Toten 42U 600x1000 Server Shkafi Perforatsiyali', 'dona', 4200000, 5600000, 6400000, 12, 15, 1),
        (13, 6, 'CLI-LEN-T14', '1C-00113', 'Lenovo ThinkPad T14 Gen 4 Core i7 16GB 512GB SSD', 'dona', 11500000, 14200000, 15900000, 12, 25, 1),
        (14, 6, 'CLI-HP-ZBOOK', '1C-00114', 'HP ZBook Power G10 Mobil Ish Stansiyasi (i7/32GB/RTX)', 'dona', 19000000, 23800000, 26500000, 12, 10, 1),
        (15, 7, 'CAB-SFP-10G', '1C-00115', 'Cisco 10GBASE-SR SFP+ Optik Transiver Modul', 'dona', 650000, 850000, 980000, 12, 60, 1),
        (16, 7, 'CAB-CAT6A-305', '1C-00116', 'Molex Cat.6A U/UTP 4-Juftlik 500MHz Tarmoq Kabeli 305m', 'buxta', 1450000, 1900000, 2200000, 12, 40, 1)
    ]
    con.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", products_data)

    # 8. Warehouse Stocks
    stocks = []
    stock_id = 1
    for w in warehouses_data:
        w_id = w[0]
        for p in products_data:
            p_id = p[0]
            qty = rng.randint(15, 120) if p[5] == 'dona' else rng.randint(40, 250)
            res = int(qty * rng.uniform(0.05, 0.25))
            stocks.append((stock_id, w_id, p_id, qty, res, '2026-11-20'))
            stock_id += 1
    con.executemany("INSERT INTO warehouse_stocks VALUES (?, ?, ?, ?, ?, ?)", stocks)

    # 9. Client Segments
    segments_data = [
        (1, 'BANK_FINTECH', 'Banklar va FinTech Tizimlari', 0.08, 30, 'Markaziy serverlar va kiberxavfsizlik xaridorlari'),
        (2, 'TELECOM_DATA', 'Telekom va Data-Markazlar', 0.12, 45, 'Yirik tarmoq va optik infratuzilma xaridorlari'),
        (3, 'ENTERPRISE_IND', 'Yirik Sanoat va Holdinglar', 0.05, 20, 'Korporativ ofislar va sanoat avtomatizatsiyasi'),
        (4, 'GOV_TENDER', 'Davlat Tashkilotlari va Tenderlar', 0.07, 45, 'Yagona portal va davlat xaridlari buyurtmachilari')
    ]
    con.executemany("INSERT INTO client_segments VALUES (?, ?, ?, ?, ?, ?)", segments_data)

    # 10. Clients (B2B IT Kontragentlar)
    clients_raw = [
        ('ATB Hamkorbank Bosh Binosi', 'ATB Hamkorbank', '200845112', 'Toshkent shahri', 'Toshkent', 1, 1, 1, 1500000000),
        ('Kapitalbank ATB IT Departamenti', 'Kapitalbank ATB', '201994331', 'Toshkent shahri', 'Toshkent', 1, 2, 1, 1800000000),
        ('O''zsanoatqurilishbank ATB (SQB)', 'SQB ATB', '200112889', 'Toshkent shahri', 'Toshkent', 1, 3, 1, 2200000000),
        ('O''zbektelecom AK Bosh Data-markazi', 'Uzbektelecom AK', '201045890', 'Toshkent shahri', 'Toshkent', 2, 1, 1, 3500000000),
        ('COSCOM MCHJ (Ucell) Texnik Majmuasi', 'COSCOM MCHJ', '300994112', 'Toshkent shahri', 'Toshkent', 2, 2, 1, 2400000000),
        ('Unitel MCHJ (Beeline Uzbekistan)', 'Unitel MCHJ', '300112445', 'Toshkent shahri', 'Toshkent', 2, 3, 1, 2100000000),
        ('Uzum Technologies MCHJ Data Hub', 'Uzum Market MCHJ', '308991223', 'Toshkent shahri', 'Toshkent', 1, 1, 1, 1600000000),
        ('UZINFOCOM Yagona Integratori', 'UZINFOCOM MCHJ', '202334991', 'Toshkent shahri', 'Toshkent', 4, 4, 1, 2800000000),
        ('Soliq Servis Davlat Unitar Korxonasi', 'Soliq Servis DUK', '203112774', 'Toshkent shahri', 'Toshkent', 4, 4, 1, 1900000000),
        ('Navoiy Kon-Metallurgiya Kombinati IT Boshqarmasi', 'NKMK AJ', '200445667', 'Navoiy viloyati', 'Navoiy', 3, 9, 5, 4500000000),
        ('Olmaliq KMK Raqamlashtirish Departamenti', 'OKMK AJ', '200223554', 'Toshkent viloyati', 'Olmaliq', 3, 1, 1, 3200000000),
        ('Artel Digital Texnopark R&D Markazi', 'Artel Electronics MCHJ', '301984223', 'Toshkent shahri', 'Toshkent', 3, 3, 1, 1100000000),
        ('Samarqand Davlat Universiteti Raqamli Ta''lim Markazi', 'SamDU', '201665443', 'Samarqand viloyati', 'Samarqand', 4, 6, 2, 800000000),
        ('Farg''ona Neftni Qayta Ishlash Zavodi IT Bo''limi', 'FNQIZ MCHJ', '200998112', 'Farg''ona viloyati', 'Farg''ona', 3, 7, 3, 1400000000),
        ('Payme (Inspired MCHJ) Infratuzilma Bo''limi', 'Inspired MCHJ', '302554778', 'Toshkent shahri', 'Toshkent', 1, 5, 1, 1200000000)
    ]
    clients_data = []
    for i, c in enumerate(clients_raw, 1):
        clients_data.append((
            i, c[5], c[6], c[7], c[0], c[1], c[2], c[3], c[4], c[8], '2025-01-10', 'active'
        ))
    con.executemany("INSERT INTO clients VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", clients_data)

    # 11. Contracts
    contracts_data = []
    terms = ['prepayment_100', 'installment_30_70', 'credit_14_days', 'credit_30_days']
    for cl in clients_data:
        c_id = cl[0]
        term = terms[(c_id - 1) % len(terms)]
        c_num = f"SHRT-2026/{c_id:04d}"
        contracts_data.append((
            c_id, c_id, 1, c_num, '2025-01-15', term, cl[9], 'UZS', 'active'
        ))
    con.executemany("INSERT INTO contracts VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", contracts_data)

    # 12. CRM Leads & 13. CRM Deals
    leads = []
    deals = []
    lead_sources = ['website', 'telegram', 'ads', 'exhibition', 'direct_call', 'recommendation']
    deal_stages = ['negotiation', 'proposal_sent', 'contract_signing', 'payment_pending', 'won']
    
    lead_id = 1
    deal_id = 1
    curr_date = START_DATE
    while curr_date <= AS_OF:
        if curr_date.weekday() in (0, 2, 4):
            for _ in range(rng.randint(2, 5)):
                cl = rng.choice(clients_data)
                mgr_id = cl[2]
                br_id = cl[3]
                src = rng.choice(lead_sources)
                status = rng.choices(['converted', 'won', 'lost', 'contacted'], weights=[60, 20, 10, 10])[0]
                status_str = 'converted' if status in ('converted', 'won') else status
                leads.append((lead_id, cl[0], mgr_id, br_id, src, status_str, curr_date.isoformat(), None if status_str != 'lost' else 'Narx qimmatligi'))
                
                if status_str == 'converted':
                    it_deal_topics = [
                        "Data Center Server Klasterini Kengaytirish (HPE/Dell)",
                        "Cisco Catalyst Korporativ Tarmoq Infratuzilmasi",
                        "HPE MSA Optik SAN Ma'lumotlar Ombori Massivi",
                        "Kiberxavfsizlik va FortiGate NGFW Shlyuzi",
                        "Server Xonasi uchun APC On-Line UPS Quvvat Tizimi",
                        "ThinkPad T14 & ZBook Ish Stansiyalari Parkini Yangilash",
                        "Optik Magistral va SFP+ 10G Infratuzilma Tenderi"
                    ]
                    deals.append((
                        deal_id, lead_id, cl[0], mgr_id, cl[0],
                        f"{rng.choice(it_deal_topics)} #{deal_id}",
                        'won' if curr_date < AS_OF - timedelta(days=15) else rng.choice(deal_stages),
                        rng.randint(60, 480) * 1000000,
                        95 if curr_date < AS_OF - timedelta(days=15) else rng.randint(60, 90),
                        curr_date.isoformat(),
                        (curr_date + timedelta(days=7)).isoformat()
                    ))
                    deal_id += 1
                lead_id += 1
        curr_date += timedelta(days=1)

    con.executemany("INSERT INTO crm_leads VALUES (?, ?, ?, ?, ?, ?, ?, ?)", leads)
    con.executemany("INSERT INTO crm_deals VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", deals)

    # 14. Orders, 15. Order Items, 16. Invoices, 17. Invoice Items, 18. Payments
    orders = []
    order_items = []
    invoices = []
    invoice_items = []
    payments = []
    
    order_id = 1
    item_id = 1
    invoice_id = 1
    inv_item_id = 1
    payment_id = 1

    curr_date = START_DATE
    while curr_date <= AS_OF:
        order_count = rng.randint(4, 9)
        for _ in range(order_count):
            client = rng.choice(clients_data)
            contract_id = client[0]
            branch_id = client[3]
            manager_id = client[2]
            order_num = f"ORD-{curr_date.strftime('%Y%m')}-{order_id:05d}"
            
            chosen_products = rng.sample(products_data, rng.randint(1, 3))
            o_total_amount = 0
            o_total_cost = 0
            
            current_order_items = []
            for prod in chosen_products:
                qty = rng.randint(1, 6) if prod[5] == 'dona' else rng.randint(50, 300)
                price = prod[7]
                cost = prod[6]
                disc = 0.05 if client[1] == 1 else 0.0
                line_amount = int(qty * price * (1.0 - disc))
                line_cost = int(qty * cost)
                
                o_total_amount += line_amount
                o_total_cost += line_cost
                
                current_order_items.append((
                    item_id, order_id, prod[0], branch_id, qty, price, cost, disc, line_amount
                ))
                item_id += 1
                
            orders.append((
                order_id, None, client[0], contract_id, branch_id, manager_id,
                order_num, curr_date.isoformat(), o_total_amount, o_total_cost, 'completed'
            ))
            order_items.extend(current_order_items)
            
            inv_num = f"ESF-{curr_date.strftime('%Y%m')}-{invoice_id:05d}"
            due_date = curr_date + timedelta(days=30)
            vat = int(o_total_amount * 0.12 / 1.12)
            
            invoices.append((
                invoice_id, order_id, client[0], contract_id, branch_id,
                inv_num, curr_date.isoformat(), due_date.isoformat(),
                o_total_amount, o_total_cost, vat, 'paid' if curr_date < AS_OF - timedelta(days=20) else 'partially_paid'
            ))
            
            for oi in current_order_items:
                invoice_items.append((
                    inv_item_id, invoice_id, oi[2], oi[4], oi[5], oi[6], oi[8]
                ))
                inv_item_id += 1
                
            pay_ratio = 1.0 if curr_date < AS_OF - timedelta(days=20) else rng.choice([0.7, 0.85, 1.0])
            pay_amount = int(o_total_amount * pay_ratio)
            pay_num = f"PP-{curr_date.strftime('%Y%m')}-{payment_id:05d}"
            pay_date = curr_date + timedelta(days=rng.randint(1, 10))
            if pay_date > AS_OF:
                pay_date = AS_OF
                
            payments.append((
                payment_id, invoice_id, client[0], contract_id,
                pay_num, pay_date.isoformat(),
                rng.choice(['bank_transfer', 'bank_transfer', 'corporate_card']),
                pay_amount
            ))
            
            order_id += 1
            invoice_id += 1
            payment_id += 1
            
        curr_date += timedelta(days=1)

    con.executemany("INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", orders)
    con.executemany("INSERT INTO order_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", order_items)
    con.executemany("INSERT INTO invoices VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", invoices)
    con.executemany("INSERT INTO invoice_items VALUES (?, ?, ?, ?, ?, ?, ?)", invoice_items)
    con.executemany("INSERT INTO payments VALUES (?, ?, ?, ?, ?, ?, ?, ?)", payments)

    # 19. Debt Settlements (Debitorlik hisoboti)
    debt_rows = []
    settle_id = 1
    for cl in clients_data:
        c_id = cl[0]
        cur_inv = con.execute("SELECT COALESCE(SUM(total_amount), 0) FROM invoices WHERE client_id=?", (c_id,)).fetchone()[0]
        cur_pay = con.execute("SELECT COALESCE(SUM(amount), 0) FROM payments WHERE client_id=?", (c_id,)).fetchone()[0]
        balance = max(0, cur_inv - cur_pay)
        overdue = int(balance * 0.35) if balance > 0 else 0
        overdue_days = rng.randint(5, 45) if overdue > 0 else 0
        risk = 'critical' if overdue_days > 30 else ('warning' if overdue_days > 14 else 'normal')
        debt_rows.append((
            settle_id, c_id, c_id, cl[3], cur_inv, cur_pay, balance, overdue, overdue_days, risk, AS_OF.isoformat()
        ))
        settle_id += 1
    con.executemany("INSERT INTO debt_settlements VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", debt_rows)

    # 20. PnL Expenses
    pnl_entries = []
    pnl_id = 1
    categories = ['payroll', 'marketing', 'rent_utilities', 'logistics', 'it_telecom', 'other_opex']
    curr_date = START_DATE
    while curr_date <= AS_OF:
        if curr_date.day in (5, 15, 25):
            for b in branches_data:
                b_id = b[0]
                cat = rng.choice(categories)
                amt = rng.randint(8, 65) * 1000000
                pnl_entries.append((
                    pnl_id, 1, b_id, b_id, curr_date.isoformat(), cat, f"{cat.title()} xarajatlari ({b[2]})", amt
                ))
                pnl_id += 1
        curr_date += timedelta(days=1)
    con.executemany("INSERT INTO pnl_expenses VALUES (?, ?, ?, ?, ?, ?, ?, ?)", pnl_entries)

def dataset_summary(con: sqlite3.Connection) -> dict:
    tables = [
        "organizations", "branches", "departments", "managers", "warehouses",
        "product_categories", "products", "warehouse_stocks", "client_segments",
        "clients", "contracts", "crm_leads", "crm_deals", "orders",
        "order_items", "invoices", "invoice_items", "payments",
        "debt_settlements", "pnl_expenses"
    ]
    counts = {}
    total_rows = 0
    for t in tables:
        count = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        counts[t] = count
        total_rows += count
    return {
        "tables": counts,
        "total_tables": len(tables),
        "total_rows": total_rows
    }
