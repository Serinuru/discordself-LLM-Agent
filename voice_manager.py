"""
voice_manager.py

Handles:
- Auto-joining a voice channel when a user joins it, auto-leaving when it's empty
- Detecting a "play music <url>" style command in text channels and streaming
  the audio through the bot's voice connection using yt-dlp + ffmpeg
- Per-guild playlist queueing: a playlist URL queues all its entries, and
  each track auto-advances to the next when it finishes

Requirements:
    pip install PyNaCl yt-dlp
    FFmpeg must be installed and on PATH (not a pip package - a real binary)

Usage (in bot.py):
    from voice_manager import VoiceManager

    voice_manager = VoiceManager(bot)

    @bot.event
    async def on_voice_state_update(member, before, after):
        await voice_manager.handle_voice_state_update(member, before, after)

    @bot.event
    async def on_message(message):
        await bot.process_commands(message)
        await voice_manager.handle_message(message)
        # ... your existing LLM logic ...
"""

import re
import asyncio
import collections
import discord
import yt_dlp

PLAY_REGEX = re.compile(r"play\s+music\s+(https?://\S+)", re.IGNORECASE)
QUEUE_REGEX = re.compile(r"queue\s+music\s+(https?://\S+)", re.IGNORECASE)
SKIP_REGEX = re.compile(r"skip\s+music\s+(\d+)?", re.IGNORECASE)
JOIN_REGEX = re.compile(r"join\s+vc", re.IGNORECASE)
CLEAR_REGEX = re.compile(r"clear\s+music", re.IGNORECASE)
YTDLP_OPTIONS = {
    "format": "bestaudio[acodec=opus]/bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    'extract_flat': False,
    'no_color': True,
    "default_search": "auto",
    "js_runtimes": {"deno": {"path": r"C:\Users\Admin\.deno\bin\deno.exe"}},
}

YTDLP_FLAT_OPTIONS = {
    **YTDLP_OPTIONS,
    "extract_flat": "in_playlist",
    "noplaylist": False,  # must override the base True, or playlists never expand
}

BASE_RECONNECT_OPTS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"

class Track:
    """A single queued item - just the page URL until it's resolved right
    before playback (resolving lazily avoids stream URLs expiring in queue)."""

    def __init__(self, page_url: str, title: str = None):
        self.page_url = page_url
        self.title = title or page_url

