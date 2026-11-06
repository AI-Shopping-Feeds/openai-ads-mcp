# All tools

**76 tools: 71 across 14 groups + 5 meta tools.**

Groups are the modules in `src/openai_ads_mcp/tools`; `OPENAI_ADS_TOOL_GROUPS`
selects some of them. `OPENAI_ADS_READ_ONLY=true` keeps only the tools marked
*read* below. The meta tools are always registered.

"Kind" comes from each tool's MCP annotations: *read* changes nothing in the ad
account, *write* creates or updates, *destructive* archives, deletes or removes
(or, for `openai_ads_request`, may do anything).

Money arguments (`daily_budget`, `lifetime_budget`, `max_bid`, `amount`) are in the
ad account's currency, e.g. `50` for 50 USD. The server converts them to the API's
micros (1.00 = 1,000,000). Results show both: `budget` in micros and
`budget_in_currency`.

## Confirm words

| Change | Needs |
|---|---|
| Archive a campaign, ad group, ad, custom audience or product feed | `confirm="ARCHIVE"` |
| Delete a spend-limit window, the daily spend limit or a lead-sync subscription | `confirm="DELETE"` |
| More than `OPENAI_ADS_BULK_THRESHOLD` items (default 10) | `confirm="APPLY"` |
| A budget, bid, audience multiplier or account spend limit rising more than `OPENAI_ADS_MAX_INCREASE_PCT` (default 20%), or whose current value couldn't be read | `confirm="APPLY"` |
| A value above `OPENAI_ADS_MAX_DAILY_BUDGET`, `OPENAI_ADS_MAX_LIFETIME_BUDGET` or `OPENAI_ADS_MAX_BID` (if set) | `confirm="APPLY"` |
| Creating anything with `status="active"`, activating campaigns/ad groups/ads, or activating the account | `confirm="APPLY"` |
| Switching an ad group to Maximize Results (no max bid cap) | `confirm="APPLY"` |
| Changing the brand name, legal name or URL (starts a brand review that pauses delivery) | `confirm="APPLY"` |
| Replacing an audience's members, starting lead sync to a webhook | `confirm="APPLY"` |
| Applying a Bulk API job (`validate_only=false`) | `confirm="APPLY"` |
| Any write through `openai_ads_request` | `confirm="APPLY"` |

Without the right word a guarded write returns `refused: true`, the reasons and
the money changes it would make, and sends nothing. In dry-run mode nothing is
sent anyway, so guards don't block; the preview says which confirm a real run
would need.

## Credentials the API returns once

`openai_ads_create_conversions_api_key`, `openai_ads_configure_feed_sftp` (password
method), `openai_ads_create_lead_sync_subscription` and any `openai_ads_request` /
`openai_ads_read_request` response containing `api_key`, `password` or
`signing_secret` save those values to an owner-only file under
`~/.openai-ads-mcp/secrets/created/` and return its path instead of the value.

## meta (5)

| Tool | What it does | Kind |
|---|---|---|
| `openai_ads_describe_server` | Describe the server configuration | read |
| `openai_ads_health_check` | Check the API key and ad account | read |
| `openai_ads_audit_lookup` | Look up an audit log entry | read |
| `openai_ads_audit_tail` | Show recent audit log entries | read |
| `openai_ads_set_dry_run` | Turn dry-run mode on or off | write |

## account (6)

| Tool | What it does | Kind |
|---|---|---|
| `openai_ads_get_ad_account` | Get the ad account | read |
| `openai_ads_list_ad_accounts` | List reachable ad accounts | read |
| `openai_ads_update_account_brand` | Update account names, URL or icon | write |
| `openai_ads_pause_account` | Pause the whole ad account | write |
| `openai_ads_activate_account` | Activate the whole ad account | write |
| `openai_ads_list_account_changes` | List the account's change history | read |

## spend_limits (6)

| Tool | What it does | Kind |
|---|---|---|
| `openai_ads_get_spend_limits` | Get account spending limits | read |
| `openai_ads_create_spend_limit_window` | Create a date-range spending limit | write |
| `openai_ads_update_spend_limit_window` | Update a date-range spending limit | write |
| `openai_ads_delete_spend_limit_window` | Delete a date-range spending limit | destructive |
| `openai_ads_set_daily_spend_limit` | Set the daily account spending limit | write |
| `openai_ads_delete_daily_spend_limit` | Remove the daily account spending limit | destructive |

## campaigns (5)

| Tool | What it does | Kind |
|---|---|---|
| `openai_ads_list_campaigns` | List campaigns | read |
| `openai_ads_get_campaign` | Get a campaign | read |
| `openai_ads_create_campaign` | Create a campaign | write |
| `openai_ads_update_campaign` | Update a campaign | write |
| `openai_ads_search_locations` | Search targetable locations | read |

## ad_groups (4)

| Tool | What it does | Kind |
|---|---|---|
| `openai_ads_list_ad_groups` | List ad groups | read |
| `openai_ads_get_ad_group` | Get an ad group | read |
| `openai_ads_create_ad_group` | Create an ad group | write |
| `openai_ads_update_ad_group` | Update an ad group | write |

## ads (6)

