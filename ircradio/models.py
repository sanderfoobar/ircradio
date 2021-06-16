# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2021, dsc@xmr.pm

import os
import re
from typing import Optional, List
from datetime import datetime

import mutagen
from peewee import SqliteDatabase, SQL
import peewee as pw

from ircradio.youtube import YouTube
import settings

db = SqliteDatabase(f"{settings.cwd}/data/db.sqlite3")


class Ban(pw.Model):
    id = pw.AutoField()
    utube_id_or_nick = pw.CharField(index=True)

    class Meta:
        database = db

class Song(pw.Model):
    id = pw.AutoField()
    date_added = pw.DateTimeField(default=datetime.now)

    title = pw.CharField(index=True)
    utube_id = pw.CharField(index=True, unique=True)
    added_by = pw.CharField(index=True, constraints=[SQL('COLLATE NOCASE')])  # ILIKE index
    duration = pw.IntegerField()
    karma = pw.IntegerField(default=5, index=True)
    banned = pw.BooleanField(default=False)

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

    @staticmethod
    def from_filepath(filepath: str) -> Optional['Song']:
        fn = os.path.basename(filepath)
        name, ext = fn.split(".", 1)
        if not YouTube.is_valid_uid(name):
            raise Exception("invalid youtube id")
        try:
            return Song.select().filter(utube_id=name).get()
        except:
            return Song.auto_create_from_filepath(filepath)

    @staticmethod
    def auto_create_from_filepath(filepath: str) -> Optional['Song']:
        from ircradio.factory import app
        fn = os.path.basename(filepath)
        uid, ext = fn.split(".", 1)
        if not YouTube.is_valid_uid(uid):
            raise Exception("invalid youtube id")

        metadata = YouTube.metadata_from_filepath(filepath)
        if not metadata:
            return

        app.logger.info(f"auto-creating for {fn}")

        try:
            song = Song.create(
                duration=metadata['duration'],
                title=metadata['name'],
                added_by='radio',
                karma=5,
                utube_id=uid)
            return song
        except Exception as ex:
            app.logger.error(f"{ex}")
            pass

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

    class Meta:
        database = db
