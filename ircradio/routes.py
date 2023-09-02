# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2021, dsc@xmr.pm

import os, re, dataclasses, random
from datetime import datetime
from typing import Tuple, Optional
from quart import request, render_template, abort, jsonify, send_from_directory, current_app, websocket, redirect, session, url_for
import asyncio
import json

import settings
from ircradio.factory import app
from ircradio.radio import Radio


@app.route("/")
async def root():
    return await render_template("index.html", settings=settings, radio_stations=settings.radio_stations.values())


@app.route("/login")
async def login():
    from ircradio.factory import keycloak
    if 'auth_token' not in session:
        return redirect(url_for(keycloak.endpoint_name_login))
    return redirect('root')


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
    from ircradio.factory import keycloak
    if 'auth_token' not in session:
        return redirect(url_for(keycloak.endpoint_name_login))

    from ircradio.models import Song
    name = request.args.get("name")
    if not name:
        return await render_template('user.html')

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

    return await render_template("user_library.html", name=name, by_date=by_date, by_karma=by_karma)


@app.route("/request")
async def request_song():
    from ircradio.factory import keycloak
    if 'auth_token' not in session:
        return redirect(url_for(keycloak.endpoint_name_login))
    return await render_template('request.html')


@app.route('/api/songs')
async def api_songs():
    from ircradio.factory import keycloak
    from ircradio.models import Song, db
    if 'auth_token' not in session:
        return abort(403)

    q = """
        SELECT title, utube_id, added_by, karma 
        FROM song 
        WHERE
        ORDER BY date_added DESC
        LIMIT ? OFFSET ?;
    """

    limit = int(request.args.get('limit', 150))
    offset = int(request.args.get('offset', 0))
    search = request.args.get('search', '')
    sort_by = request.args.get('sort')
    order = request.args.get('order', 'DESC')
    if order.lower() in ['desc', 'asc']:
        order = "desc" if order == "asc" else "desc"  # yolo
        q = q.replace('DESC', order)
    if sort_by == "karma":
        q = q.replace('date_added', 'karma')

    args = [limit, offset]

    if isinstance(search, str):
        search = search[:8]
        search = search.replace('%', '')
        args.insert(0, f"%{search}%")
        q = q.replace('WHERE', f'WHERE title LIKE ?')
    else:
        q = q.replace('WHERE', f'')

    songs = []
    cursor = db.execute_sql(q, tuple(args))  # no sqli for all the naughty people!!
    for row in cursor.fetchall():
        songs.append({'title': row[0], 'uid': row[1], 'added_by': row[2], 'karma': row[3]})

    return jsonify(songs)


@app.route('/api/request/<path:utube_id>')
async def api_request(utube_id: str = None):
    from ircradio.models import Song
    from ircradio.factory import irc_message_announce_bus
    if not utube_id:
        return abort(500)
    if 'auth_token' not in session:
        return abort(403)
    user = session['auth_token']
    username = user.get('preferred_username')

    try:
        song = Song.select().filter(Song.utube_id == utube_id).get()
    except Exception as ex:
        return abort(404)

    radio_default = settings.radio_stations['wow']
    await radio_default.queue_push(song.path or song.filepath)

    msg = f"{username} added {song.title} to the queue via webif"
    await irc_message_announce_bus.put(msg)

    return jsonify({})


@app.route('/api/boo/<path:radio_id>')
async def api_boo(radio_id: str):
    from ircradio.models import Song
    from ircradio.factory import irc_message_announce_bus
    if not radio_id or radio_id not in settings.radio_stations:
        return abort(500)
    if 'auth_token' not in session:
        return abort(403)
    user = session['auth_token']
    username = user.get('preferred_username')

    # throttling
    cache_key = f"throttle_api_boo_{username}"
    res = await current_app.session_interface.get(cache_key)
    if res:
        return jsonify({})  # silently fail

    radio_default = settings.radio_stations['wow']
    song = await radio_default.np()
    if not song:
        current_app.logger.error(f"Nothing is playing?!")
        return abort(500)

    if song.karma >= 1:
        song.karma -= 1
        song.save()

    # set cache
    await current_app.session_interface.set(cache_key, b'1', 15)

    hates = ['throwing shade', 'hating', 'boo\'ing', 'throwing tomatoes', 'flipping tables', 'raging']
    msg = f"{username} {random.choice(hates)} from webif .. \"{song.title}\" is now {song.karma}/10 .. BOOO!!!!"
    await irc_message_announce_bus.put(msg)
    return jsonify({'msg': msg})


