"""
PitWall - parser for iRacing's session info string.

iRacing calls this YAML, and it very nearly is, but it is machine-generated
with a narrow grammar and a few well-known ways of being invalid:

  * Driver, team and setup names routinely contain ':', '"', '\\' and leading
    commas.  ``UserName: Smith: The Sequel`` is not valid YAML and a real
    parser either throws or silently mangles it.
  * Car numbers like ``01`` must keep their leading zero.
  * ``CarNumber: 12:34`` gets eaten as a sexagesimal/timestamp by PyYAML's
    implicit resolvers.
  * Control bytes 0x81 0x8D 0x8F 0x90 0x9D appear in names and are invalid
    cp1252.

Rather than post-processing a general YAML parser into submission (which is
what every other tool does, and why exotic team names still break them), this
is a purpose-built recursive-descent parser for exactly the grammar iRacing
emits.  It splits each line on the FIRST colon only, which makes the entire
class of "colon in a name" bugs impossible.

No third-party dependencies.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Control bytes that are invalid in cp1252 but appear in user-supplied names.
_BAD_BYTES = str.maketrans({c: " " for c in "\x81\x8d\x8f\x90\x9d"})
_NON_PRINTABLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_ .\-]*):(?:\s|$)")

# Sections that are large and rarely needed. Skipped unless asked for.
DEFAULT_SKIP = ("CarSetup",)


class _Line:
    __slots__ = ("indent", "content")

    def __init__(self, indent: int, content: str):
        self.indent = indent
        self.content = content


def parse(text: str, skip_sections: Sequence[str] = DEFAULT_SKIP) -> Dict[str, Any]:
    """Parse an iRacing session info string into nested dicts and lists."""
    if not text:
        return {}
    text = text.translate(_BAD_BYTES)
    text = _NON_PRINTABLE.sub("", text)

    lines: List[_Line] = []
    skipping = False
    for raw in text.split("\n"):
        if not raw.strip():
            continue
        stripped = raw.lstrip(" ")
        if stripped.startswith("---") or stripped.startswith("..."):
            continue
        indent = len(raw) - len(stripped)
        stripped = stripped.rstrip()
        if indent == 0:
            key = stripped.split(":", 1)[0].strip()
            skipping = key in skip_sections
        if skipping:
            continue
        lines.append(_Line(indent, stripped))

    if not lines:
        return {}
    value, _ = _parse_block(lines, 0, lines[0].indent)
    return value if isinstance(value, dict) else {"_": value}


# ---------------------------------------------------------------------------


def _parse_block(lines: List[_Line], i: int, indent: int) -> Tuple[Any, int]:
    if i < len(lines) and lines[i].content.startswith("- "):
        return _parse_seq(lines, i, indent)
    if i < len(lines) and lines[i].content == "-":
        return _parse_seq(lines, i, indent)
    return _parse_map(lines, i, indent)


def _parse_map(lines: List[_Line], i: int, indent: int) -> Tuple[Dict[str, Any], int]:
    out: Dict[str, Any] = {}
    while i < len(lines):
        ln = lines[i]
        if ln.indent != indent or ln.content.startswith("- ") or ln.content == "-":
            break
        key, rest = _split_key(ln.content)
        if key is None:
            # Not a key line; treat as a stray scalar and move on rather than
            # aborting the whole parse.
            i += 1
            continue
        i += 1
        if rest:
            out[key] = _scalar(rest)
            continue
        # Empty value: the child is either a deeper block or a sequence at the
        # same indent as this key.
        if i < len(lines) and lines[i].indent > indent:
            out[key], i = _parse_block(lines, i, lines[i].indent)
        elif i < len(lines) and lines[i].indent == indent and (
            lines[i].content.startswith("- ") or lines[i].content == "-"
        ):
            out[key], i = _parse_seq(lines, i, indent)
        else:
            out[key] = None
    return out, i


def _parse_seq(lines: List[_Line], i: int, indent: int) -> Tuple[List[Any], int]:
    items: List[Any] = []
    item_indent = indent + 2
    while i < len(lines):
        ln = lines[i]
        if ln.indent != indent:
            break
        if ln.content == "-":
            items.append(None)
            i += 1
            continue
        if not ln.content.startswith("- "):
            break

        first = ln.content[2:].strip()
        key, rest = _split_key(first)
        if key is None:
            items.append(_scalar(first))
            i += 1
            continue

        item: Dict[str, Any] = {}
        i += 1
        if rest:
            item[key] = _scalar(rest)
        else:
            if i < len(lines) and lines[i].indent > item_indent:
                item[key], i = _parse_block(lines, i, lines[i].indent)
            elif i < len(lines) and lines[i].indent == item_indent and (
                lines[i].content.startswith("- ") or lines[i].content == "-"
            ):
                item[key], i = _parse_seq(lines, i, item_indent)
            else:
                item[key] = None
        # Remaining keys of this item, aligned under the "- ".
        if i < len(lines) and lines[i].indent == item_indent and not (
            lines[i].content.startswith("- ") or lines[i].content == "-"
        ):
            rest_map, i = _parse_map(lines, i, item_indent)
            item.update(rest_map)
        items.append(item)
    return items, i


def _split_key(content: str) -> Tuple[Optional[str], str]:
    """Split on the FIRST colon. Values keep every colon they contain."""
    m = _KEY_RE.match(content)
    if not m:
        idx = content.find(":")
        if idx <= 0:
            return None, ""
        return content[:idx].strip(), content[idx + 1 :].strip()
    key = m.group(1).strip()
    return key, content[m.end(1) + 1 :].strip()


_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$")


def _scalar(raw: str) -> Any:
    s = raw.strip()
    if not s:
        return ""
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if len(s) >= 2 and s[0] == "'" and s[-1] == "'":
        return s[1:-1]
    if _INT_RE.match(s):
        # Preserve leading zeros: car number "01" must not become 1.
        body = s.lstrip("+-")
        if len(body) > 1 and body[0] == "0":
            return s
        try:
            return int(s)
        except ValueError:
            return s
    if _FLOAT_RE.match(s):
        try:
            return float(s)
        except ValueError:
            return s
    return s


# ---------------------------------------------------------------------------
# Helpers for values that carry unit suffixes, e.g. "7.00 km", "60.00 kph".
# ---------------------------------------------------------------------------

_NUM_PREFIX = re.compile(r"^\s*([+-]?\d*\.?\d+(?:[eE][+-]?\d+)?)")


def number(value: Any, default: float = 0.0) -> float:
    """Coerce an iRacing YAML value to a float, ignoring any unit suffix."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        m = _NUM_PREFIX.match(value)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                return default
    return default


def dig(obj: Any, *path: Any, default: Any = None) -> Any:
    """Safe nested lookup across dicts and lists."""
    cur = obj
    for step in path:
        if cur is None:
            return default
        if isinstance(step, int):
            if isinstance(cur, list) and -len(cur) <= step < len(cur):
                cur = cur[step]
            else:
                return default
        else:
            if isinstance(cur, dict):
                cur = cur.get(step)
            else:
                return default
    return cur if cur is not None else default
