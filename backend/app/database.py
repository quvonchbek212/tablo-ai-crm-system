import os
import logging
import re
from contextlib import nullcontext
from decimal import Decimal
from datetime import date, datetime
import uuid
import hashlib
import time
from typing import List, Dict, Any, Optional
from sqlalchemy import create_engine, text, inspect, event
from sqlalchemy.engine import Connection, Engine, make_url, URL
from sshtunnel import SSHTunnelForwarder
from app.config import settings
from app.serializers import serialize_db_value

logger = logging.getLogger(__name__)
SQLITE_QUERY_TIMEOUT_SECONDS = 15.0

class DatabaseManager:
    def __init__(self):
        self._engine: Optional[Engine] = None
        self._ssh_tunnel: Optional[SSHTunnelForwarder] = None
        self._current_url: Optional[str] = settings.effective_database_url
        self._include_schemas: str = settings.DB_INCLUDE_SCHEMAS
        self._source_identity: Optional[str] = None
        self._soft_delete_rules: Dict[str, Dict[str, str]] = {}
        self._last_connection_error: Optional[str] = None

        # Connection is intentionally lazy. Importing a module must not open an
        # external SSH connection; FastAPI starts it explicitly at application startup.

    def connect_from_settings(self) -> bool:
        """Initialize the configured connection once, without import-time I/O."""
        if self._engine is not None:
            return True

        if settings.SSH_HOST and settings.DB_NAME and settings.DB_USER:
            try:
                self.connect_with_ssh(
                    ssh_host=settings.SSH_HOST,
                    ssh_port=settings.SSH_PORT,
                    ssh_user=settings.SSH_USER or "root",
                    ssh_pkey_path=settings.SSH_PKEY_PATH,
                    ssh_pkey_passphrase=settings.SSH_PKEY_PASSPHRASE,
                    remote_host=settings.DB_HOST or "127.0.0.1",
                    remote_port=settings.DB_PORT or 5432,
                    db_user=settings.DB_USER,
                    db_password=settings.DB_PASSWORD,
                    db_name=settings.DB_NAME,
                    include_schemas=settings.DB_INCLUDE_SCHEMAS
                )
                self._last_connection_error = None
                logger.info("Auto-connected to database via SSH tunnel on startup!")
                return True
            except Exception as e:
                self._last_connection_error = self._safe_error(
                    e,
                    settings.DB_PASSWORD,
                    settings.SSH_PKEY_PASSPHRASE,
                )
                logger.warning(f"Could not auto-connect via SSH tunnel on startup: {e}")
                return False
        elif self._current_url:
            self._init_engine(self._current_url)
            return self._engine is not None
        else:
            self._last_connection_error = "Database connection is not configured."
            logger.info("No database configured yet. Waiting for user connection credentials.")
        return False

    @property
    def is_connected(self) -> bool:
        return self._engine is not None

    @property
    def is_ssh_active(self) -> bool:
        return self._ssh_tunnel is not None and self._ssh_tunnel.is_active

    def _stop_ssh_tunnel(self):
        if self._ssh_tunnel:
            self._stop_tunnel(self._ssh_tunnel)
            self._ssh_tunnel = None

    @staticmethod
    def _stop_tunnel(tunnel: Optional[SSHTunnelForwarder]) -> None:
        if tunnel:
            try:
                tunnel.stop()
                logger.info("SSH tunnel stopped.")
            except Exception as e:
                logger.warning(f"Error stopping SSH tunnel: {e}")

    @staticmethod
    def _safe_error(error: Exception, *secrets: Optional[str]) -> str:
        """Return an actionable connection error without leaking credentials."""
        message = str(error) or type(error).__name__
        for secret in secrets:
            if secret:
                message = message.replace(str(secret), "***")
        return message

    def _resolve_key_path(self, pkey_path: Optional[str]) -> str:
        if not pkey_path:
            raise ValueError(
                "SSH private key path is required. Set SSH_PKEY_PATH "
                "to a key stored outside the project directory."
            )

        expanded = os.path.expanduser(os.path.expandvars(pkey_path))
        candidates = [expanded]
        if not os.path.isabs(expanded):
            candidates.extend([os.path.abspath(expanded), os.path.join(os.getcwd(), expanded)])

        for c in candidates:
            if c and os.path.exists(c):
                return os.path.abspath(c)
        raise FileNotFoundError(
            f"SSH private key fayli topilmadi: '{pkey_path}'. "
            "Kalitni loyiha tashqarisida saqlang va to'liq yo'lini konfiguratsiyada ko'rsating."
        )

    def connect_with_ssh(
        self,
        ssh_host: str,
        ssh_port: int = 22,
        ssh_user: str = "root",
        ssh_pkey_path: Optional[str] = None,
        ssh_pkey_passphrase: Optional[str] = None,
        remote_host: str = "127.0.0.1",
        remote_port: int = 5432,
        db_user: str = "postgres",
        db_password: str = "",
        db_name: str = "postgres",
        include_schemas: Optional[str] = None
    ) -> bool:
        """Establishes an SSH tunnel and connects SQLAlchemy to remote PostgreSQL via local forward port."""
        resolved_key = self._resolve_key_path(ssh_pkey_path)
        logger.info(f"Connecting SSH tunnel to {ssh_user}@{ssh_host}:{ssh_port} using key: {resolved_key}")

        tunnel = None
        temp_engine = None
        old_engine = self._engine
        old_tunnel = self._ssh_tunnel
        try:
            tunnel = SSHTunnelForwarder(
                (ssh_host, int(ssh_port)),
                ssh_username=ssh_user,
                ssh_pkey=resolved_key,
                ssh_private_key_password=ssh_pkey_passphrase or None,
                remote_bind_address=(remote_host or "127.0.0.1", int(remote_port or 5432)),
                local_bind_address=("127.0.0.1", 0)
            )
            tunnel.start()
            local_port = tunnel.local_bind_port
            logger.info(f"SSH Tunnel active: 127.0.0.1:{local_port} -> {remote_host}:{remote_port}")

            url = URL.create(
                drivername="postgresql+psycopg2",
                username=db_user,
                password=db_password or None,
                host="127.0.0.1",
                port=local_port,
                database=db_name,
            ).render_as_string(hide_password=False)

            temp_engine = self._build_engine(url, connect_timeout=8)

            self._verify_engine_read_only(temp_engine)

            if old_engine:
                old_engine.dispose()
            if old_tunnel and old_tunnel is not tunnel:
                self._stop_tunnel(old_tunnel)

            self._engine = temp_engine
            self._ssh_tunnel = tunnel
            self._current_url = url
            self._soft_delete_rules = {}
            if include_schemas:
                self._include_schemas = include_schemas
            self._source_identity = "|".join([
                "postgresql+ssh",
                ssh_host or "",
                str(ssh_port or ""),
                remote_host or "",
                str(remote_port or ""),
                db_name or "",
            ])
            self._last_connection_error = None

            logger.info(f"Database connected through SSH tunnel to '{db_name}' on remote {remote_host}:{remote_port}")
            return True

        except Exception as e:
            if tunnel and tunnel.is_active:
                self._stop_tunnel(tunnel)
            if temp_engine:
                temp_engine.dispose()
            safe_error = self._safe_error(e, db_password, ssh_pkey_passphrase)
            self._last_connection_error = safe_error
            logger.error(f"SSH Tunnel connection failed: {safe_error}")
            raise RuntimeError(f"SSH Tunnel / Database xatosi: {safe_error}")

    def _init_engine(self, url: str):
        engine = None
        try:
            engine = self._build_engine(url, connect_timeout=10)
            self._verify_engine_read_only(engine)
            self._engine = engine
            self._current_url = url
            self._soft_delete_rules = {}
            parsed = make_url(url)
            self._source_identity = "|".join([
                parsed.get_backend_name() or "",
                parsed.host or "",
                str(parsed.port or ""),
                parsed.database or "",
            ])
            self._last_connection_error = None
            logger.info(f"Database engine initialized for: {self._safe_url(url)} (schemas: {self._include_schemas})")
        except Exception as e:
            try:
                password = make_url(url).password
            except Exception:
                password = None
            self._last_connection_error = self._safe_error(e, password)
            logger.error(f"Failed to initialize DB engine: {self._last_connection_error}")
            if engine:
                engine.dispose()
            self._engine = None

    def _safe_url(self, url: str) -> str:
        # Hide password in logs
        try:
            return make_url(url).render_as_string(hide_password=True)
        except Exception:
            return "<invalid-or-redacted-database-url>"

    def set_connection(self, url: str, include_schemas: Optional[str] = None) -> bool:
        """Switch direct database connection dynamically with optional schemas filter."""
        temp_engine = None
        old_engine = self._engine
        old_tunnel = self._ssh_tunnel
        try:
            temp_engine = self._build_engine(url, connect_timeout=5)
            
            self._verify_engine_read_only(temp_engine)
            
            if old_engine:
                old_engine.dispose()
            if old_tunnel:
                self._stop_tunnel(old_tunnel)
            self._engine = temp_engine
            self._ssh_tunnel = None
            self._current_url = url
            self._soft_delete_rules = {}
            if include_schemas:
                self._include_schemas = include_schemas
            parsed = make_url(url)
            self._source_identity = "|".join([
                parsed.get_backend_name() or "",
                parsed.host or "",
                str(parsed.port or ""),
                parsed.database or "",
            ])
            self._last_connection_error = None
            return True
        except Exception as e:
            if temp_engine:
                temp_engine.dispose()
            try:
                password = make_url(url).password
            except Exception:
                password = None
            self._last_connection_error = self._safe_error(e, password)
            logger.error(f"Connection test failed for {self._safe_url(url)}: {self._last_connection_error}")
            raise e

    @staticmethod
    def _postgres_connect_args(connect_timeout: int) -> Dict[str, Any]:
        """Enforce read-only and bounded execution at the PostgreSQL session level."""
        return {
            "connect_timeout": connect_timeout,
            "options": "-c default_transaction_read_only=on -c statement_timeout=15000",
        }

    @classmethod
    def _build_engine(cls, url: str, connect_timeout: int) -> Engine:
        """Create a dialect-aware engine with defense-in-depth read-only guards."""
        parsed = make_url(url)
        backend = parsed.get_backend_name()
        if backend == "sqlite":
            database = parsed.database
            if database and database != ":memory:":
                resolved = os.path.abspath(database)
                if not os.path.exists(resolved):
                    raise FileNotFoundError(
                        f"Read-only SQLite database does not exist: {resolved}"
                    )
            engine = create_engine(url, connect_args={"check_same_thread": False})
        elif backend == "postgresql":
            engine = create_engine(
                url,
                pool_pre_ping=True,
                pool_size=5,
                max_overflow=10,
                connect_args=cls._postgres_connect_args(connect_timeout),
            )
        else:
            # Other SQLAlchemy dialects still receive the statement-level guard.
            # Operators should additionally use a database account with SELECT only.
            engine = create_engine(url, pool_pre_ping=True)
        cls._install_read_only_guards(engine)
        return engine

    @staticmethod
    def _install_read_only_guards(engine: Engine) -> None:
        @event.listens_for(engine, "connect")
        def _set_connection_read_only(dbapi_connection, _connection_record):
            if engine.dialect.name == "sqlite":
                cursor = dbapi_connection.cursor()
                try:
                    cursor.execute("PRAGMA query_only = ON")
                finally:
                    cursor.close()

        @event.listens_for(engine, "before_cursor_execute")
        def _reject_writes(_conn, _cursor, statement, _parameters, _context, _executemany):
            normalized = re.sub(r"/\*.*?\*/|--[^\r\n]*", " ", statement, flags=re.S).strip()
            upper = normalized.upper()
            allowed_control = (
                upper.startswith("SET TRANSACTION READ ONLY")
                or upper.startswith("SET LOCAL STATEMENT_TIMEOUT")
                or upper.startswith("PRAGMA QUERY_ONLY")
            )
            if allowed_control:
                return
            from sqlglot.tokens import Tokenizer, TokenType
            try:
                tokens = Tokenizer().tokenize(statement)
            except Exception as exc:
                raise PermissionError("Unable to tokenize statement for read-only policy.") from exc
            executable_words = []
            for index, token in enumerate(tokens):
                if token.token_type in {TokenType.STRING, TokenType.IDENTIFIER}:
                    continue
                # REPLACE(text, from, to) is a scalar string function, not
                # SQLite/MySQL's data-modifying REPLACE statement.
                if (token.text.upper() == 'REPLACE' and index + 1 < len(tokens)
                        and tokens[index + 1].token_type == TokenType.L_PAREN):
                    continue
                executable_words.append(token.text.upper())
            unsafe = re.search(
                r"\b(INSERT|UPDATE|DELETE|MERGE|UPSERT|REPLACE|CREATE|ALTER|DROP|TRUNCATE|GRANT|REVOKE|COPY|CALL|EXECUTE|VACUUM|ATTACH|DETACH|LOCK|DO|REINDEX|REFRESH|DEALLOCATE)\b",
                ' '.join(executable_words),
            )
            if unsafe:
                raise PermissionError(f"Database connection is read-only; {unsafe.group(1)} operation rejected.")

    @staticmethod
    def _verify_engine_read_only(engine: Engine) -> None:
        """Refuse PostgreSQL/SQLite engines unless their session is demonstrably read-only."""
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            if engine.dialect.name == "postgresql":
                value = conn.execute(text("SHOW transaction_read_only")).scalar()
                if str(value).lower() not in {"on", "true", "1"}:
                    raise PermissionError("PostgreSQL session is not read-only.")
            elif engine.dialect.name == "sqlite":
                value = conn.exec_driver_sql("PRAGMA query_only").scalar()
                if not bool(value):
                    raise PermissionError("SQLite connection is not query-only.")

    def test_connection(self) -> Dict[str, Any]:
        """Test current connection and return dialect info."""
        if not self._engine:
            return {
                "status": "disconnected",
                "dialect": "none",
                "url": "",
                "error": self._last_connection_error,
                "read_only": False,
                "read_only_enforced": True,
            }
        try:
            with self._engine.connect() as conn:
                conn.execute(text("SELECT 1 AS status;")).fetchone()
                dialect = self._engine.dialect.name
                read_only = True
                if dialect == "postgresql":
                    value = conn.execute(text("SHOW transaction_read_only")).scalar()
                    read_only = str(value).lower() in {"on", "true", "1"}
                elif dialect == "sqlite":
                    value = conn.exec_driver_sql("PRAGMA query_only").scalar()
                    read_only = bool(value)
                return {
                    "status": "connected",
                    "dialect": dialect,
                    "url": self._safe_url(self._current_url or ""),
                    "database_name": self.current_db_name,
                    "ssh_tunnel": self.is_ssh_active,
                    "read_only": read_only,
                    "read_only_enforced": True,
                }
        except Exception as e:
            return {
                "status": "error",
                "dialect": "none",
                "url": self._safe_url(self._current_url or ""),
                "database_name": self.current_db_name,
                "error": str(e)
            }

    def execute_query(
        self,
        sql: str,
        max_rows: int = 500,
        apply_soft_delete: bool = True,
        *,
        connection: Connection | None = None,
        parameters: dict | None = None,
    ) -> List[Dict[str, Any]]:
        """
        Execute read-only SQL query and return JSON-serializable list of dicts.
        Converts Decimal, Datetime, Date, UUID to JSON friendly formats.
        """
        if not self._engine:
            return []

        # Final application boundary: every chat/dashboard/audit query must pass
        # the same strict SELECT-only AST policy, including internal callers.
        from app.sql_validator import validate_and_sanitize_sql
        is_valid, sanitized_or_error = validate_and_sanitize_sql(
            sql,
            apply_soft_delete=apply_soft_delete,
            soft_delete_rules=self._soft_delete_rules,
        )
        if not is_valid:
            raise PermissionError(sanitized_or_error)
        sql = sanitized_or_error
        
        with (self._engine.connect() if connection is None else nullcontext(connection)) as conn:
            if conn.dialect.name == "postgresql" and not conn.in_transaction():
                try:
                    conn.execute(text("SET TRANSACTION READ ONLY;"))
                    conn.execute(text("SET LOCAL statement_timeout = '15000ms';"))
                except Exception:
                    pass
            raw_sqlite = conn.connection.driver_connection if conn.dialect.name == "sqlite" else None
            if raw_sqlite is not None:
                deadline = time.monotonic() + SQLITE_QUERY_TIMEOUT_SECONDS
                raw_sqlite.set_progress_handler(lambda: int(time.monotonic() >= deadline), 10000)
            try:
                result = conn.execute(text(sql), parameters or {})
                columns = list(result.keys())
                rows = result.fetchmany(max_rows)
                return [{col: serialize_db_value(val) for col, val in zip(columns, row)} for row in rows]
            finally:
                if raw_sqlite is not None:
                    raw_sqlite.set_progress_handler(None, 0)

    def configure_soft_delete_rules(self, rules: Dict[str, Dict[str, str]]) -> None:
        """Install schema-derived filters for the currently connected database."""
        self._soft_delete_rules = {
            str(table).lower(): {
                "column": str(rule.get("column") or "is_deleted"),
                "mode": str(rule.get("mode") or "not_true"),
            }
            for table, rule in (rules or {}).items()
            if isinstance(rule, dict)
        }

    @property
    def soft_delete_rules(self) -> Dict[str, Dict[str, str]]:
        return dict(self._soft_delete_rules)

    def get_inspector(self):
        if not self._engine:
            return None
        return inspect(self._engine)

    @property
    def current_db_name(self) -> str:
        if self._engine and hasattr(self._engine.url, "database"):
            return self._engine.url.database or "default"
        return settings.DB_NAME or "default"

    @property
    def connection_fingerprint(self) -> Optional[str]:
        """Stable source identity without exposing credentials."""
        if not self._engine:
            return None
        url = self._engine.url
        base_identity = self._source_identity or "|".join([
            url.get_backend_name() or "",
            url.host or "",
            str(url.port or ""),
            url.database or "",
        ])
        identity = "|".join([base_identity, self._include_schemas or "public"])
        return hashlib.sha256(identity.encode("utf-8")).hexdigest()

    @property
    def dialect_name(self) -> str:
        if self._engine:
            return self._engine.dialect.name
        return "none"

    @property
    def current_url(self) -> str:
        return self._current_url or ""

    @property
    def include_schemas(self) -> str:
        return self._include_schemas

    @property
    def last_connection_error(self) -> Optional[str]:
        return self._last_connection_error

    def get_schemas_list(self) -> List[str]:
        if not self._include_schemas:
            return ["public"]
        schemas = [s.strip() for s in self._include_schemas.split(",") if s.strip()]
        invalid = [s for s in schemas if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", s)]
        if invalid:
            raise ValueError("Invalid PostgreSQL schema identifier.")
        return schemas or ["public"]

    def quote_identifier(self, identifier: str) -> str:
        """Quote a schema/table/column identifier using the connected dialect."""
        if not self._engine:
            raise RuntimeError("Database is not connected.")
        parts = identifier.split(".")
        if any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", part) for part in parts):
            raise ValueError("Invalid SQL identifier.")
        preparer = self._engine.dialect.identifier_preparer
        return ".".join(preparer.quote(part) for part in parts)

db_manager = DatabaseManager()
