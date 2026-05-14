"""Per-node output schema registry.

Single source of truth for the runtime output shape of each node type.
Consumed by the editor's Input panel (via the get_node_output_schema
WebSocket handler and the GET /api/schemas/nodes/{node_type}.json
HTTP endpoint) to populate the draggable variable list *before* the
workflow has been executed. Once a node has run, the editor prefers
the real execution data over the declared schema (mirrors n8n's
VirtualSchema.vue precedence — see
docs-internal/schema_source_of_truth_rfc.md).

Design notes:
- Models here are **UI-visible shape projections**. They do NOT have to
  match the full handler return value — just the fields the user might
  reasonably want to drag into downstream parameters. Prefer minimal
  flat fields over deeply nested structures.
- Fields stay optional so a missing runtime value doesn't surface an
  error at the UI layer.
- Pydantic's ``model_json_schema()`` emits JSON Schema 7; the frontend
  understands that shape directly.
- Unknown node types return ``None`` -> the frontend falls back to
  the legacy sampleSchemas map (and once that's gone, to an empty
  ``{"data": "any"}``).

Adding a new schema: import ``BaseModel``, declare the model, add it to
``NODE_OUTPUT_SCHEMAS`` below. No frontend change needed.
"""

from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Shared bases (small, reused)
# ---------------------------------------------------------------------------


class _OutputBase(BaseModel):
    """Opt-in to arbitrary extras in the UI-visible projection — downstream
    schemas may add context-specific fields without a code change here."""

    model_config = ConfigDict(extra="allow")


# ---------------------------------------------------------------------------
# Workflow / trigger nodes
# ---------------------------------------------------------------------------


class StartOutput(_OutputBase):
    """Workflow start node: shape comes from the user's initialData JSON.
    The editor also parses initialData locally for drag-drop, so this is
    mostly informational."""

    timestamp: Optional[str] = None
    data: Optional[Any] = None


class ChatTriggerOutput(_OutputBase):
    message: Optional[str] = None
    timestamp: Optional[str] = None
    session_id: Optional[str] = None


class TaskTriggerOutput(_OutputBase):
    task_id: Optional[str] = None
    status: Optional[str] = Field(None, description="'completed' or 'error'")
    agent_name: Optional[str] = None
    agent_node_id: Optional[str] = None
    parent_node_id: Optional[str] = None
    result: Optional[str] = None
    error: Optional[str] = None
    workflow_id: Optional[str] = None


class WebhookTriggerOutput(_OutputBase):
    method: Optional[str] = None
    path: Optional[str] = None
    headers: Optional[dict] = None
    query: Optional[dict] = None
    body: Optional[str] = None
    json_: Optional[dict] = Field(None, alias="json")

    model_config = ConfigDict(populate_by_name=True, extra="allow")


# ---------------------------------------------------------------------------
# AI agents / chat models / memory
# ---------------------------------------------------------------------------


class AIAgentOutput(_OutputBase):
    """Shared shape for every LLM-backed agent + chat model."""

    response: Optional[str] = None
    thinking: Optional[str] = None
    model: Optional[str] = None
    provider: Optional[str] = None
    finish_reason: Optional[str] = None
    timestamp: Optional[str] = None


class SimpleMemoryOutput(_OutputBase):
    session_id: Optional[str] = None
    messages: Optional[list] = None
    message_count: Optional[int] = None
    window_size: Optional[int] = None


# ---------------------------------------------------------------------------
# Code executors
# ---------------------------------------------------------------------------


class CodeExecutorOutput(_OutputBase):
    output: Optional[Any] = None


# ---------------------------------------------------------------------------
# HTTP / network
# ---------------------------------------------------------------------------


class HttpRequestOutput(_OutputBase):
    status: Optional[int] = None
    data: Optional[Any] = None
    headers: Optional[dict] = None
    url: Optional[str] = None
    method: Optional[str] = None


# ---------------------------------------------------------------------------
# WhatsApp
# ---------------------------------------------------------------------------


class WhatsAppGroupInfo(BaseModel):
    group_jid: Optional[str] = None
    sender_jid: Optional[str] = None
    sender_phone: Optional[str] = None
    sender_name: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class WhatsAppReceiveOutput(_OutputBase):
    message_id: Optional[str] = None
    sender: Optional[str] = None
    sender_phone: Optional[str] = None
    chat_id: Optional[str] = None
    message_type: Optional[str] = None
    text: Optional[str] = None
    timestamp: Optional[str] = None
    is_group: Optional[bool] = None
    is_from_me: Optional[bool] = None
    push_name: Optional[str] = None
    media: Optional[dict] = None
    group_info: Optional[WhatsAppGroupInfo] = None


