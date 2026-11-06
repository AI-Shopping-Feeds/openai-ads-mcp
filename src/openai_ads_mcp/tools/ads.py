"""Ads and creatives: chat cards (one image or 4-6 images) and product-ad templates."""

from __future__ import annotations

from typing import Any

from .. import safety
from ..client import OpenAIAdsClient, path_id
from ._common import check_choice, clean, get_entity, guarded_write

CREATIVE_TYPES = ("chat_card", "product_ad_template")


def _check_copy(title: str | None, body: str | None) -> None:
    if title is not None and not 3 <= len(title.strip()) <= 50:
        raise ValueError(f"title must be 3-50 characters (got {len(title.strip())}).")
    if body is not None and len(body) > 100:
        raise ValueError(f"body must be at most 100 characters (got {len(body)}).")


def _check_images(file_id: str | None, file_ids: list[str] | None) -> None:
    if file_id and file_ids:
        raise ValueError("Use file_id for one image or file_ids for 4-6 images, not both.")
    if file_ids is not None and not 4 <= len(set(file_ids)) == len(file_ids) <= 6:
        raise ValueError("file_ids needs 4-6 distinct square images.")


def list_ads(
    client: OpenAIAdsClient,
    ad_group_id: str | None,
    campaign_id: str | None,
    *,
    include_serving_issues: bool = False,
    max_results: int = 200,
) -> tuple[list[Any], bool]:
    from .ad_groups import list_ad_groups

    base = {"include": ["serving_issues"]} if include_serving_issues else {}
    if ad_group_id:
        items, truncated, _ = client.paginate(
            "/ads",
            {**base, "ad_group_id": path_id(ad_group_id, "ad_group_id")},
            max_results=max_results,
        )
        return items, truncated
    if not campaign_id:
        raise ValueError("Pass ad_group_id or campaign_id.")
    groups, truncated = list_ad_groups(client, campaign_id, max_results=1000)
    out: list[Any] = []
    for g in groups:
        if len(out) >= max_results:
            return out[:max_results], True
        gid = g.get("id") if isinstance(g, dict) else None
        if not gid or g.get("status") == "archived":
            continue
        items, more, _ = client.paginate(
            "/ads", {**base, "ad_group_id": gid}, max_results=max_results - len(out)
        )
        out.extend({**a, "ad_group_id": a.get("ad_group_id", gid)} for a in items)
        truncated = truncated or more
    return out, truncated


