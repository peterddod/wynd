"""Chat system instruction and reply schema (PLAN §8.1 chat row; `$DRAFTS/06 §7.5`). Stub; CTL-CHAT.

`SYSTEM_INSTRUCTION` (about 120 lines: role, scope, format primer, editing discipline, reply) is written by CTL-CHAT.
"""

SYSTEM_INSTRUCTION = ""

CHAT_REPLY_SCHEMA: dict = {
    "type": "object",
    "properties": {"reply": {"type": "string"}},
    "required": ["reply"],
}
