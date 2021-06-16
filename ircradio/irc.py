# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2021, dsc@xmr.pm

from typing import List, Optional
import os
import time
import asyncio
import random

from ircradio.factory import irc_bot as bot
from ircradio.radio import Radio
from ircradio.youtube import YouTube
import settings


msg_queue = asyncio.Queue()


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

    # Don't try to join channels until server sent MOTD
    done, pending = await asyncio.wait(
        [bot.wait("RPL_ENDOFMOTD"), bot.wait("ERR_NOMOTD")],
        loop=bot.loop,
        return_when=asyncio.FIRST_COMPLETED
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
    LOOKUP = ['np', 'tune', 'boo', 'request', 'dj',
              'skip', 'listeners', 'queue',
              'queue_user', 'pop', 'search', 'stats',
              'rename', 'ban', 'whoami']

    @staticmethod
    async def np(*args, target=None, nick=None, **kwargs):
        """current song"""
        history = Radio.history()
        if not history:
            return await send_message(target, f"Nothing is playing?!")
        song = history[0]

        np = f"Now playing: {song.title} (rating: {song.karma}/10; submitter: {song.added_by}; id: {song.utube_id})"
        await send_message(target=target, message=np)

    @staticmethod
    async def tune(*args, target=None, nick=None, **kwargs):
        """upvote song"""
        history = Radio.history()
        if not history:
            return await send_message(target, f"Nothing is playing?!")
        song = history[0]

        if song.karma <= 9:
            song.karma += 1
            song.save()

        msg = f"Rating for \"{song.title}\" is {song.karma}/10 .. PARTY ON!!!!"
        await send_message(target=target, message=msg)

    @staticmethod
    async def boo(*args, target=None, nick=None, **kwargs):
        """downvote song"""
        history = Radio.history()
        if not history:
            return await send_message(target, f"Nothing is playing?!")
        song = history[0]

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
            send_message(target=target, message="usage: !request <id>")

        needle = " ".join(args)
        songs = Song.search(needle)
        if not songs:
            return await send_message(target, "Not found!")

        if len(songs) >= 2:
            random.shuffle(songs)
            await send_message(target, "Multiple found:")
            for s in songs[:4]:
                await send_message(target, f"{s.utube_id} | {s.title}")
            return

        song = songs[0]
        msg = f"Added {song.title} to the queue"
        Radio.queue(song)
        return await send_message(target, msg)

    @staticmethod
    async def search(*args, target=None, nick=None, **kwargs):
        """search for a title"""
        from ircradio.models import Song

        if not args:
            return await send_message(target=target, message="usage: !search <id>")

        needle = " ".join(args)
        songs = Song.search(needle)
        if not songs:
            return await send_message(target, "No song(s) found!")

        if len(songs) == 1:
            song = songs[0]
            await send_message(target, f"{song.utube_id} | {song.title}")
        else:
            random.shuffle(songs)
            await send_message(target, "Multiple found:")
            for s in songs[:4]:
                await send_message(target, f"{s.utube_id} | {s.title}")

    @staticmethod
    async def dj(*args, target=None, nick=None, **kwargs):
        """add (or remove) a YouTube ID to the radiostream"""
        from ircradio.models import Song
        if not args or args[0] not in ["-", "+"]:
            return await send_message(target, "usage: dj+ <youtube_id>")

        add: bool = args[0] == "+"
        utube_id = args[1]
        if not YouTube.is_valid_uid(utube_id):
            return await send_message(target, "YouTube ID not valid.")

        if add:
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

        try:
            Radio.skip()
        except Exception as ex:
            app.logger.error(f"{ex}")
            return await send_message(target=target, message="Error")

        await send_message(target, message="Song skipped. Booo! >:|")

    @staticmethod
    async def listeners(*args, target=None, nick=None, **kwargs):
        """current amount of listeners"""
        from ircradio.factory import app
        try:
            listeners = await Radio.listeners()
            if listeners:
                msg = f"{listeners} client"
                if listeners >= 2:
                    msg += "s"
                msg += " connected"
                return await send_message(target, msg)
            return await send_message(target, f"no listeners, much sad :((")
        except Exception as ex:
            app.logger.error(f"{ex}")
            await send_message(target=target, message="Error")

    @staticmethod
    async def queue(*args, target=None, nick=None, **kwargs):
        """show currently queued tracks"""
        from ircradio.models import Song
        q: List[Song] = Radio.queues()
        if not q:
            return await send_message(target, "queue empty")

        for i, s in enumerate(q):
            await send_message(target, f"{s.utube_id} | {s.title}")
            if i >= 12:
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

            if Radio.queue(song):
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

        disk = os.popen(f"du -h {settings.dir_music}").read().split("\t")[0]
        await send_message(target, f"Songs: {songs} | Disk: {disk}")

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
    if msg.startswith(settings.irc_command_prefix):
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
            pass


def start():
    bot.loop.create_task(bot.connect())


async def send_message(target: str, message: str):
    await msg_queue.put({"target": target, "message": message})
