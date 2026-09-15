/*
 * PitWall - the shared column engine.
 * ---------------------------------------------------------------------------
 * One definition of every column, used by the broadcast tower, the in-car
 * leaderboard, the relative and the ticker. Before this file existed the tower
 * and the relative each had their own idea of what "GAP" meant and they drifted
 * apart; now a column is defined once and the four lists differ only in which
 * rows they choose and how they lay them out.
 *
 * A column definition is deliberately small:
 *
 *   key      the name used in a URL, e.g. "tage"
 *   label    the header text
 *   src      sim | roster | derived | estimate   (shown in the settings editor)
 *   w        default width in pixels
 *   align    l | r | c
 *   race     false if the column is meaningless in a race
 *   timed    false if the column is meaningless in practice or qualifying
 *   get(ctx) returns a string, or an object {text, cls, title}
 *
 * ctx carries everything a column could want without any of them having to go
 * looking: { c, d, prof, me, meta, tick, opt, timed, multiclass }
 *   c     the car's entry in the tick
 *   d     the driver record
 *   prof  the league roster profile, if the driver has one
 *   me    the reference car's entry, for anything relative to you
 *
 * Nothing in here touches the DOM. A column produces text and a class name;
 * the widget decides what that looks like.
 */
(function (PW) {
  'use strict';

  var DASH = '–';

  function pad(n) { return n < 10 ? '0' + n : String(n); }

  /* Tyre compound labels. iRacing publishes an integer with no names attached
     and the meaning is per car, so this is a default that the settings editor
     can override per profile. Cars with one compound report the same value for
     everyone, which is why the column shows a dash rather than "S" for all. */
  var COMPOUND = ['S', 'M', 'H', 'W', 'X', 'Y'];

  function compoundLabel(n, map) {
    if (n === null || n === undefined || n < 0) return DASH;
    var list = map && map.length ? map : COMPOUND;
    return list[n] !== undefined ? list[n] : String(n);
  }

  /* Signed number with an explicit plus, for anything that can go either way. */
  function signed(n, places) {
    if (n === null || n === undefined) return DASH;
    var v = Number(n);
    if (!isFinite(v)) return DASH;
    var s = places ? Math.abs(v).toFixed(places) : String(Math.abs(Math.round(v)));
    if (v > 0) return '+' + s;
    if (v < 0) return '−' + s;
    return '0';
  }

  function seconds(n, places) {
    if (n === null || n === undefined) return DASH;
    return Number(n).toFixed(places === undefined ? 1 : places);
  }

  /* ------------------------------------------------------------------ cols */

  var COLS = {

    /* --- identity ---------------------------------------------------- */

    pos: {
      label: 'P', src: 'sim', w: 30, align: 'r', cls: 'c-pos',
      get: function (x) { return String(x.c.p || DASH); }
    },
    cpos: {
      label: 'CP', src: 'sim', w: 30, align: 'r', cls: 'c-pos',
      get: function (x) { return String(x.c.cp || DASH); }
    },
    gain: {
      /* Meaningless outside a race: there is no grid to have gained against. */
      label: '+/−', src: 'derived', w: 38, align: 'c', cls: 'c-gain',
      race: true, timed: false,
      get: function (x) {
        if (x.c.gain === null || x.c.gain === undefined) return DASH;
        var g = x.c.gain;
        return {
          text: g > 0 ? '▲' + g : (g < 0 ? '▼' + Math.abs(g) : '='),
          cls: g > 0 ? 'up' : (g < 0 ? 'down' : 'level'),
          title: 'Started ' + (x.c.grid || '?')
        };
      }
    },
    num: {
      label: '#', src: 'roster', w: 34, align: 'c', cls: 'c-num',
      get: function (x) { return x.d.num ? '#' + x.d.num : DASH; }
    },
    name: {
      label: 'DRIVER', src: 'roster', w: 150, align: 'l', cls: 'c-name',
      get: function (x) {
        return PW.displayName(x.c.i, (x.opt && x.opt.names) || 'short') || DASH;
      }
    },
    team: {
      label: 'TEAM', src: 'roster', w: 130, align: 'l', cls: 'c-team',
      get: function (x) { return x.d.team || DASH; }
    },
    car: {
      label: 'CAR', src: 'roster', w: 90, align: 'l', cls: 'c-car',
      get: function (x) { return x.d.carShort || x.d.car || DASH; }
    },
    cls: {
      label: 'CLS', src: 'sim', w: 44, align: 'c', cls: 'c-cls',
      get: function (x) {
        if (!x.d.classShort) return DASH;
        return { text: x.d.classShort, cls: 'chip', colour: x.d.classColor };
      }
    },
    lic: {
      label: 'LIC', src: 'roster', w: 42, align: 'c', cls: 'c-lic',
      get: function (x) {
        if (!x.d.license) return DASH;
        return { text: x.d.license, cls: 'chip', colour: x.d.licColor };
      }
    },
    ir: {
      label: 'IR', src: 'roster', w: 46, align: 'r', cls: 'c-ir',
      get: function (x) {
        var v = x.d.irating || 0;
        if (!v) return DASH;
        if (x.opt && x.opt.irStyle === 'long') return String(v);
        return v >= 1000 ? (v / 1000).toFixed(1) + 'k' : String(v);
      }
    },
    tag: {
      label: 'TAG', src: 'roster', w: 70, align: 'l', cls: 'c-tag',
      get: function (x) {
        var t = x.prof && x.prof.tag;
        if (!t || !t.label) return DASH;
        return { text: t.label, cls: 'chip', colour: t.colour || '#888' };
      }
    },
    flag: {
      label: '', src: 'roster', w: 26, align: 'c', cls: 'c-flag',
      get: function (x) {
        var code = (x.prof && x.prof.country) || x.d.country || '';
        return code ? PW.flag(code) : DASH;
      }
    },

    /* --- timing ------------------------------------------------------ */

    gap: {
      label: 'GAP', src: 'derived', w: 62, align: 'r', cls: 'c-time',
      get: function (x) {
        if (x.timed) {
          return x.c.gl === null || x.c.gl === undefined
            ? DASH : '+' + seconds(x.c.gl, 3);
        }
        return PW.fmtGap(x.c.gl, x.c.ld) || DASH;
      }
    },
    int: {
      label: 'INT', src: 'derived', w: 62, align: 'r', cls: 'c-time',
      get: function (x) {
        if (x.timed) {
          return x.c.iv === null || x.c.iv === undefined
            ? DASH : '+' + seconds(x.c.iv, 3);
        }
        return PW.fmtGap(x.c.iv, x.c.ld) || DASH;
      }
    },
    cgap: {
      label: 'C GAP', src: 'derived', w: 62, align: 'r', cls: 'c-time',
      get: function (x) { return PW.fmtGap(x.c.gcl, x.c.ld) || DASH; }
    },
    cint: {
      label: 'C INT', src: 'derived', w: 62, align: 'r', cls: 'c-time',
      get: function (x) { return PW.fmtGap(x.c.civ, x.c.ld) || DASH; }
    },
    last: {
      label: 'LAST', src: 'sim', w: 68, align: 'r', cls: 'c-time',
      get: function (x) {
        var t = PW.fmtLap(x.c.last);
        if (!t) return DASH;
        var best = x.meta && x.meta.fastest && x.meta.fastest.time;
        if (best && x.c.last && Math.abs(x.c.last - best) < 1e-6) {
          return { text: t, cls: 'purple' };
        }
        return t;
      }
    },
    best: {
      label: 'BEST', src: 'sim', w: 68, align: 'r', cls: 'c-time',
      get: function (x) {
        var t = PW.fmtLap(x.c.best);
        if (!t) return DASH;
        return x.c.fast ? { text: t, cls: 'purple' } : t;
      }
    },
    cur: {
      label: 'CUR', src: 'derived', w: 68, align: 'r', cls: 'c-time',
      get: function (x) { return PW.fmtLap(x.c.cur, 1) || DASH; }
    },
    avg: {
      label: 'AVG', src: 'derived', w: 68, align: 'r', cls: 'c-time',
      get: function (x) {
        var n = (x.opt && x.opt.avgn) || 5;
        var v = n >= 10 ? x.c.a10 : (n <= 3 ? x.c.a3 : x.c.a5);
        return PW.fmtLap(v) || DASH;
      }
    },
    delta: {
      label: 'DELTA', src: 'derived', w: 58, align: 'r', cls: 'c-time',
      race: true, timed: false,
      get: function (x) {
        if (x.c.delta === null || x.c.delta === undefined) return DASH;
        return {
          text: PW.fmtDelta(x.c.delta) || DASH,
          cls: x.c.delta < 0 ? 'good' : (x.c.delta > 0 ? 'bad' : '')
        };
      }
    },
    rel: {
      label: 'REL', src: 'derived', w: 58, align: 'r', cls: 'c-time',
      get: function (x) {
        if (x.rel === null || x.rel === undefined) return DASH;
        return {
          text: (x.rel > 0 ? '+' : '') + Number(x.rel).toFixed(1),
          cls: x.rel < 0 ? 'ahead' : 'behind'
        };
      }
    },
    lap: {
      label: 'LAP', src: 'sim', w: 40, align: 'r', cls: 'c-lap',
      get: function (x) { return String(x.c.lap || 0); }
    },
    sec: {
      label: 'SECTORS', src: 'derived', w: 150, align: 'r', cls: 'c-sec',
      get: function (x) {
        var s = x.c.sec || [];
        if (!s.length) return DASH;
        return s.map(function (v, i) {
          var t = v === null || v === undefined ? DASH : Number(v).toFixed(1);
          return (x.c.secb && x.c.secb[i]) ? '*' + t : t;
        }).join('  ');
      }
    },

    /* --- race state --------------------------------------------------- */

    st: {
      label: '', src: 'sim', w: 42, align: 'c', cls: 'c-st',
      get: function (x) {
        var m = {
          pit: { text: 'PIT', cls: 'badge pit' },
          stall: { text: 'PIT', cls: 'badge pit' },
          off: { text: 'OFF', cls: 'badge off' },
          out: { text: 'OUT', cls: 'badge out' },
          nodata: { text: DASH, cls: 'badge nodata', title: 'Not being streamed to this client. Raise Max Cars to 63.' }
        };
        return m[x.c.st] || '';
      }
    },
    stops: {
      label: 'STOP', src: 'sim', w: 38, align: 'c', cls: 'c-int',
      race: true, timed: false,
      get: function (x) { return x.c.stops ? String(x.c.stops) : DASH; }
    },
    pitlap: {
      label: 'PIT LAP', src: 'sim', w: 52, align: 'r', cls: 'c-int',
      race: true, timed: false,
      get: function (x) { return x.c.pitlap ? 'L' + x.c.pitlap : DASH; }
    },
    pittime: {
      label: 'PIT', src: 'sim', w: 52, align: 'r', cls: 'c-time',
      race: true, timed: false,
      get: function (x) { return x.c.pitdur ? seconds(x.c.pitdur, 1) : DASH; }
    },
    pitlane: {
      label: 'LANE', src: 'sim', w: 52, align: 'r', cls: 'c-time',
      race: true, timed: false,
      get: function (x) { return x.c.plt ? seconds(x.c.plt, 1) : DASH; }
    },
    tage: {
      label: 'TYRE', src: 'estimate', w: 46, align: 'r', cls: 'c-int',
      race: true, timed: false,
      get: function (x) {
        /* Only a measurement once we have watched this car come down pit road.
           Before that it is laps since we started watching, which is a very
           different thing, so it is shown greyed with the reason in the title
           rather than dressed up as tyre age. */
        if (x.c.tage === null || x.c.tage === undefined) return DASH;
        var band = (x.opt && x.opt.tband);
        var v = x.c.tage;
        var text = band
          ? (v <= 5 ? 'NEW' : (v <= 20 ? 'OK' : 'OLD'))
          : String(v) + 'L';
        if (!x.c.tk) {
          return {
            text: text, cls: 'unsure',
            title: 'Never seen pitting. This is laps since PitWall started, not tyre age.'
          };
        }
        return { text: text, title: 'Laps since leaving pit road on lap ' + x.c.pitlap };
      }
    },
    comp: {
      label: 'CMP', src: 'sim', w: 34, align: 'c', cls: 'c-int',
      get: function (x) {
        var lbl = compoundLabel(x.c.tyre, x.opt && x.opt.compounds);
        if (lbl === DASH) return DASH;
        return { text: lbl, cls: 'chip compound c' + x.c.tyre };
      }
    },
    inc: {
      label: 'INC', src: 'sim', w: 36, align: 'r', cls: 'c-int',
      get: function (x) {
        var v = x.c.inc;
        return (v === null || v === undefined) ? DASH : String(v) + 'x';
      }
    },
    fr: {
      label: 'FR', src: 'sim', w: 30, align: 'c', cls: 'c-int',
      race: true, timed: false,
      get: function (x) { return x.c.fr ? String(x.c.fr) : DASH; }
    },
    irc: {
      label: 'iR±', src: 'derived', w: 46, align: 'r', cls: 'c-int',
      race: true, timed: false,
      get: function (x) {
        if (x.c.irc === null || x.c.irc === undefined) return DASH;
        return {
          text: signed(x.c.irc),
          cls: x.c.irc > 0 ? 'up' : (x.c.irc < 0 ? 'down' : 'level'),
          title: 'Projected if the race ended now'
        };
      }
    },
    tsp: {
      label: 'SPEED', src: 'estimate', w: 54, align: 'r', cls: 'c-int',
      get: function (x) {
        if (!x.c.tsp) return DASH;
        var kph = x.c.tsp;
        var mph = (x.opt && x.opt.units === 'mph');
        return {
          text: Math.round(mph ? kph * 0.621371 : kph) + (mph ? '' : ''),
          cls: 'unsure',
          title: 'Estimated from track distance covered. Top speed on the last lap.'
        };
      }
    },
    p2p: {
      label: 'P2P', src: 'sim', w: 38, align: 'c', cls: 'c-int',
      get: function (x) {
        if (x.c.p2pn === undefined || x.c.p2pn === null || x.c.p2pn < 0) return DASH;
        return { text: String(x.c.p2pn), cls: x.c.p2p ? 'badge p2p-on' : '' };
      }
    }
  };

  /* --------------------------------------------------------------- helpers */

  /* Parse a column spec. "pos,num,name:180,gap,tage" gives the order, and an
     optional width after a colon overrides the default. Unknown keys are
     dropped rather than throwing, because these arrive from a URL that a human
     typed into OBS at five to eight on a Tuesday. */
  PW.parseCols = function (spec, dflt) {
    var raw = String(spec === undefined || spec === null || spec === '' ? (dflt || '') : spec);
    var out = [];
    raw.split(',').forEach(function (part) {
      part = part.trim();
      if (!part) return;
      var bits = part.split(':');
      var key = bits[0].trim();
      var def = COLS[key];
      if (!def) return;
      var w = bits.length > 1 ? parseInt(bits[1], 10) : NaN;
      out.push({
        key: key,
        def: def,
        label: def.label,
        w: isFinite(w) && w > 0 ? w : def.w,
        align: def.align || 'l'
      });
    });
    return out;
  };

  /* Drop columns that cannot mean anything in this session. A pit stop count
     in qualifying is a column of dashes taking up room a lap time wants. */
  PW.usableCols = function (cols, timed) {
    return cols.filter(function (c) {
      if (timed && c.def.timed === false) return false;
      if (!timed && c.def.race === false) return false;
      return true;
    });
  };

  /* Evaluate one column for one car. Always returns {text, cls, title, colour}
     so a caller never has to test which shape came back. */
  PW.cell = function (col, ctx) {
    var v;
    try {
      v = col.def.get(ctx);
    } catch (e) {
      v = DASH;
    }
    if (v === null || v === undefined) v = DASH;
    if (typeof v === 'string') return { text: v, cls: '', title: '', colour: '' };
    return {
      text: v.text === undefined ? DASH : v.text,
      cls: v.cls || '',
      title: v.title || '',
      colour: v.colour || ''
    };
  };

  /* Build the ctx object once per car per tick. */
  PW.cellCtx = function (c, opt, extra) {
    var d = PW.driver(c.i) || {};
    var ctx = {
      c: c,
      d: d,
      prof: d.profile || null,
      me: null,
      meta: PW.tick || {},
      tick: PW.tick || {},
      opt: opt || {},
      timed: !!(PW.tick && PW.tick.session && PW.tick.session.mode === 'timed'),
      multiclass: PW.multiclass()
    };
    if (extra) { for (var k in extra) if (extra.hasOwnProperty(k)) ctx[k] = extra[k]; }
    return ctx;
  };

  /* ------------------------------------------------------------ painting */

  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  /*
   * Draw one row's worth of cells into `host`.
   *
   * The DOM is only rebuilt when the column list itself changes, which happens
   * when a session flips from qualifying to race, not every tick. `override`
   * lets a widget special-case a column without forking the whole renderer:
   * the tower uses it so a car in the pit lane shows PIT in place of a gap it
   * cannot meaningfully have.
   */
  PW.paintCells = function (host, cols, ctx, override) {
    var sig = cols.map(function (c) { return c.key + ':' + c.w; }).join(',');
    if (host._sig !== sig) {
      host.innerHTML = '';
      cols.forEach(function (col) {
        var n = PW.el('div', 'cx al-' + col.align);
        n.style.width = col.w + 'px';
        host.appendChild(n);
      });
      host._sig = sig;
    }
    for (var i = 0; i < cols.length; i++) {
      var col = cols[i], node = host.children[i];
      if (!node) continue;
      var v = null;                       // reset every iteration, see below
      if (override) v = override(col, i, ctx);
      if (!v) v = PW.cell(col, ctx);
      writeCell(node, v);
    }
  };

  function writeCell(node, v) {
    var html;
    if (v.cls && (v.cls.indexOf('chip') === 0 || v.cls.indexOf('badge') === 0)) {
      html = '<span class="' + v.cls + '"' +
             (v.colour ? ' style="background:' + esc(v.colour) + '"' : '') + '>' +
             esc(v.text) + '</span>';
    } else if (v.cls) {
      html = '<span class="' + v.cls + '">' + esc(v.text) + '</span>';
    } else {
      html = esc(v.text);
    }
    if (node._h !== html) { node.innerHTML = html; node._h = html; }
    var t = v.title || '';
    if (node.title !== t) node.title = t;
  }

  PW.escHtml = esc;
  PW.COLS = COLS;
  PW.DASH = DASH;
  PW.compoundLabel = compoundLabel;

  /* Every column key, grouped, for the settings editor to render a picker. */
  PW.COL_GROUPS = [
    ['Identity', ['pos', 'cpos', 'gain', 'num', 'name', 'team', 'car', 'cls', 'lic', 'ir', 'tag', 'flag']],
    ['Timing',   ['gap', 'int', 'cgap', 'cint', 'last', 'best', 'cur', 'avg', 'delta', 'rel', 'lap', 'sec']],
    ['Race',     ['st', 'stops', 'pitlap', 'pittime', 'pitlane', 'tage', 'comp', 'inc', 'fr', 'irc', 'tsp', 'p2p']]
  ];
}(window.PW = window.PW || {}));
