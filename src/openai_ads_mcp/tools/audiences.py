"""Custom audiences: create from customer lists, change members, merge, track operations.

Not available for campaigns targeting the EEA or Switzerland. Upload only
first-party data you have the rights and consents to use (Ad Tools Terms).
Member identifiers never reach the audit log: it records how many were sent.
"""

from __future__ import annotations

from typing import Any

from .. import safety
from ..client import OpenAIAdsClient, path_id
from ._common import check_choice, clean, guarded_write, ids_list

IDENTIFIER_TYPES = ("email", "phone", "email_sha256", "phone_number_sha256", "gaid")
INTENDED_USES = ("inclusion", "exclusion", "bid_multiplier")
MAX_INLINE_IDENTIFIERS = 10_000


def _member_body(
    file_id: str | None,
    identifiers: list[str] | None,
    identifier_type: str | None,
    auto_match: bool,
    expected_revision: int | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Request body and its audit-safe version (identifiers replaced by a count)."""
    if bool(file_id) == bool(identifiers):
        raise ValueError("Pass exactly one of file_id or identifiers.")
    if identifiers and len(identifiers) > MAX_INLINE_IDENTIFIERS:
        raise ValueError(
            f"At most {MAX_INLINE_IDENTIFIERS} inline identifiers; upload a file instead."
        )
    kind = check_choice(identifier_type, IDENTIFIER_TYPES, "identifier_type")
    if not auto_match and not kind:
        raise ValueError(f"Set identifier_type ({', '.join(IDENTIFIER_TYPES)}) or auto_match.")
    body = clean(
        {
            "file_id": file_id,
            "identifiers": list(identifiers) if identifiers else None,
            "identifier_type": None if auto_match else kind,
            "identifier_resolution": "auto" if auto_match else None,
            "expected_revision": expected_revision,
        }
    )
    audit = dict(body)
    if identifiers:
        audit["identifiers"] = f"<{len(identifiers)} identifiers, not logged>"
    return body, audit


def register(mcp, client: OpenAIAdsClient) -> None:
    def get_audience(audience_id: str) -> dict[str, Any]:
        out = client.request("GET", f"/custom_audiences/{path_id(audience_id, 'audience_id')}")
        return out if isinstance(out, dict) else {}

    @mcp.tool()
    def openai_ads_list_audiences(
        intended_use: str | None = None,
        audience_ids: list[str] | None = None,
        max_results: int = 200,
    ) -> dict[str, Any]:
        """List custom audiences with status, matched-count range and membership_revision.

        intended_use (inclusion, exclusion or bid_multiplier) returns only audiences
        eligible for that use; check it before targeting or adding a multiplier."""
        params = clean(
            {
                "intended_use": check_choice(intended_use, INTENDED_USES, "intended_use"),
                "custom_audience_ids": ids_list(audience_ids, "audience_id", 500)
                if audience_ids
                else None,
            }
        )
        items, truncated, cursor = client.paginate(
            "/custom_audiences", params, max_results=max_results
        )
        return {"data": items, "count": len(items), "truncated": truncated, "next_after": cursor}

    @mcp.tool()
    def openai_ads_get_audience(audience_id: str) -> dict[str, Any]:
        """Get one custom audience: processing status (e.g. ready, too_small), matched-count
        range and membership_revision (needed to replace members)."""
        return get_audience(audience_id)

    @mcp.tool()
    def openai_ads_create_audience(
        name: str,
        description: str | None = None,
        file_id: str | None = None,
        filename: str | None = None,
        mimetype: str | None = None,
        file_size: int | None = None,
        identifier_type: str | None = None,
        auto_match: bool = False,
    ) -> dict[str, Any]:
        """Create a custom audience, from an uploaded customer list or empty.

        With a file: pass file_id, filename, mimetype and file_size exactly as returned by
        openai_ads_upload_audience_file, and either identifier_type (email, phone,
        email_sha256, phone_number_sha256, gaid) or auto_match=true for a CSV whose
        columns mix identifier types. Processing is asynchronous: poll
        openai_ads_get_audience until it is ready. Without a file it starts empty (useful
        for exclusions)."""
        body = clean(
            {
                "name": name,
                "description": description,
                "file_id": file_id,
                "filename": filename,
                "mimetype": mimetype,
                "file_size": file_size,
                "identifier_type": None
                if auto_match
                else check_choice(identifier_type, IDENTIFIER_TYPES, "identifier_type"),
                "identifier_resolution": "auto" if auto_match else None,
            }
        )
        if file_id and not (filename and mimetype and file_size):
            raise ValueError("With file_id, also pass filename, mimetype and file_size.")
        if file_id and not (auto_match or identifier_type):
            raise ValueError("With file_id, set identifier_type or auto_match=true.")
        return client.request("POST", "/custom_audiences", json_body=body, op="create_audience")

    def change_members(
        operation: str,
        audience_id: str,
        body: dict[str, Any],
        audit: dict[str, Any],
        idempotency_key: str | None,
    ) -> dict[str, Any]:
        aid = path_id(audience_id, "audience_id")
        key = idempotency_key or client.new_idempotency_key(f"audience-{operation}")
        return client.request(
            "POST",
            f"/custom_audiences/{aid}/{operation}",
            json_body=body,
            audit_body=audit,
            op=f"{operation}_audience_members",
            idempotency_key=key,
        )

    @mcp.tool()
    def openai_ads_add_audience_members(
        audience_id: str,
        file_id: str | None = None,
        identifiers: list[str] | None = None,
        identifier_type: str | None = None,
        auto_match: bool = False,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Add people to a custom audience from an uploaded file (preferred) or inline
        identifiers. Returns an operation: poll openai_ads_get_audience_operation.

        Prefer file_id from openai_ads_upload_audience_file so customer data never passes
        through the chat; if you must use identifiers, prefer hashed types
        (email_sha256, phone_number_sha256). expected_revision (the audience's
        membership_revision) guards against concurrent changes. To retry the same
        submission, reuse its idempotency_key."""
        body, audit = _member_body(
            file_id, identifiers, identifier_type, auto_match, expected_revision
        )
        return change_members("add", audience_id, body, audit, idempotency_key)

    @mcp.tool()
    def openai_ads_remove_audience_members(
        audience_id: str,
        file_id: str | None = None,
        identifiers: list[str] | None = None,
        identifier_type: str | None = None,
        auto_match: bool = False,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Remove people from a custom audience (file or inline identifiers, same rules as
        openai_ads_add_audience_members). Other members stay. Returns an operation."""
        body, audit = _member_body(
            file_id, identifiers, identifier_type, auto_match, expected_revision
        )
        return change_members("remove", audience_id, body, audit, idempotency_key)

    @mcp.tool()
    def openai_ads_replace_audience_members(
        audience_id: str,
        file_id: str,
        filename: str | None = None,
        mimetype: str | None = None,
        file_size: int | None = None,
        identifier_type: str | None = None,
        auto_match: bool = False,
        expected_revision: int | None = None,
        idempotency_key: str | None = None,
        confirm: str = "",
    ) -> dict[str, Any]:
        """Replace an audience's whole membership with an uploaded file. Everyone not in the
        file is removed, so this needs confirm='APPLY' after the user agrees.
        expected_revision is required by the API; it is read for you if omitted."""
        if expected_revision is None:
            current = client.snapshot(get_audience, audience_id)
            if not current or current.get("membership_revision") is None:
                raise RuntimeError(
                    "Couldn't read the audience's membership_revision; pass expected_revision."
                )
            expected_revision = int(current["membership_revision"])
        body, audit = _member_body(file_id, None, identifier_type, auto_match, expected_revision)
        body.update(clean({"filename": filename, "mimetype": mimetype, "file_size": file_size}))
        audit.update(clean({"filename": filename, "mimetype": mimetype, "file_size": file_size}))
        decision = safety.assess(
            client.config,
            extra_reasons=["replacing removes every member who isn't in the new file"],
        )
        return guarded_write(
            client,
            decision,
            confirm,
            audit,
            lambda: change_members("replace", audience_id, body, audit, idempotency_key),
        )

    @mcp.tool()
    def openai_ads_merge_audiences(
        name: str, audience_ids: list[str], idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Merge existing custom audiences into a new audience (the sources are unchanged).
        Returns an operation; poll it with the new audience's ID."""
        ids = ids_list(audience_ids, "audience_id")
        if len(ids) < 2:
            raise ValueError("Pass at least two audience_ids to merge.")
        key = idempotency_key or client.new_idempotency_key("audience-merge")
        return client.request(
            "POST",
            "/custom_audiences/merge",
            json_body={"name": name, "custom_audience_ids": ids},
            op="merge_audiences",
            idempotency_key=key,
        )

    @mcp.tool()
    def openai_ads_list_audience_operations(
        audience_id: str, limit: int = 50, cursor: str | None = None
    ) -> dict[str, Any]:
        """List an audience's retained add/remove/replace/merge operations (not its
        creation), with their status."""
        aid = path_id(audience_id, "audience_id")
        return client.request(
            "GET",
            f"/custom_audiences/{aid}/operations",
            params=clean({"limit": max(1, min(limit, 500)), "cursor": cursor}),
        )

    @mcp.tool()
    def openai_ads_get_audience_operation(audience_id: str, operation_id: str) -> dict[str, Any]:
        """Check an audience membership operation. If it reports
        custom_audience_operation_recovery_required (409), resume it with
        openai_ads_resume_audience_operation; don't submit a new or inverse change."""
        aid = path_id(audience_id, "audience_id")
        oid = path_id(operation_id, "operation_id")
        return client.request("GET", f"/custom_audiences/{aid}/operations/{oid}")

    @mcp.tool()
    def openai_ads_cancel_audience_operation(audience_id: str, operation_id: str) -> dict[str, Any]:
        """Cancel an accepted add or remove before it starts changing membership."""
        aid = path_id(audience_id, "audience_id")
        oid = path_id(operation_id, "operation_id")
        return client.request(
            "POST",
            f"/custom_audiences/{aid}/operations/{oid}/cancel",
            op="cancel_audience_operation",
            idempotent=True,
        )

    @mcp.tool()
    def openai_ads_resume_audience_operation(audience_id: str, operation_id: str) -> dict[str, Any]:
        """Resume an interrupted add or remove with its original inputs and saved progress.
        Replace and merge can't be resumed: retry them with their original request and
        idempotency_key."""
        aid = path_id(audience_id, "audience_id")
        oid = path_id(operation_id, "operation_id")
        return client.request(
            "POST",
            f"/custom_audiences/{aid}/operations/{oid}/resume",
            op="resume_audience_operation",
            idempotent=True,
        )
