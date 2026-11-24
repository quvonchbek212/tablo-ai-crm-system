import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
from typing import Literal
import time
from sqlalchemy.engine import URL
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.database import db_manager
from app.models import (
    ConnectionRequest, SchemaContext, DashboardResponse,
    NaturalQueryRequest, NaturalQueryResponse,
    DataQualityAuditRequest, DataQualityAuditResponse,
    GoldEvaluationRequest, GoldEvaluationResponse,
    SemanticCatalog,
)
from app.schema_profiler import SchemaProfiler
from app.database_understanding import build_understanding
from app.llm_service import llm_service
from app.dashboard_generator import DashboardGenerator
from app.agent_analyst import AIDataAnalystAgent
from app.data_quality import DataQualityAuditor
from app.relationship_guard import RelationshipGuard
from app.gold_evaluator import GoldQueryEvaluator
from app.semantic_catalog import SemanticCatalogManager, SemanticCatalogError
from app.demo_seed import ensure_demo_database, is_enterprise_demo
from app.enterprise_questions import QUESTIONS, answer_enterprise_question, equipment_question
from app.platform_api import router as platform_router
from app.voice import router as voice_router
from app.external_workspace import ConnectionIdentity, iterative_agent_enabled

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("ai_analytics")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Connect after application startup, never as a module-import side effect."""
    if settings.WORKSPACE_MODE == "demo":
        path = await asyncio.to_thread(ensure_demo_database)
        await asyncio.to_thread(db_manager.set_connection, f"sqlite:///{path.as_posix()}")
    else:
        await asyncio.to_thread(db_manager.connect_from_settings)
    yield

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="TabloAI: connected database chat, analytics, dashboards and reports powered by OpenAI",
    lifespan=lifespan,
)
app.include_router(platform_router)
app.include_router(voice_router)

# Enable CORS for React frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def root():
    return {
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "online"
    }

@app.get("/enterprise")
def get_enterprise():
    return FileResponse(Path(__file__).resolve().parents[2] / "enterprise_dashboard.html")

@app.get("/api/health")
async def get_health():
    """Report dependencies honestly; healthy means both DB and configured model are ready."""
    try:
        db_info = db_manager.test_connection()
    except Exception as e:
        db_info = {"status": "error", "error": str(e)}

    vllm_info = await llm_service.check_health()
    overall_status = (
        "healthy"
        if db_info.get("status") == "connected" and vllm_info.get("model_ready")
        else "degraded"
    )

    return {
        "status": overall_status,
        "database": db_info,
        "vllm": vllm_info,
        "ai": vllm_info,
        "workspace_mode": settings.WORKSPACE_MODE,
    }

@app.post("/api/database/connect")
def connect_database(req: ConnectionRequest):
    """Dynamically connect to any PostgreSQL database (direct or via SSH tunnel) or SQLite."""
    try:
        if req.use_ssh or req.ssh_host:
            # Connect through SSH Tunnel
            db_manager.connect_with_ssh(
                ssh_host=req.ssh_host,
                ssh_port=req.ssh_port or 22,
                ssh_user=req.ssh_user or "root",
                ssh_pkey_path=req.ssh_pkey_path,
                ssh_pkey_passphrase=req.ssh_pkey_passphrase,
                remote_host=req.host or "127.0.0.1",
                remote_port=req.port or 5432,
                db_user=req.user or "postgres",
                db_password=req.password or "",
                db_name=req.dbname or "postgres",
                include_schemas=req.include_schemas
            )
        elif req.connection_url:
            url = req.connection_url
            db_manager.set_connection(url, include_schemas=req.include_schemas)
        elif req.db_type == "sqlite":
            url = f"sqlite:///./{req.dbname or 'analytics'}.db"
            db_manager.set_connection(url, include_schemas=req.include_schemas)
        else:
            # Direct PostgreSQL connection string
            url = URL.create(
                drivername="postgresql+psycopg2",
                username=req.user,
                password=req.password or None,
                host=req.host,
                port=req.port,
                database=req.dbname,
            ).render_as_string(hide_password=False)
            db_manager.set_connection(url, include_schemas=req.include_schemas)

        # Clear previous schema and dashboard caches completely.
        SchemaProfiler.clear_cache(delete_file=False)
        DashboardGenerator.clear_cache()
        RelationshipGuard.clear_cache()

        # Freshly profile the newly connected database. Connection credentials
        # remain in memory and are never rewritten into .env by this endpoint.
        new_schema = SchemaProfiler.profile(force_refresh=True)

        return {
            "success": True,
            "message": (
                "Baza ulandi, lekin schema profili to'liq emas. Ogohlantirishlarni tekshiring."
                if not new_schema.profile_complete
                else (
                    "SSH Tunnel orqali bazaga muvaffaqiyatli ulandi."
                    if (req.use_ssh or req.ssh_host)
                    else "Ma'lumotlar bazasiga muvaffaqiyatli ulandi."
                )
            ),
            "dialect": db_manager.dialect_name,
            "database_name": db_manager.current_db_name,
            "connection_fingerprint": new_schema.connection_fingerprint,
            "understanding": build_understanding(new_schema),
            "total_tables": new_schema.total_tables,
            "profile_complete": new_schema.profile_complete,
            "profile_warnings": new_schema.profile_warnings,
            "profiled_columns": new_schema.profiled_column_count,
            "declared_relationships": new_schema.declared_relationship_count,
            "include_schemas": db_manager.include_schemas,
            "ssh_tunnel": db_manager.is_ssh_active
        }
    except Exception as e:
        logger.error(f"Failed to connect: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ulanishda xatolik: {str(e)}"
        )

@app.get("/api/database/understanding")
def get_database_understanding():
    """Describe the profiled source without returning sample row values or credentials."""
    try:
        return build_understanding(SchemaProfiler.profile())
    except Exception as exc:
        logger.error("Database understanding failed: %s", type(exc).__name__)
        raise HTTPException(400, "Baza tuzilishini o'rganib bo'lmadi. Ulanishni tekshiring.") from exc


@app.get("/api/database/schema", response_model=SchemaContext)
def get_database_schema(force_refresh: bool = False):
    """Extract and return schema metadata, columns, and sample values."""
    try:
        return SchemaProfiler.profile(force_refresh=force_refresh)
    except Exception as e:
        logger.error(f"Schema profiling failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Could not inspect schema: {str(e)}"
        )


@app.get("/api/semantic-catalog", response_model=SemanticCatalog)
def get_semantic_catalog():
    """Return the reviewed catalog or a non-certified draft for this schema."""
    try:
        schema_ctx = SchemaProfiler.profile()
        catalog = SemanticCatalogManager.load_for_context(schema_ctx)
        return catalog or SemanticCatalogManager.build_draft(schema_ctx)
    except Exception as exc:
        logger.error("Semantic catalog load failed: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


@app.put("/api/semantic-catalog", response_model=SemanticCatalog)
def update_semantic_catalog(catalog: SemanticCatalog):
    """Persist local semantic metadata; never changes the connected database."""
    try:
        schema_ctx = SchemaProfiler.profile()
        saved = SemanticCatalogManager.save_for_context(catalog, schema_ctx)
        SchemaProfiler.apply_runtime_metadata(schema_ctx)
        DashboardGenerator.clear_cache()
        RelationshipGuard.clear_cache()
        return saved
    except SemanticCatalogError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("Semantic catalog update failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/data-quality/audit", response_model=DataQualityAuditResponse)
async def audit_data_quality(req: DataQualityAuditRequest):
    """Run bounded aggregate-only quality checks; never writes to the source database."""
    try:
        schema_ctx = await asyncio.to_thread(SchemaProfiler.profile)
        return await asyncio.to_thread(DataQualityAuditor.audit, schema_ctx, req)
    except Exception as exc:
        logger.error("Data-quality audit failed: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/evaluation/gold", response_model=GoldEvaluationResponse)
async def evaluate_gold_queries(req: GoldEvaluationRequest):
    """Compare chat answers with reviewed reference SELECT queries in read-only mode."""
    try:
        return await _evaluate_chat(req, ConnectionIdentity.capture())
    except Exception as exc:
        logger.error("Gold evaluation failed: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/evaluation/gold/default", response_model=GoldEvaluationResponse)
async def evaluate_default_gold_queries():
    """Run the repository's reviewed regression questions against the live read-only source."""
    try:
        identity = ConnectionIdentity.capture()
        filename = "enterprise_sales_cases.json" if is_enterprise_demo(identity.engine) else "gold_cases.json"
        path = Path(__file__).resolve().parents[1] / filename
        with open(path, "r", encoding="utf-8") as file:
            request = GoldEvaluationRequest(**json.load(file))
        return await _evaluate_chat(request, identity)
    except Exception as exc:
        logger.error("Default gold evaluation failed: %s", exc)
        raise HTTPException(status_code=400, detail=str(exc))


