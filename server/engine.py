"""
PitWall - timing engine.

Turns a stream of raw iRacing ticks into the state a broadcast timing tower,
a second-screen timing page and an in-game HUD all need.

The engine is deliberately built entirely on the CarIdx* arrays plus the
session string.  That means it behaves identically whether you are driving,
spectating, or scrubbing a replay - which is exactly what a league broadcast
needs, because the broadcaster is never in a car.

Things iRacing does NOT give you, which this computes:

  * Live current-lap time for every car (only the player gets that natively).
  * Sector times (iRacing gives you sector *boundaries*, not times).
  * Intervals between cars, wrap-corrected and lap-aware.
  * Predicted lap times / live delta.
  * Fuel per lap and laps remaining.
  * Pit stop counts and stationary time.
  * Stint tracking and driver-change detection for team races.
"""

from __future__ import annotations

import math
import time
from typing import Any, Dict, List, Optional

from . import enums
from .sessionyaml import dig, number

# Number of bins in the track-position -> elapsed-lap-time model.
SPLINE_BINS = 400

# A lap is only fed into the reference model if it is within this band of the
# car's known best. Keeps in-laps, out-laps and off-track moments out.
CLEAN_LAP_BAND = (0.94, 1.12)


# ---------------------------------------------------------------------------


class LapSpline:
    """Maps lap distance (0..1) to elapsed seconds into the lap.

    Built from observed clean laps.  This is what makes gaps stay honest
    through corners: a car 50% around the lap is not 50% of a lap time in if
    half the lap is Kemmel straight.

    Falls back to a linear model until it has seen a lap.
    """

    __slots__ = ("bins", "have", "laptime", "samples")

    def __init__(self) -> None:
        self.bins: List[float] = [0.0] * SPLINE_BINS
        self.have = False
        self.laptime = 0.0
        self.samples = 0

    def ingest(self, marks: List[Optional[float]], lap_time: float) -> None:
        """marks[i] = SessionTime at which bin i was crossed, for one lap."""
        if lap_time <= 0:
            return
        start = marks[0]
        if start is None:
            return
        new: List[float] = []
        last = 0.0
        for i in range(SPLINE_BINS):
            m = marks[i]
            if m is None:
                new.append(last)
            else:
                last = max(0.0, m - start)
                new.append(last)
        if not self.have:
            self.bins = new
            self.laptime = lap_time
            self.have = True
            self.samples = 1
            return
        # Exponential blend, weighted toward stability.
        w = 0.25 if self.samples < 8 else 0.12
        for i in range(SPLINE_BINS):
            self.bins[i] = self.bins[i] * (1 - w) + new[i] * w
        self.laptime = self.laptime * (1 - w) + lap_time * w
        self.samples += 1

    def time_at(self, pct: float, ref_lap: float) -> float:
        if not self.have or self.laptime <= 0:
            return max(0.0, min(1.0, pct)) * ref_lap
        p = max(0.0, min(0.999999, pct))
        fi = p * (SPLINE_BINS - 1)
        i = int(fi)
        frac = fi - i
        a = self.bins[i]
        b = self.bins[min(SPLINE_BINS - 1, i + 1)]
        t = a + (b - a) * frac
        # Rescale onto the reference lap time we are actually using.
        return t * (ref_lap / self.laptime) if self.laptime > 0 else t


# ---------------------------------------------------------------------------


class CarState:
    """Everything PitWall tracks about one car slot across a session."""

    def __init__(self, idx: int) -> None:
        self.idx = idx
        self.reset_session()

    def reset_session(self) -> None:
        self.lap_start: Optional[float] = None
        self.last_lap_seen = -1
        self.last_pct = 0.0
        self.current_lap_time = 0.0
        self.last_time: Optional[float] = None
        self.best_time: Optional[float] = None
        self.best_lap_num = 0
        self.sector_marks: List[Optional[float]] = []
        self.sectors: List[Optional[float]] = []
        self.best_sectors: List[Optional[float]] = []
        self.last_sectors: List[Optional[float]] = []
        self.spline_marks: List[Optional[float]] = [None] * SPLINE_BINS
        self.last_bin = -1
        self.pit_stops = 0
        self.in_pit = False
        self.pit_enter_time: Optional[float] = None
        self.stall_enter_time: Optional[float] = None
        self.last_pit_duration: Optional[float] = None
        self.last_pit_lap = 0
        self.pit_road_time = 0.0
        self.clean_laps: List[float] = []
        # Every non-pit lap, unfiltered. clean_laps is band-filtered around the
        # car's best so the lap-distance spline is not poisoned by a lap spent
        # in traffic; average pace wants exactly those laps back, because a
        # driver stuck behind someone is genuinely lapping that slowly.
        self.recent_laps: List[float] = []
        # Highest speed observed on the lap now running, and on the one just
        # finished. Estimated, see Engine._sample_speed.
        self.lap_top_speed = 0.0
        self.top_speed = 0.0
        self.last_speed_pct: Optional[float] = None
        self.last_speed_t: Optional[float] = None
        # True once we have actually watched this car come down pit road, so
        # tyre age can say whether it is a measurement or an assumption.
        self.pit_seen = False
        self.stint_start_lap = 0
        self.driver_id: Optional[int] = None
        self.driver_name: str = ""
        self.stint_laps = 0
        self.laps_led = 0
        self.was_leader = False
        self.gap_history: List[float] = []
        self.out = False
        # iRacing reports "not in world" both for a car that has left the
        # session and for one it simply is not streaming to this client, which
        # it does constantly once a field is larger than the Max Cars setting.
        # These two let the engine tell those apart instead of calling every
        # car it cannot see retired.
        self.last_in_world: Optional[float] = None
        self.last_scored_lap = -1
        self.last_scored_at: Optional[float] = None

    # -- helpers ---------------------------------------------------------

    def median_clean(self) -> Optional[float]:
        if not self.clean_laps:
            return None
        s = sorted(self.clean_laps[-8:])
        n = len(s)
        return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0

    def reference_lap(self, class_est: float) -> float:
        m = self.median_clean()
        if m and m > 0:
            return m
        if self.best_time and self.best_time > 0:
            return self.best_time
        if self.last_time and self.last_time > 0:
            return self.last_time
        return class_est if class_est > 0 else 90.0


# ---------------------------------------------------------------------------



def _text(v: Any) -> str:
    """
    Force a session-string value to displayable text.

    iRacing's session string carries no types. A driver whose name is "747", a
    team called "911", a car number of 88 used as initials: all of these arrive
    as integers, and calling .strip() on an integer takes the whole app down at
    the precise moment it connects to the sim. Everything shown as text goes
    through here, so a numeric name is displayed rather than fatal.
    """
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    return str(v).strip()


