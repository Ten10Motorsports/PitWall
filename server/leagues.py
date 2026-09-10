"""
League profiles: saved setups so a broadcaster opens one and goes live.

A league profile is everything that changes between one series and another:
the name and colours on every graphic, where the roster is read from, and the
season calendar. Switching to a profile applies all of it at once, so the
person running the stream picks a league, picks a round, and starts OBS,
rather than re-typing a roster URL and an accent colour every week.

Stored in `data/leagues.json`, which sits next to the executable rather than
inside it, so profiles survive an app update. One file rather than a folder of
them: a league's whole season is small, and one file is one thing to back up,
copy to the co-commentator's machine, or paste into a repo.

The calendar is deliberately loose. Every field except the track is optional,
because a league that has not settled round 7 yet should still be able to save
rounds 1 through 6, and a broadcaster who wants to correct a track name ninety
seconds before going live should not have to fill in a date to do it.
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

FILENAME = "leagues.json"

# Fields a round may carry. Anything else submitted is dropped rather than
# stored, so a future version of the control panel cannot quietly fill this
# file with keys the engine has never heard of.
ROUND_FIELDS = ("round", "date", "time", "track", "name", "laps", "notes")

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    s = _SLUG_STRIP.sub("-", (name or "").strip().lower()).strip("-")
    return s or "league"


def _int_or_none(v: Any) -> Optional[int]:
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    return n


def _hex_colour(v: Any, fallback: str) -> str:
    s = str(v or "").strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", s):
        return s
    if re.fullmatch(r"[0-9a-fA-F]{6}", s):
        return "#" + s
    return fallback


class LeagueStore:
    def __init__(self, data_dir: str) -> None:
        self.data_dir = data_dir
        self.path = os.path.join(data_dir, FILENAME)
        self.leagues: List[Dict[str, Any]] = []
        self.active_id: Optional[str] = None
        self.load()

    # -- disk ------------------------------------------------------------

    def load(self) -> None:
        if not os.path.isfile(self.path):
            self.leagues = []
            self.active_id = None
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except Exception as exc:
            # A corrupt profiles file must not stop the app booting on race
            # night. Say so loudly and carry on with none.
            print(f"[PitWall] Could not read {self.path}: {exc}. Starting with no league profiles.")
            self.leagues = []
            self.active_id = None
            return
        self.leagues = [self._clean(l) for l in (raw.get("leagues") or []) if isinstance(l, dict)]
        self.active_id = raw.get("active") or None
        if self.active_id and not self.get(self.active_id):
            self.active_id = None

    def save(self) -> None:
        os.makedirs(self.data_dir, exist_ok=True)
        payload = {
            "updated": time.time(),
            "active": self.active_id,
            "leagues": self.leagues,
        }
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        # Replace in one step. A half-written profiles file read on the next
        # start would lose a whole season's calendar.
        os.replace(tmp, self.path)

    # -- shaping ---------------------------------------------------------

    def _clean(self, league: Dict[str, Any]) -> Dict[str, Any]:
        name = str(league.get("name") or "").strip() or "Untitled league"
        lid = str(league.get("id") or "").strip() or slugify(name)
        rounds = []
        for r in league.get("schedule") or []:
            if not isinstance(r, dict):
                continue
            row = {k: r.get(k) for k in ROUND_FIELDS if r.get(k) not in (None, "")}
            if not row.get("track") and not row.get("name"):
                continue
            row["round"] = _int_or_none(row.get("round")) or (len(rounds) + 1)
            laps = _int_or_none(row.get("laps"))
            if laps:
                row["laps"] = laps
            else:
                row.pop("laps", None)
            for k in ("date", "time", "track", "name", "notes"):
                if k in row:
                    row[k] = str(row[k]).strip()
            rounds.append(row)
        rounds.sort(key=lambda r: r.get("round") or 0)

        return {
            "id": lid,
            "name": name,
            "accent": _hex_colour(league.get("accent"), "#e8443a"),
            "secondary": _hex_colour(league.get("secondary"), "#12b8ff"),
            "logo": str(league.get("logo") or "").strip(),
            "hashtag": str(league.get("hashtag") or "").strip(),
            "rosterUrl": str(league.get("rosterUrl") or "").strip(),
            "activeRound": _int_or_none(league.get("activeRound")),
            "schedule": rounds,
        }

    # -- reads -----------------------------------------------------------

    def get(self, lid: str) -> Optional[Dict[str, Any]]:
        for l in self.leagues:
            if l["id"] == lid:
                return l
        return None

    def active(self) -> Optional[Dict[str, Any]]:
        return self.get(self.active_id) if self.active_id else None

    def round_of(self, league: Dict[str, Any], number: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """The round a league is set to, or a specific one by number."""
        want = number if number is not None else league.get("activeRound")
        if want is None:
            return None
        for r in league.get("schedule") or []:
            if r.get("round") == want:
                return r
        return None

    def suggest_round(self, league: Dict[str, Any], today: Optional[str] = None) -> Optional[int]:
        """
        The round a broadcaster most likely wants tonight: the first one dated
        today or later. Falls back to the last round of the season once the
        calendar has run out, so opening the app after the finale still lands
        on something sensible rather than nothing.
        """
        sched = [r for r in league.get("schedule") or [] if r.get("date")]
        if not sched:
            return None
        today = today or time.strftime("%Y-%m-%d")
        upcoming = sorted((r for r in sched if str(r["date"]) >= today), key=lambda r: str(r["date"]))
        if upcoming:
            return upcoming[0].get("round")
        return sorted(sched, key=lambda r: str(r["date"]))[-1].get("round")

    def event_payload(self) -> Optional[Dict[str, Any]]:
        """
        What the overlays are told about tonight. None when no league is
        active, which is the normal state for someone using the app casually,
        and every graphic already works without it.
        """
        league = self.active()
        if not league:
            return None
        rnd = self.round_of(league)
        out: Dict[str, Any] = {
            "league": league["name"],
            "hashtag": league.get("hashtag") or "",
            "logo": league.get("logo") or "",
        }
        if rnd:
            out.update(
                {
                    "round": rnd.get("round"),
                    "name": rnd.get("name") or (f"Round {rnd.get('round')}" if rnd.get("round") else ""),
                    "track": rnd.get("track") or "",
                    "date": rnd.get("date") or "",
                    "time": rnd.get("time") or "",
                    "laps": rnd.get("laps"),
                    "notes": rnd.get("notes") or "",
                }
            )
        return out

    def status(self) -> Dict[str, Any]:
        league = self.active()
        return {
            "count": len(self.leagues),
            "active": self.active_id,
            "activeName": league["name"] if league else None,
            "event": self.event_payload(),
        }

    # -- writes ----------------------------------------------------------

    def upsert(self, league: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(league, dict):
            raise ValueError("A league profile must be an object.")
        if not str(league.get("name") or "").strip():
            raise ValueError("Give the league a name.")
        cleaned = self._clean(league)

        # A rename keeps the original id, so the calendar and the active
        # selection survive fixing a typo in the league's name.
        for i, existing in enumerate(self.leagues):
            if existing["id"] == cleaned["id"]:
                self.leagues[i] = cleaned
                break
        else:
            # A brand new profile whose slug collides with another league gets
            # a numbered id rather than silently overwriting it.
            base = cleaned["id"]
            n = 2
            while self.get(cleaned["id"]):
                cleaned["id"] = f"{base}-{n}"
                n += 1
            self.leagues.append(cleaned)

        self.leagues.sort(key=lambda l: l["name"].lower())
        self.save()
        return cleaned

    def delete(self, lid: str) -> bool:
        before = len(self.leagues)
        self.leagues = [l for l in self.leagues if l["id"] != lid]
        if self.active_id == lid:
            self.active_id = None
        if len(self.leagues) != before:
            self.save()
            return True
        return False

    def duplicate(self, lid: str) -> Optional[Dict[str, Any]]:
        src = self.get(lid)
        if not src:
            return None
        copy = json.loads(json.dumps(src))
        copy["name"] = src["name"] + " (copy)"
        copy["id"] = ""
        return self.upsert(copy)

    def activate(self, lid: str, round_no: Optional[int] = None) -> Dict[str, Any]:
        league = self.get(lid)
        if not league:
            raise ValueError("No league profile with that name is saved.")
        self.active_id = lid
        if round_no is not None:
            league["activeRound"] = _int_or_none(round_no)
        elif league.get("activeRound") is None:
            league["activeRound"] = self.suggest_round(league)
        self.save()
        return league

    def set_round(self, round_no: Optional[int]) -> Optional[Dict[str, Any]]:
        league = self.active()
        if not league:
            raise ValueError("No league is active.")
        league["activeRound"] = _int_or_none(round_no)
        self.save()
        return league