async def _evaluate_chat(request, identity):
    schema = await asyncio.to_thread(SchemaProfiler.profile)
    identity.assert_current()

    async def answer(question):
        return await natural_language_query(NaturalQueryRequest(prompt=question))

    return await GoldQueryEvaluator.evaluate(schema, request, answer=answer, identity=identity)

@app.get("/api/dashboard/live-stats")
async def get_live_dashboard_stats(
    period: Literal["month", "quarter", "year"] = "quarter",
    dept: Literal["all", "servers", "network", "infrastructure"] = "all",
    currency: Literal["USD", "UZS"] = "USD"
):
    """
    Returns live aggregated 1C ERP metrics, timeline trends, B2B debt,
    warehouse inventory, category breakdown, P&L, and top corporate contracts.
    """
    try:
        from app.live_dashboard_service import LiveDashboardService
        return await asyncio.to_thread(LiveDashboardService.get_dashboard_data, period, dept, currency)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        logger.error(f"Error fetching live dashboard stats: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="1C paneli uchun zarur ma'lumotlarni yuklab bo'lmadi. Faol manbani tekshiring."
        )

@app.post("/api/dashboard/generate", response_model=DashboardResponse)
async def generate_dashboard(force_refresh: bool = False):
    """Generates charts through the configured model and executes validated queries."""
    try:
        return await DashboardGenerator.generate_dashboard(force_refresh=force_refresh)
    except Exception as e:
        logger.error(f"Dashboard generation error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate dashboard: {str(e)}"
        )