@app.route('/api/tune/<path:radio_id>')
async def api_tune(radio_id: str):
    from ircradio.models import Song
    from ircradio.factory import irc_message_announce_bus
    if not radio_id or radio_id not in settings.radio_stations:
        return abort(500)
    if 'auth_token' not in session:
        return abort(403)
    user = session['auth_token']
    username = user.get('preferred_username')

    # throttling
    cache_key = f"throttle_api_tune_{username}"
    res = await current_app.session_interface.get(cache_key)
    if res:
        return jsonify({})  # silently fail

    radio_default = settings.radio_stations['wow']
    song = await radio_default.np()
    if not song:
        return await send_message(target, f"Nothing is playing?!")

    song.karma += 1
    song.save()

    # set cache
    await current_app.session_interface.set(cache_key, b'1', 15)

    loves = ['dancing', 'vibin\'', 'boppin\'', 'breakdancing', 'raving', 'chair dancing']
    msg = f"{username} {random.choice(loves)} .. \"{song.title}\" is now {song.karma}/10 .. PARTY ON!!!!"
    await irc_message_announce_bus.put(msg)
    return jsonify({'msg': msg})


@app.route('/api/skip/<path:radio_id>')
async def api_skip(radio_id: str):
    from ircradio.models import Song
    from ircradio.factory import irc_message_announce_bus
    if not radio_id or radio_id not in settings.radio_stations:
        return abort(500)
    if 'auth_token' not in session:
        return abort(403)
    user = session['auth_token']
    username = user.get('preferred_username')

    # throttling
    cache_key = f"throttle_api_skip_{radio_id}_{username}"
    res = await current_app.session_interface.get(cache_key)
    if res:
        return jsonify({})  # silently fail

    radio_station = settings.radio_stations[radio_id]
    await radio_station.skip()

    # set cache
    await current_app.session_interface.set(cache_key, b'1', 15)

    hates = ['Booo', 'Rude', 'Wtf']
    msg = f"{username} skipped. {random.choice(hates)}! >:|"

    if radio_station.id == "wow":
        await irc_message_announce_bus.put(msg)

    return jsonify({'msg': msg})


@app.route("/history")
async def history():
    from ircradio.factory import keycloak
    if 'auth_token' not in session:
        return redirect(url_for(keycloak.endpoint_name_login))

    radio_default = settings.radio_stations['wow']
    songs = await radio_default.history()
    if not songs:
        return "no history"

    return await render_template('history.html', songs=songs)


@app.websocket("/ws")
async def ws():
    current_app.logger.info('websocket client connected')
    from ircradio.factory import websocket_status_bus, websocket_status_bus_last_item
    from ircradio.station import Station

    async def send_all(data: dict[str, Station]):
        return await websocket.send_json({
            k: dataclasses.asdict(v) for k, v in data.items()
        })

    if isinstance(websocket_status_bus_last_item, dict):
        current_app.logger.debug('sending data to ws peer')
        await send_all(websocket_status_bus_last_item)

    while True:
        async for data in websocket_status_bus.subscribe():
            current_app.logger.debug('sending data to ws peer')
            await send_all(data)


@app.route("/assets/art/<path:path>")
async def assets_art(path: str):
    img_default = "album_art_default.jpg"
    _base = os.path.join(settings.cwd, "ircradio", "static")

    try:
        for _dirname in [settings.dir_meta, settings.dir_music]:
            _path = os.path.join(_dirname, path)
            if os.path.exists(_path):
                return await send_from_directory(_dirname, path)
    except Exception as ex:
        current_app.logger.debug(ex)
        return await send_from_directory(_base, img_default), 500

    return await send_from_directory(_base, img_default), 404


@app.route("/static_music_meta/<path:path>")
async def static_music_meta(path: str):
    return await send_from_directory(
        settings.dir_meta,
        file_name=path)
