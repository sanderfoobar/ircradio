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

import aiofiles
import aiohttp
import jinja2
from jinja2 import Environment, PackageLoader, select_autoescape

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


def liquidsoap_version():
    ls = shutil.which("liquidsoap")
    f = os.popen(f"{ls} --version 2>/dev/null").read()
    if not f:
        print("please install liquidsoap\n\napt install -y liquidsoap")
        sys.exit()

    f = f.lower()
    match = re.search(r"liquidsoap (\d+.\d+.\d+)", f)
    if not match:
        return
    return match.groups()[0]


def liquidsoap_check_symlink():
    msg = """
    Due to a bug you need to create this symlink:
    
    $ sudo ln -s /usr/share/liquidsoap/ /usr/share/liquidsoap/1.4.1
    
    info: https://github.com/savonet/liquidsoap/issues/1224
    """
    version = liquidsoap_version()
    if not os.path.exists(f"/usr/share/liquidsoap/{version}"):
        print(msg)
        sys.exit()


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
    from ircradio.factory import user_agents
    return random.choice(user_agents)


class Price:
    def __init__(self):
        self.usd = 0.3

    def calculate(self):
        pass

    async def wownero_usd_price_loop(self):
        while True:
            self.usd = await Price.wownero_usd_price()
            asyncio.sleep(1200)

    @staticmethod
    async def wownero_usd_price():
        url = "https://api.coingecko.com/api/v3/simple/price?ids=wownero&vs_currencies=usd"
        blob = await httpget(url, json=True)
        return blob.get('usd', 0)


def print_banner():
    print("""\033[91m    ▪  ▄▄▄   ▄▄· ▄▄▄   ▄▄▄· ·▄▄▄▄  ▪        
    ██ ▀▄ █·▐█ ▌▪▀▄ █·▐█ ▀█ ██▪ ██ ██ ▪     
    ▐█·▐▀▀▄ ██ ▄▄▐▀▀▄ ▄█▀▀█ ▐█· ▐█▌▐█· ▄█▀▄ 
    ▐█▌▐█•█▌▐███▌▐█•█▌▐█ ▪▐▌██. ██ ▐█▌▐█▌.▐▌
    ▀▀▀.▀  ▀·▀▀▀ .▀  ▀ ▀  ▀ ▀▀▀▀▀• ▀▀▀ ▀█▄▀▪\033[0m
    """.strip())
