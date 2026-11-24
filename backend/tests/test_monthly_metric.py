import asyncio
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest

from app.agent_analyst import AIDataAnalystAgent
from app.database import DatabaseManager
from app.models import SchemaContext, SemanticMetricDefinition, TableColumnMeta, TableMeta, TableRelationship
from app.monthly_metric import compile_monthly_metric


@pytest.fixture
def customer_schema(monkeypatch):
    # Distinct customers can share a name. Equal totals must keep separate IDs.
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "billing.db"
        with closing(sqlite3.connect(path)) as db:
            db.executescript("""
                CREATE TABLE firms(code INTEGER PRIMARY KEY, title TEXT NOT NULL);
                CREATE TABLE ledger(id INTEGER PRIMARY KEY, firm INTEGER REFERENCES firms(code),
                                    posted DATE NOT NULL, quantity INTEGER NOT NULL);
                INSERT INTO firms VALUES (1,'Z'),(2,'A'),(3,'B'),(4,'C'),(5,'A'),(6,'No entries');
                INSERT INTO ledger VALUES (1,1,'2024-02-01',15),(2,2,'2024-02-29',15),
                    (3,3,'2024-02-10',20),(4,4,'2024-02-01',10),(5,4,'2024-03-01',500),
                    (6,5,'2024-02-10',2);
            """)
        manager = DatabaseManager()
        manager.set_connection("sqlite:///" + path.as_posix())
        monkeypatch.setattr("app.semantic_catalog.db_manager", manager)
        monkeypatch.setattr("app.agent_analyst.db_manager", manager)
        cols = lambda pairs: [TableColumnMeta(name=name, data_type=kind) for name, kind in pairs]
        facts = TableMeta(name="ledger", certified_source=True, business_role="fact",
                          default_time_column="posted", columns=cols([("id","INT"), ("firm","INT"),
                                                                     ("posted","DATE"), ("quantity","INT")]))
        dim = TableMeta(name="firms", primary_key=["code"], certified_source=True, business_role="dimension",
                        default_label_column="title", semantic_aliases=["korxona"],
                        columns=cols([("code","INT"), ("title","TEXT")]))
        fk = TableRelationship(from_table="ledger", from_column="firm", from_columns=["firm"],
                               to_table="firms", to_column="code", to_columns=["code"],
                               source="foreign_key", verified=True, cardinality="many-to-one",
                               join_hint="ledger.firm = firms.code")
        metric = SemanticMetricDefinition(id="delivered.qty", name="Yetkazilgan miqdor", table="ledger",
                                          aggregation="sum", column="quantity", certified=True)
        ctx = SchemaContext(database_type="sqlite", schema_prompt="", total_tables=2,
                            semantic_catalog_reviewed=True, tables=[facts,dim],
                            semantic_metrics=[metric], relationships=[fk])
        try:
            yield ctx, metric
        finally:
            manager._engine.dispose()


def test_monthly_metric_normal_agent_entry_does_not_call_model(customer_schema):
    ctx, _ = customer_schema
    async def answer(question):
        with patch("app.agent_analyst.plan_question", side_effect=AssertionError("Unexpected model call")):
            r = await AIDataAnalystAgent.execute_analyst_agent(question, ctx)
        assert r.success, r.explanation
        return r
    total = asyncio.run(answer("2024-yil fevral oyida yetkazilgan miqdor qancha?"))
    assert total.rows == [{"delivered_qty": 62}]
    top = asyncio.run(answer("2024-yil fevralda yetkazilgan miqdor bo'yicha eng yuqori uchta korxona qaysi?"))
    assert [list(row.values()) for row in top.rows] == [["B",20], ["Z",15], ["A",15]]
    assert "mos yozuvi bor" in top.explanation
    assert len(top.charts) == 1
    leaders = asyncio.run(answer("2024-yil fevralda yetkazilgan miqdor bo'yicha qaysi ikkita korxona yetakchi?"))
    assert [list(row.values()) for row in leaders.rows] == [["B",20], ["Z",15]]
    bottom = asyncio.run(answer("2024-yil fevralda yetkazilgan miqdor bo'yicha eng kichik 2 ta korxona "
                                "qaysi? Faqat korxona nomi va summasini kamayish tartibida ko'rsat."))
    assert [list(row.values()) for row in bottom.rows] == [["C",10], ["A",2]]
    assert len(bottom.charts) == 1


def test_monthly_metric_requires_complete_recognized_scope(customer_schema):
    ctx, metric = customer_schema
    base = "2024-yil fevralda yetkazilgan miqdor bo'yicha eng katta ikkita korxona qaysi"
    for extra in ("Samarqandda", "AQSH dollarida", "qiymati kamida 50", "va yanvarda", "shu haftada",
                  "nega oshdi", "faqat to'langan", "regionlar bo'yicha", "foizini ko'rsat",
                  "barcha korxonalar", "yozuvi bo'lmasa ham nol bilan"):
        assert compile_monthly_metric(base + " " + extra, ctx, metric) is None, extra
    assert compile_monthly_metric(base, ctx.model_copy(update={"semantic_catalog_reviewed": False}), metric) is None
    assert compile_monthly_metric(base, ctx.model_copy(update={"relationships": ctx.relationships * 2}), metric) is None
    ctx.relationships[0].verified = False
    assert compile_monthly_metric(base, ctx, metric) is None
