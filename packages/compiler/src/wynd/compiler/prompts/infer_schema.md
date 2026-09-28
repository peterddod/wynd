Task: infer the input and output schema of one step from its instruction and examples. The schema will be shown to
the process owner in plain language. A baseline inferred mechanically from the example values is given: start from
it, and correct what values alone cannot show (paths, identifiers made of digits, optional fields, fields of exits
without examples).

Types. Use only: string, number, integer, boolean, date, datetime, path, object, list[<type>]. Append "?" to a type
when the field may be missing or null (for example "string?").
- number is for amounts, measurements and anything that may have a fractional part. integer is only for counts and
  whole-number quantities. An identifier made of digits (an invoice number, a postcode) is a string.
- date is for calendar dates, datetime only when a time of day appears. path is for values that name files or
  directories. object is for a nested record whose inner structure is not the point of this step. list[<type>] is
  for repeated values.

Rules.
- Declare exactly the exits listed, in the same order. An exit that returns nothing has an empty field list.
- Every input field must appear in at least one example's inputs. Every output field of an exit must appear in at
  least one example ending in that exit, unless a constraint below names it.
- The constraints from the process graph are fixed. Field names that edges already use must appear with exactly
  those names. The entry step's inputs must match the process inputs by name.
- If a previous interface is given, keep its field names and types wherever the examples still agree with them, so
  that the rest of the process keeps working. Change only what the examples now contradict.
- If the user has corrected a previous inference, apply the correction exactly.
- Mark an output field free_text only when its value is prose that a correct implementation could word differently
  (a note, a summary, a reason). Identifiers, codes, amounts, dates, names and categories are never free text.
- Give every field a short description in plain language, as the process owner would say it.
- If the examples contradict each other, or no single schema fits them, explain each problem in plain language in
  "problems" instead of guessing. Otherwise leave "problems" empty.
