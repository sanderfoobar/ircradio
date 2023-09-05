# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2021, dsc@xmr.pm
import sys
from typing import List, Optional
import os
import time
import asyncio
import random

from ircradio.factory import irc_bot as bot
from ircradio.station import Station
from ircradio.youtube import YouTube
import settings
from settings import radio_stations

radio_default = radio_stations["wow"]
msg_queue = asyncio.Queue()
YT_DLP_LOCK = asyncio.Lock()
DU_LOCK = asyncio.Lock()


async def message_worker():
    from ircradio.factory import app

    while True:
        try:
            data: dict = await msg_queue.get()
            target = data['target']
            msg = data['message']
            bot.send("PRIVMSG", target=target, message=msg)
        except Exception as ex:
            app.logger.error(f"message_worker(): {ex}")
        await asyncio.sleep(0.3)


@bot.on('CLIENT_CONNECT')
async def connect(**kwargs):
    bot.send('NICK', nick=settings.irc_nick)
    bot.send('USER', user=settings.irc_nick, realname=settings.irc_realname)

    # @TODO: get rid of this nonsense after a while
    args = {"return_when": asyncio.FIRST_COMPLETED}
    if sys.version_info.major == 3 and sys.version_info.minor < 10:
        args["loop"] = bot.loop

    done, pending = await asyncio.wait(
        [bot.wait("RPL_ENDOFMOTD"), bot.wait("ERR_NOMOTD")],
        **args
    )

    # Cancel whichever waiter's event didn't come in.
    for future in pending:
        future.cancel()

    for chan in settings.irc_channels:
        if chan.startswith("#"):
            bot.send('JOIN', channel=chan)


@bot.on('PING')
def keepalive(message, **kwargs):
    bot.send('PONG', message=message)


@bot.on('client_disconnect')
def reconnect(**kwargs):
    from ircradio.factory import app
    app.logger.warning("Lost IRC server connection")
    time.sleep(3)
    bot.loop.create_task(bot.connect())
    app.logger.warning("Reconnecting to IRC server")


