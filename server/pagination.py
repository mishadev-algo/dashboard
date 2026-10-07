"""Progressive deal lists with filter-preserving, script-free navigation."""

from html import escape
from urllib.parse import urlencode


def deal_limit(params, step: int, total: int) -> int:
    try:
        requested = int(params.get("deals_limit", [str(step)])[0])
    except (ValueError, TypeError):
        requested = step
    return max(step, min(requested, max(step, total)))


def deal_pagination(path: str, query: dict, shown: int, total: int, step: int) -> str:
    body = f'<div class="deal-pagination"><span class="muted">Showing {shown} of {total} deals</span>'
    if shown < total:
        next_limit = min(total, shown + step)
        url = path + "?" + urlencode({**query, "deals_limit": next_limit}, doseq=True) + "#deals"
        body += f'<a class="show-more" href="{escape(url, quote=True)}">Show more <span aria-hidden="true">(+{next_limit - shown})</span></a>'
    elif total:
        body += '<span class="muted">All matching deals are shown.</span>'
    return body + '</div>'
