import re
import os
import json
import collections
from datetime import datetime

class HistoryHandler:

    FILENAME_REGEX = re.compile(r"No\.(\d+)\.json$")

    def __init__(
        self,
        directory: str = "chatHistory",
        max_len: int = 100,
    ):
        self.directory = directory
        self.max_len = max_len

        os.makedirs(self.directory, exist_ok=True)
        self.today_str = datetime.now().strftime("%d-%m-%Y")
        self.history: "collections.defaultdict[int, collections.deque]" = (
            collections.defaultdict(lambda: collections.deque(maxlen=self.max_len))
        )

        self._load_and_advance()
    
    def _recent_save_file(self) -> tuple[str, int]:
        recent = 0
        recentSaveFile = None
        for filename in os.listdir(self.directory):
            match = self.FILENAME_REGEX.search(filename)
            
            if match and int(match.group(1)) >= recent:
                recentSaveFile = filename
                recent = int(match.group(1))
                
        if recent != None:
            recentFile = (recentSaveFile, recent)
        return recentFile
    
    def _path_for(self, number: int) -> str:
        return os.path.join(self.directory, f"history-{self.today_str}-No.{number}.json")
    
    def _deserialize(self, raw: dict) -> None:
        """Populate self.history from a plain dict of {channel_id: [messages]}."""
        for channel_id_str, messages in raw.items():
            channel_id = int(channel_id_str)
            self.history[channel_id] = collections.deque(messages, maxlen=self.max_len)
 
    def _serialize(self) -> dict:
        """Convert self.history into a plain JSON-safe dict."""
        return {
            str(channel_id): list(messages)
            for channel_id, messages in self.history.items()
        }
    
    def _load_and_advance(self) -> None:
        """Load the latest existing save for today (if any), then immediately
        create the next-numbered file so future saves don't clobber the old one."""
        recent_save = self._recent_save_file()
 
        if not recent_save[0]:
            # No save file
            self.current_number = 0
            return
        
        latest_number = recent_save[1]
        latest_path = os.path.join(self.directory, recent_save[0])

        with open(latest_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        self._deserialize(raw)

        self.current_number = latest_number + 1

    def save(self) -> str:
        """Write current history to disk under this run's file number.
        Call once at shutdown. Returns the path written to."""
        path = self._path_for(self.current_number)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._serialize(), f, indent=2)
        return path