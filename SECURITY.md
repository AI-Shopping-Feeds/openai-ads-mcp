# Security

This server can create, change, pause, activate and archive OpenAI Ads campaigns,
ad groups, ads, audiences, conversion settings, product feeds and account spending
limits, using an Ads API key. Those changes spend real money and can stop or start
ad delivery across an ad account.

## Protect your credentials

- The Ads API key lives in `~/.openai-ads-mcp/secrets/openai-ads-key.json` (folder
  0700, file 0600) or in `OPENAI_ADS_API_KEY`. Never commit it, paste it into chat or
  put it in a shared client config.
- Credentials the API returns once (Conversions API keys, SFTP passwords, lead-sync
  signing secrets) are saved under `~/.openai-ads-mcp/secrets/created/` with owner-only
  permissions instead of being shown in chat. Move them into a proper secret manager
  and delete the files.
- Keys, passwords and secrets are redacted from logs, errors and the audit log.
- If a machine or key is lost, revoke the key in Ads Manager (ads.openai.com →
  Settings → General → API keys) and run `openai-ads-mcp purge`.
- Use one key per integration so you can revoke this server's access on its own.

## Limit what the agent can do

- Start with `OPENAI_ADS_DRY_RUN=true` or `OPENAI_ADS_READ_ONLY=true`.
- Set `OPENAI_ADS_MAX_DAILY_BUDGET`, `OPENAI_ADS_MAX_LIFETIME_BUDGET` and
  `OPENAI_ADS_MAX_BID` so bigger values need an explicit `confirm='APPLY'`.
- Local files can only be uploaded from `OPENAI_ADS_UPLOAD_DIR`; leave it unset to
  allow URL uploads only.
- The server only calls `https://api.ads.openai.com/v1`.

## Never expose the HTTP transport

The `streamable-http` and `sse` transports have no authentication. Anyone who can reach
the port can use your key and spend your budget. The server refuses to bind them to
anything other than `127.0.0.1`, `localhost` or `::1` unless you set
`OPENAI_ADS_ALLOW_REMOTE=true` (or pass `--allow-remote`). Only do that behind your own
authentication. Use stdio for desktop clients.

## Reporting a vulnerability or misuse of data

Report security issues privately through a
[GitHub private security advisory](https://github.com/AI-Shopping-Feeds/openai-ads-mcp/security/advisories/new)
on this repository. Don't post working exploits, keys or account data in a public issue.

Use the same channel to tell us about suspected misuse of OpenAI Ads data through this
software. If your own key or account may be compromised, also revoke the key in Ads
Manager and tell OpenAI promptly, as the Advertising Terms require.
