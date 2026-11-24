from contextlib import closing
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import live_dashboard_service as live
from app.database import DatabaseManager
from app.demo_seed import ensure_demo_database
from app.main import app
from app.schema_profiler import SchemaProfiler
from app.semantic_catalog import SemanticCatalogManager


def test_enterprise_dashboard_reconciles_filtered_actuals_and_currency():
    path = ensure_demo_database()
    manager = DatabaseManager()
    manager.set_connection("sqlite:///" + path.as_posix())
    try:
        with patch.object(live, "db_manager", manager), closing(sqlite3.connect(path)) as db:
            expected = db.execute("""
                SELECT SUM(oi.total_amount), COUNT(DISTINCT o.id), SUM(oi.quantity * oi.cost_price)
                FROM orders o JOIN order_items oi ON oi.order_id=o.id JOIN products p ON p.id=oi.product_id
                WHERE o.order_date >= '2026-10-01' AND o.order_date <= '2026-11-24'
                    AND o.status <> 'cancelled' AND p.category_id IN (1,3)
            """).fetchone()
            uzs = live.LiveDashboardService.get_dashboard_data("quarter", "servers", "UZS")
            usd = live.LiveDashboardService.get_dashboard_data("quarter", "servers", "USD")
            assert (uzs["kpis"]["rev"], uzs["kpis"]["orders"]) == expected[:2]
            assert uzs["kpis"]["gross_profit"] == expected[0] - expected[2]
            assert usd["kpis"]["rev"] == round(expected[0] / usd["rate"], 2)
            assert sum(r["amount"] for r in uzs["segments"]) == expected[0]
            assert sum(v or 0 for v in uzs["trend"]["revenue"]["actual"]) == expected[0]
            assert uzs["trend"]["revenue"]["actual"][-1] is None
            assert uzs["trend"]["revenue"]["forecast"][-1] > 0
            assert uzs["trend"]["revenue"]["forecast"][0] is None
    finally:
        manager._engine.dispose()


def test_enterprise_html_served_from_backend_working_directory(monkeypatch):
    monkeypatch.chdir(Path(__file__).resolve().parents[1])
    client = TestClient(app)
    response = client.get("/enterprise")
    assert response.status_code == 200
    assert 'id="chatInput"' in response.text
    assert client.get("/api/dashboard/live-stats?period=unsupported").status_code == 422


def test_equipment_comparison_ranks_all_completed_hardware_before_selecting_leaders():
    from app.enterprise_questions import equipment_question

    question = "Qaysi IT uskunalar eng ko‘p daromad va yuqori marja bergan?"
    analysis = equipment_question(question)
    with closing(sqlite3.connect(":memory:")) as db:
        db.executescript("""
            CREATE TABLE products(id INTEGER, name TEXT, category_id INTEGER);
            CREATE TABLE product_categories(id INTEGER, code TEXT);
            CREATE TABLE orders(id INTEGER, status TEXT);
            CREATE TABLE order_items(product_id INTEGER, order_id INTEGER, quantity INTEGER, total_amount INTEGER, cost_price INTEGER);
            INSERT INTO product_categories VALUES (1,'CAT-STO'),(2,'SERVICE');
            INSERT INTO orders VALUES (1,'completed'),(2,'cancelled');
            INSERT INTO products VALUES (1,'Volume',1),(2,'Balanced',1),(3,'Margin',1),(4,'Lower',1),(5,'Service',2);
            INSERT INTO order_items VALUES (1,1,2,200,90),(2,1,1,100,40),(2,1,1,50,50),
                (3,1,1,50,10),(4,1,1,90,81),(4,2,1,500,1),(5,1,1,1000,1);
        """)
        assert db.execute(analysis.sql).fetchall() == [
            ("Volume", 200, 20, 10.0, 1, 3), ("Balanced", 150, 60, 40.0, 2, 2),
            ("Margin", 50, 40, 80.0, 4, 1)
        ]
    assert equipment_question(question.replace("Qaysi", "Oktabrda qaysi")) is None
    assert equipment_question(question.replace("uskunalar", "uskuna turlari")) is None


