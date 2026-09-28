"""`wynd mcp|provider|registry add|list|remove` (PLAN §9, §3.22, §3.14; `$DRAFTS/06 §9.2`).

`wynd mcp add github --url U --auth-env GITHUB_TOKEN` stores `{transport: http, url: U, headers: {"Authorization":
"Bearer ${env:GITHUB_TOKEN}"}, auth_env: [GITHUB_TOKEN]}`; `--header K=V` sets (or overrides) a header; `--command
"…"` makes a stdio server (shlex-split argv; `--auth-env X` passes `X` into its env). `remove` removes the entry even
when processes still reference it and warns about them. Changing a provider's tier models changes cassette keys.
"""

from __future__ import annotations

import shlex
from typing import Any

import typer

from wynd.cli import context
from wynd.cli.output import echo, err, print_json, table
from wynd.controller.errors import Invalid, NotFound, Unavailable

JSON = typer.Option(False, "--json", help="Print one JSON document on stdout.")

mcp_app = typer.Typer(no_args_is_help=True, help="MCP servers in the user registry.")
provider_app = typer.Typer(no_args_is_help=True, help="Model providers and their tier models.")
registry_app = typer.Typer(no_args_is_help=True, help="Image registries builds can push to.")


def register(app: typer.Typer) -> None:
    app.add_typer(mcp_app, name="mcp")
    app.add_typer(provider_app, name="provider")
    app.add_typer(registry_app, name="registry")


# --- MCP servers ------------------------------------------------------------------------------------------------------

@mcp_app.command("add")
def mcp_add(
    ctx: typer.Context,
    name: str = typer.Argument(...),
    url: str | None = typer.Option(None, "--url", help="Streamable HTTP endpoint."),
    command: str | None = typer.Option(None, "--command", help="stdio server command line."),
    auth_env: str | None = typer.Option(None, "--auth-env", metavar="VAR", help="Env var holding the token."),
    headers: list[str] | None = typer.Option(None, "--header", metavar="NAME=VALUE", help="HTTP header (repeatable)."),
    description: str = typer.Option("", "--description"),
    json_output: bool = JSON,
) -> None:
    """Add or replace an MCP server."""
    if (url is None) == (command is None):
        raise Invalid("give exactly one of --url (http server) or --command (stdio server)")
    if headers and command is not None:
        raise Invalid("--header applies to --url servers only")
    entry_headers: dict[str, str] = {}
    entry_env: dict[str, str] = {}
    if auth_env and url is not None:
        entry_headers["Authorization"] = f"Bearer ${{env:{auth_env}}}"
    if auth_env and command is not None:
        entry_env[auth_env] = f"${{env:{auth_env}}}"
    for pair in headers or []:
        key, value = _pair(pair, "--header", "NAME=VALUE")
        entry_headers[key] = value
    entry = _build(_entry_model("McpServerEntry"), name=name, transport="stdio" if command is not None else "http",
                   url=url, headers=entry_headers, command=shlex.split(command) if command is not None else None,
                   env=entry_env, auth_env=[auth_env] if auth_env else [], description=description)
    stored = context.get_controller(ctx).registries.add_mcp(entry)
    if json_output:
        print_json(stored)
        return
    echo(f"added MCP server {stored.name} ({stored.transport} {_endpoint(stored)})")


@mcp_app.command("list")
def mcp_list(ctx: typer.Context, json_output: bool = JSON) -> None:
    """MCP servers, by name."""
    entries = context.get_controller(ctx).registries.list_mcp()
    if json_output:
        print_json(entries)
        return
    echo(table(["NAME", "TRANSPORT", "URL", "AUTH"],
               [[e.name, e.transport, _endpoint(e), _auth(e)] for e in entries]))


@mcp_app.command("remove")
def mcp_remove(ctx: typer.Context, name: str = typer.Argument(...)) -> None:
    """Remove an MCP server."""
    _removed(context.get_controller(ctx).registries.remove_mcp(name), f"MCP server {name!r}")
    echo(f"removed MCP server {name}")


# --- providers --------------------------------------------------------------------------------------------------------

@provider_app.command("add")
def provider_add(
    ctx: typer.Context,
    name: str = typer.Argument(...),
    tiers: list[str] | None = typer.Option(None, "--tier", metavar="TIER=MODEL", help="cheap|standard|strong model."),
    json_output: bool = JSON,
) -> None:
    """Set a provider's tier models (only the tiers given change)."""
    mapping = dict(_pair(pair, "--tier", "TIER=MODEL") for pair in tiers or [])
    info = context.get_controller(ctx).registries.add_provider(name, tiers=mapping)
    if json_output:
        print_json(info)
    else:
        echo(f"{info.name} ({info.kind}): " + ", ".join(f"{tier}={model}" for tier, model in info.tiers.items()))
    if mapping:
        err("note: tier models are part of cassette keys; re-record with `wynd test --live`")


