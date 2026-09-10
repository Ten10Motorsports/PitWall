# PitWall overlay contract

Read this plus `docs/DATA_SCHEMA.json` (a real captured payload) before writing
any page. Read `web/shared/pitwall.js` and `web/shared/base.css` in full — they
already contain the connection handling, formatting and primitives. Do not
reimplement any of it.

## Page skeleton

Every overlay is one self-contained `.html` file. No build step, no frameworks,
no CDN, no external fonts (they will not load in OBS reliably). Inline the CSS
and JS specific to that page; pull shared things from `../shared/`.

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>PitWall — Timing Tower</title>
<link rel="stylesheet" href="../shared/base.css">
<style> /* page-specific only */ </style>
</head>
<body class="overlay">          <!-- "solid" instead for full pages -->
  <div id="pw-status"></div>
  <div id="root" class="scale-root"></div>
<script src="../shared/pitwall.js"></script>
<script>
  PW.applyChrome();
  PW.on('meta', render);
  PW.on('tick', render);
  PW.start({ sub: ['meta','tick'], rate: 10 });
</script>
</body>
</html>
```

## Rules that matter

1. **`body.overlay` must stay transparent.** These are OBS browser sources and
   Electron HUD windows sitting over video and over the sim. Never set a page
   background on an overlay page.
2. **Never render a raw `-1`.** iRacing uses `-1` for "no time set" and "not in
   world". The engine already converts those to `null`; render `null` as an
   empty cell or a dash, never as a number.
3. **Use `PW.reconcile`** for any list of cars. Rebuilding innerHTML every tick
   makes rows flicker and kills position-change transitions. Rows are keyed on
   the car index `c.i`, never on position.
4. **Use `PW.fmtLap`, `PW.fmtGap`, `PW.fmtClock`, `PW.fmtDelta`.** They handle
   the sentinels and the tabular-numeral formatting.
5. **Everything configurable comes from the query string** via `PW.opt`,
   `PW.optNum`, `PW.optBool`, `PW.optList`. A broadcaster configures a graphic
   by editing the browser source URL, not by opening a settings app. Document
   every option your page supports in an HTML comment at the top of the file.
6. **Handle the empty state.** On first load there is no `meta` and no `tick`.
   Render nothing (for a broadcast graphic) or a quiet "waiting for iRacing"
   card (for the timing page and control panel). Never render an empty skeleton
   full of dashes over live video.
7. **Multiclass is the normal case for a league.** Colour by
   `d.classColor`, and support `?group=class` to break the tower into class
   blocks. When there is one class, no class chrome should appear at all.
8. **Driver-submitted profile data is optional and must degrade.** `d.profile`
   may be absent, or present with only some fields. Fall back to the iRacing
   values (`d.name`, `d.team`, `d.num`) every time. Never leave a hole.
9. Tabular numbers everywhere a number changes (class `num`), so the layout
   does not jitter.
10. No `alert()`, no `confirm()`, no `prompt()`.

## Data access cheat sheet

```js
PW.meta                 // identity: track, event, drivers{}, classes[], sectors[]
PW.tick                 // per-frame: session, cars[], fastest, battles, player
PW.cars()               // tick.cars, already in official running order
PW.driver(idx)          // meta.drivers[idx] — name, num, team, classColor, irating…
PW.profile(idx)         // driver-submitted profile, or null
PW.classOf(idx)         // {id, short, color, count}
PW.multiclass()         // true when more than one class is running
PW.focusIdx()           // car the broadcast camera is on, else the player's car
PW.relative(idx, n, m)  // n cars ahead + m behind by track position
PW.displayName(idx)     // respects ?names=full|short|surname|abbrev
PW.flag('GB')           // 🇬🇧
PW.control(action, {})  // POST /api/control
PW.api('/api/roster')   // fetch helper that throws on error
```

Car fields (short keys, see DATA_SCHEMA.json):
`i` index · `p` official position · `lp` live track-position rank ·
`cp` class position · `lap` · `lc` laps complete · `pct` lap distance 0..1 ·
`gl` gap to leader · `iv` interval to car ahead · `gcl` gap to class leader ·
`civ` interval in class · `ld` laps down · `last` · `best` · `cur` current lap
time · `sec[]` last lap's sector times · `secb[]` which of those were personal
bests · `pbsec[]` personal best sectors · `st` state (`run`/`pit`/`stall`/`off`/
`out`) · `stops` · `pitdur` last stop duration · `stint` laps this stint ·
`fr` fast repairs used · `fast` holds fastest lap · `inc` incidents ·
`delta` live delta to own best.

`gl` and `iv` are `null` under caution (the field is bunched and gaps are
meaningless) — show a dash, not a zero.

## Look

Dark, high contrast, broadcast-weight. Think a modern F1/WEC world feed, not a
racing game. Specifics:

- Type: the stack in `--font`. Numbers always in `--mono` via `.num`.
- Rows: dense but not cramped. `--row-h` is 30px at scale 1.
- Colour is information, never decoration. Class colour, flag colour, purple
  for the fastest lap, green for a personal best, yellow for pit. Nothing else
  should be coloured.
- Panels are `rgba(var(--panel), var(--panel-opacity))` with a 1px `--line`
  border. Subtle. The video underneath has to stay readable.
- Motion: position changes get a short transform transition. Nothing bounces,
  nothing pulses except an actual flashing flag.
- No emoji as UI furniture. Country flags are the one exception.

## Testing

`python3 -m server.main --demo --demo-roster --no-browser` runs a synthetic
24-car two-class race at Spa on port 8099, including sample driver profiles.
Everything must look right against that before it is done.
