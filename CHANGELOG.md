# Changelog — OpenAI Ads MCP (`openai-ads-mcp`)

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-11-06

### Added
- First version, built on the OpenAI Ads Advertiser API v1 as documented on
  6 November 2026 (changelog up to 26 October 2026): 76 tools across 14 groups plus 5
  meta tools, on the same architecture as the other AI Shopping Feeds MCP servers
  (tool catalog with titles and annotations, tool groups, read-only mode, dry-run,
  JSONL audit log, stdio and streamable HTTP transports, progress notifications).
- Ads API key auth: `openai-ads-mcp auth` checks the key against `GET /ad_account` and
  stores it in `~/.openai-ads-mcp/secrets/` (0700 folder, 0600 file). Partner keys can
  pick an account with `OPENAI_ADS_AD_ACCOUNT_ID` (`OpenAI-Ad-Account` header).
- Account details, branding, account pause/activate, OpenAI's change log; date-range
  and daily account spending limits.
- Campaigns (with location, platform and audience targeting and location search), ad
  groups (fixed bids, Maximize Results, audience bid multipliers, product sets), ads
  (single-image, multi-image and product-ad templates), previews and landing-page
  crawl evidence; pause/activate/archive for many IDs at once.
- Image and customer-list uploads (local files only from `OPENAI_ADS_UPLOAD_DIR`),
  custom audiences with add/remove/replace/merge and operation tracking.
- Conversion setup: pixels, event settings, Conversions API keys, recent pixel events.
- Insights: one page, every page with progress (`openai_ads_run_insights`), conversion
  insights in the account timezone, and Hotel insights (limited beta).
- Product feeds: create, settings, SFTP access, upload history, product queries and
  delta price/availability updates. Bulk API jobs (limited preview), validate-only by
  default. Lead-sync webhook subscriptions (early access).
- Escape hatches `openai_ads_read_request` and `openai_ads_request` (confirm-gated).
- Safety: new campaigns, ad groups and ads are created paused; confirm words for
  archives, deletes, large batches, activations, budget/bid/limit rises above
  `OPENAI_ADS_MAX_INCREASE_PCT` (default 20%) or optional absolute caps; before-snapshots
  in the audit log; 30-day audit retention; `openai-ads-mcp purge`.
- Hardening: one request choke point, only `api.ads.openai.com` is called, IDs and paths
  are validated, 200 MB response cap, writes without an Idempotency-Key aren't retried
  after server errors, client-side rate limiting below the documented limits, secrets
  returned by the API saved to owner-only files instead of shown, audience identifiers
  kept out of the audit log, HTTP transports refuse non-loopback hosts unless
  `OPENAI_ADS_ALLOW_REMOTE=true`.
