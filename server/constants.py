"""Centralized constants for node types and categories.

This module provides a single source of truth for all node type definitions,
eliminating duplicate string arrays across the codebase.
"""

from typing import FrozenSet

# =============================================================================
# AI NODE TYPES
# =============================================================================

AI_CHAT_MODEL_TYPES: FrozenSet[str] = frozenset([
    'openaiChatModel',
    'anthropicChatModel',
    'geminiChatModel',
    'openrouterChatModel',
    'groqChatModel',
    'cerebrasChatModel',
    'deepseekChatModel',
    'kimiChatModel',
    'mistralChatModel',
    # Local-server providers (Phase 1 of the LiteLLM adoption — see
    # plans/i-plan-to-implement-nested-orbit.md).
    'ollamaChatModel',
    'lmstudioChatModel',
])

AI_AGENT_TYPES: FrozenSet[str] = frozenset([
    'aiAgent',
    'chatAgent',
    'android_agent',
    'coding_agent',
    'web_agent',
    'task_agent',
    'social_agent',
    'travel_agent',
    'tool_agent',
    'productivity_agent',
    'payments_agent',
    'consumer_agent',
    'autonomous_agent',
    'orchestrator_agent',
    'ai_employee',
    'rlm_agent',
    'claude_code_agent',
])

AI_MEMORY_TYPES: FrozenSet[str] = frozenset([
    'simpleMemory',
])

# Tool node types (connect to AI Agent's input-tools handle)
AI_TOOL_TYPES: FrozenSet[str] = frozenset([
    'calculatorTool',
    'currentTimeTool',
    'duckduckgoSearch',
    'androidTool',
    'writeTodos',
    'processManager',
])

# Skill node types (connect to Zeenie's input-skill handle)
# Skills provide SKILL.md context/instructions, not executed as workflow nodes
# masterSkill: Aggregates built-in skills and user-created skills with inline editing
SKILL_NODE_TYPES: FrozenSet[str] = frozenset([
    'masterSkill',
])

# Toolkit node types (aggregate sub-nodes, n8n Sub-Node pattern)
# Sub-nodes connected to toolkits should not execute independently -
# they only execute when called via the toolkit's tool interface
TOOLKIT_NODE_TYPES: FrozenSet[str] = frozenset([
    'androidTool',  # Aggregates Android service nodes (batteryMonitor, location, etc.)
])

# All AI-related node types (for API key injection)
AI_MODEL_TYPES: FrozenSet[str] = AI_AGENT_TYPES | AI_CHAT_MODEL_TYPES

# =============================================================================
# CONFIG NODE TYPES (excluded from workflow execution)
# =============================================================================

# Config nodes provide configuration to other nodes via special handles
# (input-memory, input-tools, input-model, input-skill).
# They don't execute independently - they're used by their parent nodes.
CONFIG_NODE_TYPES: FrozenSet[str] = (
    AI_MEMORY_TYPES |    # Memory nodes (connect to input-memory)
    AI_TOOL_TYPES |      # Tool nodes (connect to AI Agent's input-tools)
    AI_CHAT_MODEL_TYPES |  # Model config nodes (connect to input-model)
    SKILL_NODE_TYPES     # Skill nodes (connect to Zeenie's input-skill)
)

# =============================================================================
# PROXY NODE TYPES
# =============================================================================

PROXY_NODE_TYPES: FrozenSet[str] = frozenset([
    'proxyRequest',
    'proxyConfig',
    'proxyStatus',
])

# Dual-purpose proxy nodes (workflow node + AI tool)
PROXY_TOOL_TYPES: FrozenSet[str] = frozenset([
    'proxyRequest',
    'proxyConfig',
    'proxyStatus',
])

# =============================================================================
# BROWSER NODE TYPES
# =============================================================================

BROWSER_NODE_TYPES: FrozenSet[str] = frozenset([
    'browser',
])

BROWSER_TOOL_TYPES: FrozenSet[str] = frozenset([
    'browser',
])

# =============================================================================
# SEARCH NODE TYPES
# =============================================================================

