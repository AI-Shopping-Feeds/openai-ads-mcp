# Getting OpenAI Ads API access

This guide is for people who run ads, not developers. It takes about 15 minutes if you
already have an OpenAI Ads account. Checked against OpenAI's documentation on
6 November 2026; screens in Ads Manager may have moved since.

## What you need

- An **OpenAI Ads account** in Ads Manager at [ads.openai.com](https://ads.openai.com/).
- **Admin access** to that account (you need it to create API keys).
- A computer with [uv](https://docs.astral.sh/uv/getting-started/installation/)
  (`brew install uv` on macOS) and an MCP client such as Claude Code or Claude Desktop.

There is no separate developer application, app review or approval for the Advertiser
API. Any Ads Manager account admin can create an API key, and the key works straight
away.

## 1. Get an Ads Manager account

Sign up at [ads.openai.com](https://ads.openai.com/). Self-service Ads Manager is
available when the legal entity that advertises and is billed is based in a country on
OpenAI's [availability list](https://help.openai.com/en/articles/20001245-ads-manager-availability)
(about 90 countries in November 2026, including the US, UK, Canada, Australia and most of
the EU). Elsewhere you can still create an account and OpenAI will tell you when
self-service opens; larger advertisers can go through OpenAI's ads sales team, agencies or
technology partners instead.

Things to know before you rely on the API:

- **Ads Manager is in beta**, and so is much of the API. Some features are only switched
  on for some accounts (see step 5).
- **Brand review:** an account can't serve ads until its brand review is approved. A new
  account usually needs an advertiser icon (`missing_favicon`). The time this takes isn't
  published; `openai_ads_health_check` shows the current state.
- **Currency and timezone** are chosen when the account is created and can't be changed
  later. All budgets and bids use that currency; all dates use that timezone.
- Custom audiences aren't available for campaigns targeting the EEA or Switzerland.

## 2. Create an Ads API key

1. Sign in to [Ads Manager](https://ads.openai.com/) and select the ad account.
2. Go to **Settings → General → API keys**.
3. Click **Create New Key**. Give it a name you'll recognise, such as "MCP server".
4. Copy the key. Treat it like a password: anyone with it can change your campaigns
   and spend your budget.

Each Ads API key belongs to **one ad account**. It is not the same as an OpenAI
platform API key (from platform.openai.com), and those don't work here.

## 3. Store the key

Run this in a terminal and paste the key when asked (it isn't shown as you type):

```shell
uvx --from git+https://github.com/AI-Shopping-Feeds/openai-ads-mcp.git openai-ads-mcp auth
```

The command calls the API with the key, prints the ad account ID (it looks like
`adacct_123`), currency and timezone, and saves the key to
`~/.openai-ads-mcp/secrets/openai-ads-key.json`, readable only by you. Nothing is saved
if the key doesn't work.

Prefer the key file to putting the key in your MCP client's config, which other tools and
backups may read. If you must, set `OPENAI_ADS_API_KEY` instead.

## 4. Add the server to your client

Follow [step 3 in the README](../README.md#3-add-it-to-your-client), then ask your agent
to "check my OpenAI Ads connection". Start with `OPENAI_ADS_DRY_RUN=true` so nothing is
changed until you're ready.

## 5. Features that need OpenAI to switch them on

Some endpoints answer `403` or `404 Not found` until OpenAI enables them for your account.
Ask your OpenAI ads contact or partner representative if you need them:

| Feature | Sign it isn't enabled |
|---|---|
| Brand updates through the API | `403` from `openai_ads_update_account_brand` |
| Pixels and Conversions API keys | `404 Not found` on the conversion tools |
| Conversion-optimised campaigns | `403 Conversion bidding is not enabled` |
| Product feeds and delta updates | `403 product_feed_api_disabled` |
| Bulk API (limited preview) | `404` from `openai_ads_submit_bulk_job` |
| Account spending limits | Only for postpaid invoice billing; needs billing permission |
| Ads with 4-6 images, segmented insights, Hotel ads | Validation errors or missing data |

## Agencies and partners

- The simplest set-up is **one key per client ad account**, created by an admin of that
  account. Add one server entry per client (`openai-ads-mcp auth --output
  /abs/path/client-a-key.json`, then `OPENAI_ADS_KEY_FILE` in that entry). Keys stay
  separate and each can be revoked on its own.
- Partner keys that reach several accounts send the account in an `OpenAI-Ad-Account`
  header: set `OPENAI_ADS_AD_ACCOUNT_ID`. `openai_ads_list_ad_accounts` shows which
  accounts a key reaches.
- OpenAI's Advertising Terms have extra rules for agencies acting for advertisers (written
  authority, vetting, no sharing keys): see [terms-and-data.md](terms-and-data.md).

## Revoking access

Delete the key in Ads Manager (Settings → General → API keys), then run
`openai-ads-mcp purge` to remove the local key file, saved credentials and audit log.

## Troubleshooting

- **`401`:** the key is wrong, revoked, or an OpenAI platform key. Create a new Ads API key.
- **`403` or `404` on a whole feature:** it isn't enabled for the account (table above).
- **Health check shows a brand review that isn't `approved`:** add an icon with
  `openai_ads_upload_image(purpose="account_favicon")` and
  `openai_ads_update_account_brand(favicon_file_id=...)`, then wait for review.
- **`429`:** the server already retries and stays under OpenAI's limits (600 requests a
  minute per endpoint, 1,200 overall); wait a minute.
