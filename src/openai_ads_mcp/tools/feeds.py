"""Product feeds: catalogs for product-feed campaigns (open beta).

The full catalog is uploaded over SFTP (outside this server); these tools create
feeds, manage SFTP access, check ingestion, query products and push delta updates
(price, availability, title) for existing variants. Feed access must be enabled
for the account (403 product_feed_api_disabled otherwise).
"""

from __future__ import annotations

from typing import Any

from .. import safety
from ..auth import stash_secrets
from ..client import OpenAIAdsClient, path_id
from ._common import clean, guarded_write

MAX_DELTA_VARIANTS = 1000


def register(mcp, client: OpenAIAdsClient) -> None:
    @mcp.tool()
    def openai_ads_list_feeds(
        include_product_count: bool = True, max_results: int = 100
    ) -> dict[str, Any]:
        """List product feeds in the account. include_product_count adds the number of
        ingested ads-eligible products (it can stay 0 while the first upload processes)."""
        params = {"include": ["product_count"]} if include_product_count else None
        items, truncated, cursor = client.paginate("/feeds", params, max_results=max_results)
        return {"data": items, "count": len(items), "truncated": truncated, "next_after": cursor}

    @mcp.tool()
    def openai_ads_create_feed(name: str, countries: list[str]) -> dict[str, Any]:
        """Create a product feed for the countries the catalog supports (ISO codes, e.g.
        ['US']). Then set up SFTP with openai_ads_configure_feed_sftp and upload a CSV
        that follows OpenAI's product file schema."""
        body = {"name": name, "countries": [c.strip().upper() for c in countries if c.strip()]}
        if not body["countries"]:
            raise ValueError("Pass at least one country code.")
        return client.request("POST", "/feeds", json_body=body, op="create_feed")

    @mcp.tool()
    def openai_ads_get_feed_settings(feed_id: str) -> dict[str, Any]:
        """Get a product feed's settings (name and whether search eligibility is respected)."""
        fid = path_id(feed_id, "feed_id")
        return client.request("GET", f"/feeds/{fid}/settings")

    @mcp.tool()
    def openai_ads_update_feed_settings(
        feed_id: str, name: str | None = None, is_search_eligible_respected: bool | None = None
    ) -> dict[str, Any]:
        """Rename a product feed or change whether it respects each product's
        is_eligible_search flag. Changes the live account."""
        fid = path_id(feed_id, "feed_id")
        body = clean({"name": name, "is_search_eligible_respected": is_search_eligible_respected})
        if not body:
            raise ValueError("Pass at least one setting to change.")
        before = client.snapshot(client.request, "GET", f"/feeds/{fid}/settings")
        return client.request(
            "PATCH",
            f"/feeds/{fid}/settings",
            json_body=body,
            op="update_feed_settings",
            before=before,
            idempotent=True,
        )

    @mcp.tool()
    def openai_ads_list_feed_uploads(
        feed_id: str | None = None, max_results: int = 50
    ) -> dict[str, Any]:
        """List catalog upload history: status (scanning, received, processing, completed,
        completed_with_errors, skipped, failed), rows accepted/rejected/ads-eligible and
        diagnostics (e.g. missing_required_column, invalid_value). feed_id filters the
        results to one feed."""
        items, truncated, cursor = client.paginate(
            "/feeds/uploads", {"paginate": True}, max_results=max_results
        )
        if feed_id:
            items = [u for u in items if isinstance(u, dict) and u.get("feed_id") == feed_id]
        return {"data": items, "count": len(items), "truncated": truncated, "next_after": cursor}

    @mcp.tool()
    def openai_ads_query_feed_products(
        feed_id: str,
        filters: list[dict[str, Any]] | None = None,
        limit: int = 50,
        after: str | None = None,
    ) -> dict[str, Any]:
        """List products in a feed that match ad-group product-set filters, to check what
        a product set would select. filters: [{"field": "brand", "operator": "in",
        "values": ["Acme"]}]; fields include title, item_id, price, brand,
        product_category, star_rating, condition; operators in, gt, gte, lt, lte (the
        comparisons only for price and star_rating, with values as strings)."""
        fid = path_id(feed_id, "feed_id")
        body = clean({"filters": filters or [], "limit": max(1, min(limit, 500)), "after": after})
        return client.request(
            "POST", f"/feeds/{fid}/products/query", json_body=body, read_only=True
        )

    @mcp.tool()
    def openai_ads_update_feed_products(
        feed_id: str, products: list[dict[str, Any]], confirm: str = ""
    ) -> dict[str, Any]:
        """Push delta updates (price, availability, title) for EXISTING product variants,
        without re-uploading the catalog. It can't add products.

        products: [{"id": "<parent product id>", "variants": [{"id": "<variant id>",
        "price": {"amount": 8999, "currency": "USD"}, "availability": {"status":
        "in_stock"}, "title": "..."}]}]. price.amount is in minor units (8999 = 89.99).
        availability: {"available": false} or {"status": "out_of_stock"}. Check that the
        response says accepted: true. More than OPENAI_ADS_BULK_THRESHOLD variants needs
        confirm='APPLY'. Needs Delta Feeds API access."""
        fid = path_id(feed_id, "feed_id")
        if not products:
            raise ValueError("Pass at least one product.")
        count = 0
        for i, p in enumerate(products):
            if not isinstance(p, dict) or not p.get("id") or not p.get("variants"):
                raise ValueError(f"products[{i}] needs id and a non-empty variants list.")
            for j, v in enumerate(p["variants"]):
                if not isinstance(v, dict) or not v.get("id"):
                    raise ValueError(f"products[{i}].variants[{j}] needs id.")
                count += 1
        if count > MAX_DELTA_VARIANTS:
            raise ValueError(f"At most {MAX_DELTA_VARIANTS} variants per call; got {count}.")
        body = {"products": products}
        decision = safety.assess(client.config, count=count)
        return guarded_write(
            client,
            decision,
            confirm,
            {"variants": count, "products": products[:20]},
            lambda: client.request(
                "PATCH",
                f"/feeds/{fid}/products",
                json_body=body,
                op="update_feed_products",
                idempotent=True,
            ),
        )

    @mcp.tool()
    def openai_ads_get_feed_sftp(feed_id: str) -> dict[str, Any]:
        """Show a feed's SFTP access: enabled, connection URI and authentication method."""
        fid = path_id(feed_id, "feed_id")
        return client.request("GET", f"/feeds/{fid}/sftp_access")

    @mcp.tool()
    def openai_ads_configure_feed_sftp(
        feed_id: str, authentication_method: str, ssh_public_key: str | None = None
    ) -> dict[str, Any]:
        """Set up SFTP access for uploading the catalog: authentication_method 'password'
        (generates a new password, replacing any previous one) or 'ssh_key' with
        ssh_public_key (the PUBLIC key file's contents; never a private key).

        A generated password is saved to an owner-only file under
        ~/.openai-ads-mcp/secrets/created/ and the path is returned instead of the
        password, so it stays out of the chat."""
        fid = path_id(feed_id, "feed_id")
        if authentication_method not in {"password", "ssh_key"}:
            raise ValueError("authentication_method must be 'password' or 'ssh_key'.")
        if authentication_method == "ssh_key":
            key = (ssh_public_key or "").strip()
            if not key:
                raise ValueError("ssh_key needs ssh_public_key.")
            if "PRIVATE KEY" in key:
                raise ValueError("That is a private key. Pass the public key (.pub) only.")
        body = clean(
            {"authentication_method": authentication_method, "ssh_public_key": ssh_public_key}
        )
        out = client.request(
            "POST", f"/feeds/{fid}/sftp_access", json_body=body, op="configure_feed_sftp"
        )
        return stash_secrets(
            out, kind="feed-sftp", label=fid, directory=client.config.secrets_dir / "created"
        )

    @mcp.tool()
    def openai_ads_set_feed_sftp_status(feed_id: str, enabled: bool) -> dict[str, Any]:
        """Pause (enabled=false) or reactivate (enabled=true) a feed's SFTP upload access.
        This controls uploads only; pause campaigns to stop ad delivery."""
        fid = path_id(feed_id, "feed_id")
        action = "activate" if enabled else "pause"
        return client.request(
            "POST",
            f"/feeds/{fid}/sftp_access/{action}",
            op=f"{action}_feed_sftp",
            idempotent=True,
        )
