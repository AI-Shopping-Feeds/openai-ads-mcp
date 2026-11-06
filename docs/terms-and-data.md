# Terms, data handling and brand use

This is a plain-English summary of the OpenAI terms that govern the Advertiser API, and
how this server follows them. It is not legal advice and doesn't replace the terms
themselves. Checked on 6 November 2026; OpenAI can update its terms (with 30 days' notice
for material changes), so read the current versions:

- [Advertising Terms](https://openai.com/policies/advertising-terms/) (updated 15 October 2026)
- [Ad Tools Terms](https://openai.com/policies/ad-tools-terms/) (published 21 October 2026),
  covering audiences, AI creative tools and business agents
- [Conversion Terms](https://openai.com/policies/conversion-terms/) (published 12 June 2026),
  covering the pixel and Conversions API
- [Ad Tools Data Processing Addendum](https://openai.com/policies/ad-tools-dpa/),
  [Advertising Policies](https://openai.com/policies/ad-policies/) and
  [Usage Policies](https://openai.com/policies/usage-policies/)
- [Brand guidelines](https://openai.com/brand/)

Not affiliated with or endorsed by OpenAI.

## What the terms allow

- **Using the API to run campaigns.** The Advertising Terms (1.4) allow APIs to be used
  "only to submit, manage, and support Campaigns", following the documentation, rate
  limits and authentication requirements. That is all this server does.
- **Automation.** Nothing in the terms forbids managing campaigns through software or an
  AI agent. The restrictions (1.2) are on reverse engineering, extracting data "other than
  as permitted", getting round limits, and generating invalid clicks or impressions.
- **Beta features.** Ads Manager is labelled beta, and the Bulk API, product-feed
  campaigns, conversion-optimised bidding, Hotel ads and lead sync are previews or betas.
  Beta services are provided as-is, "may be changed at any time without notice" (8.5),
  and OpenAI may "limit, throttle, modify, deprecate" API access (1.4).

## What the terms require, and what this server does

| Requirement | Where | How this server handles it |
|---|---|---|
| Keep API keys secure and confidential; don't share, sell or sublicense them without OpenAI's approval | Advertising Terms 1.4; Ad Tools 2.1(c); Conversion Terms 1(c) | Each user brings their own key. It is stored in a 0600 file in a 0700 folder, never logged, and redacted from errors and the audit log. Keys the API returns once go to owner-only files, not the chat. |
| Report credential compromise or security incidents promptly | Advertising Terms 1.3 | SECURITY.md says to revoke the key, run `purge` and tell OpenAI. |
| Respect rate limits; don't circumvent limits | 1.2(d), 1.4 | Client-side limiter below the documented 600/min per endpoint and 1,200/min overall, and 10 bulk jobs per 10 seconds; `Retry-After` honoured on `429`. |
| Use Reporting Data only to measure a campaign's frequency, reach or effectiveness | 5.4 | Insights stay in memory and are returned to your client only. They are never written to disk or the audit log. INSTRUCTIONS and the skill tell the agent this. |
| Don't use Reporting Data to identify people, sell it, or build audiences relating to OpenAI, its users or its brands | 5.4(b)-(d); Ad Tools 2.4; Conversion Terms 3 | INSTRUCTIONS line: never try to identify people or build audiences about OpenAI users or brands from reporting. |
| Reporting Data from conversion tools is OpenAI's confidential information | Conversion Terms 3 and 7 | Shared only with the AI client you choose to run (see "AI clients" below). |
| Audience data must be first-party, with notices and consents, and no sensitive or children's data | Ad Tools 2.2 | Upload tools say so; local files only from `OPENAI_ADS_UPLOAD_DIR`; identifiers never reach the audit log (a count is recorded instead). |
| No copying or reverse engineering | 1.2(b) | No OpenAI documentation, OpenAPI spec or sample code is in this repository. The client is written from the public docs. |
| Only OpenAI hosts | (our own rule) | Only `https://api.ads.openai.com/v1` is called; paths and IDs are validated; responses are capped at 200 MB. |
| Local data | (our own rule) | Audit log keeps writes only, pruned after 30 days (`OPENAI_ADS_AUDIT_RETENTION_DAYS`). `openai-ads-mcp purge` deletes the audit log, the key file and saved credentials. |

## Data this server stores

| What | Where | Kept |
|---|---|---|
| Ads API key | `~/.openai-ads-mcp/secrets/openai-ads-key.json` (0600) | Until `purge` or you delete it |
| Credentials returned once (Conversions API keys, SFTP passwords, signing secrets) | `~/.openai-ads-mcp/secrets/created/` (0600) | Until `purge` or you move them |
| Audit log: write requests, a before-snapshot and responses, with secrets redacted | `~/.openai-ads-mcp/logs/audit.jsonl` (0600) | 30 days by default |
| Insights, lists and other reads | Memory only, returned to your MCP client | Not stored |

Nothing is sent anywhere except OpenAI's API and your own MCP client.

## Flags for the maintainer

### (a) Using it with AI clients such as Claude

Nothing in OpenAI's advertising terms forbids managing OpenAI Ads with a third-party AI
client, and there is no data-mixing ban like Amazon's. Two things need care:

- **Reporting Data is confidential.** The Advertising Terms (5.4) let you share it with "a
  service provider acting solely on your behalf" for a permitted purpose, and the
  Conversion Terms (3) allow sharing with "your service providers solely as necessary".
  When an agent reads insights, that data goes to the AI provider. That fits when the
  provider acts as your service provider under business or API terms that don't use your
  inputs for training. A consumer chat app that trains on conversations is a weaker fit.
  The README and skill say this.
- **Personal data.** Customer lists pasted into chat go to the AI provider too. The tools
  steer agents to upload files from `OPENAI_ADS_UPLOAD_DIR` (which go straight to OpenAI)
  and to use hashed identifiers if they must send them inline.

### (b) Making the repository public under a name containing "OpenAI"

This is the main open question.

- OpenAI's [brand guidelines](https://openai.com/brand/) for API developers ask that an
  app's name, logo and description be free of OpenAI's brands. Their example of what not
  to do is a product named "MeOwpenAI". The guidelines also forbid "GPT" in product names.
- The Advertising Terms (6.5) say a customer won't use OpenAI's name on any website
  "except as expressly approved in writing by OpenAI". That binds AI Shopping Feeds if it
  is itself an OpenAI Ads customer.
- Descriptive use in text ("works with the OpenAI Ads Advertiser API") is normally fine,
  and the README does that with a not-affiliated line and no logos. A repository and
  command named `openai-ads-mcp` goes further than descriptive text.
- Others already publish community servers named `openai-ads-mcp` on GitHub, and the PyPI
  name `openai-ads-mcp` is taken by a third party. That isn't permission, and it means
  this package can't be published to PyPI under that name anyway.

Options: rename before going public to a neutral name (for example `chat-ads-mcp` or an
AI Shopping Feeds name), or ask OpenAI (partnercomms@openai.com) for written approval of
the name. Renaming is mechanical: repository, package `openai_ads_mcp`, command, the
`openai_ads_` tool prefix and `OPENAI_ADS_` variables.

### (c) Agencies using it for clients

The Advertising Terms treat agencies as "Advertising Intermediaries" (section 2):

- Before managing a client's campaigns, have a written agreement that authorises you and
  binds the client to OpenAI's terms; act within that authority; you are responsible for
  the client's compliance.
- Vet each advertiser (legal existence and licences, sanctions lists, policy compliance)
  and stop if you suspect they don't qualify.
- Give each client only the Reporting Data about its own campaigns.
- Don't share, transfer or sublicense your own API keys to clients without OpenAI's
  approval. The pattern OpenAI documents for partners is that the advertiser creates a
  key in its own account and gives it to the partner. Run one server entry per client
  key so keys and audit trails stay separate.
- If you re-invoice clients, the tax rules in 3.3-3.4 apply.

## Documented changes to design around

- **Product-feed tracking order:** accounts on the legacy query-parameter order had to
  switch by 5 November 2026. This server sets tracking templates through the API and
  doesn't depend on the old order.
- **Attribution defaults (30 September 2026):** insights default to 30-day click and
  1-day view windows with view-through conversions included. The insights tools expose
  both windows and the time basis.
- **Granular web platforms (10 September 2026):** `desktop_web`, `ios_web` and
  `android_web` alongside `web`; all are accepted in campaign targeting.
- **Legacy `name` on brand updates:** this server only sends `legal_name`, `account_name`
  and `brand_name`.
- No sunset dates for Advertiser API v1 endpoints were announced as of 6 November 2026.
