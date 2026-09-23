"""Cycles and `max_traversals` (PLAN §6.1, §6.3 pass 4, §15 item 8; owner PROC-VAL).

Tarjan SCC over step keys; every branch whose endpoints share an SCC gets `max_traversals = DEFAULT_MAX_TRAVERSALS`
in the normalised definition only (never written to YAML), reported as `I201` per branch key.
"""
