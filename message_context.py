"""
message_context.py

Formats a Discord message into Markdown for LLM consumption, using:
- Markdown-KV for the metadata block (benchmarked as the most accurate
  format for LLMs reading structured key/value data - see
  https://www.improvingagents.com/blog/best-input-data-format-for-llms)
- Section ordering (Images -> Text -> Audio) matching Gemma 4's trained
  multimodal interleaving convention: image content before text, audio after.

Usage:
    from message_context import messageContext
    from url_tools import UrlFetcher
    from image_tools import ImageHandler

    url_fetcher = UrlFetcher()
    image_handler = ImageHandler()

    context = await messageContext(message, url_fetcher, image_handler)
"""


def _kv_block(fields: dict) -> str:
    """Render a dict as a Markdown-KV fenced block: 'key: value' lines,
    with empty/falsy values normalized to 'None' for consistency."""
    lines = []
    for key, value in fields.items():
        display = value if value not in (None, "", [], {}) else "None"
        lines.append(f"{key}: {display}")
    body = "\n".join(lines)
    return f"```\n{body}\n```"


async def messageContext(message, url_fetcher=None, image_handler=None) -> str:
    """Build the full Markdown context block for a single Discord message.

    url_fetcher / image_handler are optional so this still works standalone;
    pass your UrlFetcher/ImageHandler instances to populate the Text/Images
    sections with fetched page content and image descriptions.
    """

    # ---------- Images (rendered ABOVE text, per Gemma 4 convention) ----------
    images_section = "_None_"
    if image_handler is not None:
        image_context = await image_handler.build_context(message)
        if image_context.strip():
            images_section = image_context.strip()

    # ---------- Text ----------
    text_lines = [f"> {message.content}"] if message.content else ["> _(no text content)_"]

    links_section = "_None_"
    if url_fetcher is not None:
        link_context = await url_fetcher.build_context(message)
        if link_context.strip():
            links_section = link_context.strip()

    text_section = "\n".join(text_lines)

    # ---------- Audio (rendered BELOW text, per Gemma 4 convention) ----------
    # Placeholder for future voice/audio input support
    audio_section = "_None_"

    # ---------- Metadata (Markdown-KV) ----------
    reply_value = "None"
    if message.reference is not None:
        resolved = message.reference.resolved
        if resolved is not None:
            reply_value = f"{resolved.author}<{resolved.author.id}>: \"{resolved.content}\""
        else:
            reply_value = f"[unresolved reply to message id {message.reference.message_id}]"

    reactions_value = "None"
    if message.reactions:
        reactions_value = ", ".join(f"{r.emoji}x{r.count}" for r in message.reactions)

    snapshots_value = "None"
    if message.message_snapshots:
        snapshots_value = " | ".join(
            snap.content for snap in message.message_snapshots if snap.content
        )

    server_value = f"{message.guild.name}<{message.guild.id}>" if message.guild else "DM"

    metadata_fields = {
        "type": message.type,
        "created": message.created_at,
        "edited": message.edited_at if message.edited_at else None,
        "server": server_value,
        "channel": message.channel,
        "author": f"{message.author}<{message.author.id}>",
        "mentions": message.mentions,
        "role_mentions": message.role_mentions,
        "mention_everyone": message.mention_everyone,
        "reply": reply_value,
        "reactions": reactions_value,
        "forwarded_content": snapshots_value,
        "embeds": message.embeds,
        "attachments": message.attachments,
        "stickers": message.stickers,
    }
    metadata_section = _kv_block(metadata_fields)

    return (
        "## Message\n"
        "### Images\n"
        f"{images_section}\n\n"
        "### Text\n"
        f"{text_section}\n\n"
        "### Audio\n"
        f"{audio_section}\n\n"
        "### Metadata\n"
        f"{metadata_section}"
    )