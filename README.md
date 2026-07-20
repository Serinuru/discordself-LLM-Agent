# Discord-Self Local-LLM Agent

A Discord self hosted bot using a local LLM using [Ollama](https://ollama.com) to chat, stream music, and remember conversation history separately in each channel - all hosted into your PC

## Features

 - **Chat** &mdash; replies to messages using a local Ollama model, per-channel coversation history.
 - **Images** &mdash; describes images from Discord attachments or inline URLs using a local vision model `max: 3`.
 - **Link reading** — fetches and summarizes the content of URLs posted in chat (SSRF-guarded) `max: 3`.
 - **Voice/music** — auto-joins/leaves voice channels based on occupancy; plays audio (including full playlists) from a URL via `yt-dlp` + `ffmpeg`
 - **Persistent memory** — saves conversation history to disk between runs, with incrementing dated save files `default: 15`.

## Requirements

- Python 3.10+
- [Ollama](https://ollama.com) installed and running locally
- [FFmpeg](https://ffmpeg.org) installed and available on your system `PATH`
- [Deno](https://deno.com) installed and on `PATH` (used by `yt-dlp` as a JS runtime for YouTube extraction)
- A Discord user token ([discord.py-self Docs](https://discordpy-self.readthedocs.io/en/latest/authenticating.html))

## Setup

1. **Clone the repo**
   ```bash
   git clone https://github.com/Serinuru/discordself-LLM-Agent.git
   cd discordself-LLM-Agent
   ```

2. **Install Python dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Install Ollama and pull the models you need**
   ```bash
   ollama pull gemma4       # text chat model
   ollama pull qwen3-vl     # vision/image model
   ```

4. **Install FFmpeg and Deno**, and confirm both are on `PATH`:
   ```bash
   ffmpeg -version
   deno --version
   ```

5. **Configure your environment**
   Copy the example file and fill in your own values:
   ```bash
   cp .env.example .env
   ```
   Edit `.env` with your Discord user token and any settings you want to change (model name, system prompt, history length, etc.). **Never commit your real `.env` file.**

6. **Run the bot**
   ```bash
   python main.py
   ```

## Configuration

All configuration lives in `.env` (see `.env.example` for the full list of variables). Key settings:

| Variable | Description |
|---|---|
| `MODEL_NAME` | Ollama text model to use for chat |
| `VISION_MODEL_NAME` | Ollama vison model to use for vision encoding |
| `HISTORY_LIMIT` | Number of past messages to keep per channel |
| `SYSTEM_PROMPT` | The bot's personality/behavior instructions |
| `LOCALHOST` | Ollama's local API address (default `http://localhost:11434`) |
| `DISCORD_TOKEN` | Your user token from the Discord |
| `JSON_PATH` | permissions.json.example |

## Permissions

`permissions.json` controls which guilds/channels the self bot is allowed to respond in. Structure:

```json
{
    "_comment": "Permissions config. 'id' is the Discord guild (server) ID. 'channels' lists allowed channel IDs for that guild - an EMPTY channels list means NO channels are allowed yet (channels must be explicitly added, empty does NOT mean 'all channels'). A guild not listed here at all is fully denied. 'musicBot.allowed' uses the same guild/channels structure but is checked independently - a channel can be allowed for chat without being allowed for music, or vice versa. 'directMessage' is a single true/false toggle controlling whether the bot responds to DMs at all.",
    "allowed": [
        {"id": 123456789, "channels": [111, 222]}
    ],
    "musicBot": {
        "allowed": [
            {"id": 123456789, "channels": [111]}
        ]
    },
    "directMessage": true
}
```

**Rules:**
- A guild not listed at all in `allowed` → the bot won't respond there at all.
- A guild listed with an **empty** `channels` list → no channels are allowed yet; channel IDs must be added explicitly. Empty does **not** mean "all channels."
- `musicBot.allowed` is checked **independently** from the top-level `allowed` list — a channel can be permitted for general chat but not for music playback, or vice versa.
- `directMessage` is a single top-level toggle for whether the bot responds to DMs at all (DMs have no channel-level granularity).
- `_comment` is a convention some tools use to embed documentation directly in JSON (standard JSON parsers don't treat `#`-style comments specially, so this is just a regular string key that's ignored by the loader logic, not an actual comment syntax).

## Project Structure

```
main.py              # main entry point / Discord event handling
message_context.py   # builds Markdown-formatted message context for the LLM
image_tools.py       # ImageHandler: describes images via a local vision model
url_tools.py         # UrlFetcher: fetches and summarizes linked pages
voice_manager.py     # VoiceManager: auto-join/leave + music playback/queueing
history_manager.py   # HistoryManager: persists chat history to disk
```

## Known Limitations

- Streaming audio from external URLs (e.g. YouTube) may not comply with those platforms' Terms of Service — use at your own discretion, especially in servers you don't fully control.
- Local vision models can still misdescribe or hallucinate details in complex images (e.g. dense screenshots or small text) — treat descriptions as approximate, not authoritative.
- YouTube frequently changes its extraction requirements; `yt-dlp` and its JS runtime dependency may need periodic updates.

## License

See [`LICENSE`](./LICENSE).