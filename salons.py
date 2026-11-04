# -*- coding: utf-8 -*-
"""
Tables en ligne génériques pour les jeux de la salle (routes /api/net/...).

Le serveur ne connaît pas les règles : il tient le salon (places, code,
réglages) et le journal des coups. Chaque navigateur rejoue le même
journal avec le même hasard (graine commune), si bien que tout le monde
voit la même partie. L'hôte (le créateur, ou le suivant s'il part) calcule
les coups de l'ordinateur pour les places vides ou abandonnées.
"""
import random
import secrets
import threading
import time

CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ROOM_TTL = 3 * 3600
MAX_ROOMS = 300
ONLINE_SECONDS = 8      # en dessous : connecté
AWAY_SECONDS = 25       # au-delà : l'ordinateur joue à sa place
MAX_LOG = 20000

rooms = {}
lock = threading.Lock()


def _name(n, fallback):
    n = "".join(ch for ch in str(n or "") if ch.isprintable()).strip()[:14]
    return n or fallback


class Room:
    def __init__(self, code, game, cfg, n, nmin, nmax, bots):
        self.code = code
        self.game = game
        self.cfg = cfg if isinstance(cfg, dict) else {}
        self.nmin, self.nmax = nmin, nmax
        self.n = max(nmin, min(nmax, n))
        self.allow_bots = bots
        self.seats = [None] * nmax
        self.host = 0
        self.phase = "lobby"
        self.seed = 0
        self.log = []
        self.bots = []
        self.touched = time.time()
        self.events = []      # arrivées / départs
        self.ev_n = 0

    def event(self, k, p, t):
        self.ev_n += 1
        self.events.append({"n": self.ev_n, "k": k, "p": p, "t": t})
        del self.events[:-30]

    def find(self, token):
        if not token:
            return None
        for i, s in enumerate(self.seats):
            if s and s["token"] == token:
                return i
        return None

    def humans(self):
        return [i for i, s in enumerate(self.seats) if s]

    def touch(self, i):
        self.touched = time.time()
        if i is not None and self.seats[i]:
            self.seats[i]["seen"] = time.time()

    def online(self, i):
        s = self.seats[i]
        return bool(s) and time.time() - s["seen"] < ONLINE_SECONDS

    def away(self, i):
        s = self.seats[i]
        return not s or time.time() - s["seen"] > AWAY_SECONDS

    def sit(self, name, seat=None):
        if self.phase != "lobby":
            return None, None
        free = [i for i in range(self.n) if self.seats[i] is None]
        if not free:
            return None, None
        if seat not in free:
            seat = free[0]
        token = secrets.token_hex(12)
        self.seats[seat] = {"name": _name(name, "Joueur %d" % (seat + 1)), "token": token, "seen": time.time()}
        self.event("join", seat, self.seats[seat]["name"])
        return seat, token

    def fix_host(self):
        """Si l'hôte est parti ou absent, un autre joueur connecté prend le relais."""
        if self.seats[self.host] and not self.away(self.host):
            return
        cands = [i for i in self.humans() if not self.away(i)] or self.humans()
        if cands:
            self.host = cands[0]

    def view(self, i, since):
        self.fix_host()
        names = [s["name"] if s else "" for s in self.seats[:self.n]]
        return {
            "code": self.code, "game": self.game, "phase": self.phase, "n": self.n, "nmin": self.nmin, "nmax": self.nmax,
            "me": i, "host": self.host, "names": names,
            "humans": [bool(s) for s in self.seats[:self.n]],
            "online": [self.online(k) for k in range(self.n)],
            "away": [self.away(k) for k in range(self.n)],
            "bots": self.bots, "cfg": self.cfg, "seed": self.seed, "allowBots": self.allow_bots,
            "total": len(self.log), "from": since, "log": self.log[since:since + 400],
            "events": self.events,
        }


def _cleanup():
    now = time.time()
    for code in [c for c, r in rooms.items() if now - r.touched > ROOM_TTL]:
        del rooms[code]


def _code():
    while True:
        c = "".join(random.choice(CODE_CHARS) for _ in range(4))
        if c not in rooms:
            return c


def _norm(code):
    return "".join(ch for ch in str(code or "").upper() if ch in CODE_CHARS)[:4]


def _int(v, d):
    return v if isinstance(v, int) and not isinstance(v, bool) else d


def get_state(query):
    code = _norm(query.get("code", [""])[0])
    token = query.get("token", [""])[0]
    try:
        since = max(0, int(query.get("since", ["0"])[0]))
    except ValueError:
        since = 0
    with lock:
        r = rooms.get(code)
        if r is None:
            return 404, {"error": "Table introuvable (le serveur a peut-être redémarré)."}
        i = r.find(token)
        if i is None:
            return 403, {"error": "Vous n'êtes plus à cette table."}
        r.touch(i)
        return 200, r.view(i, since)


