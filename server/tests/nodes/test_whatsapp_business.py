"""Contract for the official Meta WhatsApp Cloud API plugin.

The security-relevant test here is ``TestModelCannotChooseTheSendingNumber``.
A dual-purpose ActionNode's tool schema is its whole Params, so the way to keep
a field away from the model entirely, schema included, is to keep it out of
Params. This asserts that holds.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from nodes.whatsapp_business._base import classify_error, normalize_recipient
from nodes.whatsapp_business.whatsapp_business_send import (
    WhatsAppBusinessSendNode,
    WhatsAppBusinessSendParams,
)
from services.plugin import NodeUserError


pytestmark = pytest.mark.unit


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _ctx(**raw):
    return SimpleNamespace(
        node_id="wac-1",
        node_type="whatsappBusinessSend",
        workflow_id="wf-1",
        session_id="default",
        user_id="owner",
        workspace_dir=None,
        outputs={},
        nodes=[],
        edges=[],
        raw=dict(raw),
    )


def _capture(params, *, number="PN1", result=None):
    """Run one operation and return the outgoing Graph request."""
    node = WhatsAppBusinessSendNode()
    captured = {}

    async def _fake_post(ctx, path, body=None, **kwargs):
        captured["path"] = path
        captured["body"] = body
        captured["kwargs"] = kwargs
        return result or {"messages": [{"id": "wamid.X"}], "contacts": [{"wa_id": "1"}]}

    async def _fake_get(ctx, path, params=None):
        captured["path"] = path
        captured["params"] = params
        return result or {"data": []}

    with (
        patch("nodes.whatsapp_business.whatsapp_business_send.graph_post", new=_fake_post),
        patch("nodes.whatsapp_business.whatsapp_business_send.graph_get", new=_fake_get),
        patch(
            "services.plugin.deps.get_auth_service",
            return_value=SimpleNamespace(get_api_key=AsyncMock(return_value=number)),
        ),
    ):
        captured["envelope"] = _run(node.execute("wac-1", params, _ctx()))
    return captured


class TestOneNodeCoversTheMessagesEndpoint:
    """Meta models message type as a field on one endpoint, not as endpoints.

    The node mirrors that: every send operation posts
    ``{phone_number_id}/messages`` and differs only in ``type``. If someone
    splits these back into separate nodes, or routes one at a different URL,
    these fail.
    """

    SEND_OPS = [
        "send_text",
        "send_media",
        "send_template",
        "send_buttons",
        "send_list",
        "send_cta_url",
        "send_reaction",
        "send_location",
        "send_contacts",
    ]

    def test_every_message_type_is_an_operation_on_this_node(self):
        declared = WhatsAppBusinessSendParams.model_fields["operation"].annotation
        import typing

        values = set(typing.get_args(declared))
        assert values == set(self.SEND_OPS) | {"list_templates"}

    def test_the_absorbed_node_types_are_gone(self):
        """whatsappBusinessTemplate / ...Interactive were merged into send.

        They are unreleased, so they are deleted rather than aliased. A
        re-import would mean the split crept back.
        """
        import importlib

        for name in ("whatsapp_business_template", "whatsapp_business_interactive"):
            with pytest.raises(ModuleNotFoundError):
                importlib.import_module(f"nodes.whatsapp_business.{name}")

    def test_absorbed_types_are_not_registered(self):
        import nodes  # noqa: F401  -- triggers discovery
        from models.node_metadata import NODE_METADATA

        assert "whatsappBusinessTemplate" not in NODE_METADATA
        assert "whatsappBusinessInteractive" not in NODE_METADATA
        assert "whatsappBusinessSend" in NODE_METADATA


class TestNewMessageTypes:
    def test_reaction_addresses_its_target_and_never_quotes(self):
        """A reaction points at its target through reaction.message_id.

        Adding a `context` block as well is a different thing (a quoted
        reply), so reply_to_message_id must be ignored here.
        """
        captured = _capture(
            {
                "operation": "send_reaction",
                "to": "+14155551234",
                "reaction_message_id": "wamid.TARGET",
                "emoji": "\N{THUMBS UP SIGN}",
                "reply_to_message_id": "wamid.SOMETHINGELSE",
            }
        )
        assert captured["body"]["type"] == "reaction"
        assert captured["body"]["reaction"] == {
            "message_id": "wamid.TARGET",
            "emoji": "\N{THUMBS UP SIGN}",
        }
        assert "context" not in captured["body"]

    def test_empty_emoji_is_allowed_because_it_removes_a_reaction(self):
        """Meta documents "" as the removal signal, so it is not an error."""
        captured = _capture(
            {
                "operation": "send_reaction",
                "to": "+14155551234",
                "reaction_message_id": "wamid.TARGET",
                "emoji": "",
            }
        )
        assert captured["body"]["reaction"]["emoji"] == ""

    def test_reaction_without_a_target_is_refused(self):
        captured = _capture(
            {"operation": "send_reaction", "to": "+14155551234", "emoji": "x"}
        )
        assert captured["envelope"]["success"] is False
        assert captured["envelope"]["error_type"] == "NodeUserError"

    def test_location_sends_coordinates(self):
        captured = _capture(
            {
                "operation": "send_location",
                "to": "+14155551234",
                "latitude": 12.9716,
                "longitude": 77.5946,
            }
        )
        assert captured["body"]["type"] == "location"
        assert captured["body"]["location"] == {"latitude": 12.9716, "longitude": 77.5946}

    def test_location_address_is_nested_under_a_name(self):
        """Meta only renders an address alongside a name; alone it is dropped."""
        captured = _capture(
            {
                "operation": "send_location",
                "to": "+14155551234",
                "latitude": 1.0,
                "longitude": 2.0,
                "location_address": "221B Baker Street",
            }
        )
        assert "address" not in captured["body"]["location"]

        captured = _capture(
            {
                "operation": "send_location",
                "to": "+14155551234",
                "latitude": 1.0,
                "longitude": 2.0,
                "location_name": "Home",
                "location_address": "221B Baker Street",
            }
        )
        assert captured["body"]["location"]["address"] == "221B Baker Street"

    @pytest.mark.parametrize(
        "lat,lon",
        [(91.0, 0.0), (-91.0, 0.0), (0.0, 181.0), (0.0, -181.0)],
    )
    def test_out_of_range_coordinates_fail_locally(self, lat, lon):
        captured = _capture(
            {"operation": "send_location", "to": "+14155551234", "latitude": lat, "longitude": lon}
        )
        assert captured["envelope"]["success"] is False

    def test_missing_coordinate_is_refused(self):
        captured = _capture(
            {"operation": "send_location", "to": "+14155551234", "latitude": 1.0}
        )
        assert captured["envelope"]["success"] is False

    def test_contacts_sends_the_list_verbatim(self):
        card = {
            "name": {"formatted_name": "Ada Lovelace", "first_name": "Ada"},
            "phones": [{"phone": "+14155551234", "type": "CELL"}],
        }
        captured = _capture(
            {"operation": "send_contacts", "to": "+14155551234", "contacts": [card]}
        )
        assert captured["body"]["type"] == "contacts"
        assert captured["body"]["contacts"] == [card]

    def test_contact_without_formatted_name_is_refused(self):
        """Meta rejects the card outright; failing locally names the field."""
        captured = _capture(
            {
                "operation": "send_contacts",
                "to": "+14155551234",
                "contacts": [{"phones": [{"phone": "+1"}]}],
            }
        )
        assert captured["envelope"]["success"] is False
        assert "formatted_name" in captured["envelope"]["error"]

    def test_contacts_accepts_a_stringified_list(self):
        """LLM tool arguments routinely arrive as JSON strings."""
        captured = _capture(
            {
                "operation": "send_contacts",
                "to": "+14155551234",
                "contacts": '[{"name": {"formatted_name": "Ada"}}]',
            }
        )
        assert captured["body"]["contacts"] == [{"name": {"formatted_name": "Ada"}}]


class TestAbsorbedTemplateAndInteractive:
    """The merged operations keep the payloads their old nodes produced."""

    def test_template_envelope_survives_the_merge(self):
        captured = _capture(
            {
                "operation": "send_template",
                "to": "+14155551234",
                "template_name": "order_update",
                "language_code": "en_US",
                "body_parameters": ["A", "B"],
            }
        )
        assert captured["body"]["type"] == "template"
        assert captured["body"]["template"]["name"] == "order_update"
        assert captured["body"]["template"]["language"] == {"code": "en_US"}
        assert captured["body"]["template"]["components"] == [
            {"type": "body", "parameters": [{"type": "text", "text": "A"}, {"type": "text", "text": "B"}]}
        ]

    def test_named_parameters_win_over_positional(self):
        """A named template rejects positional values with 132000."""
        captured = _capture(
            {
                "operation": "send_template",
                "to": "+14155551234",
                "template_name": "t",
                "body_parameters": ["positional"],
                "named_parameters": {"customer": "Ada"},
            }
        )
        params = captured["body"]["template"]["components"][0]["parameters"]
        assert params == [{"type": "text", "parameter_name": "customer", "text": "Ada"}]

    def test_list_templates_hits_the_waba_endpoint_not_messages(self):
        """The one WABA-scoped call in the node. A phone-scoped path 404s."""
        captured = _capture(
            {"operation": "list_templates"},
            number="WABA123",
            result={"data": [{"name": "t", "status": "APPROVED"}]},
        )
        assert captured["path"] == "WABA123/message_templates"

    def test_buttons_envelope_survives_the_merge(self):
        captured = _capture(
            {
                "operation": "send_buttons",
                "to": "+14155551234",
                "body": "Confirm?",
                "buttons": [{"id": "yes", "title": "Yes"}],
            }
        )
        assert captured["body"]["type"] == "interactive"
        assert captured["body"]["interactive"]["type"] == "button"
        assert captured["body"]["interactive"]["action"]["buttons"] == [
            {"type": "reply", "reply": {"id": "yes", "title": "Yes"}}
        ]
        assert captured["body"]["interactive"]["body"] == {"text": "Confirm?"}

    def test_more_than_three_buttons_is_refused(self):
        captured = _capture(
            {
                "operation": "send_buttons",
                "to": "+14155551234",
                "body": "Pick",
                "buttons": [{"id": str(i), "title": str(i)} for i in range(4)],
            }
        )
        assert captured["envelope"]["success"] is False

    def test_list_row_cap_is_across_all_sections(self):
        sections = [
            {"title": "A", "rows": [{"id": f"a{i}", "title": f"a{i}"} for i in range(6)]},
            {"title": "B", "rows": [{"id": f"b{i}", "title": f"b{i}"} for i in range(6)]},
        ]
        captured = _capture(
            {"operation": "send_list", "to": "+14155551234", "body": "Pick", "sections": sections}
        )
        assert captured["envelope"]["success"] is False
        assert "across every section" in captured["envelope"]["error"]

    def test_cta_url_payload_shape(self):
        captured = _capture(
            {
                "operation": "send_cta_url",
                "to": "+14155551234",
                "body": "Open it",
                "cta_display_text": "Open",
                "cta_url": "https://example.com",
            }
        )
        assert captured["body"]["interactive"] == {
            "type": "cta_url",
            "action": {
                "name": "cta_url",
                "parameters": {"display_text": "Open", "url": "https://example.com"},
            },
            "body": {"text": "Open it"},
        }

    def test_interactive_header_is_text_typed(self):
        captured = _capture(
            {
                "operation": "send_cta_url",
                "to": "+14155551234",
                "body": "b",
                "header": "Title",
                "cta_display_text": "Open",
                "cta_url": "https://example.com",
            }
        )
        assert captured["body"]["interactive"]["header"] == {"type": "text", "text": "Title"}

    def test_interactive_never_quotes(self):
        """Meta rejects `context` alongside an interactive payload."""
        captured = _capture(
            {
                "operation": "send_buttons",
                "to": "+14155551234",
                "body": "b",
                "buttons": [{"id": "y", "title": "Y"}],
                "reply_to_message_id": "wamid.P",
            }
        )
        assert "context" not in captured["body"]


class TestRunAndDeployAgreeOnOutputShape:
    """The deployed path hands downstream nodes ``event.data`` flat, while
    the base ``shape_output`` dumps the whole CloudEvents envelope. Without
    an override, ``{{trigger.text}}`` resolves when deployed and breaks on
    Run, and the Run output does not match the declared Output model."""

    def _event(self):
        from services.events import WorkflowEvent

        return WorkflowEvent(
            source="opencompany://test",
            type="com.opencompany.whatsapp_business.message.received",
            data={"message_id": "wamid.1", "text": "hi"},
        )

    @pytest.mark.parametrize(
        "node_cls_name",
        ["WhatsAppBusinessReceiveNode", "WhatsAppBusinessStatusNode"],
    )
    def test_shape_output_is_the_flat_payload(self, node_cls_name):
        import nodes.whatsapp_business.whatsapp_business_receive as mod

        node = getattr(mod, node_cls_name)()
        shaped = node.shape_output(self._event())
        assert shaped == {"message_id": "wamid.1", "text": "hi"}
        # The envelope members must not leak into the trigger output.
        for envelope_only in ("specversion", "id", "source", "type", "data"):
            assert envelope_only not in shaped

    def test_both_triggers_share_one_implementation(self):
        """They inherit it, so the two paths cannot drift apart."""
        import nodes.whatsapp_business.whatsapp_business_receive as mod

        assert (
            mod.WhatsAppBusinessReceiveNode.shape_output
            is mod.WhatsAppBusinessStatusNode.shape_output
        )

    def test_the_shared_base_is_not_itself_a_node(self):
        from models.node_metadata import NODE_METADATA

        assert "_WhatsAppBusinessTrigger" not in NODE_METADATA


_NODE_TYPES = (
    "whatsappBusinessSend",
    "whatsappBusinessMedia",
    "whatsappBusinessReceive",
    "whatsappBusinessStatus",
)


class TestEachNodeShipsItsOwnBrandedIcon:
    """All four rendered the same glyph before: only Send had a per-node
    SVG, so the rest fell back to the folder's shared one.

    They are brand artwork rather than a library reference on purpose. A
    `lucide:<Name>` string is a hard dependency on a third-party export
    that can be renamed or dropped, and when that happens the node simply
    renders nothing -- no error. Generated SVGs in the plugin folder have
    no such failure mode, and carry WhatsApp's own green.
    """

    def _icon_paths(self):
        import pathlib

        folder = pathlib.Path(__file__).resolve().parents[2] / "nodes" / "whatsapp_business"
        return folder, {p.name for p in folder.glob("*.svg")}

    def test_every_node_type_resolves_to_its_own_file(self):
        from nodes._visuals import get_plugin_icon_path

        paths = {t: get_plugin_icon_path(t) for t in _NODE_TYPES}
        assert all(p is not None for p in paths.values()), paths
        # Distinct files, not four references to one shared icon.
        assert len({str(p) for p in paths.values()}) == len(_NODE_TYPES)

    def test_spec_serves_a_distinct_icon_endpoint_per_node(self):
        from services.node_spec import get_node_spec

        icons = set()
        for node_type in _NODE_TYPES:
            spec = get_node_spec(node_type)
            data = spec if isinstance(spec, dict) else spec.model_dump(mode="json")
            icons.add(data["icon"])
        assert len(icons) == len(_NODE_TYPES)

    def test_icons_are_purpose_glyphs_in_whatsapp_colours(self):
        """One bold glyph per node, painted in WhatsApp green.

        Three earlier attempts failed on the canvas, at roughly 28px:
        monochrome library line art read as washed-out grey; a hand-drawn
        speech bubble did not read as WhatsApp at all; and the real logo
        plus a corner badge left the logo too small and the badge glyph
        illegible. What works is a single purpose-built symbol filling the
        box, carrying the brand through colour rather than the mark.
        """
        import xml.etree.ElementTree as ET

        folder, names = self._icon_paths()
        for name in names - {"whatsapp_business.svg"}:
            body = (folder / name).read_text(encoding="utf-8")
            ET.fromstring(body)  # malformed SVG renders as nothing, silently
            assert "#25D366" in body, f"{name} does not use the WhatsApp brand green"
            # The logo belongs to the credential mark, not the node icons:
            # shrinking it to fit a badge is what made these unreadable.
            assert "M17.472 14.382" not in body, f"{name} re-embeds the full logo"

    def test_credential_brand_mark_is_kept(self):
        """It resolves through a different chain (Credential.get_icon_path),
        so it is not interchangeable with the per-node files."""
        _, names = self._icon_paths()
        assert "whatsapp_business.svg" in names

    def test_no_shared_icon_shadows_the_per_node_files(self):
        """A folder-level icon.svg would still be reachable, but its only
        effect here would be to mask a missing per-node file."""
        _, names = self._icon_paths()
        assert "icon.svg" not in names


class TestFileWidgetVisibility:
    def test_media_picker_is_gated_on_operation_as_well_as_source(self):
        """media_source defaults to "file", so keying on it alone rendered the
        file picker while the operation was send_text."""
        show = WhatsAppBusinessSendParams.model_fields["media"].json_schema_extra["displayOptions"]["show"]
        assert show["operation"] == ["send_media"]
        assert show["media_source"] == ["file"]


class TestParamsShape:
    def test_phone_number_id_is_not_a_parameter(self):
        """It selects the business identity a message is sent FROM.

        Declaring it would put it in the model's tool schema on this node
        kind, so it is credential-sourced instead. If someone adds it back,
        this fails.
        """
        assert "phone_number_id" not in WhatsAppBusinessSendParams.model_fields

    def test_tool_schema_never_exposes_the_sending_number(self):
        schema = WhatsAppBusinessSendParams.model_json_schema()
        assert "phone_number_id" not in schema.get("properties", {})

    def test_tool_facing_schema_is_flat(self):
        """LLM function-calling rejects $ref; the contract test enforces this
        repo-wide, asserted here too so the reason is local."""
        import json

        schema = WhatsAppBusinessSendParams.model_json_schema()
        assert "$defs" not in schema
        assert "$ref" not in json.dumps(schema)


class TestModelCannotChooseTheSendingNumber:
    def test_tool_args_cannot_override_the_credential_number(self):
        """Drive the real tool path, not the schema.

        A prompt injection in an inbound WhatsApp message is the realistic
        source of hostile tool arguments, and sending as another tenant's
        number is the worst outcome available here.
        """
        node = WhatsAppBusinessSendNode()
        captured = {}

        async def _fake_post(ctx, path, body=None, **kwargs):
            captured["path"] = path
            captured["body"] = body
            return {
                "messages": [{"id": "wamid.TEST"}],
                "contacts": [{"wa_id": "14155551234"}],
            }

        with (
            patch("nodes.whatsapp_business.whatsapp_business_send.graph_post", new=_fake_post),
            patch(
                "services.plugin.deps.get_auth_service",
                return_value=SimpleNamespace(get_api_key=AsyncMock(return_value="OPERATOR_NUMBER")),
            ),
        ):
            result = _run(
                node.execute_as_tool(
                    {"to": "+14155551234", "text": "hi", "phone_number_id": "ATTACKER_NUMBER"},
                    {"operation": "send_text"},
                    _ctx(),
                )
            )

        assert "ATTACKER_NUMBER" not in captured["path"]
        assert captured["path"].startswith("OPERATOR_NUMBER/")
        assert result.get("phone_number_id") == "OPERATOR_NUMBER"

    def test_missing_credential_number_is_a_clear_error(self):
        node = WhatsAppBusinessSendNode()

        with patch(
            "services.plugin.deps.get_auth_service",
            return_value=SimpleNamespace(get_api_key=AsyncMock(return_value=None)),
        ):
            envelope = _run(
                node.execute("wac-1", {"operation": "send_text", "to": "+14155551234", "text": "hi"}, _ctx())
            )

        assert envelope["success"] is False
        assert envelope["error_type"] == "NodeUserError"
        assert "phone number" in envelope["error"].lower()


class TestSendText:
    def _send(self, params, *, number="PN1"):
        return _capture({"operation": "send_text", **params}, number=number)

    def test_builds_the_documented_text_envelope(self):
        captured = self._send({"to": "+1 (415) 555-1234", "text": "hello", "format_markdown": False})
        assert captured["body"] == {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            # Punctuation a user would paste from a contacts app is stripped;
            # Meta rejects it as error 131009 otherwise.
            "to": "+14155551234",
            "type": "text",
            "text": {"preview_url": False, "body": "hello"},
        }

    def test_reply_threads_via_context(self):
        captured = self._send(
            {"to": "+14155551234", "text": "hi", "reply_to_message_id": "wamid.PARENT", "format_markdown": False}
        )
        assert captured["body"]["context"] == {"message_id": "wamid.PARENT"}

    def test_no_context_key_when_not_replying(self):
        captured = self._send({"to": "+14155551234", "text": "hi", "format_markdown": False})
        assert "context" not in captured["body"]

    def test_markdown_is_converted_by_default(self):
        captured = self._send({"to": "+14155551234", "text": "**bold**"})
        assert captured["body"]["text"]["body"] == "*bold*"

    def test_oversize_body_is_refused_not_truncated(self):
        """Silently dropping the tail of a business message is worse than
        failing."""
        envelope = self._send(
            {"to": "+14155551234", "text": "a" * 5000, "format_markdown": False}
        )["envelope"]
        assert envelope["success"] is False
        assert "4096" in envelope["error"]

    def test_empty_body_is_refused(self):
        envelope = self._send({"to": "+14155551234", "text": "   "})["envelope"]
        assert envelope["success"] is False


class TestRecipientNormalization:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("+1 (415) 555-1234", "+14155551234"),
            ("14155551234", "14155551234"),
            ("+44 20 7946 0958", "+442079460958"),
        ],
    )
    def test_punctuation_is_stripped(self, raw, expected):
        assert normalize_recipient(raw) == expected

    @pytest.mark.parametrize("raw", ["", "   ", "+", "not-a-number"])
    def test_unusable_input_is_refused(self, raw):
        with pytest.raises(NodeUserError):
            normalize_recipient(raw)


class TestErrorClassification:
    @pytest.mark.parametrize(
        "code,category,retryable",
        [
            (190, "auth", True),
            (0, "auth", True),
            (10, "permission", False),
            (200, "permission", False),
            (130429, "throttle", True),
            (131056, "throttle", True),
            (131057, "transient", True),
            (131047, "window_closed", False),
            (131050, "policy", False),
            (132000, "template", False),
            (133015, "account", False),
        ],
    )
    def test_codes_land_in_the_right_class(self, code, category, retryable):
        assert classify_error(code) == (category, retryable)

    def test_throttles_are_not_node_user_errors(self):
        """NodeUserError is non-retryable in the shared policy, so raising one
        for a throttle would defeat the backoff Meta asks for."""
        from nodes.whatsapp_business._base import raise_for_graph_error

        with pytest.raises(RuntimeError) as exc:
            raise_for_graph_error(
                {"error": {"code": 130429, "message": "Rate limit hit"}}, 429
            )
        assert not isinstance(exc.value, NodeUserError)

    def test_window_closed_points_at_the_template_node(self):
        from nodes.whatsapp_business._base import raise_for_graph_error

        with pytest.raises(NodeUserError) as exc:
            raise_for_graph_error({"error": {"code": 131047, "message": "Re-engagement"}}, 400)
        assert "Template" in str(exc.value)

    def test_auth_failure_carries_credential_annotations(self):
        """The annotated PermissionError is what produces the reconnect chip."""
        from nodes.whatsapp_business._base import raise_for_graph_error

        with pytest.raises(PermissionError) as exc:
            raise_for_graph_error({"error": {"code": 190, "message": "expired"}}, 401)
        assert exc.value.provider == "whatsapp_business"
        assert exc.value.auth == "api_key"


class TestGraphVersionPin:
    def test_version_is_pinned_not_derived(self):
        """An expired Graph version does not error -- Meta silently falls
        through to the next oldest, so the pin must be explicit."""
        from nodes.whatsapp_business._base import GRAPH_API_VERSION

        assert GRAPH_API_VERSION.startswith("v")
        assert GRAPH_API_VERSION[1:].replace(".", "").isdigit()


# ==========================================================================
# Trigger — the deployed path
# ==========================================================================
#
# Every failure covered here is invisible on the canvas Run path. That is the
# whole reason they are asserted: pressing Run exercises event_waiter, while
# deploy goes through dispatch.emit -> Temporal Visibility -> a listener whose
# EventType Search Attribute has to match the envelope exactly.


class TestTriggerIsDeployable:
    def test_trigger_types_are_registered_for_deployment(self):
        """find_trigger_nodes filters on this set. Omission means deploy
        silently ignores the node -- no listener, no error."""
        from constants import WORKFLOW_TRIGGER_TYPES

        assert "whatsappBusinessReceive" in WORKFLOW_TRIGGER_TYPES
        assert "whatsappBusinessStatus" in WORKFLOW_TRIGGER_TYPES

    def test_canary_types_match_the_emitted_envelope(self):
        """A mismatch here is the silent killer: the Visibility query asks for
        one string, the listener advertises another, and no signal arrives."""
        from nodes.whatsapp_business._events import MESSAGE_RECEIVED_TYPE, STATUS_UPDATED_TYPE
        from services.deployment.canary_registry import cloudevent_type_for

        assert cloudevent_type_for("whatsappBusinessReceive") == MESSAGE_RECEIVED_TYPE
        assert cloudevent_type_for("whatsappBusinessStatus") == STATUS_UPDATED_TYPE

    def test_emitted_types_match_the_node_prefixes(self):
        from nodes.whatsapp_business._events import MESSAGE_RECEIVED_TYPE, STATUS_UPDATED_TYPE
        from nodes.whatsapp_business.whatsapp_business_receive import (
            WhatsAppBusinessReceiveNode,
            WhatsAppBusinessStatusNode,
        )

        assert MESSAGE_RECEIVED_TYPE.startswith(WhatsAppBusinessReceiveNode.event_type_prefix)
        assert STATUS_UPDATED_TYPE.startswith(WhatsAppBusinessStatusNode.event_type_prefix)

    def test_webhook_path_is_claimed(self):
        from services.events import WEBHOOK_SOURCES

        assert "whatsapp-business" in WEBHOOK_SOURCES

    def test_triggers_have_no_input_handles(self):
        from nodes.whatsapp_business.whatsapp_business_receive import (
            WhatsAppBusinessReceiveNode,
            WhatsAppBusinessStatusNode,
        )

        for node in (WhatsAppBusinessReceiveNode, WhatsAppBusinessStatusNode):
            assert not [h for h in node.handles if h["kind"] == "input"]


def _webhook_body(*, messages=None, statuses=None, entries=1):
    """Build a Meta webhook payload with a controllable nesting shape."""
    value = {
        "messaging_product": "whatsapp",
        "metadata": {"display_phone_number": "15550783881", "phone_number_id": "PN1"},
    }
    if messages is not None:
        value["contacts"] = [{"profile": {"name": "Sheena"}, "wa_id": "16505551234"}]
        value["messages"] = messages
    if statuses is not None:
        value["statuses"] = statuses
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "WABA1", "changes": [{"value": value, "field": "messages"}]} for _ in range(entries)],
    }


def _text_message(mid: str):
    return {
        "from": "16505551234",
        "id": mid,
        "timestamp": "1749416383",
        "type": "text",
        "text": {"body": "Does it come in another color?"},
    }


class TestThreeLevelFanOut:
    """Reading only entry[0].changes[0] is the documented common bug."""

    def test_every_entry_and_message_produces_an_event(self):
        from nodes.whatsapp_business._source import iter_events

        body = _webhook_body(messages=[_text_message("wamid.A"), _text_message("wamid.B")], entries=2)
        events = iter_events(body)

        assert len(events) == 4
        assert {event_id for _, _, event_id in events} == {"wamid.A", "wamid.B"}

    def test_messages_and_statuses_in_one_payload_both_surface(self):
        from nodes.whatsapp_business._source import iter_events

        body = _webhook_body(
            messages=[_text_message("wamid.A")],
            statuses=[{"id": "wamid.OUT", "status": "delivered", "recipient_id": "165"}],
        )
        kinds = [kind for kind, _, _ in iter_events(body)]
        assert kinds == ["message", "status"]

    def test_message_without_an_id_is_dropped(self):
        """A minted id would defeat replay dedup across Meta's 7-day retries."""
        from nodes.whatsapp_business._source import iter_events

        body = _webhook_body(messages=[{"from": "165", "type": "text", "text": {"body": "x"}}])
        assert iter_events(body) == []

    def test_empty_payload_yields_nothing(self):
        from nodes.whatsapp_business._source import iter_events

        assert iter_events({"entry": []}) == []


