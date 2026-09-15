"""
PitWall - driver roster and driver-submitted profiles.

iRacing tells you a driver's name, number, team, iRating and licence. It tells
you nothing a broadcast actually wants: how to pronounce the name, what the
driver looks like, who their sponsor is, what country flag to fly, their socials,
or a one-line bio for the lower third.

So drivers submit that themselves, once, through a form PitWall serves. The
result is a roster file that overlays join against live telemetry by iRacing
customer ID.

Storage has three modes, in increasing order of "everyone in the league can see
the same thing":

  local   roster.json on the broadcast machine. Zero setup.
  github  roster.json in a GitHub repo. Read via the raw URL by anyone,
          written back through the API with a token. This is the mode to use
          for a league: the repo IS the shared database, drivers' submissions
          land as commits, and you get history and rollback for free.
  url     read-only pull from any URL (a Gist, a raw GitHub link, an S3 object).

Matching a roster entry to a live car, in priority order:
  1. iRacing customer ID (UserID in the session string) - exact, survives name
     changes and is the only reliable key in team races.
  2. Car number + surname.
  3. Exact display name.
"""

from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

MAX_IMAGE_BYTES = 2 * 1024 * 1024
ALLOWED_IMAGE = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}

FIELDS = [
    "iracingId", "displayName", "carNumber", "team", "country", "pronouns",
    "hometown", "bio", "twitch", "youtube", "instagram", "x", "discord",
    "headshot", "sponsor", "sponsorLogo", "accentColor", "number", "notes",
    # League tags: a short label and a colour that make a driver stand out in
    # every list. Stored with the roster rather than on one machine, so a tag
    # a steward adds is seen by everyone running PitWall from that roster.
    "tagLabel", "tagColour",
]

# A tag colour has to end up in a CSS custom property, so it is validated as a
# colour here rather than trusted. Anything else is dropped, not escaped: a
# broken colour is a broken graphic and there is no safe way to guess.
_COLOUR = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

_SAFE = re.compile(r"[^A-Za-z0-9._-]")