class Commands:
    LOOKUP = ['np', 'tune', 'boo', 'request', 'dj', 'url', 'urls',
              'skip', 'listeners', 'queue',
              'queue_user', 'pop', 'search', 'searchq', 'stats',
              'rename', 'ban', 'whoami']

    @staticmethod
    async def np(*args, target=None, nick=None, **kwargs):
        """current song"""
        radio_station = await Commands._parse_radio_station(args, target)
        if not radio_station:
            return
        song = await radio_station.np()
        if not song:
            return await send_message(target, f"Nothing is playing?!")

        np = "Now playing"
        if radio_station.id != "wow":
            np += f" @{radio_station.id}"

        message = f"{np}: {song.title_cleaned}"

        if song.id:
            message += f" (rating: {song.karma}/10; by: {song.added_by}; id: {song.utube_id})"

        time_status_str = song.time_status_str()
        if time_status_str:
            message += f" {time_status_str}"

        await send_message(target=target, message=message)

    @staticmethod
    async def tune(*args, target=None, nick=None, **kwargs):
        """upvote song, only wow supported, not mixes"""
        song = await radio_default.np()
        if not song:
            return await send_message(target, f"Nothing is playing?!")

        song.karma += 1
        song.save()

        msg = f"Rating for \"{song.title}\" is {song.karma}/10 .. PARTY ON!!!!"
        await send_message(target=target, message=msg)

    @staticmethod
    async def boo(*args, target=None, nick=None, **kwargs):
        """downvote song"""
        song = await radio_default.np()
        if not song:
            return await send_message(target, f"Nothing is playing?!")

        if song.karma >= 1:
            song.karma -= 1
            song.save()

        msg = f"Rating for \"{song.title}\" is {song.karma}/10 .. BOOO!!!!"
        await send_message(target=target, message=msg)

    @staticmethod
    async def request(*args, target=None, nick=None, **kwargs):
        """request a song by title or YouTube id"""
        from ircradio.models import Song
        if not args:
            await send_message(target=target, message="usage: !request <id>")

        needle = " ".join(args)
        try:
            songs = Song.search(needle)
        except Exception as ex:
            return await send_message(target, f"{ex}")
        if not songs:
            return await send_message(target, "Not found!")

        if len(songs) >= 2:
            random.shuffle(songs)
            await send_message(target, "Multiple found:")
            for s in songs[:8]:
                await send_message(target, f"{s.utube_id} | {s.title}")
            return

        song = songs[0]
        await radio_default.queue_push(song.filepath)
        msg = f"Added {song.title} to the queue"

        return await send_message(target, msg)

    @staticmethod
    async def search(*args, target=None, nick=None, **kwargs):
        from ircradio.models import Song
        return await Commands._search(*args, target=target, nick=nick, **kwargs)

    @staticmethod
    async def searchq(*args, target=None, nick=None, **kwargs):
        from ircradio.models import Song
        return await Commands._search(*args, target=target, nick=nick, report_quality=True, **kwargs)

    @staticmethod
    async def _search(*args, target=None, nick=None, **kwargs) -> Optional[List['Song']]:
        """search for a title"""
        from ircradio.models import Song

        if not args:
            return await send_message(target=target, message="usage: !search <id>")

        report_quality = kwargs.get('report_quality')
        needle = " ".join(args)

        # https://git.wownero.com/dsc/ircradio/issues/1
        needle_2nd = None
        if "|" in needle:
            spl = needle.split('|', 1)
            a = spl[0].strip()
            b = spl[1].strip()
            needle = a
            needle_2nd = b

        songs = Song.search(needle)
        if not songs:
            return await send_message(target, "No song(s) found!")

        if songs and needle_2nd:
            songs = [s for s in songs if s.title and needle_2nd in s.title.lower()]

        len_songs = len(songs)
        max_songs = 6
        moar = len_songs > max_songs
        if len_songs > 1:
            await send_message(target, "Multiple found:")

        random.shuffle(songs)

        for s in songs[:max_songs]:
            msg = f"{s.utube_id} | {s.title}"
            await s.scan(s.path or s.filepath)

            if report_quality and s.meta:
                if s.meta.bitrate:
                    msg += f" ({s.meta.bitrate / 1000}kbps)"
                if s.meta.channels:
                    msg += f" (channels: {s.meta.channels}) "
                if s.meta.sample_rate:
                    msg += f" (sample_rate: {s.meta.sample_rate}) "
            await send_message(target, msg)

        if moar:
            await send_message(target, "[...]")

    @staticmethod
    async def dj(*args, target=None, nick=None, **kwargs):
        """add (or remove) a YouTube ID to the default radio"""
        from ircradio.models import Song
        if not args or args[0] not in ["-", "+"]:
            return await send_message(target, "usage: dj+ <youtube_id>")

        add: bool = args[0] == "+"
        utube_id = args[1]
        if not YouTube.is_valid_uid(utube_id):
            return await send_message(target, "YouTube ID not valid.")

        if add:
            async with YT_DLP_LOCK:
                try:
                    await send_message(target, f"Scheduled download for '{utube_id}'")
                    song = await YouTube.download(utube_id, added_by=nick)
                    await send_message(target, f"'{song.title}' added")
                except Exception as ex:
                    return await send_message(target, f"Download '{utube_id}' failed; {ex}")
        else:
            try:
                Song.delete_song(utube_id)
                await send_message(target, "Press F to pay respects.")
            except Exception as ex:
                await send_message(target, f"Failed to remove {utube_id}; {ex}")

    @staticmethod
    async def skip(*args, target=None, nick=None, **kwargs):
        """skips current song"""
        from ircradio.factory import app
        radio_station = await Commands._parse_radio_station(args, target)
        if not radio_station:
            return

        # song = radio_station.np()
        # if not song:
        #     app.logger.error(f"nothing is playing?")
        #     return await send_message(target=target, message="Nothing is playing ?!")

        try:
            await radio_station.skip()
        except Exception as ex:
            app.logger.error(f"{ex}")
            return await send_message(target=target, message="Nothing is playing ?!")

        if radio_station.id == "wow":
            _type = "Song"
        else:
            _type = "Mix"

        await send_message(target, message=f"{_type} skipped. Booo! >:|")

    @staticmethod
    async def listeners(*args, target=None, nick=None, **kwargs):
        """current amount of listeners"""
        from ircradio.factory import app
        radio_station = await Commands._parse_radio_station(args, target)
        if not radio_station:
            return

        listeners = await radio_station.get_listeners()
        if listeners is None:
            return await send_message(target, f"something went wrong")
        if listeners == 0:
            await send_message(target, f"no listeners, much sad :((")

        msg = f"{listeners} client"
        if listeners >= 2:
            msg += "s"
        msg += " connected"
        return await send_message(target, msg)

    @staticmethod
    async def queue(*args, target=None, nick=None, **kwargs):
        """show currently queued tracks"""
        from ircradio.models import Song
        from ircradio.factory import app
        radio_station = await Commands._parse_radio_station(args, target)
        if not radio_station:
            return

        q: List[Song] = await radio_station.queue_get()
        if not q:
            return await send_message(target, "queue empty")

        for i, s in enumerate(q):
            await send_message(target, f"{s.utube_id} | {s.title}")
            if i >= 8:
                await send_message(target, "And some more...")

    @staticmethod
    async def rename(*args, target=None, nick=None, **kwargs):
        from ircradio.models import Song

        try:
            utube_id = args[0]
            title = " ".join(args[1:])
            if not utube_id or not title or not YouTube.is_valid_uid(utube_id):
                raise Exception("bad input")
        except:
            return await send_message(target, "usage: !rename <id> <new title>")

        try:
            song = Song.select().where(Song.utube_id == utube_id).get()
            if not song:
                raise Exception("Song not found")
        except Exception as ex:
            return await send_message(target, "Song not found.")

        if song.added_by != nick and nick not in settings.irc_admins_nicknames:
            return await send_message(target, "You may only rename your own songs.")

        try:
            Song.update(title=title).where(Song.utube_id == utube_id).execute()
        except Exception as ex:
            return await send_message(target, "Rename failure.")

        await send_message(target, "Song renamed.")

    @staticmethod
    async def queue_user(*args, target=None, nick=None, **kwargs):
        """queue random song by username"""
        from ircradio.models import Song

        added_by = args[0]
        try:
            q = Song.select().where(Song.added_by ** f"%{added_by}%")
            songs = [s for s in q]
        except:
            return await send_message(target, "No results.")

        for i in range(0, 5):
            song = random.choice(songs)
            res = await radio_default.queue(song.filepath)
            if res:
                return await send_message(target, f"A random {added_by} has appeared in the queue: {song.title}")

        await send_message(target, "queue_user exhausted!")

    @staticmethod
    async def stats(*args, target=None, nick=None, **kwargs):
        """random stats"""
        songs = 0
        try:
            from ircradio.models import db
            cursor = db.execute_sql('select count(*) from song;')
            res = cursor.fetchone()
            songs = res[0]
        except:
            pass

        async with DU_LOCK:
            disk = os.popen(f"du -h {settings.dir_music}").read().split("\t")[0]
            mixes = os.popen(f"du -h /home/radio/mixes/").read().split("\n")[-2].split("\t")[0]

        await send_message(target, f"Songs: {songs} | Mixes: {mixes} | Songs: {disk}")

    @staticmethod
    async def ban(*args, target=None, nick=None, **kwargs):
        """add (or remove) a YouTube ID ban (admins only)"""
        if nick not in settings.irc_admins_nicknames:
            await send_message(target, "You need to be an admin.")
            return

        from ircradio.models import Song, Ban
        if not args or args[0] not in ["-", "+"]:
            return await send_message(target, "usage: ban+ <youtube_id or nickname>")

        try:
            add: bool = args[0] == "+"
            arg = args[1]
        except:
            return await send_message(target, "usage: ban+ <youtube_id or nickname>")

        if add:
            Ban.create(utube_id_or_nick=arg)
        else:
            Ban.delete().where(Ban.utube_id_or_nick == arg).execute()
            await send_message(target, "Redemption")

    @staticmethod
    async def whoami(*args, target=None, nick=None, **kwargs):
        if nick in settings.irc_admins_nicknames:
            await send_message(target, "admin")
        else:
            await send_message(target, "user")

    @staticmethod
    async def url(*args, target=None, nick=None, **kwargs):
        radio_station = await Commands._parse_radio_station(args, target)
        if not radio_station:
            return

        msg = f"https://{settings.icecast2_hostname}/{radio_station.mount_point}"
        await send_message(target, msg)

    @staticmethod
    async def urls(*args, target=None, nick=None, **kwargs):
        url = f"https://{settings.icecast2_hostname}/{radio_stations['wow'].mount_point}"
        msg = f"main programming: {url}"
        await send_message(target, msg)

        msg = "mixes: "
        for _, radio_station in radio_stations.items():
            if _ == "wow":
                continue
            url = f"https://{settings.icecast2_hostname}/{radio_station.mount_point}"
            msg += f"{url} "
        await send_message(target, msg)

    @staticmethod
    async def _parse_radio_station(args, target) -> Optional[Station]:
        extras = " ".join(args).strip()
        if not extras or len(args) >= 2:
            return radio_default

        err = f", available streams: " + " ".join(radio_stations.keys())
        if not extras:
            msg = "nothing not found (?) :-P" + err
            return await send_message(target, msg)
        extra = extras.strip()
        if extra not in radio_stations:
            msg = f"station \"{extra}\" not found" + err
            return await send_message(target, msg)
        return radio_stations[extra]