class TestStatusDedupKey:
    def test_status_ids_are_composite_not_bare_wamids(self):
        """The same wamid reports sent -> delivered -> read. Deduping on the
        wamid alone would collapse the lifecycle into one event and the
        listener would drop two of the three."""
        from nodes.whatsapp_business._source import iter_events

        body = _webhook_body(
            statuses=[
                {"id": "wamid.OUT", "status": "sent", "recipient_id": "165"},
                {"id": "wamid.OUT", "status": "delivered", "recipient_id": "165"},
                {"id": "wamid.OUT", "status": "read", "recipient_id": "165"},
            ]
        )
        ids = [event_id for _, _, event_id in iter_events(body)]
        assert ids == ["wamid.OUT:sent", "wamid.OUT:delivered", "wamid.OUT:read"]
        assert len(set(ids)) == 3


class TestMessageShaping:
    def test_output_is_flat_for_template_resolution(self):
        """Deployed, event["data"] IS the trigger output and {{trigger.field}}
        resolves against its top level."""
        from nodes.whatsapp_business._source import iter_events

        (_, data, _), = iter_events(_webhook_body(messages=[_text_message("wamid.A")]))
        assert data["message_id"] == "wamid.A"
        assert data["from"] == "16505551234"
        assert data["text"] == "Does it come in another color?"
        assert data["profile_name"] == "Sheena"
        assert data["phone_number_id"] == "PN1"

    def test_media_carries_an_id_and_never_bytes(self):
        from nodes.whatsapp_business._source import iter_events

        message = {
            "from": "165",
            "id": "wamid.IMG",
            "type": "image",
            "image": {"id": "MEDIA123", "mime_type": "image/jpeg", "sha256": "abc", "caption": "look"},
        }
        (_, data, _), = iter_events(_webhook_body(messages=[message]))
        assert data["media"]["id"] == "MEDIA123"
        assert data["text"] == "look"
        serialized = str(data)
        assert "base64" not in serialized and "data:" not in serialized

    def test_interactive_reply_reads_as_text(self):
        """A button tap is the user speaking; downstream should not have to
        branch on message type to read it."""
        from nodes.whatsapp_business._source import iter_events

        message = {
            "from": "165",
            "id": "wamid.INT",
            "type": "interactive",
            "interactive": {"type": "button_reply", "button_reply": {"id": "cancel", "title": "Cancel"}},
        }
        (_, data, _), = iter_events(_webhook_body(messages=[message]))
        assert data["text"] == "Cancel"
        assert data["interactive_reply"]["id"] == "cancel"

    def test_status_exposes_the_window_expiry(self):
        from nodes.whatsapp_business._source import iter_events

        status = {
            "id": "wamid.OUT",
            "status": "sent",
            "recipient_id": "165",
            "conversation": {"id": "c1", "expiration_timestamp": "1750116480", "origin": {"type": "marketing"}},
            "pricing": {"billable": True, "pricing_model": "PMP", "category": "marketing"},
        }
        (_, data, _), = iter_events(_webhook_body(statuses=[status]))
        assert data["conversation_expires_at"] == "1750116480"
        assert data["pricing_model"] == "PMP"