def register(mcp, client: OpenAIAdsClient) -> None:
    @mcp.tool()
    def openai_ads_list_ads(
        ad_group_id: str | None = None,
        campaign_id: str | None = None,
        include_serving_issues: bool = False,
        max_results: int = 200,
    ) -> dict[str, Any]:
        """List ads in an ad group, or in every non-archived ad group of a campaign
        (pass campaign_id instead). Each ad has status, review_status (in_review,
        approved, rejected) and its creative."""
        items, truncated = list_ads(
            client,
            ad_group_id,
            campaign_id,
            include_serving_issues=include_serving_issues,
            max_results=max_results,
        )
        return {"data": items, "count": len(items), "truncated": truncated}

    @mcp.tool()
    def openai_ads_get_ad(ad_id: str, include_serving_issues: bool = True) -> dict[str, Any]:
        """Get one ad: status, review_status and review details, creative (with resolved
        image URLs) and, by default, serving issues that block delivery."""
        include = ["serving_issues"] if include_serving_issues else None
        return get_entity(client, "ad", ad_id, include)

    @mcp.tool()
    def openai_ads_create_ad(
        ad_group_id: str,
        name: str,
        title: str,
        body: str,
        target_url: str | None = None,
        file_id: str | None = None,
        file_ids: list[str] | None = None,
        creative_type: str = "chat_card",
        price: str | None = None,
        status: str = "paused",
        query_string_template: str | None = None,
        extra_creative: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        confirm: str = "",
    ) -> dict[str, Any]:
        """Create an ad in an ad group. It starts PAUSED unless status='active'. Creating
        an ad submits its creative for review against OpenAI's ad policies.

        name is internal (3-1000 chars). title 3-50 chars, body up to 100 chars.
        chat_card needs target_url (http/https, reachable by OpenAI's crawler) and an
        image: file_id for one image (at least 640x640), or file_ids for 4-6 distinct
        square images where the account is enabled for it. Upload images first with
        openai_ads_upload_image. creative_type='product_ad_template' (product-feed ad
        groups, one per group) takes image and URL from the feed; price may be
        '{{product.price}}'. extra_creative is merged into the creative (e.g. image_crop).

        status='active' needs confirm='APPLY'."""
        kind = check_choice(creative_type, CREATIVE_TYPES, "creative_type")
        state = check_choice(status, ("active", "paused"), "status")
        _check_copy(title, body)
        _check_images(file_id, file_ids)
        if kind == "chat_card":
            if not target_url:
                raise ValueError("chat_card ads need target_url.")
            if not (file_id or file_ids):
                raise ValueError("chat_card ads need file_id (one image) or file_ids (4-6).")
        creative: dict[str, Any] = clean(
            {
                "type": kind,
                "title": title,
                "body": body,
                "target_url": target_url,
                "file_id": file_id,
                "file_ids": file_ids,
                "price": price,
            }
        )
        creative.update(extra_creative or {})
        payload = clean(
            {
                "ad_group_id": path_id(ad_group_id, "ad_group_id"),
                "name": name,
                "status": state,
                "creative": creative,
                "landing_page_configuration": (
                    {"query_string_template": query_string_template}
                    if query_string_template
                    else None
                ),
            }
        )
        reasons = []
        if state == "active":
            reasons.append("the ad is created active, so it can serve once approved")
        decision = safety.assess(client.config, creating=True, extra_reasons=reasons)
        key = idempotency_key or client.new_idempotency_key("ad")
        return guarded_write(
            client,
            decision,
            confirm,
            payload,
            lambda: client.request(
                "POST", "/ads", json_body=payload, op="create_ad", idempotency_key=key
            ),
        )

    @mcp.tool()
    def openai_ads_update_ad(
        ad_id: str,
        name: str | None = None,
        title: str | None = None,
        body: str | None = None,
        target_url: str | None = None,
        file_id: str | None = None,
        file_ids: list[str] | None = None,
        price: str | None = None,
        fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update an ad's name or creative. Changes the live account.

        Creative changes (title, body, target_url, file_id, file_ids, price) are merged
        with the current creative, because the API needs the complete creative, and
        start a new review. For a multi-image ad, omitting file_ids keeps the images and
        omitting target_url keeps the destination. An ad can't move to another ad group,
        and a single-image ad can't become multi-image (create a new ad). fields is sent
        as-is for anything else. Use openai_ads_set_status to pause or activate."""
        aid = path_id(ad_id, "ad_id")
        _check_copy(title, body)
        _check_images(file_id, file_ids)
        current = client.snapshot(get_entity, client, "ad", aid)
        payload: dict[str, Any] = clean({"name": name})
        creative_change = clean(
            {
                "title": title,
                "body": body,
                "target_url": target_url,
                "file_id": file_id,
                "file_ids": file_ids,
                "price": price,
            }
        )
        if creative_change:
            old = (current or {}).get("creative") or {}
            if not old and (title is None or body is None):
                raise ValueError(
                    "Couldn't read the current creative; pass title and body (and the image "
                    "and target_url for a single-image ad) with the change."
                )
            multi = bool(old.get("images") or old.get("file_ids"))
            if multi and file_id:
                raise ValueError("This ad has multiple images; use file_ids, not file_id.")
            creative: dict[str, Any] = {
                "type": old.get("type", "chat_card"),
                "title": creative_change.get("title", old.get("title")),
                "body": creative_change.get("body", old.get("body")),
            }
            if multi:
                for key in ("file_ids", "target_url", "price"):
                    if key in creative_change:
                        creative[key] = creative_change[key]
            else:
                for key in ("target_url", "file_id", "price"):
                    value = creative_change.get(key, old.get(key))
                    if value is not None:
                        creative[key] = value
                if file_ids:
                    raise ValueError(
                        "A single-image ad can't switch to multiple images; create a new ad."
                    )
            payload["creative"] = creative
        payload.update(fields or {})
        if not payload:
            raise ValueError("Pass at least one field to change.")
        decision = safety.assess(client.config)
        out = guarded_write(
            client,
            decision,
            "",
            payload,
            lambda: client.request(
                "POST",
                f"/ads/{aid}",
                json_body=payload,
                op="update_ad",
                before=current,
                idempotent=True,
            ),
        )
        if creative_change and isinstance(out, dict):
            out["note"] = "Creative changed: the ad goes through review again."
        return out

    @mcp.tool()
    def openai_ads_preview_ad(ad_id: str) -> dict[str, Any]:
        """Render a preview of an existing (submitted) ad. Returns iframe HTML in
        data[0].body that expires after 24 hours. A preview shows how the ad looks; it
        doesn't confirm it can serve (check openai_ads_get_ad's serving issues)."""
        aid = path_id(ad_id, "ad_id")
        return client.request("POST", f"/ads/{aid}/preview", read_only=True)

    @mcp.tool()
    def openai_ads_get_landing_page_crawl(ad_id: str) -> dict[str, Any]:
        """Get OpenAI's saved landing-page crawl for an ad's submitted version: capture
        status and, when available, a screenshot URL valid for up to 24 hours. Useful when
        a review flags the destination. 404 = no capture yet; 409 = still pending."""
        aid = path_id(ad_id, "ad_id")
        return client.request("GET", f"/ads/{aid}/landing_page_crawl")
