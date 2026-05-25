"""SQLModel database models and tables."""

from datetime import datetime, timezone
from typing import Optional, Dict, Any
from sqlmodel import SQLModel, Field, Column, DateTime, JSON
from sqlalchemy import func


class NodeParameter(SQLModel, table=True):
    """Node parameters storage."""

    __tablename__ = "node_parameters"

    id: Optional[int] = Field(default=None, primary_key=True)
    node_id: str = Field(index=True, unique=True, max_length=255)
    parameters: Dict[str, Any] = Field(sa_column=Column(JSON))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), onupdate=func.now())
    )


class Workflow(SQLModel, table=True):
    """Workflow definitions."""

    __tablename__ = "workflows"

    id: str = Field(primary_key=True, max_length=255)
    name: str = Field(max_length=255)
    description: Optional[str] = Field(default=None, max_length=1000)
    data: Dict[str, Any] = Field(sa_column=Column(JSON))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), onupdate=func.now())
    )


class Execution(SQLModel, table=True):
    """Workflow execution history."""

    __tablename__ = "executions"

    id: str = Field(primary_key=True, max_length=255)
    workflow_id: str = Field(foreign_key="workflows.id", max_length=255)
    node_id: str = Field(max_length=255)
    status: str = Field(default="pending", max_length=50)
    result: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    error: Optional[str] = Field(default=None, max_length=2000)
    execution_time: Optional[float] = Field(default=None)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )


class APIKey(SQLModel, table=True):
    """Encrypted API key storage."""

    __tablename__ = "api_keys"

    id: str = Field(primary_key=True, max_length=255)
    provider: str = Field(max_length=50)
    session_id: str = Field(default="default", max_length=255)
    key_encrypted: str = Field(max_length=1000)
    key_hash: str = Field(max_length=64, index=True)
    models: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    is_valid: bool = Field(default=True)
    last_validated: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True)))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), onupdate=func.now())
    )


class APIKeyValidation(SQLModel, table=True):
    """API key validation cache."""

    __tablename__ = "api_key_validations"

    id: Optional[int] = Field(default=None, primary_key=True)
    key_hash: str = Field(unique=True, max_length=64, index=True)
    validated: bool = Field(default=True)
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )


class NodeOutput(SQLModel, table=True):
    """Node execution output storage - persisted across server restarts."""

    __tablename__ = "node_outputs"

    id: Optional[int] = Field(default=None, primary_key=True)
    node_id: str = Field(index=True, max_length=255)
    session_id: str = Field(default="default", max_length=255)
    output_name: str = Field(default="output_0", max_length=100)
    data: Dict[str, Any] = Field(sa_column=Column(JSON))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), onupdate=func.now())
    )


class ConversationMessage(SQLModel, table=True):
    """AI conversation message storage - persisted across server restarts."""

    __tablename__ = "conversation_messages"

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True, max_length=255)
    role: str = Field(max_length=20)  # 'human' or 'ai'
    content: str = Field(max_length=50000)  # Large content support
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )


class ToolSchema(SQLModel, table=True):
    """Tool node schema configuration - stores LLM-visible schema for tool nodes.

    This allows Android Toolkit and other aggregator nodes to update the schema
    of connected tool nodes, providing the LLM with accurate capability information.
    """

    __tablename__ = "tool_schemas"

    id: Optional[int] = Field(default=None, primary_key=True)
    node_id: str = Field(index=True, unique=True, max_length=255)
    tool_name: str = Field(max_length=255)  # e.g., 'android_device', 'calculator'
    tool_description: str = Field(max_length=2000)  # Description shown to LLM
    schema_config: Dict[str, Any] = Field(sa_column=Column(JSON))  # Schema fields and types
    connected_services: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))  # For toolkit nodes
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), onupdate=func.now())
    )


class ChatMessage(SQLModel, table=True):
    """Chat panel messages - persisted across server restarts.

    Stores user and assistant messages from the chat panel (Console Panel chat section).
    These are separate from ConversationMessage which stores AI agent memory.
    """

    __tablename__ = "chat_messages"

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(default="default", index=True, max_length=255)
    role: str = Field(max_length=20)  # 'user' or 'assistant'
    message: str = Field(max_length=50000)  # Large content support
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )


