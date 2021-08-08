# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2021, dsc@xmr.pm

from datetime import datetime
from typing import Tuple, Optional
from quart import request, render_template, abort, jsonify
import asyncio
import json

import settings
from ircradio.factory import app
from ircradio.radio import Radio


@app.route("/")
async def root():
    return await render_template("index.html", settings=settings)


history_cache: Optional[Tuple] = None


@app.route("/history.txt")
async def history():
    global history_cache
    now = datetime.now()
    if history_cache:
        if (now - history_cache[0]).total_seconds() <= 5:
            print("from cache")
            return history_cache[1]

    history = Radio.history()
    if not history:
        return "no history"

    data = ""
    for i, s in enumerate(history[:10]):
        data += f"{i+1}) <a target=\"_blank\" href=\"https://www.youtube.com/watch?v={s.utube_id}\">{s.utube_id}</a>; {s.title} <br>"

    history_cache = [now, data]
    return data


@app.route("/search")
async def search():
    # search json api endpoint
    # e.g: /search?name=test&limit=5&offset=0
    if not settings.enable_search_route:
        abort(404)

    from ircradio.models import Song
    name = request.args.get("name")
    limit = request.args.get("limit", '20')
    offset = request.args.get("offset", '0')

    try:
        limit = int(limit)
        offset = int(offset)
    except:
        limit = 50
        offset = 0

    if not name or len(name) <= 2:
        abort(404)

    if limit > 50:
        limit = 50

    name = f"%{name}%"

    try:
        q = Song.select()
        q = q.where((Song.added_by ** name) | (Song.title ** name))
        q = q.order_by(Song.date_added.desc())
        q = q.limit(limit).offset(offset)
        results = [{
            "added_by": s.added_by,
            "karma": s.karma,
            "id": s.id,
            "title": s.title,
            "utube_id": s.utube_id,
            "date_added": s.date_added.strftime("%Y-%m-%d")
        } for s in q]
    except:
        return jsonify([])

    return jsonify(results)


@app.route("/library")
async def user_library():
    from ircradio.models import Song
    name = request.args.get("name")
    if not name:
        abort(404)

    try:
        by_date = Song.select().filter(Song.added_by == name)\
            .order_by(Song.date_added.desc())
    except:
        by_date = []

    if not by_date:
        abort(404)

    try:
        by_karma = Song.select().filter(Song.added_by == name)\
            .order_by(Song.karma.desc())
    except:
        by_karma = []

    return await render_template("library.html", name=name, by_date=by_date, by_karma=by_karma)


@app.websocket("/ws")
+async def np():
    while True:
        """get current song from history"""
        history = Radio.history()
        val = ""
        if not history:
            val = f"Nothing is playing?!"
        else:
            song = history[0]
            val = song.title

        data = json.dumps({"now_playing": val})

        await websocket.send(f"{data}")
        await asyncio.sleep(5)
