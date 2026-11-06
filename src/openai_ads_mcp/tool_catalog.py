"""Title and MCP annotations for every openai_ads_* tool.

`OpenAIAdsMCPServer.tool()` looks each tool up here by name, so the tool modules
stay free of metadata. Every tool must have an entry: tests/test_mcp_client.py
fails if a registered tool is missing or an entry has no tool.

Annotation kinds (all API tools are open-world: they call the OpenAI Ads API):

* READ: changes nothing in the ad account. Includes read-only POSTs (product
  queries, conversion insights, previews).
* WRITE: creates something; repeating it creates it again.
* UPSERT: an update or state change; repeating it changes nothing more.
* DESTRUCTIVE: archives or deletes; archived entities can't be re-activated.
* DESTRUCTIVE_ONCE: removes data and isn't idempotent (audience member removal or
  replacement), and the raw request tool, which may do anything.
* LOCAL_*: touches only this server (audit log, session settings).
"""

from __future__ import annotations

from typing import NamedTuple

from mcp.types import ToolAnnotations


def _ann(*, read_only: bool, destructive: bool, idempotent: bool, open_world: bool = True):
    return ToolAnnotations(
        read_only_hint=read_only,
        destructive_hint=destructive,
        idempotent_hint=idempotent,
        open_world_hint=open_world,
    )


READ = _ann(read_only=True, destructive=False, idempotent=True)
WRITE = _ann(read_only=False, destructive=False, idempotent=False)
UPSERT = _ann(read_only=False, destructive=False, idempotent=True)
DESTRUCTIVE = _ann(read_only=False, destructive=True, idempotent=True)
DESTRUCTIVE_ONCE = _ann(read_only=False, destructive=True, idempotent=False)
LOCAL_READ = _ann(read_only=True, destructive=False, idempotent=True, open_world=False)
LOCAL_SETTING = _ann(read_only=False, destructive=False, idempotent=True, open_world=False)


class ToolSpec(NamedTuple):
    title: str
    annotations: ToolAnnotations


# Registered regardless of OPENAI_ADS_TOOL_GROUPS and OPENAI_ADS_READ_ONLY.
ALWAYS_REGISTERED = frozenset(
    {
        "openai_ads_describe_server",
        "openai_ads_health_check",
        "openai_ads_audit_lookup",
        "openai_ads_audit_tail",
        "openai_ads_set_dry_run",
    }
)