class ConsoleLog(SQLModel, table=True):
    """Console panel logs - persisted across server restarts.

    Stores output from Console nodes during workflow execution.
    Separate from chat_messages which stores Chat panel messages.
    """

    __tablename__ = "console_logs"

    id: Optional[int] = Field(default=None, primary_key=True)
    node_id: str = Field(index=True, max_length=255)  # Console node ID
    label: str = Field(max_length=255)  # User-defined label
    workflow_id: Optional[str] = Field(default=None, max_length=255)
    data: str = Field(max_length=100000)  # JSON-encoded data
    formatted: str = Field(max_length=100000)  # Pre-formatted string
    format: str = Field(default="text", max_length=20)  # json, json_compact, text, table
    source_node_id: Optional[str] = Field(default=None, max_length=255)
    source_node_type: Optional[str] = Field(default=None, max_length=100)
    source_node_label: Optional[str] = Field(default=None, max_length=255)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )


class UserSkill(SQLModel, table=True):
    """User-created custom skills for Zeenie.

    Skills are defined using the Agent Skills specification format with YAML frontmatter.
    This allows non-technical users to create and manage skills via the UI editor.
    """

    __tablename__ = "user_skills"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True, max_length=100)  # Unique skill identifier
    display_name: str = Field(max_length=200)  # Human-readable name
    description: str = Field(max_length=1000)  # Short description for skill registry
    instructions: str = Field(max_length=50000)  # Full markdown instructions
    allowed_tools: Optional[str] = Field(default=None, max_length=1000)  # Comma-separated tool names
    category: str = Field(default="custom", max_length=50)  # Skill category
    icon: str = Field(default="star", max_length=50)  # Icon identifier
    color: str = Field(default="#6366F1", max_length=20)  # Color hex code
    metadata_json: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))  # Additional metadata
    is_active: bool = Field(default=True)  # Whether skill is available
    created_by: Optional[int] = Field(default=None)  # User ID who created it
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), onupdate=func.now())
    )


class UserSettings(SQLModel, table=True):
    """User settings/preferences - persisted across server restarts.

    Database is the source of truth for all application settings.
    Settings apply on browser refresh/app load.
    """

    __tablename__ = "user_settings"

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: str = Field(default="default", unique=True, index=True, max_length=255)  # For future multi-user
    auto_save: bool = Field(default=True)
    auto_save_interval: int = Field(default=30)  # seconds
    sidebar_default_open: bool = Field(default=True)
    component_palette_default_open: bool = Field(default=True)
    console_panel_default_open: bool = Field(default=False)
    memory_window_size: int = Field(default=100)  # Message pairs in short-term memory (1-100)
    compaction_ratio: float = Field(default=0.5)  # Fraction of context window triggering compaction (0.1-0.9)
    examples_loaded: bool = Field(default=False)  # Track if example workflows were imported
    onboarding_completed: bool = Field(default=False)  # Track if user completed or skipped onboarding
    onboarding_step: int = Field(default=0)  # Last completed onboarding step (for resuming)
    default_llm_provider: Optional[str] = Field(default=None, max_length=50)  # Global default AI provider
    default_llm_model: Optional[str] = Field(default=None, max_length=200)  # Global default AI model
    auto_add_skill_for_tools: bool = Field(
        default=True
    )  # When a tool node connects to an AI agent's input-tools, auto-enable the matching skill
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), onupdate=func.now())
    )


class ProviderDefaults(SQLModel, table=True):
    """LLM provider default parameters - persisted across server restarts.

    Database is the source of truth for provider-specific default parameters.
    These defaults are applied to new AI nodes and can be overridden per-node.
    """

    __tablename__ = "provider_defaults"

    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str = Field(unique=True, index=True, max_length=50)  # openai, anthropic, gemini, etc.
    default_model: str = Field(default="", max_length=100)  # Default model for this provider
    temperature: float = Field(default=0.7)
    max_tokens: int = Field(default=1000)
    thinking_enabled: bool = Field(default=False)
    thinking_budget: int = Field(default=2048)
    reasoning_effort: str = Field(default="medium", max_length=20)  # low, medium, high
    reasoning_format: str = Field(default="parsed", max_length=20)  # parsed, hidden
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), onupdate=func.now())
    )


# =============================================================================
# Token Tracking and Memory Compaction
# =============================================================================