SEARCH_NODE_TYPES: FrozenSet[str] = frozenset([
    'braveSearch',
    'serperSearch',
    'perplexitySearch',
])

# Dual-purpose search nodes (workflow node + AI tool)
SEARCH_TOOL_TYPES: FrozenSet[str] = frozenset([
    'braveSearch',
    'serperSearch',
    'perplexitySearch',
])

# =============================================================================
# GOOGLE MAPS NODE TYPES
# =============================================================================

GOOGLE_MAPS_TYPES: FrozenSet[str] = frozenset([
    'gmaps_create',
    'gmaps_locations',
    'gmaps_nearby_places',
])

# =============================================================================
# ANDROID NODE TYPES
# =============================================================================

# System monitoring nodes
ANDROID_MONITORING_TYPES: FrozenSet[str] = frozenset([
    'batteryMonitor',
    'networkMonitor',
    'systemInfo',
    'location',
])

# App management nodes
ANDROID_APP_TYPES: FrozenSet[str] = frozenset([
    'appLauncher',
    'appList',
])

# Device automation nodes
ANDROID_AUTOMATION_TYPES: FrozenSet[str] = frozenset([
    'wifiAutomation',
    'bluetoothAutomation',
    'audioAutomation',
    'deviceStateAutomation',
    'screenControlAutomation',
    'airplaneModeControl',
])

# Sensor nodes
ANDROID_SENSOR_TYPES: FrozenSet[str] = frozenset([
    'motionDetection',
    'environmentalSensors',
])

# Media nodes
ANDROID_MEDIA_TYPES: FrozenSet[str] = frozenset([
    'cameraControl',
    'mediaControl',
])

# All Android service node types (combined)
ANDROID_SERVICE_NODE_TYPES: FrozenSet[str] = (
    ANDROID_MONITORING_TYPES |
    ANDROID_APP_TYPES |
    ANDROID_AUTOMATION_TYPES |
    ANDROID_SENSOR_TYPES |
    ANDROID_MEDIA_TYPES
)

# =============================================================================
# WHATSAPP NODE TYPES
# =============================================================================

WHATSAPP_TYPES: FrozenSet[str] = frozenset([
    'whatsappSend',
    'whatsappReceive',
    'whatsappDb',
])

# =============================================================================
# TWITTER NODE TYPES
# =============================================================================

TWITTER_TYPES: FrozenSet[str] = frozenset([
    'twitterSend',
    'twitterReceive',
    'twitterSearch',
    'twitterUser',
])

# Dual-purpose Twitter nodes (workflow node + AI tool)
TWITTER_TOOL_TYPES: FrozenSet[str] = frozenset([
    'twitterSend',
    'twitterSearch',
    'twitterUser',
])

# =============================================================================
# SOCIAL NODE TYPES (unified messaging)
# =============================================================================

SOCIAL_NODE_TYPES: FrozenSet[str] = frozenset([
    'socialReceive',
    'socialSend',
])

# Dual-purpose social nodes (workflow node + AI tool)
SOCIAL_TOOL_TYPES: FrozenSet[str] = frozenset([
    'socialSend',  # Can be used as AI Agent tool
])

# =============================================================================
# EMAIL NODE TYPES (Himalaya CLI-based IMAP/SMTP)
# =============================================================================

EMAIL_TYPES: FrozenSet[str] = frozenset([
    'emailSend',
    'emailRead',
    'emailReceive',
])

# Dual-purpose email nodes (workflow node + AI tool)
EMAIL_TOOL_TYPES: FrozenSet[str] = frozenset([
    'emailSend',
    'emailRead',
])

# =============================================================================
# CHAT NODE TYPES
# =============================================================================

CHAT_TYPES: FrozenSet[str] = frozenset([
    'chatSend',
    'chatHistory',
])

# =============================================================================
# UTILITY NODE TYPES
# =============================================================================

CODE_EXECUTOR_TYPES: FrozenSet[str] = frozenset([
    'pythonExecutor',
    'javascriptExecutor',
])

HTTP_TYPES: FrozenSet[str] = frozenset([
    'httpRequest',
    'webhookResponse',
])