class TestSubscriptionHandshake:
    def _request(self, **params):
        return SimpleNamespace(method="GET", headers={}, query_params=params)

    def _source(self, verify_token):
        from nodes.whatsapp_business._source import WhatsAppBusinessWebhookSource

        class _Cred:
            @classmethod
            async def resolve(cls):
                if verify_token is None:
                    raise PermissionError
                return {"whatsapp_business_verify_token": verify_token}

        source = WhatsAppBusinessWebhookSource()
        source.credential = _Cred
        return source

    def test_correct_token_echoes_the_bare_challenge(self):
        """Meta rejects a JSON envelope here -- it wants the raw value."""
        source = self._source("s3cret")
        resp = _run(source.handle_get(self._request(**{"hub.mode": "subscribe", "hub.verify_token": "s3cret", "hub.challenge": "1158201444"})))
        assert resp.status_code == 200
        assert resp.body == b"1158201444"
        assert resp.media_type == "text/plain"

    def test_wrong_token_is_refused(self):
        source = self._source("s3cret")
        resp = _run(source.handle_get(self._request(**{"hub.verify_token": "guess", "hub.challenge": "123"})))
        assert resp.status_code == 403

    def test_unconfigured_token_refuses_rather_than_accepting(self):
        source = self._source(None)
        resp = _run(source.handle_get(self._request(**{"hub.verify_token": "anything", "hub.challenge": "123"})))
        assert resp.status_code == 403

    def test_non_handshake_get_falls_through(self):
        source = self._source("s3cret")
        assert _run(source.handle_get(self._request())) is None


