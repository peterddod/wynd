Task: write the complete Python module for a ShellStep named {class_name} that runs the program "{program}" and
makes the given test file pass.

Rules.
- Define exactly one step class, {class_name}(ShellStep), containing the models block copied verbatim.
- Implement command(self, input: Input) -> list[str], returning the argument vector. Never build a shell string,
  never invoke a shell, and never let an input value become more than one argument.
- Set the class attribute exit_codes to exactly {exit_codes}. Keys are process exit codes and "*" is the fallback.
- Implement outputs(self, exit: str, result: ShellResult) -> Output, which turns result.stdout, result.stderr and
  result.returncode into the model for that exit.
- List the Debian packages that provide the program in system_packages (for example "poppler-utils" for pdftotext).
  Always include "shell" in effects, and add "filesystem" or "network" if the command touches files named by the
  inputs or the network.
- The rules on module-level state, environment variables and readability from deterministic steps apply here too.
- In notes, explain in one or two plain sentences what the command does.
