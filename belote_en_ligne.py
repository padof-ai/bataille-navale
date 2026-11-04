# -*- coding: utf-8 -*-
"""
Belote en ligne : tables de 4 places avec code, l'ordinateur remplace les
places vides (ou un joueur qui s'est absenté trop longtemps).
Le serveur arbitre tout : distribution, enchères, cartes permises, points.
Utilisé par bataille_navale.py (routes /api/belote/...).
"""
import random
import secrets
import threading
import time

CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
ROOM_TTL = 3 * 3600
MAX_ROOMS = 200
ONLINE_SECONDS = 8        # en dessous : joueur connecté
AWAY_SECONDS = 30         # au-delà : l'ordinateur joue à sa place quand c'est son tour
AWAY_WAIT = 6             # ... après avoir attendu ce délai sur son tour
BOT_NAMES = ["Léon", "Rosa", "Gaston", "Lulu"]

SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["7", "8", "9", "10", "V", "D", "R", "A"]
T_ORD = ["7", "8", "D", "R", "10", "A", "9", "V"]
P_ORD = ["7", "8", "9", "V", "D", "R", "10", "A"]
T_PTS = {"V": 20, "9": 14, "A": 11, "10": 10, "R": 4, "D": 3, "8": 0, "7": 0}
P_PTS = {"A": 11, "10": 10, "R": 4, "D": 3, "V": 2, "9": 0, "8": 0, "7": 0}
CARRE = {"V": 200, "9": 150, "A": 100, "10": 100, "R": 100, "D": 100}
CARRE_NAME = {"V": "valets", "9": "neuf", "A": "as", "10": "dix", "R": "rois", "D": "dames"}

rooms = {}
lock = threading.Lock()


def team(i):
    return i % 2


def nxt(i):
    return (i + 1) % 4


