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
from search_tools import SearchTool
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
_search_tool: SearchTool = None

async def search_web(query: str) -> str:
    """Search the web for current, up-to-date information. Use this when a
    question needs information that might have changed recently, or that
    you're not confident about from memory - news, current events, recent
    releases, prices, or anything time-sensitive.
 
    Args:
        query: The search query to look up
 
    Returns:
        str: Markdown-formatted search results (titles, links, and snippets)
    """
    return await _search_tool.search(query)

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
    
    AVAILABLE_TOOLS = {}
    AVAILABLE_TOOLS["search_web"] = search_web

    _history[channel_id].append({"role": "user", "content": content_message})
 
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + list(_history[channel_id])
 
    while True:
        response = await _ollamaClient.chat(
            model=MODEL,
            messages=messages,
            tools=[search_web],
        )
        
        if not response["message"].get("tool_calls"):
            reply = strip_leaked_markup(response["message"]["content"])
            _history[channel_id].append({"role": "assistant", "content": reply})
            return reply
 
        # Model wants to search - append its request, run the tool(s),
        # append the result(s), then loop back so it can answer for real
        messages.append(response["message"])
 
        for tool_call in response["message"]["tool_calls"]:
            function_name = tool_call["function"]["name"]
            function_to_call = AVAILABLE_TOOLS.get(function_name)

            print(f"Looking for: {repr(function_name)}")
            print(f"Available keys: {list(AVAILABLE_TOOLS.keys())}")

            if function_to_call is None:
                result = f"[error: unknown tool '{function_name}']"
            else:
                try:
                    result = await function_to_call(**tool_call["function"]["arguments"])  # The error is at the search_tools.py

                except Exception as e:
                    result = f"[error running {function_name}: {e}]"
            header = "### Search Results" +result
            messages.append({
                "role": "tool",
                "content": header,
                "tool_name": function_name,
            })

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
    global _search_tool

    load_dotenv()
    loadJson()
    
    print(os.getenv("SYSTEM_PROMPT"))
    print(os.getenv("VISION_MODEL_NAME"))
    print(os.getenv("SEARXNG_LOCALHOST"))

    _search_tool = SearchTool(base_url=os.getenv("SEARXNG_LOCALHOST"))
    _historyHandler = HistoryHandler(max_len=int(os.getenv("HISTORY_LIMIT")), directory=os.getenv("CHAT_HISTORY"))
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