@app.get("/api/query/presets")
def get_query_presets():
    questions = [preset.question for preset in QUESTIONS] if is_enterprise_demo(db_manager._engine) else []
    return {"questions": questions}


@app.post("/api/query/chat", response_model=NaturalQueryResponse)
async def natural_language_query(req: NaturalQueryRequest):
    """
    Autonomous AI Data Analyst Agent:
    Discovers candidate tables dynamically across any connected database,
    validates generated SQL and synthesizes only executed query results.
    """
    start_time = time.time()
    identity = ConnectionIdentity.capture()
    resolved = AIDataAnalystAgent.resolve_analytical_prompt(req.prompt, req.history)
    intent = AIDataAnalystAgent.classify_user_intent(resolved)
    if intent == "GREETING":
        return AIDataAnalystAgent._greeting_response(start_time)
    if intent == "OUT_OF_DOMAIN":
        return AIDataAnalystAgent._out_of_domain_response(start_time)
    try:
        schema_ctx = await asyncio.to_thread(SchemaProfiler.profile)
        identity.assert_current()
    except Exception as exc:
        logger.warning("Cannot profile chat source: %s", type(exc).__name__)
        return AIDataAnalystAgent._failure_response(start_time,
            "Faol baza tuzilishini o'qib bo'lmadi. Ulanishni tekshirib, savolni qayta yuboring.",
            "database_unavailable")

    if not schema_ctx.tables:
        return NaturalQueryResponse(
            sql_query="",
            chart=None,
            charts=[],
            explanation="Ma'lumotlar bazasi ulanmagan yoki profillangan jadvallar mavjud emas.",
            data_count=0,
            returned_count=0,
            execution_time_ms=(time.time() - start_time) * 1000,
            success=False,
            error="database_unavailable",
            response_type="text",
        )

    if not schema_ctx.profile_complete:
        return NaturalQueryResponse(
            sql_query="",
            chart=None,
            charts=[],
            explanation=(
                "Baza schema profili to'liq olinmaganligi sababli taxminiy SQL bajarilmadi. "
                "Schema profilini yangilang va profil ogohlantirishlarini bartaraf eting."
            ),
            data_count=0,
            returned_count=0,
            execution_time_ms=(time.time() - start_time) * 1000,
            success=False,
            error="schema_profile_incomplete",
            response_type="text",
            validation_warnings=schema_ctx.profile_warnings,
        )

    try:
        preset = next((p for p in QUESTIONS if p.question == req.prompt), None)
        if preset is None and is_enterprise_demo(identity.engine):
            preset = equipment_question(resolved)
        unavailable = SemanticCatalogManager.unavailable_metric_reason(req.prompt, schema_ctx)
        if unavailable and intent == "DATA_QUERY":
            result = AIDataAnalystAgent._failure_response(start_time, unavailable, "metric_unavailable")
        elif preset and is_enterprise_demo(identity.engine):
            result = await asyncio.to_thread(answer_enterprise_question, preset, db_manager, identity)
        elif intent == "CONVERSATIONAL":
            result = await AIDataAnalystAgent.execute_conversational_turn(req.prompt, schema_ctx, req.history)
        elif iterative_agent_enabled():
            from app.data_agent import execute_data_agent
            result = await execute_data_agent(req.prompt, schema_ctx, req.history, identity.assert_current)
        else:
            result = await AIDataAnalystAgent.execute_analyst_agent(req.prompt, schema_ctx, req.history)
        identity.assert_current()
        return result
    except Exception as e:
        logger.exception("AI Data Analyst Agent failed")
        exec_time = (time.time() - start_time) * 1000
        return NaturalQueryResponse(
            sql_query="",
            chart=None,
            charts=[],
            explanation=(
                "So'rovni ishonchli bajarib bo'lmadi. Bu holat ma'lumot yo'qligini anglatmaydi; "
                "model va ma'lumotlar bazasi ulanishini tekshiring."
            ),
            data_count=0,
            returned_count=0,
            execution_time_ms=exec_time,
            success=False,
            error="internal_query_error",
            response_type="text",
        )

