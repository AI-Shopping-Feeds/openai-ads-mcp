"""Build the MCP server. Stays import-light so tests can patch pieces individually."""

from __future__ import annotations

import functools
import inspect
import time
from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__, tools
from ._logging import configure_default, get_logger
from .audit import AuditLog
from .auth import load_auth, redact
from .client import OpenAIAdsAPIError, OpenAIAdsClient
from .config import Config, load_config
from .tool_catalog import ALWAYS_REGISTERED, TOOL_SPECS

logger = get_logger("server")

INSTRUCTIONS = """\
OpenAI Ads Advertiser API (ads in ChatGPT): account, campaigns, ad groups, ads, audiences, conversions, product feeds, insights.
Start with openai_ads_describe_server and openai_ads_health_check. Amounts in tool arguments are in the account currency; API fields ending _micros are millionths (1.00 = 1000000).
Writes change live ads and spend. Rehearse in dry-run (openai_ads_set_dry_run) and show the user what will change first. New campaigns, ad groups and ads start paused.
Archives/deletes, big batches, bid/budget/limit rises, activations and raw writes need confirm ('ARCHIVE', 'DELETE' or 'APPLY'). Every write is in the audit log (openai_ads_audit_tail).
Use OpenAI Ads reporting only to measure and improve these campaigns: never try to identify people, or build audiences about OpenAI users or brands, from it.
Tools can be limited with OPENAI_ADS_TOOL_GROUPS (module names) or OPENAI_ADS_READ_ONLY=true."""

# Errors whose message helps the agent fix the call (bad arguments, a refusal,
# OpenAI Ads API errors, permissions, missing files or config problems).
_EXPECTED_ERRORS = (
    ValueError,
    RuntimeError,
    PermissionError,
    FileNotFoundError,
    OpenAIAdsAPIError,
    httpx.HTTPError,
)


def _as_tool_error(fn):
    """Re-raises expected errors as ToolError so MCP SDK 2 shows their message.

    SDK 2 hides the text of any other exception from the client. Messages are
    redacted so keys and secrets never reach the client.
    """
    if inspect.iscoroutinefunction(fn):

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            try:
                return await fn(*args, **kwargs)
            except _EXPECTED_ERRORS as exc:
                raise ToolError(redact(str(exc))) from exc

        return async_wrapper

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except _EXPECTED_ERRORS as exc:
            raise ToolError(redact(str(exc))) from exc

    return wrapper


