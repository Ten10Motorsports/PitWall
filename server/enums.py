"""PitWall - iRacing enum and bitfield decoding."""

from __future__ import annotations

from typing import List

# --- SessionFlags (irsdk_Flags, uint32) ------------------------------------

FLAGS = [
    (0x00000001, "checkered"),
    (0x00000002, "white"),
    (0x00000004, "green"),
    (0x00000008, "yellow"),
    (0x00000010, "red"),
    (0x00000020, "blue"),
    (0x00000040, "debris"),
    (0x00000080, "crossed"),
    (0x00000100, "yellowWaving"),
    (0x00000200, "oneLapToGreen"),
    (0x00000400, "greenHeld"),
    (0x00000800, "tenToGo"),
    (0x00001000, "fiveToGo"),
    (0x00002000, "randomWaving"),
    (0x00004000, "caution"),
    (0x00008000, "cautionWaving"),
    (0x00010000, "black"),
    (0x00020000, "disqualify"),
    (0x00040000, "servicible"),
    (0x00080000, "furled"),
    (0x00100000, "repair"),
    (0x00200000, "dqScoringInvalid"),
    (0x10000000, "startHidden"),
    (0x20000000, "startReady"),
    (0x40000000, "startSet"),
    (0x80000000, "startGo"),
]

# Flags that mean "the whole track is neutralised".
CAUTION_MASK = 0x00000008 | 0x00000100 | 0x00004000 | 0x00008000
RED_MASK = 0x00000010


def decode_flags(raw: int) -> List[str]:
    if not raw:
        return []
    return [name for bit, name in FLAGS if raw & bit]


def primary_flag(raw: int) -> str:
    """The single flag a status bar should show."""
    if raw & 0x00000010:
        return "red"
    if raw & 0x00000001:
        return "checkered"
    if raw & CAUTION_MASK:
        return "yellow"
    if raw & 0x00000002:
        return "white"
    if raw & (0x00000004 | 0x00000400 | 0x80000000):
        return "green"
    return "none"


# --- SessionState ----------------------------------------------------------

SESSION_STATE = {
    0: "Invalid",
    1: "GetInCar",
    2: "Warmup",
    3: "ParadeLaps",
    4: "Racing",
    5: "Checkered",
    6: "CoolDown",
}

# --- TrackLocation (CarIdxTrackSurface) ------------------------------------

TRK_NOT_IN_WORLD = -1
TRK_OFF_TRACK = 0
TRK_IN_PIT_STALL = 1
TRK_APPROACHING_PITS = 2
TRK_ON_TRACK = 3

TRACK_SURFACE = {
    -1: "NotInWorld",
    0: "OffTrack",
    1: "InPitStall",
    2: "ApproachingPits",
    3: "OnTrack",
}

# --- TrackWetness ----------------------------------------------------------

TRACK_WETNESS = {
    0: "Unknown",
    1: "Dry",
    2: "Mostly Dry",
    3: "Very Lightly Wet",
    4: "Lightly Wet",
    5: "Moderately Wet",
    6: "Very Wet",
    7: "Extremely Wet",
}

# --- Engine warnings -------------------------------------------------------

ENGINE_WARNINGS = [
    (0x01, "waterTemp"),
    (0x02, "fuelPressure"),
    (0x04, "oilPressure"),
    (0x08, "engineStalled"),
    (0x10, "pitSpeedLimiter"),
    (0x20, "revLimiterActive"),
    (0x40, "oilTemp"),
    (0x80, "mandRepNeeded"),
    (0x100, "optRepNeeded"),
]


def decode_engine_warnings(raw: int) -> List[str]:
    if not raw:
        return []
    return [name for bit, name in ENGINE_WARNINGS if raw & bit]


# --- Pit service -----------------------------------------------------------

PIT_SV_FLAGS = [
    (0x01, "LF"),
    (0x02, "RF"),
    (0x04, "LR"),
    (0x08, "RR"),
    (0x10, "fuel"),
    (0x20, "tearoff"),
    (0x40, "fastRepair"),
]

PIT_SV_STATUS = {
    0: "none",
    1: "inProgress",
    2: "complete",
    100: "tooFarLeft",
    101: "tooFarRight",
    102: "tooFarForward",
    103: "tooFarBack",
    104: "badAngle",
    105: "cantFixThat",
}

# --- Pace flags ------------------------------------------------------------

PACE_FLAGS = [(0x01, "endOfLine"), (0x02, "freePass"), (0x04, "wavedAround")]

# --- CarLeftRight ----------------------------------------------------------

CAR_LEFT_RIGHT = {
    0: "off",
    1: "clear",
    2: "carLeft",
    3: "carRight",
    4: "carLeftRight",
    5: "twoCarsLeft",
    6: "twoCarsRight",
}

# --- Sentinels -------------------------------------------------------------

UNLIMITED_LAPS = 32767
UNLIMITED_TIME = 604800.0


def is_unlimited_laps(v) -> bool:
    return v is None or v >= UNLIMITED_LAPS


def is_unlimited_time(v) -> bool:
    return v is None or v >= UNLIMITED_TIME - 1.0


def clean_time(v):
    """iRacing uses -1 for 'no time set'. Turn that into None."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def hex_colour(v, default: str = "#9aa4b2") -> str:
    """CarClassColor / LicColor arrive as 0xRRGGBB ints (sometimes strings)."""
    if v is None:
        return default
    try:
        if isinstance(v, str):
            s = v.strip()
            n = int(s, 16) if s.lower().startswith("0x") else int(s)
        else:
            n = int(v)
    except (TypeError, ValueError):
        return default
    return "#%06x" % (n & 0xFFFFFF)
