"""Name-level binding rules (PLAN §6.1, §6.3 pass 5; owner PROC-VAL).

`E210` entry Input <-> `process.inputs`; `E211` `$exit` binds exactly the declared fields; `E212` `with:` keys within
the target Input and covering its required fields; `E215` finally inputs within `process.inputs ∪ {run_id, exit}`
(no `with`); `E223` every required Input field of the `on_error` step is a `ProcessError` field name.
"""