class OpenAIAdsMCPServer(MCPServer):
    """MCPServer whose tools report expected errors to the client.

    Each tool gets its title and annotations from `tool_catalog.TOOL_SPECS`
    unless the decorator passes them. With `read_only=True`, tools that are not
    annotated read-only are skipped (the meta tools are always registered).
    """

    def __init__(self, *args: Any, read_only: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.read_only = read_only

    def tool(  # type: ignore[override]
        self,
        name: str | None = None,
        title: str | None = None,
        description: str | None = None,
        annotations: ToolAnnotations | None = None,
        **kwargs: Any,
    ):
        def decorator(fn):
            tool_name = name or fn.__name__
            spec = TOOL_SPECS.get(tool_name)
            tool_title = title or (spec.title if spec else None)
            tool_annotations = annotations or (spec.annotations if spec else None)
            is_read_only = bool(tool_annotations and tool_annotations.read_only_hint)
            if self.read_only and not is_read_only and tool_name not in ALWAYS_REGISTERED:
                return fn
            register = super(OpenAIAdsMCPServer, self).tool(
                name=tool_name,
                title=tool_title,
                description=description,
                annotations=tool_annotations,
                **kwargs,
            )
            register(_as_tool_error(fn))
            return fn

        return decorator


def build_server(config: Config | None = None, env_file: Path | None = None) -> MCPServer:
    """Construct an MCPServer instance with all openai_ads_* tools registered."""
    configure_default()
    if config is None:
        config = load_config(env_file=env_file)
    groups = tools.resolve_tool_groups(config.tool_groups)  # fail fast on unknown groups

    auth = load_auth(config)
    audit = AuditLog(config.audit_log, retention_days=config.audit_retention_days)
    pruned = audit.prune()
    if pruned:
        logger.info(
            "pruned %d audit entries older than %d days", pruned, config.audit_retention_days
        )
    client = OpenAIAdsClient(config, auth, audit)

    logger.info(
        "starting openai-ads-mcp auth=%s account=%s dry_run=%s read_only=%s",
        auth.account_label,
        config.ad_account_id or "key's account",
        config.dry_run,
        config.read_only,
    )

    mcp = OpenAIAdsMCPServer(
        "openai-ads-mcp",
        title="OpenAI Ads",
        version=__version__,
        instructions=INSTRUCTIONS,
        read_only=config.read_only,
    )

    @mcp.tool()
    def openai_ads_describe_server() -> dict[str, Any]:
        """Show the API host, account selection and safety settings this server uses."""
        return {
            "version": __version__,
            "api_base": config.api_base,
            "auth": auth.account_label,
            "ad_account_id": config.ad_account_id or "the key's own account",
            "dry_run": config.dry_run,
            "read_only": config.read_only,
            "tool_groups": config.tool_groups or "all",
            "bulk_threshold": config.bulk_threshold,
            "max_increase_pct": config.max_increase_pct,
            "max_daily_budget": config.max_daily_budget,
            "max_lifetime_budget": config.max_lifetime_budget,
            "max_bid": config.max_bid,
            "upload_dir": str(config.upload_dir) if config.upload_dir else None,
            "audit_log": str(config.audit_log),
            "audit_retention_days": config.audit_retention_days,
            "secrets_dir": str(config.secrets_dir),
            "page_size": config.page_size,
            "timeout": config.timeout,
            "transport": config.transport,
        }

    @mcp.tool()
    def openai_ads_health_check() -> dict[str, Any]:
        """Check the setup: call the API with the key, and summarise the ad account's status,
        currency, timezone and reviews."""
        checks: list[dict[str, Any]] = []
        t0 = time.time()
        try:
            account = client.request("GET", "/ad_account")
            checks.append(
                {"check": "get_ad_account", "ok": True, "ms": int((time.time() - t0) * 1000)}
            )
        except (OpenAIAdsAPIError, httpx.HTTPError) as e:
            checks.append({"check": "get_ad_account", "ok": False, "error": redact(str(e))[:400]})
            return {"ok": False, "checks": checks}
        account = account if isinstance(account, dict) else {}
        review = (account.get("review") or {}).get("status")
        integrity = ((account.get("account_integrity_review") or {}).get("review") or {}).get(
            "status"
        )
        notes = []
        if account.get("status") and account.get("status") != "active":
            notes.append(f"The account is {account.get('status')}: nothing will deliver.")
        if review and review != "approved":
            reason = (account.get("review") or {}).get("reason")
            notes.append(
                f"Brand review is {review}{f' ({reason})' if reason else ''}: ads can't serve "
                "until it is approved."
            )
        if config.dry_run:
            notes.append("Dry-run is on: writes are logged but not sent.")
        return {
            "ok": True,
            "checks": checks,
            "account": {
                "id": account.get("id"),
                "brand_name": account.get("brand_name") or account.get("name"),
                "status": account.get("status"),
                "currency_code": account.get("currency_code"),
                "timezone": account.get("timezone"),
                "brand_review": review,
                "account_integrity_review": integrity,
            },
            "notes": notes,
        }

    @mcp.tool()
    def openai_ads_audit_lookup(audit_id: str) -> dict[str, Any]:
        """Look up an audit log entry by ID: the request, response and before-snapshot of a write."""
        entry = audit.find(audit_id)
        if not entry:
            return {"found": False, "audit_id": audit_id}
        return {"found": True, "entry": entry}

    @mcp.tool()
    def openai_ads_audit_tail(limit: int = 20) -> dict[str, Any]:
        """Show the most recent audit log entries (writes and dry-run rehearsals)."""
        entries = audit.tail(limit)
        return {"count": len(entries), "entries": entries}

    @mcp.tool()
    def openai_ads_set_dry_run(enabled: bool) -> dict[str, Any]:
        """Turn dry-run on or off for this session. In dry-run, writes are recorded in
        the audit log and returned as a preview but NOT sent to OpenAI; reads still work."""
        client.config.dry_run = enabled
        logger.info("dry_run=%s (set via tool)", enabled)
        return {"dry_run": enabled}

    for group in groups:
        getattr(tools, group).register(mcp, client)

    logger.info(
        "registered %d tools (groups=%s read_only=%s)",
        len(mcp._tool_manager.list_tools()),
        ",".join(config.tool_groups) or "all",
        config.read_only,
    )
    return mcp