_CATALOG: dict[str, tuple[str, ToolAnnotations]] = {
    # server (meta)
    "openai_ads_describe_server": ("Describe the server configuration", LOCAL_READ),
    "openai_ads_health_check": ("Check the API key and ad account", READ),
    "openai_ads_audit_lookup": ("Look up an audit log entry", LOCAL_READ),
    "openai_ads_audit_tail": ("Show recent audit log entries", LOCAL_READ),
    "openai_ads_set_dry_run": ("Turn dry-run mode on or off", LOCAL_SETTING),
    # account
    "openai_ads_get_ad_account": ("Get the ad account", READ),
    "openai_ads_list_ad_accounts": ("List reachable ad accounts", READ),
    "openai_ads_update_account_brand": ("Update account names, URL or icon", UPSERT),
    "openai_ads_pause_account": ("Pause the whole ad account", UPSERT),
    "openai_ads_activate_account": ("Activate the whole ad account", UPSERT),
    "openai_ads_list_account_changes": ("List the account's change history", READ),
    # spend_limits
    "openai_ads_get_spend_limits": ("Get account spending limits", READ),
    "openai_ads_create_spend_limit_window": ("Create a date-range spending limit", WRITE),
    "openai_ads_update_spend_limit_window": ("Update a date-range spending limit", UPSERT),
    "openai_ads_delete_spend_limit_window": ("Delete a date-range spending limit", DESTRUCTIVE),
    "openai_ads_set_daily_spend_limit": ("Set the daily account spending limit", UPSERT),
    "openai_ads_delete_daily_spend_limit": ("Remove the daily account spending limit", DESTRUCTIVE),
    # campaigns
    "openai_ads_list_campaigns": ("List campaigns", READ),
    "openai_ads_get_campaign": ("Get a campaign", READ),
    "openai_ads_create_campaign": ("Create a campaign", WRITE),
    "openai_ads_update_campaign": ("Update a campaign", UPSERT),
    "openai_ads_search_locations": ("Search targetable locations", READ),
    # ad_groups
    "openai_ads_list_ad_groups": ("List ad groups", READ),
    "openai_ads_get_ad_group": ("Get an ad group", READ),
    "openai_ads_create_ad_group": ("Create an ad group", WRITE),
    "openai_ads_update_ad_group": ("Update an ad group", UPSERT),
    # ads
    "openai_ads_list_ads": ("List ads", READ),
    "openai_ads_get_ad": ("Get an ad", READ),
    "openai_ads_create_ad": ("Create an ad", WRITE),
    "openai_ads_update_ad": ("Update an ad", UPSERT),
    "openai_ads_preview_ad": ("Preview an ad", READ),
    "openai_ads_get_landing_page_crawl": ("Get an ad's landing-page crawl", READ),
    # status
    "openai_ads_set_status": ("Pause or activate campaigns, ad groups or ads", UPSERT),
    "openai_ads_archive": ("Archive campaigns, ad groups, ads, audiences or feeds", DESTRUCTIVE),
    # files
    "openai_ads_upload_image": ("Upload an image", WRITE),
    "openai_ads_upload_audience_file": ("Upload a customer list file", WRITE),
    # audiences
    "openai_ads_list_audiences": ("List custom audiences", READ),
    "openai_ads_get_audience": ("Get a custom audience", READ),
    "openai_ads_create_audience": ("Create a custom audience", WRITE),
    "openai_ads_add_audience_members": ("Add members to a custom audience", WRITE),
    "openai_ads_remove_audience_members": (
        "Remove members from a custom audience",
        DESTRUCTIVE_ONCE,
    ),
    "openai_ads_replace_audience_members": (
        "Replace a custom audience's members",
        DESTRUCTIVE_ONCE,
    ),
    "openai_ads_merge_audiences": ("Merge custom audiences into a new one", WRITE),
    "openai_ads_list_audience_operations": ("List audience membership operations", READ),
    "openai_ads_get_audience_operation": ("Get an audience membership operation", READ),
    "openai_ads_cancel_audience_operation": ("Cancel an audience membership operation", UPSERT),
    "openai_ads_resume_audience_operation": ("Resume an audience membership operation", UPSERT),
    # conversions
    "openai_ads_list_pixels": ("List conversion pixels", READ),
    "openai_ads_create_pixel": ("Create a conversion pixel", WRITE),
    "openai_ads_list_event_settings": ("List conversion event settings", READ),
    "openai_ads_create_event_setting": ("Create a conversion event setting", WRITE),
    "openai_ads_create_conversions_api_key": ("Create a Conversions API key", WRITE),
    "openai_ads_list_recent_pixel_events": ("List recent pixel events", READ),
    # reporting
    "openai_ads_get_insights": ("Get a page of delivery insights", READ),
    "openai_ads_run_insights": ("Run an insights report and return all rows", READ),
    "openai_ads_get_conversion_insights": ("Get conversion insights", READ),
    "openai_ads_get_hotel_insights": ("Get Hotel insights", READ),
    # feeds
    "openai_ads_list_feeds": ("List product feeds", READ),
    "openai_ads_create_feed": ("Create a product feed", WRITE),
    "openai_ads_get_feed_settings": ("Get product feed settings", READ),
    "openai_ads_update_feed_settings": ("Update product feed settings", UPSERT),
    "openai_ads_list_feed_uploads": ("List product feed uploads", READ),
    "openai_ads_query_feed_products": ("Query products in a feed", READ),
    "openai_ads_update_feed_products": ("Update feed product prices and availability", UPSERT),
    "openai_ads_get_feed_sftp": ("Get a feed's SFTP access", READ),
    "openai_ads_configure_feed_sftp": ("Set up a feed's SFTP access", WRITE),
    "openai_ads_set_feed_sftp_status": ("Pause or reactivate a feed's SFTP access", UPSERT),
    # bulk
    "openai_ads_submit_bulk_job": ("Submit a bulk job", WRITE),
    "openai_ads_get_bulk_job": ("Get a bulk job", READ),
    "openai_ads_list_bulk_job_results": ("List a bulk job's operation results", READ),
    # leads
    "openai_ads_list_lead_sync_subscriptions": ("List lead-sync subscriptions", READ),
    "openai_ads_get_lead_sync_subscription": ("Get a lead-sync subscription", READ),
    "openai_ads_create_lead_sync_subscription": ("Create a lead-sync subscription", WRITE),
    "openai_ads_delete_lead_sync_subscription": (
        "Delete a lead-sync subscription",
        DESTRUCTIVE,
    ),
    # request
    "openai_ads_read_request": ("Read from any OpenAI Ads API endpoint", READ),
    "openai_ads_request": ("Call any OpenAI Ads API endpoint", DESTRUCTIVE_ONCE),
}

TOOL_SPECS: dict[str, ToolSpec] = {
    name: ToolSpec(title, annotations) for name, (title, annotations) in _CATALOG.items()
}
