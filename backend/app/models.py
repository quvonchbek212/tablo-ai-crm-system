from enum import Enum
from typing import List, Dict, Any, Optional, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from app.serializers import serialize_db_value, clean_data_structure

class ChartType(str, Enum):
    KPI = "kpi"
    AREA = "area"
    STACKED_AREA = "stacked_area"
    BAR = "bar"
    HORIZONTAL_BAR = "horizontal_bar"
    STACKED_BAR = "stacked_bar"
    GROUPED_BAR = "grouped_bar"
    LINE = "line"
    STEPPED_LINE = "stepped_line"
    MULTI_LINE = "multi_line"
    PIE = "pie"
    DONUT = "donut"
    SEMI_DONUT = "semi_donut"
    GAUGE = "gauge"
    RADAR = "radar"
    RADIAL_BAR = "radial_bar"
    COMPOSED = "composed"
    SCATTER = "scatter"
    TREEMAP = "treemap"
    FUNNEL = "funnel"
    WATERFALL = "waterfall"
    METRIC_GRID = "metric_grid"
    TABLE = "table"

class ConnectionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    connection_url: Optional[str] = None
    host: Optional[str] = Field(default="localhost", alias="db_host")
    port: Optional[int] = Field(default=5432, alias="db_port")
    user: Optional[str] = Field(default="postgres", alias="db_user")
    password: Optional[str] = Field(default="", alias="db_password")
    dbname: Optional[str] = Field(default=None, alias="db_name")
    include_schemas: Optional[str] = Field(default="public", alias="db_include_schemas")
    db_type: str = Field(default="postgresql", description="'postgresql' or 'sqlite'")

    # Optional SSH Tunnel parameters
    use_ssh: bool = False
    ssh_host: Optional[str] = Field(default=None, alias="kimyo_db_ssh_host")
    ssh_port: int = Field(default=22, alias="kimyo_db_ssh_port")
    ssh_user: Optional[str] = Field(default=None, alias="kimyo_db_ssh_username")
    ssh_pkey_path: Optional[str] = Field(default=None, alias="kimyo_db_ssh_pkey_path")
    ssh_pkey_passphrase: Optional[str] = Field(default=None, alias="kimyo_db_ssh_pkey_passphrase")

class TableColumnMeta(BaseModel):
    name: str
    data_type: str
    comment: Optional[str] = None
    is_primary_key: bool = False
    is_unique: bool = False
    is_foreign_key: bool = False
    foreign_table: Optional[str] = None
    foreign_column: Optional[str] = None
    nullable: bool = True
    sample_values: List[Any] = Field(default_factory=list)

    @field_validator("sample_values", mode="before")
    @classmethod
    def validate_sample_values(cls, v):
        return clean_data_structure(v) if v else []

class TableMeta(BaseModel):
    name: str
    row_count: int = 0
    schema_name: Optional[str] = None
    object_type: str = "table"
    comment: Optional[str] = None
    columns: List[TableColumnMeta] = Field(default_factory=list)
    sample_rows: List[Dict[str, Any]] = Field(default_factory=list)
    primary_key: List[str] = Field(default_factory=list)
    candidate_keys: List[List[str]] = Field(default_factory=list)
    grain_hint: Optional[str] = None
    soft_delete_column: Optional[str] = None
    soft_delete_mode: Optional[str] = None
    semantic_aliases: List[str] = Field(default_factory=list)
    business_description: Optional[str] = None
    business_role: Optional[str] = None
    certified_source: bool = False
    default_time_column: Optional[str] = None
    default_time_format: Optional[Literal["iso8601"]] = None
    default_label_column: Optional[str] = None
    measure_columns: List[str] = Field(default_factory=list)
    dimension_columns: List[str] = Field(default_factory=list)

    @field_validator("sample_rows", mode="before")
    @classmethod
    def validate_sample_rows(cls, v):
        return clean_data_structure(v) if v else []

class SuggestedPreset(BaseModel):
    id: str
    tag: str
    chartTag: str = "Jadval"
    title: str
    desc: str
    table_name: str
    color: str = "from-indigo-500/20 to-cyan-500/20 text-indigo-400 border-indigo-500/30"

