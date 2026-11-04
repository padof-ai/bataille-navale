#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Salle de jeux : page d'accueil, bataille navale (en ligne à deux ou contre
l'ordinateur) et les autres jeux (fichiers .html placés à côté de ce fichier).

- En local : lancez ce fichier sur un PC et ouvrez l'adresse affichée
  (ex. http://192.168.1.20:8000) sur les appareils du même Wi-Fi.
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

try:   # belote en ligne (fichier belote_en_ligne.py posé à côté)
    import belote_en_ligne
except ImportError:
    belote_en_ligne = None

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
BOT_SEAT = 1            # place occupée par l'ordinateur dans une partie solo


def random_fleet():
    """Place les navires au hasard, sans chevauchement."""
    while True:
        used, out = set(), []
        for _name, length in FLEET:
            for _ in range(300):
                horiz = random.random() < .5
                r = random.randrange(SIZE if horiz else SIZE - length + 1)
                c = random.randrange(SIZE - length + 1 if horiz else SIZE)
                cells = [(r, c + k) if horiz else (r + k, c) for k in range(length)]
                if not used & set(cells):
                    break
            else:
                break
            used |= set(cells)
            out.append(cells)
        if len(out) == len(FLEET):
            return out


def neighbors4(r, c):
    return [(r + dr, c + dc) for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1))
            if 0 <= r + dr < SIZE and 0 <= c + dc < SIZE]


class Game:
    def __init__(self, code="", bot=None):
        self.code = code
        self.bot = bot                # None, ou niveau de l'ordinateur : 1, 2 ou 3
        self.players = [None, None]   # {"token": str, "seen": float}
        if bot:
            self.players[BOT_SEAT] = {"token": secrets.token_hex(16), "seen": time.time(), "bot": True}
        self.version = 0
        self.new_round()

    def humans(self):
        return [p for p in self.players if p and not p.get("bot")]

    def last_activity(self):
        return max([p["seen"] for p in self.humans()] or [0])

    # ---------- gestion de la partie ----------
    def new_round(self):
        self.ships = [None, None]     # liste de {"name", "cells": [(r,c)], "hits": set()}
        self.shots = [{}, {}]         # tirs effectués PAR le joueur p : {(r,c): touché?}
        self.phase = "placing"        # placing -> playing -> over
        self.turn = random.randint(0, 1)
        self.winner = None
        self.last = None
        self.last_move_t = time.time()
        self.bot_wait = 1.2
        self.version += 1
        if self.bot:
            self.place(BOT_SEAT, random_fleet())

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
            if not p.get("bot") and now - p["seen"] > STALE_SECONDS:
                self.players[i] = {"token": new_token, "seen": now}
                self.new_round()
                return i, new_token
        return None, None

    def online(self, i):
        p = self.players[i]
        if p and p.get("bot"):
            return True
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
            self.last_move_t = time.time()
            self.bot_wait = 1.0
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
        self.last_move_t = time.time()
        if all(len(s["hits"]) == len(s["cells"]) for s in self.ships[target]):
            self.phase = "over"
            self.winner = i
        else:
            self.turn = target
        self.version += 1

    # ---------- l'ordinateur ----------
    def tick(self):
        """Fait tirer l'ordinateur quand c'est son tour, après un petit temps de réflexion."""
        if not self.bot or self.phase != "playing" or self.turn != BOT_SEAT:
            return
        if time.time() - self.last_move_t < self.bot_wait:
            return
        r, c = self.bot_target()
        self.fire(BOT_SEAT, r, c)
        self.bot_wait = 1.4 + random.random() * .6

    def bot_target(self):
        shots = self.shots[BOT_SEAT]
        free = [(r, c) for r in range(SIZE) for c in range(SIZE) if (r, c) not in shots]
        sunk_cells, remaining = set(), []
        for s in self.ships[1 - BOT_SEAT]:
            if len(s["hits"]) == len(s["cells"]):
                sunk_cells |= set(s["cells"])
            else:
                remaining.append(len(s["cells"]))
        open_hits = [cell for cell, hit in shots.items() if hit and cell not in sunk_cells]

        # Facile : au hasard, avec un peu de suite dans les idées
        if self.bot == 1:
            if open_hits and random.random() < .3:
                cand = [n for (r, c) in open_hits for n in neighbors4(r, c) if n not in shots]
                if cand:
                    return random.choice(cand)
            return random.choice(free)

        # Moyen : chasse en damier, puis ciblage autour des touches
        if self.bot == 2:
            if open_hits:
                hitset, line = set(open_hits), []
                for (r, c) in open_hits:
                    for dr, dc in ((0, 1), (1, 0)):
                        if (r + dr, c + dc) in hitset:
                            for sign in (1, -1):
                                rr, cc = r, c
                                while (rr, cc) in hitset:
                                    rr, cc = rr + dr * sign, cc + dc * sign
                                if 0 <= rr < SIZE and 0 <= cc < SIZE and (rr, cc) not in shots:
                                    line.append((rr, cc))
                if line:
                    return random.choice(line)
                cand = [n for (r, c) in open_hits for n in neighbors4(r, c) if n not in shots]
                if cand:
                    return random.choice(cand)
            step = min(remaining) if remaining else 2
            parity = [(r, c) for (r, c) in free if (r + c) % step == 0] if step <= 3 else []
            return random.choice(parity or free)

        # Difficile : carte des positions encore possibles pour chaque navire restant
        blocked = {cell for cell, hit in shots.items() if not hit} | sunk_cells
        hitset = set(open_hits)
        weight = {}
        for length in remaining:
            for r in range(SIZE):
                for c in range(SIZE):
                    for horiz in (True, False):
                        if (c + length > SIZE) if horiz else (r + length > SIZE):
                            continue
                        cells = [(r, c + k) if horiz else (r + k, c) for k in range(length)]
                        if any(x in blocked for x in cells):
                            continue
                        k = sum(1 for x in cells if x in hitset)
                        w = 1 + 40 * k * k
                        for x in cells:
                            if x not in shots:
                                weight[x] = weight.get(x, 0) + w
        if not weight:
            return random.choice(free)
        best = max(weight.values())
        return random.choice([x for x, w in weight.items() if w == best])

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
            "bot": self.bot,
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
    "name": "Salle de jeux",
    "short_name": "Salle de jeux",
    "lang": "fr",
    "start_url": "/",
    "display": "standalone",
    "orientation": "portrait",
    "background_color": "#17110d",
    "theme_color": "#17110d",
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

    def redirect(self, where):
        self.send_response(302)
        self.send_header("Location", where)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        url = urlparse(self.path)
        p = url.path.rstrip("/") or "/"
        html = "text/html; charset=utf-8"
        if p in ("/", "/index.html"):
            code = norm_code(parse_qs(url.query).get("p", [""])[0])
            if code:   # anciens liens d'invitation : /?p=CODE
                return self.redirect("/bataille-navale?p=" + code)
            self.send_body(200, hub_page(), html)
        elif p == "/bataille-navale":
            self.send_body(200, PAGE.replace("<body>", "<body>" + BACKBAR, 1), html)
        elif p in STATIC_GAMES:
            g = STATIC_GAMES[p]
            page = game_page(g)
            if page is None:
                return self.send_body(404, MISSING_PAGE % esc(g["file"]), html)
            self.send_body(200, page, html)
        elif p == "/manifest.webmanifest":
            self.send_body(200, MANIFEST, "application/manifest+json")
        elif p == "/sw.js":
            self.send_body(200, SERVICE_WORKER, "text/javascript")
        elif p in ICONS:
            self.send_raw(200, ICONS[p], "image/png", cache=True)
        elif p == "/api/belote/state" and belote_en_ligne:
            st, obj = belote_en_ligne.get_state(parse_qs(url.query))
            self.send_json(st, obj)
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
                game.tick()
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
        if url.path.startswith("/api/belote/") and belote_en_ligne:
            st, obj = belote_en_ligne.post(url.path[len("/api/belote/"):], body)
            return self.send_json(st, obj)
        with lock:
            cleanup_rooms()
            if url.path == "/api/create":
                if len(rooms) >= MAX_ROOMS:
                    return self.send_json(503, {"error": "Trop de parties en cours, réessayez plus tard."})
                code = new_code()
                bot = body.get("bot")
                bot = bot if bot in (1, 2, 3) else None
                rooms[code] = Game(code, bot)
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
                    if not game.humans():
                        del rooms[code]
                    return self.send_json(200, {"ok": True})
                else:
                    return self.send_json(404, {"error": "Introuvable."})
            except ValueError as e:
                return self.send_json(400, {"error": str(e)})
            self.send_json(200, game.view(i))


BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
#  Les jeux de la salle : adresse, fichier, nom, accroche, joueurs, durée, modes
# ---------------------------------------------------------------------------
def _p4_art():
    cols = {0: "#1a1a2e", 1: "#e3242b", 2: "#f7c600"}
    grid = [[0, 0, 2, 0], [0, 1, 2, 0], [1, 2, 1, 1]]
    out = '<rect x="34" y="12" width="92" height="76" rx="8" fill="#2a6fe6" stroke="#123c97" stroke-width="2"/>'
    for r, row in enumerate(grid):
        for c, v in enumerate(row):
            cx, cy = 50 + 20 * c, 30 + 20 * r
            out += '<circle cx="%d" cy="%d" r="7.5" fill="%s" stroke="#0e3485" stroke-width="2"/>' % (cx, cy, cols[v])
            if v:
                out += '<circle cx="%d" cy="%d" r="4.5" fill="none" stroke="#000" stroke-opacity=".22" stroke-width="1.2"/>' % (cx, cy)
    return out


ART = {
    "bataille-navale": '''
      <defs><radialGradient id="aBnR" cx="50%" cy="50%" r="50%"><stop offset="0" stop-color="#0b4a2a"/><stop offset="1" stop-color="#03170c"/></radialGradient>
      <linearGradient id="aBnS" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#3cff8c" stop-opacity="0"/><stop offset="1" stop-color="#3cff8c" stop-opacity=".6"/></linearGradient></defs>
      <circle cx="80" cy="50" r="42" fill="url(#aBnR)" stroke="#3cff8c" stroke-width="1.6"/>
      <circle cx="80" cy="50" r="28" fill="none" stroke="#3cff8c" stroke-opacity=".45"/>
      <circle cx="80" cy="50" r="14" fill="none" stroke="#3cff8c" stroke-opacity=".45"/>
      <path d="M38 50H122M80 8V92" stroke="#3cff8c" stroke-opacity=".3"/>
      <g class="sweep"><path d="M80 50 L80 8 A42 42 0 0 1 116.4 29 Z" fill="url(#aBnS)"/></g>
      <path d="M56 58 h34 l9 5.5 l-9 5.5 h-34 z" fill="#8d9aab" stroke="#1b232d" stroke-width="1.3"/>
      <rect x="68" y="60.5" width="13" height="6" rx="1.5" fill="#d3dbe4"/>
      <circle cx="62" cy="63.5" r="2.2" fill="#56626f"/><circle cx="88" cy="63.5" r="2.2" fill="#56626f"/>
      <circle cx="100" cy="31" r="8" fill="#ff8a3d" opacity=".35"/><circle cx="100" cy="31" r="4" fill="#ffd36b"/>''',
    "puissance-4": _p4_art(),
    "dix-de-chute": '''
      <rect x="56" y="5" width="48" height="90" rx="10" fill="#6a3df0" stroke="#2b1580" stroke-width="2"/>
      <rect x="61" y="9" width="10" height="82" rx="5" fill="#120a2e"/>
      <circle cx="88" cy="27" r="12" fill="#19b7c9" stroke="#0b5a68" stroke-width="2"/>
      <circle cx="88" cy="53" r="12" fill="#19b7c9" stroke="#0b5a68" stroke-width="2"/>
      <circle cx="88" cy="79" r="12" fill="#19b7c9" stroke="#0b5a68" stroke-width="2"/>
      <circle cx="78" cy="27" r="3.2" fill="#120a2e" stroke="#ff6b6b" stroke-width="1.4"/>
      <circle cx="94" cy="44" r="3.2" fill="#120a2e" stroke="#ff6b6b" stroke-width="1.4"/>
      <circle cx="80" cy="62" r="3.2" fill="#120a2e" stroke="#ff6b6b" stroke-width="1.4"/>
      <circle cx="96" cy="81" r="3.2" fill="#120a2e" stroke="#ff6b6b" stroke-width="1.4"/>
      <circle cx="66" cy="17" r="4.2" fill="#e3242b"/><circle cx="66" cy="46" r="4.2" fill="#e3242b"/>
      <text x="88" y="31" text-anchor="middle" font-size="11" font-weight="800" fill="#fff" font-family="Nunito,sans-serif">1</text>
      <text x="88" y="57" text-anchor="middle" font-size="11" font-weight="800" fill="#fff" font-family="Nunito,sans-serif">2</text>
      <text x="88" y="83" text-anchor="middle" font-size="11" font-weight="800" fill="#fff" font-family="Nunito,sans-serif">3</text>''',
    "morpion": '''
      <g stroke="#a9c1e6" stroke-width=".8">''' + "".join('<path d="M0 %d H160"/>' % y for y in range(6, 100, 9)) + '''</g>
      <path d="M24 0 V100" stroke="#e07a7a" stroke-width="1.6"/>
      <g stroke="#55555e" stroke-width="2.6" stroke-linecap="round" fill="none">
        <path d="M67 15 C66 40 68 62 66 86"/><path d="M95 14 C96 38 94 64 95 86"/>
        <path d="M40 39 C64 38 96 40 121 39"/><path d="M40 63 C66 64 95 62 121 63"/></g>
      <g stroke="#1f3fa6" stroke-width="3.6" stroke-linecap="round" fill="none">
        <path d="M45 19 L60 34 M60 19 L45 34"/><path d="M73 43 L88 58 M88 43 L73 58"/><path d="M101 68 L116 83 M116 68 L101 83"/></g>
      <g stroke="#c4142c" stroke-width="3.4" fill="none"><circle cx="108" cy="27" r="8"/><circle cx="53" cy="75" r="8"/></g>
      <path d="M40 15 L122 88" stroke="#1f3fa6" stroke-width="3" stroke-linecap="round" opacity=".85"/>''',
    "yams": '''
      <g transform="rotate(-12 60 52)"><rect x="40" y="32" width="40" height="40" rx="8" fill="#fbf6e9" stroke="#d8ccae" stroke-width="1.5"/>
        <g fill="#1b1b22"><circle cx="50" cy="42" r="3.6"/><circle cx="70" cy="42" r="3.6"/><circle cx="50" cy="62" r="3.6"/><circle cx="70" cy="62" r="3.6"/></g><circle cx="60" cy="52" r="3.6" fill="#1b1b22"/></g>
      <g transform="rotate(14 102 48)"><rect x="82" y="28" width="40" height="40" rx="8" fill="#fbf6e9" stroke="#d8ccae" stroke-width="1.5"/>
        <g fill="#1b1b22"><circle cx="92" cy="38" r="3.6"/><circle cx="112" cy="38" r="3.6"/><circle cx="92" cy="58" r="3.6"/><circle cx="112" cy="58" r="3.6"/><circle cx="102" cy="48" r="3.6"/></g></g>''',
    "mastermind": '''
      <rect x="18" y="30" width="124" height="40" rx="12" fill="#3c322b" stroke="#120e0b" stroke-width="2"/>
      ''' + "".join('<circle cx="%d" cy="50" r="10" fill="%s"/><circle cx="%d" cy="46" r="3" fill="#fff" opacity=".7"/>' % (36 + 24 * i, col, 33 + 24 * i)
                    for i, col in enumerate(["#e53935", "#fdd835", "#43a047", "#1e88e5"])) + '''
      <circle cx="125" cy="44" r="3.6" fill="#d81b1b"/><circle cx="135" cy="44" r="3.6" fill="#d81b1b"/>
      <circle cx="125" cy="56" r="3.6" fill="#eee"/><circle cx="135" cy="56" r="3.6" fill="#120e0b"/>''',
    "echecs": "".join('<rect x="%d" y="%d" width="15" height="15" fill="%s"/>' % (50 + 15 * c, 20 + 15 * r, "#ecd3a6" if (r + c) % 2 == 0 else "#a2703f")
                      for r in range(4) for c in range(4)) + '''
      <rect x="50" y="20" width="60" height="60" fill="none" stroke="#d8b46a" stroke-width="1.5"/>
      <text x="80" y="70" text-anchor="middle" font-size="50" font-family="'Noto Sans Symbols 2','Segoe UI Symbol','DejaVu Sans',sans-serif" fill="#fffaf0" stroke="#1d120a" stroke-width="1.6" paint-order="stroke">&#9822;&#65038;</text>''',
    "dames": '''
      <defs><pattern id="aDmG" width="14" height="14" patternUnits="userSpaceOnUse"><rect width="14" height="14" fill="#8c2626"/><rect width="7" height="14" fill="#fff" opacity=".13"/><rect width="14" height="7" fill="#fff" opacity=".13"/></pattern>
      <radialGradient id="aDmW" cx="38%" cy="32%" r="70%"><stop offset="0" stop-color="#fbf1dc"/><stop offset=".6" stop-color="#ead3a8"/><stop offset="1" stop-color="#c9a675"/></radialGradient>
      <radialGradient id="aDmB" cx="38%" cy="32%" r="70%"><stop offset="0" stop-color="#5a3a28"/><stop offset=".6" stop-color="#2a1a10"/><stop offset="1" stop-color="#140b06"/></radialGradient></defs>
      <rect width="160" height="100" fill="url(#aDmG)"/>
      <circle cx="62" cy="60" r="19" fill="url(#aDmB)"/><circle cx="62" cy="60" r="11" fill="none" stroke="#000" stroke-opacity=".45"/><circle cx="62" cy="60" r="5" fill="none" stroke="#000" stroke-opacity=".45"/>
      <circle cx="100" cy="56" r="19" fill="#b8955f"/><circle cx="100" cy="48" r="19" fill="url(#aDmW)"/>
      <path d="M89 52 l4 -10 l7 6 l7 -6 l4 10 z" fill="#e9b949" stroke="#7a5310" stroke-width="1.2" stroke-linejoin="round"/>''',
    "decal": '''
      <g transform="rotate(-14 52 54)"><rect x="37" y="32" width="30" height="42" rx="5" fill="#fffdf7" stroke="#3a74d1" stroke-width="2.4"/><text x="52" y="61" text-anchor="middle" font-size="20" font-weight="800" fill="#3a74d1" font-family="Fredoka,Nunito,sans-serif">3</text></g>
      <g><rect x="65" y="26" width="30" height="42" rx="5" fill="#fffdf7" stroke="#3a74d1" stroke-width="2.4"/><text x="80" y="55" text-anchor="middle" font-size="20" font-weight="800" fill="#3a74d1" font-family="Fredoka,Nunito,sans-serif">3</text></g>
      <g transform="rotate(14 108 54)"><rect x="93" y="32" width="30" height="42" rx="5" fill="#24356b" stroke="#fff8ec" stroke-width="2.4"/><text x="108" y="59" text-anchor="middle" font-size="16" font-weight="800" fill="#fff8ec" font-family="Nunito,sans-serif">&#8646;</text></g>''',
    "skyjo": '''
      <g transform="rotate(-10 58 52)"><rect x="40" y="24" width="36" height="52" rx="6" fill="#2fa84f" stroke="#fff" stroke-width="2.4"/><text x="58" y="58" text-anchor="middle" font-size="22" font-weight="800" fill="#fff" font-family="Fredoka,Nunito,sans-serif">4</text></g>
      <g><rect x="62" y="20" width="36" height="52" rx="6" fill="#2a63d4" stroke="#fff" stroke-width="2.4"/><text x="80" y="54" text-anchor="middle" font-size="20" font-weight="800" fill="#fff" font-family="Fredoka,Nunito,sans-serif">-2</text></g>
      <g transform="rotate(10 102 52)"><rect x="84" y="24" width="36" height="52" rx="6" fill="#e2453c" stroke="#fff" stroke-width="2.4"/><text x="102" y="58" text-anchor="middle" font-size="20" font-weight="800" fill="#fff" font-family="Fredoka,Nunito,sans-serif">12</text></g>''',
    "uno": '''
      <g transform="rotate(-12 62 52)"><rect x="44" y="20" width="38" height="58" rx="6" fill="#e3242b" stroke="#fff" stroke-width="3"/><ellipse cx="63" cy="49" rx="14" ry="20" fill="#fff" transform="rotate(25 63 49)"/><text x="63" y="57" text-anchor="middle" font-size="22" font-weight="800" fill="#e3242b" font-family="Fredoka,Nunito,sans-serif">7</text></g>
      <g transform="rotate(10 98 50)"><rect x="78" y="20" width="38" height="58" rx="6" fill="#1b1b22" stroke="#fff" stroke-width="3"/>
        <circle cx="91" cy="41" r="6" fill="#e3242b"/><circle cx="103" cy="41" r="6" fill="#f7c600"/><circle cx="91" cy="55" r="6" fill="#2a63d4"/><circle cx="103" cy="55" r="6" fill="#2fa84f"/></g>''',
    "mille-bornes": '''
      <rect x="20" y="66" width="120" height="14" rx="3" fill="#3b3b44"/><path d="M28 73 H44 M56 73 H72 M84 73 H100 M112 73 H128" stroke="#f6f0e4" stroke-width="2.4"/>
      <path d="M62 20 h36 a10 10 0 0 1 10 10 v30 h-56 v-30 a10 10 0 0 1 10 -10 z" fill="#fffaf0" stroke="#c4142c" stroke-width="3"/>
      <rect x="52" y="22" width="56" height="12" rx="4" fill="#c4142c"/>
      <text x="80" y="54" text-anchor="middle" font-size="17" font-weight="800" fill="#1b1b22" font-family="Nunito,sans-serif">1000</text>''',
    "belote": '''
      <rect x="10" y="8" width="140" height="84" rx="14" fill="#145a3a" stroke="#d6b26a" stroke-width="2"/>
      <g transform="rotate(-14 62 54)"><rect x="44" y="24" width="36" height="52" rx="5" fill="#fffdf7" stroke="#cfc7b5"/><text x="62" y="47" text-anchor="middle" font-size="16" font-weight="700" fill="#c8202c" font-family="Georgia,serif">R</text><text x="62" y="66" text-anchor="middle" font-size="16" fill="#c8202c">&#9829;</text></g>
      <g transform="rotate(12 98 52)"><rect x="80" y="22" width="36" height="52" rx="5" fill="#fffdf7" stroke="#cfc7b5"/><text x="98" y="45" text-anchor="middle" font-size="16" font-weight="700" fill="#c8202c" font-family="Georgia,serif">D</text><text x="98" y="64" text-anchor="middle" font-size="16" fill="#c8202c">&#9829;</text></g>''',
    "rummikub": '''
      <rect x="18" y="62" width="124" height="16" rx="4" fill="#b88a4a" stroke="#6b4a1f" stroke-width="2"/>
      <rect x="24" y="24" width="26" height="36" rx="4" fill="#fbf6e9" stroke="#d8ccae" stroke-width="1.5"/><text x="37" y="49" text-anchor="middle" font-size="17" font-weight="800" fill="#c4142c" font-family="Nunito,sans-serif">7</text><rect x="53" y="24" width="26" height="36" rx="4" fill="#fbf6e9" stroke="#d8ccae" stroke-width="1.5"/><text x="66" y="49" text-anchor="middle" font-size="17" font-weight="800" fill="#1f3fa6" font-family="Nunito,sans-serif">7</text><rect x="82" y="24" width="26" height="36" rx="4" fill="#fbf6e9" stroke="#d8ccae" stroke-width="1.5"/><text x="95" y="49" text-anchor="middle" font-size="17" font-weight="800" fill="#1b1b22" font-family="Nunito,sans-serif">7</text><rect x="111" y="24" width="26" height="36" rx="4" fill="#fbf6e9" stroke="#d8ccae" stroke-width="1.5"/><text x="124" y="49" text-anchor="middle" font-size="17" font-weight="800" fill="#e08a00" font-family="Nunito,sans-serif">7</text>
      <path d="M128 24 l0 0" />''',
    "tarot": '''
      <rect x="10" y="8" width="140" height="84" rx="14" fill="#16335c" stroke="#d9b25f" stroke-width="2"/>
      <g transform="rotate(-12 60 52)"><rect x="42" y="18" width="34" height="62" rx="5" fill="#f6ecd0" stroke="#c9b27a"/><circle cx="59" cy="49" r="12" fill="#fffaf0" stroke="#c8202c" stroke-width="4" stroke-dasharray="4 3"/><text x="59" y="54" text-anchor="middle" font-size="13" font-weight="700" fill="#2a3f8f" font-family="Georgia,serif">21</text></g>
      <g transform="rotate(10 100 50)"><rect x="83" y="18" width="34" height="62" rx="5" fill="#f6dca0" stroke="#c9b27a"/><text x="100" y="58" text-anchor="middle" font-size="26" fill="#7a4a0a">&#9733;</text></g>''',
}