class WhatsAppSendOutput(_OutputBase):
    success: Optional[bool] = None
    message_id: Optional[str] = None
    chat_id: Optional[str] = None
    timestamp: Optional[str] = None
    message_type: Optional[str] = None


# ---------------------------------------------------------------------------
# Google Workspace (consolidated nodes)
# ---------------------------------------------------------------------------


class GmailOutput(_OutputBase):
    operation: Optional[str] = None
    message_id: Optional[str] = None
    thread_id: Optional[str] = None
    emails: Optional[list] = None
    count: Optional[int] = None
    subject: Optional[str] = None
    from_: Optional[str] = Field(None, alias="from")
    to: Optional[str] = None
    date: Optional[str] = None
    body: Optional[str] = None
    snippet: Optional[str] = None
    labels: Optional[list] = None

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class GmailReceiveOutput(_OutputBase):
    message_id: Optional[str] = None
    thread_id: Optional[str] = None
    from_: Optional[str] = Field(None, alias="from")
    to: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    snippet: Optional[str] = None
    date: Optional[str] = None
    labels: Optional[list] = None
    attachments: Optional[list] = None
    is_unread: Optional[bool] = None

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class CalendarOutput(_OutputBase):
    operation: Optional[str] = None
    event_id: Optional[str] = None
    summary: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    location: Optional[str] = None
    description: Optional[str] = None
    attendees: Optional[list] = None
    status: Optional[str] = None
    events: Optional[list] = None
    count: Optional[int] = None
    deleted: Optional[bool] = None


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


class SearchResult(BaseModel):
    title: Optional[str] = None
    snippet: Optional[str] = None
    url: Optional[str] = None

    model_config = ConfigDict(extra="allow")


class SearchOutput(_OutputBase):
    """Shared schema for braveSearch / serperSearch / perplexitySearch."""

    query: Optional[str] = None
    results: Optional[list[SearchResult]] = None
    result_count: Optional[int] = None
    answer: Optional[str] = None
    citations: Optional[list] = None
    provider: Optional[str] = None


# ---------------------------------------------------------------------------
# Location (Google Maps, Android location)
# ---------------------------------------------------------------------------


class LocationOutput(_OutputBase):
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    accuracy: Optional[float] = None
    provider: Optional[str] = None
    altitude: Optional[float] = None
    speed: Optional[float] = None
    bearing: Optional[float] = None


# ---------------------------------------------------------------------------
# Filesystem + shell
# ---------------------------------------------------------------------------


class FileReadOutput(_OutputBase):
    content: Optional[str] = None
    file_path: Optional[str] = None
    encoding: Optional[str] = None


class FileModifyOutput(_OutputBase):
    operation: Optional[str] = None
    file_path: Optional[str] = None
    occurrences: Optional[int] = None


class ShellOutput(_OutputBase):
    stdout: Optional[str] = None
    exit_code: Optional[int] = None
    truncated: Optional[bool] = None
    command: Optional[str] = None


class FsSearchOutput(_OutputBase):
    path: Optional[str] = None
    entries: Optional[list] = None
    matches: Optional[list] = None
    pattern: Optional[str] = None
    count: Optional[int] = None


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


class CronSchedulerOutput(_OutputBase):
    timestamp: Optional[str] = None
    iteration: Optional[int] = None
    start_mode: Optional[str] = None
    frequency: Optional[str] = None
    timezone: Optional[str] = None
    schedule: Optional[str] = None
    scheduled_time: Optional[str] = None
    triggered_at: Optional[str] = None
    waited_seconds: Optional[float] = None
    message: Optional[str] = None


# ---------------------------------------------------------------------------
# Google Workspace — remaining
# ---------------------------------------------------------------------------


class DriveOutput(_OutputBase):
    operation: Optional[str] = None
    file_id: Optional[str] = None
    name: Optional[str] = None
    mime_type: Optional[str] = None
    web_view_link: Optional[str] = None
    web_content_link: Optional[str] = None
    size: Optional[int] = None
    content: Optional[str] = None
    files: Optional[list] = None
    count: Optional[int] = None
    next_page_token: Optional[str] = None
    permission_id: Optional[str] = None
    role: Optional[str] = None
    email: Optional[str] = None