class Engine:
    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or {}
        self.cars: Dict[int, CarState] = {}
        self.splines: Dict[int, LapSpline] = {}   # keyed by class id
        self.session_info: Dict[str, Any] = {}
        self.session_serial = 0
        self.meta_serial = 0
        self.sectors_pct: List[float] = [0.0]
        self.drivers: Dict[int, Dict[str, Any]] = {}
        self.class_est: Dict[int, float] = {}
        self.current_session_num = -1
        self.session_unique_id = -1
        self.last_session_time = 0.0
        self.fastest_overall: Dict[str, Any] = {}
        self.fastest_by_class: Dict[int, Dict[str, Any]] = {}
        self.input_ring: List[Dict[str, float]] = []
        self.player_fuel_laps: List[float] = []
        self._last_fuel: Optional[float] = None
        self._last_fuel_lap = -1
        self.pace_car_idx = -1
        self.player_idx = -1
        self.track: Dict[str, Any] = {}
        self.weather: Dict[str, Any] = {}
        self.roster_lookup = None      # callable(driver_dict) -> profile or None
        self.events: List[Dict[str, Any]] = []
        self.ready = False
        self.track_m = 0.0             # track length in metres, for speed
        self.grid: Dict[int, int] = {}  # car index -> starting position
        self.grid_source = ""          # "qualifying" | "green" | ""
        self._ir_change: Dict[int, float] = {}
        self._ir_change_at = 0.0

    # -- session info ----------------------------------------------------

    def apply_session_info(self, info: Dict[str, Any]) -> None:
        """Called when iRacing's session string changes (about 1 Hz at most)."""
        if not info:
            return
        self.session_info = info
        self.meta_serial += 1

        wi = info.get("WeekendInfo") or {}
        opts = wi.get("WeekendOptions") or {}
        self.track = {
            "name": _text(wi.get("TrackDisplayName")) or _text(wi.get("TrackName")),
            "shortName": _text(wi.get("TrackDisplayShortName")),
            "config": _text(wi.get("TrackConfigName")),
            "id": wi.get("TrackID"),
            "lengthKm": number(wi.get("TrackLength")),
            "city": _text(wi.get("TrackCity")),
            "country": _text(wi.get("TrackCountry")),
            "turns": wi.get("TrackNumTurns"),
            "pitSpeedKph": number(wi.get("TrackPitSpeedLimit")),
            "northOffset": number(wi.get("TrackNorthOffset")),
            "type": wi.get("TrackType") or "",
            "direction": wi.get("TrackDirection") or "",
        }
        self.track_m = float(number(wi.get("TrackLength"))) * 1000.0

        self.event = {
            "series": wi.get("SeriesID"),
            "season": wi.get("SeasonID"),
            "subSessionId": wi.get("SubSessionID"),
            "leagueId": wi.get("LeagueID"),
            "official": wi.get("Official"),
            "teamRacing": wi.get("TeamRacing"),
            "eventType": wi.get("EventType") or "",
            "simMode": wi.get("SimMode") or "full",
            "numStarters": opts.get("NumStarters"),
            "incidentLimit": opts.get("IncidentLimit"),
            "fastRepairs": opts.get("FastRepairsLimit"),
            "standingStart": opts.get("StandingStart"),
            "date": opts.get("Date"),
            "greenWhiteCheckered": opts.get("GreenWhiteCheckeredLimit"),
        }

        # --- starting grid ------------------------------------------------
        # Positions gained is only meaningful against where a driver started.
        # Qualifying results are the truth when they exist; when they do not,
        # because the session was a heat, a rolling start from a previous race
        # or an offline test, update() falls back to a snapshot taken the
        # moment the session goes green.
        if not self.grid or self.grid_source != "qualifying":
            qual = dig(info, "QualifyResultsInfo", "Results", default=[]) or []
            got: Dict[int, int] = {}
            for r in qual:
                if not isinstance(r, dict):
                    continue
                ci, pos = r.get("CarIdx"), r.get("Position")
                if ci is None or pos is None:
                    continue
                try:
                    got[int(ci)] = int(pos) + 1
                except (TypeError, ValueError):
                    continue
            if got:
                self.grid = got
                self.grid_source = "qualifying"

        sectors = dig(info, "SplitTimeInfo", "Sectors", default=[]) or []
        pcts = []
        for s in sectors:
            if isinstance(s, dict) and s.get("SectorStartPct") is not None:
                pcts.append(float(number(s.get("SectorStartPct"))))
        self.sectors_pct = pcts if pcts else [0.0]

        di = info.get("DriverInfo") or {}
        self.pace_car_idx = di.get("PaceCarIdx", -1)
        self.player_idx = di.get("DriverCarIdx", -1)
        self.pit_track_pct = number(di.get("DriverPitTrkPct"), 0.0)

        drivers: Dict[int, Dict[str, Any]] = {}
        for d in di.get("Drivers") or []:
            if not isinstance(d, dict):
                continue
            idx = d.get("CarIdx")
            if idx is None:
                continue
            idx = int(idx)
            cls = d.get("CarClassID")
            est = number(d.get("CarClassEstLapTime"))
            if cls is not None and est > 0:
                self.class_est[int(cls)] = est
            rec = {
                "idx": idx,
                "userId": d.get("UserID"),
                "name": _text(d.get("UserName")),
                "abbrev": _text(d.get("AbbrevName")),
                "initials": _text(d.get("Initials")),
                "team": _text(d.get("TeamName")),
                "teamId": d.get("TeamID"),
                "num": _text(d.get("CarNumber")),
                "numRaw": d.get("CarNumberRaw"),
                "car": _text(d.get("CarScreenName")),
                "carShort": _text(d.get("CarScreenNameShort")),
                "carPath": _text(d.get("CarPath")),
                "carId": d.get("CarID"),
                "classId": int(cls) if cls is not None else 0,
                "classShort": _text(d.get("CarClassShortName")),
                "classColor": enums.hex_colour(d.get("CarClassColor")),
                "classRelSpeed": d.get("CarClassRelSpeed"),
                "irating": d.get("IRating") or 0,
                "license": _text(d.get("LicString")),
                "licColor": enums.hex_colour(d.get("LicColor"), "#666"),
                "club": _text(d.get("ClubName")),
                "division": _text(d.get("DivisionName")),
                "incidents": d.get("CurDriverIncidentCount") or 0,
                "teamIncidents": d.get("TeamIncidentCount") or 0,
                "isPace": bool(d.get("CarIsPaceCar")),
                "isAI": bool(d.get("CarIsAI")),
                "isSpectator": bool(d.get("IsSpectator")),
                "sponsor1": d.get("CarSponsor_1"),
                "sponsor2": d.get("CarSponsor_2"),
            }
            if self.roster_lookup:
                try:
                    prof = self.roster_lookup(rec)
                except Exception:
                    prof = None
                if prof:
                    rec["profile"] = prof
            drivers[idx] = rec

            # Detect a driver change (team races): new UserID in the same slot.
            car = self.cars.get(idx)
            if car is not None:
                uid = rec.get("userId")
                if car.driver_id is not None and uid is not None and uid != car.driver_id:
                    car.stint_start_lap = 0
                    self.push_event("driverChange", idx=idx, name=rec["name"], team=rec["team"])
                car.driver_id = uid
                car.driver_name = rec["name"]

        self.drivers = drivers
        self.ready = True

    def push_event(self, kind: str, **data: Any) -> None:
        ev = {"kind": kind, "t": time.time()}
        ev.update(data)
        self.events.append(ev)
        if len(self.events) > 200:
            del self.events[:-200]

    # -- per-tick --------------------------------------------------------

    def update(self, v: Dict[str, Any]) -> None:
        """Ingest one coherent telemetry tick. `v` is name -> value."""
        st = float(v.get("SessionTime") or 0.0)
        replay = bool(v.get("IsReplayPlaying"))
        if replay:
            rt = v.get("ReplaySessionTime")
            if rt is not None:
                st = float(rt)

        # Session change or replay scrub: any state machine that assumes
        # monotonic time has to be told to let go.
        suid = v.get("SessionUniqueID")
        snum = v.get("SessionNum")
        if suid is not None and suid != self.session_unique_id:
            self.session_unique_id = suid
            self._reset_all()
        if snum is not None and snum != self.current_session_num:
            self.current_session_num = snum
            self._reset_all()
        if st < self.last_session_time - 1.0:
            self._reset_all()
        self.last_session_time = st

        n = len(v.get("CarIdxLapDistPct") or [])
        if n == 0:
            return

        pcts = v.get("CarIdxLapDistPct") or []
        laps = v.get("CarIdxLap") or []
        surf = v.get("CarIdxTrackSurface") or []
        onpit = v.get("CarIdxOnPitRoad") or []
        lastt = v.get("CarIdxLastLapTime") or []
        bestt = v.get("CarIdxBestLapTime") or []
        bestn = v.get("CarIdxBestLapNum") or []
        lapsc = v.get("CarIdxLapCompleted") or []
        posn = v.get("CarIdxPosition") or []

        for i in range(n):
            if i >= len(pcts):
                break
            car = self.cars.get(i)
            if car is None:
                car = self.cars[i] = CarState(i)

            pct = pcts[i] if i < len(pcts) else -1.0
            lap = laps[i] if i < len(laps) else 0
            surface = surf[i] if i < len(surf) else -1
            in_world = surface != enums.TRK_NOT_IN_WORLD and pct is not None and pct >= 0

            car.out = not in_world
            if not in_world:
                car.last_pct = pct if pct is not None else 0.0
                # Scoring keeps ticking over for cars the sim is not streaming,
                # so a moving lap count is proof the car is still racing.
                scored = lapsc[i] if i < len(lapsc) else 0
                if scored is not None and scored > car.last_scored_lap:
                    car.last_scored_lap = scored
                    car.last_scored_at = st
                continue
            car.last_in_world = st
            scored_now = lapsc[i] if i < len(lapsc) else 0
            if scored_now is not None and scored_now > car.last_scored_lap:
                car.last_scored_lap = scored_now
                car.last_scored_at = st

            # --- pit road -------------------------------------------------
            pit = bool(onpit[i]) if i < len(onpit) else False
            if pit and not car.in_pit:
                car.in_pit = True
                car.pit_seen = True
                car.pit_enter_time = st
                car.last_pit_lap = lap
            elif not pit and car.in_pit:
                car.in_pit = False
                if car.pit_enter_time is not None:
                    car.pit_road_time = st - car.pit_enter_time
                car.pit_enter_time = None
            if surface == enums.TRK_IN_PIT_STALL:
                if car.stall_enter_time is None:
                    car.stall_enter_time = st
            else:
                if car.stall_enter_time is not None:
                    dur = st - car.stall_enter_time
                    if dur > 1.0:
                        car.pit_stops += 1
                        car.last_pit_duration = dur
                        car.stint_start_lap = lap
                        self.push_event("pitStop", idx=i, duration=round(dur, 2), lap=lap)
                    car.stall_enter_time = None

            self._sample_speed(car, pct, st, pit)

            # --- lap rollover --------------------------------------------
            crossed = False
            if car.last_lap_seen < 0:
                car.last_lap_seen = lap
                car.lap_start = st
                car.last_bin = int(max(0.0, min(0.999999, pct)) * SPLINE_BINS)
            elif lap > car.last_lap_seen:
                crossed = True
            elif lap < car.last_lap_seen:
                # Reset / tow / replay scrub.
                car.last_lap_seen = lap
                car.lap_start = st
                car.spline_marks = [None] * SPLINE_BINS
                car.sector_marks = []

            if crossed:
                self._close_lap(car, st, lap, lastt, bestt, bestn, i)

            # --- current lap time ----------------------------------------
            if car.lap_start is not None:
                car.current_lap_time = max(0.0, st - car.lap_start)

            # --- spline + sector marks ------------------------------------
            p = max(0.0, min(0.999999, float(pct)))
            b = int(p * SPLINE_BINS)
            if b != car.last_bin:
                # Fill every bin we passed, so a 60 Hz sample that jumps two
                # bins on a straight does not leave holes.
                if car.last_bin >= 0 and b > car.last_bin:
                    for k in range(car.last_bin + 1, min(b + 1, SPLINE_BINS)):
                        car.spline_marks[k] = st
                elif b < car.last_bin:
                    car.spline_marks[b] = st
                else:
                    car.spline_marks[b] = st
                car.last_bin = b

            self._mark_sectors(car, p, st)
            car.last_pct = p

        self._capture_grid(v)
        self._update_fastest(v)
        self._collect_inputs(v)

    # -- lap close -------------------------------------------------------

    def _close_lap(self, car, st, lap, lastt, bestt, bestn, i) -> None:
        lap_time = None
        # iRacing's own last lap time is authoritative once it lands, but it
        # can lag the crossing by a tick or two, so start from our own latch.
        if car.lap_start is not None:
            lap_time = st - car.lap_start
        reported = enums.clean_time(lastt[i] if i < len(lastt) else None)
        if reported and lap_time and abs(reported - lap_time) < 3.0:
            lap_time = reported
        elif reported and lap_time is None:
            lap_time = reported

        was_pit_lap = car.in_pit or car.last_pit_lap == car.last_lap_seen

        if lap_time and lap_time > 5.0:
            car.last_time = lap_time
            car.last_sectors = list(car.sectors)
            if not was_pit_lap:
                car.recent_laps.append(lap_time)
                if len(car.recent_laps) > 20:
                    del car.recent_laps[:-20]
            best_ref = car.best_time
            if best_ref is None or lap_time < best_ref:
                if not was_pit_lap:
                    car.best_time = lap_time
                    car.best_lap_num = car.last_lap_seen
            if not was_pit_lap:
                lo, hi = CLEAN_LAP_BAND
                ref = car.best_time or lap_time
                if ref * lo <= lap_time <= ref * hi:
                    car.clean_laps.append(lap_time)
                    if len(car.clean_laps) > 20:
                        del car.clean_laps[:-20]
                    cls = (self.drivers.get(i) or {}).get("classId", 0)
                    sp = self.splines.get(cls)
                    if sp is None:
                        sp = self.splines[cls] = LapSpline()
                    sp.ingest(car.spline_marks, lap_time)
                # Sector personal bests
                for si, sv in enumerate(car.sectors):
                    if sv is None:
                        continue
                    while len(car.best_sectors) <= si:
                        car.best_sectors.append(None)
                    if car.best_sectors[si] is None or sv < car.best_sectors[si]:
                        car.best_sectors[si] = sv

        # iRacing's own best is a good cross-check.
        rb = enums.clean_time(bestt[i] if i < len(bestt) else None)
        if rb and (car.best_time is None or rb < car.best_time - 0.001):
            car.best_time = rb
            if i < len(bestn):
                car.best_lap_num = bestn[i] or 0

        # Close the final sector: it runs from the last sector boundary to the
        # start/finish line, so it can only be known here.
        ns = len(self.sectors_pct)
        if ns > 1 and len(car.sector_marks) == ns and car.sector_marks[ns - 1] is not None:
            car.sectors[ns - 1] = st - car.sector_marks[ns - 1]
            car.last_sectors = list(car.sectors)
            pb = car.best_sectors[ns - 1] if len(car.best_sectors) > ns - 1 else None
            while len(car.best_sectors) < ns:
                car.best_sectors.append(None)
            v = car.sectors[ns - 1]
            if not was_pit_lap and v and (pb is None or v < pb):
                car.best_sectors[ns - 1] = v

        car.last_lap_seen = lap
        car.lap_start = st
        car.stint_laps = max(0, lap - car.stint_start_lap)
        # Top speed belongs to a completed lap, so it rolls over here rather
        # than climbing all race.
        if car.lap_top_speed > 0.0:
            car.top_speed = car.lap_top_speed
        car.lap_top_speed = 0.0
        car.spline_marks = [None] * SPLINE_BINS
        # The lap-distance model is anchored on bin 0; without this the very
        # first mark is never written and the spline never builds.
        car.spline_marks[0] = st
        car.last_bin = 0
        car.sectors = [None] * max(1, len(self.sectors_pct))
        car.sector_marks = [None] * max(1, len(self.sectors_pct))
        car.sector_marks[0] = st

    # -- derived figures ---------------------------------------------------

    def _sample_speed(self, car, pct: float, st: float, pit: bool) -> None:
        """
        Estimate a car's speed from how fast its lap distance changes.

        iRacing publishes Speed for your own car and for nobody else, so this
        is the only route to a speed figure for the field. It is distance over
        time and nothing cleverer: a car that is teleported, towed or reset
        produces a nonsense sample, which is why the jump is bounded, and a car
        in the pit lane is skipped entirely rather than reporting sixty.

        Everything built on this is labelled an estimate in the UI.
        """
        if self.track_m <= 0.0 or pct is None or pct < 0.0:
            car.last_speed_pct = pct
            car.last_speed_t = st
            return
        if not pit and car.last_speed_pct is not None and car.last_speed_t is not None:
            dt = st - car.last_speed_t
            if 0.02 <= dt <= 1.0:
                dp = pct - car.last_speed_pct
                if dp < -0.5:
                    dp += 1.0            # crossed the start/finish line
                if 0.0 < dp < 0.25:      # anything larger is a teleport
                    kph = (dp * self.track_m / dt) * 3.6
                    if 0.0 < kph < 500.0 and kph > car.lap_top_speed:
                        car.lap_top_speed = kph
        car.last_speed_pct = pct
        car.last_speed_t = st

    def _capture_grid(self, v: Dict[str, Any]) -> None:
        """
        Snapshot the order at the green flag, when qualifying results are not
        available. Called every tick and does nothing once it has a grid.
        """
        if self.grid:
            return
        if self._mode_of(self._session_type(v)) != "race":
            return
        state = int(v.get("SessionState") or 0)
        if state < 4:                    # 4 = Racing
            return
        posn = v.get("CarIdxPosition") or []
        got: Dict[int, int] = {}
        for i, p in enumerate(posn):
            if i == self.pace_car_idx:
                continue
            try:
                p = int(p)
            except (TypeError, ValueError):
                continue
            if p > 0:
                got[i] = p
        if len(got) >= 2:
            self.grid = got
            self.grid_source = "green"

    @staticmethod
    def _avg_of(times: List[float], n: int) -> Optional[float]:
        """Mean of the last n laps, or None if there are not n of them yet."""
        if len(times) < n:
            return None
        window = times[-n:]
        return sum(window) / float(len(window))

    # iRacing's rating model. B is the published scale constant; the expected
    # score for a driver is the sum of the probabilities that each rival
    # finishes behind them, and the change is how far the result beat that.
    _IR_B = 1600.0 / math.log(2.0)

    def _irating_changes(self, entries: List[Dict[str, Any]]) -> Dict[int, float]:
        """
        Projected iRating change if the session ended right now.

        This is the community-reconstructed formula, which matches iRacing
        closely for a normal race and diverges for the cases iRacing treats
        specially: drivers who did not take the start, disconnections, and
        sessions where the field changes size part way through. It is a
        projection of an unfinished race either way, so it is shown as a
        guide rather than a promise.
        """
        field = []
        for e in entries:
            d = self.drivers.get(e["i"]) or {}
            ir = d.get("irating") or 0
            if d.get("isPace") or d.get("isSpectator") or ir <= 0:
                continue
            field.append((e["i"], float(ir), e["order"]))
        n = len(field)
        if n < 2:
            return {}

        # Precompute exp(-IR/B) once per driver: the pairwise loop below reads
        # it n times and this is the only expensive part.
        expo = {i: math.exp(-ir / self._IR_B) for i, ir, _ in field}
        out: Dict[int, float] = {}
        for i, ir_i, pos_i in field:
            ei = expo[i]
            expected = 0.0
            for j, ir_j, _ in field:
                if j == i:
                    continue
                ej = expo[j]
                denom = (1.0 - ei) * ej + (1.0 - ej) * ei
                if denom <= 0.0:
                    continue
                # Probability that i finishes ahead of j.
                expected += ((1.0 - ei) * ej) / denom
            beaten = float(n - pos_i)
            out[i] = (beaten - expected) * (200.0 / float(n))
        return out

    def _mark_sectors(self, car, pct: float, st: float) -> None:
        ns = len(self.sectors_pct)
        if ns <= 1:
            return
        if len(car.sector_marks) != ns:
            car.sector_marks = [None] * ns
            car.sectors = [None] * ns
        for si in range(ns):
            start = self.sectors_pct[si]
            if car.last_pct < start <= pct and car.sector_marks[si] is None:
                car.sector_marks[si] = st
                if si > 0 and car.sector_marks[si - 1] is not None:
                    car.sectors[si - 1] = st - car.sector_marks[si - 1]

    # -- fastest laps ----------------------------------------------------

    def _update_fastest(self, v: Dict[str, Any]) -> None:
        best_t = None
        best_i = None
        by_class: Dict[int, Any] = {}
        for i, car in self.cars.items():
            d = self.drivers.get(i)
            if not d or d.get("isPace") or d.get("isSpectator"):
                continue
            if car.best_time is None:
                continue
            if best_t is None or car.best_time < best_t:
                best_t, best_i = car.best_time, i
            cls = d.get("classId", 0)
            cur = by_class.get(cls)
            if cur is None or car.best_time < cur[0]:
                by_class[cls] = (car.best_time, i)
        if best_i is not None:
            prev = self.fastest_overall.get("idx")
            self.fastest_overall = {
                "idx": best_i,
                "time": best_t,
                "lap": self.cars[best_i].best_lap_num,
            }
            if prev != best_i:
                d = self.drivers.get(best_i) or {}
                self.push_event(
                    "fastestLap", idx=best_i, name=d.get("name", ""), time=round(best_t or 0, 3)
                )
        self.fastest_by_class = {
            c: {"idx": i, "time": t} for c, (t, i) in by_class.items()
        }

    def _collect_inputs(self, v: Dict[str, Any]) -> None:
        sample = {
            "t": round(float(v.get("SessionTime") or 0.0), 3),
            "thr": _f(v.get("Throttle")),
            "brk": _f(v.get("Brake")),
            "clu": _f(v.get("Clutch")),
            "str": _f(v.get("SteeringWheelAngle")),
            "spd": _f(v.get("Speed")),
            "rpm": _f(v.get("RPM")),
            "gear": int(v.get("Gear") or 0),
            "abs": 1 if v.get("BrakeABSactive") else 0,
        }
        self.input_ring.append(sample)
        if len(self.input_ring) > 600:
            del self.input_ring[:-600]

    def drain_inputs(self) -> List[Dict[str, float]]:
        out = self.input_ring
        self.input_ring = []
        return out

    def drain_events(self) -> List[Dict[str, Any]]:
        out = self.events
        self.events = []
        return out

    def _reset_all(self) -> None:
        for car in self.cars.values():
            car.reset_session()
        self.fastest_overall = {}
        self.fastest_by_class = {}
        self.player_fuel_laps = []
        self._last_fuel = None
        self._last_fuel_lap = -1
        self.grid = {}
        self.grid_source = ""
        self._ir_change = {}
        self._ir_change_at = 0.0

    # -- building the outgoing state -------------------------------------


    # -- car state --------------------------------------------------------

    # How long a car has to be both unseen and unscored before the engine is
    # willing to call it retired. Scoring only updates as a car crosses the
    # line, so this has to clear a whole lap with room to spare or a car on a
    # long circuit is declared retired between every timing loop. It scales
    # with the class lap time for that reason, and this floor only applies
    # when no lap time is known yet.
    OUT_AFTER = 240.0

    def _car_state(self, e: Dict[str, Any], car: "CarState", now_s: float) -> str:
        """
        run / pit / stall / off / nodata / out.

        The distinction that matters here is "out" against "nodata". iRacing
        reports a car as not in the world for two completely different reasons:
        the car has left the session, or the sim simply is not streaming that
        car to this client. The second happens constantly once a field is
        bigger than the Max Cars setting allows, and the old code called every
        such car OUT. On a forty car grid that put OUT against most of the
        field while they were racing perfectly well.

        Scoring is the tell. iRacing keeps counting laps for every car in the
        session whether or not it is streaming their position, so a lap count
        that is still moving means the car is still racing and we merely cannot
        see it. Only a car that is neither visible nor being scored, for longer
        than a couple of its own laps, is treated as genuinely gone.

        The bias is deliberate. A car wrongly shown as retired while it is
        racing is a visible error on the broadcast; a car that retired and
        shows a dash for a few minutes before the board admits it is not.
        """
        if e["inWorld"]:
            if e["surface"] == enums.TRK_IN_PIT_STALL:
                return "stall"
            if e["onPit"]:
                return "pit"
            if e["surface"] == enums.TRK_OFF_TRACK:
                return "off"
            return "run"

        # Never seen at all and never scored: it is not in this session.
        if car.last_in_world is None and car.last_scored_at is None:
            return "out"

        recent = max(
            car.last_in_world if car.last_in_world is not None else -1e9,
            car.last_scored_at if car.last_scored_at is not None else -1e9,
        )
        ref = car.reference_lap(self.class_est.get(e["cls"], 0.0)) or 0.0
        limit = max(self.OUT_AFTER, ref * 2.2)
        if now_s - recent < limit:
            return "nodata"
        return "out"

    def build_state(self, v: Dict[str, Any]) -> Dict[str, Any]:
        """Produce the per-tick payload sent to every overlay."""
        flags_raw = int(v.get("SessionFlags") or 0)
        pcts = v.get("CarIdxLapDistPct") or []
        laps = v.get("CarIdxLap") or []
        lapsc = v.get("CarIdxLapCompleted") or []
        pos = v.get("CarIdxPosition") or []
        cpos = v.get("CarIdxClassPosition") or []
        surf = v.get("CarIdxTrackSurface") or []
        onpit = v.get("CarIdxOnPitRoad") or []
        est = v.get("CarIdxEstTime") or []
        f2 = v.get("CarIdxF2Time") or []
        lastt = v.get("CarIdxLastLapTime") or []
        bestt = v.get("CarIdxBestLapTime") or []
        fastrep = v.get("CarIdxFastRepairsUsed") or []
        paceline = v.get("CarIdxPaceLine") or []
        pacerow = v.get("CarIdxPaceRow") or []
        tyre = v.get("CarIdxTireCompound") or []
        p2ps = v.get("CarIdxP2P_Status") or []
        p2pc = v.get("CarIdxP2P_Count") or []

        under_caution = bool(flags_raw & enums.CAUTION_MASK)
        mode = self._mode_of(self._session_type(v))
        timed = mode == "timed"

        entries: List[Dict[str, Any]] = []
        n = len(pcts)
        for i in range(n):
            d = self.drivers.get(i)
            if not d:
                continue
            if d.get("isPace") or d.get("isSpectator"):
                continue
            surface = surf[i] if i < len(surf) else -1
            pct = pcts[i] if i < len(pcts) else -1.0
            in_world = surface != enums.TRK_NOT_IN_WORLD and pct is not None and pct >= 0
            car = self.cars.get(i) or CarState(i)
            lap = laps[i] if i < len(laps) else 0
            cls = d.get("classId", 0)

            track_pos = (lap or 0) + max(0.0, float(pct or 0.0)) if in_world else -1e9
            entries.append(
                {
                    "i": i,
                    "d": d,
                    "car": car,
                    "trackPos": track_pos,
                    "pct": float(pct or 0.0),
                    "lap": lap,
                    "lapsComplete": lapsc[i] if i < len(lapsc) else 0,
                    "pos": pos[i] if i < len(pos) else 0,
                    "classPos": cpos[i] if i < len(cpos) else 0,
                    "surface": surface,
                    "inWorld": in_world,
                    "onPit": bool(onpit[i]) if i < len(onpit) else False,
                    "est": float(est[i]) if i < len(est) and est[i] is not None else None,
                    "f2": float(f2[i]) if i < len(f2) and f2[i] is not None else None,
                    "cls": cls,
                    "fastRepairs": fastrep[i] if i < len(fastrep) else 0,
                    "paceLine": paceline[i] if i < len(paceline) else -1,
                    "paceRow": pacerow[i] if i < len(pacerow) else -1,
                    "tyre": tyre[i] if i < len(tyre) else -1,
                    "p2pOn": bool(p2ps[i]) if i < len(p2ps) else False,
                    "p2pLeft": p2pc[i] if i < len(p2pc) else -1,
                }
            )

        # --- running order ---------------------------------------------
        # iRacing's own CarIdxPosition only moves at the timing line. For a
        # broadcast tower we want the official order; for a relative we want
        # smooth track position. Compute both.
        # iRacing fills in CarIdxPosition itself when it has an opinion, and
        # that opinion is authoritative in every session type. It only leaves
        # the field at zero in sessions it is not scoring - an open practice,
        # most commonly - and that is the case we have to score ourselves.
        has_official = any(e["pos"] and e["pos"] > 0 for e in entries)

        if timed and not has_official:
            # Quickest lap first. Cars yet to set one go to the back, ordered
            # among themselves by how far round they are, so a board at the
            # start of a session fills from the top as laps come in rather
            # than shuffling on every tick.
            def timed_key(e):
                best = e["car"].best_time
                if best and best > 0:
                    return (0, best, 0.0)
                return (1, 0.0, -e["trackPos"])
            official = sorted(entries, key=timed_key)
        else:
            official = sorted(
                entries,
                key=lambda e: (e["pos"] if e["pos"] and e["pos"] > 0 else 9999, -e["trackPos"]),
            )
        live = sorted(entries, key=lambda e: -e["trackPos"])
        for rank, e in enumerate(live, 1):
            e["livePos"] = rank if e["inWorld"] else 0
        for rank, e in enumerate(official, 1):
            e["order"] = rank

        leader = live[0] if live else None
        leader_lap_ref = None
        if leader:
            leader_lap_ref = leader["car"].reference_lap(self.class_est.get(leader["cls"], 0.0))

        # --- gaps -------------------------------------------------------
        prev_by_class: Dict[int, Dict[str, Any]] = {}
        prev_overall: Optional[Dict[str, Any]] = None
        class_leader: Dict[int, Dict[str, Any]] = {}
        for e in live:
            if not e["inWorld"]:
                continue
            if e["cls"] not in class_leader:
                class_leader[e["cls"]] = e

        for e in live:
            car: CarState = e["car"]
            ref = car.reference_lap(self.class_est.get(e["cls"], 0.0))
            e["ref"] = ref
            e["gapLeader"] = None
            e["interval"] = None
            e["gapClassLeader"] = None
            e["lapsDown"] = 0

            if not e["inWorld"] or leader is None:
                prev_overall = prev_overall
                continue

            if under_caution:
                # Field is bunched; gaps are meaningless and thrash the tower.
                e["gapLeader"] = None
                e["interval"] = None
            else:
                e["gapLeader"] = self._gap(leader, e, ref)
                if prev_overall is not None:
                    e["interval"] = self._gap(prev_overall, e, ref)
                cl = class_leader.get(e["cls"])
                if cl is not None and cl is not e:
                    e["gapClassLeader"] = self._gap(cl, e, ref)
                pc = prev_by_class.get(e["cls"])
                if pc is not None:
                    e["classInterval"] = self._gap(pc, e, ref)

            # Take the difference FIRST, then floor. Flooring each track
            # position separately makes the entire field read "+1L" for the
            # rest of the lap the instant the leader crosses the line.
            e["lapsDown"] = max(0, int(math.floor(leader["trackPos"] - e["trackPos"])))
            prev_overall = e
            prev_by_class[e["cls"]] = e

        # --- timed sessions: gaps are lap times, not track position -------
        # In practice or qualifying nobody cares that one car is 400 metres
        # ahead of another on an out lap. The number that matters is how far
        # off the quickest lap you are, so gap and interval are recomputed
        # against best laps and the laps-down counter is meaningless.
        if timed:
            ordered = sorted(official, key=lambda e: e["order"])
            fastest_best = None
            class_best: Dict[int, float] = {}
            for e in ordered:
                b = e["car"].best_time
                if not b or b <= 0:
                    continue
                if fastest_best is None:
                    fastest_best = b
                if e["cls"] not in class_best:
                    class_best[e["cls"]] = b

            prev_best: Optional[float] = None
            prev_class_best: Dict[int, float] = {}
            for e in ordered:
                b = e["car"].best_time
                e["lapsDown"] = 0
                if not b or b <= 0:
                    # No lap yet, so there is no gap to quote. A dash is the
                    # honest answer and the overlays already render one.
                    e["gapLeader"] = None
                    e["interval"] = None
                    e["gapClassLeader"] = None
                    e["classInterval"] = None
                    continue
                e["gapLeader"] = None if fastest_best is None else round(b - fastest_best, 3)
                e["interval"] = None if prev_best is None else round(b - prev_best, 3)
                cb = class_best.get(e["cls"])
                e["gapClassLeader"] = None if cb is None else round(b - cb, 3)
                pcb = prev_class_best.get(e["cls"])
                e["classInterval"] = None if pcb is None else round(b - pcb, 3)
                prev_best = b
                prev_class_best[e["cls"]] = b

        # --- assemble ---------------------------------------------------
        fastest_idx = self.fastest_overall.get("idx")
        cars_out: List[Dict[str, Any]] = []
        by_order = sorted(live, key=lambda e: e["order"])
        now_s = float(v.get("SessionTime") or 0.0)

        # The rating projection is O(n squared) over the field. At sixty cars
        # that is well under a millisecond, but there is no reason to pay it
        # twenty times a second when the answer only moves when a position
        # does, so it is recomputed twice a second and cached between.
        if timed:
            ir_changes: Dict[int, float] = {}
        else:
            if now_s - self._ir_change_at > 0.5 or not self._ir_change:
                self._ir_change = self._irating_changes(by_order)
                self._ir_change_at = now_s
            ir_changes = self._ir_change

        for e in by_order:
            car: CarState = e["car"]
            d = e["d"]
            state = self._car_state(e, car, now_s)
            sector_best = []
            for si, sv in enumerate(car.last_sectors):
                pb = car.best_sectors[si] if si < len(car.best_sectors) else None
                sector_best.append(bool(sv is not None and pb is not None and abs(sv - pb) < 1e-6))

            cars_out.append(
                {
                    "i": e["i"],
                    "p": e["order"],
                    "lp": e.get("livePos", 0),
                    "cp": e["classPos"],
                    "lap": e["lap"],
                    "lc": e["lapsComplete"],
                    "pct": round(e["pct"], 5),
                    "gl": _r(e["gapLeader"]),
                    "iv": _r(e["interval"]),
                    "gcl": _r(e.get("gapClassLeader")),
                    "civ": _r(e.get("classInterval")),
                    "ld": e["lapsDown"],
                    "last": _r(car.last_time),
                    "best": _r(car.best_time),
                    "cur": _r(car.current_lap_time, 2),
                    "sec": [_r(s) for s in car.last_sectors],
                    "secb": sector_best,
                    "pbsec": [_r(s) for s in car.best_sectors],
                    "st": state,
                    "stops": car.pit_stops,
                    "pitdur": _r(car.last_pit_duration, 1),
                    "pitlap": car.last_pit_lap,
                    "stint": max(0, e["lap"] - car.stint_start_lap),
                    # Laps since we last saw this car on pit road. "tk" says
                    # whether that is a measurement or an assumption: false
                    # means we have never watched it pit, so the number is
                    # laps since we started watching, not tyre age.
                    "tage": max(0, e["lap"] - car.stint_start_lap),
                    "tk": bool(car.pit_seen),
                    "plt": _r(car.pit_road_time, 1),
                    "fr": e["fastRepairs"],
                    "tyre": e["tyre"],
                    "p2p": e["p2pOn"],
                    "p2pn": e["p2pLeft"],
                    "a3": _r(self._avg_of(car.recent_laps, 3)),
                    "a5": _r(self._avg_of(car.recent_laps, 5)),
                    "a10": _r(self._avg_of(car.recent_laps, 10)),
                    "tsp": _r(car.top_speed, 1) if car.top_speed > 0 else None,
                    "grid": self.grid.get(e["i"]),
                    "gain": (self.grid.get(e["i"]) - e["order"]
                             if self.grid.get(e["i"]) else None),
                    "irc": _r(ir_changes.get(e["i"]), 0),
                    "pl": e["paceLine"],
                    "pr": e["paceRow"],
                    "fast": e["i"] == fastest_idx,
                    "inc": d.get("incidents", 0),
                    # Under caution the whole field is running to a pace car,
                    # so a delta against a green-flag best is arithmetically
                    # true and completely useless. Same call as the gaps.
                    "delta": None if under_caution else _r(self._predicted_delta(e, car), 2),
                }
            )

        sess = self._session_block(v, flags_raw)
        out: Dict[str, Any] = {
            "type": "tick",
            "t": round(time.time(), 3),
            "session": sess,
            "cars": cars_out,
            "fastest": {
                "idx": fastest_idx,
                "time": _r(self.fastest_overall.get("time")),
                "lap": self.fastest_overall.get("lap"),
                "name": (self.drivers.get(fastest_idx) or {}).get("name") if fastest_idx is not None else None,
            },
            "fastestByClass": {
                str(c): {"idx": x["idx"], "time": _r(x["time"])}
                for c, x in self.fastest_by_class.items()
            },
            # A "battle" is two cars fighting over track position. In
            # practice or qualifying there is no such thing, so the battle box
            # stays empty rather than inventing a fight between two cars that
            # happen to be near each other on an out lap.
            "battles": [] if timed else self._battles(live),
            "player": self._player_block(v),
            "cam": {
                "idx": v.get("CamCarIdx"),
                "group": v.get("CamGroupNumber"),
                "camera": v.get("CamCameraNumber"),
            },
            "radio": {
                "idx": v.get("RadioTransmitCarIdx"),
                "freq": v.get("RadioTransmitFrequencyIdx"),
            },
            "replay": {
                "playing": bool(v.get("IsReplayPlaying")),
                "speed": v.get("ReplayPlaySpeed"),
                "frame": v.get("ReplayFrameNum"),
                "frameEnd": v.get("ReplayFrameNumEnd"),
            },
        }
        return out

    # -- gap maths -------------------------------------------------------

    def _gap(self, ahead: Dict[str, Any], behind: Dict[str, Any], ref: float) -> Optional[float]:
        """Seconds from `behind` to `ahead`, wrap-corrected.

        Preference order:
          1. CarIdxEstTime difference - iRacing's own projection of track
             position onto a lap time. Already corner-aware.
          2. Our observed LapDistPct -> time spline for the following car's
             class. Also corner-aware, and it is ours so we can trust it in
             replays where EstTime is odd.
          3. Flat percentage of a reference lap. Breathes through corners but
             never wrong by more than about half a second.

        The denominator is always the FOLLOWING car's pace: the gap is "how
        long until the car behind reaches where the car ahead is now", and
        that is governed by the car behind.
        """
        a_est, b_est = ahead.get("est"), behind.get("est")
        if a_est is not None and b_est is not None and (a_est > 0 or b_est > 0):
            delta = a_est - b_est
            if delta < 0:
                delta += ref
            if 0 <= delta < ref * 1.5:
                return delta

        sp = self.splines.get(behind["cls"])
        if sp is not None and sp.have:
            ta = sp.time_at(ahead["pct"], ref)
            tb = sp.time_at(behind["pct"], ref)
            delta = ta - tb
            if delta < 0:
                delta += ref
            return delta

        pct = ahead["pct"] - behind["pct"]
        if pct < 0:
            pct += 1.0
        return pct * ref

    def _predicted_delta(self, e: Dict[str, Any], car: CarState) -> Optional[float]:
        """Live delta of the current lap against this car's own best.

        Meaningless for any lap that touched pit lane: a car sitting in its box
        keeps accumulating lap time without accumulating track distance, so the
        delta runs away to hundreds of seconds. In-laps and out-laps are just as
        bad. Suppress all of them rather than publish a number nobody can use.
        """
        if car.best_time is None or car.lap_start is None:
            return None
        if e["onPit"] or e["surface"] in (
            enums.TRK_IN_PIT_STALL,
            enums.TRK_APPROACHING_PITS,
        ):
            return None
        if car.last_pit_lap and car.last_pit_lap >= e["lap"]:
            return None            # this is the out-lap
        sp = self.splines.get(e["cls"])
        if sp is None or not sp.have:
            return None
        expected = sp.time_at(e["pct"], car.best_time)
        delta = car.current_lap_time - expected
        # A sane delta is seconds, not minutes. Anything beyond this is a
        # tow, a reset or an off, none of which a delta should describe.
        return delta if -30.0 < delta < 30.0 else None

    def _battles(self, live: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out = []
        threshold = float(self.config.get("battleThreshold", 1.2))
        for a, b in zip(live, live[1:]):
            iv = b.get("interval")
            if iv is None or iv > threshold:
                continue
            if not a["inWorld"] or not b["inWorld"]:
                continue
            if b["lapsDown"] != a["lapsDown"]:
                continue
            out.append(
                {
                    "ahead": a["i"],
                    "behind": b["i"],
                    "gap": _r(iv),
                    "pos": a["order"],
                    "sameClass": a["cls"] == b["cls"],
                }
            )
        out.sort(key=lambda x: (x["pos"], x["gap"] or 99))
        return out[:6]

    # -- session / player blocks -----------------------------------------


    # -- session mode -----------------------------------------------------

    def _session_type(self, v: Dict[str, Any]) -> str:
        sessions = dig(self.session_info, "SessionInfo", "Sessions", default=[]) or []
        snum = v.get("SessionNum") or 0
        cur = sessions[snum] if isinstance(sessions, list) and 0 <= snum < len(sessions) else {}
        return _text((cur or {}).get("SessionType"))

    @staticmethod
    def _mode_of(session_type: str) -> str:
        """
        "race" or "timed".

        A race is scored by who is in front. Practice, qualifying, warmup and
        testing are scored by who has set the quickest lap, and the difference
        is not cosmetic: in a timed session the position of a car on track
        carries no information at all, so ordering by it produces a board that
        reshuffles every few seconds and puts a driver three tenths quicker
        behind one who merely happens to be further round his out lap.
        """
        return "race" if "race" in _text(session_type).lower() else "timed"

    def _session_block(self, v: Dict[str, Any], flags_raw: int) -> Dict[str, Any]:
        sessions = dig(self.session_info, "SessionInfo", "Sessions", default=[]) or []
        snum = v.get("SessionNum") or 0
        cur = sessions[snum] if isinstance(sessions, list) and 0 <= snum < len(sessions) else {}
        laps_remain = v.get("SessionLapsRemainEx")
        if laps_remain is None:
            laps_remain = v.get("SessionLapsRemain")
        time_remain = v.get("SessionTimeRemain")
        return {
            "num": snum,
            "name": _text((cur or {}).get("SessionName")),
            "type": _text((cur or {}).get("SessionType")),
            "mode": self._mode_of((cur or {}).get("SessionType")),
            "state": enums.SESSION_STATE.get(int(v.get("SessionState") or 0), "Invalid"),
            "grid": self.grid_source,
            "time": _r(v.get("SessionTime"), 2),
            "timeRemain": None if enums.is_unlimited_time(time_remain) else _r(time_remain, 1),
            "timeTotal": None if enums.is_unlimited_time(v.get("SessionTimeTotal")) else _r(v.get("SessionTimeTotal"), 1),
            "lapsRemain": None if enums.is_unlimited_laps(laps_remain) else laps_remain,
            "lapsTotal": None if enums.is_unlimited_laps(v.get("SessionLapsTotal")) else v.get("SessionLapsTotal"),
            "raceLaps": v.get("RaceLaps"),
            "flagsRaw": flags_raw,
            "flags": enums.decode_flags(flags_raw),
            "flag": enums.primary_flag(flags_raw),
            "caution": bool(flags_raw & enums.CAUTION_MASK),
            "pitsOpen": bool(v.get("PitsOpen")),
            "paceMode": v.get("PaceMode"),
            "timeOfDay": _r(v.get("SessionTimeOfDay"), 0),
            "cautions": (cur or {}).get("ResultsNumCautionFlags"),
            "cautionLaps": (cur or {}).get("ResultsNumCautionLaps"),
            "leadChanges": (cur or {}).get("ResultsNumLeadChanges"),
            "official": (cur or {}).get("ResultsOfficial"),
            "trackWetness": enums.TRACK_WETNESS.get(int(v.get("TrackWetness") or 0), ""),
            "airTemp": _r(v.get("AirTemp"), 1),
            "trackTemp": _r(v.get("TrackTemp"), 1),
            "precip": _r(v.get("Precipitation"), 3),
            "windVel": _r(v.get("WindVel"), 1),
            "skies": v.get("Skies"),
        }

    def _player_block(self, v: Dict[str, Any]) -> Dict[str, Any]:
        idx = v.get("PlayerCarIdx")
        fuel = v.get("FuelLevel")
        lap = v.get("Lap") or 0
        on_pit = bool(v.get("OnPitRoad"))

        # Fuel per lap: difference FuelLevel at each S/F crossing, ignoring
        # any lap where we were on pit road.
        if fuel is not None and lap != self._last_fuel_lap:
            if (
                self._last_fuel is not None
                and self._last_fuel_lap >= 0
                and not on_pit
                and self._last_fuel > fuel
            ):
                used = self._last_fuel - fuel
                if 0.05 < used < 30.0:
                    self.player_fuel_laps.append(used)
                    if len(self.player_fuel_laps) > 12:
                        del self.player_fuel_laps[:-12]
            self._last_fuel = fuel
            self._last_fuel_lap = lap

        per_lap = None
        if self.player_fuel_laps:
            s = sorted(self.player_fuel_laps[-5:])
            per_lap = s[len(s) // 2]

        laps_left = None
        if per_lap and per_lap > 0 and fuel is not None:
            laps_left = fuel / per_lap

        car = self.cars.get(int(idx)) if idx is not None else None
        return {
            "idx": idx,
            "pos": v.get("PlayerCarPosition"),
            "classPos": v.get("PlayerCarClassPosition"),
            "onPitRoad": on_pit,
            "inPitStall": bool(v.get("PlayerCarInPitStall")),
            "pitSvStatus": enums.PIT_SV_STATUS.get(int(v.get("PlayerCarPitSvStatus") or 0), ""),
            "incidents": v.get("PlayerCarMyIncidentCount"),
            "teamIncidents": v.get("PlayerCarTeamIncidentCount"),
            "fuel": _r(fuel, 2),
            "fuelPct": _r(v.get("FuelLevelPct"), 3),
            "fuelPerLap": _r(per_lap, 3),
            "fuelLapsLeft": _r(laps_left, 2),
            "fuelToAdd": _r(self._fuel_to_add(v, per_lap, fuel), 1),
            "speed": _r(v.get("Speed"), 2),
            "rpm": _r(v.get("RPM"), 0),
            "gear": v.get("Gear"),
            "throttle": _r(v.get("Throttle"), 3),
            "brake": _r(v.get("Brake"), 3),
            "clutch": _r(v.get("Clutch"), 3),
            "steer": _r(v.get("SteeringWheelAngle"), 3),
            "steerMax": _r(v.get("SteeringWheelAngleMax"), 3),
            "abs": bool(v.get("BrakeABSactive")),
            "lap": lap,
            "lapPct": _r(v.get("LapDistPct"), 5),
            "lapCurrent": _r(v.get("LapCurrentLapTime"), 3),
            "lapLast": _r(enums.clean_time(v.get("LapLastLapTime")), 3),
            "lapBest": _r(enums.clean_time(v.get("LapBestLapTime")), 3),
            "delta": _r(v.get("LapDeltaToSessionBestLap"), 3),
            "deltaOK": bool(v.get("LapDeltaToSessionBestLap_OK")),
            "deltaBest": _r(v.get("LapDeltaToBestLap"), 3),
            "deltaOptimal": _r(v.get("LapDeltaToOptimalLap"), 3),
            "leftRight": enums.CAR_LEFT_RIGHT.get(int(v.get("CarLeftRight") or 0), "off"),
            "warnings": enums.decode_engine_warnings(int(v.get("EngineWarnings") or 0)),
            "waterTemp": _r(v.get("WaterTemp"), 1),
            "oilTemp": _r(v.get("OilTemp"), 1),
            "shiftPct": _r(v.get("ShiftIndicatorPct"), 3),
            "onTrack": bool(v.get("IsOnTrack")),
            "inGarage": bool(v.get("IsInGarage")),
            "fastRepairs": v.get("PlayerFastRepairsUsed"),
            "fastRepairAvail": v.get("FastRepairAvailable"),
            "tyres": self._tyres(v),
            "pitService": self._pit_service(v),
            "stint": (max(0, lap - car.stint_start_lap) if car else 0),
            "frameRate": _r(v.get("FrameRate"), 0),
        }

    def _fuel_to_add(self, v, per_lap, fuel):
        if not per_lap or fuel is None:
            return None
        laps_remain = v.get("SessionLapsRemainEx")
        if enums.is_unlimited_laps(laps_remain):
            tr = v.get("SessionTimeRemain")
            if enums.is_unlimited_time(tr):
                return None
            ref = None
            idx = v.get("PlayerCarIdx")
            car = self.cars.get(int(idx)) if idx is not None else None
            if car:
                ref = car.median_clean() or car.best_time
            if not ref or ref <= 0:
                return None
            laps_remain = math.ceil(float(tr) / ref) + 1
        need = per_lap * float(laps_remain) * 1.02
        return max(0.0, need - float(fuel))

    def _tyres(self, v) -> Dict[str, Any]:
        out = {}
        for c in ("LF", "RF", "LR", "RR"):
            out[c] = {
                "p": _r(v.get(f"{c}coldPressure"), 1),
                "tl": _r(v.get(f"{c}tempCL"), 1),
                "tm": _r(v.get(f"{c}tempCM"), 1),
                "tr": _r(v.get(f"{c}tempCR"), 1),
                "wl": _r(v.get(f"{c}wearL"), 3),
                "wm": _r(v.get(f"{c}wearM"), 3),
                "wr": _r(v.get(f"{c}wearR"), 3),
            }
        return out

    def _pit_service(self, v) -> Dict[str, Any]:
        raw = int(v.get("PitSvFlags") or 0)
        return {
            "flags": [n for bit, n in enums.PIT_SV_FLAGS if raw & bit],
            "fuel": _r(v.get("PitSvFuel"), 1),
            "tyreCompound": v.get("PitSvTireCompound"),
            "active": bool(v.get("PitstopActive")),
            "repairLeft": _r(v.get("PitRepairLeft"), 1),
            "optRepairLeft": _r(v.get("PitOptRepairLeft"), 1),
        }

    # -- meta ------------------------------------------------------------

    def build_meta(self) -> Dict[str, Any]:
        """Slow-changing payload: identity, track, sectors, roster."""
        sof = self._strength_of_field()
        return {
            "type": "meta",
            "serial": self.meta_serial,
            "track": self.track,
            "event": getattr(self, "event", {}),
            "sectors": self.sectors_pct,
            "drivers": {str(k): v for k, v in self.drivers.items()},
            "classes": self._classes(),
            "playerIdx": self.player_idx,
            "paceCarIdx": self.pace_car_idx,
            "sof": sof,
            "sessions": [
                {
                    "num": s.get("SessionNum"),
                    "name": _text(s.get("SessionName")),
                    "type": s.get("SessionType"),
                    "laps": s.get("SessionLaps"),
                    "time": s.get("SessionTime"),
                }
                for s in (dig(self.session_info, "SessionInfo", "Sessions", default=[]) or [])
                if isinstance(s, dict)
            ],
            "cameras": [
                {
                    "num": g.get("GroupNum"),
                    "name": _text(g.get("GroupName")),
                    "scenic": bool(g.get("IsScenic")),
                }
                for g in (dig(self.session_info, "CameraInfo", "Groups", default=[]) or [])
                if isinstance(g, dict)
            ],
        }

    def _classes(self) -> List[Dict[str, Any]]:
        seen: Dict[int, Dict[str, Any]] = {}
        for d in self.drivers.values():
            if d.get("isPace") or d.get("isSpectator"):
                continue
            cid = d.get("classId", 0)
            if cid not in seen:
                seen[cid] = {
                    "id": cid,
                    "short": d.get("classShort") or "",
                    "color": d.get("classColor"),
                    "relSpeed": d.get("classRelSpeed"),
                    "count": 0,
                }
            seen[cid]["count"] += 1
        return sorted(seen.values(), key=lambda c: -(c.get("relSpeed") or 0))

    def _strength_of_field(self) -> Optional[int]:
        irs = [
            d.get("irating") or 0
            for d in self.drivers.values()
            if not d.get("isPace") and not d.get("isSpectator") and (d.get("irating") or 0) > 0
        ]
        if not irs:
            return None
        # iRacing's SoF is the exponential-decay average used for MMR.
        br = 1600.0 / math.log(2.0)
        total = sum(math.exp(-ir / br) for ir in irs)
        if total <= 0:
            return None
        return int(round(br * math.log(len(irs) / total)))


# ---------------------------------------------------------------------------


def _r(v: Any, nd: int = 3) -> Any:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return round(f, nd)


def _f(v: Any) -> float:
    try:
        return round(float(v), 4)
    except (TypeError, ValueError):
        return 0.0
