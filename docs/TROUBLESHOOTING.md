# When something is not working

Open the control panel and look at the **Diagnostics** tab first. It checks the
real causes and names the one that applies to you. This page is the longer
explanation behind those checks.

---

## "Nothing appears in iRacing"

In order of how often it is the answer:

**1. iRacing is in exclusive fullscreen.**
Windows hands the entire screen to one program in exclusive fullscreen, so no
desktop overlay can draw on top of it — not PitWall, not any competitor that
does not inject code into the game, and injecting is the thing that carries
anti-cheat risk.

Fix: iRacing → Options → Graphics → turn **Full Screen** off and **Border** off,
then **restart iRacing**. Changing it without a restart often leaves a border
behind.

The OBS browser sources and the second-screen timing page work in any display
mode. This only affects the in-game HUD.

**2. You are in VR.**
A VR compositor never sees desktop windows, so a transparent window overlay
cannot appear in a headset. There is no setting that changes this. Put the
timing page on a phone or tablet instead, or use iRacing's own black boxes.

**3. `irsdkEnableMem=0`.**
Open `Documents\iRacing\app.ini` and find `irsdkEnableMem`. If it is `0`,
iRacing is publishing no live telemetry at all, to any tool. Set it to `1` and
restart iRacing. Diagnostics checks this for you.

**4. Windows display scaling is not 100%.**
At 125% or higher, overlay window positions drift relative to the sim. Settings
→ System → Display → Scale → 100%. If you cannot live at 100%, expect to nudge
HUD widgets after a resolution change.

**5. PitWall is not running, or is on a different port.**
The control panel tells you. Add `?debug=1` to any overlay URL to get a
connection dot in the corner: red means it cannot reach the server.

---

## "The sim stutters when overlays are on"

This is almost never the overlay's rendering cost. Two causes account for nearly
all of it:

**G-SYNC / VRR conflict.** The driver tries to sync the sim and the overlay app
at once, and frame delivery collapses. Your FPS counter can still read fine — it
is a frame-pacing problem, not a framerate problem.

Fix: NVIDIA Control Panel → set G-SYNC to **fullscreen only** globally. Then use
[NVIDIA Profile Inspector](https://github.com/Orbmu2k/nvidiaProfileInspector) to
force **fullscreen and windowed** for iRacing specifically.

**Cross-GPU desktop composition.** If Windows composites the desktop on an
integrated GPU while iRacing renders on the discrete one, every overlay frame is
copied across the bus. At triple-screen resolutions that is gigabytes per second.

Fix: disable the integrated GPU in Device Manager, or Settings → System →
Display → Graphics → set both iRacing and your browser/HUD to **High
performance**.

Diagnostics detects multiple adapters and warns you.

Third-party monitoring overlays (MSI Afterburner, RivaTuner, AMD Adrenaline)
stack on top of all this. Turn them off before blaming anything else.

---

## "Timing data looks wrong"

**Cars at the back are missing or jumping.** Set **Max Cars** to 63 on the
iRacing website (Account settings) and make sure your connection setting is
1 Mbit/s or faster. Below that, iRacing does not send you reliable position data
for the whole field, and no tool can invent it.

**Gaps show a dash under yellow.** That is deliberate. Under caution the field is
bunched behind a pace car and a gap computed from lap distance is meaningless.
PitWall shows nothing rather than a number you cannot act on. The same applies
to the delta column.

**A car's delta is blank.** Deltas are suppressed on in-laps, out-laps, and any
lap that touched pit road, because a car sitting in its box accumulates lap time
without accumulating distance.

**Sector times are missing on the first lap.** Sector times need a start/finish
crossing to anchor to, so the first lap after joining has no sector 1.

---

## "Camera and replay control does nothing"

**Elevation mismatch.** If iRacing runs as administrator and PitWall does not,
Windows blocks the window messages entirely and silently. Run both the same way
— ideally, neither elevated.

**You are not spectating.** Pit commands only work when you are in the car.
Camera and replay control work while spectating or in a replay, which is the
case that matters for a broadcast.

**Not on Windows.** Sim control is a Windows window message. The control panel
greys the whole section out and says so.

---

## "Another device cannot open the timing page"

1. Use the address from the control panel's Status tab, not `127.0.0.1` —
   that means "this machine" and will not work from a phone.
2. Allow Python through the Windows firewall on **private** networks. Windows
   usually asks the first time; if you clicked "Cancel", find Python in
   Windows Defender Firewall → Allow an app.
3. Both devices must be on the same network. Guest Wi-Fi networks usually block
   device-to-device traffic entirely.
4. If you started PitWall with `--host 127.0.0.1`, it is deliberately local-only.

---

## "The track map is empty"

It builds itself from GPS telemetry the first time anyone drives or spectates a
clean lap at that track. Until then, the overlay shows a small progress note and
nothing else. Once built it is cached in `data/tracks/` and is instant forever
after.

It will not build while sitting in the pits, driving under 5 m/s, or on pit road
— those samples would put the pit lane in the middle of the circuit.

---

## "A driver's profile is not showing"

Profiles join to live cars by **iRacing customer ID**. If it is not appearing:

1. Check the ID on the control panel's Roster tab matches their real customer ID
   (iRacing website → Account → My Account → Customer ID). This is the field
   people get wrong.
2. PitWall also falls back to matching on car number + surname, and then exact
   name — but only the customer ID is reliable in team races.
3. If the roster lives in GitHub, hit **Pull** on the Roster tab. Submissions
   made on another machine are not on this one until you pull.

---

## Starting fresh

- Delete `data/roster.json` to clear all driver profiles.
- Delete `data/tracks/` to force track maps to rebuild.
- Delete `config/pitwall.json` to reset league branding and settings.
- Nothing is installed system-wide. Deleting the PitWall folder removes it
  completely.
