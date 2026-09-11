"""
PitWall - synthetic iRacing feed.

Produces exactly the same variable dictionary and session-info structure the
real SDK reader produces, so every downstream component (engine, web server,
every overlay) runs identically with or without iRacing.

This exists for three reasons:

  1. You can see and tune every overlay without booting the sim, on any OS.
  2. Broadcast operators can rehearse a show, build an OBS scene collection
     and check every graphic before race day.
  3. Bugs in the overlays can be reproduced deterministically with a seed,
     instead of "it looked wrong at Spa once".

It models a two-class 24-car grid with per-corner speed variation, tyre
degradation, fuel burn, pit stops, driver errors, a safety car period and a
race that actually finishes.
"""

from __future__ import annotations

import math
import random
import time
from typing import Any, Dict, List

FIRST = [
    "Alex", "Jamie", "Morgan", "Casey", "Riley", "Jordan", "Taylor", "Cameron",
    "Devon", "Quinn", "Rowan", "Sasha", "Marco", "Luca", "Niels", "Emil",
    "Piet", "Tomas", "Ana", "Ines", "Kenji", "Hiro", "Diego", "Mateo",
]
LAST = [
    "Whitcombe", "Arkwright", "Delacroix", "Van Rijn", "Okonkwo", "Halvorsen",
    "Mbeki", "Rosetti", "Fairbanks", "Nakamura", "Olsen", "Petrov",
    "Castellanos", "Bergqvist", "Dunmore", "Kaczmarek", "Sorrentino",
    "Ferreira", "Lindqvist", "Achterberg", "Novak", "Ilves", "Barlow", "Renard",
]
TEAMS = [
    "Ashgrove Motorsport", "Northgate Racing", "Vireo Competition",
    "Kestrel Autosport", "Blackwater GP", "Ardent Racing Team",
    "Meridian Sport", "Fenwick Racing", "Solstice Motorsport", "Ravenline",
    "Highfield Racing", "Corvid Performance",
]
COUNTRIES = ["GB", "US", "DE", "FR", "IT", "NL", "ES", "SE", "AU", "BR", "JP", "PL"]

CLASSES = [
    {
        "id": 2523,
        "short": "GT3",
        "color": 0xFE3B1F,
        "est": 138.0,
        "car": "Ferrari 296 GT3",
        "carShort": "296 GT3",
        "count": 14,
    },
    {
        "id": 2708,
        "short": "GT4",
        "color": 0x33C9FF,
        "est": 156.0,
        "car": "BMW M4 GT4",
        "carShort": "M4 GT4",
        "count": 10,
    },
]

# A crude but plausible speed profile around a lap: 24 segments of relative
# speed. Gives the LapDistPct -> time relationship real curvature, which is
# what makes naive gap maths visibly wrong and correct gap maths look right.
SPEED_PROFILE = [
    1.00, 1.28, 1.34, 0.62, 0.70, 1.15, 1.30, 1.32, 0.55, 0.68,
    0.95, 1.10, 1.22, 0.72, 0.60, 0.88, 1.18, 1.30, 1.36, 0.65,
    0.75, 1.05, 1.20, 1.10,
]

# Normalise so that a full lap at these relative speeds takes exactly the
# class reference lap time. Without this, demo lap times drift away from the
# numbers the session string advertises and every gap check looks wrong.
_MEAN_INV = sum(1.0 / s for s in SPEED_PROFILE) / len(SPEED_PROFILE)
SPEED_PROFILE = [s * _MEAN_INV for s in SPEED_PROFILE]

SECTORS = [0.0, 0.3153, 0.6944]


class DemoCar:
    def __init__(self, idx: int, cls: Dict[str, Any], rnd: random.Random, number: int):
        self.idx = idx
        self.cls = cls
        self.num = str(number)
        self.name = f"{rnd.choice(FIRST)} {rnd.choice(LAST)}"
        self.team = rnd.choice(TEAMS)
        self.country = rnd.choice(COUNTRIES)
        self.irating = int(rnd.gauss(2400 if cls["short"] == "GT3" else 1700, 700))
        self.irating = max(600, self.irating)
        self.user_id = 100000 + idx * 37 + rnd.randint(1, 99)
        # Per-driver pace, in fractions of the class reference.
        self.pace = rnd.gauss(1.0, 0.008)
        self.consistency = abs(rnd.gauss(0.004, 0.002))
        self.pct = 0.0
        self.lap = 0
        self.laps_complete = 0
        self.last_lap = -1.0
        self.best_lap = -1.0
        self.lap_started = 0.0
        self.on_pit = False
        self.surface = 3
        self.pit_until = 0.0
        self.stops = 0
        self.fuel = 100.0
        self.incidents = 0
        self.est = 0.0
        self.f2 = 0.0
        self.next_pit_lap = rnd.randint(11, 17)
        self.retired = False
        self.tyre_age = 0
        self.rnd = rnd
        self.pace_line = -1
        self.pace_row = -1

    @property
    def base_lap(self) -> float:
        return self.cls["est"] * self.pace