class SheetsOutput(_OutputBase):
    operation: Optional[str] = None
    values: Optional[list] = None
    range: Optional[str] = None
    rows: Optional[int] = None
    columns: Optional[int] = None
    major_dimension: Optional[str] = None
    updated_range: Optional[str] = None
    updated_rows: Optional[int] = None
    updated_columns: Optional[int] = None
    updated_cells: Optional[int] = None
    table_range: Optional[str] = None


class TasksOutput(_OutputBase):
    operation: Optional[str] = None
    task_id: Optional[str] = None
    title: Optional[str] = None
    notes: Optional[str] = None
    due: Optional[str] = None
    status: Optional[str] = None
    completed: Optional[str] = None
    tasks: Optional[list] = None
    count: Optional[int] = None
    deleted: Optional[bool] = None


class ContactsOutput(_OutputBase):
    operation: Optional[str] = None
    resource_name: Optional[str] = None
    display_name: Optional[str] = None
    given_name: Optional[str] = None
    family_name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    company: Optional[str] = None
    job_title: Optional[str] = None
    contacts: Optional[list] = None
    count: Optional[int] = None
    total_people: Optional[int] = None
    next_page_token: Optional[str] = None
    deleted: Optional[bool] = None


# ---------------------------------------------------------------------------
# Messaging: WhatsApp DB, Social
# (Telegram schemas moved to nodes/telegram/, registered via
# ``register_output_schema``.)
# ---------------------------------------------------------------------------


class WhatsAppDbOutput(_OutputBase):
    """Composite — different operations surface different subsets of these
    fields. We keep them all optional so the variable panel shows the union."""

    operation: Optional[str] = None
    messages: Optional[list] = None
    total: Optional[int] = None
    has_more: Optional[bool] = None
    count: Optional[int] = None
    chat_type: Optional[str] = None
    groups: Optional[list] = None
    contacts: Optional[list] = None
    participants: Optional[list] = None
    jid: Optional[str] = None
    phone: Optional[str] = None
    name: Optional[str] = None
    push_name: Optional[str] = None
    business_name: Optional[str] = None
    is_business: Optional[bool] = None
    is_contact: Optional[bool] = None
    profile_pic: Optional[str] = None
    channels: Optional[list] = None
    channel_jid: Optional[str] = None
    timestamp: Optional[str] = None
    muted: Optional[bool] = None
    server_ids: Optional[str] = None
    status: Optional[str] = None


class SocialReceiveOutput(_OutputBase):
    """socialReceive has four output handles (message / media / contact /
    metadata) plus top-level fields for backwards compatibility. The
    variable panel dispatches per-handle via edge.sourceHandle, reading
    the nested object at the matching key."""

    message: Optional[str] = None
    media: Optional[dict] = None
    contact: Optional[dict] = None
    metadata: Optional[dict] = None
    # backwards-compat top-level fields:
    message_id: Optional[str] = None
    sender: Optional[str] = None
    sender_phone: Optional[str] = None
    sender_name: Optional[str] = None
    chat_id: Optional[str] = None
    channel: Optional[str] = None
    text: Optional[str] = None
    timestamp: Optional[str] = None
    is_group: Optional[bool] = None
    is_from_me: Optional[bool] = None


class SocialSendOutput(_OutputBase):
    success: Optional[bool] = None
    message_id: Optional[str] = None
    channel: Optional[str] = None
    recipient: Optional[str] = None
    message_type: Optional[str] = None
    timestamp: Optional[str] = None


# ---------------------------------------------------------------------------
# Twitter / X
# ---------------------------------------------------------------------------


class TwitterSendOutput(_OutputBase):
    tweet_id: Optional[str] = None
    text: Optional[str] = None
    author_id: Optional[str] = None
    created_at: Optional[str] = None
    action: Optional[str] = None


class TwitterSearchOutput(_OutputBase):
    tweets: Optional[list] = None
    count: Optional[int] = None
    query: Optional[str] = None


class TwitterUserOutput(_OutputBase):
    id: Optional[str] = None
    username: Optional[str] = None
    name: Optional[str] = None
    profile_image_url: Optional[str] = None
    verified: Optional[bool] = None


# ---------------------------------------------------------------------------
# Document processing
# ---------------------------------------------------------------------------


