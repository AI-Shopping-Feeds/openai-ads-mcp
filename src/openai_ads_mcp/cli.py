"""CLI entrypoint exposed as the `openai-ads-mcp` console script.

Subcommands:

* `openai-ads-mcp run`       (default) - start the MCP server.
* `openai-ads-mcp auth`      - store an Ads API key (checked against the API) in a 0600 file.
* `openai-ads-mcp purge`     - delete the audit log, the stored key and saved credentials.
* `openai-ads-mcp describe`  - print the effective configuration (no secrets).
* `openai-ads-mcp version`   - print the package version.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from . import __version__
from ._logging import configure_default, get_logger
from .audit import AuditLog
from .auth import ApiKeyAuth, redact, register_secret, save_key_file
from .client import OpenAIAdsAPIError, OpenAIAdsClient
from .config import (
    LOOPBACK_HOSTS,
    TRANSPORTS,
    audit_log_path,
    check_account_id,
    created_secrets_dir,
    key_file_path,
    load_config,
    load_env_files,
)
from .server import build_server

logger = get_logger("cli")


@click.group(invoke_without_command=True)
@click.option(
    "--env",
    "env_file",
    type=click.Path(dir_okay=False, path_type=Path),
    help="Path to a .env file. Defaults to ./.env (then ~/.openai-ads-mcp/.env).",
)
@click.option(
    "--transport",
    type=click.Choice(TRANSPORTS),
    default=None,
    help="Override OPENAI_ADS_TRANSPORT. 'sse' is deprecated; use 'streamable-http'.",
)
@click.option(
    "--host", default=None, help="Bind host for streamable-http / sse (default 127.0.0.1)."
)
@click.option(
    "--port", type=int, default=None, help="Bind port for streamable-http / sse (default 8000)."
)
@click.option(
    "--allow-remote",
    is_flag=True,
    help="Allow binding HTTP transports to a non-loopback host (same as OPENAI_ADS_ALLOW_REMOTE).",
)
@click.pass_context
def cli(
    ctx: click.Context,
    env_file: Path | None,
    transport: str | None,
    host: str | None,
    port: int | None,
    allow_remote: bool,
) -> None:
    """OpenAI Ads MCP server (unofficial; not affiliated with OpenAI)."""
    ctx.ensure_object(dict)
    ctx.obj["env_file"] = env_file
    ctx.obj["transport"] = transport
    ctx.obj["host"] = host
    ctx.obj["port"] = port
    ctx.obj["allow_remote"] = allow_remote
    if ctx.invoked_subcommand is None:
        ctx.invoke(run)


def check_bind(transport: str, host: str, allow_remote: bool) -> None:
    """Refuse to serve HTTP on a non-loopback address unless explicitly allowed."""
    if transport == "stdio" or host in LOOPBACK_HOSTS:
        return
    if not allow_remote:
        raise click.ClickException(
            f"Refusing to bind {transport} to {host}: the server has no authentication, so "
            "anyone who can reach it could use your OpenAI Ads key and spend your budget. "
            "Use 127.0.0.1, or set OPENAI_ADS_ALLOW_REMOTE=true (or --allow-remote) if you "
            "have put your own authentication in front of it."
        )
    logger.warning(
        "binding to %s: the server has no authentication, so anyone who can reach this "
        "address can use your OpenAI Ads key",
        host,
    )


@cli.command()
@click.pass_context
def run(ctx: click.Context) -> None:
    """Start the MCP server (default subcommand)."""
    configure_default()
    config = load_config(env_file=ctx.obj.get("env_file"))
    if ctx.obj.get("transport"):
        config.transport = ctx.obj["transport"]
    if ctx.obj.get("host"):
        config.host = ctx.obj["host"]
    if ctx.obj.get("port"):
        config.port = ctx.obj["port"]
    if ctx.obj.get("allow_remote"):
        config.allow_remote = True
    check_bind(config.transport, config.host, config.allow_remote)

    mcp = build_server(config=config)

    if config.transport == "stdio":
        mcp.run("stdio")
    elif config.transport == "streamable-http":
        logger.info("serving streamable HTTP on http://%s:%d/mcp", config.host, config.port)
        mcp.run("streamable-http", host=config.host, port=config.port)
    elif config.transport == "sse":
        logger.warning(
            "the sse transport (HTTP+SSE) is deprecated by the MCP spec; "
            "use --transport streamable-http instead"
        )
        mcp.run("sse", host=config.host, port=config.port)
    else:
        raise click.ClickException(f"Unknown transport: {config.transport}")


@cli.command()
@click.option(
    "--key-stdin",
    is_flag=True,
    help="Read the key from standard input instead of a hidden prompt.",
)
@click.option(
    "--ad-account-id",
    default=None,
    help="Only for partner keys that reach several accounts: the account to use.",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Where to save the key (default OPENAI_ADS_KEY_FILE or "
    "~/.openai-ads-mcp/secrets/openai-ads-key.json).",
)
@click.option("--no-verify", is_flag=True, help="Save without checking the key against the API.")
@click.pass_context
def auth(
    ctx: click.Context,
    key_stdin: bool,
    ad_account_id: str | None,
    output_path: Path | None,
    no_verify: bool,
) -> None:
    """Store an Ads API key for this server, after checking it works.

    Create the key in Ads Manager (ads.openai.com) -> Settings -> General -> API keys.
    It is an Ads API key, not an OpenAI platform API key. The key is saved with
    owner-only permissions and never printed.
    """
    configure_default()
    load_env_files(ctx.obj.get("env_file"))
    if key_stdin:
        key = sys.stdin.readline().strip()
    else:
        key = click.prompt("Paste the Ads API key (input hidden)", hide_input=True).strip()
    if not key or any(c.isspace() for c in key):
        raise click.ClickException("That doesn't look like a key (empty or contains spaces).")
    register_secret(key)
    account_id = check_account_id(ad_account_id, "--ad-account-id")
    target = output_path or key_file_path()

    if not no_verify:
        config = load_config(env_file=ctx.obj.get("env_file"), require_key=False)
        config.api_key = key
        config.ad_account_id = account_id
        config.dry_run = False
        audit = AuditLog(config.audit_log, retention_days=config.audit_retention_days)
        with OpenAIAdsClient(config, ApiKeyAuth(key, account_id), audit) as client:
            try:
                account = client.request("GET", "/ad_account")
            except OpenAIAdsAPIError as e:
                raise click.ClickException(
                    f"The key didn't work: {redact(str(e))}\nNothing was saved."
                ) from e
        if isinstance(account, dict):
            click.echo(
                "Key works for ad account "
                f"{account.get('id')} ({account.get('brand_name') or account.get('name')}), "
                f"currency {account.get('currency_code')}, timezone {account.get('timezone')}."
            )

    save_key_file(target, key, account_id)
    click.echo(f"Saved the key to {target} (owner-only permissions).")
    if target.resolve() != key_file_path().resolve():
        click.echo(f"Set OPENAI_ADS_KEY_FILE={target.resolve()} so the server finds it.")
    click.echo(
        "Next: run `openai-ads-mcp describe`, then add the server to your MCP client. "
        "Never paste the key into chat or commit it."
    )


@cli.command()
@click.option("--yes", is_flag=True, help="Don't ask for confirmation.")
@click.option(
    "--keep-secrets",
    is_flag=True,
    help="Delete only the audit log; keep the key file and saved credentials.",
)
@click.pass_context
def purge(ctx: click.Context, yes: bool, keep_secrets: bool) -> None:
    """Delete local OpenAI Ads data: the audit log, the stored key and saved credentials."""
    load_env_files(ctx.obj.get("env_file"))
    audit = audit_log_path()
    targets = [audit, audit.with_suffix(audit.suffix + ".tmp")]
    created = created_secrets_dir()
    if not keep_secrets:
        targets.append(key_file_path())
        if created.is_dir():
            targets.extend(sorted(p for p in created.iterdir() if p.is_file()))
    existing = [p for p in targets if p.exists()]
    if not existing:
        click.echo("Nothing to delete.")
        return
    click.echo("Will delete:\n" + "\n".join(f"  {p}" for p in existing))
    if not keep_secrets and created.is_dir() and any(created.iterdir()):
        click.echo(
            "Files under secrets/created/ may be the only copy of a Conversions API key, SFTP "
            "password or signing secret. Move any you still need first."
        )
    if not yes and not click.confirm("Delete these files?", default=False):
        click.echo("Cancelled.")
        return
    for p in existing:
        p.unlink()
    click.echo(f"Deleted {len(existing)} file(s).")
    if not keep_secrets:
        click.echo(
            "If you also set OPENAI_ADS_API_KEY in a client config, remove it there, and "
            "revoke the key in Ads Manager (Settings -> API keys) if you no longer use it."
        )


@cli.command()
def version() -> None:
    """Print the openai-ads-mcp version."""
    click.echo(__version__)


@cli.command("describe")
@click.pass_context
def describe(ctx: click.Context) -> None:
    """Print effective config (paths, account selection, limits) and exit. No secrets."""
    configure_default()
    config = load_config(env_file=ctx.obj.get("env_file"), require_key=False)
    info = {
        "version": __version__,
        "api_base": config.api_base,
        "api_key": (
            f"set (...{config.api_key[-4:]})"
            if config.api_key
            else "missing (run `openai-ads-mcp auth`)"
        ),
        "key_file": str(config.key_file),
        "ad_account_id": config.ad_account_id or "the key's own account",
        "audit_log": str(config.audit_log),
        "audit_retention_days": config.audit_retention_days,
        "secrets_dir": str(config.secrets_dir),
        "upload_dir": str(config.upload_dir) if config.upload_dir else None,
        "dry_run": config.dry_run,
        "read_only": config.read_only,
        "tool_groups": config.tool_groups or "all",
        "bulk_threshold": config.bulk_threshold,
        "max_increase_pct": config.max_increase_pct,
        "max_daily_budget": config.max_daily_budget,
        "max_lifetime_budget": config.max_lifetime_budget,
        "max_bid": config.max_bid,
        "transport": config.transport,
        "host": config.host,
        "port": config.port,
        "allow_remote": config.allow_remote,
    }
    click.echo(json.dumps(info, indent=2, ensure_ascii=False))


def main() -> None:
    try:
        cli(obj={})
    except RuntimeError as e:
        # Surface config errors cleanly instead of a Python traceback.
        click.echo(f"Error: {redact(str(e))}", err=True)
        sys.exit(2)


if __name__ == "__main__":
    main()