class VoiceManager:
    """Manages auto-join/leave voice presence and URL audio streaming."""

    def __init__(self, bot: discord.Client):
        self.bot = bot
        self._ytdlp = yt_dlp.YoutubeDL(YTDLP_OPTIONS)

        self._ytdlp_flat = yt_dlp.YoutubeDL(YTDLP_FLAT_OPTIONS)
        self._queues: dict[int, collections.deque] = collections.defaultdict(collections.deque)

        self._notify_channel: dict[int, discord.abc.Messageable] = {}

        self._skip_requested: dict[int, int] = {}
    
    #BOT VOICE JOIN 

    async def _move_voice_channel (self, channel: discord.VoiceChannel, voice_client) -> None:
        await voice_client.move_to(channel)

    async def _join_voice_channel (self, channel: discord.VoiceChannel) -> None:
        await channel.connect()
    
    async def _Leave_voice_channel (self, channel: discord.VoiceChannel) -> None:
        await channel.disconnect()

    async def handle_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        if member.bot:
            return
        
        if after.channel != None:
        
            guild = after.channel.guild
            voice_client = guild.voice_client

            if voice_client == None:
                await self._join_voice_channel(after.channel)
            elif voice_client.channel != after.channel:
                if self._human_count(voice_client.channel) > 0:
                    return
                await self._move_voice_channel(after.channel, voice_client)
        
        if before.channel != None:
            guild = before.channel.guild
            voice_client = guild.voice_client

            if voice_client == None and voice_client.channel == before.channel:
                return
            if self._human_count(before.channel) > 0:
                return
            await self._Leave_voice_channel(before.channel)
            self._queues[guild.id].clear()

    def _human_count(self, channel: discord.VoiceChannel) -> int:
        return sum(1 for m in channel.members if not m.bot)
    
    def _skip(self, guild: discord.Guild, skip: int = 1) -> bool:
        """Explicit user-triggered skip. Marks this guild so the resulting
        _after callback knows this stop() was intentional, not an error."""
        voice_client = guild.voice_client
        if voice_client and voice_client.is_playing():
            self._skip_requested[guild.id] = skip
            voice_client.stop()
    
    async def handle_message(self, message: discord.Message) -> None:
        
        if message.author.bot:
            return False
        
        matchjoin = JOIN_REGEX.search(message.content)
        if matchjoin != None:
            await self._join_voice_channel(message.author.voice.channel)

        matchskip = SKIP_REGEX.search(message.content)
        print(matchskip)
        if matchskip != None:
            skipping = self._skip(message.guild, int(matchskip.group(1)))
        
        matchplay = PLAY_REGEX.search(message.content) if PLAY_REGEX.search(message.content) != None else QUEUE_REGEX.search(message.content)
        if not matchplay:
            return False

        url = matchplay.group(1)

        guild_id = message.guild.id
        self._notify_channel[guild_id] = message.channel

        voice_client = message.guild.voice_client
        #I seriously dont know why this is here but it atleast prevents a bug
        if voice_client is None or not voice_client.is_connected():
            if message.author.voice and message.author.voice.channel:
                voice_client = await message.author.voice.channel.connect()
            else:
                await message.channel.send("Join a voice channel first so I know where to play.")
                return False
        
        #track queue management
        try:
            # extract_flat still hits the network to enumerate playlist entries,
            # so keep it off the event loop the same way _resolve_stream is
            tracks = await asyncio.to_thread(self._expand_to_tracks, url)
        except Exception as e:
            await message.channel.send(f"Couldn't process that URL: {e}")
            return False

        if not tracks:
            await message.channel.send("No playable tracks found at that URL.")
            return False
        
        # this checks of the queue is empty/not playing
        was_idle = len(self._queues[guild_id]) == 0 and not voice_client.is_playing()
        self._queues[guild_id].extend(tracks)

        if len(tracks) > 1:
            await message.channel.send(f"Queued {len(tracks)} tracks from playlist.")
        else:
            await message.channel.send(f"Queued: {tracks[0].title}")
        
        if was_idle:
            await self._play_next(message.guild)

        return f"\n\tQueued {len(tracks)} track(s) starting with '{tracks[0].title}'"

    def _expand_to_tracks(self, url: str) -> list:
        """Resolve a URL into one or more Track objects. Uses flat extraction
        so playlists don't require resolving every entry's real media URL
        up front (that happens lazily per-track in _resolve_stream)."""
        info = self._ytdlp_flat.extract_info(url, download=False)

        if "entries" in info:
            return [
                Track(entry["url"], entry.get("title"))
                for entry in info["entries"]
                if entry.get("url")
            ]
        return [Track(info.get("webpage_url", url), info.get("title"))]

    def _resolve_stream(self, page_url: str) -> tuple:
        """Resolve a single track's page URL into a real, playable stream URL
        + headers + acodec, done lazily right before it plays."""
        info = self._ytdlp.extract_info(page_url, download=False)
        if "entries" in info:
            info = info["entries"][0]

        stream_url = info["url"]
        headers = info.get("http_headers", {})
        acodec = info.get("acodec")
        addinfo = (info.get('title'), info.get('description'))
        return stream_url, headers, acodec, addinfo

    async def _play_next(self, guild: discord.Guild, skip: int = 1) -> None:
        """Pop the next track off this guild's queue and play it. Wired up as
        the 'after' callback so tracks auto-advance."""
        if skip == None:
            print("NONE TYPE")
            skip = 1
        queue = self._queues[guild.id]
        voice_client = guild.voice_client

        if (queue == None) or (voice_client == None) or (not voice_client.is_connected()):
            return
        

        track = [queue.popleft() for _ in range(skip)][-1]

        channel = self._notify_channel.get(guild.id)

        try:
            stream_url, headers, acodec, addinfo = await asyncio.to_thread(self._resolve_stream, track.page_url)
        except Exception as e:
            if channel:
                await channel.send(f"Skipping '{track.title}' - couldn't resolve: {e}")
            await self._play_next(guild)  # skip to the one after
            return
        #streaming
        ffmpeg_options = self._build_ffmpeg_options(headers)
        codec = "copy" if acodec == "opus" else None

        source = discord.FFmpegOpusAudio(
            stream_url,
            before_options=ffmpeg_options["before_options"],
            options=ffmpeg_options["options"],
            codec=codec,
        )

        def _after(error):
            was_skip = guild.id in self._skip_requested
            skip_to = self._skip_requested.pop(guild.id, None)

            if error:
                print(f"Player error on '{track.title}': {error}")
            elif was_skip:
                print(f"Skipped '{track.title}'")
            else:
                print(f"Finished '{track.title}'")

            fut = asyncio.run_coroutine_threadsafe(self._play_next(guild, skip_to), self.bot.loop)
            try:
                fut.result()
            except Exception as e:
                print(f"Error advancing queue: {e}")
            
        voice_client.play(source, after=_after)

        if channel:
            await channel.send(f"Now playing: {track.title}")


    # STREAMING AND FFMPEG
    def _extract_stream_url(self, url: str) -> tuple[str, dict]:
        """Use yt-dlp to resolve a page URL (e.g. YouTube link) down to a
        direct, streamable audio URL that ffmpeg can consume, along with the
        HTTP headers yt-dlp used to negotiate it.

        The headers matter: googlevideo.com stream URLs are bound to the
        request context (User-Agent, etc.) that negotiated them. Requesting
        the URL with different/default headers gets a 403 from the CDN, even
        though the URL itself is valid.
        """
        info = self._ytdlp.extract_info(url, download=False)
        if "entries" in info:
            # playlists/search results nest the real entry under 'entries'
            info = info["entries"][0]

        stream_url = info["url"]
        headers = info.get("http_headers", {})
        acodec = info.get("acodec")
        addinfo = (info.get('title'), info.get('description'))
        return stream_url, headers, acodec, addinfo

    def _build_ffmpeg_options(self, headers: dict) -> dict:
        """Build ffmpeg's before_options with the exact headers yt-dlp used,
        so the CDN sees a matching request and doesn't 403 it."""
        header_str = "".join(f"{k}: {v}\r\n" for k, v in headers.items())

        before_options = BASE_RECONNECT_OPTS
        if header_str:
            # ffmpeg's -headers flag takes a single CRLF-joined header block
            before_options += f' -headers "{header_str}"'

        return {
            "before_options": before_options,
            "options": "-vn",
        }