class TestSourceReachesDeployedListeners:
    """The single highest-value test in this file.

    WebhookSource.handle only calls event_waiter.dispatch, which serves the
    canvas Run path. Deployed triggers are Temporal listeners reached solely
    by services.events.dispatch.emit. A source that never calls emit works
    perfectly when you press Run and does nothing once deployed -- with no
    error anywhere.
    """

    def test_shape_emits_one_event_per_message(self):
        from nodes.whatsapp_business._source import WhatsAppBusinessWebhookSource

        source = WhatsAppBusinessWebhookSource()
        body = _webhook_body(messages=[_text_message("wamid.A"), _text_message("wamid.B")])

        with patch("services.events.dispatch.emit", new=AsyncMock()) as emit:
            _run(source.shape(SimpleNamespace(), b"{}", body))

        assert emit.await_count == 2
        emitted = [call.args[0] for call in emit.await_args_list]
        assert {ev.id for ev in emitted} == {"wamid.A", "wamid.B"}
        assert {ev.type for ev in emitted} == {"com.opencompany.whatsapp_business.message.received"}

    def test_statuses_emit_under_their_own_type(self):
        """Distinct types are the only discriminator that works deployed."""
        from nodes.whatsapp_business._source import WhatsAppBusinessWebhookSource

        source = WhatsAppBusinessWebhookSource()
        body = _webhook_body(statuses=[{"id": "wamid.OUT", "status": "failed", "recipient_id": "165"}])

        with patch("services.events.dispatch.emit", new=AsyncMock()) as emit:
            _run(source.shape(SimpleNamespace(), b"{}", body))

        assert emit.await_count == 1
        event = emit.await_args_list[0].args[0]
        assert event.type == "com.opencompany.whatsapp_business.status.updated"
        assert event.id == "wamid.OUT:failed"

    def test_emitted_data_is_a_flat_dict(self):
        """A non-dict is coerced to {} upstream, silently emptying the trigger."""
        from nodes.whatsapp_business._source import WhatsAppBusinessWebhookSource

        source = WhatsAppBusinessWebhookSource()
        with patch("services.events.dispatch.emit", new=AsyncMock()) as emit:
            _run(source.shape(SimpleNamespace(), b"{}", _webhook_body(messages=[_text_message("wamid.A")])))

        data = emit.await_args_list[0].args[0].data
        assert isinstance(data, dict)
        assert data["message_id"] == "wamid.A"

    def test_envelope_carries_no_workflow_id(self):
        """Setting it would scope delivery to one deployment; webhook events
        are meant to reach every deployment carrying the trigger."""
        from nodes.whatsapp_business._source import WhatsAppBusinessWebhookSource

        source = WhatsAppBusinessWebhookSource()
        with patch("services.events.dispatch.emit", new=AsyncMock()) as emit:
            _run(source.shape(SimpleNamespace(), b"{}", _webhook_body(messages=[_text_message("wamid.A")])))

        assert emit.await_args_list[0].args[0].workflow_id is None