class RosterStore:
    def __init__(self, data_dir: str, config: Optional[Dict[str, Any]] = None) -> None:
        self.data_dir = data_dir
        self.uploads = os.path.join(data_dir, "uploads")
        os.makedirs(self.uploads, exist_ok=True)
        self.path = os.path.join(data_dir, "roster.json")
        self.config = (config or {}).get("roster", {}) if config else {}
        self.lock = threading.RLock()
        self.entries: List[Dict[str, Any]] = []
        self.updated = 0.0
        self.last_sync: Optional[str] = None
        self.sync_error: Optional[str] = None
        self._by_id: Dict[str, Dict[str, Any]] = {}
        self._by_num_last: Dict[str, Dict[str, Any]] = {}
        self._by_name: Dict[str, Dict[str, Any]] = {}
        self.load()

    # -- persistence -----------------------------------------------------

    def load(self) -> None:
        with self.lock:
            if os.path.exists(self.path):
                try:
                    with open(self.path, "r", encoding="utf-8") as fh:
                        raw = json.load(fh)
                    self.entries = raw.get("drivers", raw if isinstance(raw, list) else [])
                except Exception:
                    self.entries = []
            self._reindex()

    def save(self) -> None:
        with self.lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(
                    {"updated": time.time(), "drivers": self.entries}, fh, indent=2, ensure_ascii=False
                )
            os.replace(tmp, self.path)
            self.updated = time.time()
            self._reindex()

    def _reindex(self) -> None:
        self._by_id, self._by_num_last, self._by_name = {}, {}, {}
        for e in self.entries:
            cid = str(e.get("iracingId") or "").strip()
            if cid:
                self._by_id[cid] = e
            name = str(e.get("displayName") or "").strip()
            if name:
                self._by_name[name.lower()] = e
                surname = name.split()[-1].lower() if name.split() else ""
                num = str(e.get("carNumber") or "").strip().lstrip("0") or "0"
                if surname:
                    self._by_num_last[f"{num}|{surname}"] = e

    # -- lookup ----------------------------------------------------------

    def lookup(self, driver: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Join a live iRacing driver record to a submitted profile."""
        with self.lock:
            uid = str(driver.get("userId") or "").strip()
            if uid and uid in self._by_id:
                return self._public(self._by_id[uid])
            name = str(driver.get("name") or "").strip()
            if name:
                num = str(driver.get("num") or "").strip().lstrip("0") or "0"
                surname = name.split()[-1].lower() if name.split() else ""
                hit = self._by_num_last.get(f"{num}|{surname}")
                if hit:
                    return self._public(hit)
                hit = self._by_name.get(name.lower())
                if hit:
                    return self._public(hit)
        return None

    @staticmethod
    def _public(e: Dict[str, Any]) -> Dict[str, Any]:
        out = {k: v for k, v in e.items() if k not in ("email", "notes", "ip")}
        return out

    # -- mutation --------------------------------------------------------

    def submit(self, payload: Dict[str, Any], local_edit: bool = False) -> Dict[str, Any]:
        """
        Accept a driver submission. Upserts on iRacing customer ID.

        local_edit marks the entry as typed into this machine's control panel
        rather than submitted by the driver. Those survive a pull from the
        shared roster, because otherwise every correction a broadcaster makes
        on race night is silently undone the next time the roster refreshes.
        """
        entry: Dict[str, Any] = {}
        for f in FIELDS:
            v = payload.get(f)
            if isinstance(v, str):
                v = v.strip()[:600]
            if v not in (None, ""):
                entry[f] = v

        cid = str(entry.get("iracingId") or "").strip()
        if not cid.isdigit():
            raise ValueError(
                "iRacing customer ID must be the numeric ID from your iRacing "
                "account page (Account > My Account > Customer ID)."
            )
        if not entry.get("displayName"):
            raise ValueError("Display name is required.")

        # Inline images arrive as data URLs and are written to disk.
        for key in ("headshotData", "sponsorLogoData"):
            data = payload.get(key)
            if data:
                target = "headshot" if key == "headshotData" else "sponsorLogo"
                entry[target] = self._store_image(data, f"{cid}-{target}")

        # Fold the two flat tag fields into the shape the widgets read.
        label = str(entry.pop("tagLabel", "") or "").strip()[:12]
        colour = str(entry.pop("tagColour", "") or "").strip()
        if not _COLOUR.match(colour):
            colour = ""
        if label or colour:
            entry["tag"] = {"label": label, "colour": colour or "#b06cff"}

        entry["submittedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if local_edit:
            # Stamped so a later pull can tell "this was typed here" from
            # "this came from the shared roster", and leave it alone.
            entry["editedHere"] = True
            entry["editedAt"] = entry["submittedAt"]

        with self.lock:
            existing = self._by_id.get(cid)
            if existing is not None:
                # Preserve images if this submission did not include new ones.
                for k in ("headshot", "sponsorLogo"):
                    if k not in entry and existing.get(k):
                        entry[k] = existing[k]
                # A driver re-submitting their own form does not clear the fact
                # that a broadcaster corrected something here.
                if not local_edit and existing.get("editedHere"):
                    entry["editedHere"] = True
                    entry["editedAt"] = existing.get("editedAt")
                idx = self.entries.index(existing)
                self.entries[idx] = entry
            else:
                self.entries.append(entry)
            self.save()
        return entry

    def delete(self, cid: str) -> bool:
        with self.lock:
            e = self._by_id.get(str(cid))
            if not e:
                return False
            self.entries.remove(e)
            self.save()
            return True

    def replace_all(self, drivers: List[Dict[str, Any]]) -> None:
        with self.lock:
            self.entries = drivers
            self.save()

    def merge_pull(self, drivers: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Fold a freshly pulled roster into the local one, keeping local edits.

        A pull used to replace the file outright, which meant any correction
        typed on the broadcast machine vanished the next time the roster
        refreshed, usually without anyone noticing until a graphic was wrong on
        air. Now an entry edited here wins over the pulled copy, and an entry
        that only exists here is kept.
        """
        with self.lock:
            kept = {
                str(e.get("iracingId")): e
                for e in self.entries
                if e.get("editedHere")
            }
            out, replaced = [], 0
            for d in drivers:
                cid = str(d.get("iracingId") or "")
                local = kept.pop(cid, None)
                if local is not None:
                    out.append(local)
                    replaced += 1
                else:
                    out.append(d)
            # Anything edited here that the shared roster has never heard of.
            local_only = list(kept.values())
            out.extend(local_only)
            self.entries = out
            self.save()
            return {
                "pulled": len(drivers),
                "keptEdited": replaced,
                "localOnly": len(local_only),
                "total": len(out),
            }

    def clear_local_edit(self, cid: str) -> bool:
        """Drop the local-edit flag so the next pull restores the shared copy."""
        with self.lock:
            e = self._by_id.get(str(cid))
            if not e or not e.get("editedHere"):
                return False
            e.pop("editedHere", None)
            e.pop("editedAt", None)
            self.save()
            return True

    def _store_image(self, data_url: str, stem: str) -> str:
        m = re.match(r"^data:([\w/+-]+);base64,(.*)$", data_url, re.S)
        if not m:
            raise ValueError("Image must be sent as a data URL.")
        mime, b64 = m.group(1), m.group(2)
        ext = ALLOWED_IMAGE.get(mime)
        if not ext:
            raise ValueError("Images must be PNG, JPEG or WebP.")
        try:
            blob = base64.b64decode(b64)
        except Exception:
            raise ValueError("Image data could not be decoded.")
        if len(blob) > MAX_IMAGE_BYTES:
            raise ValueError("Images must be under 2 MB.")
        name = _SAFE.sub("_", stem) + ext
        with open(os.path.join(self.uploads, name), "wb") as fh:
            fh.write(blob)
        return f"/uploads/{name}"

    # -- GitHub sync -----------------------------------------------------

    def github_config(self) -> Dict[str, Any]:
        c = self.config or {}
        return {
            "mode": c.get("mode", "local"),
            "repo": c.get("repo", ""),
            "branch": c.get("branch", "main"),
            "path": c.get("path", "roster.json"),
            "url": c.get("url", ""),
            "hasToken": bool(c.get("token") or os.environ.get("PITWALL_GITHUB_TOKEN")),
        }

    def _token(self) -> Optional[str]:
        return (self.config or {}).get("token") or os.environ.get("PITWALL_GITHUB_TOKEN")

    def pull(self) -> Dict[str, Any]:
        """Fetch the roster from GitHub (or any URL) and replace the local copy."""
        cfg = self.config or {}
        mode = cfg.get("mode", "local")
        try:
            if mode == "url" and cfg.get("url"):
                blob = _http_get(cfg["url"])
            elif mode == "github" and cfg.get("repo"):
                url = (
                    f"https://raw.githubusercontent.com/{cfg['repo']}/"
                    f"{cfg.get('branch', 'main')}/{cfg.get('path', 'roster.json')}"
                )
                blob = _http_get(url, token=self._token())
            else:
                return {"ok": False, "error": "Roster sync is set to local storage."}
            data = json.loads(blob.decode("utf-8"))
            drivers = data.get("drivers", data if isinstance(data, list) else [])
            stats = self.merge_pull(drivers)
            self.last_sync = time.strftime("%H:%M:%S")
            self.sync_error = None
            return {"ok": True, "count": stats["total"], **stats}
        except Exception as exc:
            self.sync_error = str(exc)
            return {"ok": False, "error": str(exc)}

    def push(self, message: Optional[str] = None) -> Dict[str, Any]:
        """Commit the roster back to GitHub."""
        cfg = self.config or {}
        if cfg.get("mode") != "github" or not cfg.get("repo"):
            return {"ok": False, "error": "GitHub sync is not configured."}
        token = self._token()
        if not token:
            return {
                "ok": False,
                "error": "No GitHub token. Set roster.token in config, or the "
                         "PITWALL_GITHUB_TOKEN environment variable.",
            }
        repo, branch = cfg["repo"], cfg.get("branch", "main")
        path = cfg.get("path", "roster.json")
        api = f"https://api.github.com/repos/{repo}/contents/{path}"

        sha = None
        try:
            cur = json.loads(_http_get(f"{api}?ref={branch}", token=token).decode("utf-8"))
            sha = cur.get("sha")
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                return {"ok": False, "error": f"GitHub read failed: {exc}"}
        except Exception as exc:
            return {"ok": False, "error": f"GitHub read failed: {exc}"}

        with self.lock:
            body = json.dumps(
                {"updated": time.time(), "drivers": self.entries}, indent=2, ensure_ascii=False
            ).encode("utf-8")

        payload = {
            "message": message or f"PitWall roster update ({len(self.entries)} drivers)",
            "content": base64.b64encode(body).decode("ascii"),
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha
        try:
            _http_json(api, payload, token=token, method="PUT")
            self.last_sync = time.strftime("%H:%M:%S")
            self.sync_error = None
            return {"ok": True, "count": len(self.entries)}
        except Exception as exc:
            self.sync_error = str(exc)
            return {"ok": False, "error": str(exc)}

    def status(self) -> Dict[str, Any]:
        cfg = self.github_config()
        cfg.update(
            {
                "count": len(self.entries),
                "editedHere": sum(1 for e in self.entries if e.get("editedHere")),
                "lastSync": self.last_sync,
                "error": self.sync_error,
                "file": self.path,
            }
        )
        return cfg


# ---------------------------------------------------------------------------


def _http_get(url: str, token: Optional[str] = None, timeout: float = 15.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "PitWall"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _http_json(url: str, payload: Dict[str, Any], token: str, method: str = "POST") -> Dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("User-Agent", "PitWall")
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(req, timeout=20.0) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")