| Tool | What it does | Kind |
|---|---|---|
| `openai_ads_list_ads` | List ads | read |
| `openai_ads_get_ad` | Get an ad | read |
| `openai_ads_create_ad` | Create an ad | write |
| `openai_ads_update_ad` | Update an ad | write |
| `openai_ads_preview_ad` | Preview an ad | read |
| `openai_ads_get_landing_page_crawl` | Get an ad's landing-page crawl | read |

## status (2)

| Tool | What it does | Kind |
|---|---|---|
| `openai_ads_set_status` | Pause or activate campaigns, ad groups or ads | write |
| `openai_ads_archive` | Archive campaigns, ad groups, ads, audiences or feeds | destructive |

## files (2)

| Tool | What it does | Kind |
|---|---|---|
| `openai_ads_upload_image` | Upload an image | write |
| `openai_ads_upload_audience_file` | Upload a customer list file | write |

## audiences (11)

| Tool | What it does | Kind |
|---|---|---|
| `openai_ads_list_audiences` | List custom audiences | read |
| `openai_ads_get_audience` | Get a custom audience | read |
| `openai_ads_create_audience` | Create a custom audience | write |
| `openai_ads_add_audience_members` | Add members to a custom audience | write |
| `openai_ads_remove_audience_members` | Remove members from a custom audience | destructive |
| `openai_ads_replace_audience_members` | Replace a custom audience's members | destructive |
| `openai_ads_merge_audiences` | Merge custom audiences into a new one | write |
| `openai_ads_list_audience_operations` | List audience membership operations | read |
| `openai_ads_get_audience_operation` | Get an audience membership operation | read |
| `openai_ads_cancel_audience_operation` | Cancel an audience membership operation | write |
| `openai_ads_resume_audience_operation` | Resume an audience membership operation | write |

## conversions (6)

| Tool | What it does | Kind |
|---|---|---|
| `openai_ads_list_pixels` | List conversion pixels | read |
| `openai_ads_create_pixel` | Create a conversion pixel | write |
| `openai_ads_list_event_settings` | List conversion event settings | read |
| `openai_ads_create_event_setting` | Create a conversion event setting | write |
| `openai_ads_create_conversions_api_key` | Create a Conversions API key | write |
| `openai_ads_list_recent_pixel_events` | List recent pixel events | read |

## reporting (4)

| Tool | What it does | Kind |
|---|---|---|
| `openai_ads_get_insights` | Get a page of delivery insights | read |
| `openai_ads_run_insights` | Run an insights report and return all rows | read |
| `openai_ads_get_conversion_insights` | Get conversion insights | read |
| `openai_ads_get_hotel_insights` | Get Hotel insights | read |

## feeds (10)

| Tool | What it does | Kind |
|---|---|---|
| `openai_ads_list_feeds` | List product feeds | read |
| `openai_ads_create_feed` | Create a product feed | write |
| `openai_ads_get_feed_settings` | Get product feed settings | read |
| `openai_ads_update_feed_settings` | Update product feed settings | write |
| `openai_ads_list_feed_uploads` | List product feed uploads | read |
| `openai_ads_query_feed_products` | Query products in a feed | read |
| `openai_ads_update_feed_products` | Update feed product prices and availability | write |
| `openai_ads_get_feed_sftp` | Get a feed's SFTP access | read |
| `openai_ads_configure_feed_sftp` | Set up a feed's SFTP access | write |
| `openai_ads_set_feed_sftp_status` | Pause or reactivate a feed's SFTP access | write |

## bulk (3)

| Tool | What it does | Kind |
|---|---|---|
| `openai_ads_submit_bulk_job` | Submit a bulk job | write |
| `openai_ads_get_bulk_job` | Get a bulk job | read |
| `openai_ads_list_bulk_job_results` | List a bulk job's operation results | read |

## leads (4)

| Tool | What it does | Kind |
|---|---|---|
| `openai_ads_list_lead_sync_subscriptions` | List lead-sync subscriptions | read |
| `openai_ads_get_lead_sync_subscription` | Get a lead-sync subscription | read |
| `openai_ads_create_lead_sync_subscription` | Create a lead-sync subscription | write |
| `openai_ads_delete_lead_sync_subscription` | Delete a lead-sync subscription | destructive |

## request (2)

| Tool | What it does | Kind |
|---|---|---|
| `openai_ads_read_request` | Read from any OpenAI Ads API endpoint | read |
| `openai_ads_request` | Call any OpenAI Ads API endpoint | destructive |

## Endpoints reachable only through `openai_ads_request`

These are in OpenAI's OpenAPI document but need Ads OAuth (partner sign-in), which this
server doesn't use, or are partner-only, so they have no dedicated tool:
`GET /me`, `POST /ad_account_creation_sessions`, `POST /sandbox/ad_eligibility_jobs`
and its status/results/cancel endpoints, `POST /partner_data/uploads` and
`GET /partner_data/uploads/{upload_id}`, and `POST /api_keys` (creates a new API key;
its key is saved to a file, not returned).

Sending conversion events (Conversions API, `https://bzr.openai.com/v1/events`) uses a
different host and a Conversions API key. This server never calls that host; send
events from the advertiser's own server or tag manager.
