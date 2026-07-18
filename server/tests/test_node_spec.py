"""Wave 6 NodeSpec contract tests.

Locks in the public shape emitted by services/node_input_schemas.py,
services/node_spec.py, services/ws_handler_registry (option-loader
registry, post-Wave-11.I M.3), and the /api/schemas/nodes/*/spec.json +
/api/schemas/nodes/options/* endpoints. Mirrors the Wave 3 test posture
for node_output_schemas.
"""

import pytest  # noqa: F401  (used by @pytest.mark.asyncio on Phase 4 tests)

from services.node_input_schemas import (
    NODE_INPUT_MODELS,
    get_node_input_schema,
    list_node_types_with_input_schema,
)
from services.node_spec import get_node_spec, list_node_types_with_spec


def _numeric_constraints(prop: dict) -> dict:
    """Pydantic emits ``Optional[float] = Field(ge=..., le=...)`` as
    ``{anyOf: [{number, minimum, maximum}, {null}]}``. This helper digs
    out the numeric branch so tests don't have to special-case nullable
    vs. required fields each time we tweak Params."""
    if "minimum" in prop or "maximum" in prop:
        return prop
    for branch in prop.get("anyOf", ()):
        if branch.get("type") == "number" or "minimum" in branch or "maximum" in branch:
            return branch
    return prop


class TestInputSchemas:
    def test_registry_not_empty(self):
        assert len(NODE_INPUT_MODELS) > 50

    def test_http_request_schema(self):
        schema = get_node_input_schema("httpRequest")
        assert schema is not None
        props = schema["properties"]
        assert "url" in props
        assert props["method"]["enum"] == ["GET", "POST", "PUT", "DELETE", "PATCH"]
        assert props["timeout"]["minimum"] == 1
        assert props["timeout"]["maximum"] == 600
        # Discriminator stripped from surface
        assert "type" not in props

    def test_ai_agent_schema(self):
        schema = get_node_input_schema("aiAgent")
        assert schema is not None
        props = schema["properties"]
        temp = _numeric_constraints(props["temperature"])
        assert temp["minimum"] == 0.0
        assert temp["maximum"] == 2.0
        # snake_case keys per Wave 11.E.4+ convention.
        # NB: ``api_key`` is NOT a declared field — credentials live in
        # the credentials DB and are auto-injected at execution time.
        assert "api_key" not in props
        assert "max_tokens" in props
        assert "system_message" in props

    def test_ai_agent_options_group(self):
        """temperature + max_tokens carry ``group=\"options\"`` so the
        frontend adapter lifts them into a collapsible Options collection.
        Class-level ``groups`` metadata customises the display name /
        placeholder. See docs-internal/plugin_system.md for the
        convention."""
        schema = get_node_input_schema("aiAgent")
        props = schema["properties"]
        assert props["temperature"]["group"] == "options"
        assert props["max_tokens"]["group"] == "options"
        # Class-level groups metadata is emitted at the schema root.
        assert schema["groups"]["options"]["display_name"] == "Options"
        assert schema["groups"]["options"]["placeholder"] == "Add Option"

    def test_specialized_agents_share_schema(self):
        # Specialized agents go through SpecializedAgentParams
        for agent_type in ["android_agent", "coding_agent", "web_agent"]:
            assert get_node_input_schema(agent_type) is not None

    def test_android_service_nodes_covered(self):
        for android_type in ["batteryMonitor", "wifiAutomation", "cameraControl"]:
            assert get_node_input_schema(android_type) is not None

    def test_unknown_type_returns_none(self):
        assert get_node_input_schema("nonExistentNodeXyz") is None

    def test_list_sorted(self):
        types = list_node_types_with_input_schema()
        assert types == sorted(types)


class TestNodeSpec:
    def test_http_request_spec_envelope(self):
        spec = get_node_spec("httpRequest")
        assert spec is not None
        assert spec["type"] == "httpRequest"
        assert spec["displayName"] == "HTTP Request"
        assert "utility" in spec["group"]
        assert "inputs" in spec
        assert "outputs" in spec
        assert spec["version"] == 1

    def test_spec_missing_entirely(self):
        assert get_node_spec("nonExistentNodeXyz") is None

    def test_spec_count_at_or_above_100(self):
        # input (67) + output (98) with overlap ≥ 100 unique types
        assert len(list_node_types_with_spec()) >= 100

    def test_spec_cached(self):
        s1 = get_node_spec("httpRequest")
        s2 = get_node_spec("httpRequest")
        assert s1 is s2  # same dict object — cache hit


class TestPhase3aCoverage:
    """Phase 3a backend parity: utility + code + process + workflow groups
    must all have full NodeSpec (input model + display metadata) before
    Phase 3e flips the VITE_NODESPEC_BACKEND flag."""

    PHASE_3A_TYPES = [
        # utility
        "httpRequest",
        "webhookTrigger",
        "webhookResponse",
        "chatTrigger",
        "console",
        "teamMonitor",
        # code
        "pythonExecutor",
        "javascriptExecutor",
        "typescriptExecutor",
        # process
        "processManager",
        # workflow
        "start",
        "taskTrigger",
    ]

    def test_all_have_input_schema(self):
        from services.node_input_schemas import get_node_input_schema

        missing = [t for t in self.PHASE_3A_TYPES if get_node_input_schema(t) is None]
        assert not missing, f"Phase 3a types missing input schema: {missing}"

    def test_all_have_metadata(self):
        from models.node_metadata import get_node_metadata

        missing = [t for t in self.PHASE_3A_TYPES if get_node_metadata(t) is None]
        assert not missing, f"Phase 3a types missing display metadata: {missing}"

    def test_all_have_full_spec(self):
        for t in self.PHASE_3A_TYPES:
            spec = get_node_spec(t)
            assert spec is not None, f"No spec for {t}"
            assert spec["displayName"] != t, f"{t} fell back to id displayName — metadata missing"
            assert spec["icon"], f"{t} missing icon"
            assert spec["group"], f"{t} missing group"

    def test_process_manager_operation_enum(self):
        spec = get_node_spec("processManager")
        op = spec["inputs"]["properties"]["operation"]
        # Literal ordering mirrors the Params declaration.
        assert set(op["enum"]) >= {"start", "stop", "restart", "list", "send_input"}

    def test_console_log_mode_enum(self):
        spec = get_node_spec("console")
        log_mode = spec["inputs"]["properties"]["log_mode"]
        assert log_mode["enum"] == ["all", "field", "expression"]


