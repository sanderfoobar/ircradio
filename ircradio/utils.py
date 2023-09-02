# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2021, dsc@xmr.pm

from typing import List, Optional, Union
import re
import shutil
import os
import sys
import random
import time
import asyncio
from asyncio.subprocess import Process
from io import TextIOWrapper
from dataclasses import dataclass

import mutagen
import aiofiles
import aiohttp
import jinja2
from aiocache import cached, Cache
from aiocache.serializers import PickleSerializer
from jinja2 import Environment, PackageLoader, select_autoescape
from quart import current_app

import settings


class AsyncSubProcess(object):
    def __init__(self, *args, **kwargs):
        self.proc: Process = None
        self.max_buffer: int = 1000
        self.buffer = []

    @property
    async def is_running(self) -> bool:
        return self.proc and self.proc.returncode is None

    async def run(self, args: List[str], ws_type_prefix: str):
        loop = asyncio.get_event_loop()
        read_stdout, write_stdout = os.pipe()
        read_stderr, write_stderr = os.pipe()
        self.proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=write_stdout,
            stderr=write_stderr,
            cwd=settings.cwd
        )

        os.close(write_stdout)
        os.close(write_stderr)

        f_stdout = os.fdopen(read_stdout, "r")
        f_stderr = os.fdopen(read_stderr, "r")

        try:
            await asyncio.gather(
                self.consume(fd=f_stdout, _type='stdout', _type_prefix=ws_type_prefix),
                self.consume(fd=f_stderr, _type='stderr', _type_prefix=ws_type_prefix),
                self.proc.communicate()
            )
        finally:
            f_stdout.close()
            f_stderr.close()

    async def consume(self, fd: TextIOWrapper, _type: str, _type_prefix: str):
        from ircradio.factory import app
        import wow.websockets as websockets
        _type_int = 0 if _type == "stdout" else 1

        reader = asyncio.StreamReader()
        loop = asyncio.get_event_loop()
        await loop.connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader),
            fd
        )

        async for line in reader:
            line = line.strip()
            msg = line.decode(errors="ignore")
            _logger = app.logger.info if _type_int == 0 else app.logger.error
            _logger(msg)

            self.buffer.append((int(time.time()), _type_int, msg))
            if len(self.buffer) >= self.max_buffer:
                self.buffer.pop(0)

            await websockets.broadcast(
                message=line,
                message_type=f"{_type_prefix}_{_type}",
            )


async def loopyloop(secs: int, func, after_func=None):
    while True:
        result = await func()
        if after_func:
            await after_func(result)
        await asyncio.sleep(secs)


def jinja2_render(template_name: str, **data):
    loader = jinja2.FileSystemLoader(searchpath=[
        os.path.join(settings.cwd, "utils"),
        os.path.join(settings.cwd, "ircradio/templates")
    ])
    env = jinja2.Environment(loader=loader, autoescape=select_autoescape())
    template = env.get_template(template_name)
    return template.render(**data)


async def write_file(fn: str, data: Union[str, bytes], mode="w"):
    async with aiofiles.open(fn, mode=mode) as f:
        f.write(data)


def write_file_sync(fn: str, data: bytes):
    f = open(fn, "wb")
    f.write(data)
    f.close()


async def executeSQL(sql: str, params: tuple = None):
    from ircradio.factory import db
    async with db.pool.acquire() as connection:
        async with connection.transaction():
            result = connection.fetch(sql, params)
    return result


def systemd_servicefile(
        name: str, description: str, user: str, group: str,
        path_executable: str, args_executable: str, env: str = None
) -> bytes:
    template = jinja2_render(
        "acme.service.jinja2",
        name=name,
        description=description,
        user=user,
        group=group,
        env=env,
        path_executable=path_executable,
        args_executable=args_executable
    )
    return template.encode()


async def httpget(url: str, json=True, timeout: int = 5, raise_for_status=True, verify_tls=True):
    headers = {"User-Agent": random_agent()}
    opts = {"timeout": aiohttp.ClientTimeout(total=timeout)}

    async with aiohttp.ClientSession(**opts) as session:
        async with session.get(url, headers=headers, ssl=verify_tls) as response:
            if raise_for_status:
                response.raise_for_status()

            result = await response.json() if json else await response.text()
            if result is None or (isinstance(result, str) and result == ''):
                raise Exception("empty response from request")
            return result


def random_agent():
    from ircradio import user_agents
    return random.choice(user_agents)


def print_banner():
    print("""\033[91m    ▪  ▄▄▄   ▄▄· ▄▄▄   ▄▄▄· ·▄▄▄▄  ▪        
    ██ ▀▄ █·▐█ ▌▪▀▄ █·▐█ ▀█ ██▪ ██ ██ ▪     
    ▐█·▐▀▀▄ ██ ▄▄▐▀▀▄ ▄█▀▀█ ▐█· ▐█▌▐█· ▄█▀▄ 
    ▐█▌▐█•█▌▐███▌▐█•█▌▐█ ▪▐▌██. ██ ▐█▌▐█▌.▐▌
    ▀▀▀.▀  ▀·▀▀▀ .▀  ▀ ▀  ▀ ▀▀▀▀▀• ▀▀▀ ▀█▄▀▪\033[0m
    """.strip())


async def radio_update_task_run_forever():
    while True:
        sleep_secs = 15
        try:
            sleep_secs = await radio_update_task(sleep_secs)
            await asyncio.sleep(sleep_secs)
        except Exception as ex:
            current_app.logger.error(ex)
            await asyncio.sleep(sleep_secs)


async def radio_update_task(sleep_secs) -> int:
    from ircradio.factory import websocket_status_bus
    from ircradio.station import SongDataclass
    from ircradio.radio import Radio
    if len(websocket_status_bus.subscribers) >= 1:
        sleep_secs = 4

    blob = {}
    radio_stations = list(settings.radio_stations.values())
    # radio_stations = radio_stations[1:]
    radio_stations = radio_stations[:2]

    for radio_station in radio_stations:
        radio_station.song = None

        data = {
            'added_by': 'system',
            'image': None,
            'duration': None,
            'progress': None,
        }

        np = await radio_station.np()
        if np:
            listeners = await radio_station.get_listeners()
            if listeners is not None:
                radio_station.listeners = listeners

            data['title'] = np.title_cleaned
            data['karma'] = np.karma
            data['utube_id'] = np.utube_id
            data['image'] = np.image()
            data['duration'] = np.duration
            data['added_by'] = np.added_by

            time_status = np.time_status()
            if time_status:
                a, b = time_status
                pct = percentage(a.seconds, b.seconds)
                if pct >= 100:
                    pct = 100
                data['progress'] = int(pct)
                data['progress_str'] = " / ".join(map(str, time_status))

            radio_station.song = SongDataclass(**data)
            blob[radio_station.id] = radio_station

    if blob:
        await websocket_status_bus.put(blob)

    return sleep_secs


@cached(ttl=3600, cache=Cache.MEMORY,
        key_builder=lambda *args, **kw: f"mutagen_file_{args[1]}",
        serializer=PickleSerializer())
async def mutagen_file(path):
    from quart import current_app
    if current_app:
        return await current_app.sync_to_async(mutagen.File)(path)
    else:
        return mutagen.File(path)


def percentage(part, whole):
    return 100 * float(part)/float(whole)
