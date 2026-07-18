"""Modern async database service with SQLModel and SQLAlchemy 2.0."""

import json
import inspect
from copy import deepcopy
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, Any, List, Optional, Tuple, Union
from pydantic_core import to_jsonable_python
from sqlmodel import SQLModel, select
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.exc import IntegrityError
from sqlalchemy import case, text, func, update, or_
from contextlib import asynccontextmanager

from core.config import Settings
from models.database import (
    NodeParameter,
    RuntimeMutation,
    Workflow,
    Execution,
    APIKey,
    APIKeyValidation,
    NodeOutput,
    ConversationMessage,
    ToolSchema,
    UserSkill,
    ChatMessage,
    UserSettings,
    TokenUsageMetric,
    CompactionEvent,
    SessionTokenState,
    ProviderDefaults,
    AgentTeam,
    TeamMember,
    TeamTask,
    TeamTaskAttempt,
    AgentMessage,
    SubagentConcurrencyCounter,
    SubagentConcurrencyPermit,
    GoogleConnection,
    ProxyProviderConfig,
    ProxyRoutingRule,
)
from models.cache import CacheEntry  # SQLite-backed cache for Redis alternative
from core.logging import get_logger

logger = get_logger(__name__)

RuntimeMutationCallback = Callable[
    [AsyncSession],
    Union[Optional[Dict[str, Any]], Awaitable[Optional[Dict[str, Any]]]],
]
ParameterMutator = Callable[
    [Dict[str, Any]],
    Union[
        Dict[str, Any],
        Tuple[Dict[str, Any], Optional[Dict[str, Any]]],
    ],
]


