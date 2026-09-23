"""Release and trigger-fire documents in the controller `DocStore` (PLAN §8.1 releases row, §3.24;
`$DRAFTS/06 §10.2`). Stub; CTL-REL.

Stored at `.wynd/controller/releases/<id>.json` and `.wynd/controller/fires/<id>.json`. Fire records carry
`started_at`/`finished_at` (SPEC §15).
"""

RELEASES = "releases"
FIRES = "fires"
