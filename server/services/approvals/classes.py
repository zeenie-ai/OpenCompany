"""What a node call may do, on Hire's ladder (``SIDE_EFFECTS`` in
services/employees/apps.py). The workflow's mode says which classes ask
(RFC-0007 §4.1, §4.2):

- ``read`` looks things up, and never asks;
- ``write`` works on the employee's own files, notes, team or the owner's
  phone;
- ``send`` reaches people or services: messages, posts, invites, deploys,
  cloud changes, commands on the computer, and fetching a URL;
- ``money`` spends money or deletes something.

An unlisted node type is ``send``, and so is a value a listed field does not
name: a new plugin asks under the default mode and needs no edit. A call its
approval spec says reaches no one (a calendar change without invites) is
``write``, not ``send``; the spec never lowers ``money``, so a delete stays a
delete.

The list lives here, not on the plugins. ``repeats.py`` answers another
question: whether a broken-off attempt may run again. Never imports
``nodes/``.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

from services.approvals.repeats import _default

READ, WRITE, SEND, MONEY = "read", "write", "send", "money"
#: Mildest first.
LADDER: Tuple[str, ...] = (READ, WRITE, SEND, MONEY)
DEFAULT = SEND

#: Every call of these has this class.
CLASSES: Dict[str, str] = {
    # Triggers: they only wait, and are never an agent's tool call.
    "chatTrigger": READ,
    "discordInteraction": READ,
    "discordReceive": READ,
    "dodoPaymentsReceive": READ,
    "emailReceive": READ,
    "googleGmailReceive": READ,
    "msMailReceive": READ,
    "stripeReceive": READ,
    "taskTrigger": READ,
    "telegramReceive": READ,
    "twitterReceive": READ,
    "webhookTrigger": READ,
    "whatsappBusinessReceive": READ,
    "whatsappBusinessStatus": READ,
    "whatsappReceive": READ,
    # Chat models.
    "anthropicChatModel": READ,
    "cerebrasChatModel": READ,
    "deepseekChatModel": READ,
    "geminiChatModel": READ,
    "groqChatModel": READ,
    "kimiChatModel": READ,
    "lmstudioChatModel": READ,
    "mistralChatModel": READ,
    "ollamaChatModel": READ,
    "openaiChatModel": READ,
    "openaiCompatibleChatModel": READ,
    "openrouterChatModel": READ,
    "sarvamChatModel": READ,
    # Android monitors.
    "appList": READ,
    "batteryMonitor": READ,
    "environmentalSensors": READ,
    "location": READ,
    "motionDetection": READ,
    "networkMonitor": READ,
    "systemInfo": READ,
    # Search and provider reads (a search through a search provider is not a
    # URL fetch, D11).
    "braveSearch": READ,
    "detectLanguage": READ,
    "duckduckgoSearch": READ,
    "embeddingGenerator": READ,
    "gmaps_create": READ,
    "gmaps_locations": READ,
    "gmaps_nearby_places": READ,
    "perplexitySearch": READ,
    "serperSearch": READ,
    "speechToText": READ,
    "textGenerator": READ,
    "translateText": READ,
    "transliterateText": READ,
    "twitterSearch": READ,
    "twitterUser": READ,
    "visionAnalyze": READ,
    # Local reads and computation.
    "calculatorTool": READ,
    "chatHistory": READ,
    "console": READ,
    "context": READ,
    "currentTimeTool": READ,
    "documentParser": READ,
    "employeeJobSource": READ,
    "fileHandler": READ,
    "fileRead": READ,
    "fsSearch": READ,
    "gallery": READ,
    "masterSkill": READ,
    "proxyStatus": READ,
    "socialReceive": READ,
    "start": READ,
    "teamMonitor": READ,
    "textChunker": READ,
    # Waits, and the ask itself.
    "approvalGate": READ,
    "cronScheduler": READ,
    "timer": READ,
    # Answers to the owner, or to the request that started the run.
    "chatReply": READ,
    "chatUi": READ,
    "webhookResponse": READ,
    # The employee's team: a held assignment would reach the loop as "not
    # sent yet". Each teammate's own calls are checked.
    "taskManager": READ,
    # Agents: handing work to one asks nothing; each of its own tool calls is
    # checked.
    "aiAgent": READ,
    "chatAgent": READ,
    "ai_employee": READ,
    "android_agent": READ,
    "autonomous_agent": READ,
    "browser_agent": READ,
    "coding_agent": READ,
    "consumer_agent": READ,
    "orchestrator_agent": READ,
    "payments_agent": READ,
    "productivity_agent": READ,
    "social_agent": READ,
    "task_agent": READ,
    "tool_agent": READ,
    "travel_agent": READ,
    "web_agent": READ,
    # The employee's own work: its board, job, files, checklist and audio.
    "canvas": WRITE,
    "employeeJob": WRITE,
    "fileModify": WRITE,
    "textToSpeech": WRITE,
    "writeTodos": WRITE,
    # The owner's own phone.
    "airplaneModeControl": WRITE,
    "appLauncher": WRITE,
    "audioAutomation": WRITE,
    "bluetoothAutomation": WRITE,
    "cameraControl": WRITE,
    "deviceStateAutomation": WRITE,
    "mediaControl": WRITE,
    "screenControlAutomation": WRITE,
    "wifiAutomation": WRITE,
    # Sends (also the default; listed so the decision is visible).
    "chatSend": SEND,
    "discordSend": SEND,
    "emailSend": SEND,
    "socialSend": SEND,
    "telegramSend": SEND,
    "whatsappSend": SEND,
    # Commands on the computer (the code tools' sandboxes are weak, D16),
    # tools that drive apps, CLI agents, custom connectors.
    "shell": SEND,
    "javascriptExecutor": SEND,
    "pythonExecutor": SEND,
    "typescriptExecutor": SEND,
    "android_tool": SEND,
    "mobile_use_agent": SEND,
    "claude_code_agent": SEND,
    "codex_agent": SEND,
    "rlm_agent": SEND,
    "mcpConnector": SEND,
    # URL fetches (D11).
    "crawleeScraper": SEND,
    "fileDownloader": SEND,
    "httpScraper": SEND,
    # Money: every Stripe command, every paid Apify run.
    "apifyActor": MONEY,
    "stripeAction": MONEY,
}

#: These have the class their field's value names; any other value is
#: ``send``, and a list takes its worst member's class.
CLASSES_WHEN: Dict[str, Tuple[str, Dict[str, str]]] = {
    # Its lookups read; a plan counts as the change it plans.
    "agentBuilder": (
        "operation",
        {
            "inspect_canvas": READ,
            "inspect_node": READ,
            "search_docs": READ,
            "read_doc": READ,
            "plan_update": WRITE,
            "apply_update": WRITE,
            "add_tool": WRITE,
            "add_skill": WRITE,
            "add_subagent": WRITE,
            "create_workflow": WRITE,
        },
    ),
    "awsAction": (
        "operation",
        {
            "whoami": READ,
            "ec2_instances_list": READ,
            "ec2_instance_describe": READ,
            "s3_list": READ,
            "s3_download": READ,
            "list_operations": READ,
            "ec2_instance_start": SEND,
            "ec2_instance_stop": SEND,
            "s3_upload": SEND,
            "s3_delete": MONEY,
            "call": MONEY,  # any operation of any service
        },
    ),
    # Observations and the owner's turn read; navigation fetches a URL (D11).
    # Site actions are send: while the mode asks, the spec's
    # restrict_while_asking runs them read-only instead of holding them.
    "browser": (
        "operation",
        {
            "snapshot": READ,
            "page_text": READ,
            "page_info": READ,
            "screenshot": READ,
            "diagnose": READ,
            "webmcp_list": READ,
            "wait": READ,
            "credential_bindings": READ,
            "request_user": READ,
            "close": WRITE,  # node-only
            "navigate": SEND,
            "tabs": SEND,  # one field: list, switch and close too
            "click": SEND,
            "hover": SEND,
            "type": SEND,
            "press": SEND,
            "select": SEND,
            "scroll": SEND,
            "back": SEND,
            "forward": SEND,
            "reload": SEND,
            "webmcp_call": SEND,
            "credential_fill": SEND,
            "evaluate": SEND,  # node-only
            "run_python": SEND,  # node-only
        },
    ),
    "cloudflareAction": (
        "operation",
        {
            "whoami": READ,
            "search_commands": READ,
            "zones_list": READ,
            "dns_records_list": READ,
            "graphql_query": READ,
            "dns_record_create": SEND,
            "dns_record_edit": SEND,
            "dns_record_delete": MONEY,
            "custom": MONEY,
        },
    ),
    "dataSource": (
        "operation",
        {
            "list": READ,
            "read": READ,
            "search": READ,
            "metadata": READ,
            "write": WRITE,
            "append": WRITE,
            "copy_to_workspace": WRITE,
        },
    ),
    "discordAction": (
        "operation",
        {
            "list_guilds": READ,
            "list_channels": READ,
            "get_channel": READ,
            "list_messages": READ,
            "get_message": READ,
            "download_attachments": READ,
            "create_thread": SEND,
            "edit_message": SEND,
            "add_reaction": SEND,
            "pin_message": SEND,
            "interaction_respond": SEND,
            "execute_webhook": SEND,
            "delete_message": MONEY,
            "custom": MONEY,  # any route, any method
        },
    ),
    "dodoPaymentsAction": (
        "operation",
        {
            "list_payments": READ,
            "get_payment": READ,
            "list_subscriptions": READ,
            "get_subscription": READ,
            "list_customers": READ,
            "get_customer": READ,
            "list_products": READ,
            "get_product": READ,
            "list_refunds": READ,
            "list_disputes": READ,
            "list_operations": READ,
            "create_checkout_session": SEND,
            "create_customer": SEND,
            "create_customer_portal_link": SEND,
            "create_refund": MONEY,
            "cancel_subscription": MONEY,
            "change_subscription_plan": MONEY,
            "call": MONEY,  # any endpoint, read or write
        },
    ),
    # A "Deleted" flag marks a message for deletion.
    "emailRead": (
        "operation",
        {"list": READ, "search": READ, "read": READ, "folders": READ, "move": SEND, "flag": MONEY, "delete": MONEY},
    ),
    "gcloudAction": (
        "operation",
        {
            "auth_list": READ,
            "config_list": READ,
            "projects_list": READ,
            "compute_instances_list": READ,
            "compute_instance_describe": READ,
            "run_services_list": READ,
            "run_service_describe": READ,
            "storage_ls": READ,
            "set_project": WRITE,
            "compute_instance_start": SEND,
            "compute_instance_stop": SEND,
            "run_deploy": SEND,
            "storage_cp": SEND,
            "storage_rm": MONEY,
            "custom": MONEY,
        },
    ),
    "githubAction": (
        "operation",
        {
            "pr_list": READ,
            "issue_list": READ,
            "repo_clone": WRITE,
            "pr_create": SEND,
            "pr_merge": SEND,
            "issue_create": SEND,
            "custom": MONEY,
        },
    ),
    "googleCalendar": ("operation", {"list": READ, "create": SEND, "update": SEND, "delete": MONEY}),
    "googleContacts": (
        "operation",
        {"list": READ, "search": READ, "get": READ, "create": SEND, "update": SEND, "delete": MONEY},
    ),
    "googleDrive": ("operation", {"download": READ, "list": READ, "upload": SEND, "share": SEND}),
    "googleGmail": ("operation", {"search": READ, "read": READ, "send": SEND}),
    "googleSheets": ("operation", {"read": READ, "write": SEND, "append": SEND}),
    "googleTasks": (
        "operation",
        {"list": READ, "create": SEND, "complete": SEND, "update": SEND, "delete": MONEY},
    ),
    # A URL fetch whatever the method (D11); DELETE deletes.
    "httpRequest": ("method", {"GET": SEND, "POST": SEND, "PUT": SEND, "PATCH": SEND, "DELETE": MONEY}),
    # Pure computation, unless the call takes a capability.
    "montyExecutor": ("capabilities", {"workspace_read": READ, "workspace_write": WRITE, "http_get": SEND}),
    "msCalendar": ("operation", {"list": READ, "create": SEND, "update": SEND, "delete": MONEY}),
    "msMail": (
        "operation",
        {
            "read": READ,
            "search": READ,
            "list_attachments": READ,
            "download_attachments": READ,
            "send": SEND,
            "reply": SEND,
        },
    ),
    "processManager": (
        "operation",
        {"list": READ, "get_output": READ, "stop": WRITE, "restart": WRITE, "start": SEND, "send_input": SEND},
    ),
    # OpenCompany's proxy settings: install-wide, and a provider's host is the
    # model's choice.
    "proxyConfig": (
        "operation",
        {
            "list_providers": READ,
            "get_stats": READ,
            "list_routing_rules": READ,
            "test_provider": READ,  # a fixed test URL through the named provider
            "add_provider": SEND,
            "update_provider": SEND,
            "remove_provider": SEND,
            "set_credentials": SEND,
            "add_routing_rule": SEND,
            "remove_routing_rule": SEND,
        },
    ),
    "proxyRequest": ("method", {"GET": SEND, "POST": SEND, "PUT": SEND, "PATCH": SEND, "DELETE": MONEY}),
    "simpleMemory": (
        "operation",
        {"recall": READ, "list": READ, "get": READ, "remember": WRITE, "update": WRITE, "forget": WRITE},
    ),
    "tikhubAction": (
        "operation",
        {"list_endpoints": READ, "describe": READ, "account": READ, "fetch_url": SEND, "call": SEND},
    ),
    "tregAction": ("operation", {"search": READ, "describe": READ, "balance": READ, "call": MONEY}),  # any paid tool
    "twitterSend": (
        "action",
        {
            "tweet": SEND,
            "reply": SEND,
            "retweet": SEND,
            "quote": SEND,
            "like": SEND,
            "unlike": SEND,
            "delete": MONEY,
        },
    ),
    "vectorStore": ("operation", {"query": READ, "store": WRITE, "delete": WRITE}),
    "vercelAction": ("operation", {"inspect": READ, "list": READ, "deploy": SEND, "custom": MONEY}),
    "whatsappBusinessMedia": ("operation", {"get_url": READ, "download": READ, "upload": SEND, "delete": MONEY}),
    "whatsappBusinessSend": (
        "operation",
        {
            "list_templates": READ,
            "send_text": SEND,
            "send_media": SEND,
            "send_template": SEND,
            "send_buttons": SEND,
            "send_list": SEND,
            "send_cta_url": SEND,
            "send_reaction": SEND,
            "send_location": SEND,
            "send_contacts": SEND,
        },
    ),
    "whatsappDb": (
        "operation",
        {
            "chat_history": READ,
            "search_groups": READ,
            "get_group_info": READ,
            "get_contact_info": READ,
            "list_contacts": READ,
            "check_contacts": READ,
            "list_channels": READ,
            "get_channel_info": READ,
            "channel_messages": READ,
            "channel_stats": READ,
            "contact_profile_pic": READ,
            "newsletter_live_updates": READ,  # subscribes this session to view counts
            "channel_follow": SEND,
            "channel_unfollow": SEND,
            "channel_create": SEND,
            "channel_mute": SEND,
            "channel_mark_viewed": SEND,
            "newsletter_react": SEND,
        },
    ),
}


#: Where a call that fetches a URL names it: the scope of a host rule
#: (RFC-0007 §4.4); the Browser is held only when it navigates (§4.3).
URL_FIELDS: Dict[str, str] = {"browser": "url", "crawleeScraper": "url", "httpRequest": "url", "proxyRequest": "url"}


def call_data(node_cls: Any, node_data: Mapping[str, Any], tool_args: Any) -> Dict[str, Any]:
    """What :func:`class_of` judges for an LLM tool call: the tool's own
    arguments when it has an input model of its own, else the call's
    arguments over the node's settings."""
    tool_input = getattr(node_cls, "tool_input_model", None)
    if isinstance(tool_args, Mapping) and callable(tool_input) and tool_input() is not getattr(node_cls, "Params", None):
        return dict(tool_args)
    return dict(node_data or {})


