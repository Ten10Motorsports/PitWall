"""
Turn a GitHub issue sign-up into a roster entry.

Drivers fill in the "Driver sign-up" issue form. GitHub renders that form into
a predictable markdown body. This reads it, validates it, and merges the driver
into roster.json, which is the same file PitWall reads.

The result: a driver installs nothing, and the roster updates itself as a
commit with full history. Fix a typo by editing the issue; the workflow reruns
and the roster follows.

Usage (the workflow does this for you):

    python scripts/roster_from_issue.py --body-file body.md \
        --issue 42 --user someone --out roster.json

Exit codes:
    0  entry written (or unchanged)
    2  the submission was rejected — reason on stdout, for the issue comment
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Any, Dict, Optional

# The issue form's field labels, mapped to roster keys. Matching is done on a
# normalised form of the label so small wording edits to the form do not
# silently break the parser.
LABEL_MAP = {
    "iracing customer id": "iracingId",
    "name to show on screen": "displayName",
    "car number": "carNumber",
    "team": "team",
    "country": "country",
    "headshot": "headshot",
    "pronouns": "pronouns",
    "where you are from": "hometown",
    "one line about you": "bio",
    "twitch": "twitch",
    "youtube": "youtube",
    "instagram": "instagram",
    "x": "x",
    "discord": "discord",
    "sponsor": "sponsor",
    "sponsor logo": "sponsorLogo",
    "accent colour": "accentColor",
    "accent color": "accentColor",
}

NO_RESPONSE = "_no response_"
IMG_MD = re.compile(r"!\[[^\]]*\]\((?P<url>[^)\s]+)")
BARE_URL = re.compile(r"https?://\S+")
HEX = re.compile(r"^#?[0-9a-fA-F]{6}$")


def parse_issue_body(body: str) -> Dict[str, str]:
    """Split GitHub's rendered issue-form markdown into label -> value."""
    out: Dict[str, str] = {}
    # Sections look like:  ### Label \n\n value \n\n ### Next label
    parts = re.split(r"^###\s+", body.replace("\r\n", "\n"), flags=re.M)
    for part in parts[1:]:
        lines = part.split("\n")
        label = lines[0].strip().lower().rstrip(":")
        value = "\n".join(lines[1:]).strip()
        if not value or value.lower() == NO_RESPONSE:
            continue
        key = LABEL_MAP.get(label)
        if key:
            out[key] = value
    return out


def clean_handle(v: str) -> str:
    v = v.strip().lstrip("@")
    # People paste whole profile URLs. Keep just the handle.
    m = re.search(r"(?:twitch\.tv|youtube\.com/@?|instagram\.com|x\.com|twitter\.com)/([^/?#\s]+)", v, re.I)
    if m:
        v = m.group(1)
    return v.strip().strip("/")[:60]


def image_url(v: str) -> Optional[str]:
    """Accept a dragged-in image (markdown) or a pasted link."""
    m = IMG_MD.search(v)
    if m:
        return m.group("url")
    m = BARE_URL.search(v)
    if m:
        return m.group(0).rstrip(")>,.")
    return None