TEXT_TYPES: FrozenSet[str] = frozenset([
    'textGenerator',
    'fileHandler',
])

# =============================================================================
# WORKFLOW CONTROL NODE TYPES
# =============================================================================

WORKFLOW_CONTROL_TYPES: FrozenSet[str] = frozenset([
    'start',
    'cronScheduler',
])

# =============================================================================
# TRIGGER NODE TYPES (handled by event_waiter)
# =============================================================================

# Event-driven triggers that wait for external events
EVENT_TRIGGER_TYPES: FrozenSet[str] = frozenset([
    'webhookTrigger',
    'whatsappReceive',
    'twitterReceive',
    'workflowTrigger',
    'chatTrigger',
    'taskTrigger',
])

# Legacy alias for backwards compatibility
TRIGGER_TYPES: FrozenSet[str] = EVENT_TRIGGER_TYPES

# =============================================================================
# POLLING TRIGGER TYPES (require active API polling in deployment mode)
# =============================================================================

# Polling triggers need to actively poll an external API for new data,
# unlike push-based triggers (WhatsApp, Webhook) that receive events externally.
# In deployment mode, these get a dedicated polling loop instead of event_waiter.
POLLING_TRIGGER_TYPES: FrozenSet[str] = frozenset([
    'googleGmailReceive',
    'twitterReceive',
    'emailReceive',
])

# =============================================================================
# ALL TRIGGER NODE TYPES (starting points for workflow graphs)
# =============================================================================

# Combined set of all trigger node types that can start a workflow
# These nodes have no input handles and serve as entry points
WORKFLOW_TRIGGER_TYPES: FrozenSet[str] = frozenset([
    # Manual start
    'start',
    # Scheduled triggers
    'cronScheduler',
    # Event-driven triggers
    'webhookTrigger',
    'whatsappReceive',
    'workflowTrigger',
    'chatTrigger',
    'taskTrigger',
    'twitterReceive',
    'googleGmailReceive',
    'telegramReceive',
    'emailReceive',
])

# =============================================================================
# AI PROVIDER DETECTION
# =============================================================================

def detect_ai_provider(node_type: str, parameters: dict = None) -> str:
    """Detect AI provider from node type or parameters.

    Substring match against the node type's lower-case form. Local-server
    providers (ollama, lmstudio) MUST be checked here — without these
    branches `lmstudioChatModel` falls through to the final ``return
    'openai'`` and ``execute_chat`` ends up calling api.openai.com with
    the local-server placeholder key, which is the exact symptom users
    see as "401 from OpenAI when I picked LM Studio".

    Args:
        node_type: The node type string (e.g. "ollamaChatModel").
        parameters: Optional parameters dict (used for aiAgent/chatAgent
            where the provider lives in a dropdown, not the type).

    Returns:
        Provider string matching a key in ``services.ai.PROVIDER_CONFIGS``.
    """
    # AI Agent types get provider from parameters
    if node_type in AI_AGENT_TYPES:
        return (parameters or {}).get('provider', 'openai')
    nt = node_type.lower()
    # Order matters: more-specific tokens first, so e.g. `lm_studio` /
    # `lmstudio` / `lm-studio` all classify before any future generic
    # `openai`-prefixed match could shadow them.
    if 'deepseek' in nt:
        return 'deepseek'
    if 'kimi' in nt:
        return 'kimi'
    if 'mistral' in nt:
        return 'mistral'
    if 'cerebras' in nt:
        return 'cerebras'
    if 'groq' in nt:
        return 'groq'
    if 'openrouter' in nt:
        return 'openrouter'
    if 'anthropic' in nt:
        return 'anthropic'
    if 'gemini' in nt:
        return 'gemini'
    # Local-server providers — match the LMStudioChatModelNode /
    # OllamaChatModelNode plugin types so the runtime path reads the
    # correct {provider}_proxy credential and the openai SDK is pointed
    # at the user's local server, not api.openai.com.
    if 'lmstudio' in nt or 'lm_studio' in nt or 'lm-studio' in nt:
        return 'lmstudio'
    if 'ollama' in nt:
        return 'ollama'
    return 'openai'
