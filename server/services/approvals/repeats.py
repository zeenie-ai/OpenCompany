"""Which node calls may simply run again after an attempt broke off.

Temporal runs an activity again when its attempt broke off (the worker
stopped, a timeout) before it reported, and the call may already have done
its work: a message gone out, a payment made. A later attempt therefore runs
again only for a call listed here, one that changes nothing outside or makes
the same change again (or one keyed to the call). Any other call, and every
node type not listed, may already have happened (RFC-0006 §11.2).

Not listed, so never repeated: sends, agents (a second run repeats every
tool call), paid runs (Apify), shell commands and process starts, the CLI
passthroughs (``custom``), Stripe commands, custom connectors and every
``call`` passthrough (AWS, Dodo, TikHub, treg), whose effect depends on the
endpoint.

The list lives here, not on the plugins: a plugin needs no edit for it, and a
new one counts as unsafe to repeat until it is listed. Never imports
``nodes/``.
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, Mapping, Optional, Tuple

#: Every call of these may run again.
REPEATABLE: FrozenSet[str] = frozenset(
    {
        # Reads and computation.
        "anthropicChatModel",
        "appList",
        "batteryMonitor",
        "braveSearch",
        "calculatorTool",
        "cerebrasChatModel",
        "chatHistory",
        "console",
        "context",
        "crawleeScraper",
        "currentTimeTool",
        "deepseekChatModel",
        "detectLanguage",
        "documentParser",
        "duckduckgoSearch",
        "embeddingGenerator",
        "employeeJobSource",
        "environmentalSensors",
        "fileHandler",
        "fileRead",
        "fsSearch",
        "gallery",
        "geminiChatModel",
        "gmaps_create",
        "gmaps_locations",
        "gmaps_nearby_places",
        "groqChatModel",
        "httpScraper",
        "javascriptExecutor",
        "kimiChatModel",
        "lmstudioChatModel",
        "location",
        "masterSkill",
        "mistralChatModel",
        "montyExecutor",
        "motionDetection",
        "networkMonitor",
        "ollamaChatModel",
        "openaiChatModel",
        "openaiCompatibleChatModel",
        "openrouterChatModel",
        "perplexitySearch",
        "proxyStatus",
        "pythonExecutor",
        "sarvamChatModel",
        "serperSearch",
        "socialReceive",
        "speechToText",
        "start",
        "systemInfo",
        "teamMonitor",
        "textChunker",
        "textGenerator",
        "translateText",
        "transliterateText",
        "twitterSearch",
        "twitterUser",
        "typescriptExecutor",
        "visionAnalyze",
        # A wait, taken up again by a later attempt.
        "approvalGate",
        "cronScheduler",
        "timer",
        # Keyed to the call or the run, so a repeat finds what the first made:
        # the Agent Builder's changes (_mutation_id), the run's reply, its
        # interface part, the team's job and the Task Manager's assignments.
        "agentBuilder",
        "chatReply",
        "chatUi",
        "employeeJob",
        "taskManager",
        # Local, and harmless to do twice.
        "canvas",
        "fileDownloader",
        "simpleMemory",
        "textToSpeech",
        "webhookResponse",
        "writeTodos",
    }
)

#: These may run again only when one field has a listed value.
REPEATABLE_WHEN: Dict[str, Tuple[str, FrozenSet[str]]] = {
    "awsAction": (
        "operation",
        frozenset(
            {
                "whoami",
                "ec2_instances_list",
                "ec2_instance_describe",
                "ec2_instance_start",
                "ec2_instance_stop",
                "s3_list",
                "s3_upload",
                "s3_download",
                "s3_delete",
                "list_operations",
            }
        ),
    ),
    # Observations, the owner's turn (its wait is taken up again) and closing.
    "browser": (
        "operation",
        frozenset(
            {
                "snapshot",
                "page_text",
                "page_info",
                "screenshot",
                "diagnose",
                "webmcp_list",
                "wait",
                "credential_bindings",
                "request_user",
                "close",
            }
        ),
    ),
    "cloudflareAction": (
        "operation",
        frozenset({"whoami", "search_commands", "zones_list", "dns_records_list", "dns_record_edit", "dns_record_delete", "graphql_query"}),
    ),
    "dataSource": ("operation", frozenset({"list", "read", "search", "metadata", "write", "copy_to_workspace"})),
    "discordAction": (
        "operation",
        frozenset(
            {
                "list_guilds",
                "list_channels",
                "get_channel",
                "list_messages",
                "get_message",
                "edit_message",
                "delete_message",
                "add_reaction",
                "pin_message",
                "download_attachments",
            }
        ),
    ),
    "dodoPaymentsAction": (
        "operation",
        frozenset(
            {
                "list_payments",
                "get_payment",
                "list_subscriptions",
                "get_subscription",
                "list_customers",
                "get_customer",
                "list_products",
                "get_product",
                "list_refunds",
                "list_disputes",
                "create_checkout_session",
                "create_customer_portal_link",
                "cancel_subscription",
                "change_subscription_plan",
                "list_operations",
            }
        ),
    ),
    "emailRead": ("operation", frozenset({"list", "search", "read", "folders", "move", "delete", "flag"})),
    # Not edit: an edit whose new text contains the old text would apply twice.
    "fileModify": ("operation", frozenset({"write"})),
    "gcloudAction": (
        "operation",
        frozenset(
            {
                "auth_list",
                "config_list",
                "projects_list",
                "set_project",
                "compute_instances_list",
                "compute_instance_start",
                "compute_instance_stop",
                "compute_instance_describe",
                "run_services_list",
                "run_service_describe",
                "storage_ls",
                "storage_cp",
                "storage_rm",
            }
        ),
    ),
    "githubAction": ("operation", frozenset({"repo_clone", "pr_list", "pr_merge", "issue_list"})),
    "googleCalendar": ("operation", frozenset({"list", "update", "delete"})),
    "googleContacts": ("operation", frozenset({"list", "search", "get", "update", "delete"})),
    "googleDrive": ("operation", frozenset({"download", "list", "share"})),
    "googleGmail": ("operation", frozenset({"search", "read"})),
    "googleSheets": ("operation", frozenset({"read", "write"})),
    "googleTasks": ("operation", frozenset({"list", "complete", "update", "delete"})),
    # RFC 9110 §9.2.2: GET, PUT and DELETE are idempotent.
    "httpRequest": ("method", frozenset({"GET", "PUT", "DELETE"})),
    "msCalendar": ("operation", frozenset({"list", "update", "delete"})),
    "msMail": ("operation", frozenset({"read", "search", "list_attachments", "download_attachments"})),
    "processManager": ("operation", frozenset({"stop", "restart", "list", "get_output"})),
    "proxyConfig": (
        "operation",
        frozenset(
            {
                "list_providers",
                "update_provider",
                "remove_provider",
                "set_credentials",
                "test_provider",
                "get_stats",
                "list_routing_rules",
                "remove_routing_rule",
            }
        ),
    ),
    "proxyRequest": ("method", frozenset({"GET", "PUT", "DELETE"})),
    "tikhubAction": ("operation", frozenset({"list_endpoints", "describe", "fetch_url", "account"})),
    "tregAction": ("operation", frozenset({"search", "describe", "balance"})),
    # Not "store": each call gives its chunks new random ids, so a repeat
    # stores them a second time.
    "vectorStore": ("operation", frozenset({"query", "delete"})),
    "vercelAction": ("operation", frozenset({"inspect", "list"})),
    "whatsappBusinessMedia": ("operation", frozenset({"upload", "get_url", "download", "delete"})),
    "whatsappBusinessSend": ("operation", frozenset({"send_reaction", "list_templates"})),
    "whatsappDb": (
        "operation",
        frozenset(
            {
                "chat_history",
                "search_groups",
                "get_group_info",
                "get_contact_info",
                "list_contacts",
                "check_contacts",
                "list_channels",
                "get_channel_info",
                "channel_messages",
                "channel_stats",
                "channel_follow",
                "channel_unfollow",
                "channel_mute",
                "channel_mark_viewed",
                "newsletter_react",
                "newsletter_live_updates",
                "contact_profile_pic",
            }
        ),
    ),
}


def repeatable(node_cls: Any, data: Mapping[str, Any], *, tool_call: bool = False) -> bool:
    """Whether this call of ``node_cls`` with ``data`` may run again after an
    attempt broke off. A field the call leaves unset takes the default of the
    model it validates against: the tool's input on an LLM tool call, else
    the node's ``Params``."""
    node_type = getattr(node_cls, "type", "")
    if node_type in REPEATABLE:
        return True
    rule = REPEATABLE_WHEN.get(node_type)
    if rule is None:
        return False
    field, values = rule
    value = data.get(field)
    if value is None:
        value = _default(node_cls, field, tool_call)
    return isinstance(value, str) and value in values


def _default(node_cls: Any, field: str, tool_call: bool) -> Optional[Any]:
    model = node_cls.tool_input_model() if tool_call and hasattr(node_cls, "tool_input_model") else node_cls.Params
    info = model.model_fields.get(field) or node_cls.Params.model_fields.get(field)
    return None if info is None or info.is_required() else info.get_default(call_default_factory=True)


__all__ = ["REPEATABLE", "REPEATABLE_WHEN", "repeatable"]