def post(action, body):
    with lock:
        _cleanup()
        if action == "create":
            if len(rooms) >= MAX_ROOMS:
                return 503, {"error": "Trop de tables en cours, réessayez plus tard."}
            game = str(body.get("game") or "")[:30]
            nmin = max(2, min(8, _int(body.get("min"), 2)))
            nmax = max(nmin, min(8, _int(body.get("max"), nmin)))
            n = _int(body.get("n"), nmax)
            code = _code()
            r = Room(code, game, body.get("cfg"), n, nmin, nmax, bool(body.get("bots", True)))
            rooms[code] = r
            seat, token = r.sit(body.get("name"), 0)
            r.host = seat
            return 200, {"code": code, "token": token, "seat": seat}
        code = _norm(body.get("code"))
        r = rooms.get(code)
        if r is None:
            return 404, {"error": "Table introuvable. Vérifiez le code."}
        if action == "join":
            game = str(body.get("game") or "")
            if game and r.game != game:
                return 409, {"error": "Ce code correspond à une table d'un autre jeu."}
            i = r.find(body.get("token"))
            if i is not None:
                r.touch(i)
                return 200, {"code": code, "token": body.get("token"), "seat": i}
            if r.phase != "lobby":
                return 409, {"error": "La partie a déjà commencé à cette table."}
            seat, token = r.sit(body.get("name"))
            if seat is None:
                return 409, {"error": "La table est complète."}
            return 200, {"code": code, "token": token, "seat": seat}
        i = r.find(body.get("token"))
        if i is None:
            return 403, {"error": "Vous n'êtes plus à cette table."}
        r.touch(i)
        if action == "seat":
            s = body.get("seat")
            if r.phase != "lobby" or not isinstance(s, int) or not 0 <= s < r.n or r.seats[s] is not None:
                return 400, {"error": "Cette place n'est pas libre."}
            r.seats[s], r.seats[i] = r.seats[i], None
            if r.host == i:
                r.host = s
            i = s
        elif action == "setn":
            n = body.get("n")
            if i != r.host or r.phase != "lobby" or not isinstance(n, int) or not r.nmin <= n <= r.nmax:
                return 400, {"error": "Impossible de changer le nombre de places."}
            if any(r.seats[k] for k in range(n, r.nmax)):
                return 400, {"error": "Des joueurs sont assis sur ces places."}
            r.n = n
        elif action == "cfg":
            if i != r.host or r.phase != "lobby":
                return 400, {"error": "Seul l'hôte peut changer les réglages."}
            if isinstance(body.get("cfg"), dict):
                r.cfg = body["cfg"]
        elif action == "start":
            if i != r.host or r.phase != "lobby":
                return 400, {"error": "Seul l'hôte peut lancer la partie."}
            humans = sum(1 for k in range(r.n) if r.seats[k])
            if not r.allow_bots and humans < r.n:
                return 400, {"error": "Il faut que toutes les places soient prises."}
            if humans < 2 and not body.get("solo"):
                return 400, {"error": "Attendez qu'au moins un autre joueur rejoigne la table."}
            r.phase = "play"
            r.seed = random.randrange(1, 2 ** 31)
            r.bots = [r.seats[k] is None for k in range(r.n)]
        elif action == "act":
            if r.phase != "play":
                return 400, {"error": "La partie n'a pas commencé."}
            seat = body.get("seat", i)
            if not isinstance(seat, int) or not 0 <= seat < r.n:
                return 400, {"error": "Place inconnue."}
            r.fix_host()
            if seat != i and not (i == r.host and (r.bots[seat] or r.away(seat))):
                return 403, {"error": "Ce n'est pas votre place."}
            expect = body.get("at")
            if isinstance(expect, int) and expect != len(r.log):
                return 409, {"error": "conflit", "total": len(r.log)}
            if len(r.log) >= MAX_LOG:
                return 400, {"error": "Partie trop longue."}
            r.log.append({"s": seat, "a": body.get("a"), "c": str(body.get("c") or "")[:16]})
        elif action == "leave":
            name = r.seats[i]["name"]
            r.seats[i] = None
            r.event("leave", i, name)
            if r.phase == "play" and i < len(r.bots):
                r.bots[i] = True
            if not r.humans():
                del rooms[code]
                return 200, {"ok": True}
            r.fix_host()
            return 200, {"ok": True}
        else:
            return 404, {"error": "Introuvable."}
        return 200, r.view(i, _int(body.get("since"), len(r.log)))
