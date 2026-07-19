"""
image_tools.py

Standalone image-handling tool for the Discord/Ollama agent.
Downloads images from Discord attachments (and image URLs found in message
text), then sends them to a local vision model via Ollama to get a text
description, so the main text model can "see" them through that description.

Usage:
    from image_tools import ImageHandler

    image_handler = ImageHandler()
    context = await image_handler.build_context(message)
"""

import re
import socket
import ipaddress
from urllib.parse import urlparse

import aiohttp
from ollama import AsyncClient

IMAGE_URL_REGEX = re.compile(
    r'https?://[^\s<>"\']+\.(?:png|jpe?g|gif|webp|bmp)(?:\?[^\s<>"\']*)?',
    re.IGNORECASE,
)

# Discord attachment content types we'll treat as images
IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"}


class ImageHandler:
    """Downloads images (Discord attachments + inline image URLs) and
    describes them using a local Ollama vision model."""

    def __init__(
        self,
        vision_model: str = "llava",
        max_bytes: int = 8_000_000,       # ~8MB cap per image
        fetch_timeout: int = 15,
        max_images_per_message: int = 3,
        prompt: str = (
            "Describe this image in one concise, precise sentence. "
            "Focus only on the most important subject and action. "
            "If text is too small or unclear to read confidently, say so instead of guessing."
        ),
    ):
        self.vision_model = vision_model
        self.max_bytes = max_bytes
        self.fetch_timeout = fetch_timeout
        self.max_images_per_message = max_images_per_message
        self.prompt = prompt
        self._client = AsyncClient()

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

    async def _download(self, url: str) -> bytes | None:
        """Download raw bytes from a URL, with SSRF and size checks. Returns
        None on any failure so callers can skip that image cleanly."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return None
        if not parsed.hostname or not self._is_safe_host(parsed.hostname):
            return None

        try:
            timeout = aiohttp.ClientTimeout(total=self.fetch_timeout)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, allow_redirects=True) as resp:
                    if resp.status != 200:
                        return None
                    data = await resp.content.read(self.max_bytes)
                    return data
        except Exception:
            return None

    def _collect_image_sources(self, message) -> tuple[list, list[str]]:
        """Returns (attachment_objects, image_urls) up to the per-message cap,
        pulling from Discord attachments first, then inline URLs in content."""
        attachments = [
            a for a in message.attachments
            if (a.content_type in IMAGE_CONTENT_TYPES if a.content_type
                else a.filename.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")))
        ]

        remaining = self.max_images_per_message - len(attachments)
        urls = IMAGE_URL_REGEX.findall(message.content)[:max(remaining, 0)]

        return attachments[: self.max_images_per_message], urls

    def _format_block(self, source: str, result: dict) -> str:
        """Render one image as a Markdown image link + its description,
        matching the Images section of the Markdown-KV message context."""
        return f"- ![image]({source}) — \"{result['description']}\""

    # ---------- public API ----------

    def _byte_info(self, image_bytes: bytes) -> dict:
        """Return basic metadata about the raw image bytes (size, and
        format/dimensions if Pillow is available) - useful for the
        Metadata section of the message context alongside the description."""
        info = {"size_bytes": len(image_bytes)}
        try:
            from io import BytesIO
            from PIL import Image
            img = Image.open(BytesIO(image_bytes))
            info["format"] = img.format
            info["dimensions"] = f"{img.width}x{img.height}"
        except Exception:
            # Pillow not installed or image couldn't be parsed - byte size alone is still useful
            pass
        return info

    async def describe_image(self, image_bytes: bytes) -> dict:
        """Send raw image bytes to the vision model and return both a short
        description and byte/format metadata about the image itself."""
        byte_info = self._byte_info(image_bytes)

        try:
            response = await self._client.chat(
                model=self.vision_model,
                messages=[{
                    "role": "user",
                    "content": self.prompt,
                    "images": [image_bytes],
                }],
            )
            description = response["message"]["content"]
        except Exception as e:
            description = f"[error describing image: {e}]"

        return {"description": description, **byte_info}

    async def build_context(self, message) -> str:
        """Find images (attachments + inline URLs) in a Discord message and
        return a formatted context block for each, matching the same style
        as messageContext() / UrlFetcher.build_context()."""
        attachments, urls = self._collect_image_sources(message)

        if not attachments and not urls:
            return ""

        blocks = []

        for attachment in attachments:
            if attachment.size > self.max_bytes:
                blocks.append(f"- {attachment.url} — [skipped: file too large]")
                continue
            image_bytes = await attachment.read()
            result = await self.describe_image(image_bytes)
            blocks.append(self._format_block(attachment.url, result))

        for url in urls:
            image_bytes = await self._download(url)
            if image_bytes is None:
                blocks.append(f"- {url} — [skipped: could not fetch]")
                continue
            result = await self.describe_image(image_bytes)
            blocks.append(self._format_block(url, result))

        return "\n".join(blocks) + "\n"