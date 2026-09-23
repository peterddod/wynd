"""Chat documents in the controller `DocStore` (PLAN §8.1 chat row; `$DRAFTS/06 §7.2`, §10.2). Stub; CTL-CHAT.

`.wynd/controller/chats/<id>.json`: `{id, title, created_at, updated_at, job_id, items, next_seq, usage_totals}`;
items are the web `ChatItem` union; deltas are never persisted.
"""

CHATS = "chats"
