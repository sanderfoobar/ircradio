# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2021, dsc@xmr.pm

import json
import os
import sys
import asyncio
import re
from typing import Optional

import settings


class YouTube:
    @staticmethod
    async def download(utube_id: str, added_by: str) -> Optional['Song']:
        from ircradio.factory import app
        from ircradio.models import Song

        output = f"{settings.dir_music}/{utube_id}.ogg"
        song = Song.by_uid(utube_id)
        if song:
            if not os.path.exists(output):
                # exists in db but not on disk; remove from db
                Song.delete().where(Song.utube_id == utube_id).execute()
            else:
                raise Exception("Song already exists.")

        if os.path.exists(output):
            song = Song.by_uid(utube_id)
            if not song:
                # exists on disk but not in db; add to db
                return Song.from_filepath(output)

            raise Exception("Song already exists.")

        try:
            proc = await asyncio.create_subprocess_exec(
                *["yt-dlp",
                    "--add-metadata",
                    "--write-all-thumbnails",
                    "--write-info-json",
                    "-f", "bestaudio",
                    "--max-filesize", "30M",
                    "--extract-audio",
                    "--audio-format", "vorbis",
                    "-o", f"{settings.dir_music}/%(id)s.ogg",
                    f"https://www.youtube.com/watch?v={utube_id}"],
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            result = await proc.communicate()
            result = result[0].decode()
            if "100%" not in result:
                raise Exception("download did not complete")
        except Exception as ex:
            msg = f"download failed: {ex}"
            app.logger.error(msg)
            raise Exception(msg)

        try:
            metadata = YouTube.metadata_from_filepath(output)
            if not metadata:
                raise Exception("failed to fetch metadata")

            if metadata['duration'] > settings.liquidsoap_max_song_duration:
                Song.delete_song(utube_id)
                raise Exception(f"Song exceeded duration of {settings.liquidsoap_max_song_duration} seconds")

            song = Song.create(
                duration=metadata['duration'],
                title=metadata['name'],
                added_by=added_by,
                karma=5,
                utube_id=utube_id)
            return song
        except Exception as ex:
            app.logger.error(f"{ex}")
            raise

    @staticmethod
    def metadata_from_filepath(filepath: str):
        from ircradio.factory import app
        import mutagen

        try:
            metadata = mutagen.File(filepath)
        except Exception as ex:
            app.logger.error(f"mutagen failure on {filepath}")
            return

        try:
            duration = metadata.info.length
        except:
            duration = 0

        artist = metadata.tags.get('artist')
        if artist:
            artist = artist[0]
        title = metadata.tags.get('title')
        if title:
            title = title[0]
        if not artist or not title:
            # try .info.json
            path_info = f"{filepath}.info.json"
            if os.path.exists(path_info):
                try:
                    blob = json.load(open(path_info,))
                    artist = blob.get('artist')
                    title = blob.get('title')
                    duration = blob.get('duration', 0)
                except:
                    pass
            else:
                artist = 'Unknown'
                title = 'Unknown'
                app.logger.warning(f"could not detect artist/title from metadata for {filepath}")

        return {
            "name": f"{artist} - {title}",
            "data": metadata,
            "duration": duration,
            "path": filepath
        }

    @staticmethod
    async def update_loop():
        while True:
            await YouTube.update()
            await asyncio.sleep(3600)

    @staticmethod
    async def update():
        pip_path = os.path.join(os.path.dirname(sys.executable), "pip")
        proc = await asyncio.create_subprocess_exec(
            *[sys.executable, pip_path, "install", "--upgrade", "yt-dlp"],
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        return stdout.decode()

    @staticmethod
    async def update_task():
        while True:
            await YouTube.update()
            await asyncio.sleep(3600)

    @staticmethod
    def is_valid_uid(uid: str) -> bool:
        return re.match(settings.re_youtube, uid) is not None