@provider_app.command("list")
def provider_list(ctx: typer.Context, json_output: bool = JSON) -> None:
    """Installed providers with their effective tier models and readiness."""
    infos = context.get_controller(ctx).registries.list_providers()
    if json_output:
        print_json(infos)
        return
    rows = [[i.name, i.kind, i.tiers.get("cheap"), i.tiers.get("standard"), i.tiers.get("strong"),
             "yes" if i.ready else f"no ({i.message})"] for i in infos]
    echo(table(["NAME", "KIND", "CHEAP", "STANDARD", "STRONG", "READY"], rows))


@provider_app.command("remove")
def provider_remove(ctx: typer.Context, name: str = typer.Argument(...)) -> None:
    """Remove a provider's tier overrides (its defaults apply again)."""
    _removed(context.get_controller(ctx).registries.remove_provider(name), f"provider entry {name!r}")
    echo(f"removed provider entry {name}")


# --- image registries -------------------------------------------------------------------------------------------------

@registry_app.command("add")
def registry_add(
    ctx: typer.Context,
    name: str = typer.Argument(...),
    url: str = typer.Argument(..., help="e.g. localhost:5001/wynd or ghcr.io/acme"),
    username_env: str | None = typer.Option(None, "--username-env", metavar="VAR"),
    password_env: str | None = typer.Option(None, "--password-env", metavar="VAR"),
    insecure: bool = typer.Option(False, "--insecure", help="Plain HTTP registry."),
    default: bool = typer.Option(False, "--default", help="Push builds here unless told otherwise."),
    json_output: bool = JSON,
) -> None:
    """Add or replace an image registry."""
    entry = _build(_entry_model("ImageRegistryEntry"), name=name, url=url, username_env=username_env,
                   password_env=password_env, insecure=insecure, default=default)
    stored = context.get_controller(ctx).registries.add_image_registry(entry)
    if json_output:
        print_json(stored)
        return
    echo(f"added image registry {stored.name} ({stored.url}){' [default]' if stored.default else ''}")


@registry_app.command("list")
def registry_list(ctx: typer.Context, json_output: bool = JSON) -> None:
    """Image registries, by name."""
    entries = context.get_controller(ctx).registries.list_image_registries()
    if json_output:
        print_json(entries)
        return
    rows = [[e.name, e.url, "yes" if e.default else "-", "yes" if e.insecure else "-",
             " ".join(f"env:{v}" for v in (e.username_env, e.password_env) if v) or "-"] for e in entries]
    echo(table(["NAME", "URL", "DEFAULT", "INSECURE", "AUTH"], rows))


@registry_app.command("remove")
def registry_remove(ctx: typer.Context, name: str = typer.Argument(...)) -> None:
    """Remove an image registry."""
    _removed(context.get_controller(ctx).registries.remove_image_registry(name), f"image registry {name!r}")
    echo(f"removed image registry {name}")


def _entry_model(name: str) -> type:
    """`McpServerEntry` / `ImageRegistryEntry`, as `wynd.controller.models` re-exports them (PLAN §3.14).

    wynd-controller is an unpinned dependency, so one without the re-export is `Unavailable` (exit 3), not a traceback.
    """
    from wynd.controller import models

    model = getattr(models, name, None)
    if model is None:
        raise Unavailable(f"the installed wynd-controller does not export wynd.controller.models.{name}; "
                          "upgrade wynd-controller")
    return model


def _build(cls: type, **fields: Any) -> Any:
    """`cls(**fields)`; a validation error is `Invalid` (exit 2) with the validators' messages."""
    try:
        return cls(**fields)
    except ValueError as error:
        errors = getattr(error, "errors", None)
        if not callable(errors):
            raise Invalid(str(error)) from None
        raise Invalid("; ".join(e["msg"].removeprefix("Value error, ") for e in errors())) from None


def _pair(pair: str, option: str, form: str) -> tuple[str, str]:
    key, sep, value = pair.partition("=")
    if not sep or not key.strip():
        raise Invalid(f"{option} expects {form}, got {pair!r}")
    return key.strip(), value


def _removed(result: Any, what: str) -> None:
    if not result.removed:
        raise NotFound(f"no {what} in the user registry")
    if result.referenced_by:
        err(f"warning: still referenced by: {', '.join(result.referenced_by)}")


def _endpoint(entry: Any) -> str:
    return entry.url if entry.transport == "http" else shlex.join(entry.command or [])


def _auth(entry: Any) -> str:
    if entry.oauth:
        return "oauth"
    return ", ".join(f"env:{name}" for name in entry.auth_env) or "-"
