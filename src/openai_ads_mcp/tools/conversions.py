"""Conversion setup: pixels (conversion sources), event settings and Conversions API keys.

Sending conversion events uses a separate host and a Conversions API key, and is
meant for the advertiser's own servers or tag manager, so this server doesn't do
it. Pixel management and key creation must be enabled for the account (404 "Not
found" otherwise).
"""

from __future__ import annotations

from typing import Any

from ..auth import stash_secrets
from ..client import OpenAIAdsClient
from ._common import clean


def register(mcp, client: OpenAIAdsClient) -> None:
    @mcp.tool()
    def openai_ads_list_pixels(max_results: int = 100) -> dict[str, Any]:
        """List conversion sources (web pixels). Each has an id (use as source_id in event
        settings) and a pixel_id (used by the JavaScript Pixel and the Conversions API)."""
        items, truncated, cursor = client.paginate("/conversions/pixels", max_results=max_results)
        return {"data": items, "count": len(items), "truncated": truncated, "next_after": cursor}

    @mcp.tool()
    def openai_ads_create_pixel(name: str) -> dict[str, Any]:
        """Create a web conversion source and its Pixel ID (3-1000 character name). Pixels
        created through the API use automatic advanced matching. Install it with the
        JavaScript Pixel or send server events with a Conversions API key."""
        return client.request(
            "POST",
            "/conversions/pixels",
            json_body={"name": name, "client_type": "web"},
            op="create_pixel",
        )

    @mcp.tool()
    def openai_ads_list_event_settings(max_results: int = 200) -> dict[str, Any]:
        """List conversion event settings (conversion definitions): event type, source,
        attribution window and the campaigns that optimise for them."""
        items, truncated, cursor = client.paginate(
            "/conversions/event_settings", max_results=max_results
        )
        return {"data": items, "count": len(items), "truncated": truncated, "next_after": cursor}

    @mcp.tool()
    def openai_ads_create_event_setting(
        name: str,
        event_type: str,
        source_id: str,
        custom_event_name: str | None = None,
        attribution_window_days: int = 30,
    ) -> dict[str, Any]:
        """Define a conversion to measure or optimise for, connected to one conversion
        source (source_id = a pixel's id, not its pixel_id).

        event_type: a supported standard event such as order_created, lead_created or
        registration_completed, or 'custom' with custom_event_name. Only standard events
        can be campaign optimisation goals. attribution_window_days: use 30."""
        if event_type == "custom" and not custom_event_name:
            raise ValueError("event_type 'custom' needs custom_event_name.")
        body = clean(
            {
                "name": name,
                "event_type": event_type,
                "custom_event_name": custom_event_name,
                "attribution_window_days": attribution_window_days,
                "source_ids": [source_id],
            }
        )
        return client.request(
            "POST", "/conversions/event_settings", json_body=body, op="create_event_setting"
        )

    @mcp.tool()
    def openai_ads_create_conversions_api_key(name: str) -> dict[str, Any]:
        """Create a Conversions API key for sending server-side conversion events.

        The key is shown only once by OpenAI. This server saves it to an owner-only file
        under ~/.openai-ads-mcp/secrets/created/ and returns that path instead of the key,
        so it doesn't end up in the chat or the audit log. Move it into the advertiser's
        server-side secret manager; never put it in browser code or source control."""
        out = client.request(
            "POST",
            "/conversions/api_keys",
            json_body={"name": name},
            op="create_conversions_api_key",
        )
        return stash_secrets(
            out,
            kind="conversions-api-key",
            label=name,
            directory=client.config.secrets_dir / "created",
        )

    @mcp.tool()
    def openai_ads_list_recent_pixel_events(pixel_id: str, limit: int = 50) -> dict[str, Any]:
        """Show up to 50 conversion events a Pixel sent in the last ~15 minutes, to test an
        installation (event type, source, channel: pixel_sdk or server_to_server). Not for
        reporting: use openai_ads_get_conversion_insights. Enabled accounts only."""
        return client.request(
            "GET",
            "/conversions/events",
            params={"pid": pixel_id.strip(), "limit": max(1, min(limit, 50))},
        )
