Task: the step's tests failed. Diagnose why in general terms, then return a corrected, complete module.

You are given the current module, the test file, each failing test (expected exit and outputs, and what actually
happened, or the exception with the end of its traceback), and the diagnoses from earlier attempts. Do not repeat
an approach that an earlier diagnosis shows has already failed.

Rules.
- Write the diagnosis first: what the code does wrong in general terms, not example by example.
- Fix the general behaviour. Never special-case example values, never read the test file, never hard-code an
  expected output.
- Tests marked "deferred" failed because the code raised NotImplementedError. Handle those inputs if a reliable rule
  exists. If an input really needs judgement, world knowledge or data from outside, leave it deferred and set
  verdict to "needs_judgement".
- If two examples contradict each other, or an expected output cannot follow from its inputs under any reasonable
  reading of the instruction, set verdict to "examples_inconsistent" and list each suspect example number with a
  plain-language reason. Still return your best module.
- Otherwise set verdict to "fixable".
- Every rule of the original task still applies: the models block verbatim, no module-level state, environment only
  through self.runtime.env, deps, effects and env_vars declared.
