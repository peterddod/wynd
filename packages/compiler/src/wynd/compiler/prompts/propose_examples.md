Task: propose edge-case examples for one step. The process owner will confirm, correct or reject each one, and
confirmed examples become permanent tests. The owner's examples usually show the common case. Your proposals should
probe the boundaries where an implementation is most likely to be wrong, or to overfit to the few examples it has.

Rules.
- Propose at most {max_proposals}, most valuable first. Propose fewer, or none, if the existing examples already
  cover the boundaries that matter.
- Each proposal has: a single question in plain language, as you would ask the owner ("What should happen if the
  invoice has no due date?"); the complete example you think is the most likely correct answer; and a one-sentence
  rationale.
- Good edge cases: a missing or empty value; boundary numbers (zero, negative, very large); the same thing written
  in a different format (dates, thousands separators, currency symbols, upper and lower case); an input that
  belongs to a different exit; a near-miss that looks valid but is not.
- Every value must match the schema. The outputs must contain every field of the chosen exit; an exit with no
  fields has empty outputs. Use the exit "error" only for inputs where the step cannot produce any declared exit,
  such as a file that does not exist.
- For path inputs you cannot create files. Use only the fixture files listed, or a path that does not exist when
  the point of the example is a missing file.
- Do not repeat an existing example, and do not propose anything the owner has already rejected (listed below).
- Write inputs_json and outputs_json as JSON objects serialised to strings, for example "{{\"total\": 0}}".