GAMES = [
    {"slug": "bataille-navale", "file": None, "name": "Bataille navale", "lid": "#0e2a4a",
     "hook": "Coulez la flotte adverse sur deux téléphones, chacun chez soi, ou affrontez l'amiral de l'ordinateur.",
     "facts": "1–2 joueurs · 15 min", "modes": ["En ligne à deux", "Contre l'ordi"], "new": "Nouveau : contre l'ordinateur"},
    {"slug": "puissance-4", "file": "puissance4.html", "name": "Puissance 4", "lid": "#f7c600",
     "hook": "Alignez quatre pions avant l'autre.", "facts": "1–2 joueurs · 5 min", "modes": ["Contre l'ordi", "Même écran"]},
    {"slug": "dix-de-chute", "file": "dix_de_chute.html", "name": "Dix de Chute", "lid": "#19a7b8",
     "hook": "Tournez les roues, faites tomber vos jetons.", "facts": "1–2 joueurs · 10 min", "modes": ["Contre l'ordi", "Même écran"]},
    {"slug": "morpion", "file": "morpion.html", "name": "Morpion", "lid": "#fdfcf6",
     "hook": "Le classique du cahier d'écolier.", "facts": "1–2 joueurs · 2 min", "modes": ["Contre l'ordi", "Même écran"]},
    {"slug": "yams", "file": "yams.html", "name": "Yam's", "lid": "#1e6b45",
     "hook": "Trois lancers pour remplir la feuille.", "facts": "1–2 joueurs · 15 min", "modes": ["Solo", "Contre l'ordi", "Même écran"]},
    {"slug": "mastermind", "file": "mastermind.html", "name": "Mastermind", "lid": "#5a4636",
     "hook": "Percez le code secret en dix essais.", "facts": "1–2 joueurs · 10 min", "modes": ["Contre l'ordi", "Même écran"]},
    {"slug": "echecs", "file": "echecs.html", "name": "Échecs", "lid": "#2e5a45",
     "hook": "Toutes les règles, trois niveaux d'ordinateur.", "facts": "1–2 joueurs · 30 min", "modes": ["Contre l'ordi", "Même écran"]},
    {"slug": "dames", "file": "dames.html", "name": "Dames", "lid": "#8c2626",
     "hook": "Damier de 10 × 10, règles françaises.", "facts": "1–2 joueurs · 30 min", "modes": ["Contre l'ordi", "Même écran"]},
    {"slug": "decal", "file": "decal.html", "name": "Décal", "lid": "#e2572c",
     "hook": "Glissez vos cartes, formez des paires.", "facts": "1–4 joueurs · 15 min", "modes": ["Contre l'ordi", "Même écran"]},
    {"slug": "skyjo", "file": "skyjo.html", "name": "Skyjo", "lid": "#2fa84f",
     "hook": "Retournez, échangez, faites le plus petit total.", "facts": "1–4 joueurs · 20 min", "modes": ["Contre l'ordi", "Même écran"], "new": "Nouveau"},
    {"slug": "uno", "file": "uno.html", "name": "Uno", "lid": "#e3242b",
     "hook": "Couleur ou chiffre, et n'oubliez pas de dire Uno !", "facts": "1–4 joueurs · 15 min", "modes": ["Contre l'ordi", "Même écran"], "new": "Nouveau"},
    {"slug": "mille-bornes", "file": "mille_bornes.html", "name": "1000 Bornes", "lid": "#c4142c",
     "hook": "Feux rouges, crevaisons et as du volant.", "facts": "1–4 joueurs · 25 min", "modes": ["Contre l'ordi", "Même écran"], "new": "Nouveau"},
    {"slug": "belote", "file": "belote.html", "name": "Belote", "lid": "#145a3a",
     "hook": "Belote, rebelote et dix de der, à quatre en ligne ou avec l'ordinateur.", "facts": "1–4 joueurs · 30 min", "modes": ["En ligne", "Contre l'ordi"], "new": "Nouveau : en ligne"},
    {"slug": "rummikub", "file": "rummikub.html", "name": "Rummikub", "lid": "#b88a4a",
     "hook": "Suites et séries de tuiles, la table se réarrange.", "facts": "1–4 joueurs · 30 min", "modes": ["Contre l'ordi", "Même écran"], "new": "Nouveau"},
    {"slug": "tarot", "file": "tarot.html", "name": "Tarot", "lid": "#16335c",
     "hook": "Petite, garde, garde sans… et le petit au bout.", "facts": "3–4 joueurs · 30 min", "modes": ["Contre l'ordi"], "new": "Nouveau"},
]
STATIC_GAMES = {"/" + g["slug"]: g for g in GAMES if g["file"]}