class TableRelationship(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    join_hint: str
    source: str = "inferred"
    confidence: float = 0.5
    verified: bool = False
    cardinality: Optional[str] = None
    constraint_name: Optional[str] = None
    constraint_validated: bool = True
    from_columns: List[str] = Field(default_factory=list)
    to_columns: List[str] = Field(default_factory=list)


class SemanticFilterDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: Optional[str] = None
    column: str
    operator: Literal[
        "eq", "neq", "in", "not_in", "is_null", "is_not_null",
        "not_in_or_null", "gt", "gte", "lt", "lte", "ilike", "not_ilike",
        "not_true", "not_ilike_or_null",
    ] = "eq"
    value: Optional[Any] = None


class SemanticPromptValueDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    aliases: List[str] = Field(default_factory=list)


class SemanticPromptFilterDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    table: str
    column: str
    operator: Literal["eq", "ilike"] = "eq"
    values: List[SemanticPromptValueDefinition] = Field(min_length=1)
    join_path: List[str] = Field(min_length=2)
    min_similarity: float = Field(default=0.8, ge=0.6, le=1.0)
    group_by_phrases: List[str] = Field(default_factory=list)
    group_by_alias: Optional[str] = None


class SemanticMetricDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    aliases: List[str] = Field(default_factory=list)
    trigger_phrases: List[str] = Field(default_factory=list)
    description: Optional[str] = None
    table: str
    aggregation: Literal["count", "count_distinct", "sum", "avg", "min", "max"]
    column: Optional[str] = None
    fallback_columns: List[str] = Field(default_factory=list)
    filters: List[SemanticFilterDefinition] = Field(default_factory=list)
    join_paths: List[List[str]] = Field(default_factory=list)
    prompt_filters: List[SemanticPromptFilterDefinition] = Field(default_factory=list)
    group_by: List[str] = Field(default_factory=list)
    group_by_alias: Optional[str] = None
    coalesce_group_by: bool = False
    exclude_null_group: bool = False
    latest_by: Optional[str] = None
    order: Literal["asc", "desc"] = "desc"
    limit: Optional[int] = Field(default=None, ge=1, le=500)
    empty_value_zero: bool = True
    unit: Optional[str] = None
    certified: bool = False


class SemanticTableDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    aliases: List[str] = Field(default_factory=list)
    description: Optional[str] = None
    role: Literal["fact", "dimension", "bridge", "snapshot", "technical", "unknown"] = "unknown"
    grain: Optional[str] = None
    default_time_column: Optional[str] = None
    default_time_format: Optional[Literal["iso8601"]] = None
    default_label_column: Optional[str] = None
    measures: List[str] = Field(default_factory=list)
    dimensions: List[str] = Field(default_factory=list)
    certified: bool = False


class SemanticJoinDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_table: str
    from_columns: List[str] = Field(min_length=1)
    to_table: str
    to_columns: List[str] = Field(min_length=1)
    cardinality: Literal["many-to-one", "one-to-many", "one-to-one", "many-to-many"]
    description: Optional[str] = None
    approved: bool = False


class UnavailableMetricDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phrases: List[str] = Field(min_length=1, max_length=50)
    reason: str = Field(min_length=1, max_length=700)


class SemanticCatalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = 1
    catalog_key: str = ""
    database_name: Optional[str] = None
    schema_fingerprint: str
    reviewed: bool = False
    tables: List[SemanticTableDefinition] = Field(default_factory=list)
    metrics: List[SemanticMetricDefinition] = Field(default_factory=list)
    unavailable_metrics: List[UnavailableMetricDefinition] = Field(default_factory=list)
    relationships: List[SemanticJoinDefinition] = Field(default_factory=list)

class SchemaContext(BaseModel):
    database_type: str
    database_name: Optional[str] = None
    included_schemas: Optional[str] = None
    total_tables: int
    tables: List[TableMeta]
    schema_prompt: str
    suggested_presets: List[SuggestedPreset] = Field(default_factory=list)
    relationships: List[TableRelationship] = Field(default_factory=list)
    saved_at: Optional[str] = None
    schema_fingerprint: Optional[str] = None
    connection_fingerprint: Optional[str] = None
    schema_changes: List[str] = Field(default_factory=list)
    profile_complete: bool = True
    profile_warnings: List[str] = Field(default_factory=list)
    source_object_count: int = 0
    profiled_column_count: int = 0
    declared_relationship_count: int = 0
    inferred_relationship_count: int = 0
    fingerprint_version: int = 1
    semantic_catalog_key: Optional[str] = None
    semantic_catalog_reviewed: bool = False
    semantic_metrics: List[SemanticMetricDefinition] = Field(default_factory=list)
    unavailable_metrics: List[UnavailableMetricDefinition] = Field(default_factory=list)


class KpiMetric(BaseModel):
    id: str
    title: str
    value: Any
    unit: Optional[str] = ""
    delta: Optional[str] = None
    delta_type: Optional[str] = "positive" # "positive", "negative", "neutral"
    description: Optional[str] = None
    target: Optional[str] = None
    category: Optional[str] = "Asosiy"
    sparkline_data: Optional[List[float]] = Field(default_factory=list)
    sql_query: Optional[str] = None

    @field_validator("value", mode="before")
    @classmethod
    def validate_kpi_value(cls, v):
        return serialize_db_value(v)

class ChartSpec(BaseModel):
    id: str
    query_id: Optional[str] = None
    title: str
    description: Optional[str] = None
    chart_type: str = "bar"
    badge: Optional[str] = None
    sql_query: str
    x_axis: Optional[str] = None
    y_axis: Optional[str] = None
    secondary_y_axis: Optional[str] = None
    data_keys: Optional[List[str]] = Field(default_factory=list)
    color_palette: Optional[str] = "indigo"
    insight: Optional[str] = None
    recommendation: Optional[str] = None
    data: List[Dict[str, Any]] = Field(default_factory=list)

    @field_validator("data", mode="before")
    @classmethod
    def validate_chart_data(cls, v):
        return clean_data_structure(v) if v else []

    @field_validator("chart_type", mode="before")
    @classmethod
    def normalize_chart_type(cls, v):
        if not v:
            return "bar"
        clean = str(v).lower().strip().replace("-", "_").replace(" ", "_")
        alias_map = {
            "area_chart": "area",
            "bar_chart": "bar",
            "line_chart": "line",
            "pie_chart": "pie",
            "donut_chart": "donut",
            "doughnut": "donut",
            "speedometer": "gauge",
            "semi_circle": "semi_donut",
            "spider": "radar",
            "spider_chart": "radar",
            "radial": "radial_bar",
            "combo": "composed",
            "mixed": "composed",
            "bar_line": "composed",
            "bubble": "scatter",
            "grid": "table",
            "matrix": "table"
        }
        return alias_map.get(clean, clean)

class BusinessDomain(BaseModel):
    name: str
    description: str
    record_count: int = 0
    status: str = "Faol"

class DashboardResponse(BaseModel):
    kpis: List[KpiMetric] = Field(default_factory=list)
    charts: List[ChartSpec] = Field(default_factory=list)
    schema_summary: str = ""
    executive_summary: Optional[str] = None
    business_domains: List[BusinessDomain] = Field(default_factory=list)
    suggested_questions: List[str] = Field(default_factory=list)
    generated_at: str
    llm_model: str
    success: bool = True
    message: Optional[str] = None

class NaturalQueryRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=4000)
    history: Optional[List[Dict[str, Any]]] = Field(default_factory=list)

