# PitWall

iRacing live timing, broadcast graphics, in-game HUD and driver-submitted
profiles. One program, one port, no installer, nothing to compile.

Built for running a league: the broadcast, the timing screen the race director
watches, the overlay the drivers run, and the sign-up form the drivers fill in,
all fed by the same engine so the numbers on screen always agree with each
other.

---

## Start here

**The short version:** download `PitWall.exe` from the Releases page, put it in
a folder, double-click it. No Python, no setup, nothing else to install.

Only **one person per session** needs it — whoever is spectating or driving.
Everyone else opens a web page.

**Running from source instead** (or building your own .exe):

1. Install Python 3.9 or newer from [python.org](https://www.python.org/downloads/).
   Tick **"Add python.exe to PATH"** during setup. That is the only prerequisite.
2. Double-click **`run.bat`**.
3. Your browser opens the control panel. Everything you need is on it.

**Want to look around first, without iRacing?** Double-click **`run-demo.bat`**.
It runs a synthetic 24-car two-class race at Spa so you can build your OBS
scenes, style every graphic and rehearse a whole broadcast with the sim closed.
Add `--speed 14` to run a 42-lap race in five minutes.

There is no `pip install`. There are no dependencies. PitWall uses only what
ships with Python.

---

## What you get

### Broadcast graphics (OBS browser sources)

| Source | Size | What it is |
|---|---|---|
| `broadcast/tower.html` | 460 × 1080 | Timing tower. Position, class, gaps, pit state, fastest lap. |
| `broadcast/session-bar.html` | 1920 × 120 | League, round, track, session, flag, laps or clock, fastest lap, weather, SoF. Drops the least important blocks rather than overlapping when they do not all fit. |
| `broadcast/driver-card.html` | 1920 × 1080 | The "who is this" card, built from driver-submitted profiles. |
| `broadcast/battle.html` | 1920 × 1080 | Battle box for the closest fight, with a closing/opening trend. |
| `broadcast/trackmap.html` | 600 × 600 | Live track map that draws its own geometry (see below). |
| `broadcast/standings.html` | 1920 × 1080 | Full-field board for pre-race, intermission and results. |

Every graphic is configured by its URL, not by a settings app:

```
tower.html?rows=16&col=int&col2=last&group=class&names=surname&scale=1.1&accent=%23ff8800
```

The control panel has a builder that writes those URLs for you.

### Second-screen timing

`http://<your-pc>:8099/timing` — a full timing monitor with 24 columns
(sortable, choosable, remembered), sector times coloured purple/green, click a
car for its profile, lap history and lap chart. It reflows into a card list on a
phone. Open it on a laptop, tablet or phone anywhere on your network.

### In-game HUD, for drivers

**Drivers download one file: `PitWall-HUD.exe`.** It carries the engine inside
itself, so there is nothing else to run, nothing to start in the right order,
and no console window. Double-click it and a settings window opens.

Eight widgets, each with its own saved settings:

| Widget | What it is |
|---|---|
| Relative | Cars around you on track with the gap in seconds |
| Leaderboard | The broadcast timing tower, sized for in-car, with pin-and-rotate |
| Flags and status | Flag, position, incidents, laps to go, the warnings that end races |
| Blindspot | Something alongside, and which side, built for peripheral vision |
| Lap time graph | Your last laps as a chart, best in purple, pit laps greyed |
| Delta bar | How this lap compares, with the sector breakdown |
| Fuel and tyres | Fuel per lap, laps left, what to add to finish |
| Inputs trace | Throttle, brake and steering |

**Layouts are per screen arrangement, and defaults are computed from the screen
you actually have.** A triple-screen or ultrawide driver opens it to a sensible
layout on the centre display rather than everything stacked in a corner. Plug
in a different monitor and that arrangement gets its own layout; plug the old
one back and your layout is still there. Settings are separate from positions,
because a preference should follow you between setups and a coordinate should
not.

| Shortcut | What it does |
|---|---|
| `Ctrl+Shift+E` | Unlock the widgets to drag and resize. Press again to lock and make them click-through. |
| `Ctrl+Shift+S` | Open settings. |
| `Ctrl+Shift+H` | Hide and show the whole HUD. |
| `Ctrl+Shift+R` | Reload every widget. |

The same pages also work as OBS sources or in a browser tab. One implementation
of every widget, three places it can be shown.

Building it from source instead: `cd hud-app && npm install && npm start`.

### Driver-submitted profiles

iRacing gives you a name, a number and a team. It gives you nothing else a
broadcast wants.

**Nobody has to sign up for the timing to work.** Names, numbers, teams, car,
class, iRating and licence all come from the sim, so a driver who never filled
in the form still appears correctly on the tower, the standings board and the
timing screen. Signing up only adds the headshot, country, hometown, bio,
sponsor and socials that the driver card uses.

Send your drivers `http://<your-pc>:8099/register`. They enter their iRacing
customer ID once and add a headshot, country, pronouns, hometown, a one-line
bio, sponsor logo, socials and an accent colour. Everything is joined to live
telemetry by customer ID, so it survives name changes and works in team races
where the driver in the car changes mid-stint.

The roster can live in a **GitHub repo** so the whole league shares one source of
truth, with commit history and rollback for free. See
[docs/GITHUB_ROSTER.md](docs/GITHUB_ROSTER.md).

Better still, drivers can sign up through a **GitHub issue form** and the roster
updates itself — no server running, no admin, and they can fix their own entry
by editing their submission. See [docs/GITHUB_HOSTING.md](docs/GITHUB_HOSTING.md).

### Practice and qualifying, not just races

A race is scored by who is in front. Practice and qualifying are scored by who
set the quickest lap, and iRacing does not always score them for you: in an open
practice it leaves every car's position at zero.

PitWall detects the session type and changes what it computes. In practice,
qualifying, warmup and testing:

- the running order is **quickest lap first**, with cars yet to set a lap at the
  back rather than shuffled through the field
- **gap** becomes how far off the quickest lap you are, and **interval** the
  difference to the car classified ahead, instead of distances round the track
  that mean nothing
- the timing tower switches its columns to **best and last** by itself, and
  switches back when the race starts
- laps down and the battle box turn themselves off

None of that needs configuring, and none of it needs the overlay reloading: a
league night that runs practice, then qualifying, then the race is one set of
OBS sources that follow along. A tower with `col=` in its URL is left alone, on
the grounds that you asked for that column and should get it.

### Track maps that build themselves

Most overlay tools ship hand-drawn track art and are missing whichever circuit
you are racing this week.

PitWall watches the latitude, longitude and lap distance the sim publishes for
the car being followed, and reconstructs the real outline of the circuit from
one clean lap. It is cached per track ID and it is correct, to scale, for every
track including new ones — with no asset pack and nothing to update.

### It tells you why it is not working

The single most common experience with overlay software is: install it, nothing
appears, and the app says nothing.

The **Diagnostics** tab checks the actual causes and names the one that is
biting you:

- iRacing not running, or running but not in a session
- `irsdkEnableMem=0` in `app.ini` — the sim is publishing no telemetry at all
- iRacing in exclusive fullscreen, where no overlay of any kind can draw
- Windows display scaling not at 100%, which makes HUD positions drift
- Elevation mismatch, which silently kills camera and replay control
- Two GPUs compositing across the bus, which is what actually causes the
  stutter people blame on overlays
- G-SYNC configuration, with the fix spelled out

---

## How it fits together

```
   iRacing  ──shared memory──▶  PitWall server (Python, stdlib only)
                                        │
                                        │  one WebSocket feed
                                        │
              ┌─────────────────────────┼─────────────────────────┐
              ▼                         ▼                         ▼
      OBS browser sources     second-screen timing        in-game HUD
      (broadcast graphics)    (laptop / tablet / phone)   (Electron shell)
```

One engine computes the numbers once. The tower, the timing page and the HUD are
different views of the same state, so they cannot disagree.

The server reads iRacing **read-only**, through the SDK iRacing publishes for
exactly this purpose. Nothing is injected into the sim, no graphics API is
hooked, and no files are modified. Camera and replay control use
`irsdk_broadcastMsg`, iRacing's own remote-control interface.

### What the engine computes that iRacing does not give you

- Live current-lap time for **every** car (iRacing only gives you your own)
- Sector times (iRacing gives you sector *boundaries*, not times)
- Intervals between cars, wrap-corrected, lap-aware and multiclass-aware
- Predicted lap times and live delta
- Fuel per lap, laps remaining, and how much to add to finish
- Pit stop counts, stationary time and stint tracking
- Driver-change detection for team races
- Strength of field

Gaps use `CarIdxEstTime` where the sim provides it, and otherwise a
lap-distance-to-time model the engine builds from observed clean laps. That
matters: a car half way around Spa is not half a lap time in, because half of
Spa is Kemmel straight. Naive percentage maths breathes by half a second every
lap. Under caution the field is bunched and gaps are meaningless, so PitWall
shows a dash instead of a number it cannot stand behind.

---

## Requirements and honest limits

- **Live telemetry needs Windows.** That is iRacing's SDK, not a PitWall choice
  — the shared memory it publishes is a Windows kernel object. Everything else
  (the server, all overlays, the timing page, the roster, demo mode) runs on
  any OS.
- **The in-game HUD needs iRacing in windowed or borderless mode.** Exclusive
  fullscreen hands the entire screen to one program, so no desktop overlay can
  draw on it. This is true of every overlay tool that does not inject into the
  game, and injecting is the thing that carries anti-cheat risk. The OBS and
  second-screen outputs work in any display mode.
- **The in-game HUD does not work in VR** for the same reason: a VR compositor
  never sees desktop windows. A VR build needs an OpenXR overlay layer, which is
  a different program from this one. The second-screen timing page is the
  practical answer for a VR driver — put it on a phone or tablet.
- Set **Max Cars to 63** on the iRacing website and your connection to 1 Mbit or
  faster, or position data for the back of the field is unreliable for every
  tool, not just this one.

---

## Running your league off GitHub

Everything except reading the telemetry can live on GitHub, with nothing for
your league to install:

- **Driver sign-ups** — a GitHub issue form. A workflow validates it, updates
  `roster.json`, replies with what it recorded, and closes the issue. Editing
  the issue updates the entry.
- **The Windows app** — built and smoke-tested on a Windows runner on every
  tagged release, then attached to the release as a single `.exe`.
- **The handbook, the showroom and the entry list** — published to GitHub Pages.
  One link is your league's whole onboarding.

The one thing that cannot move: reading iRacing's telemetry, which lives in
Windows shared memory on the PC running the sim. A cloud runner cannot see it.
Full detail, including what not to waste time trying, is in
[docs/GITHUB_HOSTING.md](docs/GITHUB_HOSTING.md).

## Files

```
run.bat / run.sh          start the server
run-demo.bat              start with a synthetic race, no iRacing needed
server/                   the engine
  irsdk.py                shared-memory reader
  sessionyaml.py          parser for iRacing's session string
  engine.py               timing, gaps, sectors, fuel, stints
  webserver.py            HTTP + WebSocket, stdlib only
  roster.py               driver profiles, GitHub sync
  leagues.py              saved league profiles and season calendars
  control.py              camera / replay / pit control
  diagnostics.py          the self-check
  trackmap.py             self-building track geometry
  demo.py                 synthetic race
web/                      every overlay and page
hud-app/                  Electron shell for the in-game HUD
config/pitwall.json       league branding and roster settings
data/                     roster, league profiles, uploaded images, cached track maps
docs/                     setup guides and the overlay contract
.github/workflows/        build the app, publish the site, process sign-ups
scripts/                  the .exe build and the sign-up parser
site/                     the GitHub Pages landing page
```

## Command line

```
python -m server.main                     live telemetry
python -m server.main --demo              synthetic race
python -m server.main --demo --scenario practice    synthetic practice session
python -m server.main --demo --scenario qualify     synthetic qualifying
python -m server.main --demo --speed 14   run a race in minutes
python -m server.main --port 8100         different port
python -m server.main --host 127.0.0.1    local only, no LAN access
python -m server.main --no-browser        do not open the control panel
```

## Licence and iRacing's rules

Reading `Local\IRSDKMemMapFileName` read-only is the sanctioned path — iRacing
publishes the SDK and the `irsdk*` settings in `app.ini` specifically so third
parties can consume it, and a whole commercial ecosystem is built on it.
PitWall does not inject, hook, or write to the sim, and does not synthesise
inputs. If you redistribute live timing publicly for a league, check your
league's and iRacing's broadcast policy separately.

## Columns, profiles and the ticker (1.7)

### The column engine

Every list in PitWall draws from one set of column definitions in
`web/shared/columns.js`: the broadcast tower, the in-car relative, the in-car
leaderboard and the scrolling ticker. A column means the same thing in all four
and is written once.

Add columns to any of them with `cols=`, comma separated, in the order you want
them. An optional width in pixels follows a colon:

    ?cols=st,gain,gap,int,last,best,tage,comp,stops,irc,name:180

Leaving `cols` out keeps exactly the behaviour that page had before, so nothing
already set up in OBS changes.

| Key | Column | Where the figure comes from |
| --- | --- | --- |
| `pos` `cpos` | Position, class position | Sim |
| `gain` | Positions gained or lost against the grid | Derived |
| `num` `name` `team` `car` `cls` `lic` `ir` `tag` `flag` | Identity | Roster |
| `gap` `int` `cgap` `cint` | Gap to leader, interval, same by class | Derived |
| `last` `best` `cur` | Lap times | Sim |
| `avg` | Average of the last 3, 5 or 10 laps, pit laps excluded | Derived |
| `delta` | Predicted difference to your pace | Derived |
| `rel` | Time to reach you on track | Derived |
| `lap` `sec` | Lap number, sector times | Sim |
| `st` | Running, pit, off, retired, no data | Sim |
| `stops` `pitlap` `pittime` `pitlane` | Pit stop count, lap, stationary time, lane time | Sim |
| `tage` | Laps since we last saw that car on pit road | **Estimate** |
| `comp` | Tyre compound | Sim, where iRacing publishes it |
| `inc` `fr` `p2p` | Incidents, fast repairs, push to pass | Sim |
| `irc` | Projected iRating change if the race ended now | Derived |
| `tsp` | Top speed on the last lap | **Estimate** |

Columns that cannot mean anything in the current session remove themselves.
Pit stops, tyre age and positions gained disappear in qualifying; the tower
switches to lap times on its own and back again when the race starts.

### The two estimates, and why they are labelled

Both are drawn in grey italic wherever they appear, and an estimate is never
allowed to look like a measurement.

**Tyre age** has no telemetry behind it. iRacing does not publish how old
anyone's tyres are, so every overlay that shows it counts laps since it last
saw that car come down pit road. Three things follow: a stop made before
PitWall started is invisible, a car that pitted for fuel only still reads as
fresh tyres, and a car outside your Max Cars streaming limit can pit unseen.
Until we have actually watched a car pit, the number is laps since we started
watching, and the column says so on hover.

**Top speed** is inferred from how quickly a car covers track distance,
because iRacing publishes speed for your own car and for nobody else. It is
roughly right on a green-flag lap and wrong in the pit lane.

**Tyre compound** is real telemetry, but iRacing only fills it in for cars that
have more than one compound. In a single-compound field every car reports the
same value, and the column shows a dash rather than inventing one.

Other cars' tyre wear, tyre temperature and fuel load are not available to any
overlay. Anything claiming to show them is showing you your own car's numbers.

### Profiles

A saved layout, stored by the engine and referenced from a widget URL:

    http://127.0.0.1:8099/broadcast/tower.html?profile=race-tower

Build one in the control panel under **Widgets**: a column picker you can drag
to reorder, a width per column, colour pickers for every car state, and a live
preview beside it. The profile can also carry a different layout for practice,
qualifying and the race, which swaps itself when the session changes with
nothing to reload.

Anything written directly into a URL beats the profile, always. A source you
have deliberately set up differently cannot be overruled by someone editing the
profile centrally.

### The scrolling ticker

    http://127.0.0.1:8099/broadcast/ticker.html      1920 x 64

The field crawling across the bottom of the frame, with the flag colour and
`LAP 14 / 40` pinned in a block on the left so they are never mid-scroll. The
whole bar tints to the flag: green, yellow, white, chequered and red. In a
timed session the block shows time remaining instead.

`speed=` sets the crawl in pixels per second, `side=right` moves the fixed
block, `h=` sets the bar height, `rows=` limits it to the top N, and `tint=0`
colours only the stripe. It is also a widget in the driver HUD, for pinning
along the top or bottom edge of a screen.

### Driver tags

Tag a driver in the roster with a short label and a colour and their row stands
out in every list. Tags live with the roster, so everyone running PitWall from
it sees the same ones. The HUD always highlights the driver using it, tagged or
not.
