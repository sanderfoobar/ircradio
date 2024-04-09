# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2021, dsc@xmr.pm

import re
import sys
import collections
from typing import List, Optional
import os
import logging
import asyncio
from asyncio import Queue

import bottom
from quart import Quart, session, redirect, url_for
from quart_keycloak import Keycloak, KeycloakAuthToken
from quart_session import Session
from asyncio_multisubscriber_queue import MultisubscriberQueue

import settings
from ircradio.radio import Radio
from ircradio.station import Station
from ircradio.utils import print_banner
from ircradio.youtube import YouTube
import ircradio.models

app = None
user_agents: Optional[List[str]] = None
websocket_status_bus = MultisubscriberQueue()
irc_message_announce_bus = MultisubscriberQueue()
websocket_status_bus_last_item: Optional[dict[str, Station]] = None
irc_bot = None
keycloak = None
NP_MAP = {}  # station, filepath
soap = Radio()


async def _setup_icecast2(app: Quart):
    global icecast2
    await icecast2.write_config()


async def _setup_database(app: Quart):
    import peewee
    models = peewee.Model.__subclasses__()
    for m in models:
        m.create_table()


async def _setup_tasks(app: Quart):
    from ircradio.utils import radio_update_task_run_forever
    asyncio.create_task(radio_update_task_run_forever())

    async def last_websocket_item_updater():
        global websocket_status_bus_last_item

        async for data in websocket_status_bus.subscribe():
            websocket_status_bus_last_item = data

    async def irc_announce_task():
        from ircradio.irc import send_message
        async for data in irc_message_announce_bus.subscribe():
            await send_message(settings.irc_channels[0], data)

    asyncio.create_task(last_websocket_item_updater())
    asyncio.create_task(irc_announce_task())
    asyncio.create_task(_now_playing_watch())


async def _setup_irc(app: Quart):
    global irc_bot
    loop = asyncio.get_event_loop()

    bottom_client = bottom.Client
    if sys.version_info.major == 3 and sys.version_info.minor >= 10:

        class Python310Client(bottom.Client):
            def __init__(self, host: str, port: int, *, encoding: str = "utf-8", ssl: bool = True,
                         loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
                """Fix 3.10 error: https://github.com/numberoverzero/bottom/issues/60"""
                super().__init__(host, port, encoding=encoding, ssl=ssl, loop=loop)
                self._events = collections.defaultdict(lambda: asyncio.Event())
        bottom_client = Python310Client

    irc_bot = bottom_client(host=settings.irc_host, port=settings.irc_port, ssl=settings.irc_ssl, loop=loop)
    from ircradio.irc import start, message_worker
    start()

    asyncio.create_task(message_worker())


async def _setup_requirements(app: Quart):
    ls_reachable = soap.liquidsoap_reachable()
    if not ls_reachable:
        raise Exception("liquidsoap is not running, please start it first")


async def _setup_cache(app: Quart):
    app.config['SESSION_TYPE'] = 'redis'
    app.config['SESSION_URI'] = settings.redis_uri
    Session(app)


async def _now_playing_watch():
    global NP_MAP
    proc = await asyncio.create_subprocess_exec(
        "journalctl", "-n15000", "-xefu", "liquidsoap",
        stdout=asyncio.subprocess.PIPE,
    )

    line = await proc.stdout.readline()
    while line:
        line = line.decode().strip()
        if '] Prepared "/' in line and ".ogg" in line:
            try:
                filename = re.findall(r"\"(.*\.ogg)\"", line)[0]
                radio = re.findall(r"\[(\w+)\:\d\]", line)[0]
                if radio == "playlist":
                    radio = "pmain"
                NP_MAP[radio] = filename
            except Exception as ex:
                print(f"_now_playing_watch: {ex}")

        line = await proc.stdout.readline()


def create_app():
    global app, soap, icecast2
    app = Quart(__name__)
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.logger.setLevel(logging.DEBUG if settings.debug else logging.INFO)
    if settings.redis_uri:
        pass

    @app.before_serving
    async def startup():
        global keycloak
        await _setup_database(app)
        await _setup_cache(app)
        await _setup_irc(app)
        await _setup_tasks(app)
        import ircradio.routes

        keycloak = Keycloak(app, **settings.openid_keycloak_config)

        @app.context_processor
        def inject_all_templates():
            return dict(settings=settings, logged_in='auth_token' in session)

        @keycloak.after_login()
        async def handle_user_login(auth_token: KeycloakAuthToken):
            user = await keycloak.user_info(auth_token.access_token)
            session['auth_token'] = user
            return redirect(url_for('root'))

        from ircradio.youtube import YouTube
        asyncio.create_task(YouTube.update_loop())
        print_banner()

    return app
