"""whatsappBusinessSend — every Cloud API outbound message type, one node.

Meta models message type as a *field* on a single endpoint
(``POST /{phone-number-id}/messages``), not as separate endpoints: text,
image, video, audio, document, sticker, location, contacts, reaction,
interactive and template all post the same URL and differ only in ``type``
and one sibling object. Splitting them across nodes made the canvas imply
an API shape that does not exist, so they are operations here.

Media *management* is the one genuinely separate endpoint family
(``POST /{phone-number-id}/media``, ``GET``/``DELETE /{media-id}``) and
stays in ``whatsappBusinessMedia``. ``list_templates`` is the other
outlier — it is WABA-scoped rather than phone-scoped — but it is kept
here because it exists to answer "what can I send", which is a question
about sending.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.plugin import ActionNode, NodeContext, NodeUserError, Operation, TaskQueue
from services.plugin.approval import ApprovalSpec

from ._base import (
    graph_get,
    graph_post,
    normalize_recipient,
    resolve_phone_number_id,
)
from ._credentials import WhatsAppBusinessCredential

_MAX_TEXT_BODY = 4096

# Interactive ceilings, enforced locally because Meta rejects the whole
# message rather than trimming, and a 400 mid-conversation is worse than a
# clear error at authoring time.
_MAX_BUTTONS = 3
_MAX_BUTTON_TITLE = 20
_MAX_ROWS_TOTAL = 10
_MAX_ROW_TITLE = 24
_MAX_ROW_DESCRIPTION = 72
_MAX_LIST_BUTTON = 20
# The OpenAPI spec says 1024 and the interactive-list docs page says 4096.
# Using the smaller of Meta's two numbers: too-short fails loudly at
# authoring time, too-long fails at send time in front of a customer.
_MAX_BODY = 1024
_MAX_FOOTER = 60
_MAX_HEADER = 60

_INTERACTIVE_OPS = ["send_buttons", "send_list", "send_cta_url"]


def _show(**conditions: Any) -> Dict[str, Any]:
    """displayOptions helper. All conditions must hold (the frontend ANDs them)."""
    return {"displayOptions": {"show": conditions}}


class WhatsAppBusinessSendParams(BaseModel):
    """Operator configuration, persisted on the node."""

    # Load-bearing, not decorative: _pick_operation reads this off the raw
    # parameter dict *before* Pydantic validation, so the default below never
    # applies at dispatch time and an absent value resolves to no operation
    # at all. Callers constructing raw dicts must pass it explicitly.
    operation: Literal[
        "send_text",
        "send_media",
        "send_template",
        "send_buttons",
        "send_list",
        "send_cta_url",
        "send_reaction",
        "send_location",
        "send_contacts",
        "list_templates",
    ] = Field(default="send_text", description="What to send.")

    to: str = Field(
        default="",
        description="Recipient phone number in international format (e.g. +14155551234).",
        json_schema_extra=_show(
            operation=[
                "send_text",
                "send_media",
                "send_template",
                *_INTERACTIVE_OPS,
                "send_reaction",
                "send_location",
                "send_contacts",
            ]
        ),
    )
    reply_to_message_id: str = Field(
        default="",
        description="Quote an inbound message by its wamid.",
        json_schema_extra=_show(
            operation=[
                "send_text",
                "send_media",
                "send_location",
                "send_contacts",
            ]
        ),
    )

    # --- send_text ----------------------------------------------------------
    text: str = Field(
        default="",
        description="Message body.",
        json_schema_extra={"rows": 4, **_show(operation=["send_text"])},
    )
    preview_url: bool = Field(
        default=False,
        description="Render a link preview for the first URL in the body.",
        json_schema_extra=_show(operation=["send_text"]),
    )
    format_markdown: bool = Field(
        default=True,
        description="Convert GFM markdown to WhatsApp's *bold* / _italic_ syntax.",
        json_schema_extra=_show(operation=["send_text"]),
    )

    # --- send_media ---------------------------------------------------------
    # Named media_type, NOT message_type. ParameterRenderer.getFileAcceptType
    # hardcodes a switch on a sibling parameter literally called
    # `message_type` and, when present, overrides whatever `accept` the
    # backend declared. Sidestepping the name keeps this node's accept list
    # authoritative without a frontend change.
    media_type: Literal["image", "video", "audio", "document", "sticker"] = Field(
        default="image",
        description="Kind of media to send.",
        json_schema_extra=_show(operation=["send_media"]),
    )
    media_source: Literal["file", "id", "link"] = Field(
        default="file",
        description=(
            "file: upload from the workspace. id: a media ID already uploaded. "
            "link: a public URL Meta fetches itself."
        ),
        json_schema_extra=_show(operation=["send_media"]),
    )
    media: Any = Field(
        default=None,
        description="File to send.",
        json_schema_extra={
            "widget": "file",
            "accept": "*/*",
            # Gated on operation as well as source: media_source defaults to
            # "file", so keying on it alone rendered the file picker during
            # send_text.
            **_show(operation=["send_media"], media_source=["file"]),
        },
    )
    media_id: str = Field(
        default="",
        description="Existing Meta media ID.",
        json_schema_extra=_show(operation=["send_media"], media_source=["id"]),
    )
    media_url: str = Field(
        default="",
        description="Publicly reachable URL. Meta downloads it directly.",
        json_schema_extra=_show(operation=["send_media"], media_source=["link"]),
    )
    caption: str = Field(
        default="",
        description="Caption. Not supported for audio or sticker.",
        json_schema_extra=_show(operation=["send_media"]),
    )
    media_filename: str = Field(
        default="",
        description="Filename shown to the recipient. Documents only.",
        json_schema_extra=_show(operation=["send_media"], media_type=["document"]),
    )

    # --- send_template ------------------------------------------------------
    template_name: str = Field(
        default="",
        description="Exact name of the approved template.",
        json_schema_extra={
            "loadOptionsMethod": "whatsappBusinessTemplates",
            **_show(operation=["send_template"]),
        },
    )
    language_code: str = Field(
        default="en_US",
        description="Template language, e.g. en_US, es_MX, hi. Meta does not translate.",
        json_schema_extra=_show(operation=["send_template"]),
    )
    body_parameters: List[str] = Field(
        default_factory=list,
        description=(
            "Values for the body placeholders, in order. For a named template "
            "use the Named Parameters field instead."
        ),
        json_schema_extra=_show(operation=["send_template"]),
    )
    named_parameters: Dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Values keyed by placeholder name, for templates created with "
            "parameter_format=named."
        ),
        json_schema_extra=_show(operation=["send_template"]),
    )
    header_media_id: str = Field(
        default="",
        description="Media ID for a template whose header is an image, video or document.",
        json_schema_extra=_show(operation=["send_template"]),
    )
    header_media_type: Literal["image", "video", "document"] = Field(
        default="image",
        description="Which media kind the template header expects.",
        json_schema_extra=_show(operation=["send_template"]),
    )

    # --- interactive --------------------------------------------------------
    body: str = Field(
        default="",
        description="Main message text.",
        json_schema_extra={"rows": 3, **_show(operation=_INTERACTIVE_OPS)},
    )
    header: str = Field(
        default="",
        description="Optional bold header text.",
        json_schema_extra=_show(operation=_INTERACTIVE_OPS),
    )
    footer: str = Field(
        default="",
        description="Optional small footer text.",
        json_schema_extra=_show(operation=_INTERACTIVE_OPS),
    )
    buttons: List[Dict[str, str]] = Field(
        default_factory=list,
        description='Up to 3 reply buttons: [{"id": "yes", "title": "Yes"}]',
        json_schema_extra=_show(operation=["send_buttons"]),
    )
    list_button_text: str = Field(
        default="Choose",
        description="Label on the button that opens the list.",
        json_schema_extra=_show(operation=["send_list"]),
    )
    sections: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            'Sections of rows: [{"title": "Plans", "rows": '
            '[{"id": "p1", "title": "Basic", "description": "..."}]}]. '
            "10 rows maximum across all sections."
        ),
        json_schema_extra=_show(operation=["send_list"]),
    )
    cta_display_text: str = Field(
        default="",
        description="Button label for the CTA URL.",
        json_schema_extra=_show(operation=["send_cta_url"]),
    )
    cta_url: str = Field(
        default="",
        description="URL the button opens.",
        json_schema_extra=_show(operation=["send_cta_url"]),
    )

    # --- send_reaction ------------------------------------------------------
    reaction_message_id: str = Field(
        default="",
        description="wamid of the message to react to.",
        json_schema_extra=_show(operation=["send_reaction"]),
    )
    emoji: str = Field(
        default="",
        description="Single emoji. Leave empty to remove an existing reaction.",
        json_schema_extra=_show(operation=["send_reaction"]),
    )

    # --- send_location ------------------------------------------------------
    latitude: Optional[float] = Field(
        default=None,
        description="Latitude in decimal degrees.",
        json_schema_extra=_show(operation=["send_location"]),
    )
    longitude: Optional[float] = Field(
        default=None,
        description="Longitude in decimal degrees.",
        json_schema_extra=_show(operation=["send_location"]),
    )
    location_name: str = Field(
        default="",
        description="Optional place name.",
        json_schema_extra=_show(operation=["send_location"]),
    )
    location_address: str = Field(
        default="",
        description="Optional street address. Only shown when a name is set.",
        json_schema_extra=_show(operation=["send_location"]),
    )

    # --- send_contacts ------------------------------------------------------
    contacts: List[Dict[str, Any]] = Field(
        default_factory=list,
        description=(
            'Contact cards: [{"name": {"formatted_name": "Ada Lovelace", '
            '"first_name": "Ada"}, "phones": [{"phone": "+14155551234", '
            '"type": "CELL"}]}]'
        ),
        json_schema_extra=_show(operation=["send_contacts"]),
    )

    # There is deliberately NO phone_number_id parameter.
    #
    # It selects which business identity a message is sent *from*, so it is
    # exactly the field a prompt injection in an inbound message would want to
    # set. Listed in server_controlled_fields, a declared field would be kept
    # from the model on every tool path (BaseNode.execute_as_tool strips it in
    # process; NodeExecutor restores the saved value on Temporal), but it would
    # still appear in the tool schema: ToolInput is a ToolNode extension, so
    # this ActionNode's schema is its whole Params, and the model would be
    # offered a field it cannot set.
    #
    # The sending number therefore comes only from the credential, which keeps
    # it out of the schema and out of every parameter merge. A per-node
    # override belongs with multi-number support, which is deferred.

    model_config = ConfigDict(extra="ignore")

    @field_validator("buttons", "sections", "body_parameters", "contacts", mode="before")
    @classmethod
    def _coerce_json_list(cls, value: Any) -> Any:
        """LLM tool arguments routinely arrive as stringified JSON.

        A ValidationError here reads as a node bug rather than a retryable
        argument mistake, so a string is parsed before Pydantic sees it.
        Malformed input still falls through.
        """
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            import json

            try:
                parsed = json.loads(text)
            except ValueError:
                return []
            return parsed if isinstance(parsed, list) else [parsed]
        return value

    @field_validator("named_parameters", mode="before")
    @classmethod
    def _coerce_json_dict(cls, value: Any) -> Any:
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return {}
            import json

            try:
                parsed = json.loads(text)
            except ValueError:
                return {}
            return parsed if isinstance(parsed, dict) else {}
        return value


class WhatsAppBusinessSendOutput(BaseModel):
    message_id: Optional[str] = None
    to: Optional[str] = None
    wa_id: Optional[str] = None
    phone_number_id: Optional[str] = None
    # Meta's reference schema always documents this, but no per-type example
    # includes it, so it is parsed as optional rather than assumed.
    message_status: Optional[str] = None
    # list_templates only.
    templates: List[dict] = Field(default_factory=list)
    count: Optional[int] = None

    model_config = ConfigDict(extra="allow")


class WhatsAppBusinessSendNode(ActionNode):
    type = "whatsappBusinessSend"
    display_name = "WhatsApp Business Send"
    subtitle = "Cloud API"
    group = ("whatsapp_business", "tool")
    description = "Send any WhatsApp message type through Meta's official Cloud API"
    component_kind = "square"
    tool_name = "whatsapp_business_send"
    tool_description = (
        "Send a WhatsApp message through the official Business Cloud API. Set "
        "`operation` to choose the kind: send_text, send_media, send_template, "
        "send_buttons, send_list, send_cta_url, send_reaction, send_location, "
        "send_contacts, or list_templates. Everything except send_template and "
        "list_templates only works if the recipient messaged the business within "
        "the last 24 hours; outside that window an approved template is the only "
        "way through. Use list_templates to see what is approved and how many "
        "placeholders each one takes."
    )
    handles = (
        {"name": "input-main", "kind": "input", "position": "left", "label": "Input", "role": "main"},
        {"name": "output-main", "kind": "output", "position": "right", "label": "Output", "role": "main"},
    )
    credentials = (WhatsAppBusinessCredential,)
    annotations = {"destructive": False, "readonly": False, "open_world": True}
    task_queue = TaskQueue.MESSAGING
    usable_as_tool = True
    approval = ApprovalSpec(
        channel="WhatsApp Business",
        action="Send a WhatsApp message",
        operations=frozenset(
            {
                "send_text",
                "send_media",
                "send_template",
                "send_buttons",
                "send_list",
                "send_cta_url",
                "send_reaction",
                "send_location",
                "send_contacts",
            }
        ),
        recipient=("to",),
        body=("text", "body", "caption"),
        details=(("Template", "template_name"), ("Link", "media_url")),
        max_length=4096,
    )

    # ToolInput / server_controlled_fields are deliberately NOT declared.
    # ToolInput is a ToolNode extension; nothing reads it on an ActionNode.
    # server_controlled_fields would work here (every tool path honours it),
    # but no field in Params needs it: the one field the model must not
    # control, the sending number, is kept out of Params (see above).
    Params = WhatsAppBusinessSendParams
    Output = WhatsAppBusinessSendOutput

    # --- text ---------------------------------------------------------------

    @Operation("send_text", cost={"service": "whatsapp_business", "action": "send_text", "count": 1})
    async def send_text(
        self, ctx: NodeContext, params: WhatsAppBusinessSendParams
    ) -> WhatsAppBusinessSendOutput:
        body = params.text or ""
        if not body.strip():
            raise NodeUserError("Cannot send an empty WhatsApp message.")

        if params.format_markdown:
            from services.markdown_formatter import to_whatsapp

            body = to_whatsapp(body)

        if len(body) > _MAX_TEXT_BODY:
            raise NodeUserError(
                f"Message body is {len(body)} characters; WhatsApp accepts at most "
                f"{_MAX_TEXT_BODY}. Split it before sending."
            )

        return await self._post_message(
            ctx,
            params,
            "text",
            {"preview_url": params.preview_url, "body": body},
        )

    # --- media --------------------------------------------------------------

    @Operation("send_media", cost={"service": "whatsapp_business", "action": "send_media", "count": 1})
    async def send_media(
        self, ctx: NodeContext, params: WhatsAppBusinessSendParams
    ) -> WhatsAppBusinessSendOutput:
        media_type = params.media_type
        phone_number_id = await resolve_phone_number_id(ctx)

        # `id` and `link` are a strict oneOf in Meta's schema -- sending both
        # is rejected, so exactly one is built here.
        media_object: Dict[str, Any] = {}
        if params.media_source == "link":
            if not params.media_url.strip():
                raise NodeUserError("Provide a media URL, or switch the source to file/id.")
            media_object["link"] = params.media_url.strip()
        elif params.media_source == "id":
            if not params.media_id.strip():
                raise NodeUserError("Provide a media ID, or switch the source to file/link.")
            media_object["id"] = params.media_id.strip()
        else:
            media_object["id"] = await _upload_for_send(ctx, params, phone_number_id)

        # Meta rejects a caption on audio and sticker rather than ignoring it.
        if params.caption.strip() and media_type not in {"audio", "sticker"}:
            media_object["caption"] = params.caption.strip()
        if media_type == "document" and params.media_filename.strip():
            media_object["filename"] = params.media_filename.strip()

        return await self._post_message(
            ctx, params, media_type, media_object, phone_number_id=phone_number_id
        )

    # --- template -----------------------------------------------------------

    @Operation("send_template", cost={"service": "whatsapp_business", "action": "send_template", "count": 1})
    async def send_template(
        self, ctx: NodeContext, params: WhatsAppBusinessSendParams
    ) -> WhatsAppBusinessSendOutput:
        if not params.template_name.strip():
            raise NodeUserError("Choose a template name. Use list_templates to see approved ones.")

        components: List[Dict[str, Any]] = []
        if params.header_media_id.strip():
            components.append(
                {
                    "type": "header",
                    "parameters": [
                        {
                            "type": params.header_media_type,
                            params.header_media_type: {"id": params.header_media_id.strip()},
                        }
                    ],
                }
            )

        body_params = _build_body_parameters(params)
        if body_params:
            components.append({"type": "body", "parameters": body_params})

        template: Dict[str, Any] = {
            "name": params.template_name.strip(),
            # An object at send time, a bare string at creation time. The
            # asymmetry is Meta's, not ours.
            "language": {"code": params.language_code.strip() or "en_US"},
        }
        if components:
            template["components"] = components

        return await self._post_message(ctx, params, "template", template, quotable=False)

    @Operation("list_templates")
    async def list_templates(
        self, ctx: NodeContext, params: WhatsAppBusinessSendParams
    ) -> WhatsAppBusinessSendOutput:
        from services.plugin.deps import get_auth_service

        waba_id = await get_auth_service().get_api_key("whatsapp_business_waba_id")
        if not waba_id:
            raise NodeUserError(
                "Add your WhatsApp Business Account ID to the credential to list templates."
            )

        result = await graph_get(
            ctx,
            f"{waba_id}/message_templates",
            {"fields": "name,status,category,language,components", "limit": 100},
        )
        rows = [
            {
                "name": item.get("name"),
                "status": item.get("status"),
                "category": item.get("category"),
                "language": item.get("language"),
                "placeholders": _count_placeholders(item),
            }
            for item in (result.get("data") or [])
        ]
        return WhatsAppBusinessSendOutput(templates=rows, count=len(rows))

    # --- interactive --------------------------------------------------------

    @Operation("send_buttons", cost={"service": "whatsapp_business", "action": "send_interactive", "count": 1})
    async def send_buttons(
        self, ctx: NodeContext, params: WhatsAppBusinessSendParams
    ) -> WhatsAppBusinessSendOutput:
        if not params.buttons:
            raise NodeUserError('Add at least one button, e.g. [{"id": "yes", "title": "Yes"}].')
        if len(params.buttons) > _MAX_BUTTONS:
            raise NodeUserError(
                f"WhatsApp allows at most {_MAX_BUTTONS} reply buttons; got {len(params.buttons)}. "
                "Use a list message for more options."
            )

        buttons = []
        for index, button in enumerate(params.buttons):
            title = str(button.get("title") or "").strip()
            if not title:
                raise NodeUserError(f"Button {index + 1} has no title.")
            if len(title) > _MAX_BUTTON_TITLE:
                raise NodeUserError(
                    f"Button title {title!r} is {len(title)} characters; the limit is {_MAX_BUTTON_TITLE}."
                )
            buttons.append(
                {
                    "type": "reply",
                    "reply": {"id": str(button.get("id") or f"btn_{index + 1}"), "title": title},
                }
            )

        return await self._send_interactive(
            ctx, params, {"type": "button", "action": {"buttons": buttons}}
        )

    @Operation("send_list", cost={"service": "whatsapp_business", "action": "send_interactive", "count": 1})
    async def send_list(
        self, ctx: NodeContext, params: WhatsAppBusinessSendParams
    ) -> WhatsAppBusinessSendOutput:
        if not params.sections:
            raise NodeUserError("Add at least one section with rows.")

        total_rows = sum(len(section.get("rows") or []) for section in params.sections)
        if total_rows == 0:
            raise NodeUserError("The list has no rows.")
        if total_rows > _MAX_ROWS_TOTAL:
            # The cap is across ALL sections, not per section -- a common
            # misreading, so the message says so.
            raise NodeUserError(
                f"A list allows {_MAX_ROWS_TOTAL} rows in total across every section; got {total_rows}."
            )

        button_text = params.list_button_text.strip() or "Choose"
        if len(button_text) > _MAX_LIST_BUTTON:
            raise NodeUserError(
                f"List button text is {len(button_text)} characters; the limit is {_MAX_LIST_BUTTON}."
            )

        sections = []
        for section in params.sections:
            rows = []
            for index, row in enumerate(section.get("rows") or []):
                title = str(row.get("title") or "").strip()
                if not title:
                    raise NodeUserError(f"Row {index + 1} has no title.")
                if len(title) > _MAX_ROW_TITLE:
                    raise NodeUserError(
                        f"Row title {title!r} is {len(title)} characters; the limit is {_MAX_ROW_TITLE}."
                    )
                entry = {"id": str(row.get("id") or f"row_{index + 1}"), "title": title}
                description = str(row.get("description") or "").strip()
                if description:
                    if len(description) > _MAX_ROW_DESCRIPTION:
                        raise NodeUserError(
                            f"Row description for {title!r} is {len(description)} characters; "
                            f"the limit is {_MAX_ROW_DESCRIPTION}."
                        )
                    entry["description"] = description
                rows.append(entry)
            sections.append({"title": str(section.get("title") or "")[:24], "rows": rows})

        return await self._send_interactive(
            ctx,
            params,
            {"type": "list", "action": {"button": button_text, "sections": sections}},
        )

    @Operation("send_cta_url", cost={"service": "whatsapp_business", "action": "send_interactive", "count": 1})
    async def send_cta_url(
        self, ctx: NodeContext, params: WhatsAppBusinessSendParams
    ) -> WhatsAppBusinessSendOutput:
        display_text = params.cta_display_text.strip()
        url = params.cta_url.strip()
        if not display_text or not url:
            raise NodeUserError("A CTA button needs both display text and a URL.")

        return await self._send_interactive(
            ctx,
            params,
            {
                "type": "cta_url",
                "action": {
                    "name": "cta_url",
                    "parameters": {"display_text": display_text, "url": url},
                },
            },
        )

    # --- reaction / location / contacts -------------------------------------

    @Operation("send_reaction", cost={"service": "whatsapp_business", "action": "send_reaction", "count": 1})
    async def send_reaction(
        self, ctx: NodeContext, params: WhatsAppBusinessSendParams
    ) -> WhatsAppBusinessSendOutput:
        """React to an inbound message.

        An empty emoji is not an error: Meta documents the empty string as the
        way to *remove* a previously sent reaction, so it is passed through
        rather than rejected.
        """
        target = params.reaction_message_id.strip()
        if not target:
            raise NodeUserError(
                "A reaction needs the wamid of the message to react to. Inbound "
                "messages carry it on message_id."
            )
        # A reaction addresses its target through reaction.message_id, so it
        # never carries a `context` block of its own.
        return await self._post_message(
            ctx,
            params,
            "reaction",
            {"message_id": target, "emoji": params.emoji.strip()},
            quotable=False,
        )

    @Operation("send_location", cost={"service": "whatsapp_business", "action": "send_location", "count": 1})
    async def send_location(
        self, ctx: NodeContext, params: WhatsAppBusinessSendParams
    ) -> WhatsAppBusinessSendOutput:
        if params.latitude is None or params.longitude is None:
            raise NodeUserError("A location needs both a latitude and a longitude.")
        if not -90 <= params.latitude <= 90:
            raise NodeUserError(f"Latitude {params.latitude} is outside -90..90.")
        if not -180 <= params.longitude <= 180:
            raise NodeUserError(f"Longitude {params.longitude} is outside -180..180.")

        location: Dict[str, Any] = {
            "latitude": params.latitude,
            "longitude": params.longitude,
        }
        name = params.location_name.strip()
        address = params.location_address.strip()
        if name:
            location["name"] = name
            # Meta only renders address alongside a name; sending it alone is
            # accepted and then silently dropped, so it is nested here to keep
            # the payload honest about what will actually show.
            if address:
                location["address"] = address

        return await self._post_message(ctx, params, "location", location)

    @Operation("send_contacts", cost={"service": "whatsapp_business", "action": "send_contacts", "count": 1})
    async def send_contacts(
        self, ctx: NodeContext, params: WhatsAppBusinessSendParams
    ) -> WhatsAppBusinessSendOutput:
        if not params.contacts:
            raise NodeUserError("Add at least one contact card.")

        for index, contact in enumerate(params.contacts):
            name = contact.get("name")
            if not isinstance(name, dict) or not str(name.get("formatted_name") or "").strip():
                raise NodeUserError(
                    f"Contact {index + 1} needs name.formatted_name; Meta rejects "
                    "a contact card without it."
                )

        # `contacts` is the one message type whose sibling is a list, so it
        # bypasses the dict-shaped helper.
        recipient = normalize_recipient(params.to)
        phone_number_id = await resolve_phone_number_id(ctx)
        payload: Dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "contacts",
            "contacts": params.contacts,
        }
        reply_to = params.reply_to_message_id.strip()
        if reply_to:
            payload["context"] = {"message_id": reply_to}

        result = await graph_post(ctx, f"{phone_number_id}/messages", payload)
        return _shape_send_result(result, recipient, phone_number_id)

    # --- shared senders -----------------------------------------------------

    async def _post_message(
        self,
        ctx: NodeContext,
        params: WhatsAppBusinessSendParams,
        message_type: str,
        sibling: Dict[str, Any],
        *,
        phone_number_id: Optional[str] = None,
        quotable: bool = True,
    ) -> WhatsAppBusinessSendOutput:
        """POST one message. Every type differs only in `type` and one sibling."""
        recipient = normalize_recipient(params.to)
        if phone_number_id is None:
            phone_number_id = await resolve_phone_number_id(ctx)

        payload: Dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": message_type,
            message_type: sibling,
        }
        reply_to = params.reply_to_message_id.strip()
        if quotable and reply_to:
            payload["context"] = {"message_id": reply_to}

        result = await graph_post(ctx, f"{phone_number_id}/messages", payload)
        return _shape_send_result(result, recipient, phone_number_id)

    async def _send_interactive(
        self,
        ctx: NodeContext,
        params: WhatsAppBusinessSendParams,
        interactive: Dict[str, Any],
    ) -> WhatsAppBusinessSendOutput:
        body = params.body.strip()
        if not body:
            raise NodeUserError("An interactive message needs body text.")
        if len(body) > _MAX_BODY:
            raise NodeUserError(f"Body is {len(body)} characters; the limit is {_MAX_BODY}.")

        interactive = dict(interactive)
        interactive["body"] = {"text": body}

        header = params.header.strip()
        if header:
            if len(header) > _MAX_HEADER:
                raise NodeUserError(
                    f"Header is {len(header)} characters; the limit is {_MAX_HEADER}."
                )
            # Text-only by construction. A list header must be text, and the
            # other two subtypes are only given text headers here, so there is
            # no media-header branch to guard.
            interactive["header"] = {"type": "text", "text": header}

        footer = params.footer.strip()
        if footer:
            if len(footer) > _MAX_FOOTER:
                raise NodeUserError(
                    f"Footer is {len(footer)} characters; the limit is {_MAX_FOOTER}."
                )
            interactive["footer"] = {"text": footer}

        return await self._post_message(
            ctx, params, "interactive", interactive, quotable=False
        )


async def _upload_for_send(
    ctx: NodeContext, params: WhatsAppBusinessSendParams, phone_number_id: str
) -> str:
    """Upload a workspace file and return the media id Meta assigns."""
    import mimetypes

    from services.media import coerce_file_param

    if params.media in (None, ""):
        raise NodeUserError("Select a file to send, or switch the media source to id/link.")

    filename, blob = coerce_file_param(params.media, ctx=ctx)

    mime_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    result = await graph_post(
        ctx,
        f"{phone_number_id}/media",
        # Bytes, not a file handle: Connection.request replays the same kwargs
        # on its one auth retry, and a consumed handle would replay empty.
        files={"file": (filename, blob, mime_type)},
        data={"messaging_product": "whatsapp", "type": mime_type},
    )
    media_id = result.get("id")
    if not media_id:
        raise NodeUserError("WhatsApp accepted the upload but returned no media ID.")
    return str(media_id)


def _build_body_parameters(params: WhatsAppBusinessSendParams) -> List[Dict[str, Any]]:
    """Named and positional are mutually exclusive, fixed at template creation.

    Named wins when both are supplied, because a named template rejects
    positional values outright with error 132000 rather than ignoring them.
    """
    if params.named_parameters:
        return [
            {"type": "text", "parameter_name": key, "text": str(value)}
            for key, value in params.named_parameters.items()
        ]
    return [{"type": "text", "text": str(value)} for value in params.body_parameters]


def _count_placeholders(template: Dict[str, Any]) -> Optional[int]:
    """How many body values the template expects.

    Surfaced because a count mismatch is error 132000, the single most common
    template failure, and the number is otherwise invisible until a send fails.
    """
    for component in template.get("components") or []:
        if str(component.get("type", "")).upper() != "BODY":
            continue
        text = component.get("text") or ""
        example = component.get("example") or {}
        named = example.get("body_text_named_params")
        if isinstance(named, list):
            return len(named)
        import re

        return len(set(re.findall(r"\{\{\s*([^}]+?)\s*\}\}", text)))
    return None


def _shape_send_result(
    result: Dict[str, Any], recipient: str, phone_number_id: str
) -> WhatsAppBusinessSendOutput:
    messages: List[Dict[str, Any]] = result.get("messages") or []
    contacts: List[Dict[str, Any]] = result.get("contacts") or []
    first = messages[0] if messages else {}
    return WhatsAppBusinessSendOutput(
        message_id=first.get("id"),
        message_status=first.get("message_status"),
        to=recipient,
        wa_id=(contacts[0].get("wa_id") if contacts else None),
        phone_number_id=phone_number_id,
    )


__all__ = [
    "WhatsAppBusinessSendNode",
    "WhatsAppBusinessSendOutput",
    "WhatsAppBusinessSendParams",
]