def game_available(g):
    return g["file"] is None or os.path.isfile(os.path.join(BASE_DIR, g["file"]))


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def hub_page():
    boxes = []
    for g in GAMES:
        ok = game_available(g)
        feat = g["slug"] == "bataille-navale"
        tag = "a" if ok else "div"
        href = ' href="/%s"' % g["slug"] if ok else ' aria-disabled="true"'
        chips = "".join("<li>%s</li>" % esc(m) for m in g["modes"])
        sticker = '<span class="sticker">%s</span>' % esc(g["new"]) if g.get("new") else ""
        missing = "" if ok else '<p class="missing">Fichier « %s » absent du serveur</p>' % esc(g["file"])
        boxes.append(
            '<%s class="box%s%s"%s style="--lid:%s">%s'
            '<div class="art"><svg viewBox="0 0 160 100" aria-hidden="true">%s</svg></div>'
            '<div class="label"><h2>%s</h2><p class="hook">%s</p><p class="facts">%s</p><ul class="modes">%s</ul>%s</div></%s>'
            % (tag, " feat" if feat else "", "" if ok else " off", href, g["lid"], sticker, ART[g["slug"]],
               esc(g["name"]), esc(g["hook"]), esc(g["facts"]), chips, missing, tag))
    return HUB_PAGE.replace("<!--BOXES-->", "\n".join(boxes)).replace("<!--COUNT-->", str(len(GAMES)))


