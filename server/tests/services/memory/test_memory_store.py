from services import memory_store
from services.llm.protocol import Message


def test_native_messages_accept_legacy_and_canonical_roles():
    memory_store._sessions.clear()
    try:
        memory_store.add_message("roles", "human", "legacy user")
        memory_store.add_message("roles", "ai", "legacy assistant")
        memory_store.add_message("roles", "user", "native user")
        memory_store.add_message("roles", "assistant", "native assistant")

        messages = memory_store.get_native_messages("roles")

        assert all(isinstance(message, Message) for message in messages)
        assert [(message.role, message.content) for message in messages] == [
            ("user", "legacy user"),
            ("assistant", "legacy assistant"),
            ("user", "native user"),
            ("assistant", "native assistant"),
        ]
        wire_messages = memory_store.get_messages("roles")
        assert [message["role"] for message in wire_messages] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]
        assert all(message["timestamp"] for message in wire_messages)
    finally:
        memory_store._sessions.clear()