def operation_of(node_cls: Any, data: Mapping[str, Any], *, tool_call: bool = False) -> Optional[str]:
    """The value of the node's class field this call names (its model's
    default when unset). None for a node the table classes whole, and for a
    value that is not one string."""
    rule = CLASSES_WHEN.get(getattr(node_cls, "type", ""))
    if rule is None:
        return None
    value = data.get(rule[0])
    if value is None:
        value = _default(node_cls, rule[0], tool_call)
    return value if isinstance(value, str) else None


def fetched_url(node_cls: Any, data: Mapping[str, Any]) -> str:
    """The URL the call fetches, or "" when it fetches none."""
    field = URL_FIELDS.get(getattr(node_cls, "type", ""))
    value = data.get(field) if field else None
    return value.strip() if isinstance(value, str) else ""


def class_of(node_cls: Any, data: Mapping[str, Any], *, tool_call: bool = False) -> str:
    """The class of this call of ``node_cls`` with ``data``.

    ``data`` is what the call runs with: the tool's own arguments when the
    tool has an input model of its own (a Browser call without an
    ``operation`` takes a snapshot, whatever the node's settings say), else
    the call's arguments over the node's settings. A field the call leaves
    unset takes the default of the model it validates against: the tool's
    input on an LLM tool call, else the node's ``Params``."""
    node_type = getattr(node_cls, "type", "")
    klass = CLASSES.get(node_type)
    rule = CLASSES_WHEN.get(node_type)
    if klass is None and rule is not None:
        field, values = rule
        value = data.get(field)
        if value is None:
            value = _default(node_cls, field, tool_call)
            data = {**data, field: value}
        klass = _value_class(values, value)
    klass = klass or DEFAULT
    if klass == SEND and _reaches_no_one(node_cls, data):
        return WRITE
    return klass


def _value_class(values: Mapping[str, str], value: Any) -> str:
    if isinstance(value, (list, tuple)):
        # The worst member's class; an empty list asks for nothing.
        return max((_value_class(values, item) for item in value), key=LADDER.index, default=READ)
    return values.get(value, DEFAULT) if isinstance(value, str) else DEFAULT


def _reaches_no_one(node_cls: Any, data: Mapping[str, Any]) -> bool:
    """Whether the node's approval spec covers this call and its ``when``
    says the call reaches no one."""
    from services.plugin.approval import approval_spec

    spec = approval_spec(node_cls)
    if spec is None or spec.when is None:
        return False
    if spec.operations is not None and str(data.get(spec.operation_field) or "") not in spec.operations:
        return False
    return not spec.sends(data)


__all__ = [
    "CLASSES",
    "CLASSES_WHEN",
    "DEFAULT",
    "LADDER",
    "MONEY",
    "READ",
    "SEND",
    "URL_FIELDS",
    "WRITE",
    "call_data",
    "class_of",
    "fetched_url",
    "operation_of",
]
