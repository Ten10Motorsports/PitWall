"""
PitWall - self-building track maps.

iRacing gives you every car's LapDistPct but no track geometry, so most
overlays either ship a hand-drawn SVG per track (and are missing whichever
one you are racing this week) or draw a meaningless oval.

PitWall builds the real outline itself. While anyone is driving or spectating,
the sim publishes the *camera-followed* car's latitude, longitude and
LapDistPct. Sample those around one clean lap and you have the actual shape of
the circuit, correctly proportioned, with a known mapping from LapDistPct to a
point on the path. Cache it per track ID and it is there forever.

Result: correct maps for every track including new ones, built on the first
clean lap anybody runs, with no asset pack to download and nothing to update.
"""

from __future__ import annotations

import json
import math
import os
import time
from typing import Any, Dict, List, Optional, Tuple

BINS = 720          # angular resolution of the stored path
MIN_COVERAGE = 0.90  # fraction of bins that must be filled before we save


class TrackMapBuilder:
    def __init__(self, data_dir: str) -> None:
        self.dir = os.path.join(data_dir, "tracks")
        os.makedirs(self.dir, exist_ok=True)
        self.track_id: Optional[int] = None
        self.points: List[Optional[Tuple[float, float]]] = [None] * BINS
        self.filled = 0
        self.cached: Dict[int, Dict[str, Any]] = {}
        self.last_pct = -1.0
        self.building = False

    # -- lifecycle -------------------------------------------------------

    def set_track(self, track_id: Optional[int], name: str = "") -> None:
        if track_id is None or track_id == self.track_id:
            return
        self.track_id = int(track_id)
        self.name = name
        self.points = [None] * BINS
        self.filled = 0
        self.last_pct = -1.0
        self.building = self.load(self.track_id) is None

    def path_for(self, track_id: int) -> str:
        return os.path.join(self.dir, f"{track_id}.json")

    def load(self, track_id: int) -> Optional[Dict[str, Any]]:
        if track_id in self.cached:
            return self.cached[track_id]
        p = self.path_for(track_id)
        if not os.path.isfile(p):
            return None
        try:
            with open(p, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            self.cached[track_id] = data
            return data
        except Exception:
            return None

    # -- sampling --------------------------------------------------------

    def sample(self, v: Dict[str, Any]) -> None:
        """Feed one telemetry tick. Cheap; safe to call every frame."""
        if not self.building or self.track_id is None:
            return
        lat, lon = v.get("Lat"), v.get("Lon")
        pct = v.get("LapDistPct")
        if lat is None or lon is None or pct is None:
            return
        try:
            lat, lon, pct = float(lat), float(lon), float(pct)
        except (TypeError, ValueError):
            return
        if not (-90 < lat < 90) or not (-180 < lon < 180) or lat == 0.0 and lon == 0.0:
            return
        if pct < 0 or pct >= 1:
            return
        # Only sample from a car that is actually on track and moving.
        if v.get("IsOnTrack") is False and v.get("IsReplayPlaying") is not True:
            return
        if v.get("OnPitRoad"):
            return
        speed = v.get("Speed")
        if speed is not None and float(speed) < 5.0:
            return

        b = int(pct * BINS) % BINS
        if self.points[b] is None:
            self.points[b] = (lat, lon)
            self.filled += 1
            if self.filled >= int(BINS * MIN_COVERAGE):
                self.finish()

    def finish(self) -> Optional[Dict[str, Any]]:
        """Interpolate the gaps, normalise to a unit box, and save."""
        if self.track_id is None:
            return None
        pts = list(self.points)
        # Fill holes by walking forward from the last known point.
        known = [i for i, p in enumerate(pts) if p is not None]
        if len(known) < BINS * 0.6:
            return None
        for i in range(BINS):
            if pts[i] is not None:
                continue
            prev = _prev_known(pts, i)
            nxt = _next_known(pts, i)
            if prev is None or nxt is None:
                continue
            pi, pv = prev
            ni, nv = nxt
            span = (ni - pi) % BINS or 1
            step = (i - pi) % BINS
            f = step / span
            pts[i] = (pv[0] + (nv[0] - pv[0]) * f, pv[1] + (nv[1] - pv[1]) * f)
        if any(p is None for p in pts):
            return None

        lat0 = sum(p[0] for p in pts) / len(pts)
        # Convert degrees to metres so the aspect ratio is right.
        mlat = 111320.0
        mlon = 111320.0 * math.cos(math.radians(lat0))
        xy = [((p[1] * mlon), (p[0] * mlat)) for p in pts]

        xs = [p[0] for p in xy]
        ys = [p[1] for p in xy]
        minx, maxx = min(xs), max(xs)
        miny, maxy = min(ys), max(ys)
        w = max(1e-6, maxx - minx)
        h = max(1e-6, maxy - miny)
        scale = 1.0 / max(w, h)
        offx = (1.0 - w * scale) / 2.0
        offy = (1.0 - h * scale) / 2.0
        norm = [
            [
                round((x - minx) * scale + offx, 5),
                # Flip Y so north is up in SVG coordinates.
                round(1.0 - ((y - miny) * scale + offy), 5),
            ]
            for x, y in xy
        ]

        data = {
            "trackId": self.track_id,
            "name": getattr(self, "name", ""),
            "bins": BINS,
            "points": norm,
            "built": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "coverage": round(self.filled / BINS, 3),
        }
        try:
            with open(self.path_for(self.track_id), "w", encoding="utf-8") as fh:
                json.dump(data, fh)
        except OSError:
            pass
        self.cached[self.track_id] = data
        self.building = False
        return data

    def status(self) -> Dict[str, Any]:
        have = self.load(self.track_id) is not None if self.track_id else False
        return {
            "trackId": self.track_id,
            "have": have,
            "building": self.building,
            "progress": round(self.filled / BINS, 3) if self.building else 1.0,
        }

    def current(self) -> Optional[Dict[str, Any]]:
        if self.track_id is None:
            return None
        return self.load(self.track_id)


def _prev_known(pts, i):
    for k in range(1, len(pts)):
        j = (i - k) % len(pts)
        if pts[j] is not None:
            return j, pts[j]
    return None


def _next_known(pts, i):
    for k in range(1, len(pts)):
        j = (i + k) % len(pts)
        if pts[j] is not None:
            return j, pts[j]
    return None


def synthetic(name: str = "Demo Circuit") -> Dict[str, Any]:
    """A plausible circuit shape for demo mode, so the map has something real
    to draw before anybody has turned a lap."""
    pts = []
    for i in range(BINS):
        t = i / BINS * 2 * math.pi
        r = (
            0.36
            + 0.10 * math.sin(3 * t + 0.6)
            + 0.05 * math.sin(5 * t + 2.1)
            + 0.03 * math.cos(7 * t)
        )
        x = 0.5 + r * math.cos(t) * 1.25
        y = 0.5 + r * math.sin(t) * 0.85
        pts.append([round(x, 5), round(y, 5)])
    return {"trackId": -1, "name": name, "bins": BINS, "points": pts, "coverage": 1.0}