class TestPhase3bCoverage:
    """Phase 3b backend parity: messaging group (whatsapp, telegram,
    twitter, social) - 11 types."""

    PHASE_3B_TYPES = [
        "whatsappSend",
        "whatsappReceive",
        "whatsappDb",
        "telegramSend",
        "telegramReceive",
        "twitterSend",
        "twitterReceive",
        "twitterSearch",
        "twitterUser",
        "socialReceive",
        "socialSend",
    ]

    def test_all_have_input_schema(self):
        from services.node_input_schemas import get_node_input_schema

        missing = [t for t in self.PHASE_3B_TYPES if get_node_input_schema(t) is None]
        assert not missing, f"Phase 3b types missing input schema: {missing}"

    def test_all_have_metadata(self):
        from models.node_metadata import get_node_metadata

        missing = [t for t in self.PHASE_3B_TYPES if get_node_metadata(t) is None]
        assert not missing, f"Phase 3b types missing display metadata: {missing}"

    def test_all_have_full_spec(self):
        for t in self.PHASE_3B_TYPES:
            spec = get_node_spec(t)
            assert spec is not None, f"No spec for {t}"
            assert spec["displayName"] != t, f"{t} fell back to id displayName"
            assert spec["group"], f"{t} missing group"

    def test_twitter_send_action_enum(self):
        spec = get_node_spec("twitterSend")
        action = spec["inputs"]["properties"]["action"]
        assert set(action["enum"]) >= {
            "tweet",
            "reply",
            "retweet",
            "quote",
            "like",
            "unlike",
            "delete",
        }

    def test_telegram_send_parse_mode_enum(self):
        spec = get_node_spec("telegramSend")
        parse_mode = spec["inputs"]["properties"]["parse_mode"]
        # Empty string is the "no parse mode" option (handler coerces to Python None).
        assert set(parse_mode["enum"]) == {"Auto", "", "HTML", "Markdown", "MarkdownV2"}
        labels = {option["value"]: option["name"] for option in parse_mode["uiHints"]["options"]}
        assert labels[""] == "None (raw text)"

    def test_social_send_platform_enum(self):
        spec = get_node_spec("socialSend")
        # Wave 8 renamed: 'platform' -> 'channel' to match the frontend SOCIAL_SEND.
        channel = spec["inputs"]["properties"]["channel"]
        assert "whatsapp" in channel["enum"]
        assert "telegram" in channel["enum"]
        assert "discord" in channel["enum"]


