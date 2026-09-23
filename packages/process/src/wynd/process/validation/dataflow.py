"""Exit-aware dataflow (PLAN §6.1, §6.3 pass 6, §15 item 6; owner PROC-VAL).

Lattice: per step key, the set of possible latest exits plus `NOT_RUN`. Branch-level guard atoms parsed from `when:`
refine the state; fixpoint per `$DRAFTS/04 §4.8` gives `WHEN_STATE`/`WITH_STATE` per branch, and `FINAL_STATE`
(the join of every reachable IN/WITH state and every error-handler entry state, where any alias may also be
`NOT_RUN` or `error`) checks `finally[].with`. Produces the `TypedSite` list. No intra-expression refinement.
"""

from __future__ import annotations

from dataclasses import dataclass

NOT_RUN = ""                              # "has not run" member of a step's exit set; never an exit name


@dataclass(frozen=True)
class ExitIs:                             # steps.x.exit == "a" | "a" == steps.x.exit | steps.x.exit in ["a", "b"]
    step: str
    exits: frozenset[str]


@dataclass(frozen=True)
class ExitIsNot:                          # steps.x.exit != "a" | not (steps.x.exit == "a") | not (… in [...])
    step: str
    exits: frozenset[str]


@dataclass(frozen=True)
class HasRun:                             # steps.x.runs > 0 | >= 1 | != 0 | steps.x.exit != null
    step: str


@dataclass(frozen=True)
class NotRun:                             # steps.x.runs == 0 | steps.x.exit == null
    step: str
