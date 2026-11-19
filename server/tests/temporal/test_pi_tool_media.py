"""New Pi Read image references survive the durable tool-message boundary."""

from services.llm.protocol import message_from_wire
from services.temporal.agent_workflow import _append_tool_result_message


def test_pi_read_media_becomes_ref_only_tool_image_block():
    reference = {"kind": "image", "path": "media/pi.png", "workflow_id": "wf", "mime_type": "image/png"}
    messages = []
    _append_tool_result_message(messages, content='{"image":"media/pi.png"}', tool_call_id="read-1", name="pi_read",
        media_result={"success": True, "result": {"llm_media": [{"ref": reference, "detail": "auto"}]}})
    message = message_from_wire(messages[0])
    assert message.tool_call_id == "read-1"
    image = next(block for block in message.blocks if block.type == "image")
    assert image.source == {"kind": "file_ref", "ref": reference, "detail": "auto"}
    assert not any("data" in (block.source or {}) for block in message.blocks)


def test_legacy_tool_message_shape_is_unchanged_without_media():
    from services.temporal.agent_workflow import _native_message

    messages = []
    _append_tool_result_message(messages, content="old result", tool_call_id="old-1", name="file_read")
    assert messages == [_native_message(role="tool", content="old result", tool_call_id="old-1", name="file_read")]
