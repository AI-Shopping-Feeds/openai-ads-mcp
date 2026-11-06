"""Configuration from environment variables and .env files.

Local files live in a per-user folder, `~/.openai-ads-mcp/` by default
(OPENAI_ADS_HOME), never in the install location (uvx installs into a cache):

* `secrets/` (0700): the Ads API key file (0600) and `created/`, where
  credentials that the API returns once (Conversions API keys, SFTP passwords,
  lead-sync signing secrets) are saved instead of being shown in chat.
* `logs/audit.jsonl`: the audit log of writes.
* `.env`: optional settings, read after `--env` / `./.env`.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# The only host this server calls (see docs/terms-and-data.md).
API_HOST = "api.ads.openai.com"
API_BASE = f"https://{API_HOST}/v1"
HOME_DIR_NAME = ".openai-ads-mcp"
KEY_FILE_NAME = "openai-ads-key.json"
AUDIT_LOG_NAME = "audit.jsonl"
# "sse" (HTTP+SSE) is deprecated by the MCP spec; prefer "streamable-http".
TRANSPORTS = ("stdio", "streamable-http", "sse")
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

_ACCOUNT_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _path_or_none(raw: str | None) -> Path | None:
    if not raw or not raw.strip():
        return None
    return Path(raw).expanduser().resolve()


def home_dir() -> Path:
    """The per-user data folder (OPENAI_ADS_HOME, default ~/.openai-ads-mcp)."""
    return _path_or_none(os.environ.get("OPENAI_ADS_HOME")) or (Path.home() / HOME_DIR_NAME)


def secrets_dir() -> Path:
    return home_dir() / "secrets"


def created_secrets_dir() -> Path:
    """Where credentials returned once by the API are saved (0600 files)."""
    return secrets_dir() / "created"


def key_file_path() -> Path:
    """Where `openai-ads-mcp auth` stores the Ads API key.

    OPENAI_ADS_KEY_FILE, default ~/.openai-ads-mcp/secrets/openai-ads-key.json.
    """
    return _path_or_none(os.environ.get("OPENAI_ADS_KEY_FILE")) or (secrets_dir() / KEY_FILE_NAME)


def audit_log_path() -> Path:
    """The audit log (OPENAI_ADS_AUDIT_LOG, default ~/.openai-ads-mcp/logs/audit.jsonl)."""
    return _path_or_none(os.environ.get("OPENAI_ADS_AUDIT_LOG")) or (
        home_dir() / "logs" / AUDIT_LOG_NAME
    )


def ensure_private_dir(path: Path) -> Path:
    """Create a folder (and parents) readable only by the owner (0700 on POSIX)."""
    path.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):  # e.g. a folder owned by someone else
        os.chmod(path, 0o700)
    return path


def write_private_json(path: Path, data: dict) -> None:
    """Write JSON to a file with owner-only (0600) permissions in a 0700 folder."""
    ensure_private_dir(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.chmod(path, 0o600)


def load_env_files(env_file: Path | None = None) -> None:
    """Load `--env` (or ./.env), then ~/.openai-ads-mcp/.env. Real env vars win."""
    if env_file is not None:
        load_dotenv(dotenv_path=env_file, override=False)
    else:
        load_dotenv(dotenv_path=Path.cwd() / ".env", override=False)
    user_env = home_dir() / ".env"
    if user_env.is_file():
        load_dotenv(dotenv_path=user_env, override=False)


def parse_tool_groups(raw: str | None) -> list[str]:
    """Split a comma-separated OPENAI_ADS_TOOL_GROUPS value and check every name.

    Names are case-insensitive and may use '-' for '_'. Empty means all groups.
    """
    from .tools import resolve_tool_groups  # local: tools imports this module

    names = [p.strip().lower().replace("-", "_") for p in (raw or "").split(",")]
    names = [n for n in names if n]
    return resolve_tool_groups(names) if names else []


def check_account_id(value: str | None, what: str = "OPENAI_ADS_AD_ACCOUNT_ID") -> str | None:
    value = (value or "").strip() or None
    if value and not _ACCOUNT_ID_RE.match(value):
        raise RuntimeError(f"{what} must look like 'adacct_123', got '{value}'.")
    return value


@dataclass
class Config:
    api_key: str | None
    audit_log: Path
    key_file: Path
    secrets_dir: Path
    # Only for partner keys that reach several accounts: sent as OpenAI-Ad-Account.
    ad_account_id: str | None = None
    # Local files may be uploaded only from inside this folder.
    upload_dir: Path | None = None
    page_size: int = 100
    timeout: float = 30.0
    dry_run: bool = False
    log_level: str = "INFO"
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000
    # HTTP transports refuse non-loopback hosts unless this is set.
    allow_remote: bool = False
    # Tool modules to register; empty means all (see openai_ads_mcp.tools.TOOL_GROUPS).
    tool_groups: list[str] = field(default_factory=list)
    # Register only tools annotated read-only (plus the meta tools).
    read_only: bool = False
    # Writes touching more than this many entities need confirm='APPLY'.
    bulk_threshold: int = 10
    # Bid, budget and spend-limit increases above this percentage need confirm='APPLY'.
    max_increase_pct: float = 20.0
    # Optional absolute caps in the account currency; values above them need confirm='APPLY'.
    max_daily_budget: float | None = None
    max_lifetime_budget: float | None = None
    max_bid: float | None = None
    # Audit entries older than this are pruned at startup; 0 keeps them.
    audit_retention_days: int = 30

    @property
    def api_base(self) -> str:
        return API_BASE


def read_key_file(path: Path) -> tuple[str | None, str | None]:
    """Return (api_key, ad_account_id) stored by `openai-ads-mcp auth`."""
    if not path.exists():
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise RuntimeError(f"Could not read the key file {path}: {e}") from e
    if not isinstance(data, dict):
        return None, None
    return (data.get("api_key") or None), (data.get("ad_account_id") or None)


def _float_or_none(name: str) -> float | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as e:
        raise RuntimeError(f"{name} must be a number, got '{raw}'") from e


def load_config(env_file: Path | None = None, *, require_key: bool = True) -> Config:
    """Read configuration from the environment (and .env files).

    Raises RuntimeError with actionable messages on misconfiguration."""
    load_env_files(env_file)

    key_file = key_file_path()
    api_key = os.environ.get("OPENAI_ADS_API_KEY", "").strip() or None
    file_account: str | None = None
    if api_key is None:
        api_key, file_account = read_key_file(key_file)
    if api_key is not None and any(c.isspace() for c in api_key):
        raise RuntimeError("OPENAI_ADS_API_KEY contains whitespace; paste the key exactly.")
    if api_key is None and require_key:
        raise RuntimeError(
            "No Ads API key configured. Either:\n"
            "  - run `openai-ads-mcp auth` once (saves it to "
            f"{key_file}), or\n"
            "  - set OPENAI_ADS_API_KEY to a key from Ads Manager -> Settings -> API keys, or\n"
            "  - set OPENAI_ADS_KEY_FILE to the file `openai-ads-mcp auth` wrote.\n"
            "This is an Ads API key from ads.openai.com, not an OpenAI platform API key."
        )

    ad_account_id = check_account_id(os.environ.get("OPENAI_ADS_AD_ACCOUNT_ID") or file_account)

    upload_dir = _path_or_none(os.environ.get("OPENAI_ADS_UPLOAD_DIR"))
    if upload_dir is not None and not upload_dir.is_dir():
        raise RuntimeError(f"OPENAI_ADS_UPLOAD_DIR is not a folder: {upload_dir}")

    transport = os.environ.get("OPENAI_ADS_TRANSPORT", "stdio").strip().lower()
    if transport not in TRANSPORTS:
        raise RuntimeError(
            f"OPENAI_ADS_TRANSPORT must be one of {', '.join(TRANSPORTS)}, got '{transport}'."
        )

    def _int(name: str, default: str) -> int:
        try:
            return int(os.environ.get(name, default))
        except ValueError as e:
            raise RuntimeError(f"{name} must be an integer") from e

    def _float(name: str, default: str) -> float:
        try:
            return float(os.environ.get(name, default))
        except ValueError as e:
            raise RuntimeError(f"{name} must be a number") from e

    page_size = _int("OPENAI_ADS_PAGE_SIZE", "100")
    if not 1 <= page_size <= 500:
        raise RuntimeError("OPENAI_ADS_PAGE_SIZE must be between 1 and 500.")

    return Config(
        api_key=api_key,
        ad_account_id=ad_account_id,
        audit_log=audit_log_path(),
        key_file=key_file,
        secrets_dir=secrets_dir(),
        upload_dir=upload_dir,
        page_size=page_size,
        timeout=_float("OPENAI_ADS_TIMEOUT", "30"),
        dry_run=_truthy(os.environ.get("OPENAI_ADS_DRY_RUN")),
        log_level=os.environ.get("OPENAI_ADS_LOG_LEVEL", "INFO").upper(),
        transport=transport,
        host=os.environ.get("OPENAI_ADS_HOST", "127.0.0.1").strip() or "127.0.0.1",
        port=_int("OPENAI_ADS_PORT", "8000"),
        allow_remote=_truthy(os.environ.get("OPENAI_ADS_ALLOW_REMOTE")),
        tool_groups=parse_tool_groups(os.environ.get("OPENAI_ADS_TOOL_GROUPS")),
        read_only=_truthy(os.environ.get("OPENAI_ADS_READ_ONLY")),
        bulk_threshold=_int("OPENAI_ADS_BULK_THRESHOLD", "10"),
        max_increase_pct=_float("OPENAI_ADS_MAX_INCREASE_PCT", "20"),
        max_daily_budget=_float_or_none("OPENAI_ADS_MAX_DAILY_BUDGET"),
        max_lifetime_budget=_float_or_none("OPENAI_ADS_MAX_LIFETIME_BUDGET"),
        max_bid=_float_or_none("OPENAI_ADS_MAX_BID"),
        audit_retention_days=_int("OPENAI_ADS_AUDIT_RETENTION_DAYS", "30"),
    )
