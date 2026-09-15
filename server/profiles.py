"""
PitWall - named widget profiles.

A widget is configured by its query string. That worked while there were four
options; with thirty-two columns and a colour for every car state it produces
URLs long enough to wrap three times in the OBS properties box, and changing
one of them means editing six browser sources by hand on a Tuesday evening.

So a layout is saved here instead, under a name, and a widget is pointed at it:

    http://127.0.0.1:8099/broadcast/tower.html?profile=race-tower

The engine serves the profile; the widget merges it underneath its own query
string, so an explicit parameter in the URL still wins. That ordering matters:
it means nothing anyone has already set up can be broken by a profile someone
else edits, and it means a profile can be overridden for one source without
having to clone it.

A profile also carries a per-session-type layer. A league night runs practice,
then qualifying, then the race, and the columns worth showing differ in each;
`sessions.race`, `sessions.qualify` and `sessions.practice` are merged over the
base when the session changes, so the swap happens without anyone touching OBS.

The file is data/profiles.json and is written atomically, because the one time
it will be saved is thirty seconds before a race starts.
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Optional

# What a widget gets when it asks for a profile that does not exist. Deliberately
# not an error: a typo in an OBS URL should produce the default graphic, not a
# blank source that nobody notices until the stream is live.
EMPTY: Dict[str, Any] = {"name": "", "widget": "", "opts": {}, "sessions": {}}

SESSION_KEYS = ("race", "qualify", "practice")

# Widgets that can carry a profile. Anything else is rejected on save so a typo
# does not create a profile that can never be used.
WIDGETS = (
    "tower", "relative", "ticker", "standings", "driver-card",
    "session-bar", "battle", "trackmap", "flags", "delta", "fuel",
    "inputs", "blindspot", "lapgraph",
)


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").strip().lower())
    return re.sub(r"-{2,}", "-", s).strip("-") or "profile"


def _clean_opts(raw: Any) -> Dict[str, str]:
    """
    Options are query-string values, so they are stored as strings.

    Anything that cannot be expressed in a URL cannot be a widget option, and
    keeping them as strings here means the merge in the browser is the same
    operation whether a value came from the URL or from the profile.
    """
    out: Dict[str, str] = {}
    if not isinstance(raw, dict):
        return out
    for k, v in raw.items():
        key = re.sub(r"[^A-Za-z0-9_]", "", str(k))[:40]
        if not key:
            continue
        if isinstance(v, bool):
            out[key] = "1" if v else "0"
        elif v is None:
            continue
        else:
            out[key] = str(v)[:600]
    return out


class ProfileStore:
    def __init__(self, data_dir: str) -> None:
        self.path = os.path.join(data_dir, "profiles.json")
        self.profiles: Dict[str, Dict[str, Any]] = {}
        self.load()

    # -- disk ------------------------------------------------------------

    def load(self) -> None:
        if not os.path.isfile(self.path):
            self.profiles = {}
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            items = raw.get("profiles") if isinstance(raw, dict) else raw
            self.profiles = {}
            for p in items or []:
                rec = self._shape(p)
                if rec:
                    self.profiles[rec["id"]] = rec
        except Exception as exc:
            print(f"[PitWall] Could not read {self.path}: {exc}. Starting with none.")
            self.profiles = {}

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        payload = {
            "version": 1,
            "updated": time.time(),
            "profiles": list(self.profiles.values()),
        }
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, self.path)

    # -- shaping ---------------------------------------------------------

    def _shape(self, raw: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(raw, dict):
            return None
        name = str(raw.get("name") or "").strip()
        if not name:
            return None
        widget = str(raw.get("widget") or "").strip().lower()
        if widget not in WIDGETS:
            return None
        pid = slugify(raw.get("id") or name)
        sessions: Dict[str, Dict[str, str]] = {}
        for key in SESSION_KEYS:
            block = (raw.get("sessions") or {}).get(key)
            cleaned = _clean_opts(block)
            if cleaned:
                sessions[key] = cleaned
        return {
            "id": pid,
            "name": name[:60],
            "widget": widget,
            "opts": _clean_opts(raw.get("opts")),
            "sessions": sessions,
            "updated": time.time(),
        }

    # -- api -------------------------------------------------------------

    def list(self) -> List[Dict[str, Any]]:
        return sorted(self.profiles.values(), key=lambda p: (p["widget"], p["name"].lower()))

    def get(self, pid: str) -> Dict[str, Any]:
        rec = self.profiles.get(slugify(pid))
        return rec if rec else dict(EMPTY)

    def upsert(self, payload: Any) -> Dict[str, Any]:
        rec = self._shape(payload)
        if not rec:
            raise ValueError("A profile needs a name and a known widget.")
        # An id collision with a different name means someone typed a name that
        # slugs to an existing one. Suffix rather than silently overwrite: losing
        # a layout to a name collision is not a thing anyone should discover
        # during a broadcast.
        existing = self.profiles.get(rec["id"])
        wanted_id = str(payload.get("id") or "").strip()
        if existing and not wanted_id and existing["name"].lower() != rec["name"].lower():
            n = 2
            while f"{rec['id']}-{n}" in self.profiles:
                n += 1
            rec["id"] = f"{rec['id']}-{n}"
        self.profiles[rec["id"]] = rec
        self.save()
        return rec

    def delete(self, pid: str) -> bool:
        pid = slugify(pid)
        if pid in self.profiles:
            del self.profiles[pid]
            self.save()
            return True
        return False

    def duplicate(self, pid: str) -> Optional[Dict[str, Any]]:
        src = self.profiles.get(slugify(pid))
        if not src:
            return None
        copy = json.loads(json.dumps(src))
        copy["name"] = (src["name"] + " copy")[:60]
        copy.pop("id", None)
        return self.upsert(copy)

    def status(self) -> Dict[str, Any]:
        return {
            "count": len(self.profiles),
            "path": self.path,
            "widgets": sorted({p["widget"] for p in self.profiles.values()}),
        }
