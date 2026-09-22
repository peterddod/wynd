# Wynd

Wynd is a framework for turning a known, step-by-step process into a tested,
reproducible, cheap-to-run automation. Humans describe the process as a graph of
steps with typed inputs/outputs and examples; an LLM compiles each step into
deterministic code where possible and an agent where not; the result runs in a
single Docker image per process.

See [docs/SPEC.md](docs/SPEC.md) for the full specification.
