"""Small synthetic catalog fixtures independent of a developer's cached database."""
from app.models import (
    SchemaContext, TableMeta, TableColumnMeta, SemanticCatalog,
    SemanticMetricDefinition, SemanticJoinDefinition, SemanticFilterDefinition,
    SemanticPromptFilterDefinition, SemanticPromptValueDefinition, SemanticTableDefinition,
)


def reviewed_warehouse_fixture():
    tables = []
    for name, columns in {
        "warehouse": ["id", "name", "working_factory_id", "district_id"],
        "district": ["id", "region_id"],
        "region": ["id", "name_uz"],
    }.items():
        tables.append(TableMeta(name=name, primary_key=["id"], columns=[
            TableColumnMeta(name=column, data_type="text" if "name" in column else "integer",
                            is_primary_key=column == "id") for column in columns
        ]))
    schema = SchemaContext(database_name="synthetic_warehouses", database_type="postgresql",
                           schema_fingerprint="fixture", total_tables=3, tables=tables, schema_prompt="")
    catalog = SemanticCatalog(schema_fingerprint="fixture", reviewed=True,
    tables=[SemanticTableDefinition(table=name, certified=True, role="dimension")
            for name in ("warehouse", "district", "region")], metrics=[
        SemanticMetricDefinition(
            id="authorized_warehouse_count", name="Vakolatli omborlar soni",
            aliases=["vakolatli ombor", "vakolatli omborlar"],
            table="warehouse", aggregation="count", certified=True,
            filters=[SemanticFilterDefinition(column="working_factory_id", operator="is_not_null"),
                     SemanticFilterDefinition(column="name", operator="not_ilike", value="%Chiqim Ombor%")],
            prompt_filters=[SemanticPromptFilterDefinition(
                id="region", table="region", column="name_uz",
                join_path=["warehouse", "district", "region"],
                values=[SemanticPromptValueDefinition(value="Qashqadaryo viloyati",
                        aliases=["Qashqadaryo", "Qashqadartoda"])],
                group_by_phrases=["har bitta viloyatda", "viloyatlar kesimida"],
                group_by_alias="viloyat",
            )],
        )
    ], relationships=[
        SemanticJoinDefinition(from_table="warehouse", from_columns=["district_id"],
                               to_table="district", to_columns=["id"], cardinality="many-to-one", approved=True),
        SemanticJoinDefinition(from_table="district", from_columns=["region_id"],
                               to_table="region", to_columns=["id"], cardinality="many-to-one", approved=True),
    ])
    return schema, catalog
