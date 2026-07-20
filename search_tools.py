"""
search_tool.py

Queries a locally self-hosted SearXNG instance to give the LLM access to
current web information, addressing the "model's knowledge is frozen at
training time" limitation. Same pattern as UrlFetcher/ImageHandler: builds
a Markdown section body the caller (message_context.py) can drop under its
own header.

Requires a running SearXNG instance with the JSON format enabled in
settings.yml (search: formats: [html, json]) - see README for setup.

Usage:
    from search_tool import SearchTool

    search_tool = SearchTool(base_url="http://localhost:6768")
    results_markdown = await search_tool.search("latest news on X")
"""

import aiohttp


class SearchTool:
    """Queries a local SearXNG instance and formats results as Markdown."""

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        max_results: int = 5,
        fetch_timeout: int = 10,
        snippet_char_limit: int = 300,
    ):
        self.base_url = base_url.rstrip("/")
        self.max_results = max_results
        self.fetch_timeout = fetch_timeout
        self.snippet_char_limit = snippet_char_limit

    # ---------- internal helpers ----------

    def _format_result(self, result: dict) -> str:
        title = result.get("title", "Untitled")
        url = result.get("url", "")
        content = result.get("content", "") or ""

        snippet = content[: self.snippet_char_limit]
        if len(content) > self.snippet_char_limit:
            snippet += "..."

        return f'- [{title}]({url}) — "{snippet}"' if snippet else f"- [{title}]({url})"

    # ---------- public API ----------

    async def raw_search(self, query: str) -> list[dict]:
        """Query SearXNG and return the raw list of result dicts (title, url,
        content, etc.) as given by its JSON API. Returns [] on any failure."""
        params = {"q": query, "format": "json"}

        try:
            timeout = aiohttp.ClientTimeout(total=self.fetch_timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self.base_url}/search", params=params) as resp:
                    if resp.status != 200:
                        return []
                    data = await resp.json()
        except Exception:
            return []

        return data.get("results", [])[: self.max_results]

    async def search(self, query: str) -> str:
        """Run a search and return a Markdown bullet-list section body
        (no header - caller adds e.g. '### Search') summarizing the top
        results: title, link, and a short content snippet for each."""
        results = await self.raw_search(query)

        if not results:
            return "_No results found._"

        bullets = [self._format_result(r) for r in results]
        return "\n".join(bullets)