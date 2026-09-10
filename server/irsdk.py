"""
PitWall - iRacing SDK reader.

Reads iRacing's live telemetry out of the Windows shared-memory mapped file
`Local\\IRSDKMemMapFileName`. Pure stdlib: ctypes only, no third-party packages,
no compiled extensions, no build step.

Design notes that matter for correctness:

  * The sim writes into a rotating set of buffers (numBuf, 3 in practice) and
    bumps `tickCount` AFTER the write completes. We pick the buffer with the
    highest tickCount, copy the WHOLE row (bufLen bytes) into local memory, then
    re-check tickCount. If it moved, the row was torn and we retry.
    Never read individual fields straight out of shared memory: you will get
    CarIdxLap from one tick and CarIdxLapDistPct from the next, and every gap
    on your timing tower will glitch once a lap.

  * The session info string (YAML) is expensive to parse and only changes at
    about 1 Hz or slower. We gate re-parsing on header.sessionInfoUpdate.

  * Array variables (CarIdx*) have a `count` in their var header. Do not
    hardcode 64.

This module is import-safe on Linux and macOS: the Windows bits are only
touched inside `open()`. That lets the rest of PitWall (engine, web server,
demo mode) be developed and run anywhere.
"""

from __future__ import annotations

import ctypes
import mmap
import struct
import sys
from typing import Any, Dict, List, Optional

IS_WINDOWS = sys.platform == "win32"

# ---------------------------------------------------------------------------
# Constants from irsdk_defines.h
# ---------------------------------------------------------------------------

MEMMAPFILE = "Local\\IRSDKMemMapFileName"
DATAVALIDEVENT = "Local\\IRSDKDataValidEvent"
BROADCASTMSGNAME = "IRSDK_BROADCASTMSG"

# pyirsdk maps a fixed region; this is the practical upper bound.
MEMMAPFILESIZE = 1164 * 1024  # 1,191,936 bytes

MAX_BUFS = 4
MAX_STRING = 32
MAX_DESC = 64

UNLIMITED_LAPS = 32767
UNLIMITED_TIME = 604800.0

STATUS_CONNECTED = 1

# Win32
FILE_MAP_READ = 0x0004
SYNCHRONIZE = 0x00100000
WAIT_OBJECT_0 = 0x0
ERROR_FILE_NOT_FOUND = 2

# irsdk_VarType -> (struct code, byte width)
VAR_TYPES = [
    ("c", 1),  # 0 irsdk_char
    ("?", 1),  # 1 irsdk_bool
    ("i", 4),  # 2 irsdk_int
    ("I", 4),  # 3 irsdk_bitField
    ("f", 4),  # 4 irsdk_float
    ("d", 8),  # 5 irsdk_double
]

# Header layout (all int32 unless noted)
_HDR = struct.Struct("<hhiiiiiiiiiiB3x")  # not used directly; see _read_header

VARHEADER_SIZE = 144
VARBUF_SIZE = 16


class VarHeader:
    """One entry of the irsdk_varHeader array. 144 bytes on the wire."""

    __slots__ = ("type", "offset", "count", "count_as_time", "name", "desc", "unit")

    def __init__(self, buf: bytes, base: int):
        (self.type, self.offset, self.count, count_as_time) = struct.unpack_from(
            "<iii?", buf, base
        )
        self.count_as_time = bool(count_as_time)
        self.name = _cstr(buf, base + 16, MAX_STRING)
        self.desc = _cstr(buf, base + 48, MAX_DESC)
        self.unit = _cstr(buf, base + 112, MAX_STRING)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"<VarHeader {self.name} type={self.type} count={self.count}>"


def _cstr(buf: bytes, offset: int, length: int) -> str:
    raw = buf[offset : offset + length]
    nul = raw.find(b"\x00")
    if nul >= 0:
        raw = raw[:nul]
    return raw.decode("latin-1", errors="replace")


class Header:
    __slots__ = (
        "ver",
        "status",
        "tick_rate",
        "session_info_update",
        "session_info_len",
        "session_info_offset",
        "num_vars",
        "var_header_offset",
        "num_buf",
        "buf_len",
        "var_bufs",
    )

    def __init__(self, buf: bytes):
        (
            self.ver,
            self.status,
            self.tick_rate,
            self.session_info_update,
            self.session_info_len,
            self.session_info_offset,
            self.num_vars,
            self.var_header_offset,
            self.num_buf,
            self.buf_len,
        ) = struct.unpack_from("<10i", buf, 0)
        # offsets 40/44 are declared pad1[2] but current sims write
        # curBufTickCount / curBuf there. We deliberately ignore them and use
        # the guaranteed sort-by-tickCount scan instead, for portability.
        self.var_bufs: List[tuple] = []
        for i in range(MAX_BUFS):
            base = 48 + i * VARBUF_SIZE
            tick_count, buf_offset = struct.unpack_from("<ii", buf, base)
            self.var_bufs.append((tick_count, buf_offset))

    @property
    def connected(self) -> bool:
        return bool(self.status & STATUS_CONNECTED)