def card(i):
    return {"s": i // 8, "r": RANKS[i % 8], "id": i}


def clean_name(n, fallback):
    n = "".join(ch for ch in str(n or "") if ch.isprintable()).strip()[:14]
    return n or fallback


class Belote:
    def __init__(self, code, cfg):
        self.code = code
        c = cfg if isinstance(cfg, dict) else {}
        def pick(k, allowed, d):
            v = c.get(k)
            return v if v in allowed else d
        self.cfg = {"lvl": pick("lvl", (1, 2, 3), 2), "goal": pick("goal", (501, 1000), 1000), "ann": pick("ann", (0, 1), 1),
                    "strict": pick("strict", (0, 1), 0), "tie": pick("tie", (0, 1), 1), "r2": pick("r2", (0, 1), 1)}
        self.seats = [None, None, None, None]   # {"name", "token", "seen"} ou None (ordinateur)
        self.creator = 0
        self.phase = "lobby"
        self.events = []
        self.ev_n = 0
        self.created = time.time()
        self.touched = time.time()
        self.score = [0, 0]
        self.hist = []
        self.dealer = random.randrange(4)
        self.litige = 0
        self.donne = 0
        self.over = False
        self.detail = None
        self.msg = ""
        self.t_next = 0
        self.turn_t = 0
        self._reset_deal()

    # ---------- joueurs ----------
    def find(self, token):
        if not token:
            return None
        for i, s in enumerate(self.seats):
            if s and s["token"] == token:
                return i
        return None

    def touch(self, i):
        self.touched = time.time()
        if i is not None and self.seats[i]:
            self.seats[i]["seen"] = time.time()

    def humans(self):
        return [i for i, s in enumerate(self.seats) if s]

    def last_activity(self):
        return self.touched

    def online(self, i):
        s = self.seats[i]
        return bool(s) and time.time() - s["seen"] < ONLINE_SECONDS

    def away(self, i):
        s = self.seats[i]
        return not s or time.time() - s["seen"] > AWAY_SECONDS

    def sit(self, name, seat=None):
        free = [i for i in range(4) if self.seats[i] is None]
        if not free:
            return None, None
        if seat not in free:
            # pendant la partie on remplace un ordinateur ; au salon on prend la 1re place libre
            seat = free[0]
        token = secrets.token_hex(12)
        self.seats[seat] = {"name": clean_name(name, "Joueur %d" % (seat + 1)), "token": token, "seen": time.time()}
        self.event("join", seat, self.seats[seat]["name"])
        return seat, token

    def move(self, i, seat):
        if self.phase != "lobby":
            raise ValueError("La partie a déjà commencé.")
        if seat not in (0, 1, 2, 3) or self.seats[seat] is not None:
            raise ValueError("Cette place est prise.")
        self.seats[seat], self.seats[i] = self.seats[i], None
        if self.creator == i:
            self.creator = seat
        return seat

    def leave(self, i):
        name = self.seats[i]["name"]
        self.seats[i] = None
        self.event("leave", i, name)
        if self.creator == i and self.humans():
            self.creator = self.humans()[0]

    def names(self):
        return [s["name"] if s else BOT_NAMES[i] for i, s in enumerate(self.seats)]

    def event(self, kind, p=-1, text=""):
        self.ev_n += 1
        self.events.append({"n": self.ev_n, "k": kind, "p": p, "t": text})
        del self.events[:-40]

    # ---------- donne ----------
    def _reset_deal(self):
        self.hands = [[], [], [], []]
        self.deck = []
        self.trick = []
        self.tricks = [[], []]
        self.last_trick = None
        self.trump = -1
        self.taker = -1
        self.turned = None
        self.cur = -1
        self.belote = [False] * 4
        self.bel_said = [0] * 4
        self.decl = [[], [], [], []]
        self.ann_win = -1
        self.ann_by = [0, 0]
        self.won = [0, 0]
        self.trick_no = 0
        self.bid_count = 0

    def start(self):
        if self.phase != "lobby":
            raise ValueError("La partie a déjà commencé.")
        self.new_deal()

    def new_game(self):
        self.score = [0, 0]
        self.hist = []
        self.litige = 0
        self.donne = 0
        self.over = False
        self.new_deal()

    def new_deal(self):
        self._reset_deal()
        self.donne += 1
        self.dealer = nxt(self.dealer)
        self.detail = None
        self.msg = ""
        self.deck = list(range(32))
        random.shuffle(self.deck)
        for n in (3, 2):
            for k in range(4):
                p = (self.dealer + 1 + k) % 4
                for _ in range(n):
                    self.hands[p].append(self.deck.pop())
        self.turned = self.deck.pop()
        self.phase = "bid1"
        self.cur = nxt(self.dealer)
        self.event("deal", self.dealer)
        self._turn(1.4)

    def _turn(self, delay):
        now = time.time()
        self.t_next = now + delay
        self.turn_t = now

    def pts(self, c):
        r = RANKS[c % 8]
        return T_PTS[r] if c // 8 == self.trump else P_PTS[r]

    def power(self, c, led):
        s, r = c // 8, RANKS[c % 8]
        if s == self.trump:
            return 100 + T_ORD.index(r)
        if s == led:
            return P_ORD.index(r)
        return -1

    def winner_idx(self, trick):
        led = trick[0][1] // 8
        best = 0
        for k, (p, c) in enumerate(trick):
            if self.power(c, led) > self.power(trick[best][1], led):
                best = k
        return best

    def legal(self, i):
        hand, tr = self.hands[i], self.trick
        if not tr:
            return hand[:]
        led = tr[0][1] // 8
        follow = [c for c in hand if c // 8 == led]
        hi = max([-1] + [T_ORD.index(RANKS[c % 8]) for p, c in tr if c // 8 == self.trump])
        trumps = [c for c in hand if c // 8 == self.trump]
        over = [c for c in trumps if T_ORD.index(RANKS[c % 8]) > hi]
        if led == self.trump:
            if follow:
                return over or follow
            return hand[:]
        if follow:
            return follow
        w = tr[self.winner_idx(tr)][0]
        if not trumps:
            return hand[:]
        if team(w) == team(i) and not self.cfg["strict"]:
            others = [c for c in hand if c // 8 != self.trump]
            return others + ((over or trumps) if hi >= 0 else trumps)
        return over or trumps

    def declarations(self, hand):
        out, used = [], set()
        for r, v in CARRE.items():
            cs = [c for c in hand if RANKS[c % 8] == r]
            if len(cs) == 4:
                out.append({"kind": "carre", "pts": v, "top": RANKS.index(r), "len": 4, "r": r, "s": -1})
                used.update(cs)
        for s in range(4):
            idx = sorted(c % 8 for c in hand if c // 8 == s and c not in used)
            run = []
            def flush():
                if len(run) >= 3:
                    out.append({"kind": "seq", "pts": 20 if len(run) == 3 else 50 if len(run) == 4 else 100,
                                "top": run[-1], "len": len(run), "s": s, "r": ""})
            for x in idx:
                if run and x == run[-1] + 1:
                    run.append(x)
                else:
                    flush()
                    run = [x]
            flush()
        return out

    @staticmethod
    def decl_name(d):
        if d["kind"] == "carre":
            return "Carré de " + CARRE_NAME[d["r"]]
        return "Tierce" if d["len"] == 3 else "Cinquante" if d["len"] == 4 else "Cent"

    def decl_key(self, d):
        if d["kind"] == "carre":
            return (2, d["pts"], d["top"], 0)
        return (1, min(d["len"], 5), d["top"], 1 if d["s"] == self.trump else 0)

    # ---------- actions ----------
    def bid(self, i, s):
        if self.phase not in ("bid1", "bid2") or self.cur != i:
            raise ValueError("Ce n'est pas à vous d'annoncer.")
        if s is None or s < 0:
            self.event("pass", i, "Je passe")
            self.bid_count += 1
            if self.bid_count == 4:
                if self.phase == "bid1" and self.cfg["r2"]:
                    self.phase = "bid2"
                    self.bid_count = 0
                    self.cur = nxt(self.dealer)
                    self.msg = "Personne ne prend : second tour, chacun peut choisir une autre couleur."
                    self.event("msg", -1, self.msg)
                    self._turn(2.0)
                    return
                self.msg = "Personne ne prend : on redistribue."
                self.event("msg", -1, self.msg)
                self.phase = "redeal"
                self.cur = -1
                self._turn(2.2)
                return
            self.cur = nxt(self.cur)
            self._turn(1.0)
            return
        turned_s = self.turned // 8
        if self.phase == "bid1" and s != turned_s:
            raise ValueError("Au premier tour, on prend à la couleur retournée.")
        if self.phase == "bid2" and (s == turned_s or s not in (0, 1, 2, 3)):
            raise ValueError("Choisissez une autre couleur.")
        self.trump, self.taker = s, i
        self.msg = ""
        self.event("take", i, "Je prends à %s !" % SUITS[s])
        self.hands[i].append(self.turned)
        self.turned = None
        for k in range(4):
            p = (self.dealer + 1 + k) % 4
            for _ in range(2 if p == i else 3):
                self.hands[p].append(self.deck.pop())
        for p in range(4):
            h = self.hands[p]
            self.belote[p] = (s * 8 + RANKS.index("R")) in h and (s * 8 + RANKS.index("D")) in h
        if self.cfg["ann"]:
            self.decl = [self.declarations(h) for h in self.hands]
            best, best_p = None, -1
            for k in range(4):
                p = (self.dealer + 1 + k) % 4
                for d in self.decl[p]:
                    if best is None or self.decl_key(d) > self.decl_key(best):
                        best, best_p = d, p
            self.ann_win = team(best_p) if best_p >= 0 else -1
        self.phase = "play"
        self.cur = nxt(self.dealer)
        self.trick_no = 1
        self._turn(1.6)

    def play(self, i, cid):
        if self.phase != "play" or self.cur != i or len(self.trick) == 4:
            raise ValueError("Ce n'est pas à vous de jouer.")
        if cid not in self.legal(i):
            raise ValueError("Cette carte n'est pas permise.")
        self.hands[i].remove(cid)
        self.trick.append((i, cid))
        cut = len(self.trick) > 1 and cid // 8 == self.trump and self.trick[0][1] // 8 != self.trump
        self.event("play", i, "cut" if cut else "")
        delay = 0.9
        if self.cfg["ann"] and self.trick_no == 1 and self.decl[i]:
            self.event("ann", i, " + ".join(self.decl_name(d) for d in self.decl[i]) + " !")
            delay += 0.8
        r = RANKS[cid % 8]
        if self.belote[i] and cid // 8 == self.trump and r in ("R", "D"):
            self.bel_said[i] += 1
            self.event("belote", i, "Belote !" if self.bel_said[i] == 1 else "Rebelote !")
            delay += 0.5
        if len(self.trick) < 4:
            self.cur = nxt(i)
            self._turn(delay)
        else:
            self.cur = -1
            self._turn(1.5)

    def collect(self):
        w = self.trick[self.winner_idx(self.trick)][0]
        tp = sum(self.pts(c) for p, c in self.trick) + (10 if self.trick_no == 8 else 0)
        self.tricks[team(w)].extend(c for p, c in self.trick)
        self.won[team(w)] += 1
        self.last_trick = {"cards": [{"p": p, "c": card(c)} for p, c in self.trick], "w": w, "tp": tp}
        self.trick = []
        self.event("trick", w, str(tp))
        delay = 0.5
        if self.cfg["ann"] and self.trick_no == 1 and self.ann_win >= 0:
            lst = [d for p in range(4) if team(p) == self.ann_win for d in self.decl[p]]
            self.ann_by[self.ann_win] = sum(d["pts"] for d in lst)
            self.event("annwin", self.ann_win, ", ".join("%s (%d)" % (self.decl_name(d), d["pts"]) for d in lst))
            delay = 1.6
        if self.trick_no == 8:
            return self.end_deal()
        self.trick_no += 1
        self.cur = w
        self._turn(delay)

    def end_deal(self):
        self.phase = "end"
        self.cur = -1
        tk = self.taker
        tt, dt = team(tk), 1 - team(tk)
        cardp = [sum(self.pts(c) for c in self.tricks[t]) for t in (0, 1)]
        cardp[team(self.last_trick["w"])] += 10
        bel = [0, 0]
        for p in range(4):
            if self.belote[p] and self.bel_said[p] >= 2:
                bel[team(p)] += 20
        ann = self.ann_by[:]
        capot = 0 if self.won[0] == 8 else 1 if self.won[1] == 8 else -1
        if capot >= 0:
            cardp[capot] = 252
            cardp[1 - capot] = 0
            ann[1 - capot] = 0
        tot = [cardp[t] + ann[t] + bel[t] for t in (0, 1)]
        res = [0, 0]
        litige_in = self.litige
        if capot >= 0 and capot == dt:
            res[dt] = 252 + self.ann_by[0] + self.ann_by[1] + bel[dt]
            res[tt] = bel[tt]
            kind = "capotdef"
        elif tot[tt] > tot[dt] or (tot[tt] == tot[dt] and not self.cfg["tie"]):
            res = tot[:]
            kind = "capot" if capot == tt else "ok"
        elif tot[tt] == tot[dt]:
            res[dt] = tot[dt]
            res[tt] = bel[tt]
            self.litige += tot[tt] - bel[tt]
            kind = "litige"
        else:
            res[dt] = 162 + self.ann_by[0] + self.ann_by[1] + bel[dt]
            res[tt] = bel[tt]
            kind = "dedans"
        lit_to = -1
        if litige_in and kind != "litige":
            lit_to = 0 if res[0] >= res[1] else 1
            res[lit_to] += litige_in
            self.litige = 0
        self.score[0] += res[0]
        self.score[1] += res[1]
        self.hist.append({"t": SUITS[self.trump], "tk": tk, "r": res[:]})
        self.over = max(self.score) >= self.cfg["goal"] and self.score[0] != self.score[1]
        self.detail = {"card": cardp, "ann": ann, "bel": bel, "res": res, "kind": kind, "tot": tot, "tk": tk,
                       "capot": capot, "litigeIn": litige_in, "litTo": lit_to, "litige": self.litige}
        self.event("end", tk, kind)

    def next_deal(self):
        if self.phase != "end":
            return
        if self.over:
            self.new_game()
        else:
            self.new_deal()

    # ---------- ordinateur ----------
    def auto(self, i):
        """Vrai si l'ordinateur doit jouer pour la place i."""
        if self.seats[i] is None:
            return True
        return self.away(i) and time.time() - self.turn_t > AWAY_WAIT

    def tick(self):
        now = time.time()
        for _ in range(6):
            if now < self.t_next:
                return
            if self.phase == "redeal":
                self.new_deal()
            elif self.phase in ("bid1", "bid2") and self.auto(self.cur):
                self.bid(self.cur, self.ai_bid(self.cur))
            elif self.phase == "play" and len(self.trick) == 4:
                self.collect()
            elif self.phase == "play" and self.cur >= 0 and self.auto(self.cur):
                self.play(self.cur, self.ai_card(self.cur))
            else:
                return

    def hand_value(self, hand, s):
        v = 0.0
        t = [RANKS[c % 8] for c in hand if c // 8 == s]
        has = lambda r: r in t
        if has("V"): v += 1.05
        if has("9"): v += .9 if has("V") else .55
        if has("A"): v += .65
        if has("10"): v += .45
        v += sum(1 for r in t if r in ("R", "D", "8", "7")) * .3
        if len(t) >= 4: v += (len(t) - 3) * .35
        for c in hand:
            if c // 8 == s:
                continue
            r = RANKS[c % 8]
            if r == "A": v += .8
            elif r == "10" and (c // 8 * 8 + 7) in hand: v += .5
        if has("R") and has("D"): v += .25
        return v

    def ai_bid(self, i):
        h, lvl = self.hands[i], self.cfg["lvl"]
        thr = 2.9 + random.random() * .6 if lvl == 1 else 3.35 if lvl == 2 else 3.25
        ts = self.turned // 8
        if self.phase == "bid1":
            v = self.hand_value(h + [self.turned], ts)
            if lvl == 3 and team(i) == team(self.dealer) and i != self.dealer and RANKS[self.turned % 8] == "V":
                v += .2
            return ts if v >= thr else -1
        best, bv = -1, 0
        for s in range(4):
            if s == ts:
                continue
            v = self.hand_value(h, s) + .35
            if v > bv:
                best, bv = s, v
        return best if bv >= thr else -1

    def ai_card(self, i):
        lvl, L, hand = self.cfg["lvl"], self.legal(i), self.hands[i]
        if len(L) == 1:
            return L[0]
        if lvl == 1 and random.random() < .35:
            return random.choice(L)
        played = set(self.tricks[0]) | set(self.tricks[1]) | {c for p, c in self.trick}
        unseen = [c for c in range(32) if c not in played and c not in hand]
        T = self.trump
        def is_master(c):
            s, r = c // 8, RANKS[c % 8]
            if s == T:
                return not any(x // 8 == T and T_ORD.index(RANKS[x % 8]) > T_ORD.index(r) for x in unseen)
            return not any(x // 8 == s and P_ORD.index(RANKS[x % 8]) > P_ORD.index(r) for x in unseen)
        trumps_out = sum(1 for c in unseen if c // 8 == T)
        def low(arr):
            return sorted(arr, key=lambda c: (self.pts(c), 1 if c // 8 == T else 0, self.power(c, c // 8)))[0]
        def high(arr):
            return sorted(arr, key=lambda c: -self.pts(c))[0]
        if not self.trick:
            my_trumps = [c for c in L if c // 8 == T]
            if team(i) == team(self.taker) and my_trumps and trumps_out > 0:
                m = next((c for c in my_trumps if is_master(c)), None)
                if m is not None:
                    return m
                if i == self.taker and lvl >= 2:
                    return sorted(my_trumps, key=lambda c: T_ORD.index(RANKS[c % 8]))[0]
            masters = [c for c in L if c // 8 != T and is_master(c)]
            if masters:
                return high(masters)
            plain = [c for c in L if c // 8 != T and not (RANKS[c % 8] == "10" and (c // 8 * 8 + 7) not in hand)]
            return low(plain or L)
        led = self.trick[0][1] // 8
        wk = self.winner_idx(self.trick)
        wp, wc = self.trick[wk]
        last = len(self.trick) == 3
        partner = team(wp) == team(i)
        trick_pts = sum(self.pts(c) for p, c in self.trick)
        if partner and (last or is_master(wc) or (wc // 8 == T and led != T)):
            give = [c for c in L if c // 8 != T]
            if give:
                return high(give)
            small = [c for c in L if RANKS[c % 8] not in ("V", "9")]
            return low(small or L)
        win = [c for c in L if self.power(c, led) > self.power(wc, led)]
        if win:
            cheap = sorted(win, key=lambda c: self.power(c, led))
            if last:
                return cheap[0]
            sure = [c for c in cheap if is_master(c)]
            if sure:
                if led == T:
                    return sure[0]
                return next((c for c in sure if c // 8 != T), sure[0])
            if cheap[0] // 8 == T and led != T and (trick_pts >= 10 or lvl >= 2):
                return cheap[0]
            if lvl == 3 and trick_pts < 4:
                return low(L)
            return cheap[0]
        return low(L)

    # ---------- vue d'un joueur ----------
    def view(self, i):
        names = self.names()
        return {
            "code": self.code, "me": i, "phase": self.phase, "creator": self.creator, "cfg": self.cfg,
            "names": names, "bots": [s is None for s in self.seats],
            "online": [self.online(k) for k in range(4)],
            "dealer": self.dealer, "cur": self.cur, "taker": self.taker, "trump": self.trump,
            "turned": card(self.turned) if self.turned is not None else None,
            "hand": [card(c) for c in self.hands[i]], "counts": [len(h) for h in self.hands],
            "trick": [{"p": p, "c": card(c)} for p, c in self.trick],
            "lastTrick": self.last_trick, "score": self.score, "litige": self.litige, "donne": self.donne,
            "hist": self.hist, "detail": self.detail, "over": self.over, "msg": self.msg,
            "trickNo": self.trick_no, "events": self.events,
        }


# ---------------------------------------------------------------------------
#  Routes (appelées par bataille_navale.py)
# ---------------------------------------------------------------------------
def _cleanup():
    now = time.time()
    for code in [c for c, g in rooms.items() if now - g.last_activity() > ROOM_TTL]:
        del rooms[code]


def _new_code():
    while True:
        code = "".join(random.choice(CODE_CHARS) for _ in range(4))
        if code not in rooms:
            return code


def _norm(code):
    return "".join(ch for ch in str(code or "").upper() if ch in CODE_CHARS)[:4]


def get_state(query):
    """GET /api/belote/state?code=..&token=.."""
    code = _norm(query.get("code", [""])[0])
    token = query.get("token", [""])[0]
    with lock:
        g = rooms.get(code)
        if g is None:
            return 404, {"error": "Table introuvable (le serveur a peut-être redémarré)."}
        i = g.find(token)
        if i is None:
            return 403, {"error": "Vous n'êtes plus à cette table."}
        g.touch(i)
        g.tick()
        return 200, g.view(i)


def post(action, body):
    with lock:
        _cleanup()
        if action == "create":
            if len(rooms) >= MAX_ROOMS:
                return 503, {"error": "Trop de tables en cours, réessayez plus tard."}
            code = _new_code()
            g = Belote(code, body.get("cfg"))
            rooms[code] = g
            seat, token = g.sit(body.get("name"), 0)
            g.creator = seat
            return 200, {"code": code, "token": token, "seat": seat}
        code = _norm(body.get("code"))
        g = rooms.get(code)
        if g is None:
            return 404, {"error": "Table introuvable. Vérifiez le code."}
        if action == "join":
            i = g.find(body.get("token"))
            if i is not None:   # reconnexion
                g.touch(i)
                return 200, {"code": code, "token": body.get("token"), "seat": i}
            seat, token = g.sit(body.get("name"))
            if seat is None:
                return 409, {"error": "La table est complète : 4 joueurs sont déjà assis."}
            return 200, {"code": code, "token": token, "seat": seat}
        i = g.find(body.get("token"))
        if i is None:
            return 403, {"error": "Vous n'êtes plus à cette table."}
        g.touch(i)
        try:
            if action == "seat":
                g.move(i, body.get("seat"))
            elif action == "start":
                if i != g.creator:
                    raise ValueError("Seul le créateur de la table peut lancer la partie.")
                g.start()
            elif action == "bid":
                s = body.get("s")
                g.bid(i, s if s in (0, 1, 2, 3) else -1)
            elif action == "play":
                cid = body.get("id")
                if not isinstance(cid, int):
                    raise ValueError("Carte inconnue.")
                g.play(i, cid)
            elif action == "next":
                g.next_deal()
            elif action == "leave":
                g.leave(i)
                if not g.humans():
                    del rooms[code]
                return 200, {"ok": True}
            else:
                return 404, {"error": "Introuvable."}
        except ValueError as e:
            return 400, {"error": str(e)}
        i = g.find(body.get("token"))
        g.tick()
        return 200, g.view(i)
