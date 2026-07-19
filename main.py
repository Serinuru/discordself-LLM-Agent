import discord
import atexit
import os
import ollama
import json
import collections
import re

from ollama import AsyncClient
from dotenv import load_dotenv

from url_tools import UrlFetcher
from image_tools import ImageHandler
from history_manager import HistoryHandler
from voice_manager import VoiceManager
from message_context import messageContext

_permissionsJSON: dict = None
_historyHandler: HistoryHandler = None
_history: "collections.defaultdict[int, collections.deque]" = None 

_voice_manager:VoiceManager = None
_ollamaClient: AsyncClient = None
_url_fetcher: UrlFetcher = None
_image_handler: ImageHandler = None

def strip_leaked_markup(text: str) -> str:
    text = re.sub(r"</?blockquote>", "", text, flags=re.IGNORECASE)
    return text.strip()

def loadJson():
    global _permissionsJSON
    JSONPATH = os.getenv("JSON_PATH")
    jsonFile = open(JSONPATH, "r")
    data = json.load(jsonFile)
    _permissionsJSON = data

async def chatOllama(channel_id: int, content_message: str) -> str:
    global _ollamaClient
    global _history

    MODEL = os.getenv("MODEL_NAME") 
    SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT") 
    
    _history[channel_id].append({"role": "user", "content": content_message})

    message = [{"role": "system", "content": SYSTEM_PROMPT}] + list(_history[channel_id])
    response = await _ollamaClient.chat(model=MODEL, messages=message)
    reply = strip_leaked_markup(response["message"]["content"])
    
    _history[channel_id].append({"role": "assistant", "content": reply})
    return reply

async def vertifyGuild(message) -> bool:
    global _permissionsJSON

    if message.guild is None and _permissionsJSON["directMessage"]:
        return True
    for guild in _permissionsJSON["allowed"]:
        if (message.guild.id == guild["id"]) and (message.channel.id in guild["channels"]):
            return True

    return False
async def vertifyGuildVC(after) -> bool:
    global _permissionsJSON
    print(after)
    if after.channel == None:
        return False
    for guild in _permissionsJSON["musicBot"]["allowed"]:
        if (after.channel.guild.id == guild["id"]) and (after.channel.id in guild["channels"]):
            return True

    return False

async def playMusicChannel(message, isVC=False) -> bool:
    global _permissionsJSON
    if message.guild is None and _permissionsJSON["directMessage"]:
        return False
    for guild in _permissionsJSON["musicBot"]["allowed"]:
        if (message.guild.id == guild["id"]) and (message.channel.id in guild["channels"]):
            return True

    return False
'''
async def messageContext(message) -> str:
    # Reply context — message.reference is None unless this message is a reply,
    # so we can't touch .resolved without checking first.
    reply_text = "None"
    if message.reference is not None:
        resolved = message.reference.resolved
        if resolved is not None:
            reply_text = f"{resolved.author}<{resolved.author.id}>: \"{resolved.content}\""
        else:
            # resolved can be None if the original message was deleted or
            # too old for the cache — message.reference.message_id still exists
            reply_text = f"[unresolved reply to message id {message.reference.message_id}]"

    # Edited timestamp — None if the message was never edited
    edited_text = str(message.edited_at) if message.edited_at else "Not edited"

    # Reactions — emoji + count; does NOT include who reacted
    reactions_text = ", ".join(
        f"{r.emoji}x{r.count}" for r in message.reactions
    ) if message.reactions else "None"

    # Forwarded messages put their content in message_snapshots instead of
    # message.content, which is often empty on the forward itself
    snapshots_text = "None"
    if message.message_snapshots:
        snapshots_text = " | ".join(
            snap.content for snap in message.message_snapshots if snap.content
        )

    location = ""
    if message.guild:
        location = f"{message.guild.name}<{message.guild.id}> @ {message.channel}"
    else:
        location = f"DM @ {message.author}"

    logMessage = f"""[{location}]
{message.author}<{message.author.id}>: "{message.content}"
\tType = {message.type}
\tCreated = {message.created_at}
\tEdited = {edited_text}
\tEmbeds = {str(message.embeds)}
\tAttachments = {str(message.attachments)}
\tStickers = {str(message.stickers)}
\tMentions = {str(message.mentions)}
\tRole Mentions = {str(message.role_mentions)}
\tMention Everyone = {message.mention_everyone}
\tReply = {reply_text}
\tReactions = {reactions_text}
\tForwarded Content = {snapshots_text}
"""
    logMessage += await _url_fetcher.build_context(message)
    logMessage += await _image_handler.build_context(message)
    return logMessage
'''
class MyClient(discord.Client):
    async def on_ready(self):
        print('Logged on as', self.user)

    async def on_message(self, message):

        # only respond to ourselves
        vertify = await vertifyGuild(message)
        playMusic = await playMusicChannel(message)
        if not vertify or message.author == self.user:
            return

        context = await messageContext(message, url_fetcher=_url_fetcher, image_handler=_image_handler)
        print(context)

        if playMusic:
            check = await _voice_manager.handle_message(message)
            
            if check != False:
                pass
                #logMessage += f"\n\tYour Action = You are currently playing music in a discord VC. Reply with something related to the music <\"{check}\">(IGNORE THIS IF IT'S NOT THE FIRST MESSAGE)" 


        async with message.channel.typing():
            try:
                reply = await chatOllama(int(message.channel.id), context)
            except Exception as e:
                reply = f"Error talking to local LLM: {e}"
 
        # Discord has a 2000 char limit per message
        for chunk in [reply[i:i + 1900] for i in range(0, len(reply), 1900)]:
            await message.channel.send(chunk)
    async def on_voice_state_update(self, member, before, after):
        vertifyAfter = await vertifyGuildVC(after)
        vertifyBefore = await vertifyGuildVC(before)
        if (vertifyAfter or vertifyBefore):
            await _voice_manager.handle_voice_state_update(member, before, after)


def main():
    global _image_handler
    global _url_fetcher
    global _ollamaClient
    global _historyHandler
    global _history
    global _voice_manager

    load_dotenv()
    loadJson()
    print(os.getenv("SYSTEM_PROMPT"))
    print(os.getenv("VISION_MODEL_NAME"))
    
    

    _historyHandler = HistoryHandler(max_len=int(os.getenv("HISTORY_LIMIT")))
    _history = _historyHandler.history
    _ollamaClient = AsyncClient(host=os.getenv("LOCALHOST"))
    _image_handler = ImageHandler(vision_model=os.getenv("VISION_MODEL_NAME"))
    _url_fetcher = UrlFetcher()

    client = MyClient()

    _voice_manager=VoiceManager(client)
    client.run(os.getenv("DISCORD_TOKEN"))

    atexit.register(_historyHandler.save)

if __name__ == '__main__':
    main()