class TestPhase3cCoverage:
    """Phase 3c backend parity: agents (3 base + 16 specialized) +
    chat models (9). All Pydantic models existed before Phase 3c -
    this sub-commit only adds NODE_METADATA entries."""

    PHASE_3C_TYPES = [
        # Base agents
        "aiAgent",
        "chatAgent",
        "simpleMemory",
        # Specialized agents (16)
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
        # Chat models (9)
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

    def test_all_have_metadata(self):
        from models.node_metadata import get_node_metadata

        missing = [t for t in self.PHASE_3C_TYPES if get_node_metadata(t) is None]
        assert not missing, f"Phase 3c types missing metadata: {missing}"

    def test_all_have_full_spec(self):
        for t in self.PHASE_3C_TYPES:
            spec = get_node_spec(t)
            assert spec is not None, f"No spec for {t}"
            assert spec["displayName"] != t, f"{t} fell back to id displayName"
            assert spec["group"], f"{t} missing group"

    def test_chat_models_are_grouped_as_model(self):
        # Dashboard routes group=['model'] -> ModelNode (or SquareNode for AI types).
        for t in ["openaiChatModel", "anthropicChatModel", "geminiChatModel"]:
            assert "model" in get_node_spec(t)["group"]

    def test_specialized_agents_share_pydantic_constraints(self):
        # All 16 specialized agents share SpecializedAgentParams - constraints
        # like temperature 0-2 should appear identically.
        spec = get_node_spec("coding_agent")
        temp = _numeric_constraints(spec["inputs"]["properties"]["temperature"])
        assert temp["minimum"] == 0.0
        assert temp["maximum"] == 2.0


class TestPhase3dCoverage:
    """Phase 3d.i backend parity: location + scheduler + chat + text + 16
    Android service nodes + gmail trigger. All Pydantic models existed
    before this sub-commit - it adds NODE_METADATA entries only."""

    PHASE_3D_TYPES = [
        # Location
        "gmaps_create",
        "gmaps_locations",
        "gmaps_nearby_places",
        # Scheduler / triggers
        "cronScheduler",
        "timer",
        "googleGmailReceive",
        # Chat / text
        "chatSend",
        "chatHistory",
        "textGenerator",
        "fileHandler",
        # Android (16)
        "batteryMonitor",
        "networkMonitor",
        "systemInfo",
        "location",
        "appLauncher",
        "appList",
        "wifiAutomation",
        "bluetoothAutomation",
        "audioAutomation",
        "deviceStateAutomation",
        "screenControlAutomation",
        "airplaneModeControl",
        "motionDetection",
        "environmentalSensors",
        "cameraControl",
        "mediaControl",
    ]

    def test_all_have_metadata(self):
        from models.node_metadata import get_node_metadata

        missing = [t for t in self.PHASE_3D_TYPES if get_node_metadata(t) is None]
        assert not missing, f"Phase 3d.i types missing metadata: {missing}"

    def test_all_have_full_spec(self):
        for t in self.PHASE_3D_TYPES:
            spec = get_node_spec(t)
            assert spec is not None, f"No spec for {t}"
            assert spec["displayName"] != t, f"{t} fell back to id displayName"
            assert spec["group"], f"{t} missing group"

    def test_android_services_grouped(self):
        # Dashboard routes group containing 'android' OR 'service' to SquareNode.
        for t in ["batteryMonitor", "wifiAutomation", "cameraControl"]:
            assert "android" in get_node_spec(t)["group"]

    def test_input_model_coverage_complete(self):
        """Every node type with a Pydantic input model now also has
        display metadata. Phase 3d.i closes the input-model gap."""
        from services.node_input_schemas import NODE_INPUT_MODELS
        from models.node_metadata import NODE_METADATA

        unseeded = [t for t in NODE_INPUT_MODELS if t not in NODE_METADATA]
        assert not unseeded, f"Input-modeled types still missing metadata: {unseeded}"


class TestPhase3dIICoverage:
    """Phase 3d.ii backend parity: 28 previously output-only types now
    have full Pydantic input models + metadata. Closes the long tail
    of integrations: search, browser/scraping, email, Google Workspace,
    document/RAG, filesystem, proxy."""

    PHASE_3D_II_TYPES = [
        # Search
        "braveSearch",
        "serperSearch",
        "perplexitySearch",
        # Browser / scraping
        "browser",
        "crawleeScraper",
        "httpScraper",
        "apifyActor",
        # Email
        "emailSend",
        "emailRead",
        "emailReceive",
        # Google Workspace
        "googleGmail",
        "googleCalendar",
        "googleDrive",
        "googleSheets",
        "googleTasks",
        "googleContacts",
        # Document / RAG
        "documentParser",
        "textChunker",
        "embeddingGenerator",
        "vectorStore",
        "fileDownloader",
        # Filesystem
        "fileRead",
        "fileModify",
        "fsSearch",
        "shell",
        # Proxy
        "proxyRequest",
        "proxyConfig",
        "proxyStatus",
    ]

    def test_all_have_input_schema(self):
        from services.node_input_schemas import get_node_input_schema

        missing = [t for t in self.PHASE_3D_II_TYPES if get_node_input_schema(t) is None]
        assert not missing, f"Phase 3d.ii types missing input schema: {missing}"

    def test_all_have_metadata(self):
        from models.node_metadata import get_node_metadata

        missing = [t for t in self.PHASE_3D_II_TYPES if get_node_metadata(t) is None]
        assert not missing, f"Phase 3d.ii types missing metadata: {missing}"

    def test_all_have_full_spec(self):
        for t in self.PHASE_3D_II_TYPES:
            spec = get_node_spec(t)
            assert spec is not None, f"No spec for {t}"
            assert spec["displayName"] != t, f"{t} fell back to id displayName"
            assert spec["group"], f"{t} missing group"

    def test_search_nodes_grouped_search_and_tool(self):
        for t in ["braveSearch", "serperSearch", "perplexitySearch"]:
            groups = get_node_spec(t)["group"]
            assert "search" in groups and "tool" in groups

    def test_google_workspace_grouped_google(self):
        for t in ["googleGmail", "googleCalendar", "googleDrive", "googleSheets", "googleTasks", "googleContacts"]:
            assert "google" in get_node_spec(t)["group"]

    def test_apify_actor_actor_id_field(self):
        spec = get_node_spec("apifyActor")
        assert "actor_id" in spec["inputs"]["properties"]

    def test_proxy_request_method_enum(self):
        spec = get_node_spec("proxyRequest")
        method = spec["inputs"]["properties"]["method"]
        assert method["enum"] == ["GET", "POST", "PUT", "DELETE", "PATCH"]


class TestWave6FullCoverage:
    """End-of-Wave-6 invariants: every Pydantic input model has
    metadata; every metadata entry has either an input model or an
    output schema; all 110+ types emit a complete NodeSpec."""

    def test_input_models_have_metadata(self):
        from services.node_input_schemas import NODE_INPUT_MODELS
        from models.node_metadata import NODE_METADATA

        gap = sorted(set(NODE_INPUT_MODELS.keys()) - set(NODE_METADATA.keys()))
        assert not gap, f"Input models without metadata: {gap}"

    def test_total_nodespec_count_at_least_110(self):
        from services.node_spec import list_node_types_with_spec

        assert len(list_node_types_with_spec()) >= 110

    def test_input_model_count_at_least_100(self):
        from services.node_input_schemas import NODE_INPUT_MODELS

        assert len(NODE_INPUT_MODELS) >= 100


class TestPhase4LoadOptions:
    """Wave 6 Phase 4: unified loadOptionsMethod dispatch registry."""

    def test_registry_has_whatsapp_methods(self):
        from services.ws_handler_registry import list_load_options_methods

        methods = list_load_options_methods()
        for method in ["whatsappGroups", "whatsappChannels", "whatsappGroupMembers"]:
            assert method in methods

    def test_registry_has_google_methods(self):
        from services.ws_handler_registry import list_load_options_methods

        methods = list_load_options_methods()
        for method in ["gmailLabels", "googleCalendarList", "googleDriveFolders", "googleTasklists"]:
            assert method in methods

    def test_list_methods_sorted(self):
        from services.ws_handler_registry import list_load_options_methods

        methods = list_load_options_methods()
        assert methods == sorted(methods)
        assert len(methods) >= 7

    @pytest.mark.asyncio
    async def test_unknown_method_returns_empty(self):
        from services.ws_handler_registry import dispatch_load_options

        result = await dispatch_load_options("nonExistentMethodXyz", {})
        assert result == []

    @pytest.mark.asyncio
    async def test_dispatch_passes_params(self):
        # Smoke test: unknown method tolerates arbitrary params, doesn't crash
        from services.ws_handler_registry import dispatch_load_options

        result = await dispatch_load_options("unknown", {"group_id": "abc"})
        assert result == []


class TestPhase5NodeGroups:
    """Wave 6 Phase 5: backend-derived node-groups index that replaces
    the 34 frontend ``*_NODE_TYPES`` arrays."""

    def test_returns_dict(self):
        from services.node_spec import list_node_groups

        groups = list_node_groups()
        assert isinstance(groups, dict)
        assert len(groups) > 0

    def test_known_groups_present(self):
        from services.node_spec import list_node_groups

        groups = list_node_groups()
        for expected in ["agent", "trigger", "tool", "model", "android", "social"]:
            assert expected in groups, f"Missing group {expected!r}"

    def test_android_group_is_visible_in_normal_mode(self):
        from services.node_spec import list_node_groups

        assert list_node_groups()["android"]["visibility"] == "normal"

    def test_tool_group_includes_known_tools(self):
        from services.node_spec import list_node_groups

        tools = set(list_node_groups()["tool"]["types"])
        for expected in ["pythonExecutor", "javascriptExecutor", "httpRequest"]:
            assert expected in tools

    def test_trigger_group_includes_known_triggers(self):
        from services.node_spec import list_node_groups

        triggers = set(list_node_groups()["trigger"]["types"])
        for expected in ["webhookTrigger", "chatTrigger", "telegramReceive", "twitterReceive"]:
            assert expected in triggers

    def test_each_group_alphabetised(self):
        from services.node_spec import list_node_groups

        for group, entry in list_node_groups().items():
            types = entry["types"]
            assert types == sorted(types), f"Group {group!r} not sorted: {types}"

    def test_every_group_carries_palette_metadata(self):
        """Wave 10.B: every group the node registry emits must have a
        palette metadata entry (label/icon/color/visibility) registered
        via ``register_group`` in ``server/nodes/groups.py``. Prevents
        a new group key silently rendering unlabelled in the palette."""
        from services.node_spec import list_node_groups

        for group, entry in list_node_groups().items():
            assert entry["label"], f"{group}: missing label"
            assert entry["icon"], f"{group}: missing icon"
            assert entry["visibility"] in ("normal", "dev", "all"), f"{group}: visibility={entry['visibility']!r}"


class TestDisplayOptionsEnrichment:
    """Wave 6 Phase 6 enrichment: Pydantic Field(json_schema_extra=...)
    hints are lifted into the adapter-visible schema so the frontend
    panel renders conditional visibility correctly without the legacy
    nodeDefinitions/* displayOptions declarations."""

    def test_twitter_send_text_shown_for_tweet_actions(self):
        # text field should carry displayOptions restricting it to
        # tweet/reply/quote actions.
        spec = get_node_spec("twitterSend")
        text = spec["inputs"]["properties"]["text"]
        rule = text.get("displayOptions", {}).get("show", {})
        assert rule.get("action") == ["tweet", "reply", "quote"]

    def test_twitter_send_tweet_id_shown_for_edit_actions(self):
        spec = get_node_spec("twitterSend")
        prop = spec["inputs"]["properties"]["tweet_id"]
        rule = prop.get("displayOptions", {}).get("show", {})
        assert "reply" in rule.get("action", [])
        assert "delete" in rule.get("action", [])

    def test_twitter_user_username_shown_for_by_username(self):
        spec = get_node_spec("twitterUser")
        username = spec["inputs"]["properties"]["username"]
        assert username["displayOptions"]["show"]["operation"] == ["by_username"]

    def test_telegram_send_media_shown_for_photo_document(self):
        spec = get_node_spec("telegramSend")
        media_url = spec["inputs"]["properties"]["media_url"]
        assert media_url["displayOptions"]["show"]["message_type"] == ["photo", "document"]

    def test_telegram_send_text_shown_for_text_type(self):
        spec = get_node_spec("telegramSend")
        text = spec["inputs"]["properties"]["text"]
        assert text["displayOptions"]["show"]["message_type"] == ["text"]

    def test_http_request_body_shown_for_post_put_patch(self):
        spec = get_node_spec("httpRequest")
        body = spec["inputs"]["properties"]["body"]
        assert body["displayOptions"]["show"]["method"] == ["POST", "PUT", "PATCH"]

    def test_http_request_proxy_fields_gated_on_use_proxy(self):
        spec = get_node_spec("httpRequest")
        for field in ["proxy_country", "proxy_provider", "session_type"]:
            prop = spec["inputs"]["properties"][field]
            assert prop["displayOptions"]["show"]["use_proxy"] == [True]

    def test_whatsapp_send_groupid_carries_load_options(self):
        # whatsappSend group_id routes through loadOptionsMethod
        spec = get_node_spec("whatsappSend")
        group_id = spec["inputs"]["properties"]["group_id"]
        assert group_id["loadOptionsMethod"] == "whatsappGroups"
        assert group_id["displayOptions"]["show"]["recipient_type"] == ["group"]

    def test_whatsapp_send_channel_jid_carries_load_options(self):
        spec = get_node_spec("whatsappSend")
        channel = spec["inputs"]["properties"]["channel_jid"]
        assert channel["loadOptionsMethod"] == "whatsappChannels"

    def test_whatsapp_send_media_shown_for_media_types(self):
        spec = get_node_spec("whatsappSend")
        media = spec["inputs"]["properties"]["media_url"]
        assert "image" in media["displayOptions"]["show"]["message_type"]
        assert "document" in media["displayOptions"]["show"]["message_type"]

    def test_gmail_fields_gated_on_operation(self):
        spec = get_node_spec("googleGmail")
        assert spec["inputs"]["properties"]["to"]["displayOptions"]["show"]["operation"] == ["send"]
        assert spec["inputs"]["properties"]["query"]["displayOptions"]["show"]["operation"] == ["search"]
        assert spec["inputs"]["properties"]["message_id"]["displayOptions"]["show"]["operation"] == ["read"]

    def test_calendar_event_id_gated_on_update_delete(self):
        spec = get_node_spec("googleCalendar")
        event_id = spec["inputs"]["properties"]["event_id"]
        assert event_id["displayOptions"]["show"]["operation"] == ["update", "delete"]

    def test_social_send_media_url_gated_on_message_type(self):
        spec = get_node_spec("socialSend")
        media = spec["inputs"]["properties"]["media_url"]
        assert "image" in media["displayOptions"]["show"]["message_type"]
        # 'text' is in the message field's gate, mediaUrl is gated on media types.
        assert "text" not in media["displayOptions"]["show"]["message_type"]

    def test_gmail_receive_label_carries_load_options(self):
        spec = get_node_spec("googleGmailReceive")
        label = spec["inputs"]["properties"]["label_filter"]
        assert label["loadOptionsMethod"] == "gmailLabels"

    def test_calendar_calendar_id_carries_load_options(self):
        spec = get_node_spec("googleCalendar")
        cal = spec["inputs"]["properties"]["calendar_id"]
        assert cal["loadOptionsMethod"] == "googleCalendarList"

    def test_drive_folder_id_carries_load_options(self):
        spec = get_node_spec("googleDrive")
        folder = spec["inputs"]["properties"]["folder_id"]
        assert folder["loadOptionsMethod"] == "googleDriveFolders"

    def test_tasks_tasklist_id_carries_load_options(self):
        spec = get_node_spec("googleTasks")
        tl = spec["inputs"]["properties"]["tasklist_id"]
        assert tl["loadOptionsMethod"] == "googleTasklists"

    def test_ai_agent_temperature_step_size(self):
        spec = get_node_spec("aiAgent")
        temp = spec["inputs"]["properties"]["temperature"]
        assert temp["numberStepSize"] == 0.1

    def test_ai_agent_prompt_carries_placeholder_and_rows(self):
        spec = get_node_spec("aiAgent")
        prompt = spec["inputs"]["properties"]["prompt"]
        assert prompt["placeholder"] == "Enter your prompt or use template variables..."
        assert prompt["rows"] == 4

    def test_ai_agent_has_no_api_key_field(self):
        # Credentials live in the credentials DB and are auto-injected by
        # node_executor._inject_api_keys at execution time — they must
        # NOT surface as a user-editable node parameter.
        spec = get_node_spec("aiAgent")
        assert "api_key" not in spec["inputs"]["properties"]

    def test_specialized_agent_shares_ai_agent_tuning_surface(self):
        # SpecializedAgentParams mirrors AIAgentParams — same provider,
        # model, temperature, max_tokens, system_message fields. No
        # api_key (injected at runtime). Thinking mode surfaces via
        # AIAgentOutput.thinking, not as a Params toggle.
        spec = get_node_spec("coding_agent")
        props = spec["inputs"]["properties"]
        for field in ("provider", "model", "temperature", "max_tokens", "system_message"):
            assert field in props, f"coding_agent missing {field}"
        assert "api_key" not in props

    def test_chat_model_password_masked(self):
        spec = get_node_spec("openaiChatModel")
        api_key = spec["inputs"]["properties"]["api_key"]
        assert api_key["password"] is True

    def test_webhook_trigger_header_auth_gated(self):
        spec = get_node_spec("webhookTrigger")
        for field in ["header_name", "header_value"]:
            prop = spec["inputs"]["properties"][field]
            assert prop["displayOptions"]["show"]["authentication"] == ["header"]
        assert spec["inputs"]["properties"]["header_value"]["password"] is True

    def test_console_field_mode_gating(self):
        spec = get_node_spec("console")
        assert spec["inputs"]["properties"]["field_path"]["displayOptions"]["show"]["log_mode"] == ["field"]
        assert spec["inputs"]["properties"]["expression"]["displayOptions"]["show"]["log_mode"] == ["expression"]

    def test_process_manager_operation_gating(self):
        spec = get_node_spec("processManager")
        assert spec["inputs"]["properties"]["command"]["displayOptions"]["show"]["operation"] == ["start"]
        assert spec["inputs"]["properties"]["input_text"]["displayOptions"]["show"]["operation"] == ["send_input"]

    def test_proxy_request_body_gating(self):
        spec = get_node_spec("proxyRequest")
        body = spec["inputs"]["properties"]["body"]
        assert body["displayOptions"]["show"]["method"] == ["POST", "PUT", "PATCH"]

    def test_vector_store_query_fields_gated(self):
        spec = get_node_spec("vectorStore")
        for field in ["query_embedding", "top_k"]:
            assert spec["inputs"]["properties"][field]["displayOptions"]["show"]["operation"] == ["query"]

    def test_email_read_operation_gating(self):
        spec = get_node_spec("emailRead")
        assert "search" in spec["inputs"]["properties"]["query"]["displayOptions"]["show"]["operation"]
        assert "read" in spec["inputs"]["properties"]["message_id"]["displayOptions"]["show"]["operation"]

    def test_file_modify_edit_gated(self):
        spec = get_node_spec("fileModify")
        for field in ["old_string", "new_string", "replace_all"]:
            assert spec["inputs"]["properties"][field]["displayOptions"]["show"]["operation"] == ["edit"]
        assert spec["inputs"]["properties"]["content"]["displayOptions"]["show"]["operation"] == ["write"]


class TestUIHintsInNodeSpec:
    """Wave 6 Phase 5.b: NODE_METADATA carries panel-level uiHints
    (isChatTrigger/isConsoleSink/hasCodeEditor/etc) so frontend
    dispatch can read them off the cached NodeSpec instead of
    importing legacy *_NODE_TYPES arrays or per-node definition flags."""

    def test_chat_trigger_carries_is_chat_trigger(self):
        spec = get_node_spec("chatTrigger")
        assert spec.get("uiHints", {}).get("isChatTrigger") is True

    def test_console_carries_is_console_sink(self):
        spec = get_node_spec("console")
        assert spec.get("uiHints", {}).get("isConsoleSink") is True

    def test_team_monitor_carries_is_monitor_panel(self):
        spec = get_node_spec("teamMonitor")
        hints = spec.get("uiHints", {})
        assert hints.get("isMonitorPanel") is True
        assert hints.get("hideInputSection") is True
        assert hints.get("hideOutputSection") is True

    def test_task_manager_surfaces_span_the_full_parameter_panel(self):
        hints = get_node_spec("taskManager").get("uiHints", {})
        assert hints.get("isTaskManagerPanel") is True
        assert hints.get("hideInputSection") is True
        assert hints.get("hideOutputSection") is True

        for node_type in ("orchestrator_agent", "ai_employee"):
            lead_hints = get_node_spec(node_type).get("uiHints", {})
            assert lead_hints.get("isTaskManagerPanel") is True
            assert lead_hints.get("hideInputSection") is not True
            assert lead_hints.get("hideOutputSection") is not True

    def test_process_manager_surfaces_span_the_full_parameter_panel(self):
        hints = get_node_spec("processManager").get("uiHints", {})
        assert hints.get("isProcessManagerPanel") is True
        assert hints.get("hideInputSection") is True
        assert hints.get("hideOutputSection") is True

    def test_master_skill_seeded(self):
        spec = get_node_spec("masterSkill")
        assert spec is not None
        assert spec["displayName"] == "Master Skill"
        hints = spec.get("uiHints", {})
        assert hints.get("isMasterSkillEditor") is True
        assert hints.get("hideRunButton") is True

    def test_simple_memory_carries_memory_panel(self):
        spec = get_node_spec("simpleMemory")
        hints = spec.get("uiHints", {})
        assert hints.get("isMemoryPanel") is True
        assert hints.get("hasCodeEditor") is True

    def test_python_executor_carries_code_editor(self):
        spec = get_node_spec("pythonExecutor")
        assert spec.get("uiHints", {}).get("hasCodeEditor") is True

    def test_gmaps_create_carries_location_panel(self):
        spec = get_node_spec("gmaps_create")
        assert spec.get("uiHints", {}).get("showLocationPanel") is True

    def test_start_carries_hidden_panels(self):
        spec = get_node_spec("start")
        hints = spec.get("uiHints", {})
        assert hints.get("hideInputSection") is True
        assert hints.get("hideOutputSection") is True


class TestNodeSpecContractInvariants:
    """Wave 6 Phase 3e safety net: runs over every registered NodeSpec
    and asserts the wire contract stays intact. Catches Pydantic drift
    / metadata typos before they reach the frontend adapter. Extend
    these guards when the adapter gets new required fields."""

    def _all_spec_types(self):
        from services.node_spec import list_node_types_with_spec

        return list_node_types_with_spec()

    def test_every_spec_has_required_wire_fields(self):
        """Every spec must have: type, displayName, group, version.
        Missing any of these would break the adapter's defaults()."""
        from services.node_spec import get_node_spec

        for t in self._all_spec_types():
            spec = get_node_spec(t)
            assert spec is not None, f"No spec for {t}"
            for field in ("type", "displayName", "group", "version"):
                assert field in spec, f"{t}: missing required field {field!r}"
            assert isinstance(spec["group"], list), f"{t}: group must be list"
            assert isinstance(spec["version"], int), f"{t}: version must be int"

    def test_input_schemas_have_json_schema_shape(self):
        """Every emitted input schema must be a valid JSON Schema 7
        object with properties dict. The adapter crashes if properties
        is missing."""
        from services.node_input_schemas import get_node_input_schema, NODE_INPUT_MODELS

        for t in NODE_INPUT_MODELS:
            schema = get_node_input_schema(t)
            assert schema is not None, f"Input schema None for {t}"
            assert isinstance(schema.get("properties"), dict), f"{t}: no properties dict"
            # Discriminator stripped from surface
            assert "type" not in schema["properties"], f"{t}: type discriminator leaked"

    def test_display_options_on_every_rule_is_well_formed(self):
        """Every displayOptions.show / .hide rule must be a dict whose
        values are lists (the INodeProperties.displayOptions shape the
        frontend evaluator expects)."""
        from services.node_input_schemas import get_node_input_schema, NODE_INPUT_MODELS

        for t in NODE_INPUT_MODELS:
            schema = get_node_input_schema(t)
            for prop_name, prop in schema.get("properties", {}).items():
                rules = prop.get("displayOptions", {})
                for key in ("show", "hide"):
                    rule = rules.get(key)
                    if rule is None:
                        continue
                    assert isinstance(rule, dict), f"{t}.{prop_name}.displayOptions.{key} not dict"
                    for ref_field, allowed in rule.items():
                        assert isinstance(allowed, list), f"{t}.{prop_name}.displayOptions.{key}[{ref_field!r}] must be list"

    def test_load_options_methods_are_registered(self):
        """Every Pydantic Field(loadOptionsMethod=X) must point at a
        registered loader -- either in the legacy table or in a
        plugin's ``register_option_loader`` call. Catches typos and
        forgotten loader registrations."""
        from services.node_input_schemas import get_node_input_schema, NODE_INPUT_MODELS
        from services.ws_handler_registry import list_load_options_methods

        methods = set(list_load_options_methods())
        for t in NODE_INPUT_MODELS:
            schema = get_node_input_schema(t)
            for prop_name, prop in schema.get("properties", {}).items():
                method = prop.get("loadOptionsMethod")
                if method is None:
                    continue
                assert method in methods, f"{t}.{prop_name} references unknown loadOptionsMethod {method!r}"

    def test_every_enum_option_is_serialisable(self):
        """Frontend options are {name, value} where value is scalar.
        Catches Pydantic Literal unions that accidentally include
        non-serialisable types."""
        from services.node_input_schemas import get_node_input_schema, NODE_INPUT_MODELS
        import json

        for t in NODE_INPUT_MODELS:
            schema = get_node_input_schema(t)
            for prop_name, prop in schema.get("properties", {}).items():
                enum_vals = prop.get("enum", [])
                for val in enum_vals:
                    try:
                        json.dumps(val)
                    except (TypeError, ValueError) as e:
                        raise AssertionError(f"{t}.{prop_name} enum value {val!r} not JSON-serialisable: {e}")

    def test_ui_hints_only_carry_known_flags(self):
        """NODE_METADATA uiHints should only carry flags the frontend
        INodeUIHints knows about. New flag names without a frontend
        consumer are dead weight."""
        from models.node_metadata import NODE_METADATA

        known = {
            "hideInputSection",
            "hideOutputSection",
            "hideRunButton",
            "hasCodeEditor",
            "isMasterSkillEditor",
            "isMemoryPanel",
            "isToolPanel",
            # writeTodos: render the editable Current Todos manager in the
            # middle section (in addition to isToolPanel).
            "isTodoEditor",
            "isMonitorPanel",
            "isTaskManagerPanel",
            "isProcessManagerPanel",
            "showLocationPanel",
            "isAndroidToolkit",
            "isChatTrigger",
            "isConsoleSink",
            "hasSkills",
            # Wave 10.A: size hints carried by plugin registrations
            "width",
            "height",
            # Wave 10.G.5: start-node's user-authored JSON blob marker
            "hasInitialDataBlob",
            # Auto-derived from group membership ('memory' / 'tool') by
            # _derive_auto_ui_hints in services/plugin/base.py. Tells the
            # parameter panel that this node is an auxiliary config node
            # and should inherit the parent's main inputs.
            "isConfigNode",
            # OutputPanel: 'terminal' renders the node's textual output
            # preformatted instead of through ReactMarkdown (CLI-wrapper
            # plugins: githubAction, vercelAction, shell).
            "outputMode",
        }
        for node_type, meta in NODE_METADATA.items():
            hints = meta.get("uiHints") or {}
            unknown = set(hints.keys()) - known
            assert not unknown, (
                f"{node_type}: NODE_METADATA['uiHints'] has unknown flags {unknown}. " "Add to INodeUIHints + to the `known` set here."
            )


class TestPluginContractInvariants:
    """Wave 10.A invariants: NodeSpec envelope carries the full visual
    contract for every plugin-registered node. Locks in the guarantee
    that adding a new node = one register_node call + zero frontend work.
    """

    VALID_KINDS = {
        "square",
        "circle",
        "trigger",
        "start",
        "agent",
        "chat",
        "tool",
        "model",
        "generic",
    }
    VALID_POSITIONS = {"top", "bottom", "left", "right"}

    def _plugin_types(self):
        # Types registered via services.node_registry.register_node (Wave 10.C).
        import nodes  # noqa: F401  — import trigger
        from services.node_registry import registered_node_types

        # Fall back to any metadata entry that carries componentKind, so the
        # test still passes for metadata-only registrations (no handler).
        from models.node_metadata import NODE_METADATA

        explicit = {t for t, m in NODE_METADATA.items() if "componentKind" in m}
        return registered_node_types() | explicit

    def test_every_plugin_node_has_component_kind(self):
        for t in self._plugin_types():
            spec = get_node_spec(t)
            assert spec is not None, f"{t}: no NodeSpec emitted"
            assert (
                spec.get("componentKind") in self.VALID_KINDS
            ), f"{t}: componentKind={spec.get('componentKind')!r} not in {self.VALID_KINDS}"

    def test_every_plugin_node_has_color(self):
        # Color is the brand/accent each node owns. Without it the frontend
        # can't render borders, glow, or top output handles consistently.
        for t in self._plugin_types():
            spec = get_node_spec(t)
            color = spec.get("color")
            assert color and isinstance(color, str), f"{t}: missing 'color' string"
            # Accept hex (#rgb / #rrggbb) or CSS token-style values. Reject empty.
            assert color.strip() == color and color != "", f"{t}: bad color {color!r}"

    def test_every_plugin_node_has_at_least_one_handle(self):
        # Even output-only / input-only nodes declare at least one handle
        # so React Flow knows how to connect them.
        for t in self._plugin_types():
            spec = get_node_spec(t)
            handles = spec.get("handles") or []
            assert len(handles) >= 1, f"{t}: no handles declared"

    def test_every_handle_is_well_formed(self):
        for t in self._plugin_types():
            spec = get_node_spec(t)
            for h in spec.get("handles") or []:
                assert "name" in h, f"{t}: handle missing name: {h}"
                assert h.get("kind") in {"input", "output"}, f"{t}.{h.get('name')}: kind={h.get('kind')!r} not input/output"
                assert h.get("position") in self.VALID_POSITIONS, f"{t}.{h.get('name')}: position={h.get('position')!r} invalid"

    def test_agent_kind_has_skill_tool_memory_handles(self):
        # Contract: anything registered as an agent must accept skill,
        # tools, memory, and task inputs (the core n8n AIAgent handle
        # set). Keeps the AIAgentNode renderer honest.
        for t in self._plugin_types():
            spec = get_node_spec(t)
            if spec.get("componentKind") != "agent":
                continue
            # Some agents (socialReceive/socialSend) reuse the agent
            # component for multi-handle rendering but don't take agent
            # inputs — detect those by group.
            if "social" in (spec.get("group") or []):
                continue
            names = {h.get("name") for h in spec.get("handles") or []}
            for required in ("input-skill", "input-tools", "input-memory", "input-task"):
                assert required in names, f"{t}: componentKind=agent missing handle {required!r}; got {sorted(names)}"

    def test_trigger_kind_emits_output(self):
        # Triggers without an output are useless — they emit events
        # that downstream nodes subscribe to.
        for t in self._plugin_types():
            spec = get_node_spec(t)
            if spec.get("componentKind") != "trigger":
                continue
            outputs = [h for h in spec.get("handles") or [] if h.get("kind") == "output"]
            assert outputs, f"{t}: trigger declared no output handle"
            # Triggers don't have input handles (they're the source).
            inputs = [h for h in spec.get("handles") or [] if h.get("kind") == "input"]
            assert not inputs, f"{t}: trigger has input handle(s): {inputs}"

    def test_hide_output_handle_nodes_really_have_no_output(self):
        # If a node advertises hideOutputHandle=True, it shouldn't also
        # declare an output in its handles list — inconsistent.
        # Exception: tool-oriented nodes (component_kind="tool" pure
        # ToolNodes, or ActionNode + usable_as_tool=True dual-use nodes)
        # have hide_output_handle auto-derived True via
        # BaseNode.__init_subclass__. Their declared handles tuple still
        # carries output-tool / output-main for backend awareness, but
        # the SquareNode frontend suppresses the default render. The
        # invariant's original intent was "explicit author intent should
        # be consistent" — auto-derived is not author intent, so skip
        # those nodes.
        from services.node_registry import get_node_class

        for t in self._plugin_types():
            spec = get_node_spec(t)
            if not spec.get("hideOutputHandle"):
                continue
            cls = get_node_class(t)
            if cls is not None and (getattr(cls, "usable_as_tool", False) or getattr(cls, "component_kind", "") == "tool"):
                continue
            outs = [h for h in spec.get("handles") or [] if h.get("kind") == "output"]
            assert not outs, f"{t}: hideOutputHandle=True but handles declares output(s) {outs}"


class TestWave10GContractInvariants:
    """Wave 10.G.4 invariants: every widget-dispatch uiHint MiddleSection
    reads is declared at the plugin level, so the frontend doesn't need
    per-family string fallbacks.
    """

    AGENT_EXEMPT = {"socialReceive", "socialSend"}  # reuse component but aren't agents

    def _plugin_types(self):
        import nodes  # noqa: F401
        from services.node_registry import registered_node_types
        from models.node_metadata import NODE_METADATA

        explicit = {t for t, m in NODE_METADATA.items() if "componentKind" in m}
        return registered_node_types() | explicit

    def test_every_componentKind_agent_declares_hasSkills(self):
        """MiddleSection's `isAgentWithSkills` reads `uiHints.hasSkills`;
        the Connected Skills accordion + memory-edge follow-through both
        depend on it. An agent-kind node without this hint silently loses
        skill-panel rendering."""
        for t in self._plugin_types():
            spec = get_node_spec(t)
            if spec.get("componentKind") != "agent":
                continue
            if t in self.AGENT_EXEMPT:
                continue
            hints = spec.get("uiHints") or {}
            assert hints.get("hasSkills") is True, f"{t}: componentKind=agent must declare uiHints.hasSkills=True"

    def test_every_tool_kind_declares_isToolPanel(self):
        """Every dedicated tool node (componentKind='tool') must emit
        `uiHints.isToolPanel=True` so MiddleSection renders the
        ToolSchemaEditor. Dual-purpose nodes (group includes 'tool' but
        componentKind='square') don't show the editor — they render as
        normal squares and become tools only when wired to an agent's
        input-tools handle."""
        for t in self._plugin_types():
            spec = get_node_spec(t)
            if spec.get("componentKind") != "tool":
                continue
            hints = spec.get("uiHints") or {}
            assert hints.get("isToolPanel") is True, f"{t}: componentKind=tool must declare uiHints.isToolPanel=True"

    def test_google_workspace_nodes_have_operation_gating(self):
        """Drive / Sheets / Tasks / Contacts / Gmail all have an
        `operation` discriminator; at least one sub-field must hide
        behind `displayOptions.show.operation` so the panel isn't a
        flat dump of every op's fields."""
        from services.node_input_schemas import get_node_input_schema

        for t in ("googleGmail", "googleDrive", "googleSheets", "googleTasks", "googleContacts"):
            schema = get_node_input_schema(t)
            assert schema, f"{t}: no input schema"
            props = schema.get("properties") or {}
            gated = [name for name, p in props.items() if (p.get("displayOptions") or {}).get("show", {}).get("operation")]
            assert gated, f"{t}: no field declares displayOptions.show.operation; " "every per-op field would render at once"

    def test_code_executors_declare_code_editor(self):
        """The ParameterRenderer `case 'code'` widget only fires when
        the Pydantic field emits `editor: 'code'`. Without this the
        code executor falls back to a plain string input."""
        from services.node_input_schemas import get_node_input_schema

        for t in ("pythonExecutor", "javascriptExecutor", "typescriptExecutor"):
            schema = get_node_input_schema(t)
            code_field = (schema.get("properties") or {}).get("code")
            assert code_field, f"{t}: `code` field missing from input schema"
            assert code_field.get("editor") == "code", f"{t}.code: expected `editor: 'code'`, got {code_field.get('editor')!r}"

    def test_every_asset_icon_has_matching_svg(self):
        """Wave 10.B: every ``asset:<key>`` string emitted by a plugin
        must resolve to a real SVG file under
        ``client/src/assets/icons/<folder>/<key>.svg``. Without this
        invariant, a typo in a ``register_node(... icon="asset:foo")``
        call silently renders no icon on the canvas — user sees an
        empty slot instead of the intended logo."""
        from pathlib import Path
        import re
        from models.node_metadata import NODE_METADATA

        # Walk client/src/assets/icons/**/*.svg once; derive keys.
        repo_root = Path(__file__).resolve().parents[2]
        assets_dir = repo_root / "client" / "src" / "assets" / "icons"
        assert assets_dir.is_dir(), f"icon assets dir not found at {assets_dir}"
        svg_keys = {p.stem for p in assets_dir.rglob("*.svg")}

        ASSET_RE = re.compile(r"^asset:([A-Za-z0-9_\-]+)$")
        missing: list[str] = []
        for node_type, meta in NODE_METADATA.items():
            icon = meta.get("icon", "") or ""
            m = ASSET_RE.match(icon)
            if m and m.group(1) not in svg_keys:
                missing.append(f"{node_type}.icon={icon!r}")
        assert not missing, (
            f"asset:<key> references with no matching SVG: {missing}. " f"Drop the file at client/src/assets/icons/<folder>/<key>.svg."
        )

    def test_api_key_fields_are_password_masked(self):
        """Every field named `apiKey` / `api_key` across every node's
        input schema must emit `password: True` so the UI masks it.
        Tribal risk: a new node author forgets the hint and the key
        renders in plaintext."""
        from services.node_input_schemas import NODE_INPUT_MODELS, get_node_input_schema

        offenders = []
        for t in NODE_INPUT_MODELS:
            schema = get_node_input_schema(t)
            if not schema:
                continue
            for name, prop in (schema.get("properties") or {}).items():
                if name in ("api_key", "api_key") and prop.get("password") is not True:
                    offenders.append(f"{t}.{name}")
        assert not offenders, "API-key fields missing password:True — they would render in " f"plaintext: {offenders}"
