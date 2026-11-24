import asyncio
import logging
import time
from typing import Dict, Any, List, Tuple, Optional, Callable
from app.database import db_manager
from app.sql_validator import validate_and_sanitize_sql
from app.llm_service import LLMRateLimitError, LLMUnavailableError, llm_service
from app.config import settings
from app.models import SchemaContext
from app.relationship_guard import RelationshipGuard

logger = logging.getLogger(__name__)

class SelfHealingQueryEngine:
    """
    Executes queries with a Self-Healing loop:
    If a query fails with a database-level error (syntax, missing column, wrong join, group-by error),
    it feeds the error back to Gemma to automatically repair the SQL query and re-runs it.
    """

    @classmethod
    async def execute_with_self_healing(
        cls, 
        sql_query: str, 
        schema_prompt: str, 
        user_intent: Optional[str] = None,
        schema_ctx: Optional[SchemaContext] = None,
        semantic_validator: Optional[Callable[[str], Tuple[bool, Optional[str]]]] = None,
        max_rows: int = 500,
    ) -> Tuple[bool, List[Dict[str, Any]], str, Optional[str]]:
        """
        Returns:
            (success: bool, data: List[Dict], final_sql: str, error_message: Optional[str])
        """
        current_sql = sql_query
        last_error = None
        if schema_ctx is not None:
            schema_prompt = f"Active SQL dialect: {schema_ctx.database_type}.\n{schema_prompt}"

        for attempt in range(settings.MAX_SQL_RETRIES):
            # 1. Safety & AST validation
            is_valid, sanitized_or_err = validate_and_sanitize_sql(
                current_sql,
                soft_delete_rules=db_manager.soft_delete_rules,
            )
            if not is_valid:
                last_error = sanitized_or_err
                logger.warning(f"[Attempt {attempt + 1}] Security validation failed: {last_error}")
                if attempt < settings.MAX_SQL_RETRIES - 1:
                    current_sql = await cls._ask_llm_to_fix(current_sql, last_error, schema_prompt, user_intent)
                    continue
                break

            current_sql = sanitized_or_err

            if semantic_validator is not None:
                semantic_ok, semantic_error = semantic_validator(current_sql)
                if not semantic_ok:
                    last_error = semantic_error or "Generated SQL does not answer the requested metric."
                    logger.warning(
                        "[Attempt %s] Semantic validation failed: %s",
                        attempt + 1,
                        last_error,
                    )
                    if attempt < settings.MAX_SQL_RETRIES - 1:
                        current_sql = await cls._ask_llm_to_fix(
                            current_sql,
                            last_error,
                            schema_prompt,
                            user_intent,
                        )
                        continue
                    break

            joins_valid, join_error = await asyncio.to_thread(
                RelationshipGuard.validate_query,
                current_sql,
                schema_ctx,
            )
            if not joins_valid:
                last_error = join_error
                logger.warning("[Attempt %s] Join validation failed: %s", attempt + 1, join_error)
                if attempt < settings.MAX_SQL_RETRIES - 1:
                    current_sql = await cls._ask_llm_to_fix(
                        current_sql,
                        join_error or "Unsafe join",
                        schema_prompt,
                        user_intent,
                    )
                    continue
                break

            # 2. Database Execution
            try:
                start_time = time.time()
                data = await asyncio.to_thread(db_manager.execute_query, current_sql, max_rows=max_rows)
                exec_time = (time.time() - start_time) * 1000
                logger.info(f"Query succeeded on attempt {attempt + 1} ({exec_time:.1f}ms): {current_sql[:80]}...")
                return True, data, current_sql, None

            except Exception as db_err:
                error_msg = str(db_err).strip()
                last_error = error_msg
                logger.warning(f"[Attempt {attempt + 1}] DB execution failed: {error_msg}")
                
                # If we still have retries, ask LLM to heal the query
                if attempt < settings.MAX_SQL_RETRIES - 1:
                    try:
                        current_sql = await cls._ask_llm_to_fix(current_sql, error_msg, schema_prompt, user_intent)
                    except (LLMRateLimitError, LLMUnavailableError):
                        raise
                    except Exception as llm_err:
                        logger.error(f"Failed to get fix from LLM: {llm_err}")
                        break

        return False, [], current_sql, f"Execution failed after {settings.MAX_SQL_RETRIES} attempts. Last error: {last_error}"

    @classmethod
    async def _ask_llm_to_fix(
        cls, 
        failed_sql: str, 
        error_msg: str, 
        schema_prompt: str, 
        user_intent: Optional[str]
    ) -> str:
        """Sends the exact PostgreSQL error message and schema back to Gemma to produce a corrected query."""
        system_prompt = (
            "You are an expert SQL debugger. Your job is to fix a failed SQL query.\n"
            "Rules:\n"
            "1. Output ONLY the raw corrected SQL query.\n"
            "2. Do NOT include markdown code blocks, explanations, or quotes.\n"
            "3. Only use existing tables and columns specified in the schema.\n"
            "4. Only produce a read-only SELECT query.\n"
            "5. If error is 'invalid input syntax for type numeric' (e.g. text containing commas like '0,00' or spaces), convert safely using: CAST(NULLIF(REPLACE(REPLACE(column_name, ' ', ''), ',', '.'), '') AS NUMERIC).\n"
            "6. Standard string functions like REPLACE(col, ',', '.') and REGEXP_REPLACE are safe, valid, and permitted in SELECT queries."
        )

        user_prompt = (
            f"{schema_prompt}\n\n"
            f"Original Intent: {user_intent or 'Generate analytical query'}\n"
            f"Failed SQL:\n{failed_sql}\n\n"
            f"Database Error Message:\n{error_msg}\n\n"
            "Fix the SQL query to resolve this error. Return ONLY the valid SQL statement:"
        )

        fixed_text = await llm_service.generate_completion(system_prompt, user_prompt)
        
        # Clean potential fences
        import re
        fixed_sql = re.sub(r"<think>[\s\S]*?(?:</think>|$)", "", fixed_text, flags=re.IGNORECASE).strip()
        if "```" in fixed_sql:
            import re
            m = re.search(r"```(?:sql)?\s*([\s\S]*?)\s*```", fixed_sql, re.IGNORECASE)
            if m:
                fixed_sql = m.group(1).strip()
        
        return fixed_sql.rstrip(";")