class IRSDK:
    """Live reader against iRacing's shared memory.

    Typical use::

        sdk = IRSDK()
        while True:
            if not sdk.connected and not sdk.open():
                time.sleep(1.0); continue
            if sdk.wait_for_tick(timeout_ms=32):
                snap = sdk.snapshot()       # dict of var name -> value
                if sdk.session_info_dirty:
                    yaml_text = sdk.session_info_raw()
    """

    def __init__(self) -> None:
        self._mm: Optional[mmap.mmap] = None
        self._event = None
        self._header: Optional[Header] = None
        self._var_headers: Dict[str, VarHeader] = {}
        self._last_tick = -1
        self._last_session_update = -1
        self._row: bytes = b""
        self._num_vars_seen = -1
        self.connected = False
        self.session_info_dirty = False
        self.last_error: Optional[str] = None

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> bool:
        """Try to attach to the sim. Returns True if attached and connected.

        Safe to call in a loop once per second; cheap when the sim is absent.
        """
        if not IS_WINDOWS:
            self.last_error = (
                "iRacing's SDK is Windows-only. Run PitWall on the machine "
                "running iRacing, or use --demo to preview without the sim."
            )
            return False

        if self._mm is None:
            try:
                k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                handle = k32.OpenFileMappingW(FILE_MAP_READ, False, MEMMAPFILE)
                if not handle:
                    err = ctypes.get_last_error() if False else k32.GetLastError()
                    if err == ERROR_FILE_NOT_FOUND:
                        self.last_error = "iRacing is not running."
                    else:
                        self.last_error = f"OpenFileMapping failed (Win32 error {err})."
                    return False
                k32.CloseHandle(handle)
                self._mm = mmap.mmap(-1, MEMMAPFILESIZE, MEMMAPFILE, access=mmap.ACCESS_READ)
            except Exception as exc:  # pragma: no cover - windows only
                self.last_error = f"Could not map iRacing shared memory: {exc}"
                self._mm = None
                return False

        if self._event is None:
            try:
                k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
                self._event = k32.OpenEventW(SYNCHRONIZE, False, DATAVALIDEVENT)
            except Exception:  # pragma: no cover
                self._event = None

        if not self._refresh_header():
            return False

        self.connected = bool(self._header and self._header.connected)
        if self.connected:
            self.last_error = None
        else:
            self.last_error = "iRacing is running but not producing telemetry yet."
        return self.connected

    def close(self) -> None:
        if self._event and IS_WINDOWS:
            try:
                ctypes.windll.kernel32.CloseHandle(self._event)  # type: ignore[attr-defined]
            except Exception:
                pass
        self._event = None
        if self._mm is not None:
            try:
                self._mm.close()
            except Exception:
                pass
        self._mm = None
        self._header = None
        self._var_headers = {}
        self._last_tick = -1
        self._last_session_update = -1
        self.connected = False

    # -- header / var headers ---------------------------------------------

    def _refresh_header(self) -> bool:
        if self._mm is None:
            return False
        try:
            raw = self._mm[0:112]
        except (ValueError, OSError) as exc:
            self.last_error = f"Shared memory went away: {exc}"
            self.close()
            return False
        hdr = Header(raw)
        if hdr.ver != 2 and hdr.ver != 0:
            # Unexpected but not fatal; iRacing has only shipped ver 2.
            pass
        self._header = hdr
        if hdr.num_vars != self._num_vars_seen and hdr.num_vars > 0:
            self._load_var_headers()
        return True

    def _load_var_headers(self) -> None:
        assert self._mm is not None and self._header is not None
        hdr = self._header
        size = hdr.num_vars * VARHEADER_SIZE
        try:
            blob = self._mm[hdr.var_header_offset : hdr.var_header_offset + size]
        except (ValueError, OSError):
            return
        headers: Dict[str, VarHeader] = {}
        for i in range(hdr.num_vars):
            try:
                vh = VarHeader(blob, i * VARHEADER_SIZE)
            except struct.error:
                continue
            if vh.name:
                headers[vh.name] = vh
        self._var_headers = headers
        self._num_vars_seen = hdr.num_vars

    @property
    def var_headers(self) -> Dict[str, VarHeader]:
        return self._var_headers

    # -- the read loop -----------------------------------------------------

    def wait_for_tick(self, timeout_ms: int = 32) -> bool:
        """Block until the sim publishes a new frame, then latch it.

        Returns True when a fresh, untorn row is available via `get()` /
        `snapshot()`.
        """
        if self._mm is None:
            return False
        if self._event and IS_WINDOWS:
            try:
                ctypes.windll.kernel32.WaitForSingleObject(self._event, timeout_ms)  # type: ignore[attr-defined]
            except Exception:
                pass
        return self.latch()

    def latch(self) -> bool:
        """Copy the newest complete buffer row into local memory."""
        if self._mm is None:
            return False
        if not self._refresh_header():
            return False
        hdr = self._header
        assert hdr is not None

        if not hdr.connected:
            self.connected = False
            self._last_tick = -1
            return False
        self.connected = True

        n = max(1, min(hdr.num_buf, MAX_BUFS))
        latest = 0
        for i in range(1, n):
            if hdr.var_bufs[i][0] > hdr.var_bufs[latest][0]:
                latest = i
        tick, offset = hdr.var_bufs[latest]

        if tick <= self._last_tick:
            if tick < self._last_tick:
                # Sim restarted / session reset; resync.
                self._last_tick = -1
            return False

        # Two attempts: copy the row, then verify the tick did not move under us.
        for _ in range(2):
            before = hdr.var_bufs[latest][0]
            try:
                row = self._mm[offset : offset + hdr.buf_len]
            except (ValueError, OSError):
                return False
            check = Header(self._mm[0:112])
            after = check.var_bufs[latest][0]
            if before == after:
                self._row = row
                self._last_tick = after
                self.session_info_dirty = (
                    hdr.session_info_update != self._last_session_update
                )
                if self.session_info_dirty:
                    self._last_session_update = hdr.session_info_update
                return True
            hdr = check
            self._header = check
            tick, offset = hdr.var_bufs[latest]
        return False

    # -- value access ------------------------------------------------------

    def get(self, name: str, index: Optional[int] = None) -> Any:
        """Read one variable from the latched row.

        `index` selects one element of an array variable; omit it to get the
        whole list.
        """
        vh = self._var_headers.get(name)
        if vh is None or not self._row:
            return None
        code, width = VAR_TYPES[vh.type] if 0 <= vh.type < len(VAR_TYPES) else ("i", 4)

        if vh.count == 1:
            try:
                (val,) = struct.unpack_from("<" + code, self._row, vh.offset)
            except struct.error:
                return None
            return _post(code, val)

        if index is not None:
            if index < 0 or index >= vh.count:
                return None
            try:
                (val,) = struct.unpack_from("<" + code, self._row, vh.offset + index * width)
            except struct.error:
                return None
            return _post(code, val)

        try:
            vals = struct.unpack_from("<" + code * vh.count, self._row, vh.offset)
        except struct.error:
            return None
        if code == "c":
            return [v.decode("latin-1", "replace") for v in vals]
        return list(vals)

    def snapshot(self, names: Optional[Any] = None) -> Dict[str, Any]:
        """Read many variables from the single latched row (one coherent tick).

        Pass a set of names to read only those. iRacing publishes ~300
        variables and unpacking all of them 60 times a second is real CPU on
        the machine that is also running the sim.
        """
        out: Dict[str, Any] = {}
        if names is None:
            keys = list(self._var_headers.keys())
        else:
            # Intersect with what this session actually publishes, so a name
            # that only exists on some cars is skipped rather than erroring.
            keys = [k for k in self._var_headers if k in names]
        for k in keys:
            v = self.get(k)
            if v is not None:
                out[k] = v
        return out

    def car_count(self, name: str = "CarIdxLapDistPct") -> int:
        vh = self._var_headers.get(name)
        return vh.count if vh else 64

    # -- session info string ----------------------------------------------

    def session_info_raw(self) -> str:
        """Return the session info YAML string, decoded.

        iRacing emits cp1252 by default. If app.ini has irsdkUTF8SessionStr=1
        the string starts with an Encoding: UTF8 marker.
        """
        if self._mm is None or self._header is None:
            return ""
        hdr = self._header
        try:
            raw = self._mm[hdr.session_info_offset : hdr.session_info_offset + hdr.session_info_len]
        except (ValueError, OSError):
            return ""
        nul = raw.find(b"\x00")
        if nul >= 0:
            raw = raw[:nul]
        head = raw[:120]
        if b"Encoding: UTF8" in head or b"Encoding: utf8" in head:
            return raw.decode("utf-8", errors="replace")
        return raw.decode("cp1252", errors="replace")

    @property
    def tick_rate(self) -> int:
        return self._header.tick_rate if self._header else 60

    @property
    def session_info_update(self) -> int:
        return self._header.session_info_update if self._header else -1


def _post(code: str, val: Any) -> Any:
    if code == "c":
        return val.decode("latin-1", "replace") if isinstance(val, bytes) else val
    return val


# ---------------------------------------------------------------------------
# Is the sim running? Cheap out-of-band probe that does not touch shared memory.
# ---------------------------------------------------------------------------

def sim_is_running(timeout: float = 0.25) -> Optional[bool]:
    """Probe iRacing's local status endpoint.

    Returns True/False, or None if the probe itself could not be performed.
    """
    try:
        import urllib.request

        with urllib.request.urlopen(
            "http://127.0.0.1:32034/get_sim_status?object=simStatus", timeout=timeout
        ) as resp:
            body = resp.read().decode("latin-1", "replace")
        return "running:1" in body
    except Exception:
        return None
