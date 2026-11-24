"""A bounded, inspectable summary of the active database's actual schema."""
from app.models import SchemaContext


def build_understanding(schema: SchemaContext) -> dict:
    tables = schema.tables
    complete = schema.profile_complete
    status = "partial" if not complete else ("ready" if tables else "empty")
    suggestions = []
    if tables and complete:
        suggestions = [
            f'"{tables[0].name}" jadvalida nechta yozuv bor?',
            "Ulangan baza bo'yicha umumiy dashboard yarat",
            "Ulangan ma'lumotlar bo'yicha hisobot tayyorla",
            "Bazadagi ma'lumotlar sifatini tekshir",
        ]
    return {
        "status": status,
        "database_name": schema.database_name,
        "dialect": schema.database_type,
        "table_count": schema.total_tables,
        "column_count": sum(len(table.columns) for table in tables),
        "relationship_count": len(schema.relationships),
        "declared_relationship_count": sum(r.source == "foreign_key" for r in schema.relationships),
        "inferred_relationship_count": sum(r.source != "foreign_key" for r in schema.relationships),
        "profile_complete": complete,
        "warnings": list(schema.profile_warnings),
        "schema_fingerprint": schema.schema_fingerprint,
        "connection_fingerprint": schema.connection_fingerprint,
        "profiled_at": schema.saved_at,
        "read_only": True,
        "semantic_catalog_reviewed": schema.semantic_catalog_reviewed,
        "tables_truncated": len(tables) > 100,
        "tables": [
            {
                "name": table.name,
                "row_count_estimate": table.row_count,
                "column_count": len(table.columns),
                "columns_truncated": len(table.columns) > 30,
                "primary_key": table.primary_key,
                "description": table.business_description or table.comment,
                "columns": [
                    {"name": c.name, "data_type": c.data_type,
                     "is_primary_key": c.is_primary_key, "is_foreign_key": c.is_foreign_key}
                    for c in table.columns[:30]
                ],
            }
            for table in tables[:100]
        ],
        "suggested_questions": suggestions,
    }
