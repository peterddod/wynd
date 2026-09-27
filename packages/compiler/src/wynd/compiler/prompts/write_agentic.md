Task: write the instruction (the class docstring) and choose the capabilities for an AgenticStep. At run time
{tier_words} receives your docstring as its only instructions, the step's input as JSON, and any context you
declare, and must answer with output that validates against one of the exit models. It never sees the examples,
this conversation or the process owner.

The docstring.
- Write it for a capable but literal reader who knows nothing about this business. Start with one sentence stating
  the goal. Then say when to choose each exit, using the exit names exactly. Then say how to fill each output field:
  formats, units, normalisation (ISO dates, currency codes, number formats). Then say what to do when information is
  missing, ambiguous or contradictory.
- Derive general rules from the examples. Never copy example inputs or outputs into the docstring, and never
  mention examples or tests.
- Aim for fewer than 250 words: plain text, short paragraphs and hyphen lists.
{split_note}
Capabilities. Most steps need none. When everything needed is in the input, leave tools, mcp and tool_methods empty.
- When a capability is needed, prefer the built-in tools listed. Write a bespoke @tool method only for logic
  specific to this step. Use an MCP server from the registry only when neither covers the need, and list in allow
  only the tool names you need.
- A bespoke tool is an ordinary method with type hints and a one-line docstring, decorated
  @tool(effects=[...], idempotent=..., env=[...]). Put its source, indented by four spaces as it will appear in the
  class body, in tool_methods. idempotent is true only if calling it twice is harmless.
- context lists extra trace context the model should see. Choose only from: "process.goal", "previous.summary",
  "previous.outputs", and "steps.<node>.outputs" for the upstream nodes listed. Use ["process.goal"] when a process
  goal exists, and request more only when the instruction needs information that is not in the input.
- List any environment variables the tools read in env_vars, and any packages the tool methods import in deps.

If a previous docstring is given, keep what is still right. If user guidance is given, follow it.
In notes, explain in one or two plain sentences why this step needs a model.
