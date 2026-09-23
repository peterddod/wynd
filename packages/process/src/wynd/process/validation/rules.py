"""Child, provider and package rules (PLAN §6.1, §6.3 pass 7; owner PROC-VAL).

`W203`–`W205` (child declares a different `env.base`/`provider`/`latency`), `W206` (agentic step using an
AgentProvider in a `latency: fast` root, closure-wide), `W207` (unknown provider), `W128`, `W129`, `I130`, `W202`,
`E128`, and runtime lint `L001`–`L005` (errors) over every non-test `.py` file of each compiled step package in the
closure (`wynd.runtime.lint.check_step_module`).
"""
