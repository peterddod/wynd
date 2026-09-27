Task: the AgenticStep's tests failed when the run-time model followed your docstring. Diagnose why in general terms
and return a revised docstring and capabilities.

You are given the current docstring and capabilities, the model tier, each failing test (the input, the expected
exit and outputs, and what the model actually returned, or the validation or tool error), and the diagnoses from
earlier attempts.

Rules.
- Diagnose first: which rule was missing, unclear or wrong, or which capability was missing.
- Revise the rules, not the examples. Never copy example values into the docstring.
- Keep the docstring short. Make each rule clearer instead of adding many special cases.
- If an expected output cannot follow from its input under any reasonable reading, set verdict to
  "examples_inconsistent" and list the suspect example numbers with reasons.
- If the docstring is already clear and correct and the model still fails, set verdict to "needs_stronger_model".
- Otherwise set verdict to "fixable".
- Every rule of the original task still applies.