HUB_PAGE = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Salle de jeux</title>
<link rel="manifest" href="/manifest.webmanifest">
<link rel="icon" type="image/png" href="/icon-192.png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<meta name="theme-color" content="#17110d">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Salle de jeux">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Luckiest+Guy&family=Nunito:wght@600;800&family=Fredoka:wght@700&family=Noto+Sans+Symbols+2&display=swap">
<style>
/* Layout : une armoire à jeux ; chaque jeu est une boîte, couvercle illustré en haut et étiquette sur la tranche. La bataille navale, seul jeu en ligne, occupe une rangée entière. */
:root{
  color-scheme:dark;
  --wall:#17110d;        /* mur de la salle */
  --lamp:#3a2616;        /* halo de la lampe */
  --side:#231a14;        /* tranche des boîtes */
  --cream:#f6eedf;
  --muted:#c2b29c;
  --gold:#f2c14e;
  --display:"Luckiest Guy","Arial Black","Trebuchet MS",sans-serif;
  --ui:"Nunito","Segoe UI",system-ui,sans-serif;
}
*{box-sizing:border-box}
[hidden]{display:none!important}
html,body{min-height:100%}
body{margin:0;font-family:var(--ui);color:var(--cream);background:radial-gradient(ellipse 80% 50% at 50% 0,var(--lamp) 0,var(--wall) 70%) fixed,var(--wall);
  padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}
