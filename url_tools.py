"""
url_tools.py

Standalone URL-fetching tool for the Discord/Ollama agent.
Encapsulates URL extraction, SSRF-safe fetching, and HTML-to-text cleanup
behind a single UrlFetcher class so it can be dropped into bot.py and
configured per-instance (e.g. different limits per permission tier later).

Output is a Markdown '### Links' section - kept separate from the message's
own '### Text' section, since fetched page content is supplementary material
about a link, not something the user actually said.

Usage:
    from url_tools import UrlFetcher

    url_fetcher = UrlFetcher()
    context = await url_fetcher.build_context(message)
"""

import re
import socket
import ipaddress
from urllib.parse import urlparse

import aiohttp
from bs4 import BeautifulSoup


class UrlFetcher:
    """Fetches and cleans text content from URLs found in Discord messages,
    with basic SSRF protection (blocks private/loopback/link-local hosts)."""

    URL_REGEX = re.compile(r'https?://[^\s<>"\']+')

    def __init__(
        self,
        max_bytes: int = 200_000,       # cap on what's actually fetched/read
        context_char_limit: int = 1_500,  # cap on what's shown to the LLM per URL
        fetch_timeout: int = 10,
        max_urls_per_message: int = 3,
        user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
    ):
        self.max_bytes = max_bytes
        self.context_char_limit = context_char_limit
        self.fetch_timeout = fetch_timeout
        self.max_urls_per_message = max_urls_per_message
        self.custom_headers = {
            "User-agent": user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive"}

    # ---------- internal helpers ----------

    def _is_safe_host(self, hostname: str) -> bool:
        """Block localhost / private / link-local addresses to prevent SSRF."""
        try:
            infos = socket.getaddrinfo(hostname, None)
        except socket.gaierror:
            return False

        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                return False
        return True

    def _extract_urls(self, content: str) -> list[str]:
        return self.URL_REGEX.findall(content)[: self.max_urls_per_message]

    def _clean_html(self, html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        return re.sub(r"\s+", " ", text).strip()

    # ---------- public API ----------

    async def fetch_url(self, url: str) -> str:
        """Fetch a single URL and return cleaned, readable text (or an error string).
        Reads/decodes up to max_bytes - callers wanting a smaller, LLM-context-sized
        snippet should use build_context(), which applies context_char_limit."""
        parsed = urlparse(url)

        if parsed.scheme not in ("http", "https"):
            return f"[skipped: unsupported scheme in {url}]"

        if not parsed.hostname or not self._is_safe_host(parsed.hostname):
            return f"[skipped: blocked host in {url}]"

        try:
            timeout = aiohttp.ClientTimeout(total=self.fetch_timeout)
            async with aiohttp.ClientSession(timeout=timeout, headers=self.custom_headers) as session:
                async with session.get(url, allow_redirects=True) as resp:
                    if resp.status != 200:
                        return f"[error: {url} returned status {resp.status}]"

                    content_type = resp.headers.get("Content-Type", "")
                    if "text/html" not in content_type and "text/plain" not in content_type:
                        return f"[skipped: unsupported content type '{content_type}' at {url}]"

                    raw = await resp.content.read(self.max_bytes)
                    body = raw.decode(errors="ignore")

        except Exception as e:
            return f"[error fetching {url}: {e}]"

        text = self._clean_html(body) if "text/html" in content_type else body
        return text[: self.max_bytes]

    async def build_context(self, message) -> str:
        """Find URLs in a Discord message and return a Markdown '### Links'
        section body (bullets only - caller adds the header), one bullet
        per URL, truncated to context_char_limit for LLM consumption."""
        urls = self._extract_urls(message.content)
        if not urls:
            return ""

        bullets = []
        for url in urls:
            content = await self.fetch_url(url)
            snippet = content[: self.context_char_limit]
            if len(content) > self.context_char_limit:
                snippet += "..."
            bullets.append(f'- [{url}]({url}) — "{snippet}"')

        return "\n".join(bullets)