"""Canonical internal links; strategy identity always includes broker and account."""

from urllib.parse import urlencode


def strategy_url(server: str, login: int, strategy: str, start: str = "", end: str = "", **filters) -> str:
    query = {"server": server, "login": str(login), "strategy": strategy}
    if start:
        query["from"] = start
    if end:
        query["to"] = end
    query.update({key: value for key, value in filters.items() if value})
    return "/strategy?" + urlencode(query)