class NaturalQueryResponse(BaseModel):
    sql_query: str
    chart: Optional[ChartSpec] = None
    charts: Optional[List[ChartSpec]] = Field(default_factory=list)
    explanation: str
    summary_text: Optional[str] = None
    table_used: Optional[str] = None
    columns: Optional[List[str]] = Field(default_factory=list)
    rows: Optional[List[Dict[str, Any]]] = Field(default_factory=list)
    data_count: int
    execution_time_ms: float
    success: bool = True
    error: Optional[str] = None
    response_type: Optional[str] = "dashboard" # "dashboard" or "text"
    returned_count: Optional[int] = None
    is_truncated: bool = False
    validation_warnings: List[str] = Field(default_factory=list)
    agent_trace: List[Dict[str, Any]] = Field(default_factory=list)
    query_results: List[Dict[str, Any]] = Field(default_factory=list)
    agent_status: Optional[str] = None

    @field_validator("rows", mode="before")
    @classmethod
    def validate_rows_data(cls, v):
        return clean_data_structure(v) if v else []


class DataQualityAuditRequest(BaseModel):
    tables: List[str] = Field(default_factory=list)
    max_tables: int = Field(default=12, ge=1, le=30)
    sample_rows: int = Field(default=5000, ge=100, le=20000)
    include_relationships: bool = True


class TableQualityResult(BaseModel):
    table: str
    row_count_estimate: int = 0
    declared_primary_key: List[str] = Field(default_factory=list)
    verified_candidate_key: List[str] = Field(default_factory=list)
    duplicate_key_groups: Optional[int] = None
    sampled_rows: int = 0
    null_rates: Dict[str, float] = Field(default_factory=dict)
    freshness_column: Optional[str] = None
    latest_timestamp: Optional[str] = None
    status: str = "unknown"
    issues: List[str] = Field(default_factory=list)


class RelationshipQualityResult(BaseModel):
    from_table: str
    from_column: str
    to_table: str
    to_column: str
    source: str
    verified: bool = False
    cardinality: Optional[str] = None
    orphan_count_sample: Optional[int] = None
    inactive_parent_count_sample: Optional[int] = None
    checked_rows: int = 0
    issue: Optional[str] = None


class DataQualityAuditResponse(BaseModel):
    database_name: Optional[str] = None
    read_only: bool = True
    checked_at: str
    tables_checked: int
    relationships_checked: int
    tables: List[TableQualityResult] = Field(default_factory=list)
    relationships: List[RelationshipQualityResult] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class GoldQueryCase(BaseModel):
    name: str
    question: str
    gold_sql: str = ""
    expected_error: Optional[Literal["clarification_required", "metric_unavailable"]] = None
    ranked_chart: bool = False


class GoldEvaluationRequest(BaseModel):
    cases: List[GoldQueryCase] = Field(min_length=1, max_length=50)
    database_name: Optional[str] = None
    schema_fingerprint: Optional[str] = None


class GoldEvaluationCaseResult(BaseModel):
    name: str
    question: str
    passed: bool
    outcome: Literal["correct", "wrong_answer", "clarification", "error", "not_run"] = "error"
    generated_sql: str = ""
    gold_sql: str = ""
    generated_row_count: int = 0
    gold_row_count: int = 0
    mismatch: Optional[str] = None


class GoldEvaluationResponse(BaseModel):
    database_name: Optional[str] = None
    read_only: bool = True
    total: int
    passed: int
    failed: int
    not_run: int = 0
    results: List[GoldEvaluationCaseResult] = Field(default_factory=list)

