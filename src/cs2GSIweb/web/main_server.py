# Copyright (C) 2024 Urufusan
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import functools
import json
import os

# import subprocess
# import sys
# import threading
import time

# import hashlib
from collections import defaultdict
from io import BytesIO
from pprint import pprint

import requests
from flask import Flask, Response, after_this_request, jsonify, redirect, request, send_file, url_for
from flask_sock import Sock as FlaskWSocket
from simple_websocket.ws import Server

BASE_PATH = os.path.dirname(os.path.realpath(__file__))
os.chdir(BASE_PATH)
AVATAR_DIR = os.path.join(BASE_PATH, "avatars")
if not os.path.exists(AVATAR_DIR):
    os.mkdir(AVATAR_DIR)

SERVER_NAME = "CS2-GSI-WEB"
SPEC_SKEY = hex(0xB * 0x94E55 * 0xBBC21 * 0x0BEF549 * 0xE95E221 * 0x29431C687).upper()[2:]


class TerminalColors:
    HEADER = "\033[95m"
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    ORANGE = "\033[33m"

    @staticmethod
    def terminalpaint(color):
        if isinstance(color, str):
            if color.startswith("#"):
                color = color[1:]
            r, g, b = tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))
        elif isinstance(color, tuple) and len(color) == 3:
            r, g, b = color
        else:
            raise ValueError("Invalid color format")

        color_code = f"\x1b[38;2;{r};{g};{b}m"
        return color_code

    def print_ctext(self, _text, color="#964bb4"):
        _raw_pp_str = str(_text)  # pprint.pformat(object=_text)
        print(f"{self.terminalpaint(color)}{_raw_pp_str}{self.ENDC}")


tc = TerminalColors()
printfc = tc.print_ctext

app = Flask(SERVER_NAME, static_folder="static", static_url_path="")

# https://github.com/miguelgrinberg/flask-sock
app.config["SOCK_SERVER_OPTIONS"] = {"ping_interval": 10}
wsock = FlaskWSocket(app)

clients_map: dict[str, list[Server]] = defaultdict(list)


def _no_cache_steamid_json_provider(_steamid: str) -> dict:
    print(f"Accesing data for {_steamid}...")
    return requests.get(f"https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/?key={SPEC_SKEY}&steamids={_steamid}").json()


@functools.cache
def _steamid_json_provider(_steamid: str) -> dict:
    print(f"Adding {_steamid} JSON to cache...")
    return _no_cache_steamid_json_provider(_steamid)


@functools.cache
def _steamid_to_username(_steamid: str) -> str:
    print(f"Adding {_steamid} username to cache...")
    if not _steamid:
        raise ValueError("SteamID can't be blank!")
    return safe_list_get(_steamid_json_provider(_steamid).get("response", {}).get("players", ({None: None},)), 0, {}).get("personaname", "Unknown")


def safe_list_get(l: list | tuple, idx: int, default):
    try:
        return l[idx]
    except IndexError:
        return default


def _steamid_to_avatar(_steamid: str) -> str:
    _player_json = safe_list_get(_no_cache_steamid_json_provider(_steamid).get("response", {}).get("players", ({None: None},)), 0, {})
    if _avatar_url := _player_json.get("avatarfull", ""):
        _image_bytes = requests.get(_avatar_url).content
        with open(f"{AVATAR_DIR}/{_player_json.get('avatarhash')}.jpg", "wb") as _f_ctx:
            _f_ctx.write(_image_bytes)
        return f"{AVATAR_DIR}/{_player_json.get('avatarhash')}.jpg"

    return f"{BASE_PATH}/static/profile.png"


def _avatar_exists(_steamid: str) -> bool:
    return f"{_steamid}.jpg" in os.listdir(AVATAR_DIR)


def _file_age(_filepath: str) -> float:
    return time.time() - os.path.getmtime(_filepath)


@app.route("/gamestate", methods=["POST"])
def GSI_post():
    _payload: dict = request.json
    pprint(_payload)

    _steamid = _payload.get("provider", {}).get("steamid", None)

    printfc(clients_map, "#e0ae75")
    for _ws_client in clients_map[_steamid_to_username(_steamid)]:
        if _ws_client.connected:
            _ws_client.send(request.data.decode())
        else:
            clients_map.pop(_steamid_to_username(_steamid))

    return Response("OK", 200, mimetype="text/plain")


@wsock.route("/wsp")
def wsock_frontend_com(ws: Server):

    tc.print_ctext(f"[WS] New websocket connection! - {request.user_agent.string}", color="#3dfa4d")
    persona_name = request.cookies.get("gsi_personaname")

    @after_this_request
    def _nuke_ws_client(_ws_resp_ctx):
        clients_map[persona_name].remove(ws)
        if not clients_map[persona_name]:
            clients_map.pop(persona_name)
        tc.print_ctext(f"[WS] {request.user_agent.string} Disconnected!", color="#ff4444")
        return _ws_resp_ctx

    clients_map[persona_name].append(ws)
    while True:
        data = ws.receive(None)
        if data:
            print(f"{tc.terminalpaint('#3357bb')}[WS DATA - {type(data).__name__}]{tc.ENDC}", data)


@app.route("/avatar/<int:_steamid>.jpg", methods=["POST", "GET"])
def get_avatar(_steamid):
    steamid = str(_steamid)
    _local_avatar_path = f"{AVATAR_DIR}/{steamid}.jpg"
    if _avatar_exists(steamid):
        if (_file_age(_local_avatar_path) < 86400.0) or (
            "profile.png" == os.path.basename(os.readlink(_local_avatar_path))
        ):  # one day old or default pfp (default if it doesn't exist)
            return send_file(_local_avatar_path)  #  mimetype='image/jpeg'
        else:
            _player_json = safe_list_get(_no_cache_steamid_json_provider(steamid).get("response", {}).get("players", ({None: None},)), 0, {})
            if os.path.basename(os.readlink(_local_avatar_path)).split(".")[0] == _player_json.get("avatarhash", ""):
                os.utime(_local_avatar_path, (os.stat(_local_avatar_path).st_atime_ns, time.time_ns()))  # update age
                return send_file(_local_avatar_path)  # mimetype='image/jpeg'

    _new_avatar_path = _steamid_to_avatar(_steamid)
    if os.path.lexists(_local_avatar_path):
        os.unlink(_local_avatar_path)
    os.symlink(_new_avatar_path, _local_avatar_path)

    return send_file(_local_avatar_path)  # mimetype='image/jpeg'


# @app.route("/")
# def goto_correct():
#     print(request.args.to_dict())
#     _wurl_params = {key: value for key, value in request.args.items()}
#     return redirect(url_for("static", filename="index.html", **_wurl_params))


@app.route("/")
def root_pth():
    return app.send_static_file("index.html")


if __name__ == "__main__":
    # t = threading.Thread(target=msg_broadcaster)
    # t.daemon = True
    # t.start()

    print(tc.terminalpaint("#964bb4"), "/// cs2GSIweb ///", tc.ENDC)
    app.run(host="0.0.0.0", port=3000, debug=False)