class DemoFeed:
    """Drop-in replacement for the live SDK reader."""

    def __init__(self, seed: int = 7, scenario: str = "race", speed: float = 1.0) -> None:
        self.rnd = random.Random(seed)
        self.scenario = scenario
        # Time compression. At speed 10 a 42-lap race runs in about six
        # minutes, so you can rehearse a whole broadcast - green flag, pit
        # window, safety car, checkered - over a coffee.
        self.speed = max(0.1, float(speed))
        self.t0 = time.time()
        self.session_time = 0.0
        self.cars: List[DemoCar] = []
        self.session_info_update = 1
        self._built = 0
        self.race_laps = 42
        self.state = 4  # Racing
        self.flags = 0x00000004
        self.caution_until = -1.0
        self.next_caution = self.rnd.uniform(300, 700)
        self.player_idx = 0
        self.finished = False
        self._build_grid()
        self._last_real = time.time()

    # -- grid ------------------------------------------------------------

    def _build_grid(self) -> None:
        idx = 0
        number = 2
        for cls in CLASSES:
            for _ in range(cls["count"]):
                self.cars.append(DemoCar(idx, cls, self.rnd, number))
                idx += 1
                number += self.rnd.randint(1, 5)
        # Grid order: fastest class first, then by pace.
        self.cars.sort(key=lambda c: (-c.cls["est"] * 0 - CLASSES.index(c.cls) * -1, c.pace))
        self.cars.sort(key=lambda c: (CLASSES.index(c.cls), c.pace))
        for i, c in enumerate(self.cars):
            c.idx = i
            # Stagger the standing start down the grid.
            c.pct = 1.0 - (i * 0.0016)
            c.lap = 0
        self.player_idx = 0

    # -- ticking ---------------------------------------------------------

    def tick(self) -> Dict[str, Any]:
        now = time.time()
        dt = min(0.25, now - self._last_real) * self.speed
        self._last_real = now
        self.session_time += dt

        self._update_flags()

        under_caution = bool(self.flags & (0x00000008 | 0x00004000 | 0x00008000))

        for car in self.cars:
            if car.retired:
                continue
            self._advance(car, dt, under_caution)

        self._score()
        return self._vars()

    def _update_flags(self) -> None:
        if self.finished:
            return
        if self.session_time > self.caution_until and self.flags & 0x00004000:
            self.flags = 0x00000004
        if self.scenario == "race" and self.session_time > self.next_caution and not (
            self.flags & 0x00004000
        ):
            self.flags = 0x00004000 | 0x00000008
            self.caution_until = self.session_time + self.rnd.uniform(90, 150)
            self.next_caution = self.session_time + self.rnd.uniform(500, 1100)
        leader = max(self.cars, key=lambda c: c.lap + c.pct)
        if leader.lap >= self.race_laps:
            self.flags = 0x00000001
            self.state = 5
            self.finished = True

    def _advance(self, car: DemoCar, dt: float, caution: bool) -> None:
        if self.session_time < car.pit_until:
            car.surface = 1  # in pit stall
            car.on_pit = True
            return
        if car.on_pit and car.surface == 1:
            car.on_pit = True
            car.surface = 2
            car.fuel = 100.0
            car.tyre_age = 0
            car.stops += 1

        lap_time = car.base_lap
        lap_time *= 1.0 + self.rnd.gauss(0.0, car.consistency)
        lap_time *= 1.0 + car.tyre_age * 0.0011          # degradation
        lap_time *= 1.0 + max(0.0, (100.0 - car.fuel)) * -0.00018  # lighter is faster
        if caution:
            lap_time *= 1.9
        if car.on_pit:
            # Pit lane costs time over the pit-lane portion of the lap only,
            # not the whole lap. A pit lap should be roughly +30s, the way a
            # real one is, or every downstream lap-time and delta looks insane.
            lap_time *= 1.24

        # Speed varies around the lap so LapDistPct is not linear in time.
        seg = int(car.pct * len(SPEED_PROFILE)) % len(SPEED_PROFILE)
        rel = SPEED_PROFILE[seg]
        if caution or car.on_pit:
            rel = 1.0

        car.pct += (dt / lap_time) * rel
        car.fuel = max(0.0, car.fuel - (dt / lap_time) * 3.1)

        if self.rnd.random() < 0.00008:
            car.incidents += self.rnd.choice([1, 1, 2, 4])
        if self.rnd.random() < 0.000004 and self.session_time > 200:
            car.retired = True
            car.surface = -1

        if car.pct >= 1.0:
            car.pct -= 1.0
            car.lap += 1
            car.laps_complete = car.lap - 1
            car.tyre_age += 1
            if car.lap_started > 0 and not car.on_pit and not caution:
                t = self.session_time - car.lap_started
                car.last_lap = t
                if car.best_lap < 0 or t < car.best_lap:
                    car.best_lap = t
            elif car.lap_started > 0:
                car.last_lap = self.session_time - car.lap_started
            car.lap_started = self.session_time

            if car.on_pit and car.surface == 2:
                car.on_pit = False
                car.surface = 3

            if car.lap >= car.next_pit_lap and not car.retired and not caution:
                car.on_pit = True
                car.surface = 1
                car.pit_until = self.session_time + self.rnd.uniform(24, 38)
                car.next_pit_lap = car.lap + self.rnd.randint(13, 18)

    def _score(self) -> None:
        order = sorted(
            [c for c in self.cars if not c.retired],
            key=lambda c: -(c.lap + c.pct),
        )
        leader = order[0] if order else None
        for pos, c in enumerate(order, 1):
            c.position = pos
        for c in self.cars:
            if c.retired:
                c.position = 0
        # Class positions
        for cls in CLASSES:
            k = 1
            for c in order:
                if c.cls["id"] == cls["id"]:
                    c.class_position = k
                    k += 1
        # EstTime: seconds into a lap that this track position corresponds to.
        for c in self.cars:
            c.est = self._est_time(c.pct, c.base_lap)
            if leader:
                if c is leader:
                    c.f2 = 0.0
                else:
                    laps_down = int(leader.lap + leader.pct) - int(c.lap + c.pct)
                    d = leader.est - c.est
                    if d < 0:
                        d += c.base_lap
                    c.f2 = d + laps_down * c.base_lap

    @staticmethod
    def _est_time(pct: float, lap_time: float) -> float:
        """Integrate the inverse speed profile up to `pct`."""
        n = len(SPEED_PROFILE)
        total = sum(1.0 / s for s in SPEED_PROFILE)
        acc = 0.0
        full = pct * n
        i = 0
        while i < int(full):
            acc += 1.0 / SPEED_PROFILE[i % n]
            i += 1
        acc += (full - int(full)) / SPEED_PROFILE[int(full) % n]
        return (acc / total) * lap_time

    # -- output ----------------------------------------------------------

    def _vars(self) -> Dict[str, Any]:
        n = 64
        def arr(fn, default):
            out = [default] * n
            for c in self.cars:
                out[c.idx] = fn(c)
            return out

        p = self.cars[self.player_idx]
        leader = max((c for c in self.cars if not c.retired), key=lambda c: c.lap + c.pct)
        laps_remain = max(0, self.race_laps - leader.lap)

        return {
            "SessionTime": self.session_time,
            "SessionTick": int(self.session_time * 60),
            "SessionNum": 0,
            "SessionState": self.state,
            "SessionUniqueID": 1,
            "SessionFlags": self.flags,
            # A race counts down laps; practice and qualifying count down a
            # clock. iRacing signals "not applicable" with its unlimited
            # sentinel rather than with zero, so the demo does the same.
            "SessionTimeRemain": 604800.0 if self.scenario == "race"
            else max(0.0, 3600.0 - self.session_time),
            "SessionTimeTotal": 604800.0 if self.scenario == "race" else 3600.0,
            "SessionLapsRemain": laps_remain if self.scenario == "race" else 32767,
            "SessionLapsRemainEx": laps_remain if self.scenario == "race" else 32767,
            "SessionLapsTotal": self.race_laps if self.scenario == "race" else 32767,
            "SessionTimeOfDay": 50400.0 + self.session_time,
            "RaceLaps": leader.lap,
            "PitsOpen": True,
            "PaceMode": 4 if not (self.flags & 0x00004000) else 1,
            "IsReplayPlaying": False,
            "IsOnTrack": True,
            "IsInGarage": False,
            "AirTemp": 21.4,
            "TrackTemp": 31.8,
            "TrackWetness": 1,
            "Precipitation": 0.0,
            "WindVel": 2.4,
            "Skies": 1,
            "FrameRate": 144.0,

            "CarIdxLapDistPct": arr(lambda c: c.pct if not c.retired else -1.0, -1.0),
            "CarIdxLap": arr(lambda c: c.lap, 0),
            "CarIdxLapCompleted": arr(lambda c: c.laps_complete, 0),
            # iRacing only scores a position when it is running a race. In an
            # open practice it leaves the whole field at zero, which is exactly
            # the case the engine has to handle for itself, so the demo models
            # it rather than papering over it.
            "CarIdxPosition": arr(lambda c: getattr(c, "position", 0), 0)
            if self.scenario == "race" else [0] * len(self.cars),
            "CarIdxClassPosition": arr(lambda c: getattr(c, "class_position", 0), 0)
            if self.scenario == "race" else [0] * len(self.cars),
            "CarIdxClass": arr(lambda c: c.cls["id"], 0),
            "CarIdxTrackSurface": arr(lambda c: -1 if c.retired else c.surface, -1),
            "CarIdxOnPitRoad": arr(lambda c: c.on_pit, False),
            "CarIdxLastLapTime": arr(lambda c: c.last_lap, -1.0),
            "CarIdxBestLapTime": arr(lambda c: c.best_lap, -1.0),
            "CarIdxBestLapNum": arr(lambda c: 1, 0),
            "CarIdxEstTime": arr(lambda c: c.est, 0.0),
            "CarIdxF2Time": arr(lambda c: c.f2, 0.0),
            "CarIdxFastRepairsUsed": arr(lambda c: 0, 0),
            "CarIdxPaceLine": arr(lambda c: c.pace_line, -1),
            "CarIdxPaceRow": arr(lambda c: c.pace_row, -1),
            "CarIdxTireCompound": arr(lambda c: 0, -1),

            "PlayerCarIdx": p.idx,
            "PlayerCarPosition": getattr(p, "position", 0),
            "PlayerCarClassPosition": getattr(p, "class_position", 0),
            "PlayerCarMyIncidentCount": p.incidents,
            "PlayerCarTeamIncidentCount": p.incidents,
            "PlayerCarInPitStall": p.surface == 1,
            "PlayerCarPitSvStatus": 1 if p.surface == 1 else 0,
            "PlayerFastRepairsUsed": 0,
            "FastRepairAvailable": 1,
            "OnPitRoad": p.on_pit,
            "Lap": p.lap,
            "LapDistPct": p.pct,
            "LapCurrentLapTime": self.session_time - p.lap_started,
            "LapLastLapTime": p.last_lap,
            "LapBestLapTime": p.best_lap,
            "LapDeltaToSessionBestLap": math.sin(self.session_time * 0.7) * 0.45,
            "LapDeltaToSessionBestLap_OK": True,
            "LapDeltaToBestLap": math.sin(self.session_time * 0.7) * 0.45,
            "LapDeltaToOptimalLap": math.sin(self.session_time * 0.9) * 0.6,
            "FuelLevel": p.fuel,
            "FuelLevelPct": p.fuel / 100.0,
            "Speed": 40 + 45 * SPEED_PROFILE[int(p.pct * len(SPEED_PROFILE)) % len(SPEED_PROFILE)],
            "RPM": 4200 + 3300 * SPEED_PROFILE[int(p.pct * len(SPEED_PROFILE)) % len(SPEED_PROFILE)] / 1.4,
            "Gear": 1 + int(5 * SPEED_PROFILE[int(p.pct * len(SPEED_PROFILE)) % len(SPEED_PROFILE)] / 1.4),
            "Throttle": max(0.0, min(1.0, (SPEED_PROFILE[int(p.pct * len(SPEED_PROFILE)) % len(SPEED_PROFILE)] - 0.6) / 0.7)),
            "Brake": max(0.0, min(1.0, (0.95 - SPEED_PROFILE[int(p.pct * len(SPEED_PROFILE)) % len(SPEED_PROFILE)]) / 0.4)),
            "Clutch": 1.0,
            "SteeringWheelAngle": math.sin(p.pct * math.pi * 14) * 2.1,
            "SteeringWheelAngleMax": 8.0,
            "BrakeABSactive": False,
            "CarLeftRight": 1,
            "EngineWarnings": 0,
            "WaterTemp": 88.0,
            "OilTemp": 104.0,
            "ShiftIndicatorPct": 0.6,
            "PitSvFlags": 0x10 | 0x01 | 0x02,
            "PitSvFuel": 42.0,
            "PitSvTireCompound": 0,
            "PitstopActive": p.surface == 1,
            "PitRepairLeft": 0.0,
            "PitOptRepairLeft": 0.0,
            "CamCarIdx": leader.idx,
            "CamGroupNumber": 3,
            "CamCameraNumber": 1,
            "RadioTransmitCarIdx": leader.idx if int(self.session_time) % 17 < 3 else -1,
            "RadioTransmitFrequencyIdx": 0,
            **self._tyre_vars(),
        }

    def _tyre_vars(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        p = self.cars[self.player_idx]
        wear = max(0.35, 1.0 - p.tyre_age * 0.012)
        for i, c in enumerate(("LF", "RF", "LR", "RR")):
            out[f"{c}coldPressure"] = 165.0 + i
            out[f"{c}tempCL"] = 82.0 + i * 2
            out[f"{c}tempCM"] = 85.0 + i * 2
            out[f"{c}tempCR"] = 88.0 + i * 2
            out[f"{c}wearL"] = wear
            out[f"{c}wearM"] = wear - 0.02
            out[f"{c}wearR"] = wear - 0.01
        return out

    # -- session string ---------------------------------------------------

    _SESSION_NAMES = {
        "race": ("RACE", "Race"),
        "practice": ("PRACTICE", "Practice"),
        "qualify": ("QUALIFY", "Open Qualify"),
        "warmup": ("WARMUP", "Warmup"),
    }

    def _session_name(self) -> str:
        return self._SESSION_NAMES.get(self.scenario, self._SESSION_NAMES["race"])[0]

    def _session_type(self) -> str:
        return self._SESSION_NAMES.get(self.scenario, self._SESSION_NAMES["race"])[1]

    def session_info(self) -> Dict[str, Any]:
        drivers = []
        for c in self.cars:
            drivers.append(
                {
                    "CarIdx": c.idx,
                    "UserName": c.name,
                    "AbbrevName": f"{c.name.split()[-1]}, {c.name[0]}",
                    "Initials": "".join(w[0] for w in c.name.split()[:2]),
                    "UserID": c.user_id,
                    "TeamID": 900000 + c.idx,
                    "TeamName": c.team,
                    "CarNumber": c.num,
                    "CarNumberRaw": int(c.num),
                    "CarScreenName": c.cls["car"],
                    "CarScreenNameShort": c.cls["carShort"],
                    "CarClassID": c.cls["id"],
                    "CarClassShortName": c.cls["short"],
                    "CarClassColor": c.cls["color"],
                    "CarClassRelSpeed": 100 if c.cls["short"] == "GT3" else 80,
                    "CarClassEstLapTime": c.cls["est"],
                    "IRating": c.irating,
                    "LicString": self._lic(c.irating),
                    "LicColor": 0x00C8FF,
                    "ClubName": c.country,
                    "DivisionName": "Division 2",
                    "CurDriverIncidentCount": c.incidents,
                    "TeamIncidentCount": c.incidents,
                    "CarIsPaceCar": 0,
                    "CarIsAI": 0,
                    "IsSpectator": 0,
                }
            )
        drivers.append(
            {
                "CarIdx": 63,
                "UserName": "Pace Car",
                "CarNumber": "0",
                "CarNumberRaw": 0,
                "CarClassID": 0,
                "CarIsPaceCar": 1,
                "IsSpectator": 0,
            }
        )
        return {
            "WeekendInfo": {
                "TrackName": "spa gp",
                "TrackID": 341,
                "TrackDisplayName": "Circuit de Spa-Francorchamps",
                "TrackDisplayShortName": "Spa",
                "TrackConfigName": "Grand Prix",
                "TrackLength": "7.00 km",
                "TrackCity": "Stavelot",
                "TrackCountry": "Belgium",
                "TrackNumTurns": 19,
                "TrackPitSpeedLimit": "60.00 kph",
                "TrackNorthOffset": "3.1416 rad",
                "TrackType": "road course",
                "TrackDirection": "neutral",
                "SubSessionID": 78123456,
                "LeagueID": 4412,
                "TeamRacing": 1,
                "EventType": "Race",
                "SimMode": "full",
                "Official": 0,
                "WeekendOptions": {
                    "NumStarters": len(self.cars),
                    "StandingStart": 1,
                    "IncidentLimit": 17,
                    "FastRepairsLimit": 1,
                    "GreenWhiteCheckeredLimit": 0,
                    "Date": "2026-09-09",
                    "TimeOfDay": "2:00 pm",
                },
            },
            "SessionInfo": {
                "Sessions": [
                    {
                        "SessionNum": 0,
                        "SessionName": self._session_name(),
                        "SessionType": self._session_type(),
                        "SessionLaps": self.race_laps if self.scenario == "race" else "unlimited",
                        "SessionTime": "unlimited",
                        "ResultsNumCautionFlags": 1 if self.session_time > 400 else 0,
                        "ResultsNumCautionLaps": 3 if self.session_time > 400 else 0,
                        "ResultsNumLeadChanges": 2,
                        "ResultsOfficial": 0,
                    }
                ]
            },
            "DriverInfo": {
                "DriverCarIdx": self.player_idx,
                "PaceCarIdx": 63,
                "DriverCarEstLapTime": self.cars[self.player_idx].base_lap,
                "DriverPitTrkPct": 0.01452,
                "DriverCarFuelMaxLtr": 100.0,
                "DriverCarRedLine": 7800,
                "DriverCarSLBlinkRPM": 7400,
                "DriverCarSLShiftRPM": 7200,
                "DriverCarSLFirstRPM": 6200,
                "Drivers": drivers,
            },
            "SplitTimeInfo": {
                "Sectors": [
                    {"SectorNum": i, "SectorStartPct": p} for i, p in enumerate(SECTORS)
                ]
            },
            "CameraInfo": {
                "Groups": [
                    {"GroupNum": 1, "GroupName": "Nose", "Cameras": [{"CameraNum": 1, "CameraName": "CamNose"}]},
                    {"GroupNum": 2, "GroupName": "Gearbox", "Cameras": [{"CameraNum": 1, "CameraName": "CamGearbox"}]},
                    {"GroupNum": 3, "GroupName": "TV1", "Cameras": [{"CameraNum": 1, "CameraName": "CamTV1"}]},
                    {"GroupNum": 4, "GroupName": "TV2", "Cameras": [{"CameraNum": 1, "CameraName": "CamTV2"}]},
                    {"GroupNum": 5, "GroupName": "Chase", "Cameras": [{"CameraNum": 1, "CameraName": "CamChase"}]},
                    {"GroupNum": 11, "GroupName": "Scenic", "IsScenic": 1, "Cameras": [{"CameraNum": 1, "CameraName": "CamScenic"}]},
                ]
            },
            "RadioInfo": {"SelectedRadioNum": 0, "Radios": []},
        }

    @staticmethod
    def _lic(ir: int) -> str:
        if ir > 3500:
            return "A 4.12"
        if ir > 2500:
            return "A 3.05"
        if ir > 1800:
            return "B 3.44"
        if ir > 1200:
            return "C 2.87"
        return "D 2.10"

    # Demo drivers for the roster page, so the profile join can be seen working.
    def sample_profiles(self) -> List[Dict[str, Any]]:
        out = []
        for c in self.cars[:8]:
            out.append(
                {
                    "iracingId": c.user_id,
                    "displayName": c.name,
                    "carNumber": c.num,
                    "team": c.team,
                    "country": c.country,
                    "pronouns": "",
                    "hometown": "",
                    "bio": "Demo profile generated by PitWall so you can see the "
                           "driver-submitted fields render on the overlays.",
                    "twitch": "",
                    "instagram": "",
                    "x": "",
                    "headshot": "",
                    "sponsor": "",
                    "accentColor": "#%06x" % c.cls["color"],
                    "submittedAt": "demo",
                }
            )
        return out
