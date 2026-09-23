"""`wynd-kube` console script (`$DRAFTS/08 §5.11`): `render install|release`, `env-template`, `checkout`, `fire`.
Commands are added by the KUBE unit; the root callback keeps `wynd-kube --help` working until then."""

from __future__ import annotations

import typer

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main() -> None:
    """Wynd on Kubernetes: render install and release manifests, check out the workspace repo, fire triggers."""