@bot.on('PRIVMSG')
async def message(nick, target, message, **kwargs):
    from ircradio.factory import app
    from ircradio.models import Ban
    if nick == settings.irc_nick:
        return
    if settings.irc_ignore_pms and not target.startswith("#"):
        return

    if target == settings.irc_nick:
        target = nick

    msg = message
    if not msg.startswith(settings.irc_command_prefix):
        return

    msg = msg[len(settings.irc_command_prefix):]

    try:
        if nick not in settings.irc_admins_nicknames:
            banned = Ban.select().filter(utube_id_or_nick=nick).get()
            if banned:
                return
    except:
        pass

    data = {
        "nick": nick,
        "target": target
    }

    spl = msg.split(" ")
    cmd = spl[0].strip()
    spl = spl[1:]

    if cmd.endswith("+") or cmd.endswith("-"):
        spl.insert(0, cmd[-1])
        cmd = cmd[:-1]

    if cmd in Commands.LOOKUP and hasattr(Commands, cmd):
        attr = getattr(Commands, cmd)
        try:
            await attr(*spl, **data)
        except Exception as ex:
            app.logger.error(f"message_worker(): {ex}")


def start():
    bot.loop.create_task(bot.connect())


async def send_message(target: str, message: str):
    await msg_queue.put({"target": target, "message": message})