class HttpScraperOutput(_OutputBase):
    items: Optional[list] = None
    item_count: Optional[int] = None
    errors: Optional[list] = None


class FileDownloaderOutput(_OutputBase):
    downloaded: Optional[int] = None
    skipped: Optional[int] = None
    failed: Optional[int] = None
    files: Optional[list] = None
    output_dir: Optional[str] = None


class DocumentParserOutput(_OutputBase):
    documents: Optional[list] = None
    parsed_count: Optional[int] = None
    failed: Optional[list] = None


class TextChunkerOutput(_OutputBase):
    chunks: Optional[list] = None
    chunk_count: Optional[int] = None


class EmbeddingGeneratorOutput(_OutputBase):
    embeddings: Optional[list] = None
    embedding_count: Optional[int] = None
    dimensions: Optional[int] = None
    chunks: Optional[list] = None


class VectorStoreOutput(_OutputBase):
    stored_count: Optional[int] = None
    matches: Optional[list] = None
    collection_name: Optional[str] = None
    backend: Optional[str] = None


# ---------------------------------------------------------------------------
# Web scrapers / browser
# ---------------------------------------------------------------------------


class ApifyOutput(_OutputBase):
    run_id: Optional[str] = None
    actor_id: Optional[str] = None
    status: Optional[str] = None
    dataset_id: Optional[str] = None
    items: Optional[list] = None
    item_count: Optional[int] = None
    compute_units: Optional[float] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


class BrowserOutput(_OutputBase):
    operation: Optional[str] = None
    data: Optional[Any] = None
    session: Optional[str] = None


class CrawleeOutput(_OutputBase):
    pages: Optional[list] = None
    page_count: Optional[int] = None
    crawler_type: Optional[str] = None
    mode: Optional[str] = None
    proxied: Optional[bool] = None


# ---------------------------------------------------------------------------
# Proxy nodes
# ---------------------------------------------------------------------------


class ProxyRequestOutput(_OutputBase):
    status: Optional[int] = None
    data: Optional[Any] = None
    headers: Optional[dict] = None
    url: Optional[str] = None
    method: Optional[str] = None
    proxy_provider: Optional[str] = None
    latency_ms: Optional[float] = None
    bytes_transferred: Optional[int] = None
    attempt: Optional[int] = None


class ProxyStatusOutput(_OutputBase):
    enabled: Optional[bool] = None
    providers: Optional[list] = None
    stats: Optional[dict] = None


class ProxyConfigOutput(_OutputBase):
    operation: Optional[str] = None
    success: Optional[bool] = None
    data: Optional[Any] = None


# ---------------------------------------------------------------------------
# Email (Himalaya)
# ---------------------------------------------------------------------------


class EmailSendOutput(_OutputBase):
    from_: Optional[str] = Field(None, alias="from")
    to: Optional[str] = None
    subject: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True, extra="allow")


class EmailReadOutput(_OutputBase):
    operation: Optional[str] = None
    folder: Optional[str] = None
    data: Optional[Any] = None


class EmailReceiveOutput(_OutputBase):
    message_id: Optional[str] = None
    from_: Optional[str] = Field(None, alias="from")
    to: Optional[str] = None
    subject: Optional[str] = None
    date: Optional[str] = None
    body: Optional[str] = None
    folder: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True, extra="allow")


# ---------------------------------------------------------------------------
# Android services (remaining — monitoring / apps / automation / sensors / media)
# ---------------------------------------------------------------------------


class BatteryMonitorOutput(_OutputBase):
    battery_level: Optional[float] = None
    is_charging: Optional[bool] = None
    temperature_celsius: Optional[float] = None
    health: Optional[str] = None
    voltage: Optional[float] = None


class SystemInfoOutput(_OutputBase):
    device_model: Optional[str] = None
    android_version: Optional[str] = None
    api_level: Optional[int] = None
    manufacturer: Optional[str] = None
    total_memory: Optional[int] = None
    available_memory: Optional[int] = None


class NetworkMonitorOutput(_OutputBase):
    connected: Optional[bool] = None
    type: Optional[str] = None
    wifi_ssid: Optional[str] = None
    ip_address: Optional[str] = None


class WifiAutomationOutput(_OutputBase):
    wifi_enabled: Optional[bool] = None
    ssid: Optional[str] = None
    ip_address: Optional[str] = None
    signal_strength: Optional[float] = None


