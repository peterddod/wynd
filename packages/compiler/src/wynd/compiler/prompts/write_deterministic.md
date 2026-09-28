Task: write the complete Python module for a DeterministicStep named {class_name} that makes the given test file
pass. The tests were generated from the examples before any code existed and will not change.

Rules.
- Define exactly one step class, {class_name}(DeterministicStep), containing the models block copied verbatim and a
  method run(self, input: Input) -> Output that returns one of the exit models. Import what the models block needs.
- Write a general implementation of the instruction. Never special-case example values, embed the examples, read
  the test file, or branch on anything that exists only in the tests. The tests are a sample; production inputs
  will differ.
- When an input falls outside what your code can handle with confidence, raise NotImplementedError with a short
  reason instead of guessing. The runtime routes such a failure to a fallback. A wrong answer would go unnoticed.
- Prefer the standard library. If a third-party package is clearly the right tool (for example pypdf to read PDF
  files), import it and put a requirement such as "pypdf>=5" in deps. Never list wynd packages.
- Module level may contain only imports, constants named in UPPER_CASE bound to immutable values (str, int, float,
  bool, None, tuples, frozensets, re.compile(...)), functions and classes. Keep no state between runs; use
  self.runtime.cache when a cache is genuinely needed.
- Read configuration and secrets only with self.runtime.env("NAME"). List every such name in env_vars with a plain
  description. Never hard-code secrets.
- Write files only under self.runtime.workspace, unless the instruction says to write elsewhere. Declare
  "filesystem" in effects if the code reads or writes files named by its inputs or outside the workspace, "network"
  if it makes network calls, and "shell" if it runs subprocesses.
- Use pre() and post() only for per-run setup and teardown of things the runtime cannot see (temporary files, open
  sessions, subprocesses).
- Keep the code small, explicit and readable. A reviewer will read it in a git diff. Add a short comment only where
  the logic is not obvious.
- If a previous implementation is given, it may contain hand edits by a reviewer. Keep them wherever they are still
  consistent with the tests.
- If user guidance is given, follow it.
- In notes, explain in one or two plain sentences how the step works.
- Set system_packages to [] and requires_glibc to false unless a dependency genuinely needs them.