def test_demo_metric_definitions_reconcile_and_are_bound_to_the_database(monkeypatch):
    path = ensure_demo_database()
    manager = DatabaseManager()
    manager.set_connection("sqlite:///" + path.as_posix())
    monkeypatch.setattr("app.schema_profiler.db_manager", manager)
    monkeypatch.setattr("app.semantic_catalog.db_manager", manager)
    # A path below a regular file cannot contain a user catalog.
    monkeypatch.setattr("app.semantic_catalog.CATALOG_DIRECTORY", str(path / "catalogs"))
    monkeypatch.setattr(SchemaProfiler, "_cached_context", None)
    monkeypatch.setattr(SchemaProfiler, "_cached_time", 0)
    monkeypatch.setattr(SchemaProfiler, "save_to_file", lambda ctx: True)
    try:
        schema = SchemaProfiler.profile(force_refresh=True)
        assert schema.semantic_catalog_reviewed
        balance_question = "Mijozlardan olinadigan jami qoldiq qancha?"
        valid, _ = SemanticCatalogManager.validate_metric_sql(
            balance_question, "SELECT SUM(total_amount) FROM invoices", schema)
        assert not valid
        valid, reason = SemanticCatalogManager.validate_metric_sql(
            balance_question, "SELECT SUM(balance_receivable) FROM debt_settlements", schema)
        assert valid, reason
        with closing(sqlite3.connect(path)) as db:
            for question, reference in [
                ("Jami pul tushumi", "SELECT SUM(amount) FROM payments"),
                ("Yakunlangan buyurtmalar summasi", "SELECT SUM(total_amount) FROM orders WHERE status='completed'"),
            ]:
                metric, sql = SemanticCatalogManager.compile_matching_metric_sql(question, schema)
                assert manager.execute_query(sql)[0][metric.id] == db.execute(reference).fetchone()[0]
        assert not SemanticCatalogManager.validate_metric_sql(
            "Oktabrda yakunlangan buyurtmalar summasi", "SELECT SUM(total_amount) FROM orders", schema)[0]
        questions = [
            ("Oktabrda mijozlarga qancha pul qaytarilgan?", "qaytarim"),
            ("Mijozlarga qaytarilgan to‘lovlarning jami summasi qancha?", "qaytarim"),
            ("Barcha soliqlar va foiz xarajatlaridan keyin qolgan sof foyda qancha?", "foyda solig‘i"),
        ]
        with (patch.object(SchemaProfiler, "profile", return_value=schema),
              patch("app.main.iterative_agent_enabled") as dispatch,
              patch("app.llm_service.llm_service.generate_completion") as model,
              patch("app.main.db_manager.execute_query") as execute):
            client = TestClient(app)
            for question, missing in questions:
                result = client.post("/api/query/chat", json={"prompt": question}).json()
                assert result["error"] == "metric_unavailable" and missing in result["explanation"]
                assert result["rows"] == [] and result["charts"] == [] and not result["sql_query"]
            dispatch.assert_not_called()
            model.assert_not_called()
            execute.assert_not_called()
        for supported in ["QQS summasi qancha?", "Dashboarddagi sof foyda qancha?", "Yalpi foyda qancha?",
                          "Soliqdan keyingi sof foyda emas, yalpi foyda qancha?"]:
            assert SemanticCatalogManager.unavailable_metric_reason(supported, schema) is None
        unreviewed = schema.model_copy(update={"semantic_catalog_reviewed": False})
        assert SemanticCatalogManager.unavailable_metric_reason(questions[0][0], unreviewed) is None
        assert SemanticCatalogManager.load_for_context(
            schema.model_copy(update={"schema_fingerprint": "different-schema"})) is None
        assert SemanticCatalogManager.load_for_context(
            schema.model_copy(update={"database_name": str(path.parent / "external" / path.name)})) is None
        manager.set_connection("sqlite:///:memory:")
        assert SemanticCatalogManager.load_for_context(schema) is None
    finally:
        manager._engine.dispose()