class TokenUsageMetric(SQLModel, table=True):
    """Token usage per agent execution."""

    __tablename__ = "token_usage_metrics"

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True, max_length=255)
    node_id: str = Field(index=True, max_length=255)
    workflow_id: Optional[str] = Field(default=None, max_length=255)
    provider: str = Field(max_length=50)
    model: str = Field(max_length=100)
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    total_tokens: int = Field(default=0)
    cache_creation_tokens: int = Field(default=0)
    cache_read_tokens: int = Field(default=0)
    reasoning_tokens: int = Field(default=0)
    iteration: int = Field(default=1)
    execution_id: Optional[str] = Field(default=None, max_length=255)
    created_at: Optional[datetime] = Field(default=None)
    # Cost fields (USD)
    input_cost: float = Field(default=0.0)
    output_cost: float = Field(default=0.0)
    cache_cost: float = Field(default=0.0)
    total_cost: float = Field(default=0.0)


class CompactionEvent(SQLModel, table=True):
    """Compaction event history."""

    __tablename__ = "compaction_events"

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(index=True, max_length=255)
    node_id: str = Field(max_length=255)
    workflow_id: Optional[str] = Field(default=None, max_length=255)
    trigger_reason: str = Field(max_length=50)
    tokens_before: int = Field(default=0)
    tokens_after: int = Field(default=0)
    messages_before: int = Field(default=0)
    messages_after: int = Field(default=0)
    summary_model: str = Field(max_length=100)
    summary_provider: str = Field(max_length=50)
    summary_tokens_used: int = Field(default=0)
    success: bool = Field(default=True)
    error_message: Optional[str] = Field(default=None, max_length=2000)
    summary_content: Optional[str] = Field(default=None, max_length=50000)
    created_at: Optional[datetime] = Field(default=None)


class SessionTokenState(SQLModel, table=True):
    """Cumulative token state per memory session."""

    __tablename__ = "session_token_states"

    id: Optional[int] = Field(default=None, primary_key=True)
    session_id: str = Field(unique=True, index=True, max_length=255)
    cumulative_input_tokens: int = Field(default=0)
    cumulative_output_tokens: int = Field(default=0)
    cumulative_cache_tokens: int = Field(default=0)
    cumulative_reasoning_tokens: int = Field(default=0)
    cumulative_total: int = Field(default=0)
    last_compaction_at: Optional[datetime] = Field(default=None)
    compaction_count: int = Field(default=0)
    custom_threshold: Optional[int] = Field(default=None)
    compaction_enabled: bool = Field(default=True)
    updated_at: Optional[datetime] = Field(default=None)
    # Cumulative cost fields (USD)
    cumulative_input_cost: float = Field(default=0.0)
    cumulative_output_cost: float = Field(default=0.0)
    cumulative_total_cost: float = Field(default=0.0)


class APIUsageMetric(SQLModel, table=True):
    """API service usage for cost tracking (Twitter, Google Maps, etc.)."""

    __tablename__ = "api_usage_metrics"

    id: Optional[int] = Field(default=None, primary_key=True)
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )
    session_id: str = Field(index=True, max_length=255)
    node_id: str = Field(max_length=255)
    workflow_id: Optional[str] = Field(default=None, max_length=255)

    # Service identification
    service: str = Field(index=True, max_length=50)  # twitter, google_maps, etc.
    operation: str = Field(max_length=100)  # posts_read, content_create, etc.
    endpoint: str = Field(max_length=100)  # Handler action (tweet, search, like)

    # Usage counts
    resource_count: int = Field(default=1)

    # Cost in USD
    cost: float = Field(default=0.0)


# =============================================================================
# Agent Teams - Claude SDK Agent Teams Pattern
# =============================================================================


class AgentTeam(SQLModel, table=True):
    """Agent team for multi-agent collaboration.

    Implements Claude SDK Agent Teams pattern where a team lead coordinates
    multiple teammate agents working on shared tasks.
    """

    __tablename__ = "agent_teams"

    id: str = Field(primary_key=True, max_length=255)
    workflow_id: str = Field(index=True, max_length=255)
    team_lead_node_id: str = Field(max_length=255)
    status: str = Field(default="active", max_length=20)  # active, completed, failed, dissolved
    config: Dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )
    completed_at: Optional[datetime] = Field(default=None)


class TeamMember(SQLModel, table=True):
    """Agent team membership.

    Tracks which agents belong to which team and their current status.
    """

    __tablename__ = "team_members"

    id: Optional[int] = Field(default=None, primary_key=True)
    team_id: str = Field(index=True, max_length=255)
    agent_node_id: str = Field(index=True, max_length=255)
    agent_type: str = Field(max_length=100)  # orchestrator_agent, android_agent, etc.
    agent_label: Optional[str] = Field(default=None, max_length=255)  # User-defined label
    role: str = Field(default="teammate", max_length=20)  # team_lead, teammate
    status: str = Field(default="idle", max_length=20)  # idle, working, offline
    capabilities: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    joined_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )


