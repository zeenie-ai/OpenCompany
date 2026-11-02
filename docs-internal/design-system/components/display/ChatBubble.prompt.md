> **Status: Prototype reference.** This is the bundled demo component contract. For production use the existing React primitives, live theme tokens and current frontend documentation; prototype props and visual restrictions do not override shipped behavior.

Chat message for the dock's Chat tab (talking to your agent).

```jsx
<ChatBubble role="user" time="12:03:58">Summarize my unread emails every weekday at 9 AM.</ChatBubble>
<ChatBubble role="agent">Done — I scheduled a daily digest. Want it on WhatsApp too?</ChatBubble>
```

- Agent voice: confirms outcome first, then one short follow-up question.
