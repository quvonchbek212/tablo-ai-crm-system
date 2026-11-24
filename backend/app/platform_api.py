"""Atlas Auto demo workspace routes; legacy universal connectors remain independent."""
import asyncio
import logging
from dataclasses import dataclass
from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from app import platform_service as service
from app.platform_ai import chat, analyze_agent_run
from app.llm_service import LLMServiceError, llm_service
from app.assistant_commands import command

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/platform", tags=["Atlas Auto workspace"])


class FilterBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: str = "2026-01-01"
    end_date: str = "2026-11-12"
    branch_id: int | None = Field(default=None, ge=1)
    category: Literal["passenger", "truck"] | None = None

    def filters(self):
        return service.Filters(self.start_date, self.end_date, self.branch_id, self.category)


class ReportBody(FilterBody):
    title: str | None = Field(default=None, min_length=1, max_length=180)


class AgentRunBody(FilterBody):
    use_ai: bool = True


class HistoryMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=8000)


class ChatBody(FilterBody):
    prompt: str = Field(min_length=1, max_length=4000)
    history: list[HistoryMessage] = Field(default_factory=list, max_length=12)


@dataclass(frozen=True)
class ExternalFilters:
    start_date: str | None = None
    end_date: str | None = None
    branch_id: int | None = None
    category: str | None = None

    def __post_init__(self):
        for value in (self.start_date, self.end_date):
            if value is not None:
                try:
                    if date.fromisoformat(value).isoformat() != value:
                        raise ValueError()
                except ValueError as exc:
                    raise ValueError("Sana YYYY-MM-DD formatida bo'lishi kerak.") from exc
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValueError("Boshlanish sanasi tugash sanasidan keyin bo'lishi mumkin emas.")
        if self.branch_id is not None or self.category is not None:
            raise ValueError("Tashqi baza filtrlarini savol matnida ko'rsating.")


class CommandBody(ChatBody):
    source: Literal["demo", "external"] = "demo"
    expected_connection_fingerprint: str | None = Field(default=None, min_length=1, max_length=128)
    start_date: str | None = None
    end_date: str | None = None

    def filters(self):
        if self.source == "external":
            return ExternalFilters(self.start_date, self.end_date, self.branch_id, self.category)
        return service.Filters(self.start_date or "2026-01-01", self.end_date or "2026-11-12", self.branch_id, self.category)


def guarded(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0])) from exc
    except Exception as exc:
        logger.error("Platform operation failed: %s", type(exc).__name__)
        raise HTTPException(500, "Ma'lumotni qayta ishlashda xatolik. Backend jurnalini tekshiring.") from exc


@router.get("/overview")
def get_overview(start_date: str = "2026-01-01", end_date: str = "2026-11-12", branch_id: int | None = Query(None, ge=1), category: Literal["passenger", "truck"] | None = None):
    return guarded(lambda: service.overview(service.Filters(start_date, end_date, branch_id, category)))


@router.get("/inventory")
def get_inventory(search: str = Query("", max_length=100), status: Literal["available", "reserved", "sold"] | None = None, category: Literal["passenger", "truck"] | None = None, branch_id: int | None = Query(None, ge=1), page: int = Query(1, ge=1, le=100000), page_size: int = Query(20, ge=1, le=100)):
    return guarded(service.inventory, search, status, category, branch_id, page, page_size)


@router.get("/sources")
def get_sources():
    return guarded(service.sources)


@router.get("/agents")
def get_agents():
    return guarded(service.agents)


@router.post("/agents/{agent_id}/run")
async def execute_agent(agent_id: str, body: AgentRunBody | None = None):
    body = body or AgentRunBody()
    run = await asyncio.to_thread(guarded, lambda: service.run_agent(agent_id, body.filters()))
    if body.use_ai and llm_service.provider == "openai" and llm_service.api_key.strip():
        try:
            analysis = await analyze_agent_run(run)
            changes = {"mode": "openai", "analysis": analysis["summary"],
                       "recommendations": analysis["recommendations"], "analysis_error": None}
        except Exception:
            changes = {"mode": "rule_based", "analysis_error": "Hisob-kitob yakunlandi, ammo OpenAI izohi olinmadi. Qayta urinib ko'ring."}
    else:
        changes = {"mode": "rule_based", "analysis_error": None}
    return await asyncio.to_thread(guarded, service.update_artifact, run["id"], "agent", changes)


@router.get("/reports")
def get_reports():
    return guarded(service.list_reports)


@router.post("/reports", status_code=201)
def post_report(body: ReportBody):
    return guarded(lambda: service.create_report(body.filters(), body.title))


@router.get("/reports/{report_id}")
def read_report(report_id: str):
    return guarded(service.get_report, report_id)


@router.get("/reports/{report_id}/export")
def download_report(report_id: str, format: Literal["csv", "html", "xlsx", "json"] = "csv"):
    content, content_type = guarded(service.export_report, report_id, format)
    # Use the persisted opaque ID instead of user-supplied title in HTTP headers.
    safe_id = service.get_report(report_id)["id"]
    return Response(content, media_type=content_type, headers={"Content-Disposition": f'attachment; filename="report-{safe_id}.{format}"'})


@router.post("/chat")
async def post_chat(body: ChatBody):
    if not body.prompt.strip():
        raise HTTPException(400, "Savol matnini kiriting.")
    try:
        return await chat(body.prompt, body.filters(), [m.model_dump() for m in body.history])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except LLMServiceError as exc:
        logger.warning("Platform AI request failed: %s", type(exc).__name__)
        raise HTTPException(502, "OpenAI javob bera olmadi. API kaliti, hisob balansi va ulanishni tekshiring. Hisoblangan ma'lumotlar dashboardda mavjud.") from exc
    except Exception as exc:
        logger.error("Platform chat failed: %s", type(exc).__name__)
        raise HTTPException(500, "Suhbatni qayta ishlashda xatolik yuz berdi.") from exc


@router.post("/command")
async def post_command(body: CommandBody):
    if not body.prompt.strip():
        raise HTTPException(400, "Topshiriq matnini kiriting.")
    try:
        return await command(body.prompt, body.filters(), [m.model_dump() for m in body.history], body.source,
                             body.expected_connection_fingerprint)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except LLMServiceError as exc:
        logger.warning("Assistant planning failed: %s", type(exc).__name__)
        raise HTTPException(502, "Jarvis javob bera olmadi. OpenAI ulanishini tekshirib, qayta urinib ko'ring.") from exc
    except Exception as exc:
        logger.error("Assistant command failed: %s", type(exc).__name__)
        raise HTTPException(500, "Topshiriqni bajarishda xatolik yuz berdi.") from exc
