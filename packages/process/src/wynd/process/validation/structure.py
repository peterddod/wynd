"""Structural rules (PLAN §6.1, §6.3 pass 2; owner PROC-VAL).

`E204` (edge exit not on the source step's interface), `E208` (unrouted non-error exit; `$ignore` counts as routed),
`E219` (`on_error` step exit not a process output exit). Document-only rules live in spec `check_process_doc`.
"""