def build_entry(fields: Dict[str, str], issue: Optional[int], user: Optional[str]) -> Dict[str, Any]:
    cid = re.sub(r"\D", "", fields.get("iracingId", ""))
    if not cid:
        raise ValueError(
            "I could not read an iRacing customer ID. It should be numbers only — "
            "the number on the iRacing website under **Account → My Account → Customer ID**. "
            "Edit the issue to fix it and I will pick it up automatically."
        )
    if len(cid) > 9:
        raise ValueError(
            f"`{cid}` does not look like an iRacing customer ID — they are up to about "
            "seven digits. Please check **Account → My Account → Customer ID** and edit the issue."
        )
    name = fields.get("displayName", "").strip()
    if not name:
        raise ValueError("A name to show on screen is required. Edit the issue to add one.")

    entry: Dict[str, Any] = {"iracingId": cid, "displayName": name[:60]}

    if fields.get("carNumber"):
        entry["carNumber"] = re.sub(r"[^0-9]", "", fields["carNumber"])[:3] or fields["carNumber"].strip()[:3]
    if fields.get("team"):
        entry["team"] = fields["team"].strip()[:60]

    cc = fields.get("country", "").strip().upper()
    m = re.search(r"\b([A-Z]{2})\b", cc)
    if m:
        entry["country"] = m.group(1)

    for k in ("pronouns", "hometown", "sponsor"):
        if fields.get(k):
            entry[k] = fields[k].strip()[:60]
    if fields.get("bio"):
        entry["bio"] = " ".join(fields["bio"].split())[:280]

    for k in ("twitch", "youtube", "instagram", "x", "discord"):
        if fields.get(k):
            h = clean_handle(fields[k])
            if h:
                entry[k] = h

    for k in ("headshot", "sponsorLogo"):
        if fields.get(k):
            url = image_url(fields[k])
            if url:
                entry[k] = url

    col = fields.get("accentColor", "").strip()
    if col and HEX.match(col):
        entry["accentColor"] = "#" + col.lstrip("#").lower()

    entry["submittedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if issue:
        entry["sourceIssue"] = issue
    if user:
        entry["submittedBy"] = user
    return entry


def merge(path: str, entry: Dict[str, Any]) -> Dict[str, Any]:
    data: Dict[str, Any] = {"drivers": []}
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            data = raw if isinstance(raw, dict) else {"drivers": raw}
        except Exception:
            data = {"drivers": []}
    drivers = data.get("drivers") or []

    replaced = False
    for i, d in enumerate(drivers):
        if str(d.get("iracingId")) == str(entry["iracingId"]):
            # Keep an image they uploaded previously if this submission omits it.
            for k in ("headshot", "sponsorLogo"):
                if k not in entry and d.get(k):
                    entry[k] = d[k]
            drivers[i] = entry
            replaced = True
            break
    if not replaced:
        drivers.append(entry)

    drivers.sort(key=lambda d: (str(d.get("carNumber") or "").zfill(4), str(d.get("displayName") or "")))
    data["drivers"] = drivers
    data["updated"] = time.time()

    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return {"replaced": replaced, "count": len(drivers)}


def summary(entry: Dict[str, Any], result: Dict[str, Any]) -> str:
    lines = [
        "**" + ("Updated" if result["replaced"] else "Added") + " your entry.** "
        "It will appear on the broadcast next time the roster is loaded.",
        "",
        "| | |",
        "|---|---|",
        f"| Customer ID | `{entry['iracingId']}` |",
        f"| Name | {entry['displayName']} |",
    ]
    for label, key in (
        ("Number", "carNumber"), ("Team", "team"), ("Country", "country"),
        ("Pronouns", "pronouns"), ("From", "hometown"),
    ):
        if entry.get(key):
            lines.append(f"| {label} | {entry[key]} |")
    socials = [k for k in ("twitch", "youtube", "instagram", "x", "discord") if entry.get(k)]
    if socials:
        lines.append("| Socials | " + ", ".join(f"{k}: {entry[k]}" for k in socials) + " |")
    lines.append("| Headshot | " + ("received" if entry.get("headshot") else "none — your initials will be shown instead") + " |")
    if entry.get("bio"):
        lines += ["", f"> {entry['bio']}"]
    lines += [
        "",
        f"There are now **{result['count']}** drivers in the roster.",
        "",
        "Something wrong? Edit this issue and it will update automatically. "
        "You do not need to open a new one.",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--body-file", required=True)
    ap.add_argument("--out", default="roster.json")
    ap.add_argument("--issue", type=int, default=None)
    ap.add_argument("--user", default=None)
    ap.add_argument("--comment-file", default=None, help="write the reply markdown here")
    a = ap.parse_args()

    with open(a.body_file, "r", encoding="utf-8") as fh:
        body = fh.read()

    def emit(text: str) -> None:
        if a.comment_file:
            with open(a.comment_file, "w", encoding="utf-8") as fh:
                fh.write(text)
        print(text)

    try:
        fields = parse_issue_body(body)
        entry = build_entry(fields, a.issue, a.user)
    except ValueError as exc:
        emit("**I could not add you to the roster.**\n\n" + str(exc))
        return 2

    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    result = merge(a.out, entry)
    emit(summary(entry, result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
