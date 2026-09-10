# Setting up OBS

Every PitWall graphic is a **Browser** source. Nothing else needs installing.

Start PitWall first (`run.bat`, or `run-demo.bat` to build your scenes without
iRacing open), then in OBS: **Sources → + → Browser**.

For each source, set the URL and the **Width/Height** to the size in the table.
Leave "Shutdown source when not visible" **off** — the graphics reconnect on
their own, but leaving them alive avoids a visible reconnect when you cut to a
scene.

## The sources

| URL | Width | Height |
|---|---|---|
| `http://127.0.0.1:8099/broadcast/tower.html` | 460 | 1080 |
| `http://127.0.0.1:8099/broadcast/session-bar.html` | 1920 | 120 |
| `http://127.0.0.1:8099/broadcast/driver-card.html` | 1920 | 1080 |
| `http://127.0.0.1:8099/broadcast/battle.html` | 1920 | 1080 |
| `http://127.0.0.1:8099/broadcast/trackmap.html` | 600 | 600 |
| `http://127.0.0.1:8099/broadcast/standings.html` | 1920 | 1080 |

The full-frame ones (driver card, battle box, standings) are 1920×1080 and
position themselves inside that frame, so drop them in at 0,0 and do not resize
them. Use their `pos=` option to move them to a different corner instead —
scaling a browser source makes the text soft.

## Configuring a graphic

Options go on the URL. The control panel's **Overlays** tab has a builder that
writes them for you, with a live preview.

A tower showing intervals and last lap, grouped by class, at 110%:

```
http://127.0.0.1:8099/broadcast/tower.html?rows=16&col=int&col2=last&group=class&scale=1.1
```

Common options, on every graphic:

| Option | Does |
|---|---|
| `scale` | Size multiplier, e.g. `1.15`. Better than resizing the source in OBS. |
| `accent` | Override the league accent colour. URL-encode the `#` as `%23`. |
| `opacity` | Panel background opacity, `0`–`1`. Lower it if the video underneath matters. |
| `names` | `full`, `short` (default), `surname`, `abbrev`. |
| `class` | Filter to one class, e.g. `?class=GT3`. |
| `bg` | `1` for a solid background, for checking a graphic outside OBS. |
| `debug` | `1` shows a connection dot. |
| `server` | Point the page at a different PitWall machine. See below. |

Each file lists its own options in a comment at the top.

## A scene layout that works

**Race scene**
- Game capture or NDI feed of iRacing
- `session-bar.html` at the top, 0,0
- `tower.html` down the left, 0,120
- `driver-card.html` at 0,0 with `?auto=1` — it appears for eight seconds
  whenever the director cuts to a new car, then hides itself
- `battle.html` at 0,0 with `?max=1.5` — it hides itself unless there is an
  actual fight
- `trackmap.html` bottom right

Because the card and the battle box hide themselves, you can leave them in the
scene permanently and never touch them during a race.

**Pre-race / intermission scene**
- `standings.html` over your holding graphic or a scenic camera

**Results scene**
- `standings.html` again — it re-titles itself to RESULTS and promotes the
  podium once the checkered flag is out

## A second machine

If OBS runs on a different PC from iRacing, point the browser sources at the
sim PC's address and tell the page where to get data:

```
http://192.168.1.20:8099/broadcast/tower.html
```

That is all — the page is served by, and connects back to, the same machine.

If instead you host the overlay files somewhere else (GitHub Pages, say), use
the `server` option to point them back:

```
https://your-league.github.io/overlays/broadcast/tower.html?server=http://192.168.1.20:8099
```

The control panel's Status tab lists the exact LAN addresses to use. If another
machine cannot reach it, allow Python through the Windows firewall on **private**
networks.

## If a graphic is blank

1. Open the same URL in a normal browser tab. If it works there and not in OBS,
   it is OBS's browser cache — right-click the source → **Refresh cache of
   current page**.
2. Add `?debug=1` to the URL. A red dot bottom-right means it cannot reach the
   server; green means it is connected and simply has nothing to show yet.
3. Check the control panel's **Diagnostics** tab. If PitWall is not reading
   iRacing, every graphic is correctly showing nothing.
4. Graphics deliberately render **nothing at all** before data arrives, rather
   than a skeleton of dashes over your video. Blank before the session starts is
   the intended behaviour.

## Performance

The graphics are lightweight, but OBS browser sources are full Chromium
instances. If you are running OBS on the same PC as iRacing:

- Use the fewest sources you need, and put rarely-used ones in scenes rather
  than duplicating them everywhere.
- Lower the update rate on graphics that do not need to be fluid, e.g.
  `standings.html?rate=2`. The tower is fine at its default; a full-field board
  does not need 15 updates a second.
- The stutter people usually blame on overlays is almost always G-SYNC or
  cross-GPU desktop compositing. The Diagnostics tab checks for both and tells
  you what to change.