class BluetoothAutomationOutput(_OutputBase):
    bluetooth_enabled: Optional[bool] = None
    paired_devices: Optional[list] = None
    connected_devices: Optional[list] = None


class AudioAutomationOutput(_OutputBase):
    music_volume: Optional[float] = None
    ring_volume: Optional[float] = None
    muted: Optional[bool] = None


class AppLauncherOutput(_OutputBase):
    package_name: Optional[str] = None
    launched: Optional[bool] = None
    app_name: Optional[str] = None


class AppListOutput(_OutputBase):
    apps: Optional[list] = None
    count: Optional[int] = None


class DeviceStateAutomationOutput(_OutputBase):
    airplane_mode: Optional[bool] = None
    screen_on: Optional[bool] = None
    brightness: Optional[float] = None


class ScreenControlAutomationOutput(_OutputBase):
    brightness: Optional[float] = None
    auto_brightness: Optional[bool] = None
    screen_timeout: Optional[int] = None


class AirplaneModeControlOutput(_OutputBase):
    airplane_mode_enabled: Optional[bool] = None


class MotionDetectionOutput(_OutputBase):
    accelerometer: Optional[dict] = None
    gyroscope: Optional[dict] = None
    motion_detected: Optional[bool] = None


class EnvironmentalSensorsOutput(_OutputBase):
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    pressure: Optional[float] = None
    light_level: Optional[float] = None


class CameraControlOutput(_OutputBase):
    cameras: Optional[list] = None
    photo_path: Optional[str] = None
    success: Optional[bool] = None


class MediaControlOutput(_OutputBase):
    volume: Optional[float] = None
    is_playing: Optional[bool] = None
    current_track: Optional[str] = None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
# Map node type (matches `nodeDefinition.name` on the frontend) -> Pydantic
# model class. Missing entries return None from ``get_node_output_schema``.
#
# Aliasing: several node types share the same shape — we just point at the
# same model. For example every agent and every chat model uses AIAgentOutput.

_AGENT_TYPES = [
    "aiAgent",
    "chatAgent",
    "android_agent",
    "coding_agent",
    "web_agent",
    "task_agent",
    "social_agent",
    "travel_agent",
    "tool_agent",
    "productivity_agent",
    "payments_agent",
    "consumer_agent",
    "autonomous_agent",
    "orchestrator_agent",
    "ai_employee",
    "rlm_agent",
    "claude_code_agent",
]

_CHAT_MODEL_TYPES = [
    "openaiChatModel",
    "anthropicChatModel",
    "geminiChatModel",
    "openrouterChatModel",
    "groqChatModel",
    "cerebrasChatModel",
    "deepseekChatModel",
    "kimiChatModel",
    "mistralChatModel",
]

_SEARCH_TYPES = ["braveSearch", "serperSearch", "perplexitySearch"]

_CODE_EXECUTOR_TYPES = ["pythonExecutor", "javascriptExecutor", "typescriptExecutor"]


