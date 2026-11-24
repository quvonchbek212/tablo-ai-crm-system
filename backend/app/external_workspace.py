"""Source-bound tools for an arbitrary connected SQL database.

Only executed, read-only results become artifacts. The connection identity is
captured before planning, and checked again after every asynchronous operation.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app import platform_service as service
from app.agent_analyst import AIDataAnalystAgent
from app.dashboard_generator import DashboardGenerator
from app.data_quality import DataQualityAuditor
from app.database import db_manager
from app.dealership_seed import DEMO_DB_PATH
from app.demo_seed import is_enterprise_demo
from app.models import DataQualityAuditRequest, NaturalQueryResponse, SchemaContext
from app.schema_profiler import SchemaProfiler
from app.llm_service import llm_service


def iterative_agent_enabled():
    return (getattr(llm_service, "provider", "") == "openai" and bool(llm_service.api_key.strip())
            and callable(getattr(llm_service, "generate_tool_turn", None)))


class ExternalSourceUnavailable(ValueError):
    """The requested external source is not connected."""


class SourceChangedError(ValueError):
    """Results from an obsolete connection must not be returned or saved."""


@dataclass(frozen=True)
class ConnectionIdentity:
    engine: object
    fingerprint: str | None

    @classmethod
    def capture(cls):
        return cls(db_manager._engine, db_manager.connection_fingerprint)

    def assert_current(self):
        if db_manager._engine is not self.engine or db_manager.connection_fingerprint != self.fingerprint:
            raise SourceChangedError("So'rov davomida ulangan baza almashdi. Natija saqlanmadi; yangi baza uchun qayta yuboring.")

    @property
    def is_demo(self):
        engine = self.engine
        return bool(engine and engine.dialect.name == "sqlite" and engine.url.database
                    and Path(engine.url.database).resolve() == DEMO_DB_PATH.resolve())


@dataclass
class ExternalWorkspace:
    identity: ConnectionIdentity
    schema: SchemaContext
    is_demo: bool = False

    @classmethod
    async def open(cls, identity: ConnectionIdentity | None = None, allow_demo: bool = False):
        identity = identity or ConnectionIdentity.capture()
        identity.assert_current()
        engine = identity.engine
        is_demo = identity.is_demo
        if engine is None or (is_demo and not allow_demo):
            raise ExternalSourceUnavailable("Avval tashqi bazangizni ulang. Namuna bazasi tashqi manba o'rniga ishlatilmadi.")
        schema = await asyncio.to_thread(SchemaProfiler.profile)
        identity.assert_current()
        if schema.connection_fingerprint and schema.connection_fingerprint != identity.fingerprint:
            raise SourceChangedError("Baza tavsifi faol ulanishga mos emas. Bazani qayta ulang.")
        if not schema.profile_complete:
            raise ValueError("Baza tuzilmasi to'liq o'rganilmadi. Ulanishdagi ogohlantirishlarni tekshiring va qayta ulang.")
        if not schema.tables:
            raise ValueError("Ulangan bazada tahlil uchun jadvallar topilmadi.")
        return cls(identity=identity, schema=schema, is_demo=is_demo)

    def assert_current(self):
        self.identity.assert_current()
        cached = SchemaProfiler._cached_context
        if (cached is not None and cached.connection_fingerprint == self.identity.fingerprint
                and cached.schema_fingerprint != self.schema.schema_fingerprint):
            raise SourceChangedError("So'rov davomida baza tuzilmasi yangilandi. Tahlilni qayta yuboring.")

    @property
    def source(self):
        # A database name is useful provenance; connection URLs and credentials are never returned.
        name = self.schema.database_name or db_manager.current_db_name
        return {"name": "Atlas Auto Group" if self.is_demo else name, "database_name": name, "dialect": self.schema.database_type,
                "synthetic": self.is_demo or is_enterprise_demo(self.identity.engine), "connection_fingerprint": self.identity.fingerprint,
                "schema_fingerprint": self.schema.schema_fingerprint}

    async def analyze(self, prompt, history=None, filters=None, *, multiple_charts=False):
        self.assert_current()
        scoped_prompt = prompt_with_filters(prompt, filters)
        if iterative_agent_enabled():
            from app.data_agent import execute_data_agent
            result = await execute_data_agent(scoped_prompt, self.schema, history or [], self.assert_current,
                                              multiple_charts=multiple_charts)
        else:
            result = await AIDataAnalystAgent.execute_analyst_agent(scoped_prompt, self.schema, history or [])
        self.assert_current()
        data = result.model_dump(mode="json") if hasattr(result, "model_dump") else dict(result)
        data["source"] = self.source
        return data

    async def dashboard(self, prompt, history=None, filters=None, title=None):
        self.assert_current()
        payload = {"kind": "external", "title": title or "Baza dashboardi", "source": self.source,
                   "prompt": prompt, "generated_at": datetime.now(timezone.utc).isoformat()}
        if iterative_agent_enabled():
            request = prompt + "\nNatijani dashboard sifatida tayyorlang: so'rovga mos ko'rsatkichlar va grafiklarni haqiqiy SQL natijalaridan yarating. Bir nechta nuqtai nazarni tekshiring; mavjud bo'lmagan ma'lumotni o'ylab topmang."
            data = await self.analyze(request, history, filters, multiple_charts=True)
            require_executed_analysis(data)
            payload["analysis"] = data
            # Compatibility for dashboard API clients; every value has its SQL.
            kpis = []
            for query in data.get("query_results", []):
                rows = query.get("rows") or []
                if len(rows) != 1:
                    continue
                for column, value in rows[0].items():
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        kpis.append({"id": f"{query['id']}-{column}", "title": column,
                                     "value": value, "unit": "", "sql_query": query["sql_query"]})
            payload["dashboard"] = {"kpis": kpis[:12], "charts": data.get("charts") or [],
                                    "executive_summary": data.get("explanation"),
                                    "schema_summary": "", "generated_at": payload["generated_at"],
                                    "success": data["success"], "llm_model": llm_service.model}
        elif is_general_overview(prompt, history, filters):
            # The generator executes its own guarded SQL for every KPI/chart. A
            # fresh generation prevents cross-request dashboard cache reuse.
            result = await DashboardGenerator.generate_dashboard(force_refresh=True)
            self.assert_current()
            data = result.model_dump(mode="json")
            if not data.get("success") or not (data.get("kpis") or data.get("charts")):
                raise ValueError(data.get("message") or "Ulangan bazadan dashboard ko'rsatkichlari olinmadi.")
            payload["dashboard"] = data
        else:
            data = await self.analyze(prompt, history, filters, multiple_charts=True)
            require_executed_analysis(data)
            payload["analysis"] = data
        if filters:
            payload["filters"] = filters
        return payload

    async def report(self, prompt, history=None, filters=None, title=None):
        analysis = await self.analyze(prompt, history, filters, multiple_charts=True)
        require_executed_analysis(analysis)
        payload = {"kind": "external", "title": title or "Ulangan baza hisoboti", "source": self.source,
                   "prompt": prompt, "analysis": analysis}
        if filters:
            payload["filters"] = filters
        # Keep the final identity check and SQLite persistence in one synchronous
        # turn: a connection endpoint cannot interleave at an await here.
        self.assert_current()
        saved = service.save_artifact("report", payload)
        saved["exports"] = {fmt: f"/api/platform/reports/{saved['id']}/export?format={fmt}" for fmt in ("csv", "html", "xlsx", "json")}
        return saved

    async def audit(self, prompt):
        self.assert_current()
        audit = await asyncio.to_thread(DataQualityAuditor.audit, self.schema, DataQualityAuditRequest())
        self.assert_current()
        data = audit.model_dump(mode="json")
        findings = []
        for table in data["tables"]:
            if table["issues"]:
                findings.append({"severity": "warning", "title": table["table"],
                                 "description": "; ".join(table["issues"])})
        for relationship in data["relationships"]:
            if relationship.get("issue"):
                findings.append({"severity": "warning", "title": f"{relationship['from_table']} → {relationship['to_table']}",
                                 "description": relationship["issue"]})
        if not findings:
            findings.append({"severity": "info", "title": "Tekshiruv yakunlandi",
                             "description": "Bajarilgan tekshiruvlarda muammo topilmadi. Bu bazadagi barcha qoidalar tekshirildi degani emas."})
        summary = (f"{data['tables_checked']} ta jadval va {data['relationships_checked']} ta bog'lanish tekshirildi. "
                   "Bo'sh qiymatlar va bog'lanishlar cheklangan namunada tekshiriladi; audit vaqt yoki biznes filtrlari bilan cheklanmagan.")
        payload = {"kind": "external", "agent_id": "quality", "agent_name": "Baza sifati tekshiruvchisi",
                   "status": "completed", "summary": summary, "findings": findings, "source": self.source,
                   "prompt": prompt, "audit": data, "mode": "rule_based"}
        self.assert_current()
        return service.save_artifact("agent", payload)


def require_executed_analysis(data):
    if not data.get("success") or not data.get("sql_query"):
        raise ValueError(data.get("explanation") or "Savol bo'yicha tasdiqlangan hisoblash bajarilmadi.")


def prompt_with_filters(prompt, filters):
    values = {key: value for key, value in (filters or {}).items() if value is not None and value != ""}
    # A new explicit period supersedes inherited UI dates. Do not include both
    # scopes in the agent's question, where they would become contradictory.
    if re.search(r"\b(?:19|20)\d{2}\b|\b(?:yanvar|fevral|mart|aprel|may|iyun|iyul|avgust|sentabr|sentyabr|oktabr|oktyabr|noyabr|dekabr|bugun|kecha)\b", prompt.casefold()):
        values.pop("start_date", None)
        values.pop("end_date", None)
    if not values:
        return prompt
    scope = []
    if values.get("start_date"):
        scope.append(f"boshlanish sanasi {values['start_date']}")
    if values.get("end_date"):
        scope.append(f"tugash sanasi {values['end_date']}")
    if values.get("branch_id") is not None:
        scope.append(f"filial identifikatori {values['branch_id']}")
    if values.get("category"):
        scope.append(f"kategoriya {values['category']}")
    return prompt + "\nFoydalanuvchi tanlagan filtrlar: " + "; ".join(scope) + ". Savolda aniq boshqa davr berilgan bo'lsa, savoldagi davrni qo'llang. Filtrga mos ustun ma'nosi noma'lum bo'lsa, aniqlashtiring."


def is_general_overview(prompt, history=None, filters=None):
    """Only broad requests may use an unfiltered schema-wide dashboard."""
    if any(value is not None and value != "" for value in (filters or {}).values()):
        return False
    words = re.findall(r"[\w]+", prompt.casefold().replace("‘", "'").replace("’", "'"))
    allowed = set("""dashboard dashboardni dashboardini dashbord dashbordni dashbordini yarat yarating
        tayyorla tayyorlang tayyorlab ber bering tuz tuzing qilib qil qiling ko rsat korsat
        umumiy barcha ulangan baza bazani bazam bazamni bazadagi bazaga bazaning bazamning
        malumot ma lumot ma lumotlar malumotlar malumotlarni bo yicha boyicha uchun va
        iltimos menga faoliyat faoliyati overview general create build the a of my connected database""".split())
    return bool(words) and any(word.startswith(("dashboard", "dashbord")) or word == "overview" for word in words) and all(word in allowed for word in words)
