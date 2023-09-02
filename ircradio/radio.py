# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2021, dsc@xmr.pm
import logging
import re
import json
import os
import socket
from typing import List, Optional, Dict
import asyncio
import sys

import settings
from ircradio.models import Song
from ircradio.station import Station
from ircradio.utils import httpget
from ircradio.youtube import YouTube


class Radio:
    @staticmethod
    async def icecast_metadata(radio: Station) -> dict:
        cache_key = f"icecast_meta_{radio.id}"

        from quart import current_app
        if current_app:  # caching only when Quart is active
            cache: SessionInterface = current_app.session_interface
            res = await cache.get(cache_key)
            if res:
                return json.loads(res)

        url = f"http://{settings.icecast2_bind_host}:{settings.icecast2_bind_port}"
        url = f"{url}/status-json.xsl"

        blob = await httpget(url, json=True)
        if not isinstance(blob, dict) or "icestats" not in blob:
            raise Exception("icecast2 metadata not dict")

        arr = blob["icestats"].get('source')
        if not isinstance(arr, list) or not arr:
            raise Exception("no metadata results #1")

        try:
            res = next(r for r in arr if radio.mount_point == r['server_name'])

            from quart import current_app
            if current_app:  # caching only when Quart is active
                cache: SessionInterface = current_app.session_interface
                await cache.set(cache_key, json.dumps(res), expiry=4)

            return res

        except Exception as ex:
            raise Exception("no metadata results #2")

    @staticmethod
    async def command(cmd: str) -> bytes:
        """via LiquidSoap control port"""
        from datetime import datetime

        if settings.debug:
            print(f"cmd: {cmd}")

        try:
            reader, writer = await asyncio.open_connection(
                settings.liquidsoap_host, settings.liquidsoap_port)
        except Exception as ex:
            raise Exception(f"error connecting to {settings.liquidsoap_host}:{settings.liquidsoap_port}: {ex}")

        writer.write(cmd.encode() + b"\n")
        await writer.drain()

        try:
            task = reader.readuntil(b"\x0d\x0aEND\x0d\x0a")
            data = await asyncio.wait_for(task, 1)
        except Exception as ex:
            logging.error(ex)
            return b""

        return data
