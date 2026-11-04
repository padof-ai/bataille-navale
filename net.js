/* Salle de jeux : tables en ligne communes à tous les jeux.
   Chaque navigateur rejoue le même journal de coups avec le même hasard ;
   l'hôte joue pour l'ordinateur (places vides ou joueurs absents). */
(function(){
  "use strict";
  const API = "/api/net/";
  const css = `
.sdj-back{display:flex!important;flex-wrap:wrap;gap:6px;align-items:center}
.net-pill{display:inline-flex;align-items:center;gap:6px;font:700 13px/1 system-ui,-apple-system,"Segoe UI",sans-serif;color:#fff;background:rgba(0,0,0,.4);border:1px solid rgba(255,255,255,.2);padding:7px 12px;border-radius:999px;cursor:pointer}
.net-pill:hover{background:rgba(0,0,0,.6)}.net-pill:focus-visible{outline:3px solid #f2c14e;outline-offset:2px}
.net-pill.on{background:#f2c14e;color:#24180a;border-color:#f2c14e}
.net-who{display:flex;flex-wrap:wrap;gap:6px;flex-basis:100%}
.net-who span{font:700 12px/1 system-ui,sans-serif;color:#fff;background:rgba(0,0,0,.45);border:1px solid rgba(255,255,255,.18);border-radius:999px;padding:5px 9px;display:inline-flex;align-items:center;gap:5px}
.net-who span i{width:8px;height:8px;border-radius:50%;background:#7a7a7a;display:inline-block}
.net-who span.ok i{background:#4ade80}.net-who span.bot i{background:#60a5fa}.net-who span.cur{background:#f2c14e;color:#24180a}
.net-who span.me{border-color:#f2c14e}
.net-modal{position:fixed;inset:0;z-index:1000;display:flex;align-items:center;justify-content:center;background:rgba(5,5,10,.7);padding:16px max(16px,env(safe-area-inset-right,0px)) 16px max(16px,env(safe-area-inset-left,0px));font:15px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif}
.net-modal[hidden]{display:none!important}
.net-box{background:#1d1b24;color:#f4f1ea;border:1px solid rgba(255,255,255,.16);border-radius:16px;padding:20px;width:100%;max-width:440px;max-height:100%;overflow:auto;box-shadow:0 20px 40px rgba(0,0,0,.5)}
.net-box h2{margin:0 0 10px;font-size:1.35rem;color:#f2c14e}
.net-box p{margin:0 0 12px;color:#c9c3d4;font-size:.92rem}
.net-box label{display:block;font-weight:700;font-size:.88rem;margin:0 0 6px}
.net-box input{font:inherit;font-weight:700;color:#f4f1ea;background:rgba(0,0,0,.35);border:1px solid rgba(255,255,255,.2);border-radius:10px;padding:10px 12px;width:100%;box-sizing:border-box;margin-bottom:12px}
.net-box input:focus{outline:3px solid #f2c14e;outline-offset:1px}
.net-box input.cd{text-transform:uppercase;letter-spacing:.3em;text-align:center;font-size:1.2rem}
.net-box button{font:inherit;font-weight:700;color:#f4f1ea;background:rgba(255,255,255,.08);border:1px solid rgba(255,255,255,.2);border-radius:10px;padding:10px 12px;cursor:pointer}
.net-box button:disabled{opacity:.45;cursor:default}
.net-box button.pri{background:#f2c14e;color:#24180a;border-color:#f2c14e}
.net-box .row{display:flex;gap:8px;flex-wrap:wrap;margin-top:8px}.net-box .row button{flex:1}
.net-box .wide{width:100%}
.net-box .or{display:flex;align-items:center;gap:10px;color:#a9a3b4;font-size:.8rem;font-weight:700;margin:14px 0 10px}
.net-box .or::before,.net-box .or::after{content:"";flex:1;height:1px;background:rgba(255,255,255,.16)}
.net-box .err{color:#ff9b9b;font-weight:700}
.net-box .code{font-size:2.4rem;letter-spacing:.2em;text-align:center;color:#f2c14e;font-weight:800;margin:0 0 8px}
.net-seats{display:grid;gap:6px;margin-bottom:10px}
.net-seats button{text-align:left;display:flex;justify-content:space-between;align-items:center;gap:8px}
.net-seats button small{color:#a9a3b4;font-weight:600;font-size:.78rem}
.net-seats button.me{border-color:#f2c14e;box-shadow:inset 0 0 0 1px #f2c14e}
.net-seats button.free{border-style:dashed;color:#c9c3d4}
.net-seats button:disabled{opacity:1;cursor:default}
.net-cnt{display:flex;gap:6px;margin-bottom:12px}.net-cnt button{flex:1}.net-cnt button[aria-pressed="true"]{background:#f2c14e;color:#24180a}
.net-toast{position:fixed;left:50%;bottom:max(16px,env(safe-area-inset-bottom,0px));transform:translateX(-50%);z-index:1001;background:#1d1b24;color:#f4f1ea;border:1px solid rgba(255,255,255,.2);border-radius:12px;padding:9px 14px;font:700 14px/1.35 system-ui,sans-serif;box-shadow:0 8px 20px rgba(0,0,0,.5);max-width:calc(100vw - 32px);text-align:center}
.net-toast[hidden]{display:none!important}`;
  const load = k => { try{ return localStorage.getItem(k); }catch(e){ return null; } };
  const save = (k, v) => { try{ localStorage.setItem(k, v); }catch(e){} };
  const esc = t => String(t).replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"})[c]);

  async function api(path, body){
    const r = await fetch(API + path, body ? {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body)} : {cache: "no-store"});
    let j = null; try{ j = await r.json(); }catch(e){}
    if(!r.ok || !j) throw Object.assign(new Error((j && j.error) || "Serveur injoignable."), {status: r.status, data: j});
    return j;
  }

  // hasard partagé (mulberry32)
  function mulberry(a){ return function(){ a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }

  const Net = {
    available: false, on: false, me: -1, n: 0, names: [], bots: [], cfg: null, host: 0,
    _g: null, _room: null, _log: [], _pos: 0, _fetched: 0, _poll: 0, _rand: Math.random, _waits: [], _mine: null, _v: null, _lastEv: 0, _desync: false,
    rand(){ return Net._rand(); },
    isHost(){ return Net.on && Net.host === Net.me; },
    kind(seat){ if(!Net.on) return "local"; if(seat === Net.me) return "me"; return Net.bots[seat] ? "bot" : "human"; },
    setup(g){
      Net._g = Object.assign({min: 2, max: 2, bots: true, seatName: (k, n) => "Place " + (k + 1)}, g);
      if(Net.available) Net._ui(); else Net._probe();
    },
    /* coup joué par ce navigateur (pour sa place, ou pour l'ordinateur s'il est l'hôte) */
    send(a, seat, soft){
      if(!Net.on) return;
      seat = seat === undefined || seat === null ? Net.me : seat;
      if(soft === "free"){
        // coup simultané (ex. cartes retournées par tous au début) : il est ajouté au journal
        // sans place réservée, et chacun (y compris l'auteur) le recevra dans l'ordre du journal
        const room = Net._room;
        api("act", {code: room.code, token: room.token, seat, a, c: "f", since: Net._fetched}).then(v => { if(Net._room === room) Net._apply(v); }).catch(() => {});
        return;
      }
      if(seat === Net.me) Net._mine = null;
      // un « coup libre » (nouvelle partie, donne suivante…) répond à l'attente générale
      if(Net._waits.length && Net._waits[0].seat === -1){ const w = Net._waits.shift(); setTimeout(() => w.res(a)); }
      const at = Net._pos; Net._pos++;
      const c = Math.random().toString(36).slice(2, 10);
      Net._log[at] = {s: seat, a, c, local: true, soft: !!soft};
      const room = Net._room;
      api("act", {code: room.code, token: room.token, seat, a, at, c, since: Net._fetched}).then(v => { if(Net._room === room) Net._apply(v); }).catch(e => {
        if(Net._room !== room) return;
        if(e.status === 409 && soft){ delete Net._log[at]; }
        else if(e.status === 409){ Net._desyncMsg(); }
        else if(e.status === 404 || e.status === 403) Net._quit(e.message);
      });
    },
    /* attend le coup d'une autre place ; l'hôte calcule celui de l'ordinateur */
    turn(seat, ai){
      return new Promise(res => { Net._waits.push({seat, ai, res, busy: false}); Net._pump(); });
    },
    /* c'est à moi : si l'ordinateur a dû jouer pour moi pendant une absence, on applique son coup */
    mine(cb){ Net._mine = cb; Net._pump(); },
    /* attend le prochain coup, de n'importe qui (ex. « nouvelle partie ») */
    next(){ return Net.turn(-1, null); },
    leave(){ Net._quit(); },
    /* oublie les attentes en cours (nouvelle partie) */
    cancel(){ Net._waits = []; Net._mine = null; },

    _pump(){
      if(!Net.on) return;
      // coups reçus, dans l'ordre du journal
      while(Net._waits.length || Net._mine){
        const e = Net._log[Net._pos];
        if(e && !e.local){
          if(Net._waits.length){
            const w = Net._waits.shift(); Net._pos++;
            if(e.s !== w.seat) console.warn("Net : coup inattendu", e, w.seat);
            w.res(e.a); continue;
          }
          if(Net._mine && e.s === Net.me){ const cb = Net._mine; Net._mine = null; Net._pos++; cb(e.a); continue; }
          break;
        }
        // rien de reçu : l'hôte joue pour l'ordinateur
        const w = Net._waits[0];
        if(w && !w.busy && Net.isHost() && (Net.bots[w.seat] || (Net._v && Net._v.away[w.seat]))){
          w.busy = true;
          Promise.resolve().then(() => w.ai()).then(a => {
            if(!Net.on || Net._waits[0] !== w) return;
            if(Net._log[Net._pos] && !Net._log[Net._pos].local){ w.busy = false; return Net._pump(); }
            Net._waits.shift(); Net.send(a, w.seat); w.res(a);
            Net._pump();
          });
        }
        break;
      }
    },
    _apply(v){
      Net._v = v; Net.host = v.host;
      if(v.from === Net._fetched){
        v.log.forEach((e, k) => {
          const at = v.from + k, mine = Net._log[at];
          if(mine && mine.local){ if(mine.c !== e.c && !mine.soft) Net._desyncMsg(); }
          else Net._log[at] = e;
        });
        Net._fetched = v.from + v.log.length;
      }
      // arrivées et départs
      (v.events || []).forEach(e => { if(e.n > Net._lastEv){ Net._lastEv = e.n; if(Net._ready && e.p !== Net.me) Net._toast(e.k === "join" ? `${e.t} rejoint la table` : `${e.t} a quitté la table${v.phase === "play" ? " : l'ordinateur prend sa place" : ""}`); } });
      Net._ready = true;
      if(v.phase === "play" && v.bots) v.bots.forEach((b, k) => { if(b) Net.bots[k] = true; });
      if(v.phase === "lobby") Net._lobby(v);
      else if(!Net.on){ Net._startGame(v); }
      Net._bar();
      Net._pump();
    },
    async _tick(){
      clearTimeout(Net._poll);
      const room = Net._room; if(!room) return;
      try{
        const v = await api(`state?code=${room.code}&token=${encodeURIComponent(room.token)}&since=${Net._fetched}`);
        if(Net._room === room) Net._apply(v);
      }catch(e){ if(Net._room === room && (e.status === 404 || e.status === 403)) return Net._quit(e.message); }
      if(Net._room === room) Net._poll = setTimeout(Net._tick, document.hidden ? 3000 : 650);
    },
    _startGame(v){
      Net.on = true; Net.me = v.me; Net.n = v.n; Net.bots = v.bots.slice(); Net.cfg = v.cfg; Net.host = v.host;
      Net.names = v.names.map((x, k) => v.bots[k] ? "Ordi " + (k + 1) : x);
      Net._rand = mulberry(v.seed);
      $("net-lobby").hidden = true; $("net-join").hidden = true;
      try{ Net._g.start({n: v.n, me: v.me, names: Net.names.slice(), bots: Net.bots.slice(), cfg: v.cfg, host: v.host}); }
      catch(e){ console.error(e); }
    },
    _quit(msg){
      const room = Net._room, wasOn = Net.on;
      clearTimeout(Net._poll);
      if(room && msg === undefined) api("leave", {code: room.code, token: room.token}).catch(() => {});
      Net._room = null; Net.on = false; Net._waits = []; Net._mine = null; Net._log = []; Net._pos = 0; Net._fetched = 0; Net._v = null; Net._rand = Math.random; Net._ready = false;
      save("net_room_" + Net._g.game, "");
      $("net-lobby").hidden = true;
      Net._bar();
      if(wasOn && Net._g.leave) Net._g.leave();
      if(msg) Net._toast(msg, 4000);
    },
    _desyncMsg(){
      if(Net._desync) return; Net._desync = true;
      Net._toast("Votre coup est arrivé trop tard (l'ordinateur avait joué pour vous). Quittez puis recréez une table.", 6000);
    },
    _toast(t, ms = 2600){ const el = $("net-toast"); el.textContent = t; el.hidden = false; clearTimeout(Net._tt); Net._tt = setTimeout(() => el.hidden = true, ms); },

    // ---------- interface ----------
    async _probe(){
      if(!/^https?:$/.test(location.protocol)) return;
      try{ await api("state?code=ZZZZ&token=x"); }catch(e){ if(!(e.status === 404 && e.data && /Table/.test(e.data.error || ""))) return; }
      Net.available = true;
      if(Net._g) Net._ui();
    },
    _ui(){
      if($("net-btn")) return;
      const st = document.createElement("style"); st.textContent = css; document.head.appendChild(st);
      let nav = document.querySelector(".sdj-back");
      if(!nav){ nav = document.createElement("nav"); nav.className = "sdj-back"; document.body.prepend(nav); }
      nav.insertAdjacentHTML("beforeend", `<button class="net-pill" id="net-btn">🌐 Jouer en ligne</button><span id="net-tbl" hidden></span><div class="net-who" id="net-who" hidden></div>`);
      document.body.insertAdjacentHTML("beforeend", `
<div class="net-modal" id="net-join" hidden><div class="net-box" role="dialog" aria-labelledby="net-jt">
  <h2 id="net-jt">${esc(Net._g.name)} en ligne</h2>
  <p>Chacun joue sur son téléphone ou son ordinateur.${Net._g.bots ? " Les places restées libres sont jouées par l'ordinateur." : ""}</p>
  <label for="net-name">Votre prénom</label><input id="net-name" maxlength="14" autocomplete="nickname" placeholder="Ex. Pierre">
  <button class="pri wide" id="net-create">Créer une table</button>
  <p style="margin:6px 0 0;font-size:.8rem">Vos réglages actuels s'appliquent à la table.</p>
  <div class="or">ou rejoindre une table</div>
  <label for="net-code">Code de la table</label><input id="net-code" class="cd" maxlength="4" autocomplete="off" placeholder="ABCD">
  <button class="wide" id="net-joinb">Rejoindre</button>
  <p class="err" id="net-err" hidden></p>
  <div class="row"><button id="net-close">Fermer</button></div>
</div></div>
<div class="net-modal" id="net-lobby" hidden><div class="net-box" role="dialog" aria-labelledby="net-lt">
  <h2 id="net-lt">Table en ligne</h2>
  <div class="code" id="net-lcode"></div>
  <p id="net-ltxt"></p>
  <div class="net-cnt" id="net-cnt"></div>
  <div class="net-seats" id="net-seats"></div>
  <p class="err" id="net-lerr" hidden></p>
  <div class="row"><button id="net-inv">Copier le lien d'invitation</button></div>
  <div class="row"><button id="net-lquit">Quitter</button><button class="pri" id="net-start">Lancer la partie</button></div>
</div></div>
<div class="net-toast" id="net-toast" hidden></div>`);
      $("net-btn").onclick = () => Net.on || Net._room ? Net._invite($("net-btn")) : Net._openJoin("");
      $("net-close").onclick = () => $("net-join").hidden = true;
      $("net-create").onclick = Net._create;
      $("net-joinb").onclick = () => Net._join();
      $("net-code").onkeydown = e => { if(e.key === "Enter") Net._join(); };
      $("net-inv").onclick = () => Net._invite($("net-inv"));
      $("net-lquit").onclick = () => Net._quit();
      $("net-start").onclick = Net._start;
      document.addEventListener("visibilitychange", () => { if(Net._room && !document.hidden) Net._tick(); });
      Net._bar();
      const q = new URLSearchParams(location.search).get("t");
      let saved = null; try{ saved = JSON.parse(load("net_room_" + Net._g.game) || "null"); }catch(e){}
      if(saved && saved.code && (!q || q.toUpperCase() === saved.code)){
        api("join", {code: saved.code, token: saved.token, game: Net._g.game}).then(r => {
          // on ne peut revenir que dans un salon pas encore lancé
          Net._enter(r); }).catch(() => { save("net_room_" + Net._g.game, ""); if(q) Net._openJoin(q); });
      } else if(q) Net._openJoin(q.toUpperCase());
    },
    _bar(){
      const b = $("net-btn"); if(!b) return;
      const who = $("net-who"), tbl = $("net-tbl");
      if(Net._room){
        b.classList.add("on"); b.textContent = `🌐 Table ${Net._room.code} · Inviter`;
        tbl.hidden = false; tbl.innerHTML = `<button class="net-pill" id="net-q">Quitter</button>`; $("net-q").onclick = () => Net._quit();
      } else { b.classList.remove("on"); b.textContent = "🌐 Jouer en ligne"; tbl.hidden = true; }
      if(Net.on && Net._v){
        who.hidden = false;
        const cur = Net._g.current ? Net._g.current() : -1;
        who.innerHTML = Net.names.map((nm, k) => {
          const bot = Net.bots[k], ok = !bot && Net._v.online[k];
          return `<span class="${bot ? "bot" : ok ? "ok" : ""}${k === Net.me ? " me" : ""}${k === cur ? " cur" : ""}"><i></i>${esc(k === Net.me ? nm + " (vous)" : nm)}${!bot && !ok ? " · absent" : ""}</span>`;
        }).join("");
      } else who.hidden = true;
    },
    refresh(){ Net._bar(); },
    _openJoin(code){
      $("net-name").value = load("net_name") || ""; $("net-code").value = code || ""; Net._err("net-err", "");
      $("net-join").hidden = false;
      setTimeout(() => (code && $("net-name").value ? $("net-joinb") : $("net-name")).focus(), 50);
    },
    _err(id, m){ const e = $(id); e.textContent = m || ""; e.hidden = !m; },
    _askName(){
      const n = $("net-name").value.trim();
      if(!n){ Net._err("net-err", "Indiquez votre prénom : il s'affichera à la table."); $("net-name").focus(); return null; }
      save("net_name", n); return n;
    },
    async _create(){
      const name = Net._askName(); if(!name) return;
      const g = Net._g;
      try{
        const cfg = g.cfg ? g.cfg() : {};
        const n = g.seats ? g.seats() : g.max;
        const r = await api("create", {game: g.game, name, cfg, n, min: g.min, max: g.max, bots: g.bots});
        Net._enter(r);
      }catch(e){ Net._err("net-err", e.message); }
    },
    async _join(){
      const name = Net._askName(); if(!name) return;
      const code = $("net-code").value.toUpperCase().replace(/[^A-Z0-9]/g, "");
      if(code.length !== 4) return Net._err("net-err", "Le code de la table a 4 caractères.");
      try{ Net._enter(await api("join", {code, name, game: Net._g.game})); }catch(e){ Net._err("net-err", e.message); }
    },
    _enter(r){
      Net._room = {code: r.code, token: r.token}; Net.me = r.seat; Net._lastEv = 0; Net._ready = false; Net._desync = false;
      save("net_room_" + Net._g.game, JSON.stringify({code: r.code, token: r.token}));
      $("net-join").hidden = true;
      Net._bar(); Net._tick();
    },
    _lobby(v){
      Net.me = v.me;
      $("net-lobby").hidden = false; $("net-lcode").textContent = v.code;
      const host = v.host === v.me, g = Net._g;
      $("net-ltxt").textContent = host
        ? `Donnez ce code à vos amis ou envoyez-leur le lien, puis lancez la partie.${g.bots ? " Les places libres seront jouées par l'ordinateur." : ""} Touchez une place libre pour changer de place.`
        : `En attente : ${v.names[v.host]} lancera la partie. Touchez une place libre pour changer de place.`;
      const cnt = $("net-cnt");
      if(host && v.nmax > v.nmin){
        let h = ""; for(let k = v.nmin; k <= v.nmax; k++) h += `<button data-n="${k}" aria-pressed="${k === v.n}">${k} joueurs</button>`;
        cnt.innerHTML = h; cnt.hidden = false;
        cnt.querySelectorAll("button").forEach(b => b.onclick = () => Net._post("setn", {n: +b.dataset.n}));
      } else cnt.hidden = true;
      $("net-seats").innerHTML = v.names.map((nm, k) => {
        const me = k === v.me, free = !v.humans[k];
        const label = free ? `Place libre <small>${g.bots ? "ordinateur si personne" : "en attente d'un joueur"}</small>` : `${esc(nm)}${me ? " (vous)" : ""} <small>${k === v.host ? "hôte" : v.online[k] ? "connecté" : "hors ligne"}</small>`;
        return `<button class="${me ? "me" : ""}${free ? " free" : ""}" data-s="${k}"${free ? "" : " disabled"}><span>${esc(g.seatName(k, v.n))}</span><span>${label}</span></button>`;
      }).join("");
      $("net-seats").querySelectorAll("button.free").forEach(b => b.onclick = () => Net._post("seat", {seat: +b.dataset.s}));
      const humans = v.humans.filter(Boolean).length;
      const st = $("net-start"); st.hidden = !host;
      st.disabled = humans < 2 || (!g.bots && humans < v.n);
      st.textContent = humans < 2 ? "En attente d'un 2e joueur…" : (!g.bots && humans < v.n) ? "En attente des joueurs…" : humans < v.n ? `Lancer (${v.n - humans} ordi${v.n - humans > 1 ? "s" : ""})` : "Lancer la partie";
    },
    async _post(path, extra){
      const room = Net._room; if(!room) return;
      try{ const v = await api(path, Object.assign({code: room.code, token: room.token, since: Net._fetched}, extra || {})); if(Net._room === room) Net._apply(v); }
      catch(e){ Net._err("net-lerr", e.message); setTimeout(() => Net._err("net-lerr", ""), 3000); }
    },
    _start(){ Net._post("start"); },
    async _invite(btn){
      if(!Net._room) return;
      const link = `${location.origin}${location.pathname}?t=${Net._room.code}`;
      try{ if(navigator.share && /Android|iPhone|iPad/i.test(navigator.userAgent)){ await navigator.share({title: Net._g.name, text: `Rejoins ma table (code ${Net._room.code})`, url: link}); return; } }catch(e){ return; }
      const old = btn.textContent;
      try{ await navigator.clipboard.writeText(link); btn.textContent = "Lien copié !"; }catch(e){ btn.textContent = link; }
      setTimeout(() => { Net._bar(); if(btn.id === "net-inv") btn.textContent = "Copier le lien d'invitation"; }, 2500);
    }
  };
  function $(id){ return document.getElementById(id); }
  window.Net = Net;
  if(document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => Net._probe()); else Net._probe();
})();
