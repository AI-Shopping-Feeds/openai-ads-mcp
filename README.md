# OpenAI Ads MCP Server (read + write)

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE) [![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

An [MCP](https://modelcontextprotocol.io) server for ads in ChatGPT, using the
[OpenAI Ads Advertiser API](https://developers.openai.com/ads/api-overview). Use it from
Claude, ChatGPT, Cursor, Codex or any MCP client to build and change campaigns, ad
groups and ads, manage budgets, bids, targeting, audiences, conversion tracking and
product feeds, and report on performance.

It is maintained by [AI Shopping Feeds](https://github.com/AI-Shopping-Feeds) and offers:

- **The whole documented Advertiser API v1:** account and spending limits, campaigns,
  ad groups, ads and previews, uploads, custom audiences, conversion setup, insights,
  product feeds, Bulk API jobs and lead sync.
- **Safety:** dry-run, confirm words for archives, deletes, activations and bid or budget
  rises, paused-by-default creation, spend caps, an audit log with before-snapshots,
  read-only mode and tool groups.
- **Anything else** through a read-only and a confirm-gated raw request tool.

OpenAI doesn't publish an MCP server for OpenAI Ads; its own tools are Ads Manager and
this API. This one runs on your machine with your own Ads API key. The Advertiser API
and Ads Manager are in beta and change often: see [docs/terms-and-data.md](docs/terms-and-data.md).
Not affiliated with or endorsed by OpenAI.

Part of the AI Shopping Feeds MCP family: [Google Ads](https://github.com/AI-Shopping-Feeds/google-ads-mcp) · [Google Merchant Centre](https://github.com/AI-Shopping-Feeds/google-merchant-centre-mcp) · [Google Analytics](https://github.com/AI-Shopping-Feeds/google-analytics-mcp) · [YouTube Studio](https://github.com/AI-Shopping-Feeds/youtube-studio-mcp) · [Amazon Ads](https://github.com/AI-Shopping-Feeds/amazon-ads-mcp) · [Apple Ads](https://github.com/AI-Shopping-Feeds/apple-ads-mcp) · [Microsoft Ads](https://github.com/AI-Shopping-Feeds/microsoft-ads-mcp) · [TikTok Ads](https://github.com/AI-Shopping-Feeds/tiktok-ads-mcp) · [Pinterest Ads](https://github.com/AI-Shopping-Feeds/pinterest-ads-mcp) · [Snapchat Ads](https://github.com/AI-Shopping-Feeds/snapchat-ads-mcp) · **[OpenAI Ads](https://github.com/AI-Shopping-Feeds/openai-ads-mcp)** · [AppLovin](https://github.com/AI-Shopping-Feeds/applovin-mcp).

## Let your agent set it up

Paste this into Claude Code, Codex, Cursor or any agent that can read web pages and edit files:

```text
Set up the OpenAI Ads MCP server for me by following https://raw.githubusercontent.com/AI-Shopping-Feeds/openai-ads-mcp/main/skills/openai-ads-mcp/SKILL.md
```

The agent checks whether it's already connected, installs the runner if you agree,
walks you through creating an Ads API key and storing it, adds the server to your
client's config without touching your other servers, and checks it works by calling
`openai_ads_health_check`.

The same file is an [Agent Skill](https://agentskills.io), so your agent can keep it as a guide to using the tools well. For Claude Code, copy the folder into your skills:

```bash
git clone --depth 1 https://github.com/AI-Shopping-Feeds/openai-ads-mcp.git /tmp/openai-ads-mcp
cp -r /tmp/openai-ads-mcp/skills/openai-ads-mcp ~/.claude/skills/
```

Other agents that support skills load the same `skills/openai-ads-mcp` folder from their own skills directory.

## What it can do

76 tools, all prefixed `openai_ads_`. The full list is in [docs/tools.md](docs/tools.md).

### Read

| Area | Tools |
|---|---|
| Account | `get_ad_account`, `list_ad_accounts`, `list_account_changes`, `get_spend_limits` |
| Campaigns | `list_campaigns`, `get_campaign` (with serving issues), `search_locations` |
| Ad groups and ads | `list_ad_groups`, `get_ad_group`, `list_ads`, `get_ad`, `preview_ad`, `get_landing_page_crawl` |
| Audiences and conversions | `list_audiences`, `get_audience`, audience operations, `list_pixels`, `list_event_settings`, `list_recent_pixel_events` |
| Reporting | `get_insights`, `run_insights` (every page, with progress), `get_conversion_insights`, `get_hotel_insights` |
| Feeds and bulk | `list_feeds`, `get_feed_settings`, `list_feed_uploads`, `query_feed_products`, `get_feed_sftp`, `get_bulk_job`, `list_bulk_job_results` |
| Any endpoint | `read_request` |

### Write

| Area | Tools |
|---|---|
| Account | `update_account_brand`, `pause_account`, `activate_account`, spend-limit windows, `set_daily_spend_limit` |
| Campaigns, ad groups, ads | `create_campaign`, `update_campaign`, `create_ad_group`, `update_ad_group`, `create_ad`, `update_ad` |
| Status | `set_status` (pause or activate many), `archive` |
| Uploads and audiences | `upload_image`, `upload_audience_file`, `create_audience`, add, remove and replace members, `merge_audiences` |
| Conversions | `create_pixel`, `create_event_setting`, `create_conversions_api_key` |
| Feeds, bulk and leads | `create_feed`, `update_feed_products` (price and stock), SFTP access, `submit_bulk_job`, lead-sync subscriptions |
| Any endpoint | `request` |

### Safety

- **Dry-run** (`OPENAI_ADS_DRY_RUN=true` or `openai_ads_set_dry_run`) logs writes without sending them.
- **Paused by default:** new campaigns, ad groups and ads start paused; creating or activating anything live needs `confirm='APPLY'`.
- **Confirm words:** `ARCHIVE` for archives, `DELETE` for deletes, and `APPLY` for more than 10 items, budget, bid or spend-limit rises above 20% (`OPENAI_ADS_MAX_INCREASE_PCT`) or whose current value can't be read, values over optional caps, Maximize Results switches and raw writes.
- **Audit log** of every write with a before-snapshot (`~/.openai-ads-mcp/logs/audit.jsonl`), kept 30 days (`OPENAI_ADS_AUDIT_RETENTION_DAYS`); `openai-ads-mcp purge` deletes it and the stored key.
- **Read-only mode** (`OPENAI_ADS_READ_ONLY=true`) and **tool groups** (`OPENAI_ADS_TOOL_GROUPS`).
- **Secrets stay out of chat:** Conversions API keys, SFTP passwords and signing secrets go to owner-only files; audience identifiers never reach the audit log.
- Refuses to bind its HTTP transports to anything but localhost unless `OPENAI_ADS_ALLOW_REMOTE=true`.
- What OpenAI's terms allow and how data is handled: [docs/terms-and-data.md](docs/terms-and-data.md).

## Supported clients

| Client | Works? | How |
|---|---|---|
| Claude Code | ✅ | Local server, `claude mcp add` (below) |
| Claude Desktop | ✅ | Local server, `claude_desktop_config.json` |
| Cursor | ✅ | Local server, `~/.cursor/mcp.json` |
| VS Code (Copilot) | ✅ | Local server, `.vscode/mcp.json` (uses `servers` instead of `mcpServers`) |
| Codex | ✅ | Local server, `~/.codex/config.toml` |
| Gemini CLI | ✅ | Local server, `~/.gemini/settings.json` |
| Windsurf and other local clients | ✅ | Local server, same JSON entry |
| Claude.ai and ChatGPT | ❌ not yet | These need a remote server with sign-in. This server's HTTP transports have no sign-in, so don't expose them publicly. Use a local client. |

## Setup

### 1. Install uv

The server runs with `uvx` from [uv](https://docs.astral.sh/uv/getting-started/installation/)
(`brew install uv` on macOS) and needs Python 3.11 or later. It installs straight
from this repository:

```shell
uvx --from git+https://github.com/AI-Shopping-Feeds/openai-ads-mcp.git openai-ads-mcp
```

Pin a release by adding `@vX.Y.Z` to the URL.

### 2. Get credentials

1. **Ads Manager:** you need an OpenAI Ads account at [ads.openai.com](https://ads.openai.com/)
   with self-service access in your country, and admin access to it.
2. **Ads API key:** in Ads Manager go to Settings → General → API keys and create a key.
   It's an Ads API key, not an OpenAI platform key. Step by step:
   [docs/setup-openai-ads-api.md](docs/setup-openai-ads-api.md).
3. **Store it** (it is checked against the API and saved with owner-only permissions in
   `~/.openai-ads-mcp/secrets/`):

   ```shell
   uvx --from git+https://github.com/AI-Shopping-Feeds/openai-ads-mcp.git openai-ads-mcp auth
   ```

### 3. Add it to your client

Replace the placeholders. Keep your other servers in each file. With a key stored by
`auth` you need no `env` at all; the examples add dry-run for a safe first run.

**Claude Code**

```bash
claude mcp add --scope user openai-ads \
  --env OPENAI_ADS_DRY_RUN=true \
  --env OPENAI_ADS_UPLOAD_DIR=/abs/path/ad-assets \
  -- uvx --from git+https://github.com/AI-Shopping-Feeds/openai-ads-mcp.git openai-ads-mcp
```

**Claude Desktop, Cursor, Gemini CLI, Windsurf** (and VS Code under `servers`)

```json
{
  "mcpServers": {
    "openai-ads": {
      "command": "uvx",
      "args": ["--from", "git+https://github.com/AI-Shopping-Feeds/openai-ads-mcp.git", "openai-ads-mcp"],
      "env": {
        "OPENAI_ADS_DRY_RUN": "true",
        "OPENAI_ADS_UPLOAD_DIR": "/abs/path/ad-assets"
      }
    }
  }
}
```

Desktop apps often can't find `uvx` on the PATH; if the server doesn't start, use
the full path from `which uvx` as the `command`.

**Codex** (`~/.codex/config.toml`)

```toml
[mcp_servers.openai-ads]
command = "uvx"
args = ["--from", "git+https://github.com/AI-Shopping-Feeds/openai-ads-mcp.git", "openai-ads-mcp"]
env = { OPENAI_ADS_DRY_RUN = "true", OPENAI_ADS_UPLOAD_DIR = "/abs/path/ad-assets" }
```

To manage several ad accounts, add one server entry per account, each with its own key
(`OPENAI_ADS_KEY_FILE=/abs/path/client-a-key.json`, written by `auth --output`).

### 4. Check it works

Restart or reload the client, then ask it to "check my OpenAI Ads connection". It should
call `openai_ads_health_check` and show the ad account's currency, timezone and review status.

## Configuration

| Variable | Required | What it does |
|---|---|---|
| `OPENAI_ADS_API_KEY` | if no key file | Ads API key. Prefer `openai-ads-mcp auth`, which writes the key file |
| `OPENAI_ADS_KEY_FILE` | no | Key file path (default `~/.openai-ads-mcp/secrets/openai-ads-key.json`) |
| `OPENAI_ADS_AD_ACCOUNT_ID` | no | Account to act on, for partner keys that reach several accounts |
| `OPENAI_ADS_DRY_RUN` | no | `true` logs writes without sending them |
| `OPENAI_ADS_READ_ONLY` | no | `true` registers only read-only tools |
| `OPENAI_ADS_TOOL_GROUPS` | no | Comma-separated groups to register (default all): `account`, `spend_limits`, `campaigns`, `ad_groups`, `ads`, `status`, `files`, `audiences`, `conversions`, `reporting`, `feeds`, `bulk`, `leads`, `request`. The meta tools are always on |
| `OPENAI_ADS_BULK_THRESHOLD` | no | Items per call before `confirm='APPLY'` is needed (default 10) |
| `OPENAI_ADS_MAX_INCREASE_PCT` | no | Budget, bid and limit rise allowed without `confirm='APPLY'` (default 20) |
| `OPENAI_ADS_MAX_DAILY_BUDGET`, `OPENAI_ADS_MAX_LIFETIME_BUDGET`, `OPENAI_ADS_MAX_BID` | no | Optional caps in the account currency; higher values need `confirm='APPLY'` |
| `OPENAI_ADS_UPLOAD_DIR` | no | Folder that local images and customer lists may be uploaded from |
| `OPENAI_ADS_HOME` | no | Per-user data folder (default `~/.openai-ads-mcp`, also read for `.env`) |
| `OPENAI_ADS_AUDIT_LOG`, `OPENAI_ADS_AUDIT_RETENTION_DAYS` | no | Audit log path and days to keep entries (default 30; 0 keeps them) |
| `OPENAI_ADS_PAGE_SIZE`, `OPENAI_ADS_TIMEOUT`, `OPENAI_ADS_LOG_LEVEL` | no | List page size (1-500), request timeout and log level |
| `OPENAI_ADS_TRANSPORT`, `OPENAI_ADS_HOST`, `OPENAI_ADS_PORT` | no | `stdio` (default), `streamable-http` or `sse` (deprecated), and its bind address (default `127.0.0.1:8000`) |
| `OPENAI_ADS_ALLOW_REMOTE` | no | `true` lets HTTP transports bind to a non-loopback host (no built-in auth: only behind your own) |

Settings are read from the environment, then `--env` or `./.env`, then
`~/.openai-ads-mcp/.env`. A full example is in [.env.example](.env.example).

## Example prompts

```text
Show spend, clicks and CTR by campaign for the last 14 days, and flag any campaign with serving issues.
```

```text
Create a paused clicks campaign called "Example spring launch" in the US with a 50 daily budget, one ad group with a 1.50 max bid and context hints about trail running shoes, and an ad using the image at https://example.com/shoe.jpg. Do a dry run first.
```

```text
Lower the max bid by 15% on every ad group in campaign cmpn_123 whose CTR was below 0.5% last week.
```

```text
Check why ad ad_123 isn't serving: review status, serving issues, parent statuses, budget and account spend limits.
```

## More docs

- [All tools](docs/tools.md)
- [Getting OpenAI Ads API access](docs/setup-openai-ads-api.md)
- [Terms, data handling and brand use](docs/terms-and-data.md)
- [Agent skill](skills/openai-ads-mcp/SKILL.md): setup and usage guide for agents
- [Changelog](CHANGELOG.md) and [security policy](SECURITY.md)

## Contributing and licence

Issues and pull requests are welcome on [this repository](https://github.com/AI-Shopping-Feeds/openai-ads-mcp). See
[CONTRIBUTING.md](CONTRIBUTING.md).

Licensed under the [MIT License](LICENSE). "OpenAI" and "ChatGPT" are trademarks of
OpenAI, used here only to describe the API this software works with.

---

Try out [AI Shopping Feeds](https://aishoppingfeeds.com/), our shopping feed management tool (including Google Shopping feeds).
