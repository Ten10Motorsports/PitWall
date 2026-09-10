/*
 * PitWall - shared browser client.
 *
 * Every overlay, the timing page and the control panel all talk to the server
 * through this. It handles connection, reconnection, the meta/tick split, and
 * the formatting that timing screens get wrong.
 *
 * Usage:
 *
 *   PW.start({ sub: ['meta','tick'], rate: 10 });
 *   PW.on('tick', s => render(s));
 *
 * Every overlay reads its options from the URL query string, so you configure
 * a graphic by editing the browser source URL in OBS rather than by digging
 * through a settings app:
 *
 *   tower.html?rows=16&scale=1.1&accent=%23ff8800&cols=pos,num,name,gap,last
 */
(function (global) {
  'use strict';

  var PW = {
    meta: null,
    tick: null,
    hello: null,
    connected: false,
    demo: false,
    _handlers: {},
    _ws: null,
    _retry: 0,
    _opts: null
  };

  /* ---------------------------------------------------------------- options */

  function params() {
    if (PW._opts) return PW._opts;
    var q = {};
    var s = global.location.search.replace(/^\?/, '');
    s.split('&').forEach(function (pair) {
      if (!pair) return;
      var i = pair.indexOf('=');
      var k = i < 0 ? pair : pair.slice(0, i);
      var v = i < 0 ? '1' : decodeURIComponent(pair.slice(i + 1).replace(/\+/g, ' '));
      q[k.toLowerCase()] = v;
    });
    PW._opts = q;
    return q;
  }

  PW.opt = function (name, dflt) {
    var v = params()[String(name).toLowerCase()];
    return v === undefined ? dflt : v;
  };
  PW.optNum = function (name, dflt) {
    var v = parseFloat(PW.opt(name, NaN));
    return isNaN(v) ? dflt : v;
  };
  PW.optBool = function (name, dflt) {
    var v = PW.opt(name, null);
    if (v === null) return dflt;
    return v !== '0' && v !== 'false' && v !== 'no';
  };
  PW.optList = function (name, dflt) {
    var v = PW.opt(name, null);
    if (v === null) return dflt;
    return v.split(',').map(function (s) { return s.trim(); }).filter(Boolean);
  };

  /* ------------------------------------------------------------- connection */

  PW.serverBase = function () {
    // Overlays can be hosted anywhere (GitHub Pages, a file:// scene
    // collection) and pointed back at the PitWall machine with ?server=
    var s = PW.opt('server', null);
    if (s) return s.replace(/\/$/, '');
    if (global.location.protocol === 'file:') return 'http://127.0.0.1:8099';
    return global.location.origin;
  };

  PW.start = function (options) {
    options = options || {};
    PW._sub = options.sub || ['meta', 'tick'];
    PW._rate = options.rate || PW.optNum('rate', 15);
    PW._name = options.name || document.title || 'overlay';
    connect();
    return PW;
  };

  function wsUrl() {
    var base = PW.serverBase();
    return base.replace(/^http/, 'ws') + '/ws';
  }

  function connect() {
    var url;
    try { url = wsUrl(); } catch (e) { return; }
    var ws;
    try { ws = new WebSocket(url); } catch (e) { scheduleRetry(); return; }
    PW._ws = ws;

    ws.onopen = function () {
      PW._retry = 0;
      PW.connected = true;
      ws.send(JSON.stringify({ sub: PW._sub, rate: PW._rate, name: PW._name }));
      emit('open', null);
      setStatus(true);
    };
    ws.onclose = function () {
      PW.connected = false;
      emit('close', null);
      setStatus(false);
      scheduleRetry();
    };
    ws.onerror = function () { try { ws.close(); } catch (e) {} };
    ws.onmessage = function (ev) {
      var msg;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      route(msg);
    };
  }

  function scheduleRetry() {
    PW._retry = Math.min(PW._retry + 1, 10);
    var wait = Math.min(500 * PW._retry, 4000);
    setTimeout(connect, wait);
  }

  function route(msg) {
    switch (msg.type) {
      case 'hello':
        PW.hello = msg;
        PW.demo = !!msg.demo;
        if (msg.league) applyLeague(msg.league);
        emit('hello', msg);
        break;
      case 'meta':
        PW.meta = msg;
        PW._driverCache = null;
        emit('meta', msg);
        break;
      case 'tick':
        PW.tick = msg;
        emit('tick', msg);
        break;
      case 'inputs':
        emit('inputs', msg);
        break;
      case 'events':
        (msg.e || []).forEach(function (e) { emit('event:' + e.kind, e); emit('event', e); });
        break;
      case 'waiting':
        emit('waiting', msg);
        break;
      default:
        emit(msg.type, msg);
    }
  }

  PW.on = function (name, fn) {
    (PW._handlers[name] = PW._handlers[name] || []).push(fn);
    return PW;
  };
  function emit(name, data) {
    (PW._handlers[name] || []).forEach(function (fn) {
      try { fn(data); } catch (e) { console.error('[PitWall]', name, e); }
    });
  }

  PW.send = function (obj) {
    if (PW._ws && PW._ws.readyState === 1) PW._ws.send(JSON.stringify(obj));
  };

  PW.control = function (action, extra) {
    var body = Object.assign({ action: action }, extra || {});
    return fetch(PW.serverBase() + '/api/control', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json(); });
  };

  PW.api = function (path, opts) {
    return fetch(PW.serverBase() + path, opts).then(function (r) {
      return r.json().then(function (j) {
        if (!r.ok) throw new Error(j.error || r.statusText);
        return j;
      });
    });
  };

  /* ----------------------------------------------------------- status pill */

  function setStatus(up) {
    var el = document.getElementById('pw-status');
    if (!el) return;
    el.classList.toggle('up', up);
    el.classList.toggle('down', !up);
  }

  /* ------------------------------------------------------------ league skin */

  function applyLeague(league) {
    var root = document.documentElement;
    var accent = PW.opt('accent', league.accent);
    var second = PW.opt('secondary', league.secondary);
    if (accent) root.style.setProperty('--accent', accent);
    if (second) root.style.setProperty('--secondary', second);
    PW.league = league;
    emit('league', league);
  }

  /* ---------------------------------------------------------------- lookups */

  PW.driver = function (idx) {
    if (!PW.meta || !PW.meta.drivers) return null;
    return PW.meta.drivers[String(idx)] || null;
  };

  PW.profile = function (idx) {
    var d = PW.driver(idx);
    return (d && d.profile) || null;
  };

  PW.classOf = function (idx) {
    var d = PW.driver(idx);
    if (!d || !PW.meta) return null;
    var id = d.classId;
    return (PW.meta.classes || []).filter(function (c) { return c.id === id; })[0] || null;
  };

  PW.multiclass = function () {
    return PW.meta && PW.meta.classes && PW.meta.classes.length > 1;
  };

  PW.cars = function () {
    return (PW.tick && PW.tick.cars) || [];
  };

  PW.carByIdx = function (idx) {
    var cs = PW.cars();
    for (var i = 0; i < cs.length; i++) if (cs[i].i === idx) return cs[i];
    return null;
  };

  /** The car the broadcast camera is on, else the player's car. */
  PW.focusIdx = function () {
    var t = PW.tick;
    if (!t) return null;
    var forced = PW.optNum('car', NaN);
    if (!isNaN(forced)) return forced;
    if (t.cam && typeof t.cam.idx === 'number' && t.cam.idx >= 0) return t.cam.idx;
    if (t.player && typeof t.player.idx === 'number') return t.player.idx;
    return null;
  };

  /** Cars around a reference car, for a relative display. */
  PW.relative = function (idx, ahead, behind) {
    var cars = PW.cars().slice().sort(function (a, b) { return a.lp - b.lp; });
    var live = cars.filter(function (c) { return c.lp > 0; });
    var me = -1;
    for (var i = 0; i < live.length; i++) if (live[i].i === idx) { me = i; break; }
    if (me < 0) return [];
    var out = [];
    for (var k = me - ahead; k <= me + behind; k++) {
      if (k < 0 || k >= live.length) continue;
      out.push(live[k]);
    }
    return out;
  };

  /* -------------------------------------------------------------- formatting */

  /** 98.432 -> "1:38.432". The classic rookie bug is rendering -1.000. */
  PW.fmtLap = function (s, places) {
    if (s === null || s === undefined || s <= 0) return '';
    places = places === undefined ? 3 : places;
    var m = Math.floor(s / 60);
    var r = s - m * 60;
    var rs = r.toFixed(places);
    if (r < 10) rs = '0' + rs;
    return m > 0 ? m + ':' + rs : rs;
  };

  /** Gaps: "+1.234", "+2 L", "" for the leader. */
  PW.fmtGap = function (s, lapsDown, places) {
    if (lapsDown && lapsDown > 0) return '+' + lapsDown + 'L';
    if (s === null || s === undefined) return '';
    if (s <= 0.0005) return '';
    places = places === undefined ? (s >= 100 ? 1 : 3) : places;
    return '+' + s.toFixed(places);
  };

  PW.fmtDelta = function (s, places) {
    if (s === null || s === undefined) return '';
    places = places === undefined ? 2 : places;
    return (s >= 0 ? '+' : '-') + Math.abs(s).toFixed(places);
  };

  /** Seconds -> "1:23:45" or "23:45". */
  PW.fmtClock = function (s) {
    if (s === null || s === undefined) return '';
    s = Math.max(0, Math.floor(s));
    var h = Math.floor(s / 3600);
    var m = Math.floor((s % 3600) / 60);
    var ss = s % 60;
    var p = function (n) { return n < 10 ? '0' + n : '' + n; };
    return h > 0 ? h + ':' + p(m) + ':' + p(ss) : m + ':' + p(ss);
  };

  PW.fmtTod = function (s) {
    if (s === null || s === undefined) return '';
    var t = Math.floor(s) % 86400;
    var h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60);
    var p = function (n) { return n < 10 ? '0' + n : '' + n; };
    return p(h) + ':' + p(m);
  };

  /** "Alex Fenwick" -> "A. Fenwick". Broadcast towers are narrow. */
  PW.shortName = function (name) {
    if (!name) return '';
    var parts = name.trim().split(/\s+/);
    if (parts.length === 1) return parts[0];
    return parts[0].charAt(0) + '. ' + parts.slice(1).join(' ');
  };

  PW.surname = function (name) {
    if (!name) return '';
    var parts = name.trim().split(/\s+/);
    return parts[parts.length - 1];
  };

  PW.displayName = function (idx, style) {
    var d = PW.driver(idx);
    if (!d) return '';
    var p = d.profile || {};
    var name = p.displayName || d.name || '';
    switch (style || PW.opt('names', 'short')) {
      case 'full': return name;
      case 'surname': return PW.surname(name).toUpperCase();
      case 'abbrev': return d.abbrev || PW.shortName(name);
      default: return PW.shortName(name);
    }
  };

  /** Country code -> flag emoji. Falls back to the raw code. */
  PW.flag = function (code) {
    if (!code || code.length !== 2) return '';
    var cc = code.toUpperCase();
    if (!/^[A-Z]{2}$/.test(cc)) return '';
    return String.fromCodePoint(
      0x1f1e6 + cc.charCodeAt(0) - 65,
      0x1f1e6 + cc.charCodeAt(1) - 65
    );
  };

  PW.ordinal = function (n) {
    if (!n) return '';
    var s = ['th', 'st', 'nd', 'rd'], v = n % 100;
    return n + (s[(v - 20) % 10] || s[v] || s[0]);
  };

  /* --------------------------------------------------------------- utilities */

  PW.el = function (tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  };

  /** Keyed list reconciliation: no full re-render, so rows do not flicker
   *  and CSS transitions on position changes actually work. */
  PW.reconcile = function (container, items, keyFn, createFn, updateFn) {
    var existing = {};
    Array.prototype.forEach.call(container.children, function (c) {
      if (c.dataset && c.dataset.key) existing[c.dataset.key] = c;
    });
    var used = {};
    items.forEach(function (item, i) {
      var key = String(keyFn(item));
      used[key] = true;
      var node = existing[key];
      if (!node) {
        node = createFn(item);
        node.dataset.key = key;
      }
      updateFn(node, item, i);
      if (container.children[i] !== node) {
        container.insertBefore(node, container.children[i] || null);
      }
    });
    Object.keys(existing).forEach(function (k) {
      if (!used[k]) existing[k].remove();
    });
  };

  /** Apply generic look-and-feel query params to any overlay. */
  PW.applyChrome = function () {
    var root = document.documentElement;
    var scale = PW.optNum('scale', 1);
    if (scale !== 1) root.style.setProperty('--scale', scale);
    var font = PW.opt('font', null);
    if (font) root.style.setProperty('--font', font);
    var accent = PW.opt('accent', null);
    if (accent) root.style.setProperty('--accent', accent);
    var bg = PW.opt('bg', null);
    if (bg) document.body.style.background = bg === '1' ? '#0b0d10' : bg;
    if (PW.optBool('debug', false)) root.classList.add('pw-debug');
    var op = PW.optNum('opacity', NaN);
    if (!isNaN(op)) root.style.setProperty('--panel-opacity', op);
  };

  global.PW = PW;
})(window);
