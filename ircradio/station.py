import re, os, json, logging
from collections import OrderedDict
from typing import List, Optional
from dataclasses import dataclass

import mutagen
import aiofiles
from aiocache import cached, Cache
from aiocache.serializers import PickleSerializer

import settings
from ircradio.models import Song


@dataclass
class SongDataclass:
    title: str
    karma: int
    utube_id: str
    added_by: str
    image: Optional[str]
    duration: Optional[int]
    progress: Optional[int]  # pct
    progress_str: Optional[str] = None


@dataclass
class Station:
    id: str
    music_dir: str     # /full/path/to/music/
    mount_point: str   # wow.ogg
    request_id: str    # pberlin  (for telnet requests)
    title: str         # for webif
    description: str   # for webif
    image: str         # for webif
    listeners: int = 0

    song: Optional[SongDataclass] = None

    async def skip(self) -> bool:
        from ircradio.radio import Radio
        try:
            await Radio.command(self.telnet_cmd_skip)
            return True
        except Exception as ex:
            return False

    async def np(self) -> Optional[Song]:
        history = await self.history()
        if history:
            return history[-1]

    @cached(ttl=4, cache=Cache.MEMORY,
            key_builder=lambda *args, **kw: f"history_station_{args[1].id}",
            serializer=PickleSerializer())
    async def history(self) -> List[Song]:
        from ircradio.radio import Radio
        # 1. ask liquidsoap for history
        # 2. check database (Song.from_filepath)
        # 3. check direct file exif (Song.from_filepath)
        # 4. check .ogg.json metadata file (Song.from_filepath)
        # 5. verify the above by comparing icecast metadata

        # find a better way to get current song
        liq_filenames = []
        from ircradio.factory import NP_MAP
        np_uid = self.id
        if np_uid == "main":
            np_uid = "pmain"
        elif np_uid == "wow":
            np_uid = "pmain"

        if np_uid in NP_MAP:
            liq_filenames = [NP_MAP[np_uid]]

        liq_remaining = await Radio.command(self.telnet_cmd_remaining)
        liq_remaining = liq_remaining.decode(errors="ignore")

        remaining = None
        if re.match('\d+.\d+', liq_remaining):
            remaining = int(liq_remaining.split('.')[0])

        songs = []
        for liq_fn in liq_filenames:
            try:
                song = await Song.from_filepath(liq_fn)
                songs.append(song)
            except Exception as ex:
                logging.error(ex)

        if not songs:
            return []

        # icecast compare, silliness ahead
        meta: dict = await Radio.icecast_metadata(self)
        if meta:
            meta_title = meta.get('title')
            if meta_title and meta_title.lower() in [' ', 'unknown', 'error', 'empty', 'bleepbloopblurp']:
                meta_title = None

            if meta_title and len(songs) > 1 and songs:
                title_a = songs[-1].title_for_real_nocap.lower()
                title_b = meta_title.lower()

                if title_a not in title_b and title_b not in title_a:
                    # song detection and actual icecast metadata differ
                    logging.error(f"song detection and icecast metadata differ:\n{meta_title}\n{songs[-1].title}")
                    songs[-1].title = meta_title

        if remaining:
            songs[-1].remaining = remaining

        return songs

    async def queue_get(self) -> List[Song]:
        from ircradio.radio import Radio

        queues = await Radio.command(self.telnet_cmd_queue)
        queue_ids = re.findall(b"\d+", queues)
        if not queue_ids:
            return []

        queue_ids: List[int] = list(reversed(list(map(int, queue_ids))))  # yolo

        songs = []
        for request_id in queue_ids:
            liq_meta = await Radio.command(f"request.metadata {request_id}")
            liq_meta = liq_meta.decode(errors='none')
            liq_fn = re.findall(r"filename=\"(.*)\"", liq_meta)
            if not liq_fn:
                continue
            liq_fn = liq_fn[0]

            try:
                song = await Song.from_filepath(liq_fn)
                songs.append(song)
            except Exception as ex:
                logging.error(ex)

        # remove the now playing song from the queue
        now_playing = await self.np()
        if songs and now_playing:
            if songs[0].filepath == now_playing.filepath:
                songs = songs[1:]
        return songs

    async def queue_push(self, filepath: str) -> bool:
        from ircradio.radio import Radio
        from ircradio.factory import app

        if not os.path.exists(filepath):
            logging.error(f"file does not exist: {filepath}")
            return False

        current_queue = await self.queue_get()
        if filepath in [c.filepath for c in current_queue]:
            logging.error(f"already added to queue: {song.filepath}")
            return False

        try:
            await Radio.command(f"{self.telnet_cmd_push} {filepath}")
        except Exception as ex:
            logging.error(f"failed to push, idunno; {ex}")
            return False

        return True

    async def get_listeners(self) -> int:
        from ircradio.radio import Radio
        meta: dict = await Radio.icecast_metadata(self)
        return meta.get('listeners', 0)

    @property
    def telnet_cmd_push(self):  # push into queue
        return f"{self.request_id}.push"

    @property
    def telnet_cmd_queue(self):  # view queue_ids
        return f"{self.request_id}.queue"

    @property
    def telnet_cmd_metadata(self):
        return f"now_playing"

    @property
    def telnet_cmd_remaining(self):
        return f"{self.mount_point.replace('.', '_')}.remaining"

    @property
    def telnet_cmd_skip(self):
        return f"{self.mount_point.replace('.', '_')}.skip"

    @property
    def stream_url(self):
        return f"http://{settings.icecast2_hostname}/{self.mount_point}"