class Database:
    """Async database service with SQLModel."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.engine = None
        self.async_session = None

    async def startup(self):
        """Initialize database connection and create tables."""
        try:
            # Disable verbose database and asyncio logging
            import logging

            logging.getLogger("aiosqlite").setLevel(logging.WARNING)
            logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
            logging.getLogger("sqlalchemy.dialects").setLevel(logging.WARNING)
            logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)

            # Create async engine.
            #
            # ``json_serializer`` is SQLAlchemy's documented extension point
            # for every JSON column on this engine (node_outputs.data,
            # node_parameters, ...). ``pydantic_core.to_jsonable_python`` is
            # Pydantic's official arbitrary-object → JSON coercion
            # (dataclasses, BaseModel, datetime, enums, sets natively;
            # ``fallback=str`` for true unknowns) — stdlib ``json.dumps``
            # alone raised ``TypeError: Object of type X is not JSON
            # serializable`` and silently dropped node-output persistence.
            self.engine = create_async_engine(
                self.settings.database_url,
                echo=self.settings.database_echo,
                pool_size=self.settings.database_pool_size,
                max_overflow=self.settings.database_max_overflow,
                future=True,
                json_serializer=lambda obj: json.dumps(to_jsonable_python(obj, fallback=str)),
            )

            # Create session factory
            self.async_session = async_sessionmaker(bind=self.engine, class_=AsyncSession, expire_on_commit=False)

            # Create tables
            async with self.engine.begin() as conn:
                await conn.run_sync(SQLModel.metadata.create_all)

            # Add missing columns to existing tables (simple migration)
            await self._migrate_user_settings()
            await self._migrate_agent_teams()

            logger.info("Database initialized successfully")

        except Exception as e:
            logger.error("Database startup failed", error=str(e))
            raise

    async def _migrate_user_settings(self):
        """Add missing columns to user_settings table."""
        try:
            async with self.engine.begin() as conn:
                # Check if column exists and add if missing
                result = await conn.execute(text("PRAGMA table_info(user_settings)"))
                columns = {row[1] for row in result.fetchall()}

                if "console_panel_default_open" not in columns:
                    await conn.execute(text("ALTER TABLE user_settings ADD COLUMN console_panel_default_open BOOLEAN DEFAULT 0"))
                    logger.info("Added console_panel_default_open column to user_settings")

                if "examples_loaded" not in columns:
                    await conn.execute(text("ALTER TABLE user_settings ADD COLUMN examples_loaded BOOLEAN DEFAULT 0"))
                    logger.info("Added examples_loaded column to user_settings")

                if "onboarding_completed" not in columns:
                    await conn.execute(text("ALTER TABLE user_settings ADD COLUMN onboarding_completed BOOLEAN DEFAULT 0"))
                    # Existing users (examples_loaded=1) skip onboarding
                    await conn.execute(text("UPDATE user_settings SET onboarding_completed = 1 WHERE examples_loaded = 1"))
                    logger.info("Added onboarding_completed column to user_settings")

                if "onboarding_step" not in columns:
                    await conn.execute(text("ALTER TABLE user_settings ADD COLUMN onboarding_step INTEGER DEFAULT 0"))
                    logger.info("Added onboarding_step column to user_settings")

                if "memory_window_size" not in columns:
                    await conn.execute(text("ALTER TABLE user_settings ADD COLUMN memory_window_size INTEGER DEFAULT 100"))
                    logger.info("Added memory_window_size column to user_settings")

                if "compaction_ratio" not in columns:
                    await conn.execute(text("ALTER TABLE user_settings ADD COLUMN compaction_ratio REAL DEFAULT 0.8"))
                    logger.info("Added compaction_ratio column to user_settings")

                if "default_llm_provider" not in columns:
                    await conn.execute(text("ALTER TABLE user_settings ADD COLUMN default_llm_provider VARCHAR(50)"))
                    logger.info("Added default_llm_provider column to user_settings")

                if "default_llm_model" not in columns:
                    await conn.execute(text("ALTER TABLE user_settings ADD COLUMN default_llm_model VARCHAR(200)"))
                    logger.info("Added default_llm_model column to user_settings")

                if "auto_add_skill_for_tools" not in columns:
                    await conn.execute(text("ALTER TABLE user_settings ADD COLUMN auto_add_skill_for_tools BOOLEAN DEFAULT 1"))
                    logger.info("Added auto_add_skill_for_tools column to user_settings")

                if "auto_rebind_tools_after_canvas_change" not in columns:
                    await conn.execute(text("ALTER TABLE user_settings ADD COLUMN auto_rebind_tools_after_canvas_change BOOLEAN DEFAULT 1"))
                    logger.info("Added auto_rebind_tools_after_canvas_change column to user_settings")

                if "agent_recursion_limit" not in columns:
                    await conn.execute(text("ALTER TABLE user_settings ADD COLUMN agent_recursion_limit INTEGER DEFAULT 200"))
                    logger.info("Added agent_recursion_limit column to user_settings")

                if "max_concurrent_subagents" not in columns:
                    await conn.execute(text("ALTER TABLE user_settings ADD COLUMN max_concurrent_subagents INTEGER DEFAULT 3"))
                if "max_delegation_depth" not in columns:
                    await conn.execute(text("ALTER TABLE user_settings ADD COLUMN max_delegation_depth INTEGER DEFAULT 2"))

                # Migrate token_usage_metrics table - add cost columns
                result = await conn.execute(text("PRAGMA table_info(token_usage_metrics)"))
                columns = {row[1] for row in result.fetchall()}

                for col in ["input_cost", "output_cost", "cache_cost", "total_cost"]:
                    if col not in columns:
                        await conn.execute(text(f"ALTER TABLE token_usage_metrics ADD COLUMN {col} REAL DEFAULT 0.0"))
                        logger.info(f"Added {col} column to token_usage_metrics")

                # Migrate session_token_states table - add cumulative cost columns
                result = await conn.execute(text("PRAGMA table_info(session_token_states)"))
                columns = {row[1] for row in result.fetchall()}

                for col in ["cumulative_input_cost", "cumulative_output_cost", "cumulative_total_cost"]:
                    if col not in columns:
                        await conn.execute(text(f"ALTER TABLE session_token_states ADD COLUMN {col} REAL DEFAULT 0.0"))
                        logger.info(f"Added {col} column to session_token_states")

                # Migrate provider_defaults table - add default_model column
                result = await conn.execute(text("PRAGMA table_info(provider_defaults)"))
                columns = {row[1] for row in result.fetchall()}
                if columns and "default_model" not in columns:
                    await conn.execute(text("ALTER TABLE provider_defaults ADD COLUMN default_model TEXT DEFAULT ''"))
                    logger.info("Added default_model column to provider_defaults")

                # Create api_usage_metrics table if not exists
                await conn.execute(
                    text("""
                    CREATE TABLE IF NOT EXISTS api_usage_metrics (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                        session_id TEXT NOT NULL,
                        node_id TEXT NOT NULL,
                        workflow_id TEXT,
                        service TEXT NOT NULL,
                        operation TEXT NOT NULL,
                        endpoint TEXT NOT NULL,
                        resource_count INTEGER DEFAULT 1,
                        cost REAL DEFAULT 0.0
                    )
                """)
                )
                await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_api_usage_session ON api_usage_metrics(session_id)"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS idx_api_usage_service ON api_usage_metrics(service)"))
                logger.info("Ensured api_usage_metrics table exists")

                # Migrate gmail_connections to google_connections
                # Check if old table exists and new table doesn't
                result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='gmail_connections'"))
                old_table_exists = result.fetchone() is not None

                result = await conn.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='google_connections'"))
                new_table_exists = result.fetchone() is not None

                if old_table_exists and not new_table_exists:
                    await conn.execute(text("ALTER TABLE gmail_connections RENAME TO google_connections"))
                    logger.info("Migrated gmail_connections table to google_connections")

        except Exception as e:
            logger.warning(f"Migration check failed (table may not exist yet): {e}")

    async def _migrate_agent_teams(self):
        """Add execution identity columns used by durable delegation."""
        try:
            async with self.engine.begin() as conn:
                result = await conn.execute(text("PRAGMA table_info(agent_teams)"))
                columns = {row[1] for row in result.fetchall()}
                if columns and "execution_id" not in columns:
                    await conn.execute(text("ALTER TABLE agent_teams ADD COLUMN execution_id VARCHAR(255)"))
                if columns and "root_execution_id" not in columns:
                    await conn.execute(text("ALTER TABLE agent_teams ADD COLUMN root_execution_id VARCHAR(255)"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_agent_teams_execution_id ON agent_teams(execution_id)"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_agent_teams_root_execution_id ON agent_teams(root_execution_id)"))
                await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_team_execution_lead ON agent_teams(execution_id, team_lead_node_id) WHERE execution_id IS NOT NULL"))

                result = await conn.execute(text("PRAGMA table_info(agent_messages)"))
                message_columns = {row[1] for row in result.fetchall()}
                if message_columns and "event_id" not in message_columns:
                    await conn.execute(text("ALTER TABLE agent_messages ADD COLUMN event_id VARCHAR(255)"))
                await conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ix_agent_messages_event_id ON agent_messages(event_id) WHERE event_id IS NOT NULL"))

                result = await conn.execute(text("PRAGMA table_info(team_tasks)"))
                task_columns = {row[1] for row in result.fetchall()}
                additions = {
                    "workflow_id": "VARCHAR(255)", "execution_id": "VARCHAR(255)",
                    "root_execution_id": "VARCHAR(255)", "parent_agent_id": "VARCHAR(255)",
                    "mission": "VARCHAR(10000)", "context": "JSON",
                    "acceptance_criteria": "JSON", "queue_sequence": "INTEGER DEFAULT 0",
                    "revision": "INTEGER DEFAULT 0", "current_attempt": "INTEGER DEFAULT 0",
                    "child_workflow_id": "VARCHAR(500)", "child_run_id": "VARCHAR(255)",
                    "trace_id": "VARCHAR(255)", "cancellation_requested": "BOOLEAN DEFAULT 0",
                    "cancellation_reason": "VARCHAR(2000)", "usage": "JSON",
                }
                for column, definition in additions.items():
                    if task_columns and column not in task_columns:
                        await conn.execute(text(f"ALTER TABLE team_tasks ADD COLUMN {column} {definition}"))
                if task_columns:
                    await conn.execute(text("UPDATE team_tasks SET status='queued' WHERE status='pending'"))
                    await conn.execute(text("UPDATE team_tasks SET status='running' WHERE status='in_progress'"))
                    await conn.execute(text("UPDATE team_tasks SET status='submitted' WHERE status='completed'"))
                    await conn.execute(text("UPDATE team_tasks SET status='cancelled' WHERE status='skipped'"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_team_tasks_execution_id ON team_tasks(execution_id)"))
                await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_team_tasks_root_execution_id ON team_tasks(root_execution_id)"))
        except Exception as e:
            logger.warning(f"Agent-team migration check failed: {e}")

    async def shutdown(self):
        """Close database connections."""
        if self.engine:
            await self.engine.dispose()
            logger.info("Database connections closed")

    @asynccontextmanager
    async def get_session(self):
        """Get async database session."""
        if not self.async_session:
            raise RuntimeError("Database not initialized")

        async with self.async_session() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    async def run_runtime_mutation(
        self,
        *,
        resource_type: str,
        resource_id: str,
        operation: str,
        mutate: RuntimeMutationCallback,
        mutation_id: Optional[str] = None,
    ) -> Tuple[Dict[str, Any], bool]:
        """Run one shared-state mutation under a reserved write transaction.

        SQLite's normal deferred transactions allow two workers to read the
        same JSON value before either writes it.  ``BEGIN IMMEDIATE`` obtains
        the write reservation *before* the read, serialising the complete
        read/modify/write cycle.  When ``mutation_id`` is supplied, the
        durable ledger row is committed in that same transaction and a retry
        returns the original result with ``applied=False``.

        The callback receives the transaction's :class:`AsyncSession` and
        must not commit it.  This method also works with non-SQLite test or
        future production databases, using their regular transaction begin.
        """
        if not self.async_session:
            raise RuntimeError("Database not initialized")

        async with self.async_session() as session:
            try:
                if self.engine is not None and self.engine.dialect.name == "sqlite":
                    await session.execute(text("BEGIN IMMEDIATE"))
                else:
                    await session.begin()

                if mutation_id:
                    found = await session.execute(
                        select(RuntimeMutation).where(
                            RuntimeMutation.mutation_id == mutation_id,
                            RuntimeMutation.resource_type == resource_type,
                            RuntimeMutation.resource_id == resource_id,
                        )
                    )
                    existing = found.scalar_one_or_none()
                    if existing is not None:
                        result = deepcopy(existing.result or {})
                        await session.rollback()
                        return result, False

                result_or_awaitable = mutate(session)
                if inspect.isawaitable(result_or_awaitable):
                    result = await result_or_awaitable
                else:
                    result = result_or_awaitable
                result = deepcopy(result or {})

                if mutation_id:
                    session.add(
                        RuntimeMutation(
                            mutation_id=mutation_id,
                            resource_type=resource_type,
                            resource_id=resource_id,
                            operation=operation,
                            result=result,
                        )
                    )

                await session.commit()
                return result, True
            except Exception:
                await session.rollback()
                raise

    async def mutate_node_parameters_atomic(
        self,
        node_id: str,
        mutator: ParameterMutator,
        *,
        mutation_id: Optional[str] = None,
        operation: str = "update",
    ) -> Tuple[Dict[str, Any], Dict[str, Any], bool]:
        """Atomically transform one node's parameters.

        ``mutator`` receives an isolated copy and may return either the new
        parameter dict or ``(new_parameters, result_metadata)``.  The final
        persisted parameters, metadata, and whether this call applied the
        write are returned.
        """

        async def _mutate(session: AsyncSession) -> Dict[str, Any]:
            selected = await session.execute(
                select(NodeParameter).where(NodeParameter.node_id == node_id)
            )
            row = selected.scalar_one_or_none()
            current = deepcopy(row.parameters if row is not None else {})
            transformed = mutator(current)
            if isinstance(transformed, tuple):
                new_parameters, metadata = transformed
            else:
                new_parameters, metadata = transformed, {}
            if not isinstance(new_parameters, dict):
                raise TypeError("node parameter mutator must return a dict")
            if row is None:
                session.add(
                    NodeParameter(
                        node_id=node_id,
                        parameters=deepcopy(new_parameters),
                    )
                )
            else:
                row.parameters = deepcopy(new_parameters)
                row.updated_at = datetime.now(timezone.utc)
            return deepcopy(metadata or {})

        metadata, applied = await self.run_runtime_mutation(
            resource_type="node_parameters",
            resource_id=node_id,
            operation=operation,
            mutation_id=mutation_id,
            mutate=_mutate,
        )
        parameters = await self.get_node_parameters(node_id) or {}
        return parameters, metadata, applied

    async def mutate_workflow_data_atomic(
        self,
        workflow_id: str,
        mutator: ParameterMutator,
        *,
        mutation_id: Optional[str] = None,
        operation: str = "update",
    ) -> Tuple[Optional[Workflow], Dict[str, Any], bool]:
        """Atomically transform ``Workflow.data`` without stale overwrites."""

        async def _mutate(session: AsyncSession) -> Dict[str, Any]:
            selected = await session.execute(
                select(Workflow).where(Workflow.id == workflow_id)
            )
            workflow = selected.scalar_one_or_none()
            if workflow is None:
                return {"found": False}
            transformed = mutator(deepcopy(workflow.data or {}))
            if isinstance(transformed, tuple):
                new_data, metadata = transformed
            else:
                new_data, metadata = transformed, {}
            if not isinstance(new_data, dict):
                raise TypeError("workflow data mutator must return a dict")
            workflow.data = deepcopy(new_data)
            workflow.updated_at = datetime.now(timezone.utc)
            return {"found": True, **deepcopy(metadata or {})}

        metadata, applied = await self.run_runtime_mutation(
            resource_type="workflow",
            resource_id=workflow_id,
            operation=operation,
            mutation_id=mutation_id,
            mutate=_mutate,
        )
        workflow = await self.get_workflow(workflow_id)
        return workflow, metadata, applied

    # ============================================================================
    # Node Parameters
    # ============================================================================

    async def save_node_parameters(self, node_id: str, parameters: Dict[str, Any]) -> bool:
        """Save or update node parameters."""
        try:
            async with self.get_session() as session:
                # Try to get existing parameter
                stmt = select(NodeParameter).where(NodeParameter.node_id == node_id)
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    existing.parameters = parameters
                else:
                    existing = NodeParameter(node_id=node_id, parameters=parameters)
                    session.add(existing)

                await session.commit()
                return True

        except Exception as e:
            logger.error("Failed to save node parameters", node_id=node_id, error=str(e))
            return False

    async def get_node_parameters(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Get node parameters."""
        try:
            async with self.get_session() as session:
                stmt = select(NodeParameter).where(NodeParameter.node_id == node_id)
                result = await session.execute(stmt)
                parameter = result.scalar_one_or_none()

                return parameter.parameters if parameter else None

        except Exception as e:
            logger.error("Failed to get node parameters", node_id=node_id, error=str(e))
            return None

    async def delete_node_parameters(self, node_id: str) -> bool:
        """Delete node parameters."""
        try:
            async with self.get_session() as session:
                stmt = select(NodeParameter).where(NodeParameter.node_id == node_id)
                result = await session.execute(stmt)
                parameter = result.scalar_one_or_none()

                if parameter:
                    await session.delete(parameter)
                    await session.commit()

                return True

        except Exception as e:
            logger.error("Failed to delete node parameters", node_id=node_id, error=str(e))
            return False

    # ============================================================================
    # Workflows
    # ============================================================================

    async def save_workflow(
        self,
        workflow_id: str,
        name: str,
        slug: str,
        data: Dict[str, Any],
        description: Optional[str] = None,
    ) -> bool:
        """Save or update workflow.

        ``slug`` is required for new rows and is updated on every save
        for existing rows (so a rename routed through this path stays
        consistent). Callers compute the slug via
        :func:`services.workflow_naming.next_available_slug` before
        calling this method — the unique constraint on the column is
        the final check against collision.
        """
        try:
            async with self.get_session() as session:
                stmt = select(Workflow).where(Workflow.id == workflow_id)
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    existing.name = name
                    existing.slug = slug
                    existing.description = description
                    existing.data = data
                else:
                    existing = Workflow(id=workflow_id, name=name, slug=slug, description=description, data=data)
                    session.add(existing)

                await session.commit()
                return True

        except Exception as e:
            logger.error("Failed to save workflow", workflow_id=workflow_id, error=str(e))
            return False

    async def get_workflow(self, workflow_id: str) -> Optional[Workflow]:
        """Get workflow by ID."""
        try:
            async with self.get_session() as session:
                stmt = select(Workflow).where(Workflow.id == workflow_id)
                result = await session.execute(stmt)
                return result.scalar_one_or_none()

        except Exception as e:
            logger.error("Failed to get workflow", workflow_id=workflow_id, error=str(e))
            return None

    async def get_all_workflows(self) -> List[Workflow]:
        """Get all workflows."""
        try:
            async with self.get_session() as session:
                stmt = select(Workflow).order_by(Workflow.updated_at.desc())
                result = await session.execute(stmt)
                return result.scalars().all()

        except Exception as e:
            logger.error("Failed to get all workflows", error=str(e))
            return []

    async def list_workflow_slugs(self) -> List[tuple]:
        """Cheap projection of ``(id, slug)`` pairs.

        Consumed by :func:`services.workflow_naming.next_available_slug`
        to find the lowest free ``_<N>`` suffix for a given slug base.
        Returns an empty list on error so the slug allocator falls
        through to ``_1``.
        """
        try:
            async with self.get_session() as session:
                stmt = select(Workflow.id, Workflow.slug)
                result = await session.execute(stmt)
                return list(result.all())
        except Exception as e:
            logger.error("Failed to list workflow slugs", error=str(e))
            return []

    async def rename_workflow(self, workflow_id: str, new_name: str, new_slug: str) -> bool:
        """Atomically update display name + slug. ``id`` (PK) never moves.

        Cross-table FK references (``Execution.workflow_id``) and soft
        refs (``ConsoleLog`` / ``TokenUsageMetric`` / etc.) all key on
        the UUID, so renaming is a single-row UPDATE — no cascade
        needed. The unique constraint on ``slug`` is the collision
        guard; the caller must pre-allocate a free slug via
        :func:`services.workflow_naming.next_available_slug`.
        """
        try:
            async with self.get_session() as session:
                stmt = select(Workflow).where(Workflow.id == workflow_id)
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()
                if not existing:
                    return False
                existing.name = new_name
                existing.slug = new_slug
                await session.commit()
                return True
        except Exception as e:
            logger.error("Failed to rename workflow", workflow_id=workflow_id, error=str(e))
            return False

    async def delete_workflow(self, workflow_id: str) -> bool:
        """Delete workflow."""
        try:
            async with self.get_session() as session:
                stmt = select(Workflow).where(Workflow.id == workflow_id)
                result = await session.execute(stmt)
                workflow = result.scalar_one_or_none()

                if workflow:
                    await session.delete(workflow)
                    await session.commit()

                return True

        except Exception as e:
            logger.error("Failed to delete workflow", workflow_id=workflow_id, error=str(e))
            return False

    # ============================================================================
    # Executions
    # ============================================================================

    async def save_execution(
        self,
        execution_id: str,
        workflow_id: str,
        node_id: str,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
        execution_time: Optional[float] = None,
    ) -> bool:
        """Save execution result."""
        try:
            async with self.get_session() as session:
                execution = Execution(
                    id=execution_id,
                    workflow_id=workflow_id,
                    node_id=node_id,
                    status=status,
                    result=result,
                    error=error,
                    execution_time=execution_time,
                )
                session.add(execution)
                await session.commit()
                return True

        except Exception as e:
            logger.error("Failed to save execution", execution_id=execution_id, error=str(e))
            return False

    async def get_execution(self, execution_id: str) -> Optional[Execution]:
        """Get execution by ID."""
        try:
            async with self.get_session() as session:
                stmt = select(Execution).where(Execution.id == execution_id)
                result = await session.execute(stmt)
                return result.scalar_one_or_none()

        except Exception as e:
            logger.error("Failed to get execution", execution_id=execution_id, error=str(e))
            return None

    # ============================================================================
    # API Keys
    # ============================================================================

    async def save_api_key(
        self, key_id: str, provider: str, session_id: str, key_encrypted: str, key_hash: str, models: Optional[List[str]] = None
    ) -> bool:
        """Save encrypted API key."""
        logger.info(f"Database save_api_key called with key_id: {key_id}, provider: {provider}")

        try:
            async with self.get_session() as session:
                api_key = APIKey(
                    id=key_id,
                    provider=provider,
                    session_id=session_id,
                    key_encrypted=key_encrypted,
                    key_hash=key_hash,
                    models={"models": models} if models else None,
                    last_validated=datetime.now(timezone.utc),
                )
                session.add(api_key)
                await session.commit()
                logger.info(f"Successfully saved new API key: {key_id}")
                return True

        except IntegrityError as e:
            logger.info(f"API key {key_id} already exists, attempting update. Error: {str(e)}")
            # Key already exists, update it
            try:
                async with self.get_session() as session:
                    stmt = select(APIKey).where(APIKey.id == key_id)
                    result = await session.execute(stmt)
                    existing = result.scalar_one_or_none()

                    if existing:
                        logger.info(f"Found existing API key {key_id}, updating...")
                        existing.key_encrypted = key_encrypted
                        existing.key_hash = key_hash
                        existing.models = {"models": models} if models else None
                        existing.last_validated = datetime.now(timezone.utc)
                        await session.commit()
                        logger.info(f"Successfully updated API key: {key_id}")
                        return True
                    else:
                        logger.error(f"Could not find existing API key {key_id} for update")
                        return False
            except Exception as update_e:
                logger.error(f"Failed to update API key {key_id}", error=str(update_e))
                return False

        except Exception as e:
            logger.error("Failed to save API key", provider=provider, error=str(e))
            import traceback

            logger.error("Full traceback", traceback=traceback.format_exc())
            return False

    async def get_api_key(self, key_id: str) -> Optional[APIKey]:
        """Get API key by ID."""
        try:
            async with self.get_session() as session:
                stmt = select(APIKey).where(APIKey.id == key_id)
                result = await session.execute(stmt)
                return result.scalar_one_or_none()

        except Exception as e:
            logger.error("Failed to get API key", key_id=key_id, error=str(e))
            return None

    async def get_api_key_by_provider(self, provider: str, session_id: str = "default") -> Optional[APIKey]:
        """Get API key by provider and session."""
        try:
            async with self.get_session() as session:
                stmt = select(APIKey).where(APIKey.provider == provider, APIKey.session_id == session_id, APIKey.is_valid)
                result = await session.execute(stmt)
                return result.scalar_one_or_none()

        except Exception as e:
            logger.error("Failed to get API key by provider", provider=provider, error=str(e))
            return None

    async def delete_api_key(self, provider: str, session_id: str = "default") -> bool:
        """Delete API key."""
        try:
            async with self.get_session() as session:
                stmt = select(APIKey).where(APIKey.provider == provider, APIKey.session_id == session_id)
                result = await session.execute(stmt)
                api_key = result.scalar_one_or_none()

                if api_key:
                    await session.delete(api_key)
                    await session.commit()
                    logger.debug("API key deleted", provider=provider, session_id=session_id)

                return True

        except Exception as e:
            logger.error("Failed to delete API key", provider=provider, error=str(e))
            return False

    # ============================================================================
    # API Key Validation Cache
    # ============================================================================

    async def save_api_key_validation(self, key_hash: str) -> bool:
        """Save API key validation status."""
        try:
            async with self.get_session() as session:
                validation = APIKeyValidation(key_hash=key_hash, validated=True)
                session.add(validation)
                await session.commit()
                return True

        except IntegrityError:
            # Already exists, update timestamp
            async with self.get_session() as session:
                stmt = select(APIKeyValidation).where(APIKeyValidation.key_hash == key_hash)
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    existing.timestamp = datetime.now(timezone.utc)
                    await session.commit()
                    return True
                return False

        except Exception as e:
            logger.error("Failed to save API key validation", key_hash=key_hash, error=str(e))
            return False

    async def is_api_key_validated(self, key_hash: str) -> bool:
        """Check if API key is validated."""
        try:
            async with self.get_session() as session:
                stmt = select(APIKeyValidation).where(APIKeyValidation.key_hash == key_hash)
                result = await session.execute(stmt)
                validation = result.scalar_one_or_none()
                return validation is not None and validation.validated

        except Exception as e:
            logger.error("Failed to check API key validation", key_hash=key_hash, error=str(e))
            return False

    # ============================================================================
    # Node Outputs
    # ============================================================================

    async def save_node_output(self, node_id: str, session_id: str, output_name: str, data: Dict[str, Any]) -> bool:
        """Save or update node output."""
        try:
            async with self.get_session() as session:
                # Try to get existing output
                stmt = select(NodeOutput).where(
                    NodeOutput.node_id == node_id, NodeOutput.session_id == session_id, NodeOutput.output_name == output_name
                )
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                action = "updated"
                if existing:
                    existing.data = data
                else:
                    action = "inserted"
                    existing = NodeOutput(node_id=node_id, session_id=session_id, output_name=output_name, data=data)
                    session.add(existing)

                await session.commit()
                logger.debug("[DB] Node output saved", action=action, node_id=node_id, session_id=session_id, output_name=output_name)
                return True

        except Exception as e:
            logger.error("Failed to save node output", node_id=node_id, error=str(e))
            import traceback

            traceback.print_exc()
            return False

    async def get_node_output(self, node_id: str, session_id: str = "default", output_name: str = "output_0") -> Optional[Dict[str, Any]]:
        """Get node output data."""
        try:
            async with self.get_session() as session:
                stmt = select(NodeOutput).where(
                    NodeOutput.node_id == node_id, NodeOutput.session_id == session_id, NodeOutput.output_name == output_name
                )
                result = await session.execute(stmt)
                output = result.scalar_one_or_none()

                return output.data if output else None

        except Exception as e:
            logger.error("Failed to get node output", node_id=node_id, error=str(e))
            return None

    async def get_node_output_by_session(self, session_id: str, output_name: str = "output_0") -> Optional[Dict[str, Any]]:
        """Get node output by session_id only (for delegation result lookup).

        Used when node_id is unknown but session_id encodes the lookup key.
        """
        try:
            async with self.get_session() as session:
                stmt = select(NodeOutput).where(NodeOutput.session_id == session_id, NodeOutput.output_name == output_name)
                result = await session.execute(stmt)
                output = result.scalar_one_or_none()

                return {"data": output.data} if output else None

        except Exception as e:
            logger.error("Failed to get node output by session", session_id=session_id, error=str(e))
            return None

    async def delete_node_output(self, node_id: str) -> int:
        """Delete all outputs for a node (any session). Returns count deleted."""
        try:
            async with self.get_session() as session:
                stmt = select(NodeOutput).where(NodeOutput.node_id == node_id)
                result = await session.execute(stmt)
                outputs = result.scalars().all()

                count = len(outputs)
                for output in outputs:
                    await session.delete(output)

                await session.commit()
                logger.info("Deleted node outputs", node_id=node_id, count=count)
                return count

        except Exception as e:
            logger.error("Failed to delete node output", node_id=node_id, error=str(e))
            return 0

    async def clear_session_outputs(self, session_id: str = "default") -> int:
        """Clear all outputs for a session. Returns count deleted."""
        try:
            async with self.get_session() as session:
                stmt = select(NodeOutput).where(NodeOutput.session_id == session_id)
                result = await session.execute(stmt)
                outputs = result.scalars().all()

                count = len(outputs)
                for output in outputs:
                    await session.delete(output)

                await session.commit()
                logger.info("Cleared session outputs", session_id=session_id, count=count)
                return count

        except Exception as e:
            logger.error("Failed to clear session outputs", session_id=session_id, error=str(e))
            return 0

    # ============================================================================
    # Conversation Messages (AI Memory)
    # ============================================================================

    async def add_conversation_message(self, session_id: str, role: str, content: str) -> bool:
        """Add a message to conversation history."""
        try:
            async with self.get_session() as session:
                message = ConversationMessage(session_id=session_id, role=role, content=content)
                session.add(message)
                await session.commit()
                logger.info(f"[Memory] Added {role} message to session '{session_id}'")
                return True

        except Exception as e:
            logger.error("Failed to add conversation message", session_id=session_id, error=str(e))
            return False

    async def get_conversation_messages(self, session_id: str, window_size: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get conversation messages, optionally limited to last N."""
        try:
            async with self.get_session() as session:
                stmt = (
                    select(ConversationMessage)
                    .where(ConversationMessage.session_id == session_id)
                    .order_by(ConversationMessage.created_at.asc())
                )

                result = await session.execute(stmt)
                messages = result.scalars().all()

                # Apply window limit if specified
                if window_size and window_size > 0:
                    messages = messages[-window_size:]

                return [{"role": m.role, "content": m.content, "timestamp": m.created_at.isoformat()} for m in messages]

        except Exception as e:
            logger.error("Failed to get conversation messages", session_id=session_id, error=str(e))
            return []

    async def clear_conversation(self, session_id: str) -> int:
        """Clear all messages in a conversation session. Returns count deleted."""
        try:
            async with self.get_session() as session:
                stmt = select(ConversationMessage).where(ConversationMessage.session_id == session_id)
                result = await session.execute(stmt)
                messages = result.scalars().all()

                count = len(messages)
                for message in messages:
                    await session.delete(message)

                await session.commit()
                logger.info(f"[Memory] Cleared {count} messages from session '{session_id}'")
                return count

        except Exception as e:
            logger.error("Failed to clear conversation", session_id=session_id, error=str(e))
            return 0

    async def get_all_conversation_sessions(self) -> List[Dict[str, Any]]:
        """Get info about all conversation sessions."""
        try:
            async with self.get_session() as session:
                # Get distinct session IDs with message count
                from sqlalchemy import func as sql_func

                stmt = select(
                    ConversationMessage.session_id,
                    sql_func.count(ConversationMessage.id).label("message_count"),
                    sql_func.min(ConversationMessage.created_at).label("created_at"),
                ).group_by(ConversationMessage.session_id)

                result = await session.execute(stmt)
                rows = result.all()

                return [
                    {
                        "session_id": row.session_id,
                        "message_count": row.message_count,
                        "created_at": row.created_at.isoformat() if row.created_at else None,
                    }
                    for row in rows
                ]

        except Exception as e:
            logger.error("Failed to get conversation sessions", error=str(e))
            return []

    # ============================================================================
    # Chat Messages (Console Panel persistence)
    # ============================================================================

    async def add_chat_message(self, session_id: str, role: str, message: str) -> bool:
        """Add a chat message to the console panel history."""
        try:
            async with self.get_session() as session:
                chat_msg = ChatMessage(session_id=session_id, role=role, message=message)
                session.add(chat_msg)
                await session.commit()
                logger.debug(f"[Chat] Added {role} message to session '{session_id}'")
                return True

        except Exception as e:
            logger.error("Failed to add chat message", session_id=session_id, error=str(e))
            return False

    async def get_chat_messages(self, session_id: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get chat messages for a session, optionally limited to last N."""
        try:
            async with self.get_session() as session:
                stmt = select(ChatMessage).where(ChatMessage.session_id == session_id).order_by(ChatMessage.created_at.asc())

                result = await session.execute(stmt)
                messages = result.scalars().all()

                # Apply limit if specified
                if limit and limit > 0:
                    messages = messages[-limit:]

                return [{"role": m.role, "message": m.message, "timestamp": m.created_at.isoformat()} for m in messages]

        except Exception as e:
            logger.error("Failed to get chat messages", session_id=session_id, error=str(e))
            return []

    async def clear_chat_messages(self, session_id: str) -> int:
        """Clear all chat messages for a session. Returns count deleted."""
        try:
            async with self.get_session() as session:
                stmt = select(ChatMessage).where(ChatMessage.session_id == session_id)
                result = await session.execute(stmt)
                messages = result.scalars().all()

                count = len(messages)
                for message in messages:
                    await session.delete(message)

                await session.commit()
                logger.info(f"[Chat] Cleared {count} messages from session '{session_id}'")
                return count

        except Exception as e:
            logger.error("Failed to clear chat messages", session_id=session_id, error=str(e))
            return 0

    async def get_chat_sessions(self) -> List[Dict[str, Any]]:
        """Get list of all chat sessions with message counts."""
        try:
            async with self.get_session() as session:
                from sqlalchemy import func as sa_func

                stmt = (
                    select(
                        ChatMessage.session_id,
                        sa_func.count(ChatMessage.id).label("message_count"),
                        sa_func.max(ChatMessage.created_at).label("last_message_at"),
                    )
                    .group_by(ChatMessage.session_id)
                    .order_by(sa_func.max(ChatMessage.created_at).desc())
                )

                result = await session.execute(stmt)
                rows = result.all()

                return [
                    {
                        "session_id": row.session_id,
                        "message_count": row.message_count,
                        "last_message_at": row.last_message_at.isoformat() if row.last_message_at else None,
                    }
                    for row in rows
                ]

        except Exception as e:
            logger.error("Failed to get chat sessions", error=str(e))
            return []

    # ============================================================================
    # Console Logs (Console Panel persistence)
    # ============================================================================

    async def add_console_log(self, log_data: Dict[str, Any]) -> bool:
        """Add a console log entry to the database."""
        from models.database import ConsoleLog
        import json

        try:
            async with self.get_session() as session:
                console_log = ConsoleLog(
                    node_id=log_data.get("node_id", ""),
                    label=log_data.get("label", ""),
                    workflow_id=log_data.get("workflow_id"),
                    data=json.dumps(log_data.get("data", {})),
                    formatted=log_data.get("formatted", ""),
                    format=log_data.get("format", "text"),
                    source_node_id=log_data.get("source_node_id"),
                    source_node_type=log_data.get("source_node_type"),
                    source_node_label=log_data.get("source_node_label"),
                )
                session.add(console_log)
                await session.commit()
                logger.debug(f"[Console] Added log from node '{log_data.get('node_id')}'")
                return True

        except Exception as e:
            logger.error("Failed to add console log", error=str(e))
            return False

    async def get_console_logs(self, limit: int = 100, workflow_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get console logs, optionally limited and scoped to a workflow.

        ``workflow_id=None`` returns the global stream (legacy behavior, used
        by the initial WebSocket-bootstrap fetch before any workflow is
        selected). When a workflow is open, the frontend passes its id so
        the panel only shows logs from that workflow.
        """
        from models.database import ConsoleLog
        import json

        try:
            async with self.get_session() as session:
                stmt = select(ConsoleLog).order_by(ConsoleLog.created_at.desc()).limit(limit)
                if workflow_id is not None:
                    stmt = stmt.where(ConsoleLog.workflow_id == workflow_id)

                result = await session.execute(stmt)
                logs = result.scalars().all()

                # Return in reverse-chronological order (newest first)
                # Matches real-time prepend convention in WebSocketContext
                return [
                    {
                        "node_id": log.node_id,
                        "label": log.label,
                        "workflow_id": log.workflow_id,
                        "data": json.loads(log.data) if log.data else {},
                        "formatted": log.formatted,
                        "format": log.format,
                        "source_node_id": log.source_node_id,
                        "source_node_type": log.source_node_type,
                        "source_node_label": log.source_node_label,
                        "timestamp": log.created_at.isoformat(),
                    }
                    for log in logs
                ]

        except Exception as e:
            logger.error("Failed to get console logs", error=str(e))
            return []

    async def clear_console_logs(self, workflow_id: Optional[str] = None) -> int:
        """Clear console logs, optionally scoped to a single workflow.

        ``workflow_id=None`` clears every row (used for the global "Clear"
        action and one-shot maintenance). When a workflow is open, the
        frontend passes its id so other workflows' history is preserved.
        """
        from models.database import ConsoleLog

        try:
            async with self.get_session() as session:
                stmt = select(ConsoleLog)
                if workflow_id is not None:
                    stmt = stmt.where(ConsoleLog.workflow_id == workflow_id)
                result = await session.execute(stmt)
                logs = result.scalars().all()

                count = len(logs)
                for log in logs:
                    await session.delete(log)

                await session.commit()
                logger.info(f"[Console] Cleared {count} console logs" + (f" (workflow={workflow_id})" if workflow_id else " (all)"))
                return count

        except Exception as e:
            logger.error("Failed to clear console logs", error=str(e))
            return 0

    async def cleanup_old_console_logs(self, keep: int = 1000) -> int:
        """Keep only the most recent N console logs. Returns count deleted."""
        from models.database import ConsoleLog

        try:
            async with self.get_session() as session:
                # Count total logs
                count_stmt = select(func.count()).select_from(ConsoleLog)
                total_result = await session.execute(count_stmt)
                total = total_result.scalar() or 0

                if total <= keep:
                    return 0

                # Get IDs of logs to keep (most recent)
                keep_stmt = select(ConsoleLog.id).order_by(ConsoleLog.created_at.desc()).limit(keep)
                keep_result = await session.execute(keep_stmt)
                keep_ids = {row[0] for row in keep_result.fetchall()}

                # Delete logs not in keep list
                delete_stmt = select(ConsoleLog).where(ConsoleLog.id.notin_(keep_ids))
                result = await session.execute(delete_stmt)
                old_logs = result.scalars().all()

                count = len(old_logs)
                for log in old_logs:
                    await session.delete(log)

                await session.commit()
                if count > 0:
                    logger.info("Cleaned up old console logs", deleted=count, kept=keep)
                return count

        except Exception as e:
            logger.error("Failed to cleanup old console logs", error=str(e))
            return 0

    # ============================================================================
    # Cache Entries (SQLite-backed Redis alternative)
    # ============================================================================

    async def get_cache_entry(self, key: str) -> Optional[str]:
        """Get cache value by key. Returns None if expired or not found."""
        import time

        try:
            async with self.get_session() as session:
                stmt = select(CacheEntry).where(CacheEntry.key == key)
                result = await session.execute(stmt)
                entry = result.scalar_one_or_none()

                if not entry:
                    return None

                # Check expiration
                if entry.expires_at and entry.expires_at < time.time():
                    # Entry expired - delete it
                    await session.delete(entry)
                    await session.commit()
                    return None

                return entry.value

        except Exception as e:
            logger.error("Failed to get cache entry", key=key, error=str(e))
            return None

    async def set_cache_entry(self, key: str, value: str, ttl: Optional[int] = None) -> bool:
        """Set cache value with optional TTL in seconds."""
        import time

        try:
            expires_at = time.time() + ttl if ttl else None

            async with self.get_session() as session:
                # Try to get existing entry
                stmt = select(CacheEntry).where(CacheEntry.key == key)
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    existing.value = value
                    existing.expires_at = expires_at
                    existing.created_at = time.time()
                else:
                    entry = CacheEntry(key=key, value=value, expires_at=expires_at, created_at=time.time())
                    session.add(entry)

                await session.commit()
                return True

        except Exception as e:
            logger.error("Failed to set cache entry", key=key, error=str(e))
            return False

    async def delete_cache_entry(self, key: str) -> bool:
        """Delete cache entry by key."""
        try:
            async with self.get_session() as session:
                stmt = select(CacheEntry).where(CacheEntry.key == key)
                result = await session.execute(stmt)
                entry = result.scalar_one_or_none()

                if entry:
                    await session.delete(entry)
                    await session.commit()

                return True

        except Exception as e:
            logger.error("Failed to delete cache entry", key=key, error=str(e))
            return False

    async def delete_cache_pattern(self, pattern: str) -> int:
        """Delete cache entries matching pattern (uses SQL LIKE)."""
        try:
            # Convert glob pattern to SQL LIKE pattern
            sql_pattern = pattern.replace("*", "%")

            async with self.get_session() as session:
                stmt = select(CacheEntry).where(CacheEntry.key.like(sql_pattern))
                result = await session.execute(stmt)
                entries = result.scalars().all()

                count = len(entries)
                for entry in entries:
                    await session.delete(entry)

                await session.commit()
                logger.debug("Deleted cache entries", pattern=pattern, count=count)
                return count

        except Exception as e:
            logger.error("Failed to delete cache pattern", pattern=pattern, error=str(e))
            return 0

    async def cleanup_expired_cache(self) -> int:
        """Remove all expired cache entries. Returns count deleted."""
        import time

        try:
            async with self.get_session() as session:
                stmt = select(CacheEntry).where(CacheEntry.expires_at.isnot(None), CacheEntry.expires_at < time.time())
                result = await session.execute(stmt)
                entries = result.scalars().all()

                count = len(entries)
                for entry in entries:
                    await session.delete(entry)

                await session.commit()
                if count > 0:
                    logger.info("Cleaned up expired cache entries", count=count)
                return count

        except Exception as e:
            logger.error("Failed to cleanup expired cache", error=str(e))
            return 0

    async def cleanup_old_cache(self, max_age_hours: int = 24) -> int:
        """Remove cache entries older than max_age_hours. Returns count deleted."""
        import time

        try:
            async with self.get_session() as session:
                cutoff_time = time.time() - (max_age_hours * 3600)
                stmt = select(CacheEntry).where(CacheEntry.created_at < cutoff_time)
                result = await session.execute(stmt)
                entries = result.scalars().all()

                count = len(entries)
                for entry in entries:
                    await session.delete(entry)

                await session.commit()
                if count > 0:
                    logger.info("Cleaned up old cache entries", count=count, max_age_hours=max_age_hours)
                return count

        except Exception as e:
            logger.error("Failed to cleanup old cache", error=str(e))
            return 0

    async def cache_exists(self, key: str) -> bool:
        """Check if cache key exists and is not expired."""
        import time

        try:
            async with self.get_session() as session:
                stmt = select(CacheEntry).where(CacheEntry.key == key)
                result = await session.execute(stmt)
                entry = result.scalar_one_or_none()

                if not entry:
                    return False

                # Check expiration
                if entry.expires_at and entry.expires_at < time.time():
                    return False

                return True

        except Exception as e:
            logger.error("Failed to check cache exists", key=key, error=str(e))
            return False

    # ============================================================================
    # Tool Schemas (Source of truth for tool node configurations)
    # ============================================================================

    async def save_tool_schema(
        self,
        node_id: str,
        tool_name: str,
        tool_description: str,
        schema_config: Dict[str, Any],
        connected_services: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Save or update tool schema for a node."""
        try:
            async with self.get_session() as session:
                # Try to get existing schema
                stmt = select(ToolSchema).where(ToolSchema.node_id == node_id)
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                action = "updated"
                if existing:
                    existing.tool_name = tool_name
                    existing.tool_description = tool_description
                    existing.schema_config = schema_config
                    existing.connected_services = connected_services
                else:
                    action = "created"
                    existing = ToolSchema(
                        node_id=node_id,
                        tool_name=tool_name,
                        tool_description=tool_description,
                        schema_config=schema_config,
                        connected_services=connected_services,
                    )
                    session.add(existing)

                await session.commit()
                logger.info(f"[DB] Tool schema {action}", node_id=node_id, tool_name=tool_name)
                return True

        except Exception as e:
            logger.error("Failed to save tool schema", node_id=node_id, error=str(e))
            return False

    async def get_tool_schema(self, node_id: str) -> Optional[Dict[str, Any]]:
        """Get tool schema for a node."""
        try:
            async with self.get_session() as session:
                stmt = select(ToolSchema).where(ToolSchema.node_id == node_id)
                result = await session.execute(stmt)
                schema = result.scalar_one_or_none()

                if not schema:
                    return None

                pending_count = sum(1 for t in tasks if t.status == "pending")
                active_count = sum(1 for t in tasks if t.status == "in_progress")
                completed_count = sum(1 for t in tasks if t.status == "completed")
                failed_count = sum(1 for t in tasks if t.status == "failed")
                return {
                    "node_id": schema.node_id,
                    "tool_name": schema.tool_name,
                    "tool_description": schema.tool_description,
                    "schema_config": schema.schema_config,
                    "connected_services": schema.connected_services,
                    "created_at": schema.created_at.isoformat() if schema.created_at else None,
                    "updated_at": schema.updated_at.isoformat() if schema.updated_at else None,
                }

        except Exception as e:
            logger.error("Failed to get tool schema", node_id=node_id, error=str(e))
            return None

    async def delete_tool_schema(self, node_id: str) -> bool:
        """Delete tool schema for a node."""
        try:
            async with self.get_session() as session:
                stmt = select(ToolSchema).where(ToolSchema.node_id == node_id)
                result = await session.execute(stmt)
                schema = result.scalar_one_or_none()

                if schema:
                    await session.delete(schema)
                    await session.commit()
                    logger.info("[DB] Tool schema deleted", node_id=node_id)

                return True

        except Exception as e:
            logger.error("Failed to delete tool schema", node_id=node_id, error=str(e))
            return False

    async def get_all_tool_schemas(self) -> List[Dict[str, Any]]:
        """Get all tool schemas."""
        try:
            async with self.get_session() as session:
                stmt = select(ToolSchema).order_by(ToolSchema.updated_at.desc())
                result = await session.execute(stmt)
                schemas = result.scalars().all()

                return [
                    {
                        "node_id": s.node_id,
                        "tool_name": s.tool_name,
                        "tool_description": s.tool_description,
                        "schema_config": s.schema_config,
                        "connected_services": s.connected_services,
                        "updated_at": s.updated_at.isoformat() if s.updated_at else None,
                    }
                    for s in schemas
                ]

        except Exception as e:
            logger.error("Failed to get all tool schemas", error=str(e))
            return []

    # ============================================================================
    # Android Relay Session Persistence
    # ============================================================================

    async def save_android_relay_session(
        self, relay_url: str, api_key: str, device_id: str, device_name: Optional[str] = None, session_token: Optional[str] = None
    ) -> bool:
        """Save Android relay pairing session for auto-reconnect on server restart.

        Args:
            relay_url: WebSocket relay URL
            api_key: API key for relay authentication
            device_id: Paired Android device ID
            device_name: Paired device name
            session_token: Relay session token
        """
        import json

        try:
            session_data = json.dumps(
                {
                    "relay_url": relay_url,
                    "api_key": api_key,
                    "device_id": device_id,
                    "device_name": device_name,
                    "session_token": session_token,
                }
            )
            # No TTL - session persists until explicitly cleared
            return await self.set_cache_entry("android_relay_session", session_data)
        except Exception as e:
            logger.error("Failed to save Android relay session", error=str(e))
            return False

    async def get_android_relay_session(self) -> Optional[Dict[str, Any]]:
        """Get stored Android relay session for auto-reconnect.

        Returns:
            Session data dict or None if not found
        """
        import json

        try:
            value = await self.get_cache_entry("android_relay_session")
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            logger.error("Failed to get Android relay session", error=str(e))
            return None

    async def clear_android_relay_session(self) -> bool:
        """Clear stored Android relay session (on explicit disconnect)."""
        try:
            return await self.delete_cache_entry("android_relay_session")
        except Exception as e:
            logger.error("Failed to clear Android relay session", error=str(e))
            return False

    # ============================================================================
    # User Skills (Custom skills for Zeenie)
    # ============================================================================

    async def create_user_skill(
        self,
        name: str,
        display_name: str,
        description: str,
        instructions: str,
        allowed_tools: Optional[str] = None,
        category: str = "custom",
        icon: str = "star",
        color: str = "#6366F1",
        metadata_json: Optional[Dict[str, Any]] = None,
        created_by: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Create a new user skill."""
        try:
            async with self.get_session() as session:
                skill = UserSkill(
                    name=name,
                    display_name=display_name,
                    description=description,
                    instructions=instructions,
                    allowed_tools=allowed_tools,
                    category=category,
                    icon=icon,
                    color=color,
                    metadata_json=metadata_json,
                    created_by=created_by,
                )
                session.add(skill)
                await session.commit()
                await session.refresh(skill)

                logger.info(f"[DB] Created user skill: {name}")
                return self._skill_to_dict(skill)

        except IntegrityError:
            logger.error(f"User skill with name '{name}' already exists")
            return None
        except Exception as e:
            logger.error("Failed to create user skill", name=name, error=str(e))
            return None

    async def get_user_skill(self, name: str) -> Optional[Dict[str, Any]]:
        """Get user skill by name."""
        try:
            async with self.get_session() as session:
                stmt = select(UserSkill).where(UserSkill.name == name)
                result = await session.execute(stmt)
                skill = result.scalar_one_or_none()

                return self._skill_to_dict(skill) if skill else None

        except Exception as e:
            logger.error("Failed to get user skill", name=name, error=str(e))
            return None

    async def get_user_skill_by_id(self, skill_id: int) -> Optional[Dict[str, Any]]:
        """Get user skill by ID."""
        try:
            async with self.get_session() as session:
                stmt = select(UserSkill).where(UserSkill.id == skill_id)
                result = await session.execute(stmt)
                skill = result.scalar_one_or_none()

                return self._skill_to_dict(skill) if skill else None

        except Exception as e:
            logger.error("Failed to get user skill by id", skill_id=skill_id, error=str(e))
            return None

    async def get_all_user_skills(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """Get all user skills, optionally filtered by active status."""
        try:
            async with self.get_session() as session:
                if active_only:
                    stmt = select(UserSkill).where(UserSkill.is_active).order_by(UserSkill.display_name)
                else:
                    stmt = select(UserSkill).order_by(UserSkill.display_name)

                result = await session.execute(stmt)
                skills = result.scalars().all()

                return [self._skill_to_dict(s) for s in skills]

        except Exception as e:
            logger.error("Failed to get all user skills", error=str(e))
            return []

    async def update_user_skill(
        self,
        name: str,
        display_name: Optional[str] = None,
        description: Optional[str] = None,
        instructions: Optional[str] = None,
        allowed_tools: Optional[str] = None,
        category: Optional[str] = None,
        icon: Optional[str] = None,
        color: Optional[str] = None,
        metadata_json: Optional[Dict[str, Any]] = None,
        is_active: Optional[bool] = None,
    ) -> Optional[Dict[str, Any]]:
        """Update an existing user skill."""
        try:
            async with self.get_session() as session:
                stmt = select(UserSkill).where(UserSkill.name == name)
                result = await session.execute(stmt)
                skill = result.scalar_one_or_none()

                if not skill:
                    logger.error(f"User skill '{name}' not found for update")
                    return None

                # Update only provided fields
                if display_name is not None:
                    skill.display_name = display_name
                if description is not None:
                    skill.description = description
                if instructions is not None:
                    skill.instructions = instructions
                if allowed_tools is not None:
                    skill.allowed_tools = allowed_tools
                if category is not None:
                    skill.category = category
                if icon is not None:
                    skill.icon = icon
                if color is not None:
                    skill.color = color
                if metadata_json is not None:
                    skill.metadata_json = metadata_json
                if is_active is not None:
                    skill.is_active = is_active

                await session.commit()
                await session.refresh(skill)

                logger.info(f"[DB] Updated user skill: {name}")
                return self._skill_to_dict(skill)

        except Exception as e:
            logger.error("Failed to update user skill", name=name, error=str(e))
            return None

    async def delete_user_skill(self, name: str) -> bool:
        """Delete a user skill by name."""
        try:
            async with self.get_session() as session:
                stmt = select(UserSkill).where(UserSkill.name == name)
                result = await session.execute(stmt)
                skill = result.scalar_one_or_none()

                if skill:
                    await session.delete(skill)
                    await session.commit()
                    logger.info(f"[DB] Deleted user skill: {name}")
                    return True

                return False

        except Exception as e:
            logger.error("Failed to delete user skill", name=name, error=str(e))
            return False

    def _skill_to_dict(self, skill: UserSkill) -> Dict[str, Any]:
        """Convert UserSkill model to dictionary."""
        return {
            "id": skill.id,
            "name": skill.name,
            "display_name": skill.display_name,
            "description": skill.description,
            "instructions": skill.instructions,
            "allowed_tools": skill.allowed_tools.split(",") if skill.allowed_tools else [],
            "category": skill.category,
            "icon": skill.icon,
            "color": skill.color,
            "metadata": skill.metadata_json,
            "is_active": skill.is_active,
            "created_by": skill.created_by,
            "created_at": skill.created_at.isoformat() if skill.created_at else None,
            "updated_at": skill.updated_at.isoformat() if skill.updated_at else None,
        }

    # ============================================================================
    # User Settings (UI defaults and preferences)
    # ============================================================================

    async def get_user_settings(self, user_id: str = "default") -> Optional[Dict[str, Any]]:
        """Get user settings. Returns None if not found."""
        try:
            async with self.get_session() as session:
                stmt = select(UserSettings).where(UserSettings.user_id == user_id)
                result = await session.execute(stmt)
                settings = result.scalar_one_or_none()

                if not settings:
                    return None

                return {
                    "user_id": settings.user_id,
                    "auto_save": settings.auto_save,
                    "auto_save_interval": settings.auto_save_interval,
                    "sidebar_default_open": settings.sidebar_default_open,
                    "component_palette_default_open": settings.component_palette_default_open,
                    "console_panel_default_open": settings.console_panel_default_open,
                    "memory_window_size": settings.memory_window_size,
                    "compaction_ratio": settings.compaction_ratio,
                    "examples_loaded": settings.examples_loaded,
                    "onboarding_completed": settings.onboarding_completed,
                    "onboarding_step": settings.onboarding_step,
                    "default_llm_provider": settings.default_llm_provider,
                    "default_llm_model": settings.default_llm_model,
                    "auto_add_skill_for_tools": settings.auto_add_skill_for_tools,
                    "auto_rebind_tools_after_canvas_change": settings.auto_rebind_tools_after_canvas_change,
                    "agent_recursion_limit": settings.agent_recursion_limit,
                    "max_concurrent_subagents": settings.max_concurrent_subagents,
                    "max_delegation_depth": settings.max_delegation_depth,
                    "created_at": settings.created_at.isoformat() if settings.created_at else None,
                    "updated_at": settings.updated_at.isoformat() if settings.updated_at else None,
                }

        except Exception as e:
            logger.error("Failed to get user settings", user_id=user_id, error=str(e))
            return None

    async def save_user_settings(self, settings_data: Dict[str, Any], user_id: str = "default") -> bool:
        """Save or update user settings.

        Both branches now read field names off ``UserSettings.model_fields``
        instead of duplicating each setting's name + default. Adding a new
        field to :class:`UserSettings` no longer requires touching this
        method, and the SQLModel field's ``Field(default=...)`` is the
        single source of truth for the default value.
        """
        try:
            async with self.get_session() as session:
                stmt = select(UserSettings).where(UserSettings.user_id == user_id)
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                # Filter incoming dict to known model fields. ``user_id`` and
                # the bookkeeping columns (id / created_at / updated_at) are
                # never overridden through this path.
                _MANAGED = {"id", "user_id", "created_at", "updated_at"}
                known_fields = set(UserSettings.model_fields.keys()) - _MANAGED
                payload = {k: v for k, v in settings_data.items() if k in known_fields}

                if existing:
                    for key, value in payload.items():
                        setattr(existing, key, value)
                else:
                    # SQLModel field defaults (``Field(default=...)``) apply
                    # for every key the caller did not explicitly pass.
                    existing = UserSettings(user_id=user_id, **payload)
                    session.add(existing)

                await session.commit()
                logger.info(f"[DB] User settings saved for user_id: {user_id}")
                return True

        except Exception as e:
            logger.error("Failed to save user settings", user_id=user_id, error=str(e))
            return False

    # ============================================================================
    # Provider Defaults
    # ============================================================================

    async def get_provider_defaults(self, provider: str) -> Optional[Dict[str, Any]]:
        """Get default parameters for a provider."""
        try:
            async with self.get_session() as session:
                stmt = select(ProviderDefaults).where(ProviderDefaults.provider == provider.lower())
                result = await session.execute(stmt)
                record = result.scalar_one_or_none()
                if not record:
                    return None
                return {
                    "default_model": record.default_model,
                    "temperature": record.temperature,
                    "max_tokens": record.max_tokens,
                    "thinking_enabled": record.thinking_enabled,
                    "thinking_budget": record.thinking_budget,
                    "reasoning_effort": record.reasoning_effort,
                    "reasoning_format": record.reasoning_format,
                }
        except Exception as e:
            logger.error("Failed to get provider defaults", provider=provider, error=str(e))
            return None

    async def save_provider_defaults(self, provider: str, defaults: Dict[str, Any]) -> bool:
        """Upsert provider defaults."""
        try:
            async with self.get_session() as session:
                stmt = select(ProviderDefaults).where(ProviderDefaults.provider == provider.lower())
                result = await session.execute(stmt)
                record = result.scalar_one_or_none()

                now = datetime.now(timezone.utc)
                if record:
                    record.default_model = defaults.get("default_model", record.default_model)
                    record.temperature = defaults.get("temperature", record.temperature)
                    record.max_tokens = defaults.get("max_tokens", record.max_tokens)
                    record.thinking_enabled = defaults.get("thinking_enabled", record.thinking_enabled)
                    record.thinking_budget = defaults.get("thinking_budget", record.thinking_budget)
                    record.reasoning_effort = defaults.get("reasoning_effort", record.reasoning_effort)
                    record.reasoning_format = defaults.get("reasoning_format", record.reasoning_format)
                    record.updated_at = now
                else:
                    record = ProviderDefaults(
                        provider=provider.lower(),
                        default_model=defaults.get("default_model", ""),
                        temperature=defaults.get("temperature", 0.7),
                        max_tokens=defaults.get("max_tokens", 1000),
                        thinking_enabled=defaults.get("thinking_enabled", False),
                        thinking_budget=defaults.get("thinking_budget", 2048),
                        reasoning_effort=defaults.get("reasoning_effort", "medium"),
                        reasoning_format=defaults.get("reasoning_format", "parsed"),
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(record)

                await session.commit()
                logger.info(f"[DB] Provider defaults saved for: {provider}")
                return True

        except Exception as e:
            logger.error("Failed to save provider defaults", provider=provider, error=str(e))
            return False

    # ============================================================================
    # Token Usage Metrics
    # ============================================================================

    async def save_token_metric(self, metric: Dict[str, Any]) -> bool:
        """Save a token usage metric record."""
        try:
            async with self.get_session() as session:
                entry = TokenUsageMetric(
                    session_id=metric.get("session_id", "default"),
                    node_id=metric.get("node_id", ""),
                    workflow_id=metric.get("workflow_id"),
                    provider=metric.get("provider", ""),
                    model=metric.get("model", ""),
                    input_tokens=metric.get("input_tokens", 0),
                    output_tokens=metric.get("output_tokens", 0),
                    total_tokens=metric.get("total_tokens", 0),
                    cache_creation_tokens=metric.get("cache_creation_tokens", 0),
                    cache_read_tokens=metric.get("cache_read_tokens", 0),
                    reasoning_tokens=metric.get("reasoning_tokens", 0),
                    iteration=metric.get("iteration", 1),
                    execution_id=metric.get("execution_id"),
                    created_at=datetime.now(timezone.utc),
                    # Cost fields
                    input_cost=metric.get("input_cost", 0.0),
                    output_cost=metric.get("output_cost", 0.0),
                    cache_cost=metric.get("cache_cost", 0.0),
                    total_cost=metric.get("total_cost", 0.0),
                )
                session.add(entry)
                await session.commit()
                return True
        except Exception as e:
            logger.error("Failed to save token metric", error=str(e))
            return False

    async def get_session_token_metrics(self, session_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get token metrics for a session."""
        try:
            async with self.get_session() as session:
                stmt = (
                    select(TokenUsageMetric)
                    .where(TokenUsageMetric.session_id == session_id)
                    .order_by(TokenUsageMetric.created_at.desc())
                    .limit(limit)
                )
                result = await session.execute(stmt)
                metrics = result.scalars().all()

                return [
                    {
                        "id": m.id,
                        "session_id": m.session_id,
                        "node_id": m.node_id,
                        "provider": m.provider,
                        "model": m.model,
                        "input_tokens": m.input_tokens,
                        "output_tokens": m.output_tokens,
                        "total_tokens": m.total_tokens,
                        "cache_creation_tokens": m.cache_creation_tokens,
                        "cache_read_tokens": m.cache_read_tokens,
                        "reasoning_tokens": m.reasoning_tokens,
                        "iteration": m.iteration,
                        "created_at": m.created_at.isoformat() if m.created_at else None,
                    }
                    for m in metrics
                ]
        except Exception as e:
            logger.error("Failed to get token metrics", session_id=session_id, error=str(e))
            return []

    async def get_provider_usage_summary(self) -> List[Dict[str, Any]]:
        """Get aggregated token usage and cost by provider.

        Returns a list of provider summaries with:
        - provider: Provider name (openai, anthropic, etc.)
        - total_input_tokens: Sum of input tokens
        - total_output_tokens: Sum of output tokens
        - total_tokens: Sum of all tokens
        - total_input_cost: Sum of input costs (USD)
        - total_output_cost: Sum of output costs (USD)
        - total_cost: Sum of all costs (USD)
        - execution_count: Number of executions
        - models: Breakdown by model (list of dicts)
        """
        try:
            async with self.get_session() as session:
                from sqlalchemy import func

                # First get per-model breakdown
                model_stmt = (
                    select(
                        TokenUsageMetric.provider,
                        TokenUsageMetric.model,
                        func.sum(TokenUsageMetric.input_tokens).label("input_tokens"),
                        func.sum(TokenUsageMetric.output_tokens).label("output_tokens"),
                        func.sum(TokenUsageMetric.total_tokens).label("total_tokens"),
                        func.sum(TokenUsageMetric.input_cost).label("input_cost"),
                        func.sum(TokenUsageMetric.output_cost).label("output_cost"),
                        func.sum(TokenUsageMetric.cache_cost).label("cache_cost"),
                        func.sum(TokenUsageMetric.total_cost).label("total_cost"),
                        func.count().label("execution_count"),
                    )
                    .group_by(TokenUsageMetric.provider, TokenUsageMetric.model)
                    .order_by(TokenUsageMetric.provider, TokenUsageMetric.model)
                )
                result = await session.execute(model_stmt)
                rows = result.all()

                # Aggregate by provider
                providers: Dict[str, Dict] = {}
                for row in rows:
                    provider = row.provider or "unknown"
                    if provider not in providers:
                        providers[provider] = {
                            "provider": provider,
                            "total_input_tokens": 0,
                            "total_output_tokens": 0,
                            "total_tokens": 0,
                            "total_input_cost": 0.0,
                            "total_output_cost": 0.0,
                            "total_cache_cost": 0.0,
                            "total_cost": 0.0,
                            "execution_count": 0,
                            "models": [],
                        }

                    p = providers[provider]
                    p["total_input_tokens"] += row.input_tokens or 0
                    p["total_output_tokens"] += row.output_tokens or 0
                    p["total_tokens"] += row.total_tokens or 0
                    p["total_input_cost"] += float(row.input_cost or 0)
                    p["total_output_cost"] += float(row.output_cost or 0)
                    p["total_cache_cost"] += float(row.cache_cost or 0)
                    p["total_cost"] += float(row.total_cost or 0)
                    p["execution_count"] += row.execution_count or 0

                    p["models"].append(
                        {
                            "model": row.model,
                            "input_tokens": row.input_tokens or 0,
                            "output_tokens": row.output_tokens or 0,
                            "total_tokens": row.total_tokens or 0,
                            "input_cost": round(float(row.input_cost or 0), 6),
                            "output_cost": round(float(row.output_cost or 0), 6),
                            "cache_cost": round(float(row.cache_cost or 0), 6),
                            "total_cost": round(float(row.total_cost or 0), 6),
                            "execution_count": row.execution_count or 0,
                        }
                    )

                # Round provider totals
                for p in providers.values():
                    p["total_input_cost"] = round(p["total_input_cost"], 6)
                    p["total_output_cost"] = round(p["total_output_cost"], 6)
                    p["total_cache_cost"] = round(p["total_cache_cost"], 6)
                    p["total_cost"] = round(p["total_cost"], 6)

                return list(providers.values())

        except Exception as e:
            logger.error("Failed to get provider usage summary", error=str(e))
            return []

    # ============================================================================
    # API Usage Metrics (Twitter, Google Maps, etc.)
    # ============================================================================

    async def save_api_usage_metric(self, metric: Dict[str, Any]) -> bool:
        """Save an API usage metric record.

        Args:
            metric: Dict with session_id, node_id, service, operation, endpoint, resource_count, cost
        """
        try:
            from models.database import APIUsageMetric

            async with self.get_session() as session:
                entry = APIUsageMetric(
                    session_id=metric.get("session_id", "default"),
                    node_id=metric.get("node_id", ""),
                    workflow_id=metric.get("workflow_id"),
                    service=metric.get("service", ""),
                    operation=metric.get("operation", ""),
                    endpoint=metric.get("endpoint", ""),
                    resource_count=metric.get("resource_count", 1),
                    cost=metric.get("cost", 0.0),
                )
                session.add(entry)
                await session.commit()
                return True
        except Exception as e:
            logger.error("Failed to save API usage metric", error=str(e))
            return False

    async def get_api_usage_summary(self, service: str = None) -> List[Dict[str, Any]]:
        """Get aggregated API usage and cost by service.

        Args:
            service: Optional service name to filter (e.g., 'twitter')

        Returns a list of service summaries with:
        - service: Service name (twitter, google_maps, etc.)
        - total_resources: Sum of resources fetched/requests made
        - total_cost: Sum of costs (USD)
        - execution_count: Number of API calls
        - operations: Breakdown by operation (list of dicts)
        """
        try:
            from models.database import APIUsageMetric
            from sqlalchemy import func

            async with self.get_session() as session:
                # Build query with optional service filter
                query = select(
                    APIUsageMetric.service,
                    APIUsageMetric.operation,
                    func.sum(APIUsageMetric.resource_count).label("resource_count"),
                    func.sum(APIUsageMetric.cost).label("cost"),
                    func.count().label("execution_count"),
                ).group_by(APIUsageMetric.service, APIUsageMetric.operation)

                if service:
                    query = query.where(APIUsageMetric.service == service)

                query = query.order_by(APIUsageMetric.service, APIUsageMetric.operation)
                result = await session.execute(query)
                rows = result.all()

                # Aggregate by service
                services: Dict[str, Dict] = {}
                for row in rows:
                    svc = row.service or "unknown"
                    if svc not in services:
                        services[svc] = {"service": svc, "total_resources": 0, "total_cost": 0.0, "execution_count": 0, "operations": []}

                    s = services[svc]
                    s["total_resources"] += row.resource_count or 0
                    s["total_cost"] += float(row.cost or 0)
                    s["execution_count"] += row.execution_count or 0

                    s["operations"].append(
                        {
                            "operation": row.operation,
                            "resource_count": row.resource_count or 0,
                            "total_cost": round(float(row.cost or 0), 6),
                            "execution_count": row.execution_count or 0,
                        }
                    )

                # Round service totals
                for s in services.values():
                    s["total_cost"] = round(s["total_cost"], 6)

                return list(services.values())

        except Exception as e:
            logger.error("Failed to get API usage summary", error=str(e))
            return []

    # ============================================================================
    # Session Token State
    # ============================================================================

    async def get_or_create_session_token_state(self, session_id: str) -> Dict[str, Any]:
        """Get or create token state for a session.

        Handles race conditions when multiple requests try to create the same session.
        """
        try:
            async with self.get_session() as session:
                stmt = select(SessionTokenState).where(SessionTokenState.session_id == session_id)
                result = await session.execute(stmt)
                state = result.scalar_one_or_none()

                if not state:
                    try:
                        state = SessionTokenState(session_id=session_id, updated_at=datetime.now(timezone.utc))
                        session.add(state)
                        await session.commit()
                        await session.refresh(state)
                    except IntegrityError:
                        # Race condition: another request created the session
                        # Rollback and fetch the existing record
                        await session.rollback()
                        result = await session.execute(stmt)
                        state = result.scalar_one_or_none()
                        if not state:
                            # Shouldn't happen, but return defaults if it does
                            return {
                                "session_id": session_id,
                                "cumulative_input_tokens": 0,
                                "cumulative_output_tokens": 0,
                                "cumulative_cache_tokens": 0,
                                "cumulative_reasoning_tokens": 0,
                                "cumulative_total": 0,
                                "last_compaction_at": None,
                                "compaction_count": 0,
                                "custom_threshold": None,
                                "compaction_enabled": True,
                            }

                return {
                    "session_id": state.session_id,
                    "cumulative_input_tokens": state.cumulative_input_tokens,
                    "cumulative_output_tokens": state.cumulative_output_tokens,
                    "cumulative_cache_tokens": state.cumulative_cache_tokens,
                    "cumulative_reasoning_tokens": state.cumulative_reasoning_tokens,
                    "cumulative_total": state.cumulative_total,
                    "last_compaction_at": state.last_compaction_at.isoformat() if state.last_compaction_at else None,
                    "compaction_count": state.compaction_count,
                    "custom_threshold": state.custom_threshold,
                    "compaction_enabled": state.compaction_enabled,
                }
        except Exception as e:
            logger.error("Failed to get session token state", error=str(e))
            return {"session_id": session_id, "cumulative_total": 0, "compaction_enabled": True}

    async def update_session_token_state(self, session_id: str, updates: Dict[str, Any]) -> bool:
        """Update session token state."""
        try:
            async with self.get_session() as session:
                stmt = select(SessionTokenState).where(SessionTokenState.session_id == session_id)
                result = await session.execute(stmt)
                state = result.scalar_one_or_none()

                if not state:
                    state = SessionTokenState(session_id=session_id)
                    session.add(state)

                for key, value in updates.items():
                    if hasattr(state, key):
                        setattr(state, key, value)

                state.updated_at = datetime.now(timezone.utc)
                await session.commit()
                return True
        except Exception as e:
            logger.error("Failed to update session token state", error=str(e))
            return False

    async def reset_session_token_state(self, session_id: str) -> bool:
        """Reset token state after compaction."""
        return await self.update_session_token_state(
            session_id,
            {
                "cumulative_input_tokens": 0,
                "cumulative_output_tokens": 0,
                "cumulative_cache_tokens": 0,
                "cumulative_reasoning_tokens": 0,
                "cumulative_total": 0,
                "last_compaction_at": datetime.now(timezone.utc),
            },
        )

    # ============================================================================
    # Compaction Events
    # ============================================================================

    async def save_compaction_event(self, event: Dict[str, Any]) -> bool:
        """Save a compaction event record."""
        try:
            async with self.get_session() as session:
                entry = CompactionEvent(
                    session_id=event.get("session_id", "default"),
                    node_id=event.get("node_id", ""),
                    workflow_id=event.get("workflow_id"),
                    trigger_reason=event.get("trigger_reason", "threshold"),
                    tokens_before=event.get("tokens_before", 0),
                    tokens_after=event.get("tokens_after", 0),
                    messages_before=event.get("messages_before", 0),
                    messages_after=event.get("messages_after", 0),
                    summary_model=event.get("summary_model", ""),
                    summary_provider=event.get("summary_provider", ""),
                    summary_tokens_used=event.get("summary_tokens_used", 0),
                    success=event.get("success", True),
                    error_message=event.get("error_message"),
                    summary_content=event.get("summary_content"),
                    created_at=datetime.now(timezone.utc),
                )
                session.add(entry)
                await session.commit()
                return True
        except Exception as e:
            logger.error("Failed to save compaction event", error=str(e))
            return False

    async def get_compaction_history(self, session_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Get compaction history for a session."""
        try:
            async with self.get_session() as session:
                stmt = (
                    select(CompactionEvent)
                    .where(CompactionEvent.session_id == session_id)
                    .order_by(CompactionEvent.created_at.desc())
                    .limit(limit)
                )
                result = await session.execute(stmt)
                events = result.scalars().all()

                return [
                    {
                        "id": e.id,
                        "session_id": e.session_id,
                        "node_id": e.node_id,
                        "trigger_reason": e.trigger_reason,
                        "tokens_before": e.tokens_before,
                        "tokens_after": e.tokens_after,
                        "messages_before": e.messages_before,
                        "messages_after": e.messages_after,
                        "success": e.success,
                        "error_message": e.error_message,
                        "created_at": e.created_at.isoformat() if e.created_at else None,
                    }
                    for e in events
                ]
        except Exception as e:
            logger.error("Failed to get compaction history", session_id=session_id, error=str(e))
            return []

    # ============================================================================
    # Agent Teams - CRUD Operations
    # ============================================================================

    async def create_team(
        self, team_id: str, workflow_id: str, team_lead_node_id: str, config: Optional[Dict[str, Any]] = None,
        execution_id: Optional[str] = None, root_execution_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Create a new agent team."""
        try:
            async with self.get_session() as session:
                team = AgentTeam(
                    id=team_id,
                    workflow_id=workflow_id,
                    team_lead_node_id=team_lead_node_id,
                    execution_id=execution_id,
                    root_execution_id=root_execution_id or execution_id,
                    config=config or {},
                    created_at=datetime.now(timezone.utc),
                )
                session.add(team)
                await session.commit()
                logger.info(f"[Teams] Created team {team_id}")
                return {"id": team.id, "workflow_id": team.workflow_id, "execution_id": team.execution_id,
                        "root_execution_id": team.root_execution_id, "status": team.status}
        except Exception as e:
            logger.error(f"Failed to create team: {e}")
            return None

    async def get_team(self, team_id: str) -> Optional[Dict[str, Any]]:
        """Get team by ID."""
        try:
            async with self.get_session() as session:
                result = await session.execute(select(AgentTeam).where(AgentTeam.id == team_id))
                team = result.scalar_one_or_none()
                if not team:
                    return None
                return {
                    "id": team.id,
                    "workflow_id": team.workflow_id,
                    "team_lead_node_id": team.team_lead_node_id,
                    "execution_id": team.execution_id,
                    "root_execution_id": team.root_execution_id,
                    "status": team.status,
                    "config": team.config,
                    "created_at": team.created_at.isoformat() if team.created_at else None,
                }
        except Exception as e:
            logger.error(f"Failed to get team: {e}")
            return None

    async def find_team(
        self, workflow_id: str, team_lead_node_id: str,
        execution_id: Optional[str] = None, active_first: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Resolve an execution team, or the latest team for a workflow lead."""
        try:
            async with self.get_session() as session:
                query = select(AgentTeam).where(
                    AgentTeam.workflow_id == workflow_id,
                    AgentTeam.team_lead_node_id == team_lead_node_id,
                )
                if execution_id:
                    query = query.where(AgentTeam.execution_id == execution_id)
                else:
                    # Execution identity, not a mutable lifecycle flag, defines
                    # the current run. A crashed/legacy team can remain marked
                    # active forever; active-first ordering then hides every
                    # newer submitted or accepted task. The newest execution is
                    # the default, and callers select older runs explicitly by
                    # execution_id.
                    query = query.order_by(AgentTeam.created_at.desc())
                result = await session.execute(query.limit(1))
                team = result.scalar_one_or_none()
                return await self.get_team(team.id) if team else None
        except Exception as e:
            logger.error(f"Failed to find team: {e}")
            return None

    async def list_team_executions(
        self, workflow_id: str, team_lead_node_id: str
    ) -> List[Dict[str, Any]]:
        """List every durable execution owned by a workflow lead, newest first."""
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    select(AgentTeam)
                    .where(
                        AgentTeam.workflow_id == workflow_id,
                        AgentTeam.team_lead_node_id == team_lead_node_id,
                    )
                    .order_by(AgentTeam.created_at.desc(), AgentTeam.id.desc())
                )
                return [
                    {
                        "team_id": team.id,
                        "execution_id": team.execution_id,
                        "root_execution_id": team.root_execution_id,
                        "status": team.status,
                        "created_at": team.created_at.isoformat() if team.created_at else None,
                        "completed_at": team.completed_at.isoformat() if team.completed_at else None,
                    }
                    for team in result.scalars().all()
                ]
        except Exception as e:
            logger.error(f"Failed to list team executions: {e}")
            return []

    async def update_team_status(self, team_id: str, status: str) -> bool:
        """Update team status."""
        try:
            async with self.get_session() as session:
                result = await session.execute(select(AgentTeam).where(AgentTeam.id == team_id))
                team = result.scalar_one_or_none()
                if not team:
                    return False
                team.status = status
                if status in ("completed", "failed", "dissolved"):
                    team.completed_at = datetime.now(timezone.utc)
                elif status == "active":
                    team.completed_at = None
                await session.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to update team status: {e}")
            return False

    async def add_team_member(
        self, team_id: str, agent_node_id: str, agent_type: str, role: str = "teammate", agent_label: Optional[str] = None,
        capabilities: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Add member to team."""
        try:
            async with self.get_session() as session:
                existing = await session.execute(select(TeamMember).where(
                    TeamMember.team_id == team_id, TeamMember.agent_node_id == agent_node_id
                ))
                member = existing.scalar_one_or_none()
                if member:
                    member.agent_type, member.agent_label, member.role = agent_type, agent_label, role
                    member.capabilities = capabilities
                    await session.commit()
                    return {"id": member.id, "agent_node_id": agent_node_id, "role": role}
                member = TeamMember(
                    team_id=team_id,
                    agent_node_id=agent_node_id,
                    agent_type=agent_type,
                    agent_label=agent_label,
                    role=role,
                    capabilities=capabilities,
                    joined_at=datetime.now(timezone.utc),
                )
                session.add(member)
                await session.commit()
                return {"id": member.id, "agent_node_id": agent_node_id, "role": role}
        except Exception as e:
            logger.error(f"Failed to add team member: {e}")
            return None

    async def get_team_members(self, team_id: str) -> List[Dict[str, Any]]:
        """Get all team members."""
        try:
            async with self.get_session() as session:
                result = await session.execute(select(TeamMember).where(TeamMember.team_id == team_id))
                return [
                    {
                        "id": m.id,
                        "agent_node_id": m.agent_node_id,
                        "agent_type": m.agent_type,
                        "agent_label": m.agent_label,
                        "role": m.role,
                        "status": m.status,
                    }
                    for m in result.scalars().all()
                ]
        except Exception as e:
            logger.error(f"Failed to get team members: {e}")
            return []

    async def update_member_status(self, team_id: str, agent_node_id: str, status: str) -> bool:
        """Update member status (idle, working, offline)."""
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.agent_node_id == agent_node_id)
                )
                member = result.scalar_one_or_none()
                if not member:
                    return False
                member.status = status
                await session.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to update member status: {e}")
            return False

    async def add_team_task(
        self,
        task_id: str,
        team_id: str,
        title: str,
        created_by: str,
        description: Optional[str] = None,
        priority: int = 3,
        depends_on: Optional[List[str]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Add task to team's shared list."""
        try:
            async with self.get_session() as session:
                existing = await session.execute(select(TeamTask).where(TeamTask.id == task_id))
                task = existing.scalar_one_or_none()
                if task:
                    if task.team_id != team_id:
                        return None
                    return {"id": task.id, "title": task.title, "status": task.status}
                task = TeamTask(
                    id=task_id,
                    team_id=team_id,
                    title=title,
                    description=description,
                    priority=priority,
                    created_by=created_by,
                    depends_on={"task_ids": depends_on} if depends_on else None,
                    status="queued",
                    created_at=datetime.now(timezone.utc),
                )
                session.add(task)
                await session.commit()
                return {"id": task.id, "title": title, "status": "queued"}
        except Exception as e:
            logger.error(f"Failed to add team task: {e}")
            return None

    @staticmethod
    def _team_task_dict(task: TeamTask, attempts: Optional[List[TeamTaskAttempt]] = None) -> Dict[str, Any]:
        allowed_actions = {
            "blocked": ["modify", "cancel"], "queued": ["modify", "cancel"],
            "running": ["cancel"], "submitted": ["accept", "retry", "reassign"],
            "failed": ["retry", "reassign"], "cancelled": ["retry", "reassign"],
            "accepted": [],
        }.get(task.status, [])
        return {
            "id": task.id, "team_id": task.team_id, "workflow_id": task.workflow_id,
            "execution_id": task.execution_id, "root_execution_id": task.root_execution_id,
            "parent_agent_id": task.parent_agent_id, "title": task.title,
            "description": task.description, "mission": task.mission,
            "context": task.context, "acceptance_criteria": task.acceptance_criteria,
            "status": task.status, "priority": task.priority, "queue_sequence": task.queue_sequence,
            "created_by": task.created_by, "assigned_to": task.assigned_to,
            "depends_on": task.depends_on.get("task_ids", []) if task.depends_on else [],
            "result": task.result, "error": task.error, "progress": task.progress,
            "revision": task.revision, "current_attempt": task.current_attempt,
            "retry_count": task.retry_count, "max_retries": task.max_retries,
            "child_workflow_id": task.child_workflow_id, "child_run_id": task.child_run_id,
            "trace_id": task.trace_id, "cancellation_requested": task.cancellation_requested,
            "cancellation_reason": task.cancellation_reason, "usage": task.usage,
            "created_at": task.created_at.isoformat() if task.created_at else None,
            "started_at": task.started_at.isoformat() if task.started_at else None,
            "completed_at": task.completed_at.isoformat() if task.completed_at else None,
            "attempts": [
                {"id": a.id, "attempt_number": a.attempt_number, "assignee_node_id": a.assignee_node_id,
                 "status": a.status, "child_workflow_id": a.child_workflow_id,
                 "child_run_id": a.child_run_id, "result": a.result, "error": a.error,
                 "usage": a.usage, "created_at": a.created_at.isoformat() if a.created_at else None,
                 "started_at": a.started_at.isoformat() if a.started_at else None,
                 "completed_at": a.completed_at.isoformat() if a.completed_at else None}
                for a in (attempts or [])
            ],
            "allowed_actions": allowed_actions,
        }

    async def create_durable_team_task(self, **values: Any) -> Optional[Dict[str, Any]]:
        """Persist one scoped queue item, idempotently by task id."""
        try:
            async with self.get_session() as session:
                existing = (await session.execute(select(TeamTask).where(TeamTask.id == values["id"]))).scalar_one_or_none()
                if existing:
                    return self._team_task_dict(existing) if existing.team_id == values["team_id"] else None
                maximum = (await session.execute(
                    select(func.max(TeamTask.queue_sequence)).where(TeamTask.team_id == values["team_id"])
                )).scalar_one_or_none() or 0
                values.setdefault("queue_sequence", maximum + 1)
                values.setdefault("status", "blocked" if values.get("depends_on") else "queued")
                if isinstance(values.get("depends_on"), list):
                    values["depends_on"] = {"task_ids": values["depends_on"]}
                task = TeamTask(**values)
                session.add(task)
                await session.commit()
                await session.refresh(task)
                return self._team_task_dict(task)
        except Exception as e:
            logger.error(f"Failed to create durable team task: {e}")
            return None

    async def get_durable_team_task(self, team_id: str, task_id: str) -> Optional[Dict[str, Any]]:
        async with self.get_session() as session:
            task = (await session.execute(select(TeamTask).where(TeamTask.id == task_id, TeamTask.team_id == team_id))).scalar_one_or_none()
            if not task:
                return None
            attempts = (await session.execute(
                select(TeamTaskAttempt).where(TeamTaskAttempt.task_id == task_id).order_by(TeamTaskAttempt.attempt_number)
            )).scalars().all()
            return self._team_task_dict(task, list(attempts))

    async def transition_team_task(
        self, team_id: str, task_id: str, expected_revision: int,
        allowed_statuses: List[str], values: Dict[str, Any], *, create_attempt: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Revision-and-state guarded task transition (compare-and-swap)."""
        async with self.get_session() as session:
            values = dict(values)
            values["revision"] = expected_revision + 1
            changed = await session.execute(
                update(TeamTask).where(
                    TeamTask.id == task_id, TeamTask.team_id == team_id,
                    TeamTask.revision == expected_revision, TeamTask.status.in_(allowed_statuses),
                ).values(**values)
            )
            if changed.rowcount != 1:
                await session.rollback()
                return None
            task = (await session.execute(select(TeamTask).where(TeamTask.id == task_id))).scalar_one()
            if create_attempt:
                attempt_number = task.current_attempt
                session.add(TeamTaskAttempt(
                    id=f"{task.id}:attempt:{attempt_number}", task_id=task.id, team_id=team_id,
                    attempt_number=attempt_number, assignee_node_id=task.assigned_to,
                    status=task.status, child_workflow_id=task.child_workflow_id,
                    child_run_id=task.child_run_id, created_at=datetime.now(timezone.utc),
                ))
            await session.commit()
            await session.refresh(task)
            return self._team_task_dict(task)

    async def get_team_tasks(self, team_id: str, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """Get team tasks, optionally filtered by status."""
        try:
            async with self.get_session() as session:
                query = select(TeamTask).where(TeamTask.team_id == team_id)
                if status:
                    query = query.where(TeamTask.status == status)
                query = query.order_by(TeamTask.queue_sequence.asc(), TeamTask.created_at.asc(), TeamTask.id.asc())
                result = await session.execute(query)
                tasks = [self._team_task_dict(t) for t in result.scalars().all()]
                queue_position = 0
                for task in tasks:
                    if task["status"] in {"blocked", "queued", "pending"}:
                        queue_position += 1
                        task["queue_position"] = queue_position
                    else:
                        task["queue_position"] = None
                return tasks
        except Exception as e:
            logger.error(f"Failed to get team tasks: {e}")
            return []

    async def claim_task(self, task_id: str, agent_node_id: str) -> Optional[Dict[str, Any]]:
        """Atomically claim a pending task; same-agent retries are safe."""
        try:
            async with self.get_session() as session:
                claimed = await session.execute(
                    update(TeamTask)
                    .where(
                        TeamTask.id == task_id,
                        TeamTask.status == "queued",
                        or_(TeamTask.assigned_to.is_(None), TeamTask.assigned_to == agent_node_id),
                    )
                    .values(
                        assigned_to=agent_node_id,
                        status="running",
                        started_at=datetime.now(timezone.utc),
                    )
                )
                # The guarded UPDATE is the compare-and-swap. It remains
                # correct on databases where a SELECT + Python mutation would
                # race because there is no SQLite BEGIN IMMEDIATE reservation.
                selected = await session.execute(
                    select(TeamTask).where(TeamTask.id == task_id)
                )
                task = selected.scalar_one_or_none()
                if task is None:
                    await session.rollback()
                    return None

                won = bool(claimed.rowcount == 1)
                same_agent_retry = (
                    task.status == "running"
                    and task.assigned_to == agent_node_id
                )
                if not won and not same_agent_retry:
                    await session.rollback()
                    return None

                await session.commit()
                return {
                    "id": task.id,
                    "title": task.title,
                    "assigned_to": agent_node_id,
                }
        except Exception as e:
            logger.error(f"Failed to claim task: {e}")
            return None

    async def complete_task(self, task_id: str, result_data: Optional[Dict[str, Any]] = None) -> bool:
        """Atomically submit a running task for lead review."""
        try:
            usage = (
                result_data.get("usage")
                if isinstance(result_data, dict) and isinstance(result_data.get("usage"), dict)
                else None
            )
            async def _complete(session: AsyncSession) -> Dict[str, Any]:
                completed = await session.execute(
                    update(TeamTask)
                    .where(
                        TeamTask.id == task_id,
                        TeamTask.status == "running",
                    )
                    .values(
                        status="submitted",
                        result=result_data,
                        usage=usage,
                        progress=100,
                        completed_at=datetime.now(timezone.utc),
                    )
                )
                if completed.rowcount == 1:
                    return {"success": True}

                selected = await session.execute(
                    select(TeamTask).where(TeamTask.id == task_id)
                )
                task = selected.scalar_one_or_none()
                return {
                    "success": bool(
                        task is not None and task.status == "submitted"
                    )
                }

            result, _applied = await self.run_runtime_mutation(
                resource_type="team_task",
                resource_id=task_id,
                operation="complete",
                mutate=_complete,
            )
            return bool(result.get("success"))
        except Exception as e:
            logger.error(f"Failed to complete task: {e}")
            return False

    async def fail_task(self, task_id: str, error: str) -> bool:
        """Atomically record one failed attempt and release or fail it."""
        try:
            async def _fail(session: AsyncSession) -> Dict[str, Any]:
                next_retry = TeamTask.retry_count + 1
                retryable = next_retry < TeamTask.max_retries
                failed = await session.execute(
                    update(TeamTask)
                    .where(
                        TeamTask.id == task_id,
                        TeamTask.status == "running",
                    )
                    .values(
                        error=error,
                        retry_count=next_retry,
                        status=case(
                            (retryable, "queued"),
                            else_="failed",
                        ),
                        assigned_to=case(
                            (retryable, None),
                            else_=TeamTask.assigned_to,
                        ),
                        completed_at=case(
                            (retryable, TeamTask.completed_at),
                            else_=datetime.now(timezone.utc),
                        ),
                    )
                )
                if failed.rowcount == 1:
                    return {"success": True}

                selected = await session.execute(
                    select(TeamTask).where(TeamTask.id == task_id)
                )
                task = selected.scalar_one_or_none()
                # A transport retry after this exact failure observes the
                # released/terminal state and must not increment twice.
                return {
                    "success": bool(
                        task is not None
                        and task.status in {"queued", "failed"}
                        and task.error == error
                    )
                }

            result, _applied = await self.run_runtime_mutation(
                resource_type="team_task",
                resource_id=task_id,
                operation="fail",
                mutate=_fail,
            )
            return bool(result.get("success"))
        except Exception as e:
            logger.error(f"Failed to fail task: {e}")
            return False

    async def get_claimable_tasks(self, team_id: str) -> List[Dict[str, Any]]:
        """Get pending tasks with resolved dependencies."""
        try:
            async with self.get_session() as session:
                result = await session.execute(select(TeamTask).where(TeamTask.team_id == team_id))
                tasks = result.scalars().all()
                completed_ids = {t.id for t in tasks if t.status == "accepted"}
                claimable = []
                for t in tasks:
                    if t.status != "queued":
                        continue
                    deps = t.depends_on.get("task_ids", []) if t.depends_on else []
                    if all(d in completed_ids for d in deps):
                        claimable.append({"id": t.id, "title": t.title, "priority": t.priority})
                return claimable
        except Exception as e:
            logger.error(f"Failed to get claimable tasks: {e}")
            return []

    async def add_agent_message(
        self, team_id: str, from_agent: str, content: str, message_type: str = "direct", to_agent: Optional[str] = None,
        event_id: Optional[str] = None, extra_data: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Add message between agents."""
        try:
            async with self.get_session() as session:
                if event_id:
                    existing = await session.execute(select(AgentMessage).where(AgentMessage.event_id == event_id))
                    msg = existing.scalar_one_or_none()
                    if msg:
                        return {"id": msg.id, "event_id": msg.event_id, "from_agent": msg.from_agent, "to_agent": msg.to_agent}
                msg = AgentMessage(
                    event_id=event_id,
                    team_id=team_id,
                    from_agent=from_agent,
                    to_agent=to_agent,
                    message_type=message_type,
                    content=content,
                    extra_data=extra_data,
                    created_at=datetime.now(timezone.utc),
                )
                session.add(msg)
                await session.commit()
                return {"id": msg.id, "event_id": msg.event_id, "from_agent": from_agent, "to_agent": to_agent}
        except Exception as e:
            logger.error(f"Failed to add agent message: {e}")
            return None

    async def get_agent_messages(
        self, team_id: str, agent_node_id: Optional[str] = None, unread_only: bool = False
    ) -> List[Dict[str, Any]]:
        """Get messages for team or specific agent."""
        try:
            async with self.get_session() as session:
                from sqlalchemy import or_

                query = select(AgentMessage).where(AgentMessage.team_id == team_id)
                if agent_node_id:
                    query = query.where(or_(AgentMessage.to_agent == agent_node_id, AgentMessage.to_agent.is_(None)))
                if unread_only:
                    query = query.where(not AgentMessage.read)
                query = query.order_by(AgentMessage.created_at.asc()).limit(100)
                result = await session.execute(query)
                return [
                    {
                        "id": m.id,
                        "event_id": m.event_id,
                        "from_agent": m.from_agent,
                        "to_agent": m.to_agent,
                        "message_type": m.message_type,
                        "content": m.content,
                        "read": m.read,
                        "created_at": m.created_at.isoformat() if m.created_at else None,
                        "extra_data": m.extra_data,
                    }
                    for m in result.scalars().all()
                ]
        except Exception as e:
            logger.error(f"Failed to get agent messages: {e}")
            return []

    async def mark_messages_read(self, team_id: str, agent_node_id: str) -> int:
        """Mark all messages as read for an agent."""
        try:
            async with self.get_session() as session:
                from sqlalchemy import or_

                result = await session.execute(
                    select(AgentMessage).where(
                        AgentMessage.team_id == team_id,
                        not AgentMessage.read,
                        or_(AgentMessage.to_agent == agent_node_id, AgentMessage.to_agent.is_(None)),
                    )
                )
                messages = result.scalars().all()
                for m in messages:
                    m.read = True
                await session.commit()
                return len(messages)
        except Exception as e:
            logger.error(f"Failed to mark messages read: {e}")
            return 0

    async def get_team_stats(self, team_id: str) -> Dict[str, Any]:
        """Get team statistics."""
        try:
            async with self.get_session() as session:
                # Get counts in simple queries
                team_result = await session.execute(select(AgentTeam).where(AgentTeam.id == team_id))
                team = team_result.scalar_one_or_none()
                if not team:
                    return {"error": "Team not found"}

                members_result = await session.execute(select(TeamMember).where(TeamMember.team_id == team_id))
                members = members_result.scalars().all()

                tasks_result = await session.execute(select(TeamTask).where(TeamTask.team_id == team_id))
                tasks = tasks_result.scalars().all()

                counts = {
                    state: sum(t.status == state for t in tasks)
                    for state in ("blocked", "queued", "running", "submitted", "accepted", "failed", "cancelled")
                }
                # Compatibility with rows created by older runtimes during a rolling upgrade.
                pending_count = counts["blocked"] + counts["queued"] + sum(t.status == "pending" for t in tasks)
                active_count = counts["running"] + sum(t.status == "in_progress" for t in tasks)
                # A worker result is only submitted for review. "Done" means
                # the lead explicitly accepted it (plus historical completed
                # rows retained for compatibility).
                completed_count = counts["accepted"] + sum(t.status == "completed" for t in tasks)
                failed_count = counts["failed"]

                return {
                    "team_id": team_id,
                    "status": team.status,
                    "workflow_id": team.workflow_id,
                    "team_lead_node_id": team.team_lead_node_id,
                    "execution_id": team.execution_id,
                    "root_execution_id": team.root_execution_id,
                    "member_count": len(members),
                    "task_total": len(tasks),
                    "task_pending": pending_count,
                    "task_in_progress": active_count,
                    "task_completed": completed_count,
                    "task_failed": failed_count,
                    # Stable response aliases consumed by TeamMonitor and
                    # existing WS clients.
                    "task_count": len(tasks),
                    "pending_count": pending_count,
                    "queued_count": pending_count,
                    "active_count": active_count,
                    "completed_count": completed_count,
                    "failed_count": failed_count,
                    "blocked_count": counts["blocked"],
                    "submitted_count": counts["submitted"],
                    "accepted_count": counts["accepted"],
                    "cancelled_count": counts["cancelled"],
                    "status_counts": counts,
                    "members": [{"id": m.agent_node_id, "agent_node_id": m.agent_node_id,
                                 "type": m.agent_type, "agent_type": m.agent_type, "label": m.agent_label,
                                 "role": m.role, "status": m.status, "capabilities": m.capabilities} for m in members],
                    "queued_tasks": [
                        {"id": t.id, "title": t.title, "assigned_to": t.assigned_to}
                        for t in tasks if t.status in {"pending", "blocked", "queued"}
                    ],
                    "active_tasks": [
                        {"id": t.id, "title": t.title, "assigned_to": t.assigned_to, "progress": t.progress}
                        for t in tasks
                        if t.status in {"in_progress", "running"}
                    ],
                    "tasks": [self._team_task_dict(t) for t in sorted(tasks, key=lambda item: (item.queue_sequence, item.created_at))],
                }
        except Exception as e:
            logger.error(f"Failed to get team stats: {e}")
            return {"error": str(e)}

    async def acquire_subagent_permit(self, root_execution_id: str, permit_id: str, limit: int = 3) -> bool:
        """Atomically acquire a root-global permit; retries by the owner succeed."""
        if limit < 1:
            return False
        try:
            async with self.get_session() as session:
                existing = await session.execute(
                    select(SubagentConcurrencyPermit).where(SubagentConcurrencyPermit.permit_id == permit_id)
                )
                permit = existing.scalar_one_or_none()
                if permit:
                    return permit.root_execution_id == root_execution_id and permit.status == "active"

                await session.execute(text(
                    "INSERT OR IGNORE INTO subagent_concurrency_counters "
                    "(root_execution_id, active_count, updated_at) VALUES (:root, 0, CURRENT_TIMESTAMP)"
                ), {"root": root_execution_id})
                claimed = await session.execute(
                    update(SubagentConcurrencyCounter)
                    .where(
                        SubagentConcurrencyCounter.root_execution_id == root_execution_id,
                        SubagentConcurrencyCounter.active_count < limit,
                    )
                    .values(
                        active_count=SubagentConcurrencyCounter.active_count + 1,
                        updated_at=datetime.now(timezone.utc),
                    )
                )
                if claimed.rowcount != 1:
                    await session.rollback()
                    return False
                session.add(SubagentConcurrencyPermit(
                    permit_id=permit_id, root_execution_id=root_execution_id, status="active"
                ))
                await session.commit()
                return True
        except IntegrityError:
            # A concurrent retry inserted the same permit. Resolve ownership
            # from durable state rather than incrementing the counter again.
            async with self.get_session() as session:
                result = await session.execute(
                    select(SubagentConcurrencyPermit).where(SubagentConcurrencyPermit.permit_id == permit_id)
                )
                permit = result.scalar_one_or_none()
                return bool(permit and permit.root_execution_id == root_execution_id and permit.status == "active")
        except Exception as e:
            logger.error("Failed to acquire subagent permit", root_execution_id=root_execution_id, permit_id=permit_id, error=str(e))
            return False

    async def release_subagent_permit(self, root_execution_id: str, permit_id: str) -> bool:
        """Idempotently release a permit and decrement its root counter once."""
        try:
            async with self.get_session() as session:
                released = await session.execute(
                    update(SubagentConcurrencyPermit)
                    .where(
                        SubagentConcurrencyPermit.permit_id == permit_id,
                        SubagentConcurrencyPermit.root_execution_id == root_execution_id,
                        SubagentConcurrencyPermit.status == "active",
                    )
                    .values(status="released", released_at=datetime.now(timezone.utc))
                )
                if released.rowcount == 1:
                    await session.execute(
                        update(SubagentConcurrencyCounter)
                        .where(
                            SubagentConcurrencyCounter.root_execution_id == root_execution_id,
                            SubagentConcurrencyCounter.active_count > 0,
                        )
                        .values(
                            active_count=SubagentConcurrencyCounter.active_count - 1,
                            updated_at=datetime.now(timezone.utc),
                        )
                    )
                    await session.commit()
                    return True
                existing = await session.execute(
                    select(SubagentConcurrencyPermit).where(
                        SubagentConcurrencyPermit.permit_id == permit_id,
                        SubagentConcurrencyPermit.root_execution_id == root_execution_id,
                    )
                )
                permit = existing.scalar_one_or_none()
                already_released = bool(permit and permit.status == "released")
                await session.rollback()
                return already_released
        except Exception as e:
            logger.error("Failed to release subagent permit", root_execution_id=root_execution_id, permit_id=permit_id, error=str(e))
            return False

    # ============================================================================
    # Google Connections - Customer Mode OAuth Storage
    # ============================================================================

    async def save_google_connection(
        self,
        customer_id: str,
        email: str,
        access_token: str,
        refresh_token: str,
        scopes: str,
        name: Optional[str] = None,
    ) -> bool:
        """Save or update a Google Workspace connection for a customer."""
        try:
            async with self.get_session() as session:
                # Check if connection exists
                result = await session.execute(select(GoogleConnection).where(GoogleConnection.customer_id == customer_id))
                existing = result.scalar_one_or_none()

                now = datetime.now(timezone.utc)
                if existing:
                    # Update existing connection
                    existing.email = email
                    existing.name = name
                    existing.access_token = access_token
                    existing.refresh_token = refresh_token
                    existing.scopes = scopes
                    existing.is_active = True
                    existing.updated_at = now
                else:
                    # Create new connection
                    connection = GoogleConnection(
                        customer_id=customer_id,
                        email=email,
                        name=name,
                        access_token=access_token,
                        refresh_token=refresh_token,
                        scopes=scopes,
                        is_active=True,
                        connected_at=now,
                        updated_at=now,
                    )
                    session.add(connection)

                await session.commit()
                logger.info(f"Saved Google connection for customer {customer_id}")
                return True
        except Exception as e:
            logger.error(f"Failed to save Google connection: {e}")
            return False

    async def get_google_connection(self, customer_id: str) -> Optional[GoogleConnection]:
        """Get Google Workspace connection for a customer."""
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    select(GoogleConnection).where(GoogleConnection.customer_id == customer_id, GoogleConnection.is_active)
                )
                return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"Failed to get Google connection: {e}")
            return None

    async def delete_google_connection(self, customer_id: str) -> bool:
        """Delete Google Workspace connection for a customer."""
        try:
            async with self.get_session() as session:
                result = await session.execute(select(GoogleConnection).where(GoogleConnection.customer_id == customer_id))
                connection = result.scalar_one_or_none()
                if connection:
                    await session.delete(connection)
                    await session.commit()
                    logger.info(f"Deleted Google connection for customer {customer_id}")
                return True
        except Exception as e:
            logger.error(f"Failed to delete Google connection: {e}")
            return False

    async def list_google_connections(self, limit: int = 100) -> List[Dict[str, Any]]:
        """List all Google Workspace connections."""
        try:
            async with self.get_session() as session:
                result = await session.execute(
                    select(GoogleConnection).where(GoogleConnection.is_active).order_by(GoogleConnection.connected_at.desc()).limit(limit)
                )
                connections = result.scalars().all()
                return [
                    {
                        "customer_id": c.customer_id,
                        "email": c.email,
                        "name": c.name,
                        "connected_at": c.connected_at.isoformat() if c.connected_at else None,
                        "last_used_at": c.last_used_at.isoformat() if c.last_used_at else None,
                    }
                    for c in connections
                ]
        except Exception as e:
            logger.error(f"Failed to list Google connections: {e}")
            return []

    async def update_google_connection_tokens(
        self,
        customer_id: str,
        access_token: str,
        refresh_token: Optional[str] = None,
    ) -> bool:
        """Update tokens for a Google Workspace connection (after refresh)."""
        try:
            async with self.get_session() as session:
                result = await session.execute(select(GoogleConnection).where(GoogleConnection.customer_id == customer_id))
                connection = result.scalar_one_or_none()
                if connection:
                    connection.access_token = access_token
                    if refresh_token:
                        connection.refresh_token = refresh_token
                    connection.updated_at = datetime.now(timezone.utc)
                    await session.commit()
                    return True
                return False
        except Exception as e:
            logger.error(f"Failed to update Google tokens: {e}")
            return False

    async def update_google_last_used(self, customer_id: str) -> bool:
        """Update last_used_at timestamp for a Google Workspace connection."""
        try:
            async with self.get_session() as session:
                result = await session.execute(select(GoogleConnection).where(GoogleConnection.customer_id == customer_id))
                connection = result.scalar_one_or_none()
                if connection:
                    connection.last_used_at = datetime.now(timezone.utc)
                    await session.commit()
                    return True
                return False
        except Exception as e:
            logger.error(f"Failed to update Google last used: {e}")
            return False

    # ============================================================================
    # Proxy Provider CRUD
    # ============================================================================

    async def save_proxy_provider(self, data: Dict[str, Any]) -> bool:
        """Save or update a proxy provider configuration (upsert by name)."""
        try:
            name = data.get("name")
            if not name:
                logger.error("Cannot save proxy provider without name")
                return False

            async with self.get_session() as session:
                stmt = select(ProxyProviderConfig).where(ProxyProviderConfig.name == name)
                result = await session.execute(stmt)
                existing = result.scalar_one_or_none()

                if existing:
                    for field in ["enabled", "priority", "cost_per_gb", "gateway_host", "gateway_port", "url_template"]:
                        if field in data:
                            setattr(existing, field, data[field])
                    existing.updated_at = datetime.now(timezone.utc)
                else:
                    existing = ProxyProviderConfig(
                        name=name,
                        enabled=data.get("enabled", True),
                        priority=data.get("priority", 50),
                        cost_per_gb=data.get("cost_per_gb", 0.0),
                        gateway_host=data.get("gateway_host", ""),
                        gateway_port=data.get("gateway_port", 0),
                        url_template=data.get("url_template", "{}"),
                    )
                    session.add(existing)

                await session.commit()
                logger.info(f"[DB] Proxy provider saved: {name}")
                return True

        except Exception as e:
            logger.error(f"Failed to save proxy provider: {e}")
            return False

    async def get_proxy_provider(self, name: str) -> Optional[Dict[str, Any]]:
        """Get a single proxy provider by name."""
        try:
            async with self.get_session() as session:
                stmt = select(ProxyProviderConfig).where(ProxyProviderConfig.name == name)
                result = await session.execute(stmt)
                p = result.scalar_one_or_none()

                if not p:
                    return None

                return {
                    "id": p.id,
                    "name": p.name,
                    "enabled": p.enabled,
                    "priority": p.priority,
                    "cost_per_gb": p.cost_per_gb,
                    "gateway_host": p.gateway_host,
                    "gateway_port": p.gateway_port,
                    "url_template": p.url_template,
                    "created_at": p.created_at.isoformat() if p.created_at else None,
                    "updated_at": p.updated_at.isoformat() if p.updated_at else None,
                }

        except Exception as e:
            logger.error(f"Failed to get proxy provider: {e}")
            return None

    async def get_proxy_providers(self) -> List[Dict[str, Any]]:
        """Get all proxy provider configurations."""
        try:
            async with self.get_session() as session:
                stmt = select(ProxyProviderConfig).order_by(ProxyProviderConfig.priority)
                result = await session.execute(stmt)
                providers = result.scalars().all()

                return [
                    {
                        "id": p.id,
                        "name": p.name,
                        "enabled": p.enabled,
                        "priority": p.priority,
                        "cost_per_gb": p.cost_per_gb,
                        "gateway_host": p.gateway_host,
                        "gateway_port": p.gateway_port,
                        "url_template": p.url_template,
                        "created_at": p.created_at.isoformat() if p.created_at else None,
                        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
                    }
                    for p in providers
                ]

        except Exception as e:
            logger.error(f"Failed to get proxy providers: {e}")
            return []

    async def delete_proxy_provider(self, name: str) -> bool:
        """Delete a proxy provider by name."""
        try:
            async with self.get_session() as session:
                stmt = select(ProxyProviderConfig).where(ProxyProviderConfig.name == name)
                result = await session.execute(stmt)
                provider = result.scalar_one_or_none()

                if provider:
                    await session.delete(provider)
                    await session.commit()
                    logger.info(f"[DB] Proxy provider deleted: {name}")

                return True

        except Exception as e:
            logger.error(f"Failed to delete proxy provider: {e}")
            return False

    # ============================================================================
    # Proxy Routing Rules CRUD
    # ============================================================================

    async def save_proxy_routing_rule(self, data: Dict[str, Any]) -> bool:
        """Save a new proxy routing rule."""
        try:
            async with self.get_session() as session:
                rule = ProxyRoutingRule(
                    domain_pattern=data.get("domain_pattern", ""),
                    preferred_providers=data.get("preferred_providers", "[]"),
                    required_country=data.get("required_country", ""),
                    session_type=data.get("session_type", "rotating"),
                )
                session.add(rule)
                await session.commit()
                logger.info(f"[DB] Proxy routing rule saved: {data.get('domain_pattern')}")
                return True

        except Exception as e:
            logger.error(f"Failed to save proxy routing rule: {e}")
            return False

    async def get_proxy_routing_rules(self) -> List[Dict[str, Any]]:
        """Get all proxy routing rules."""
        try:
            async with self.get_session() as session:
                stmt = select(ProxyRoutingRule).order_by(ProxyRoutingRule.id)
                result = await session.execute(stmt)
                rules = result.scalars().all()

                return [
                    {
                        "id": r.id,
                        "domain_pattern": r.domain_pattern,
                        "preferred_providers": r.preferred_providers,
                        "required_country": r.required_country,
                        "session_type": r.session_type,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    }
                    for r in rules
                ]

        except Exception as e:
            logger.error(f"Failed to get proxy routing rules: {e}")
            return []

    async def delete_proxy_routing_rule(self, rule_id: int) -> bool:
        """Delete a proxy routing rule by ID."""
        try:
            async with self.get_session() as session:
                stmt = select(ProxyRoutingRule).where(ProxyRoutingRule.id == rule_id)
                result = await session.execute(stmt)
                rule = result.scalar_one_or_none()

                if rule:
                    await session.delete(rule)
                    await session.commit()
                    logger.info(f"[DB] Proxy routing rule deleted: {rule_id}")

                return True

        except Exception as e:
            logger.error(f"Failed to delete proxy routing rule: {e}")
            return False