NODE_OUTPUT_SCHEMAS: dict[str, type[BaseModel]] = {
    # workflow / triggers
    "start": StartOutput,
    "chatTrigger": ChatTriggerOutput,
    "taskTrigger": TaskTriggerOutput,
    "webhookTrigger": WebhookTriggerOutput,
    "cronScheduler": CronSchedulerOutput,
    # AI
    **{t: AIAgentOutput for t in _AGENT_TYPES},
    **{t: AIAgentOutput for t in _CHAT_MODEL_TYPES},
    "simpleMemory": SimpleMemoryOutput,
    # code
    **{t: CodeExecutorOutput for t in _CODE_EXECUTOR_TYPES},
    # network
    "httpRequest": HttpRequestOutput,
    # whatsapp
    "whatsappReceive": WhatsAppReceiveOutput,
    "whatsappSend": WhatsAppSendOutput,
    "whatsappDb": WhatsAppDbOutput,
    # telegram entries are registered by nodes/telegram/__init__.py via
    # register_output_schema() to keep all telegram code self-contained.
    # twitter
    "twitterSend": TwitterSendOutput,
    "twitterSearch": TwitterSearchOutput,
    "twitterUser": TwitterUserOutput,
    # social
    "socialReceive": SocialReceiveOutput,
    "socialSend": SocialSendOutput,
    # google workspace
    "googleGmail": GmailOutput,
    "googleGmailReceive": GmailReceiveOutput,
    "googleCalendar": CalendarOutput,
    "googleDrive": DriveOutput,
    "googleSheets": SheetsOutput,
    "googleTasks": TasksOutput,
    "googleContacts": ContactsOutput,
    # search
    **{t: SearchOutput for t in _SEARCH_TYPES},
    # location
    "location": LocationOutput,
    "gmaps_locations": LocationOutput,
    "gmaps_nearby_places": LocationOutput,
    # filesystem
    "fileRead": FileReadOutput,
    "fileModify": FileModifyOutput,
    "shell": ShellOutput,
    "fsSearch": FsSearchOutput,
    # document processing
    "httpScraper": HttpScraperOutput,
    "fileDownloader": FileDownloaderOutput,
    "documentParser": DocumentParserOutput,
    "textChunker": TextChunkerOutput,
    "embeddingGenerator": EmbeddingGeneratorOutput,
    "vectorStore": VectorStoreOutput,
    # web scrapers / browser
    "apifyActor": ApifyOutput,
    "browser": BrowserOutput,
    "crawleeScraper": CrawleeOutput,
    # proxy
    "proxyRequest": ProxyRequestOutput,
    "proxyStatus": ProxyStatusOutput,
    "proxyConfig": ProxyConfigOutput,
    # email
    "emailSend": EmailSendOutput,
    "emailRead": EmailReadOutput,
    "emailReceive": EmailReceiveOutput,
    # android (monitoring)
    "batteryMonitor": BatteryMonitorOutput,
    "systemInfo": SystemInfoOutput,
    "networkMonitor": NetworkMonitorOutput,
    # android (apps)
    "appLauncher": AppLauncherOutput,
    "appList": AppListOutput,
    # android (automation)
    "wifiAutomation": WifiAutomationOutput,
    "bluetoothAutomation": BluetoothAutomationOutput,
    "audioAutomation": AudioAutomationOutput,
    "deviceStateAutomation": DeviceStateAutomationOutput,
    "screenControlAutomation": ScreenControlAutomationOutput,
    "airplaneModeControl": AirplaneModeControlOutput,
    # android (sensors / media)
    "motionDetection": MotionDetectionOutput,
    "environmentalSensors": EnvironmentalSensorsOutput,
    "cameraControl": CameraControlOutput,
    "mediaControl": MediaControlOutput,
}


# Cache of compiled JSON Schemas so we don't re-serialise on every request.
_schema_cache: dict[str, dict[str, Any]] = {}


def get_node_output_schema(node_type: str) -> Optional[dict[str, Any]]:
    """Return the JSON Schema for a node's output, or None if the node
    type has no declared schema. Cached per-process."""

    if node_type in _schema_cache:
        return _schema_cache[node_type]
    model = NODE_OUTPUT_SCHEMAS.get(node_type)
    if model is None:
        return None
    schema = model.model_json_schema()
    _schema_cache[node_type] = schema
    return schema


def list_node_types_with_schema() -> list[str]:
    """Exposed to the frontend so it can probe which node types have
    schemas before making individual requests. Alphabetised for a stable
    client cache key."""

    return sorted(NODE_OUTPUT_SCHEMAS.keys())


from services.plugin.registry import IdempotentRegistry as _IdempotentRegistry  # noqa: E402


def _bust_schema_cache(node_type: str, _model_class: type[BaseModel]) -> None:
    """on_register hook: drop the cached JSON schema for the re-registered type."""
    _schema_cache.pop(node_type, None)


# Backed by the module-level NODE_OUTPUT_SCHEMAS dict so existing
# readers (e.g. get_output_schema, list_node_types_with_schema, tests)
# keep working.
_OUTPUT_SCHEMA_REGISTRY: _IdempotentRegistry[str, type[BaseModel]] = (
    _IdempotentRegistry(
        "output_schema",
        items=NODE_OUTPUT_SCHEMAS,
        on_register=_bust_schema_cache,
    )
)


def register_output_schema(node_type: str, model_class: type[BaseModel]) -> None:
    """Publish an output schema for a node type from a plugin package.

    Plugin folders (``nodes/<group>/__init__.py``) use this to register
    their own ``Output`` Pydantic class so the central registry above
    doesn't need to import or duplicate it. Idempotent on re-import;
    registering a different class for an existing type raises
    ``ValueError``.

    See e.g. ``nodes/telegram/__init__.py``.
    """
    _OUTPUT_SCHEMA_REGISTRY.register(node_type, model_class)
