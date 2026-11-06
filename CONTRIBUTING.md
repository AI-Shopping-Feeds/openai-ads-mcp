# Contributing

Thanks for helping improve the OpenAI Ads MCP server.

## Development setup

```bash
git clone https://github.com/AI-Shopping-Feeds/openai-ads-mcp.git
cd openai-ads-mcp
uv venv && uv pip install -e ".[test,dev]"
```

Or with the standard library: `python -m venv .venv && .venv/bin/pip install -e ".[test,dev]"`.
Python 3.11 or later.

To try it against a real ad account, store an Ads API key with
`.venv/bin/openai-ads-mcp auth` and start with `OPENAI_ADS_DRY_RUN=true`.

## Checks

```bash
.venv/bin/ruff check src tests
.venv/bin/ruff format --check src tests
.venv/bin/mypy src/openai_ads_mcp
.venv/bin/pytest
```

Tests run offline: HTTP is mocked with `respx`. They never touch a real ad account.

## Pull requests

- Keep keys, secrets and ad account data out of commits, tests and issues. Use
  placeholders such as `adacct_123`, `cmpn_123` and `/abs/path/...`.
- New tools go in the matching module under `src/openai_ads_mcp/tools/`, use the
  `openai_ads_` prefix, and need an entry in `tool_catalog.py` (title and annotations)
  and a row in `docs/tools.md`. The test suite fails otherwise.
- Writes go through `OpenAIAdsClient.request` so dry-run, the audit log and
  redaction apply. Money changes use `safety.assess()`; archives and deletes need a
  confirm word.
- Build only on endpoints in OpenAI's public Ads documentation. Link to the docs; never
  paste OpenAI's documentation, OpenAPI spec or sample code into the repository.
- Update the README and `docs/` when tools, settings or setup change.