.wrap{max-width:1080px;margin:0 auto;padding-block:26px 34px;padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));
  display:flex;flex-direction:column;gap:22px}
header{display:flex;flex-direction:column;gap:6px}
h1{margin:0;font-family:var(--display);font-weight:400;font-size:clamp(2.6rem,10vw,4.2rem);line-height:.95;letter-spacing:.02em;color:var(--gold);
  text-shadow:0 4px 0 #7a4a14,0 10px 24px rgba(0,0,0,.5);text-wrap:balance}
.sub{margin:0;color:var(--muted);font-size:1.02rem;max-width:56ch;line-height:1.45}
.sub b{color:var(--cream)}

.shelf{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,230px),1fr));gap:18px}
@media (max-width:560px){.shelf{grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.label{padding:10px 10px 12px;gap:5px}.label h2{font-size:1.2rem}.hook{font-size:.8rem}.modes li{font-size:.68rem;padding:3px 6px}.box:not(.feat) .art{aspect-ratio:4/3}.sticker{font-size:.7rem;top:8px}}
.box{position:relative;display:flex;flex-direction:column;border-radius:14px;overflow:hidden;text-decoration:none;color:inherit;background:var(--side);
  box-shadow:0 6px 0 #0d0906,0 14px 26px rgba(0,0,0,.45);transition:transform .18s ease,box-shadow .18s ease}
a.box:hover{transform:translateY(-4px);box-shadow:0 10px 0 #0d0906,0 20px 30px rgba(0,0,0,.5)}
a.box:active{transform:translateY(0)}
a.box:focus-visible{outline:3px solid var(--gold);outline-offset:4px}
.art{position:relative;aspect-ratio:16/9;max-width:100%;background:var(--lid);display:flex;align-items:center;justify-content:center;
  box-shadow:inset 0 2px 0 rgba(255,255,255,.25),inset 0 -10px 18px rgba(0,0,0,.18)}
.art::after{content:"";position:absolute;inset:0;background:linear-gradient(160deg,rgba(255,255,255,.18),transparent 40%);pointer-events:none}
.art svg{width:86%;height:86%;overflow:visible;filter:drop-shadow(0 4px 4px rgba(0,0,0,.35))}
.label{display:flex;flex-direction:column;gap:6px;padding:12px 14px 14px;border-top:3px solid rgba(0,0,0,.35)}
.label h2{margin:0;font-family:var(--display);font-weight:400;font-size:1.45rem;letter-spacing:.03em;line-height:1}
.hook{margin:0;color:var(--muted);font-size:.9rem;line-height:1.4}
.facts{margin:0;font-weight:800;font-size:.82rem;color:var(--gold);font-variant-numeric:tabular-nums}
.modes{list-style:none;margin:2px 0 0;padding:0;display:flex;flex-wrap:wrap;gap:5px}
.modes li{font-size:.74rem;font-weight:800;padding:3px 8px;border-radius:999px;background:rgba(246,238,223,.1);color:var(--cream)}
.sticker{position:absolute;z-index:2;top:12px;right:-6px;background:var(--gold);color:#3a2400;font-weight:800;font-size:.78rem;padding:6px 14px 6px 12px;border-radius:6px 0 0 6px;
  box-shadow:0 3px 6px rgba(0,0,0,.35);transform:rotate(3deg)}
.box.feat{grid-column:1/-1}
@media (min-width:640px){
  .box.feat{flex-direction:row}
  .box.feat .art{flex:1.15;aspect-ratio:auto;min-height:230px}
  .box.feat .label{flex:1;justify-content:center;border-top:0;border-left:3px solid rgba(0,0,0,.35);padding:22px 24px}
  .box.feat h2{font-size:2.2rem}
  .box.feat .hook{font-size:1rem}
}
.box.feat .modes li:first-child{background:#3cff8c;color:#03210f}
.box.off{opacity:.55;filter:grayscale(.7)}
.missing{margin:0;font-size:.78rem;color:#ffb3a8;font-weight:800}
.sweep{transform-box:view-box;transform-origin:80px 50px;animation:spin 4s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

.tip{display:flex;flex-wrap:wrap;align-items:center;gap:12px 16px;padding:14px 16px;border-radius:14px;background:rgba(246,238,223,.06);border:1px solid rgba(246,238,223,.14)}
.tip p{margin:0;flex:1;min-width:220px;color:var(--muted);font-size:.9rem;line-height:1.45}
.tip b{color:var(--cream)}
.tip button{font:inherit;font-weight:800;border:0;border-radius:10px;padding:10px 14px;background:var(--gold);color:#3a2400;cursor:pointer}
.tip button:focus-visible{outline:3px solid var(--cream);outline-offset:2px}
@media (prefers-reduced-motion:reduce){.sweep{animation:none}.box{transition:none}}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Salle de jeux</h1>
    <p class="sub"><b><!--COUNT--> jeux de société</b>, avec leurs vrais plateaux et leurs bruits de pions. Seul contre l'ordinateur, à plusieurs sur le même écran, ou à deux sur deux téléphones pour la bataille navale.</p>
  </header>
  <main class="shelf">
<!--BOXES-->
  </main>
  <div class="tip" id="tip">
    <p><b>Comme une application.</b> Sur iPhone : bouton Partager, puis « Sur l'écran d'accueil ». Sur Android : menu ⋮ du navigateur, puis « Installer l'application ».</p>
    <button id="install" hidden>📲 Installer</button>
  </div>
</div>
<script>
(() => {
  let evt = null;
  const tip = document.getElementById("tip"), btn = document.getElementById("install");
  if(window.matchMedia && matchMedia("(display-mode: standalone)").matches) tip.hidden = true;
  window.addEventListener("beforeinstallprompt", e => { e.preventDefault(); evt = e; btn.hidden = false; });
  btn.onclick = async () => { if(!evt) return; evt.prompt(); try{ await evt.userChoice; }catch(e){} evt = null; tip.hidden = true; };
  if("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
})();
</script>
</body>
</html>
"""

# Barre « retour » ajoutée en haut de chaque jeu
BACKBAR = ('<style>.sdj-back{position:relative;z-index:40;max-width:1100px;margin:0 auto;padding-top:8px;'
           'padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}'
           '.sdj-back a{display:inline-flex;align-items:center;gap:6px;font:700 13px/1 system-ui,-apple-system,"Segoe UI",sans-serif;'
           'color:#fff;text-decoration:none;background:rgba(0,0,0,.4);border:1px solid rgba(255,255,255,.2);padding:7px 12px 7px 10px;border-radius:999px}'
           '.sdj-back a:hover{background:rgba(0,0,0,.6)}.sdj-back a:focus-visible{outline:3px solid #f2c14e;outline-offset:2px}</style>'
           '<nav class="sdj-back"><a href="/">&#8249; Salle de jeux</a></nav>')

GAME_HEAD = ('<!doctype html><html lang="fr"><head><meta charset="utf-8">'
             '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
             '<link rel="manifest" href="/manifest.webmanifest"><link rel="icon" type="image/png" href="/icon-192.png">'
             '<link rel="apple-touch-icon" href="/apple-touch-icon.png">'
             '<meta name="apple-mobile-web-app-capable" content="yes"><meta name="mobile-web-app-capable" content="yes">'
             '<style>:root{color-scheme:light;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}'
             'body{margin:0;font:14px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif}img{max-width:100%}'
             '[hidden]{display:none!important}</style></head><body>')


def game_page(g):
    """Lit le fichier du jeu et l'habille d'une page complète, avec le bouton de retour."""
    try:
        with open(os.path.join(BASE_DIR, g["file"]), encoding="utf-8") as f:
            html = f.read()
    except OSError:
        return None
    return GAME_HEAD + BACKBAR + html + "</body></html>"


MISSING_PAGE = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Jeu introuvable</title>
<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#17110d;color:#f6eedf;
font:16px/1.5 system-ui,sans-serif;padding:24px}div{max-width:420px}a{color:#f2c14e}</style></head>
<body><div><h1>Ce jeu n'est pas encore installé</h1><p>Le fichier « %s » doit être déposé sur GitHub à côté de bataille_navale.py.</p>
<p><a href="/">Revenir à la salle de jeux</a></p></div></body></html>"""


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
.card .lvls{display:flex;gap:6px;margin:0 0 10px}
.card .lvls button{flex:1;width:auto;padding:9px 4px;font-size:.85rem}
.card .lvls button[aria-pressed="true"]{background:var(--green);border-color:var(--green);color:#03210f}
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
const shareUrl = () => location.origin + "/bataille-navale?p=" + room;
const OPP = () => S && S.bot ? "L'ordinateur" : "L'adversaire";
const LEVELS = {1: "Facile", 2: "Moyen", 3: "Difficile"};

function resetLocal(){
  S = null; lastJson = ""; view = ""; placed = []; sel = 0; hover = null;
  prevMine = -1; prevRecv = -1; prevMyTurn = null; prevPhase = null;
}
function enterRoom(code, tok){
  resetLocal();
  room = code; token = tok;
  store("bn_t_" + code, tok); store("bn_room", code);
  try{ history.replaceState(null, "", "/bataille-navale?p=" + code); }catch(e){}
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
async function createRoom(bot){
  const d = await api("/api/create", bot ? {bot} : {});
  enterRoom(d.code, d.token);
}
function leaveToHome(msg){
  room = null; token = null; resetLocal();
  store("bn_room", null);
  try{ history.replaceState(null, "", "/bataille-navale"); }catch(e){}
  showHome(msg);
}
function showHome(msg){
  view = "home";
  $("roomtag").textContent = ""; $("quit").classList.add("hidden");
  $("share").classList.add("hidden"); $("status").classList.add("hidden"); setLog("");
  const last = load("bn_room");
  const m = $("main"); m.innerHTML = "";
  const h = div("home");
  let lvl = +(load("bn_lvl") || 2); if(![1, 2, 3].includes(lvl)) lvl = 2;
  h.innerHTML = `
    <div class="card">
      <h2 class="hud">Contre l'ordinateur</h2>
      <p>Une partie en solo, sur cet appareil. Choisissez la force de l'amiral adverse.</p>
      <div class="lvls" role="group" aria-label="Niveau de l'ordinateur">${[1, 2, 3].map(l => `<button data-l="${l}" aria-pressed="${l === lvl}">${LEVELS[l]}</button>`).join("")}</div>
      <button id="solo" class="primary">🤖 Jouer contre l'ordinateur</button>
    </div>
    <div class="card">
      <h2 class="hud">À deux, en ligne</h2>
      <p>Créez une partie, puis envoyez le code ou le lien à votre adversaire (SMS, WhatsApp…). Chacun joue sur son téléphone, où qu'il soit.</p>
      <button id="create">⚓ Créer une partie</button>
    </div>
    <div class="card">
      <h2 class="hud">Rejoindre une partie</h2>
      <p>Saisissez le code reçu (4 caractères).</p>
      <div class="joinrow"><input id="codein" maxlength="4" autocomplete="off" autocapitalize="characters" spellcheck="false" placeholder="CODE"><button id="joinbtn">Rejoindre</button></div>
    </div>
    ${last ? `<div class="card"><h2 class="hud">Partie en cours</h2><button id="resume">Reprendre la partie ${last}</button></div>` : ""}
    <p class="err" id="homeerr"></p>`;
  m.appendChild(h);
  const err = t => { $("homeerr").textContent = t || ""; };
  if(msg) err(msg);
  const guard = fn => async () => { if(busy) return; busy = true; err(""); try{ await fn(); }catch(e){ err(e.message); } busy = false; };
  $("create").onclick = guard(() => createRoom(0));
  h.querySelectorAll(".lvls button").forEach(b => b.onclick = () => {
    lvl = +b.dataset.l; store("bn_lvl", String(lvl));
    h.querySelectorAll(".lvls button").forEach(x => x.setAttribute("aria-pressed", x === b));
  });
  $("solo").onclick = guard(() => createRoom(lvl));
  const doJoin = guard(async () => {
    const code = cleanCode($("codein").value);
    if(code.length !== 4) throw new Error("Le code doit faire 4 caractères.");
    await joinRoom(code);
  });
  $("joinbtn").onclick = doJoin;
  $("codein").addEventListener("input", e => { e.target.value = cleanCode(e.target.value); });
  $("codein").addEventListener("keydown", e => { if(e.key === "Enter") doJoin(); });
  if(last) $("resume").onclick = guard(() => joinRoom(last));
}
function updateShare(){
  const waiting = S && S.players < 2;
  $("share").classList.toggle("hidden", !waiting);
  if(waiting) $("sharecode").textContent = room;
  if(S) $("roomtag").textContent = S.bot ? "Ordi · " + LEVELS[S.bot] : "Partie " + room;
}
$("sharebtn").onclick = async () => {
  const url = shareUrl(), text = "Rejoins-moi à la bataille navale ! Code : " + room;
  if(navigator.share){ try{ await navigator.share({title: "Bataille navale", text, url}); return; }catch(e){ if(e && e.name === "AbortError") return; } }
  try{ await navigator.clipboard.writeText(text + "\n" + url); setLog("Lien copié ! Collez-le dans un SMS ou une messagerie."); }
  catch(e){ window.prompt("Copiez ce lien :", url); }
};
$("quit").onclick = async () => {
  if(!confirm(S && S.bot ? "Abandonner cette partie contre l'ordinateur ?" : "Quitter cette partie ? Votre adversaire devra en recréer une avec vous.")) return;
  try{ await api("/api/leave", {code: room, token}); }catch(e){}
  store("bn_t_" + room, null);
  leaveToHome();
};
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
  else setStatus(S.bot ? "Déployez votre flotte — l'ordinateur a déjà caché la sienne." : S.opp_ready ? "Déployez votre flotte — l'adversaire est prêt !" : "Déployez votre flotte", "");
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
  else if(S.phase === "playing"){ st = myTurn ? "À vous de tirer ! Choisissez une cible sur le radar." : OPP() + " ajuste son tir…"; cls = myTurn ? "myturn" : "wait"; }
  else { const win = S.winner === me; st = win ? "Victoire ! Toute la flotte adverse est au fond." : "Défaite… votre flotte a été coulée."; cls = win ? "win" : "lose"; }
  if(!S.opp_connected && S.phase !== "over") st += " (adversaire déconnecté)";
  setStatus(st, cls);
  if(L){
    const who = L.by === me ? "Vous tirez" : OPP() + " tire";
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
        "e8rVFlWQ+iY/WZjOH72RXnf4iZOnzj3y9Jfs21tmE6lSMr05WWgHzJD9vfrynzSXJ9loqzU25gKN6UCJugYc9iqv8UwfHThYsy2B"
        "Sjn0CZV+pro+qdOFoUxyRWbU6lQq6pe/JQqbvZFlw/A8nDoNY7B3T29Qse2OqeKxJ7B7N0pFxLVpXfc+IGLEyVLuu/0DP3zg2mt8"
        "33dxESQR0Xbz/q8/fUcx0YMokDeF4vT87Jtunby0WHzq+SjvpTq6koJwn+icRj+6ch4Ek0AHTNxusiZuV0kRCOs2fYajB9AYQBgT"
        "Q+Mosq4qfcbCMn3TOzK0mBOUy1hZwZmz2DWfIYTiwupd82g08ORT2LUL5RKiKBFvycRSMercsv9d+YPvO3BgN5RE9MrBTXYiUFQ8"
        "60+WZouvP7J88nTz8RMo5BGLU0qmg00y/98rFx4KG+VY/DBRLHsQP0hIJVscHTrS9Bmr9DQrGWImS7kuDFBWHIrWoMhS5ybP0zMX"
        "+NYb3B23SrPRK4SYkc/j1Gk0mjiwPyM1FsubfftQq+GJpzA9hckJRC7WXHDCcFIxb9Xd7z9wzYHY+b2C0ZMSR6LCbPKFyejIrpXP"
        "Ph6eX0Crez+CzrL5o/rKB2oL02y0ZfqYthZrsTxwHIQnpXX57YMSDD3PJxJoEIYoa9z6MAxRh+G6XRxdb+Ab7gl72jPigNDEBIIA"
        "z59CqYC5uezsmAj27gEUjz4OJ5idhbGIHIlDQHtrU//h4HW3mjb9x1WxEZGqeuz501ON+dzSP34u/tcImGDzVNj88dWzNhE64Lb5"
        "nDwAte+jVJf3aOU1vBKoS/x0nm+rsFEYokzu3wxjqO3fxiO98r4+/Ry/7vbophu02ewypWMBMzWJCxdw/gL27oHvZ8ihWGHt2YXp"
        "aTzzDJ47CWKUS2xIl71vmzr47smpknNCVwt6OhiC+JzTXaXlZ05UHz9Bed9TVCE/vnLuvLgCGxCYKTZ9YsnD3Bp2Tb2Fez3Ka4jp"
        "M9zt6kFXB0AbxBANIJhmQiPAxSV6zzui/h4xVeRyKORx8hQqVRzYP5j9JEK5jAP7EYZ45gSfOiXOO1C49od377+OBlbVX+GbgthY"
        "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"
    ),
    "/icon-512.png": (
        "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"
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
        print("Salle de jeux : serveur en ligne sur le port %d" % PORT, flush=True)
        server.serve_forever()
        return
    ip = local_ip()
    print("=" * 56)
    print("  SALLE DE JEUX — serveur démarré")
    print("=" * 56)
    print("  Sur vos appareils (même Wi-Fi), ouvrez :")
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
