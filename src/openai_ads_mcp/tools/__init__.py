from . import (
    account,
    ad_groups,
    ads,
    audiences,
    bulk,
    campaigns,
    conversions,
    feeds,
    files,
    leads,
    reporting,
    request,
    spend_limits,
    status,
)

__all__ = [
    "account",
    "ad_groups",
    "ads",
    "audiences",
    "bulk",
    "campaigns",
    "conversions",
    "feeds",
    "files",
    "leads",
    "reporting",
    "request",
    "spend_limits",
    "status",
]

# Tool groups (one per module) in registration order, which is also the order of
# tools/list. OPENAI_ADS_TOOL_GROUPS selects a subset by these names.
TOOL_GROUPS: tuple[str, ...] = (
    "account",
    "spend_limits",
    "campaigns",
    "ad_groups",
    "ads",
    "status",
    "files",
    "audiences",
    "conversions",
    "reporting",
    "feeds",
    "bulk",
    "leads",
    "request",
)


def resolve_tool_groups(requested: list[str] | tuple[str, ...]) -> list[str]:
    """Validate group names and return them in registration order.

    An empty selection means every group. Raises RuntimeError naming the
    unknown groups and listing the valid ones.
    """
    if not requested:
        return list(TOOL_GROUPS)
    unknown = [g for g in requested if g not in TOOL_GROUPS]
    if unknown:
        raise RuntimeError(
            f"OPENAI_ADS_TOOL_GROUPS has unknown group(s): {', '.join(unknown)}.\n"
            f"Valid groups: {', '.join(sorted(TOOL_GROUPS))}"
        )
    wanted = set(requested)
    return [g for g in TOOL_GROUPS if g in wanted]
