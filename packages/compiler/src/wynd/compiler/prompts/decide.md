Task: for each example of one step, decide what an implementation needs in order to produce the expected result.
Also decide whether the step is a shell command.

Classify each example as exactly one of:
- pure: the result follows from the inputs by rules a programmer could write down and trust on unseen inputs of
  the same kind (parsing a fixed format, arithmetic, lookups in fixed tables, formatting, validation, fixed decision
  rules), using the Python standard library or a well-known package.
- judgement: it requires reading comprehension, classifying free-form text, or tolerating phrasing that written
  rules would not reliably capture.
- world_knowledge: it requires facts that are not in the inputs.
- external_data: it requires fetching information at run time (a web page, an API, a database).

Be realistic rather than optimistic. Choose pure only when you could write the rule and expect it to generalise
beyond these examples. Free-form text written by different people usually needs judgement. Machine-generated or
templated text (logs, CSV, exports, PDFs produced by one system, fixed forms) is usually pure.

Set command to true only when the instruction clearly asks to run a named command-line program, or clearly
describes the step as running a command. Then put the program's name in "program". Otherwise set command to false
and program to "".

In "summary", explain the classification to the process owner in two or three plain sentences.
