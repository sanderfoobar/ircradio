# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2021, dsc@xmr.pm

import functools
import os
import logging
import json
import re
from typing import Optional, List
from datetime import datetime, timedelta
from dataclasses import dataclass

import mutagen
from mutagen.oggvorbis import OggVorbisInfo, OggVCommentDict
import aiofiles
from peewee import SqliteDatabase, SQL
import peewee as pw
from quart import current_app

from ircradio.youtube import YouTube
import settings

db = SqliteDatabase(f"{settings.cwd}/data/db.sqlite3")


class Ban(pw.Model):
    id = pw.AutoField()
    utube_id_or_nick = pw.CharField(index=True)

    class Meta:
        database = db


@dataclass
class SongMeta:
    title: Optional[str] = None
    description: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    album_cover: Optional[str] = None  # path to image
    bitrate: Optional[int] = None
    length: Optional[float] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    mime: Optional[str] = None


class Song(pw.Model):
    id = pw.AutoField()
    date_added: datetime = pw.DateTimeField(default=datetime.now)

    title: str = pw.CharField(index=True)
    utube_id: str = pw.CharField(index=True, unique=True)
    added_by: str = pw.CharField(index=True, constraints=[SQL('COLLATE NOCASE')])  # ILIKE index
    duration: int = pw.IntegerField()  # seconds
    karma: int = pw.IntegerField(default=5, index=True)
    banned: bool = pw.BooleanField(default=False)

    meta: SongMeta = None  # directly from file (exif) or metadata json
    path: Optional[str] = None
    remaining: int = None  # liquidsoap playing status in seconds

    @property
    def to_json(self):
        return {
            "title": self.title,
            "utube_id": self.utube_id,
            "added_by": self.added_by,
            "duration": self.duration,
            "karma": self.karma,
            "banned": self.banned
        }

    @staticmethod
    def delete_song(utube_id: str) -> bool:
        from ircradio.factory import app
        try:
            fn = f"{settings.dir_music}/{utube_id}.ogg"
            Song.delete().where(Song.utube_id == utube_id).execute()
            os.remove(fn)
        except Exception as ex:
            app.logger.error(f"{ex}")
            return False

    @staticmethod
    def search(needle: str, min_chars=3) -> List['Song']:
        needle = needle.replace("%", "")
        if len(needle) < min_chars:
            raise Exception("Search too short. Wow. More typing plz. Much effort.")

        if YouTube.is_valid_uid(needle):
            try:
                song = Song.select().filter(Song.utube_id == needle).get()
                return [song]
            except:
                pass

        try:
            q = Song.select().filter(Song.title ** f"%{needle}%")
            return [s for s in q]
        except:
            pass

        return []

    @staticmethod
    def by_uid(uid: str) -> Optional['Song']:
        try:
            return Song.select().filter(Song.utube_id == uid).get()
        except:
            pass

    @classmethod
    async def from_filepath(cls, path: str) -> 'Song':
        if not os.path.exists(path):
            raise Exception("filepath does not exist")

        # try to detect youtube id in filename
        basename = os.path.splitext(os.path.basename(path))[0]

        if YouTube.is_valid_uid(basename):
            try:
                song = cls.select().where(Song.utube_id == basename).get()
            except Exception as ex:
                song = Song()
        else:
            song = Song()

        # scan for metadata
        await song.scan(path)
        return song

    async def scan(self, path: str = None):
        # update with metadata, etc.
        if path is None:
            path = self.filepath

        if not os.path.exists(path):
            raise Exception(f"filepath {path} does not exist")
        basename = os.path.splitext(os.path.basename(path))[0]

        self.meta = SongMeta()
        self.path = path

        # EXIF direct
        from ircradio.utils import mutagen_file
        _m = await mutagen_file(path)

        if _m:
            if _m.info:
                self.meta.channels = _m.info.channels
                self.meta.length = int(_m.info.length)
                if hasattr(_m.info, 'bitrate'):
                    self.meta.bitrate = _m.info.bitrate
                if hasattr(_m.info, 'sample_rate'):
                    self.meta.sample_rate = _m.info.sample_rate
            if _m.tags:
                if hasattr(_m.tags, 'as_dict'):
                    _tags = _m.tags.as_dict()
                else:
                    try:
                        _tags = {k: v for k, v in _m.tags.items()}
                    except:
                        _tags = {}

                for k, v in _tags.items():
                    if isinstance(v, list):
                        v = v[0]
                    elif isinstance(v, (str, int, float)):
                        pass
                    else:
                        continue

                    if k in ["title", "description", "language", "date", "purl", "artist"]:
                        if hasattr(self.meta, k):
                            setattr(self.meta, k, v)

        # yt-dlp metadata json file
        fn_utube_meta = os.path.join(settings.dir_meta, f"{basename}.info.json")
        utube_meta = {}
        if os.path.exists(fn_utube_meta):
            async with aiofiles.open(fn_utube_meta, mode="r") as f:
                try:
                    utube_meta = json.loads(await f.read())
                except Exception as ex:
                    logging.error(f"could not parse {fn_utube_meta}, {ex}")

        if utube_meta:
            # utube_meta file does not have anything we care about
            pass

        if not self.title and not self.meta.title:
            # just adopt filename
            self.title = os.path.basename(self.path)

        return self

    @property
    def title_for_real_nocap(self):
        if self.title:
            return self.title
        if self.meta.title:
            return self.meta.title
        return "unknown!"

    @property
    def title_cleaned(self):
        _title = self.title_for_real_nocap
        _title = re.sub(r"\(official\)", "", _title, flags=re.IGNORECASE)
        _title = re.sub(r"\(official \w+\)", "", _title, flags=re.IGNORECASE)
        _title = _title.replace(" - Topic - ", "")
        return _title

    @property
    def filepath(self):
        """Absolute"""
        return os.path.join(settings.dir_music, f"{self.utube_id}.ogg")

    @property
    def filepath_noext(self):
        """Absolute filepath without extension ... maybe"""
        try:
            return os.path.splitext(self.filepath)[0]
        except:
            return self.filepath

    def image(self) -> Optional[str]:
        dirname = os.path.dirname(self.path)
        basename = os.path.basename(self.path)
        name, ext = os.path.splitext(basename)

        for _dirname in [dirname, settings.dir_meta]:
            for _ext in ["", ext]:
                meta_json = os.path.join(_dirname, name + _ext + ".info.json")
                if os.path.exists(meta_json):
                    try:
                        f = open(meta_json, "r")
                        data = f.read()
                        f.close()

                        blob = json.loads(data)
                        if "thumbnails" in blob and isinstance(blob['thumbnails'], list):
                            thumbs = list(sorted(blob['thumbnails'], key=lambda k: int(k['id']), reverse=True))
                            image_id = thumbs[0]['id']

                            for sep in ['.', "_"]:
                                _fn = os.path.join(_dirname, name + _ext + f"{sep}{image_id}")
                                for img_ext in ['webp', 'jpg', 'jpeg', 'png']:
                                    _fn_full = f"{_fn}.{img_ext}"
                                    if os.path.exists(_fn_full):
                                        return os.path.basename(_fn_full)
                    except Exception as ex:
                        logging.error(f"could not parse {meta_json}, {ex}")

    def time_status(self) -> Optional[tuple]:
        if not self.remaining:
            return

        duration = self.duration
        if not duration:
            if self.meta.length:
                duration = int(self.meta.length)
        if not duration:
            return

        _ = lambda k: timedelta(seconds=k)
        a = _(duration - self.remaining)
        b = _(duration)
        return a, b

    def time_status_str(self) -> Optional[str]:
        _status = self.time_status()
        if not _status:
            return

        a, b = map(str, _status)
        if a.startswith("0:") and \
                b.startswith("0:"):
            a = a[2:]
            b = b[2:]
        return f"({a}/{b})"

    class Meta:
        database = db
