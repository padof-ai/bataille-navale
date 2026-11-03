#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Bataille navale à deux joueurs, avec salons à code.

- En local : lancez ce fichier sur un PC et ouvrez l'adresse affichée
  (ex. http://192.168.1.20:8000) sur deux appareils du même Wi-Fi.
- En ligne : déployez ce fichier chez un hébergeur (ex. Render) avec la
  commande de démarrage « python bataille_navale.py ». Le port est lu
  dans la variable d'environnement PORT.

Aucune bibliothèque externe : uniquement Python 3.7+.
"""
import base64
import json
import os
import random
import secrets
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PORT = int(os.environ.get("PORT", "8000"))
ONLINE = "PORT" in os.environ         # True chez l'hébergeur
CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # sans O/0 ni I/1
ROOM_TTL = 3 * 3600     # un salon sans aucune activité pendant 3 h est supprimé
MAX_ROOMS = 300
SIZE = 10
LETTERS = "ABCDEFGHIJ"
FLEET = [
    ("Porte-avions", 5),
    ("Croiseur", 4),
    ("Contre-torpilleur", 3),
    ("Sous-marin", 3),
    ("Torpilleur", 2),
]
STALE_SECONDS = 600     # au-delà, la place d'un joueur absent peut être reprise
ONLINE_SECONDS = 6      # en dessous, le joueur est considéré connecté


class Game:
    def __init__(self, code=""):
        self.code = code
        self.players = [None, None]   # {"token": str, "seen": float}
        self.version = 0
        self.new_round()

    def last_activity(self):
        return max([p["seen"] for p in self.players if p] or [0])

    # ---------- gestion de la partie ----------
    def new_round(self):
        self.ships = [None, None]     # liste de {"name", "cells": [(r,c)], "hits": set()}
        self.shots = [{}, {}]         # tirs effectués PAR le joueur p : {(r,c): touché?}
        self.phase = "placing"        # placing -> playing -> over
        self.turn = random.randint(0, 1)
        self.winner = None
        self.last = None
        self.version += 1

    def find(self, token):
        if not token:
            return None
        for i, p in enumerate(self.players):
            if p and p["token"] == token:
                return i
        return None

    def touch(self, i):
        self.players[i]["seen"] = time.time()

    def join(self, token):
        i = self.find(token)
        if i is not None:
            self.touch(i)
            return i, token
        new_token = secrets.token_hex(16)
        for i, p in enumerate(self.players):
            if p is None:
                self.players[i] = {"token": new_token, "seen": time.time()}
                self.version += 1
                return i, new_token
        now = time.time()
        for i, p in enumerate(self.players):
            if now - p["seen"] > STALE_SECONDS:
                self.players[i] = {"token": new_token, "seen": now}
                self.new_round()
                return i, new_token
        return None, None

    def online(self, i):
        p = self.players[i]
        return bool(p) and time.time() - p["seen"] < ONLINE_SECONDS

    # ---------- placement ----------
    def place(self, i, ships):
        if self.phase != "placing":
            raise ValueError("Le placement n'est plus possible.")
        if self.ships[i] is not None:
            raise ValueError("Vos bateaux sont déjà placés.")
        if not isinstance(ships, list) or len(ships) != len(FLEET):
            raise ValueError("Flotte incomplète.")
        used = set()
        result = []
        for (name, length), cells in zip(FLEET, ships):
            try:
                cells = [(int(r), int(c)) for r, c in cells]
            except Exception:
                raise ValueError("Coordonnées invalides.")
            if len(cells) != length:
                raise ValueError("Taille incorrecte pour : " + name)
            if any(not (0 <= r < SIZE and 0 <= c < SIZE) for r, c in cells):
                raise ValueError(name + " sort de la grille.")
            rows = {r for r, _ in cells}
            cols = {c for _, c in cells}
            if len(rows) == 1:
                line = sorted(c for _, c in cells)
            elif len(cols) == 1:
                line = sorted(r for r, _ in cells)
            else:
                raise ValueError(name + " doit être droit.")
            if line != list(range(line[0], line[0] + length)):
                raise ValueError(name + " doit être d'un seul tenant.")
            if used & set(cells):
                raise ValueError("Des bateaux se chevauchent.")
            used |= set(cells)
            result.append({"name": name, "cells": cells, "hits": set()})
        self.ships[i] = result
        if self.ships[0] is not None and self.ships[1] is not None:
            self.phase = "playing"
        self.version += 1

    # ---------- tir ----------
    def fire(self, i, r, c):
        if self.phase != "playing":
            raise ValueError("La partie n'est pas en cours.")
        if self.turn != i:
            raise ValueError("Ce n'est pas votre tour.")
        r, c = int(r), int(c)
        if not (0 <= r < SIZE and 0 <= c < SIZE):
            raise ValueError("Case hors grille.")
        if (r, c) in self.shots[i]:
            raise ValueError("Vous avez déjà tiré ici.")
        target = 1 - i
        hit_ship = None
        for ship in self.ships[target]:
            if (r, c) in ship["cells"]:
                hit_ship = ship
                break
        self.shots[i][(r, c)] = hit_ship is not None
        event = {"by": i, "cell": LETTERS[r] + str(c + 1), "result": "miss", "ship": None}
        if hit_ship:
            hit_ship["hits"].add((r, c))
            event["result"] = "hit"
            if len(hit_ship["hits"]) == len(hit_ship["cells"]):
                event["result"] = "sunk"
                event["ship"] = hit_ship["name"]
        self.last = event
        if all(len(s["hits"]) == len(s["cells"]) for s in self.ships[target]):
            self.phase = "over"
            self.winner = i
        else:
            self.turn = target
        self.version += 1

    # ---------- vue envoyée à un joueur ----------
    def view(self, i):
        o = 1 - i

        def sunk(s):
            return len(s["hits"]) == len(s["cells"])

        my_ships = None
        if self.ships[i]:
            my_ships = [{"name": s["name"], "cells": s["cells"], "sunk": sunk(s)}
                        for s in self.ships[i]]
        opp_sunk, opp_reveal = [], []
        if self.ships[o]:
            opp_sunk = [{"name": s["name"], "cells": s["cells"]}
                        for s in self.ships[o] if sunk(s)]
            if self.phase == "over":
                opp_reveal = [{"name": s["name"], "cells": s["cells"]}
                              for s in self.ships[o]]
        return {
            "me": i,
            "phase": self.phase,
            "turn": self.turn,
            "winner": self.winner,
            "fleet": FLEET,
            "opp_connected": self.online(o),
            "me_ready": self.ships[i] is not None,
            "opp_ready": self.ships[o] is not None,
            "my_ships": my_ships,
            "shots_on_me": [[r, c, h] for (r, c), h in self.shots[o].items()],
            "my_shots": [[r, c, h] for (r, c), h in self.shots[i].items()],
            "opp_sunk": opp_sunk,
            "opp_reveal": opp_reveal,
            "last": self.last,
            "version": self.version,
            "code": self.code,
            "players": sum(1 for p in self.players if p),
        }


rooms = {}
lock = threading.Lock()


def cleanup_rooms():
    now = time.time()
    for code in [c for c, g in rooms.items() if now - g.last_activity() > ROOM_TTL]:
        del rooms[code]


def new_code():
    while True:
        code = "".join(random.choice(CODE_CHARS) for _ in range(4))
        if code not in rooms:
            return code


def norm_code(code):
    return "".join(ch for ch in str(code or "").upper() if ch in CODE_CHARS)[:4]


MANIFEST = json.dumps({
    "name": "Bataille navale",
    "short_name": "Bataille navale",
    "lang": "fr",
    "start_url": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#040c18",
    "theme_color": "#0a1e38",
    "icons": [
        {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
        {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
    ],
})

SERVICE_WORKER = "self.addEventListener('install',e=>self.skipWaiting());" \
                 "self.addEventListener('activate',e=>self.clients.claim());" \
                 "self.addEventListener('fetch',()=>{});"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # console silencieuse

    def send_raw(self, code, data, ctype, cache=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400" if cache else "no-store")
        self.end_headers()
        self.wfile.write(data)

    def send_body(self, code, body, ctype):
        self.send_raw(code, body.encode("utf-8"), ctype)

    def send_json(self, code, obj):
        self.send_body(code, json.dumps(obj), "application/json; charset=utf-8")

    def do_HEAD(self):
        self.send_raw(200, b"", "text/plain")

    def do_GET(self):
        url = urlparse(self.path)
        p = url.path
        if p in ("/", "/index.html"):
            self.send_body(200, PAGE, "text/html; charset=utf-8")
        elif p == "/manifest.webmanifest":
            self.send_body(200, MANIFEST, "application/manifest+json")
        elif p == "/sw.js":
            self.send_body(200, SERVICE_WORKER, "text/javascript")
        elif p in ICONS:
            self.send_raw(200, ICONS[p], "image/png", cache=True)
        elif p == "/api/state":
            q = parse_qs(url.query)
            code = norm_code(q.get("code", [""])[0])
            token = q.get("token", [""])[0]
            with lock:
                game = rooms.get(code)
                if game is None:
                    return self.send_json(404, {"error": "Partie introuvable."})
                i = game.find(token)
                if i is None:
                    return self.send_json(403, {"error": "Session inconnue."})
                game.touch(i)
                self.send_json(200, game.view(i))
        else:
            self.send_json(404, {"error": "Introuvable."})

    def do_POST(self):
        url = urlparse(self.path)
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 20000:
                raise ValueError
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except Exception:
            return self.send_json(400, {"error": "Requête invalide."})
        with lock:
            cleanup_rooms()
            if url.path == "/api/create":
                if len(rooms) >= MAX_ROOMS:
                    return self.send_json(503, {"error": "Trop de parties en cours, réessayez plus tard."})
                code = new_code()
                rooms[code] = Game(code)
                i, token = rooms[code].join(None)
                return self.send_json(200, {"code": code, "token": token, "player": i})
            code = norm_code(body.get("code"))
            game = rooms.get(code)
            if game is None:
                return self.send_json(404, {"error": "Partie introuvable. Vérifiez le code."})
            if url.path == "/api/join":
                i, token = game.join(body.get("token"))
                if i is None:
                    return self.send_json(409, {"error": "Partie complète : deux joueurs sont déjà dedans."})
                return self.send_json(200, {"code": code, "token": token, "player": i})
            i = game.find(body.get("token"))
            if i is None:
                return self.send_json(403, {"error": "Session inconnue."})
            game.touch(i)
            try:
                if url.path == "/api/place":
                    game.place(i, body.get("ships"))
                elif url.path == "/api/fire":
                    game.fire(i, body.get("r"), body.get("c"))
                elif url.path == "/api/reset":
                    if game.phase != "over":
                        raise ValueError("La partie n'est pas terminée.")
                    game.new_round()
                elif url.path == "/api/leave":
                    game.players[i] = None
                    game.new_round()
                    if not any(game.players):
                        del rooms[code]
                    return self.send_json(200, {"ok": True})
                else:
                    return self.send_json(404, {"error": "Introuvable."})
            except ValueError as e:
                return self.send_json(400, {"error": str(e)})
            self.send_json(200, game.view(i))


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))   # aucun paquet n'est envoyé
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return "127.0.0.1"


PAGE = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Bataille navale</title>
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" type="image/png" href="/icon-192.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<meta name="theme-color" content="#0a1e38">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Bataille navale">
<style>
:root{--text:#e8f1ff;--muted:#8fa8c8;--accent:#ffb020;--green:#3cff8c;--panel:rgba(10,30,58,.85)}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:var(--text);
  background:radial-gradient(ellipse at 50% -10%,#16406e 0,#0a1e38 45%,#040c18 100%) fixed;-webkit-tap-highlight-color:transparent}
.hud{font-family:"Consolas","Courier New",ui-monospace,monospace;letter-spacing:.12em;text-transform:uppercase}
header{max-width:900px;margin:0 auto;padding:12px 16px 4px}
.top{display:flex;align-items:center;justify-content:space-between;gap:10px}
h1{margin:0;font-size:1.25rem;font-weight:800;text-shadow:0 0 12px rgba(60,255,140,.35)}
h1 span{color:var(--green)}
#snd{padding:8px 12px;font-size:.85rem}
#status{margin-top:10px;padding:11px 14px;border-radius:10px;background:var(--panel);border:1px solid rgba(143,168,200,.25);font-weight:700;display:flex;align-items:center;gap:10px}
#status::before{content:"";width:10px;height:10px;border-radius:50%;background:var(--muted);flex:none}
#status.myturn{border-color:var(--green);box-shadow:0 0 18px rgba(60,255,140,.25)}
#status.myturn::before{background:var(--green);box-shadow:0 0 8px var(--green);animation:blink 1s infinite}
#status.wait::before{background:var(--accent);animation:blink 1.4s infinite}
#status.win{background:rgba(20,90,55,.9);border-color:var(--green)}
#status.lose{background:rgba(110,25,30,.9);border-color:#ff6b6b}
@keyframes blink{50%{opacity:.25}}
#log{color:var(--muted);font-size:.88rem;margin-top:7px;min-height:1.2em}
main{display:flex;flex-wrap:wrap;gap:22px;justify-content:center;padding:10px 16px 30px}
.board{width:min(92vw,430px)}
.bhead{display:flex;justify-content:space-between;align-items:baseline;margin:4px 0 8px;font-size:.8rem;color:var(--muted)}
.radar .bhead span:first-child{color:var(--green)}
.fleetb .bhead span:first-child{color:#8cc8ff}
.gw{display:grid;grid-template-columns:18px 1fr;grid-template-rows:18px auto;gap:3px}
.cols{display:grid;grid-template-columns:repeat(10,1fr)}
.rows{display:grid;grid-template-rows:repeat(10,1fr)}
.cols span,.rows span{display:flex;align-items:center;justify-content:center;font-size:.65rem;color:var(--muted);font-family:Consolas,monospace}
.sea{position:relative;aspect-ratio:1;border-radius:8px;overflow:hidden;user-select:none;-webkit-user-select:none;border:1px solid rgba(143,168,200,.3)}
.layer{position:absolute;inset:0}
.cellsL{display:grid;grid-template-columns:repeat(10,1fr);grid-template-rows:repeat(10,1fr);z-index:2}
.shipsL{z-index:1;pointer-events:none}
.cell{position:relative;border:.5px solid rgba(255,255,255,.07)}

/* Mer (ma flotte) */
.fleetb .sea{background:linear-gradient(160deg,#145a96,#0a3766 55%,#061f3d)}
.waves{z-index:0;background:
  repeating-linear-gradient(115deg,rgba(255,255,255,.06) 0 5px,transparent 5px 24px),
  repeating-linear-gradient(65deg,rgba(255,255,255,.04) 0 3px,transparent 3px 28px);
  animation:waves 14s linear infinite}
@keyframes waves{to{background-position:240px 120px,-200px 160px}}
.fleetb.hitme .sea{animation:shake .5s}
@keyframes shake{20%{transform:translate(-5px,3px)}40%{transform:translate(5px,-3px)}60%{transform:translate(-3px,-2px)}80%{transform:translate(3px,2px)}}

/* Radar (grille adverse) */
.radar .sea{border-color:rgba(60,255,140,.35);background:radial-gradient(circle at 50% 50%,#0b4228 0,#04200f 55%,#010b05 100%)}
.radar .cell{border-color:rgba(60,255,140,.13)}
.rings{z-index:0;background:
  repeating-radial-gradient(circle at 50% 50%,transparent 0 calc(16% - 1px),rgba(60,255,140,.32) calc(16% - 1px) 16%),
  linear-gradient(rgba(60,255,140,.22),rgba(60,255,140,.22)) 50% 0/1px 100% no-repeat,
  linear-gradient(rgba(60,255,140,.22),rgba(60,255,140,.22)) 0 50%/100% 1px no-repeat}
.sweep{position:absolute;left:-21%;top:-21%;width:142%;height:142%;z-index:3;pointer-events:none;
  background:conic-gradient(from 0deg,rgba(60,255,140,0) 0deg,rgba(60,255,140,0) 290deg,rgba(60,255,140,.38) 360deg);
  animation:spin 4s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}
.scan{z-index:4;pointer-events:none;background:repeating-linear-gradient(0deg,rgba(0,0,0,.2) 0 2px,transparent 2px 4px)}
.radar.active .sea{box-shadow:0 0 0 2px var(--green),0 0 26px rgba(60,255,140,.45)}
.radar:not(.active) .sea{filter:brightness(.78) saturate(.8)}
.radar .clickable{cursor:crosshair}
.radar .clickable:hover::before{content:"";position:absolute;inset:10%;border:2px solid #7dffb3;border-radius:50%;box-shadow:0 0 8px var(--green),inset 0 0 6px var(--green);z-index:6}
.radar .clickable:hover::after{content:"";position:absolute;inset:0;z-index:6;
  background:linear-gradient(#7dffb3,#7dffb3) 50% 0/2px 30% no-repeat,linear-gradient(#7dffb3,#7dffb3) 50% 100%/2px 30% no-repeat,
  linear-gradient(#7dffb3,#7dffb3) 0 50%/30% 2px no-repeat,linear-gradient(#7dffb3,#7dffb3) 100% 50%/30% 2px no-repeat}
.overmsg{position:absolute;inset:0;z-index:7;display:flex;align-items:center;justify-content:center;font-size:clamp(1.6rem,8vw,2.6rem);font-weight:900;
  background:rgba(0,0,0,.35);text-shadow:0 0 18px currentColor;animation:pop .6s ease-out}
.overmsg.win{color:var(--green)}.overmsg.lose{color:#ff6b6b}
@keyframes pop{from{transform:scale(1.6);opacity:0}}

/* Navires */
.ship{position:absolute;padding:2px;filter:drop-shadow(0 4px 3px rgba(0,0,0,.55))}
.ship svg{width:100%;height:100%;display:block;overflow:visible}
.ship.sunk{filter:grayscale(1) brightness(.42) drop-shadow(0 2px 2px rgba(0,0,0,.6))}
.ship.ghost{opacity:.75;filter:drop-shadow(0 0 6px var(--green))}
.ship.ghost.bad{filter:sepia(1) saturate(6) hue-rotate(-50deg) drop-shadow(0 0 6px #ff4040)}
.ship.wreck{filter:grayscale(1) sepia(1) saturate(3) hue-rotate(75deg) brightness(.75) drop-shadow(0 0 5px rgba(60,255,140,.6))}
.ship.reveal{opacity:.55;filter:grayscale(.4) sepia(.6) hue-rotate(75deg) drop-shadow(0 0 4px rgba(60,255,140,.6))}

/* Impacts */
.mk{position:absolute;pointer-events:none;z-index:2}
.mk.hit{inset:16%;border-radius:50%;background:radial-gradient(circle,#fff7c8 0 14%,#ffbe3c 34%,#ff5a2a 56%,rgba(229,60,40,0) 72%);animation:flick .5s infinite alternate}
.mk.hit::after{content:"";position:absolute;left:-20%;right:-20%;top:-70%;height:90%;background:radial-gradient(ellipse at 50% 85%,rgba(70,70,70,.65),transparent 65%);animation:smoke 2.2s infinite}
.sunkc .mk.hit{animation:none;background:radial-gradient(circle,#ffb04a 0 12%,#c2351f 40%,rgba(120,20,10,0) 70%);opacity:.9}
@keyframes flick{from{transform:scale(.88)}to{transform:scale(1.1);filter:brightness(1.3)}}
@keyframes smoke{0%{transform:translateY(0) scale(.7);opacity:.75}100%{transform:translateY(-55%) scale(1.5);opacity:0}}
.mk.miss{inset:30%;border-radius:50%;border:2px solid rgba(215,238,255,.85);background:rgba(215,238,255,.22)}
.radar .mk.miss{border-color:rgba(130,255,180,.7);background:rgba(130,255,180,.14)}
.boom{position:absolute;inset:-70%;border-radius:50%;z-index:8;pointer-events:none;
  background:radial-gradient(circle,#fff 0 9%,#ffe08a 22%,#ff7a2b 42%,rgba(255,60,0,0) 68%);animation:boom .75s ease-out forwards}
.boom.big{inset:-130%}
@keyframes boom{0%{transform:scale(.2);opacity:1}100%{transform:scale(1.5);opacity:0}}
.ripple{position:absolute;inset:15%;border-radius:50%;border:2px solid #e6f7ff;z-index:8;pointer-events:none;animation:ripple 1s ease-out forwards}
.ripple.r2{animation-delay:.22s;opacity:0}
@keyframes ripple{0%{transform:scale(.2);opacity:1}100%{transform:scale(2.3);opacity:0}}

/* Commandes */
.controls{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}
button{font:inherit;padding:10px 14px;border-radius:9px;border:1px solid rgba(143,168,200,.3);background:#173a66;color:var(--text);cursor:pointer;font-weight:700}
button:hover{background:#1f4a80}
button.primary{background:var(--accent);border-color:var(--accent);color:#1b1300}
button:disabled{opacity:.4;cursor:default}
.fleet{display:flex;flex-wrap:wrap;gap:6px;margin:0 0 9px}
.chip{padding:5px 10px;border-radius:20px;background:rgba(23,58,102,.8);font-size:.78rem;border:2px solid transparent}
.pick .chip{cursor:pointer}
.chip.sel{border-color:var(--accent)}
.chip.done{opacity:.45}
.radar .chip{background:rgba(10,60,35,.8);color:#b8ffd5}
.chip.sunk{background:rgba(110,25,30,.9)!important;color:#ffb3b3!important;text-decoration:line-through}
.hint{color:var(--muted);font-size:.82rem;margin:0 0 9px}

/* Accueil et salons */
.btns{display:flex;gap:8px;align-items:center}
#quit{padding:8px 12px;font-size:.85rem}
.hidden{display:none!important}
.home{width:min(92vw,430px);display:flex;flex-direction:column;gap:16px;margin-top:6px}
.card{background:var(--panel);border:1px solid rgba(143,168,200,.25);border-radius:14px;padding:18px}
.card h2{margin:0 0 6px;font-size:.95rem;color:var(--green)}
.card p{margin:0 0 12px;color:var(--muted);font-size:.88rem;line-height:1.4}
.card button{width:100%;padding:13px;font-size:1rem}
.joinrow{display:flex;gap:8px}
.joinrow input{flex:1;min-width:0;font:inherit;font-family:Consolas,monospace;font-size:1.4rem;letter-spacing:.35em;text-transform:uppercase;text-align:center;
  padding:10px;border-radius:9px;border:1px solid rgba(143,168,200,.4);background:#061a30;color:var(--text)}
.joinrow button{width:auto}
.err{color:#ff8a8a;font-size:.9rem;min-height:1.1em;margin:0}
#share{margin-top:10px;padding:12px 14px;border-radius:10px;background:rgba(255,176,32,.12);border:1px solid rgba(255,176,32,.5);
  display:flex;flex-wrap:wrap;align-items:center;gap:10px}
#share .code{font-family:Consolas,monospace;font-size:1.6rem;font-weight:800;letter-spacing:.25em;color:var(--accent)}
#share .txt{flex:1;min-width:160px;font-size:.85rem;color:var(--muted)}
#roomtag{font-family:Consolas,monospace;color:var(--muted);font-size:.75rem;letter-spacing:.15em}
h1{white-space:nowrap}
@media (max-width:520px){
  h1{font-size:1rem;letter-spacing:.06em}
  #roomtag{display:none}
  .btns{gap:6px;flex:none}
  .btns button{padding:7px 9px;font-size:.72rem;letter-spacing:.06em;white-space:nowrap}
  #snd .lbl{display:none}
  .top{gap:6px}
}
</style>
</head>
<body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true"><defs>
  <linearGradient id="gHull" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#a9b5c3"/><stop offset=".5" stop-color="#6f7d8e"/><stop offset="1" stop-color="#3a4552"/></linearGradient>
  <linearGradient id="gDeck" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#8996a5"/><stop offset="1" stop-color="#5a6574"/></linearGradient>
  <linearGradient id="gFlight" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#58636f"/><stop offset="1" stop-color="#3b444e"/></linearGradient>
  <linearGradient id="gSuper" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#d3dbe4"/><stop offset="1" stop-color="#8a97a6"/></linearGradient>
  <radialGradient id="gTurret" cx=".4" cy=".35" r=".7"><stop offset="0" stop-color="#c5cfda"/><stop offset="1" stop-color="#525e6b"/></radialGradient>
  <linearGradient id="gSub" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#4a5868"/><stop offset=".55" stop-color="#28323d"/><stop offset="1" stop-color="#11161c"/></linearGradient>
</defs></svg>
<header>
  <div class="top">
    <h1 class="hud">&#9875; Bataille <span>navale</span></h1>
    <div class="btns"><span id="roomtag"></span><button id="snd" class="hud"></button><button id="quit" class="hud hidden">Quitter</button></div>
  </div>
  <div id="share" class="hidden">
    <span class="code" id="sharecode"></span>
    <span class="txt">Envoyez ce code ou le lien à votre adversaire pour qu'il vous rejoigne.</span>
    <button id="sharebtn">📤 Partager le lien</button>
  </div>
  <div id="status" class="wait">Connexion…</div>
  <div id="log"></div>
</header>
<main id="main"></main>
<script>
const SIZE = 10, LETTERS = "ABCDEFGHIJ";
let room = null, token = null, S = null, lastJson = "", view = "", installEvt = null;
let placed = [], sel = 0, horiz = true, hover = null, busy = false, P = null;
let prevMine = -1, prevRecv = -1, prevMyTurn = null, prevPhase = null;

function store(k, v){ try{ v === null ? localStorage.removeItem(k) : localStorage.setItem(k, v); }catch(e){} }
function load(k){ try{ return localStorage.getItem(k); }catch(e){ return null; } }
const $ = id => document.getElementById(id);
function div(cls){ const d = document.createElement("div"); if(cls) d.className = cls; return d; }
function setStatus(t, cls){ const s = $("status"); s.textContent = t; s.className = cls || ""; }
function setLog(t){ $("log").textContent = t || ""; }
const key = (r, c) => r * SIZE + c;

/* ================= SONS (générés, aucun fichier) ================= */
const Snd = {
  ctx: null, on: load("bn_sound") !== "0", nb: null, amb: null, pingT: null,
  init(){
    if(!this.ctx){
      const AC = window.AudioContext || window.webkitAudioContext; if(!AC) return;
      this.ctx = new AC(); this.out = this.ctx.createGain(); this.out.gain.value = .9; this.out.connect(this.ctx.destination);
      const n = this.ctx.sampleRate * 2, b = this.ctx.createBuffer(1, n, this.ctx.sampleRate), d = b.getChannelData(0);
      for(let i = 0; i < n; i++) d[i] = Math.random() * 2 - 1;
      this.nb = b;
    }
    if(this.ctx.state === "suspended") this.ctx.resume();
    if(this.on && !this.amb) this.ambient();
  },
  ok(){ return this.on && this.ctx; },
  env(g, t, dur, peak){ g.gain.setValueAtTime(.0001, t); g.gain.exponentialRampToValueAtTime(peak, t + .015); g.gain.exponentialRampToValueAtTime(.0001, t + dur); },
  noise(dur, type, f0, f1, peak, delay = 0, q = 1){
    if(!this.ok()) return;
    const c = this.ctx, t = c.currentTime + delay, s = c.createBufferSource(), f = c.createBiquadFilter(), g = c.createGain();
    s.buffer = this.nb; s.loop = true; f.type = type; f.Q.value = q;
    f.frequency.setValueAtTime(f0, t); f.frequency.exponentialRampToValueAtTime(Math.max(f1, 20), t + dur);
    this.env(g, t, dur, peak); s.connect(f).connect(g).connect(this.out); s.start(t, Math.random()); s.stop(t + dur + .05);
  },
  tone(type, f0, f1, dur, peak, delay = 0){
    if(!this.ok()) return;
    const c = this.ctx, t = c.currentTime + delay, o = c.createOscillator(), g = c.createGain();
    o.type = type; o.frequency.setValueAtTime(f0, t); o.frequency.exponentialRampToValueAtTime(Math.max(f1, 20), t + dur);
    this.env(g, t, dur, peak); o.connect(g).connect(this.out); o.start(t); o.stop(t + dur + .05);
  },
  launch(){ this.noise(.4, "bandpass", 400, 2800, .3, 0, 2); this.tone("sawtooth", 160, 70, .22, .06); },
  whistle(){ this.tone("sine", 2000, 450, .75, .09); },
  explosion(big, delay = 0){
    const d = big ? 1.9 : 1.05;
    this.noise(d, "lowpass", big ? 2600 : 1900, 60, big ? 1 : .8, delay);
    this.tone("sine", big ? 95 : 115, 28, d * .9, big ? .9 : .6, delay);
    this.noise(.22, "highpass", 3200, 1600, .25, delay);
    if(big){ this.noise(1.3, "lowpass", 900, 45, .6, delay + .35); this.tone("sine", 62, 24, 1.5, .55, delay + .3); }
  },
  splash(delay = 0){ this.noise(.6, "bandpass", 2300, 320, .5, delay, 1.2); this.tone("sine", 760, 170, .28, .16, delay); this.noise(.35, "highpass", 4200, 2600, .12, delay + .06); },
  impact(res, delay = 0){ res === "miss" ? this.splash(delay) : this.explosion(res === "sunk", delay); },
  ping(peak = .22, delay = 0){ this.tone("sine", 1320, 1290, 1.4, peak, delay); this.tone("sine", 1320, 1290, 1.2, peak * .3, delay + .5); },
  click(){ this.tone("square", 900, 520, .06, .04); },
  victory(delay = 0){ [523, 659, 784, 1047, 784, 1047].forEach((f, i) => this.tone("triangle", f, f, i > 3 ? .6 : .24, .2, delay + i * .18)); },
  defeat(delay = 0){ [392, 349, 311, 262].forEach((f, i) => this.tone("sawtooth", f, f * .97, .55, .06, delay + i * .42)); this.tone("sine", 80, 28, 2, .45, delay + 1.6); },
  ambient(){
    this.stopAmbient(); if(!this.ok()) return;
    const c = this.ctx, s = c.createBufferSource(), f = c.createBiquadFilter(), g = c.createGain(), lfo = c.createOscillator(), lg = c.createGain();
    s.buffer = this.nb; s.loop = true; f.type = "lowpass"; f.frequency.value = 240; g.gain.value = .05;
    lfo.frequency.value = .13; lg.gain.value = .03; lfo.connect(lg).connect(g.gain);
    s.connect(f).connect(g).connect(this.out); s.start(); lfo.start();
    this.amb = {s, lfo};
    this.pingT = setInterval(() => this.ping(.035), 8000);
  },
  stopAmbient(){ if(this.amb){ try{ this.amb.s.stop(); this.amb.lfo.stop(); }catch(e){} this.amb = null; } clearInterval(this.pingT); this.pingT = null; },
  toggle(){ this.on = !this.on; store("bn_sound", this.on ? "1" : "0"); if(this.on) this.init(); else this.stopAmbient(); sndLabel(); }
};
function sndLabel(){ $("snd").innerHTML = Snd.on ? "🔊<span class=\"lbl\"> Son</span>" : "🔇<span class=\"lbl\"> Muet</span>"; }
$("snd").onclick = () => Snd.toggle();
sndLabel();
["pointerdown", "keydown"].forEach(ev => document.addEventListener(ev, () => Snd.init(), {once: true}));

/* ================= NAVIRES (dessins SVG) ================= */
const KIND = {"Porte-avions":"carrier", "Croiseur":"cruiser", "Contre-torpilleur":"destroyer", "Sous-marin":"sub", "Torpilleur":"patrol"};
function hull(W, t, b, bow){
  return `<path d="M10 ${t+9} Q10 ${t} 24 ${t} L${W-bow} ${t} Q${W-10} ${t+5} ${W-4} 50 Q${W-10} ${b-5} ${W-bow} ${b} L24 ${b} Q10 ${b} 10 ${b-9} Z" fill="url(#gHull)" stroke="#1b232d" stroke-width="3"/>`;
}
function turret(x, dir, r = 11){
  const l = r * 3.2 * dir;
  return `<line x1="${x}" y1="46" x2="${x+l}" y2="46" stroke="#2a333e" stroke-width="4" stroke-linecap="round"/>` +
         `<line x1="${x}" y1="54" x2="${x+l}" y2="54" stroke="#2a333e" stroke-width="4" stroke-linecap="round"/>` +
         `<circle cx="${x}" cy="50" r="${r}" fill="url(#gTurret)" stroke="#1b232d" stroke-width="2"/>`;
}
function shipSVG(name, len, horiz){
  const W = len * 100, k = KIND[name] || "cruiser"; let g = "";
  if(k === "carrier"){
    g += hull(W, 16, 84, 75);
    g += `<path d="M16 20 L${W-110} 18 L${W-36} 30 L${W-36} 70 L${W-110} 82 L16 80 Z" fill="url(#gFlight)" stroke="#2a313a" stroke-width="2"/>`;
    g += `<line x1="28" y1="50" x2="${W-60}" y2="50" stroke="#efe6b0" stroke-width="2.5" stroke-dasharray="16 11"/>`;
    g += `<line x1="40" y1="72" x2="${W*.55}" y2="30" stroke="#ffffff" stroke-opacity=".55" stroke-width="2"/>`;
    g += `<rect x="${W*.6}" y="12" width="70" height="18" rx="3" fill="url(#gSuper)" stroke="#1b232d" stroke-width="2"/>`;
    g += `<circle cx="${W*.6+48}" cy="21" r="5" fill="#2a333e"/>`;
    [70, 140, 210].forEach(x => g += `<path d="M${x} 58 l26 7 l-26 7 l6 -7 z M${x+8} 54 l6 11 l-6 11 z" fill="#d0d8e2" stroke="#3a434d" stroke-width="1"/>`);
  } else if(k === "cruiser"){
    g += hull(W, 20, 80, 62);
    g += `<rect x="24" y="28" width="${W-96}" height="44" rx="10" fill="url(#gDeck)"/>`;
    g += `<rect x="${W*.36}" y="31" width="${W*.24}" height="38" rx="7" fill="url(#gSuper)" stroke="#1b232d" stroke-width="2"/>`;
    g += `<rect x="${W*.36+W*.24-34}" y="36" width="26" height="28" rx="5" fill="#9aa7b6" stroke="#1b232d" stroke-width="2"/>`;
    g += `<ellipse cx="${W*.36+30}" cy="50" rx="13" ry="9" fill="#2a333e"/>`;
    g += turret(W - 100, 1) + turret(W - 160, 1) + turret(70, -1);
  } else if(k === "destroyer"){
    g += hull(W, 25, 75, 56);
    g += `<rect x="24" y="32" width="${W-88}" height="36" rx="9" fill="url(#gDeck)"/>`;
    g += `<rect x="${W*.4}" y="34" width="${W*.26}" height="32" rx="7" fill="url(#gSuper)" stroke="#1b232d" stroke-width="2"/>`;
    g += `<circle cx="${W*.4+22}" cy="50" r="7" fill="#2a333e"/>`;
    g += `<rect x="40" y="38" width="44" height="24" rx="2" fill="#4a5562" stroke="#1b232d" stroke-width="2"/>`;
    g += `<path d="M51 38 v24 M62 38 v24 M73 38 v24" stroke="#2a333e" stroke-width="2"/>`;
    g += turret(W - 88, 1, 10);
  } else if(k === "sub"){
    g += `<path d="M12 50 Q12 27 64 27 L${W-64} 29 Q${W-6} 35 ${W-6} 50 Q${W-6} 65 ${W-64} 71 L64 73 Q12 73 12 50 Z" fill="url(#gSub)" stroke="#0b0f13" stroke-width="3"/>`;
    g += `<path d="M14 50 L2 34 M14 50 L2 66" stroke="#28323d" stroke-width="7" stroke-linecap="round"/>`;
    g += `<rect x="${W*.52}" y="38" width="66" height="24" rx="12" fill="#1d252e" stroke="#4a5868" stroke-width="2"/>`;
    g += `<circle cx="${W*.52+46}" cy="50" r="4" fill="#7d8a99"/>`;
    g += `<line x1="70" y1="50" x2="${W*.52-14}" y2="50" stroke="#4a5868" stroke-width="2" stroke-dasharray="6 8"/>`;
  } else {
    g += hull(W, 30, 70, 46);
    g += `<rect x="${W*.3}" y="37" width="58" height="26" rx="6" fill="url(#gSuper)" stroke="#1b232d" stroke-width="2"/>`;
    g += `<rect x="${W*.3+40}" y="41" width="12" height="18" rx="2" fill="#3b4a5a"/>`;
    g += turret(W - 56, 1, 8);
  }
  return horiz
    ? `<svg viewBox="0 0 ${W} 100" preserveAspectRatio="none">${g}</svg>`
    : `<svg viewBox="0 0 100 ${W}" preserveAspectRatio="none"><g transform="translate(100 0) rotate(90)">${g}</g></svg>`;
}
function addShip(layer, name, cells, cls){
  const rs = cells.map(x => x[0]), cs = cells.map(x => x[1]), len = cells.length;
  const isH = rs.every(v => v === rs[0]);
  const el = div("ship " + (cls || ""));
  el.style.left = Math.min(...cs) * 10 + "%"; el.style.top = Math.min(...rs) * 10 + "%";
  el.style.width = (isH ? len : 1) * 10 + "%"; el.style.height = (isH ? 1 : len) * 10 + "%";
  el.innerHTML = shipSVG(name, len, isH);
  layer.appendChild(el);
}

/* ================= PLATEAUX ================= */
function makeBoard(kind, title, sub){
  const b = div("board " + kind);
  const h = div("bhead hud"); h.innerHTML = `<span>${title}</span><span>${sub || ""}</span>`; b.appendChild(h);
  const extra = div(); b.appendChild(extra);
  const gw = div("gw"); gw.appendChild(div());
  const cols = div("cols"), rows = div("rows");
  for(let i = 0; i < SIZE; i++){
    const a = document.createElement("span"); a.textContent = i + 1; cols.appendChild(a);
    const l = document.createElement("span"); l.textContent = LETTERS[i]; rows.appendChild(l);
  }
  const sea = div("sea");
  const bg = div("layer " + (kind.startsWith("radar") ? "rings" : "waves"));
  const shipsL = div("layer shipsL"), cellsL = div("layer cellsL");
  sea.append(bg, shipsL, cellsL);
  const t = performance.now() / 1000;
  bg.style.animationDelay = -(t % 14) + "s";
  if(kind.startsWith("radar")){ const sw = div("sweep"); sw.style.animationDelay = -(t % 4) + "s"; sea.append(sw, div("layer scan")); }
  gw.append(cols, rows, sea); b.appendChild(gw);
  const cells = [];
  for(let r = 0; r < SIZE; r++){ cells[r] = []; for(let c = 0; c < SIZE; c++){ const el = div("cell"); cellsL.appendChild(el); cells[r][c] = el; } }
  return {b, sea, shipsL, cells, extra};
}
function mark(el, hit){ const m = div("mk " + (hit ? "hit" : "miss")); el.appendChild(m); }
function burst(el, res){
  if(res === "miss"){ el.appendChild(div("ripple")); el.appendChild(div("ripple r2")); }
  else el.appendChild(div("boom" + (res === "sunk" ? " big" : "")));
}

/* ================= RÉSEAU ================= */
async function api(path, body){
  const opt = body === undefined ? {} : {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body)};
  let r;
  try{ r = await fetch(path, opt); }
  catch(e){ throw Object.assign(new Error("Connexion au serveur perdue…"), {status:0}); }
  let d = {}; try{ d = await r.json(); }catch(e){}
  if(!r.ok) throw Object.assign(new Error(d.error || ("Erreur " + r.status)), {status:r.status});
  return d;
}
/* ================= SALONS ================= */
const CODE_RE = /[^ABCDEFGHJKLMNPQRSTUVWXYZ23456789]/g;
const cleanCode = s => String(s || "").toUpperCase().replace(CODE_RE, "").slice(0, 4);
const shareUrl = () => location.origin + "/?p=" + room;

function resetLocal(){
  S = null; lastJson = ""; view = ""; placed = []; sel = 0; hover = null;
  prevMine = -1; prevRecv = -1; prevMyTurn = null; prevPhase = null;
}
function enterRoom(code, tok){
  resetLocal();
  room = code; token = tok;
  store("bn_t_" + code, tok); store("bn_room", code);
  try{ history.replaceState(null, "", "/?p=" + code); }catch(e){}
  $("roomtag").textContent = "Partie " + code;
  $("quit").classList.remove("hidden");
  $("status").classList.remove("hidden");
  setStatus("Connexion à la partie…", "wait"); setLog("");
  poll();
}
async function joinRoom(code){
  const d = await api("/api/join", {code, token: load("bn_t_" + code)});
  enterRoom(d.code, d.token);
}
async function createRoom(){
  const d = await api("/api/create", {});
  enterRoom(d.code, d.token);
}
function leaveToHome(msg){
  room = null; token = null; resetLocal();
  store("bn_room", null);
  try{ history.replaceState(null, "", "/"); }catch(e){}
  showHome(msg);
}
function showHome(msg){
  view = "home";
  $("roomtag").textContent = ""; $("quit").classList.add("hidden");
  $("share").classList.add("hidden"); $("status").classList.add("hidden"); setLog("");
  const last = load("bn_room");
  const m = $("main"); m.innerHTML = "";
  const h = div("home");
  h.innerHTML = `
    <div class="card">
      <h2 class="hud">Nouvelle partie</h2>
      <p>Créez une partie, puis envoyez le code ou le lien à votre adversaire (SMS, WhatsApp…).</p>
      <button id="create" class="primary">⚓ Créer une partie</button>
    </div>
    <div class="card">
      <h2 class="hud">Rejoindre une partie</h2>
      <p>Saisissez le code reçu (4 caractères).</p>
      <div class="joinrow"><input id="codein" maxlength="4" autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="CODE"><button id="joinbtn">Rejoindre</button></div>
    </div>
    ${last ? `<div class="card"><h2 class="hud">Partie en cours</h2><button id="resume">Reprendre la partie ${last}</button></div>` : ""}
    <p class="err" id="homeerr"></p>
    <div class="card" id="installcard">
      <h2 class="hud">Installer sur le téléphone</h2>
      <p id="installtxt">Sur iPhone : bouton Partager, puis « Sur l'écran d'accueil ». Sur Android : menu ⋮ du navigateur, puis « Installer l'application » ou « Ajouter à l'écran d'accueil ».</p>
      <button id="installbtn" class="hidden">📲 Installer l'application</button>
    </div>`;
  m.appendChild(h);
  const err = t => { $("homeerr").textContent = t || ""; };
  if(msg) err(msg);
  const guard = fn => async () => { if(busy) return; busy = true; err(""); try{ await fn(); }catch(e){ err(e.message); } busy = false; };
  $("create").onclick = guard(createRoom);
  const doJoin = guard(async () => {
    const code = cleanCode($("codein").value);
    if(code.length !== 4) throw new Error("Le code doit faire 4 caractères.");
    await joinRoom(code);
  });
  $("joinbtn").onclick = doJoin;
  $("codein").addEventListener("input", e => { e.target.value = cleanCode(e.target.value); });
  $("codein").addEventListener("keydown", e => { if(e.key === "Enter") doJoin(); });
  if(last) $("resume").onclick = guard(() => joinRoom(last));
  if(window.matchMedia && matchMedia("(display-mode: standalone)").matches) $("installcard").classList.add("hidden");
  if(installEvt){ $("installbtn").classList.remove("hidden"); }
  $("installbtn").onclick = async () => { if(!installEvt) return; installEvt.prompt(); try{ await installEvt.userChoice; }catch(e){} installEvt = null; $("installcard").classList.add("hidden"); };
}
function updateShare(){
  const waiting = S && S.players < 2;
  $("share").classList.toggle("hidden", !waiting);
  if(waiting) $("sharecode").textContent = room;
}
$("sharebtn").onclick = async () => {
  const url = shareUrl(), text = "Rejoins-moi à la bataille navale ! Code : " + room;
  if(navigator.share){ try{ await navigator.share({title: "Bataille navale", text, url}); return; }catch(e){ if(e && e.name === "AbortError") return; } }
  try{ await navigator.clipboard.writeText(text + "\n" + url); setLog("Lien copié ! Collez-le dans un SMS ou une messagerie."); }
  catch(e){ window.prompt("Copiez ce lien :", url); }
};
$("quit").onclick = async () => {
  if(!confirm("Quitter cette partie ? Votre adversaire devra en recréer une avec vous.")) return;
  try{ await api("/api/leave", {code: room, token}); }catch(e){}
  store("bn_t_" + room, null);
  leaveToHome();
};
window.addEventListener("beforeinstallprompt", e => {
  e.preventDefault(); installEvt = e;
  const b = $("installbtn"); if(b) b.classList.remove("hidden");
});
if("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

async function poll(){
  if(!room || !token || busyPoll) return;
  busyPoll = true;
  try{
    const d = await api("/api/state?code=" + room + "&token=" + encodeURIComponent(token));
    if(!room) return;
    const j = JSON.stringify(d);
    if(j !== lastJson){ lastJson = j; S = d; updateShare(); render(); }
  }catch(e){
    if(!room) return;
    if(e.status === 404){ leaveToHome("Cette partie n'existe plus (le serveur a peut-être redémarré après une mise en veille). Créez-en une nouvelle."); }
    else if(e.status === 403){
      try{ await joinRoom(room); }
      catch(e2){ leaveToHome(e2.message); }
    }
    else setStatus(e.message, "wait");
  }finally{ busyPoll = false; }
}
let busyPoll = false;
function render(){
  if(S.phase === "placing" && !S.me_ready){
    if(view !== "place"){
      if(view === "game" || placed.length !== S.fleet.length){ placed = S.fleet.map(() => null); sel = 0; }
      buildPlacement();
    }
    placementStatus();
  } else renderGame();
}

/* ================= PLACEMENT ================= */
function shipCells(r, c, len, h){ const a = []; for(let i = 0; i < len; i++) a.push(h ? [r, c + i] : [r + i, c]); return a; }
function occupied(except){ const s = new Set(); placed.forEach((p, i) => { if(p && i !== except) p.forEach(([r, c]) => s.add(key(r, c))); }); return s; }
function canPlace(cells, except){ const occ = occupied(except); return cells.every(([r, c]) => r >= 0 && r < SIZE && c >= 0 && c < SIZE && !occ.has(key(r, c))); }
function shipAt(r, c){ return placed.findIndex(p => p && p.some(([a, b]) => a === r && b === c)); }
function nextFree(){ return placed.findIndex(p => !p); }

function buildPlacement(){
  view = "place"; setLog("");
  const m = $("main"); m.innerHTML = "";
  P = makeBoard("fleetb", "Ma flotte", "Déploiement");
  P.extra.innerHTML = '<div class="hint">Choisissez un navire, puis touchez la mer pour le poser. Touchez un navire posé pour le reprendre.</div><div class="fleet pick" id="fleet"></div>';
  for(let r = 0; r < SIZE; r++) for(let c = 0; c < SIZE; c++){
    const el = P.cells[r][c];
    el.addEventListener("mouseenter", () => { hover = [r, c]; paint(); });
    el.addEventListener("click", () => { hover = [r, c]; clickPlace(r, c); });
  }
  P.sea.addEventListener("mouseleave", () => { hover = null; paint(); });
  const ctr = div("controls");
  ctr.innerHTML = '<button id="rot"></button><button id="rnd">🎲 Aléatoire</button><button id="clr">Effacer</button><button id="ok" class="primary">Valider ✓</button>';
  P.b.appendChild(ctr); m.appendChild(P.b);
  $("rot").onclick = () => { horiz = !horiz; Snd.click(); paint(); };
  $("rnd").onclick = () => { randomFleet(); Snd.click(); paint(); };
  $("clr").onclick = () => { placed = S.fleet.map(() => null); sel = 0; paint(); };
  $("ok").onclick = submitFleet;
  paint();
}
function paint(){
  if(view !== "place") return;
  const fleet = $("fleet"); fleet.innerHTML = "";
  S.fleet.forEach(([name, len], i) => {
    const ch = div("chip" + (i === sel ? " sel" : "") + (placed[i] ? " done" : ""));
    ch.textContent = name + " (" + len + ")";
    ch.onclick = () => { sel = i; if(placed[i]) placed[i] = null; paint(); };
    fleet.appendChild(ch);
  });
  P.shipsL.innerHTML = "";
  placed.forEach((p, i) => { if(p) addShip(P.shipsL, S.fleet[i][0], p, ""); });
  if(hover && sel >= 0 && !placed[sel]){
    const cells = shipCells(hover[0], hover[1], S.fleet[sel][1], horiz);
    addShip(P.shipsL, S.fleet[sel][0], cells, "ghost" + (canPlace(cells, sel) ? "" : " bad"));
  }
  $("rot").textContent = horiz ? "↔ Horizontal" : "↕ Vertical";
  $("ok").disabled = nextFree() >= 0;
}
function clickPlace(r, c){
  const i = shipAt(r, c);
  if(i >= 0){ placed[i] = null; sel = i; Snd.click(); paint(); return; }
  if(sel < 0 || placed[sel]) sel = nextFree();
  if(sel < 0) return;
  const cells = shipCells(r, c, S.fleet[sel][1], horiz);
  if(!canPlace(cells, sel)){ setLog("Impossible de placer ce navire ici."); return; }
  setLog(""); Snd.click();
  placed[sel] = cells; sel = nextFree(); paint();
}
function randomFleet(){
  for(let attempt = 0; attempt < 100; attempt++){
    placed = S.fleet.map(() => null); let ok = true;
    for(let i = 0; i < S.fleet.length && ok; i++){
      ok = false;
      for(let t = 0; t < 300; t++){
        const cells = shipCells(Math.floor(Math.random() * SIZE), Math.floor(Math.random() * SIZE), S.fleet[i][1], Math.random() < .5);
        if(canPlace(cells, i)){ placed[i] = cells; ok = true; break; }
      }
    }
    if(ok) break;
  }
  sel = -1;
}
async function submitFleet(){
  if(busy) return; busy = true;
  try{ await api("/api/place", {code: room, token, ships: placed}); Snd.ping(.18); await poll(); }
  catch(e){ setLog(e.message); }
  busy = false;
}
function placementStatus(){
  if(!S.opp_connected) setStatus("Déployez votre flotte — en attente du 2e joueur…", "wait");
  else setStatus(S.opp_ready ? "Déployez votre flotte — l'adversaire est prêt !" : "Déployez votre flotte", "");
}
document.addEventListener("keydown", e => { if(view === "place" && (e.key === "r" || e.key === "R")){ horiz = !horiz; paint(); } });

/* ================= PARTIE ================= */
function parseCell(s){ return [LETTERS.indexOf(s[0]), parseInt(s.slice(1), 10) - 1]; }

function renderGame(){
  view = "game";
  const me = S.me, myTurn = S.phase === "playing" && S.turn === me, L = S.last;
  const m = $("main"); m.innerHTML = "";

  // --- Sons et animations des nouveaux tirs ---
  const nm = S.my_shots.length, nr = S.shots_on_me.length;
  let freshMine = null, freshRecv = null;
  if(prevMine >= 0 && nm > prevMine && L && L.by === me){ freshMine = L; Snd.impact(L.result); }
  if(prevRecv >= 0 && nr > prevRecv && L && L.by !== me){ freshRecv = L; Snd.whistle(); Snd.impact(L.result, .55); }
  if(prevMyTurn === false && myTurn) Snd.ping(.2, freshRecv ? 1.4 : .2);
  if(S.phase === "over" && prevPhase && prevPhase !== "over") S.winner === me ? Snd.victory(1.2) : Snd.defeat(1.2);
  prevMine = nm; prevRecv = nr; prevMyTurn = myTurn; prevPhase = S.phase;

  // --- Statut ---
  let st = "", cls = "";
  if(S.phase === "placing"){ st = "Flotte déployée. En attente de l'adversaire…"; cls = "wait"; }
  else if(S.phase === "playing"){ st = myTurn ? "À vous de tirer ! Choisissez une cible sur le radar." : "L'adversaire ajuste son tir…"; cls = myTurn ? "myturn" : "wait"; }
  else { const win = S.winner === me; st = win ? "Victoire ! Toute la flotte adverse est au fond." : "Défaite… votre flotte a été coulée."; cls = win ? "win" : "lose"; }
  if(!S.opp_connected && S.phase !== "over") st += " (adversaire déconnecté)";
  setStatus(st, cls);
  if(L){
    const who = L.by === me ? "Vous tirez" : "L'adversaire tire";
    const res = L.result === "miss" ? "dans l'eau." : L.result === "hit" ? "touché !" : L.ship + " coulé !";
    setLog("📡 " + who + " en " + L.cell + " : " + res);
  } else setLog("");

  // --- Radar (grille adverse) ---
  const left = S.fleet.length - S.opp_sunk.length;
  const R = makeBoard("radar" + (myTurn ? " active" : ""), "Radar — zone adverse", left + " cible" + (left > 1 ? "s" : "") + " restante" + (left > 1 ? "s" : ""));
  const of = div("fleet"), remaining = S.opp_sunk.map(s => s.name);
  S.fleet.forEach(([name, len]) => {
    const i = remaining.indexOf(name), isSunk = i >= 0; if(isSunk) remaining.splice(i, 1);
    const ch = div("chip" + (isSunk ? " sunk" : "")); ch.textContent = name + " (" + len + ")"; of.appendChild(ch);
  });
  R.extra.appendChild(of);
  const sunkSet = new Set(); S.opp_sunk.forEach(s => s.cells.forEach(([r, c]) => sunkSet.add(key(r, c))));
  const sunkKeys = new Set(S.opp_sunk.map(s => s.name + s.cells.join()));
  S.opp_reveal.forEach(s => { if(!sunkKeys.has(s.name + s.cells.join())) addShip(R.shipsL, s.name, s.cells, "reveal"); });
  S.opp_sunk.forEach(s => addShip(R.shipsL, s.name, s.cells, "wreck"));
  const shot = new Set();
  S.my_shots.forEach(([r, c, h]) => { shot.add(key(r, c)); const el = R.cells[r][c]; mark(el, h); if(sunkSet.has(key(r, c))) el.classList.add("sunkc"); });
  if(myTurn) for(let r = 0; r < SIZE; r++) for(let c = 0; c < SIZE; c++){
    if(shot.has(key(r, c))) continue;
    const el = R.cells[r][c]; el.classList.add("clickable"); el.onclick = () => fire(r, c);
  }
  if(freshMine){ const [r, c] = parseCell(freshMine.cell); burst(R.cells[r][c], freshMine.result); }
  if(S.phase === "over"){
    const win = S.winner === me, o = div("overmsg hud " + (win ? "win" : "lose"));
    o.textContent = win ? "Victoire" : "Défaite"; R.sea.appendChild(o);
    const ctr = div("controls"), btn = document.createElement("button");
    btn.className = "primary"; btn.textContent = "⟳ Rejouer";
    btn.onclick = async () => { try{ await api("/api/reset", {code: room, token}); await poll(); }catch(e){ setLog(e.message); } };
    ctr.appendChild(btn); R.b.appendChild(ctr);
  }

  // --- Ma flotte ---
  const alive = (S.my_ships || []).filter(s => !s.sunk).length;
  const F = makeBoard("fleetb" + (freshRecv && freshRecv.result !== "miss" ? " hitme" : ""), "Ma flotte", alive + " navire" + (alive > 1 ? "s" : "") + " à flot");
  const mySunk = new Set();
  (S.my_ships || []).forEach(s => { addShip(F.shipsL, s.name, s.cells, s.sunk ? "sunk" : ""); if(s.sunk) s.cells.forEach(([r, c]) => mySunk.add(key(r, c))); });
  S.shots_on_me.forEach(([r, c, h]) => { const el = F.cells[r][c]; mark(el, h); if(mySunk.has(key(r, c))) el.classList.add("sunkc"); });
  if(freshRecv){ const [r, c] = parseCell(freshRecv.cell); setTimeout(() => burst(F.cells[r][c], freshRecv.result), 550); }

  m.append(R.b, F.b);
}
async function fire(r, c){
  if(busy) return; busy = true;
  Snd.launch();
  try{ await api("/api/fire", {code: room, token, r, c}); await poll(); }
  catch(e){ setLog(e.message); }
  busy = false;
}

(async () => {
  const fromUrl = cleanCode(new URLSearchParams(location.search).get("p"));
  if(fromUrl.length === 4){
    try{ await joinRoom(fromUrl); }catch(e){ leaveToHome(e.message); }
  } else showHome();
})();
setInterval(poll, 800);
</script>
</body>
</html>
"""


ICONS_B64 = {
    "/icon-192.png": (
        "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"
    ),
    "/icon-512.png": (
        "iVBORw0KGgoAAAANSUhEUgAAAgAAAAIACAIAAAB7GkOtAAEAAElEQVR42uz9d5wc13UmDJ9z762qjtOTZwAMMAgECBLMQaQokiJF"
        "yZJsJSs4aNdee+21d2159921vd8b1n5fe22vd+2Vg6yVg2xpnSVLsiRbEimRYhRJiZkgQBA5Tk7d06mq7r3n+6O6u0JX98wAIDgA"
        "6mh+1KCnOkzPzHnueZ5znoPWxhsgiSSSSCKJyy9Y8hYkkUQSSSQAkEQSSSSRRAIASSSRRBJJJACQRBJJJJFEAgBJJJFEEkkkAJBE"
        "EkkkkUQCAEkkkUQSSSQAkEQSSSSRRAIASSSRRBJJJACQRBJJJJFEAgBJJJFEEkkkAJBEEkkkkUQCAEkkkUQSSSQAkEQSSSSRRAIA"
        "SSSRRBJJJACQRBJJJJFEAgBJJJFEEkkkAJBEEkkkkUQCAEkkkUQSSSQAkEQSSSSRRAIASSSRRBJJJACQRBJJJJFEAgBJJJFEEkkk"
        "AJBEEkkkkQBAEkkkkUQSCQAkkUQSSSSRAEASSSSRRBIJACSRRBJJJJEAQBJJJJFEEgkAJJFEEkkkkQBAEkkkkUQSCQAkkUQSSSSR"
        "AEASSSSRRBIJACSRRBJJJJEAQBJJJJFEEgkAJJFEEkkkkQBAEkkkkUQSCQAkkUQSSSSRAEASSSSRRBIJACSRRBJJJJEAQBJJJJFE"
        "EgkAJJFEEkkkkQBAEkkkkUQSCQAkkUQSSSSRAEASSSSRRBIJACSRRBJJJJEAQBJJJJFEAgBJJJFEEkkkAJBEEkkkkUQCAEkkkUQS"
        "SSQAkEQSSSSRRAIASSSRRBJJJACQRBJJJJFEAgBJJJFEEkkkAJBEEkkkkUQCAEkkkUQSSSQAkEQSSSSRRAIASSSRRBJJrJMQyVuQ"
        "xKUQ2PoEMfB56BpNFLicwvcOfglY+I7N+xH5n4fun0QSCQAkkcSFSfToJWhEACBNiECSSGlAJFeR1F6CbnzSqnZNDuHEDhwbqVwH"
        "0jmBdlToOQVrPK9gaDAgQs5QIFHzlRB5ryQBhiQSAEgiifOW7hEREAARiIiAXA1Eqq5a+Z1ZnDSJHlP0pclV5sasMZwhrdFguRsH"
        "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"
        "soo/jM+PoQAAAABJRU5ErkJggg=="
    ),
    "/apple-touch-icon.png": (
        "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"
    ),
}
ICONS = {k: base64.b64decode(v) for k, v in ICONS_B64.items()}


def main():
    try:
        server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    except OSError:
        print("Impossible d'ouvrir le port %d (déjà utilisé ?)." % PORT)
        if not ONLINE:
            input("Appuyez sur Entrée pour quitter…")
        return
    if ONLINE:
        print("Bataille navale : serveur en ligne sur le port %d" % PORT, flush=True)
        server.serve_forever()
        return
    ip = local_ip()
    print("=" * 56)
    print("  BATAILLE NAVALE — serveur démarré")
    print("=" * 56)
    print("  Sur les deux appareils (même Wi-Fi), ouvrez :")
    print()
    print("      http://%s:%d" % (ip, PORT))
    print()
    print("  Fermez cette fenêtre (ou Ctrl+C) pour arrêter.")
    print("=" * 56)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    print("Serveur arrêté.")


if __name__ == "__main__":
    main()
