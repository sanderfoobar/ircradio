# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2021, dsc@xmr.pm

import re
import os
import socket
from typing import List, Optional, Dict
import asyncio
import sys

import settings
from ircradio.models import Song
from ircradio.utils import httpget
from ircradio.youtube import YouTube


class Radio:
    @staticmethod
    def queue(song: Song) -> bool:
        from ircradio.factory import app
        queues = Radio.queues()
        queues_filepaths = [s.filepath for s in queues]

        if song.filepath in queues_filepaths:
            app.logger.info(f"already added to queue: {song.filepath}")
            return False

        Radio.command(f"requests.push {song.filepath}")
        return True

    @staticmethod
    def skip() -> None:
        Radio.command(f"{settings.liquidsoap_iface}.skip")

    @staticmethod
    def queues() -> Optional[List[Song]]:
        """get queued songs"""
        from ircradio.factory import app

        queues = Radio.command(f"requests.queue")
        try:
            queues = [q for q in queues.split(b"\r\n") if q != b"END" and q]
            if not queues:
                return []
            queues = [q.decode() for q in queues[0].split(b" ")]
        except Exception as ex:
            app.logger.error(str(ex))
            raise Exception("Error")

        paths = []
        for request_id in queues:
            meta = Radio.command(f"request.metadata {request_id}")
            path = Radio.filenames_from_strlist(meta.decode(errors="ignore").split("\n"))
            if path:
                paths.append(path[0])

        songs = []
        for fn in list(dict.fromkeys(paths)):
            try:
                song = Song.from_filepath(fn)
                if not song:
                    continue
                songs.append(song)
            except Exception as ex:
                app.logger.warning(f"skipping {fn}; file not found or something: {ex}")

        # remove the now playing song from the queue
        now_playing = Radio.now_playing()
        if songs and now_playing:
            if songs[0].filepath == now_playing.filepath:
                songs = songs[1:]
        return songs

    @staticmethod
    async def get_icecast_metadata() -> Optional[Dict]:
        from ircradio.factory import app
        # http://127.0.0.1:24100/status-json.xsl
        url = f"http://{settings.icecast2_bind_host}:{settings.icecast2_bind_port}"
        url = f"{url}/status-json.xsl"
        try:
            blob = await httpget(url, json=True)
            if not isinstance(blob, dict) or "icestats" not in blob:
                raise Exception("icecast2 metadata not dict")
            return blob["icestats"].get('source')
        except Exception as ex:
            app.logger.error(f"{ex}")

    @staticmethod
    def history() -> Optional[List[Song]]:
        # 0 = currently playing
        from ircradio.factory import app

        try:
            status = Radio.command(f"{settings.liquidsoap_iface}.metadata")
            status = status.decode(errors="ignore")
        except Exception as ex:
            app.logger.error(f"{ex}")
            raise Exception("failed to contact liquidsoap")

        try:
            # paths = re.findall(r"filename=\"(.*)\"", status)
            paths = Radio.filenames_from_strlist(status.split("\n"))
            # reverse, limit
            paths = paths[::-1][:5]

            songs = []
            for fn in list(dict.fromkeys(paths)):
                try:
                    song = Song.from_filepath(fn)
                    if not song:
                        continue
                    songs.append(song)
                except Exception as ex:
                    app.logger.warning(f"skipping {fn}; file not found or something: {ex}")
        except Exception as ex:
            app.logger.error(f"{ex}")
            app.logger.error(f"liquidsoap status:\n{status}")
            raise Exception("error parsing liquidsoap status")
        return songs

    @staticmethod
    def command(cmd: str) -> bytes:
        """via LiquidSoap control port"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((settings.liquidsoap_host, settings.liquidsoap_port))
        sock.sendall(cmd.encode() + b"\n")
        data = sock.recv(4096*1000)
        sock.close()
        return data

    @staticmethod
    def liquidsoap_reachable():
        from ircradio.factory import app
        try:
            Radio.command("help")
        except Exception as ex:
            app.logger.error("liquidsoap not reachable")
            return False
        return True

    @staticmethod
    def now_playing():
        try:
            now_playing = Radio.history()
            if now_playing:
                return now_playing[0]
        except:
            pass

    @staticmethod
    async def listeners():
        data: dict = await Radio.get_icecast_metadata()
        if not data:
            return 0
        return data.get('listeners', 0)

    @staticmethod
    def filenames_from_strlist(strlist: List[str]) -> List[str]:
        paths = []
        for line in strlist:
            if not line.startswith("filename"):
                continue
            line = line[10:]
            fn = line[:-1]
            if not os.path.exists(fn):
                continue
            paths.append(fn)
        return paths