class TeamTask(SQLModel, table=True):
    """Shared task in agent team task list.

    Tasks are created by the team lead and claimed by teammates.
    Supports dependencies between tasks.
    """

    __tablename__ = "team_tasks"

    id: str = Field(primary_key=True, max_length=255)
    team_id: str = Field(index=True, max_length=255)
    title: str = Field(max_length=500)
    description: Optional[str] = Field(default=None, max_length=5000)
    status: str = Field(default="pending", max_length=20)  # pending, in_progress, completed, failed, skipped
    priority: int = Field(default=3)  # 1-5, lower = higher priority
    created_by: str = Field(max_length=255)  # agent_node_id
    assigned_to: Optional[str] = Field(default=None, max_length=255)
    depends_on: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))  # List of task_ids
    result: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    error: Optional[str] = Field(default=None, max_length=2000)
    retry_count: int = Field(default=0)
    max_retries: int = Field(default=3)
    progress: int = Field(default=0)  # 0-100 percentage
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )
    started_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)


class AgentMessage(SQLModel, table=True):
    """Inter-agent messages within a team.

    Supports direct messages between agents and broadcasts to all team members.
    """

    __tablename__ = "agent_messages"

    id: Optional[int] = Field(default=None, primary_key=True)
    team_id: str = Field(index=True, max_length=255)
    from_agent: str = Field(max_length=255)  # node_id
    to_agent: Optional[str] = Field(default=None, max_length=255)  # None = broadcast
    message_type: str = Field(max_length=50)  # direct, broadcast, task_assignment, task_update, task_complete
    content: str = Field(max_length=10000)
    extra_data: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    read: bool = Field(default=False)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )


class ProxyProviderConfig(SQLModel, table=True):
    """Proxy provider configuration stored in database.

    Each row represents a configured residential proxy provider with
    gateway endpoint, url_template for parameter encoding, and metadata.
    """

    __tablename__ = "proxy_providers"

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(unique=True, index=True, max_length=255)
    enabled: bool = Field(default=True)
    priority: int = Field(default=50)  # Lower = preferred
    cost_per_gb: float = Field(default=0.0)
    gateway_host: str = Field(default="", max_length=500)
    gateway_port: int = Field(default=0)
    url_template: str = Field(default="{}", max_length=5000)  # JSON template config
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), onupdate=func.now())
    )


class ProxyRoutingRule(SQLModel, table=True):
    """Domain-based routing rules for proxy providers.

    Routes specific domain patterns through preferred providers
    with optional geo-targeting and session type requirements.
    """

    __tablename__ = "proxy_routing_rules"

    id: Optional[int] = Field(default=None, primary_key=True)
    domain_pattern: str = Field(max_length=500)  # e.g., *.linkedin.com
    preferred_providers: str = Field(default="[]", max_length=2000)  # JSON array
    required_country: str = Field(default="", max_length=10)
    session_type: str = Field(default="rotating", max_length=20)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )


class GoogleConnection(SQLModel, table=True):
    """Google Workspace OAuth connections for customer access mode.

    Stores OAuth tokens for each connected Google account, supporting both:
    - Owner mode: Single account stored via auth_service (credentials modal)
    - Customer mode: Multiple customer accounts stored here with customer_id

    This enables apps to access customer Google Workspace services
    (Gmail, Calendar, Drive, Sheets, Tasks, Contacts).
    """

    __tablename__ = "google_connections"

    id: Optional[int] = Field(default=None, primary_key=True)
    customer_id: str = Field(index=True, max_length=255)  # Your app's customer identifier
    email: str = Field(max_length=255)  # Connected Google account email
    name: Optional[str] = Field(default=None, max_length=255)  # User's display name
    access_token: str = Field(max_length=2000)  # Encrypted OAuth access token
    refresh_token: str = Field(max_length=2000)  # Encrypted OAuth refresh token
    token_expiry: Optional[datetime] = Field(default=None)  # When access token expires
    scopes: str = Field(max_length=1000)  # Comma-separated granted scopes
    is_active: bool = Field(default=True)  # Whether connection is active
    last_used_at: Optional[datetime] = Field(default=None)  # Last API call timestamp
    connected_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), server_default=func.now())
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), sa_column=Column(DateTime(timezone=True), onupdate=func.now())
    )
