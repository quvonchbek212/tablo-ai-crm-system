import math
import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Sequence, Set

from app.models import SchemaContext, TableMeta


@dataclass
class TableMatch:
    table: TableMeta
    score: float
    evidence: List[str] = field(default_factory=list)


class SchemaRetriever:
    """Database-agnostic table retrieval based only on schema metadata.

    The vocabulary maps user-language concepts to generic schema fragments; it
    never names a customer table. Database-specific source-of-truth decisions
    belong in an external semantic catalog, not in application code.
    """

    CONCEPTS: Sequence[Dict[str, Sequence[str]]] = (
        {"query": ("tashkilot", "korxona", "zavod", "company", "organization", "enterprise"),
         "schema": ("organization", "company", "enterprise", "factory", "legal_entity", "business")},
        {"query": ("mijoz", "xaridor", "klient", "customer", "client", "buyer"),
         "schema": ("customer", "client", "buyer", "account")},
        {"query": ("xodim", "ishchi", "personal", "shtat", "employee", "staff"),
         "schema": ("employee", "staff", "personnel", "personal", "worker")},
        {"query": ("mahsulot", "tovar", "material", "product", "item", "goods"),
         "schema": ("product", "item", "goods", "sku", "material", "nomenclature")},
        {"query": ("uskuna", "jihoz", "equipment", "hardware", "device"),
         "schema": ("equipment", "hardware", "device", "product", "asset")},
        {"query": ("marja", "margin", "rentabellik"),
         "schema": ("margin", "profit", "cost", "cost_price")},
        {"query": ("sotuv", "savdo", "tushum", "daromad", "sales", "revenue"),
         "schema": ("sale", "sales", "sold", "revenue", "turnover", "order", "invoice")},
        {"query": ("buyurtma", "sorov", "so'rov", "order", "request", "purchase"),
         "schema": ("order", "request", "purchase", "application")},
        {"query": ("filial", "branch"),
         "schema": ("branch", "filial")},
        {"query": ("ombor", "sklad", "warehouse", "storage"),
         "schema": ("warehouse", "storage", "stock", "inventory")},
        {"query": ("qoldiq", "qoldig", "qoldigi", "zaxira", "balans", "stock", "inventory", "remaining"),
         "schema": ("balance", "stock", "inventory", "remaining", "quantity", "daily_amount", "warehouse_amount")},
        {"query": ("miqdor", "hajm", "hajmi", "soni", "quantity", "volume", "amount", "count"),
         "schema": ("quantity", "volume", "amount", "total", "count")},
        {"query": ("qarz", "debitor", "kreditor", "qarzdor", "debt", "receivable", "payable"),
         "schema": ("debt", "debtor", "creditor", "receivable", "payable", "arrear", "balance")},
        {"query": ("soliq", "nedoimka", "penya", "tax", "arrear"),
         "schema": ("tax", "debt", "arrear", "penalty", "legal_entity")},
        {"query": ("tolov", "to'lov", "payment", "transaction", "tranzaksiya"),
         "schema": ("payment", "transaction", "receipt", "cash", "bank")},
        {"query": ("faktura", "invoice", "bill", "hisob"),
         "schema": ("invoice", "bill", "document", "faktura")},
        {"query": ("shartnoma", "contract", "agreement", "kelishuv"),
         "schema": ("contract", "agreement", "deal")},
        {"query": ("ishlab", "ishlab chiqarish", "production", "manufacturing"),
         "schema": ("production", "manufacturing", "output", "workshop")},
        {"query": ("reja", "plan", "target", "budget"),
         "schema": ("plan", "planned", "target", "budget", "forecast")},
        {"query": ("amal", "amaldagi", "haqiqat", "fakt", "actual"),
         "schema": ("actual", "fact", "realized", "daily")},
        {"query": ("investitsiya", "loyiha", "investment", "project"),
         "schema": ("investment", "project", "capital")},
        {"query": ("tashilgan", "jonatilgan", "jo'natilgan", "yuborilgan", "shipment", "transfer"),
         "schema": ("shipment", "transfer", "dispatch", "delivery", "movement")},
        {"query": ("kirim", "chiqim", "harakat", "movement", "income", "outcome"),
         "schema": ("income", "outcome", "movement", "transaction", "flow", "stock")},
        {"query": ("transport", "mashina", "avtomobil", "vehicle", "fleet"),
         "schema": ("transport", "vehicle", "fleet", "car", "truck")},
        {"query": ("haydovchi", "driver", "shofyor"),
         "schema": ("driver", "chauffeur", "operator")},
        {"query": ("reys", "qatnov", "safar", "trip", "route"),
         "schema": ("trip", "route", "waybill", "journey")},
        {"query": ("yoqilgi", "yoqilg'i", "fuel", "benzin", "dizel"),
         "schema": ("fuel", "gasoline", "diesel", "consumption")},
        {"query": ("viloyat", "tuman", "hudud", "region", "district", "location"),
         "schema": ("region", "district", "province", "city", "location", "territory")},
        {"query": ("laboratoriya", "namuna", "tahlil", "quality", "sample", "laboratory"),
         "schema": ("laboratory", "lab", "sample", "quality", "analysis", "inspection")},
        {"query": ("brak", "nomuvofiqlik", "defect", "deviation"),
         "schema": ("defect", "deviation", "non_conformance", "quality")},
    )

    STOPWORDS = {
        "eng", "esa", "ham", "bilan", "uchun", "qaysi", "qaysilar", "qancha",
        "nechta", "barcha", "hamma", "haqida", "boyicha", "qanday", "jami",
        "the", "and", "for", "are", "which", "what", "how", "many", "shu", "bu", "ta",
    }
    TECHNICAL_TOKENS = {
        "log", "audit", "progress", "history", "notification", "staging",
        "temp", "sync", "attachment", "message", "migration", "job",
    }

    @staticmethod
    def _normalize(value: str) -> str:
        value = (value or "").lower()
        value = value.replace("‘", "'").replace("’", "'").replace("`", "'")
        return re.sub(r"[^\w']+", " ", value, flags=re.UNICODE).strip()

    @classmethod
    def _tokens(cls, value: str) -> Set[str]:
        return {
            token for token in cls._normalize(value).replace("'", "").split()
            if len(token) >= 3 and token not in cls.STOPWORDS
        }

    @staticmethod
    def _equivalent_token(left: str, right: str) -> bool:
        if left == right:
            return True
        singular, plural = sorted((left, right), key=len)
        if singular.endswith(("ch", "sh", "ss", "x", "z")):
            return plural == singular + "es"
        if singular.endswith("y") and len(singular) > 1 and singular[-2] not in "aeiou":
            return plural == singular[:-1] + "ies"
        return plural == singular + "s"

    @classmethod
    def _term_covered(cls, term: str, available_tokens: Set[str]) -> bool:
        """Match schema tokens without unsafe substring matches (count/account)."""
        parts = set(filter(None, term.split("_")))
        if not parts:
            return False
        for part in parts:
            compatible = any(cls._equivalent_token(token, part) for token in available_tokens)
            if not compatible:
                return False
        return True

    @classmethod
    def _expanded_schema_terms(cls, prompt: str, tokens: Set[str]) -> Set[str]:
        return set().union(*cls._matched_schema_groups(prompt, tokens)) if tokens else set()

    @classmethod
    def _matched_schema_groups(cls, prompt: str, tokens: Set[str]) -> List[Set[str]]:
        normalized = cls._normalize(prompt)
        groups: List[Set[str]] = []
        for concept in cls.CONCEPTS:
            matched = False
            for query_term in concept["query"]:
                q = cls._normalize(query_term)
                if " " in q:
                    matched = q in normalized
                else:
                    matched = any(token == q or token.startswith(q) or q.startswith(token) for token in tokens)
                if matched:
                    break
            if matched:
                groups.append({
                    cls._normalize(term).replace(" ", "_")
                    for term in concept["schema"]
                })
        return groups

    @classmethod
    def rank(cls, prompt: str, schema_ctx: SchemaContext, top_k: int = 8) -> List[TableMatch]:
        query_tokens = cls._tokens(prompt)
        if not query_tokens:
            return []
        semantic_groups = cls._matched_schema_groups(prompt, query_tokens)
        wants_technical = bool(query_tokens.intersection(cls.TECHNICAL_TOKENS))
        normalized_prompt = cls._normalize(prompt)
        certified_metric_tables: Set[str] = set()
        for metric in schema_ctx.semantic_metrics:
            names = [metric.name, *metric.aliases]
            if any(
                cls._normalize(name) and cls._normalize(name) in normalized_prompt
                for name in names
            ):
                certified_metric_tables.add(metric.table.lower())
        matches: List[TableMatch] = []

        for table in schema_ctx.tables:
            base_name = table.name.split(".")[-1].lower()
            table_tokens = set(filter(None, re.split(r"[_\W]+", base_name)))
            comment_tokens = cls._tokens(
                " ".join(filter(None, [table.business_description, table.comment]))
            )
            column_tokens: Set[str] = set()
            for column in table.columns:
                normalized_column = column.name.lower()
                column_tokens.update(filter(None, re.split(r"[_\W]+", normalized_column)))
                column_tokens.update(cls._tokens(column.comment or ""))

            score = 0.0
            evidence: List[str] = []
            # Underscores are word characters: tokenizing `sales_events` never
            # produced the split {sales, events} expected by the old check.
            # An explicitly named source outranks broad business synonyms.
            if re.search(r'(?<![\w.])' + re.escape(base_name) + r'(?![\w])', prompt.lower()):
                score += 10000
                evidence.append('explicit identifier match')

            for alias in table.semantic_aliases:
                normalized_alias = cls._normalize(alias)
                alias_pattern = r"(?<!\w)" + re.escape(normalized_alias)
                if len(normalized_alias) <= 3:
                    alias_pattern += r"(?!\w)"
                if normalized_alias and re.search(alias_pattern, normalized_prompt):
                    score += 180 if table.certified_source else 80
                    evidence.append(f"semantic catalog alias:{alias}")

            if table.name.lower() in certified_metric_tables:
                score += 240
                evidence.append("certified metric source")

            if table_tokens and table_tokens.issubset(query_tokens):
                score += 140
                evidence.append("explicit table-name match")

            for token in query_tokens:
                if token in table_tokens:
                    score += 42
                    evidence.append(f"table token:{token}")
                elif any(token in part or part in token for part in table_tokens if len(part) >= 4):
                    score += 20
                if token in column_tokens:
                    score += 12
                    evidence.append(f"column token:{token}")
                if token in comment_tokens:
                    score += 18

            # Synonyms within one concept are alternatives, not independent
            # evidence. Cap each concept at its strongest schema match so a
            # table containing product/item/material is not triple-counted.
            for terms in semantic_groups:
                best_score = 0
                best_evidence = ""
                for term in terms:
                    term_score = 0
                    term_evidence = ""
                    if cls._equivalent_token(term, base_name):
                        term_score = 90
                        term_evidence = f"semantic table:{term}"
                    elif cls._term_covered(term, table_tokens):
                        term_score = 52
                        term_evidence = f"semantic fragment:{term}"
                    if cls._term_covered(term, column_tokens):
                        term_score = max(term_score, 22)
                        term_evidence = term_evidence or f"semantic column:{term}"
                    if term_score > best_score:
                        best_score = term_score
                        best_evidence = term_evidence
                score += best_score
                if best_evidence:
                    evidence.append(best_evidence)

            technical = bool(table_tokens.intersection(cls.TECHNICAL_TOKENS)) or any(
                marker in base_name for marker in ("_log", "_audit", "_progress", "temp_", "sync_")
            )
            if technical and not wants_technical:
                score -= 120
            if table.certified_source and score > 0:
                score += 20
            if table.object_type in {"view", "materialized_view"} and score > 0:
                score += 8
            if table.primary_key or table.candidate_keys:
                score += 4
            if score > 0 and table.row_count > 0:
                score += min(6.0, math.log10(table.row_count + 1))

            if score > 0:
                matches.append(TableMatch(table=table, score=round(score, 3), evidence=evidence[:8]))

        matches.sort(
            key=lambda item: (
                item.score,
                bool(item.table.primary_key or item.table.candidate_keys),
                item.table.row_count or 0,
                item.table.name,
            ),
            reverse=True,
        )
        return matches[:top_k]

    @classmethod
    def grouped_sources(cls, dimensions: Sequence[str], ranked: Sequence[TableMatch],
                        schema_ctx: SchemaContext, ranked_entity: str = "") -> List[TableMeta]:
        """Resolve a named fact and its named lookup tables through verified edges."""
        def names_table(match: TableMatch) -> bool:
            return any(item.startswith(("explicit identifier", "semantic table:", "semantic catalog alias:"))
                       for item in match.evidence)

        lookups = {}
        for excerpt in dimensions:
            matching = [m.table for m in cls.rank(excerpt, schema_ctx, top_k=4) if names_table(m)]
            if len(matching) == 1:
                lookups[matching[0].name] = matching[0]
        if ranked_entity and not lookups:
            # Some plans place "which products" in entity and omit it from
            # dimensions. Only use that fallback for a reviewed dimension.
            matching = [m.table for m in cls.rank(ranked_entity, schema_ctx, top_k=4)
                        if names_table(m) and m.table.certified_source and m.table.business_role == "dimension"]
            if len(matching) == 1:
                lookups[matching[0].name] = matching[0]
        if not lookups or len(lookups) > 3:
            return []
        # A header can outrank its detail lines on a broad term like sales, but
        # only a line fact with a reviewed product FK supports product-level sales.
        related = {}
        for rel in schema_ctx.relationships:
            if rel.verified and rel.source in {"foreign_key", "semantic_catalog"}:
                related.setdefault(rel.from_table, set()).add(rel.to_table)
        facts = [m.table for m in ranked if m.table.name not in lookups
                 and (names_table(m) or (m.table.certified_source and m.table.business_role == "fact"))
                 and lookups.keys() <= related.get(m.table.name, set())]
        if len(facts) != 1:
            return []
        return [facts[0], *lookups.values()]

    @staticmethod
    def ambiguous(matches: Iterable[TableMatch]) -> bool:
        ranked = list(matches)
        if not ranked:
            return True
        if ranked[0].score < 40:
            return True
        if len(ranked) < 2:
            return False
        # Close scores are acceptable when the top match has strong direct
        # evidence; otherwise ask instead of silently picking a source.
        close = ranked[0].score - ranked[1].score < 8
        strong_direct = any(item.startswith(("explicit", "table token", "semantic table")) for item in ranked[0].evidence)
        return close and not strong_direct
