"""
PitWall - environment self-check.

The single most common failure across every iRacing overlay tool is: the user
installs it, nothing appears, and the app says nothing useful. The causes are
always the same short list, and every one of them is detectable.

So PitWall checks them and tells you which one it is, in words, on the control
panel, before you waste an evening.
"""

from __future__ import annotations

import ctypes
import os
import socket
import subprocess
import sys
from typing import Any, Dict, List, Optional

IS_WINDOWS = sys.platform == "win32"

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"


def _check(name: str, status: str, detail: str, fix: str = "") -> Dict[str, str]:
    return {"name": name, "status": status, "detail": detail, "fix": fix}


def iracing_docs_dir() -> Optional[str]:
    if not IS_WINDOWS:
        return None
    candidates = []
    up = os.environ.get("USERPROFILE")
    if up:
        candidates.append(os.path.join(up, "Documents", "iRacing"))
        candidates.append(os.path.join(up, "OneDrive", "Documents", "iRacing"))
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None


def _read_ini(path: str) -> Dict[str, str]:
    """iRacing's ini files are flat enough that a naive reader is fine."""
    out: Dict[str, str] = {}
    try:
        with open(path, "r", encoding="latin-1", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith((";", "#", "[")):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    out[k.strip()] = v.split(";")[0].strip()
    except OSError:
        pass
    return out


def run(port: int, sdk_connected: bool, sdk_error: Optional[str], demo: bool) -> Dict[str, Any]:
    checks: List[Dict[str, str]] = []

    # --- platform -------------------------------------------------------
    if demo:
        checks.append(
            _check(
                "Data source",
                INFO,
                "Running in demo mode with a synthetic 24-car race.",
                "Restart without --demo to read live iRacing telemetry.",
            )
        )
    if not IS_WINDOWS:
        checks.append(
            _check(
                "Operating system",
                FAIL if not demo else WARN,
                f"This machine is {sys.platform}. iRacing's telemetry SDK is Windows-only.",
                "Run PitWall on the Windows PC that runs iRacing. Everything "
                "else (overlays, timing page, roster) works from any machine.",
            )
        )
        checks.append(_check("Web server", OK, f"Listening on port {port}.", ""))
        return {"checks": checks, "summary": _summarise(checks)}

    # --- sim running ----------------------------------------------------
    from .irsdk import sim_is_running

    running = sim_is_running()
    if running is True:
        checks.append(_check("iRacing running", OK, "The sim is running.", ""))
    elif running is False:
        checks.append(
            _check(
                "iRacing running",
                WARN,
                "iRacing is installed and its local service answered, but the sim "
                "is not in a session.",
                "Join a session, a test drive, or open a replay.",
            )
        )
    else:
        checks.append(
            _check(
                "iRacing running",
                WARN if not sdk_connected else OK,
                "Could not reach iRacing's local status service on port 32034.",
                "Harmless if telemetry is connecting anyway.",
            )
        )

    # --- shared memory --------------------------------------------------
    if sdk_connected:
        checks.append(_check("Telemetry", OK, "Reading iRacing's shared memory.", ""))
    else:
        checks.append(
            _check(
                "Telemetry",
                FAIL if not demo else WARN,
                sdk_error or "Not connected to iRacing's shared memory.",
                "Start iRacing and join a session. If it is already running, "
                "check irsdkEnableMem below.",
            )
        )

    docs = iracing_docs_dir()
    if not docs:
        checks.append(
            _check(
                "iRacing settings folder",
                WARN,
                "Could not find Documents\\iRacing.",
                "If your Documents folder is redirected (OneDrive), some checks "
                "below will be skipped. Nothing is broken.",
            )
        )
    else:
        # --- app.ini -----------------------------------------------------
        app_ini = os.path.join(docs, "app.ini")
        app = _read_ini(app_ini)
        mem = app.get("irsdkEnableMem")
        if mem is None:
            checks.append(
                _check(
                    "irsdkEnableMem",
                    INFO,
                    "Not set in app.ini, which means it is at its default of 1 (on).",
                    "",
                )
            )
        elif mem.strip() in ("0", "false"):
            checks.append(
                _check(
                    "irsdkEnableMem",
                    FAIL,
                    "app.ini has irsdkEnableMem=0. iRacing is publishing no live "
                    "telemetry at all, to any overlay tool.",
                    f"Set irsdkEnableMem=1 in {app_ini}, then restart iRacing.",
                )
            )
        else:
            checks.append(_check("irsdkEnableMem", OK, "Live telemetry is enabled in app.ini.", ""))

        max_cars = app.get("maxCarsToDraw")
        if max_cars and max_cars.isdigit() and int(max_cars) < 63:
            checks.append(
                _check(
                    "Cars drawn",
                    WARN,
                    f"app.ini maxCarsToDraw={max_cars}.",
                    "This only affects rendering, not timing. But also check "
                    "Account > Max Cars on the iRacing website is 63, or position "
                    "data for the back of the field can be unreliable.",
                )
            )

        # --- renderer.ini: fullscreen and border -------------------------
        for rname in ("rendererDX11.ini", "rendererDX11OpenXR.ini"):
            rpath = os.path.join(docs, rname)
            if not os.path.isfile(rpath):
                continue
            r = _read_ini(rpath)
            fullscreen = r.get("FullScreen", r.get("Fullscreen"))
            border = r.get("BorderlessWindow", r.get("Border"))
            if fullscreen and fullscreen.strip() == "1":
                checks.append(
                    _check(
                        "Display mode",
                        WARN,
                        f"{rname} has FullScreen=1. Windows gives exclusive "
                        "fullscreen the whole screen, so no desktop overlay of any "
                        "kind can draw on top of it.",
                        "In iRacing: Options > Graphics, turn Full Screen off and "
                        "Border off, then restart iRacing. The OBS and second-screen "
                        "outputs work regardless.",
                    )
                )
            elif fullscreen is not None:
                checks.append(
                    _check("Display mode", OK, f"{rname}: windowed. Overlays can draw on top.", "")
                )
            if border and border.strip() == "1":
                checks.append(
                    _check(
                        "Window border",
                        WARN,
                        f"{rname} has a window border enabled.",
                        "Turn Border off in iRacing's graphics options for a clean "
                        "borderless window, then restart iRacing.",
                    )
                )

    # --- DPI scaling ----------------------------------------------------
    try:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # type: ignore[attr-defined]
        except Exception:
            pass
        hdc = user32.GetDC(0)
        gdi = ctypes.windll.gdi32  # type: ignore[attr-defined]
        LOGPIXELSX = 88
        dpi = gdi.GetDeviceCaps(hdc, LOGPIXELSX)
        user32.ReleaseDC(0, hdc)
        scale = round(dpi / 96.0 * 100)
        if scale != 100:
            checks.append(
                _check(
                    "Display scaling",
                    WARN,
                    f"Windows display scaling is {scale}%.",
                    "Overlay positions drift at anything other than 100%. Set "
                    "Settings > System > Display > Scale to 100%, or expect to "
                    "nudge HUD widgets after each restart.",
                )
            )
        else:
            checks.append(_check("Display scaling", OK, "100%. Overlay positions will be stable.", ""))
    except Exception:
        pass

    # --- elevation ------------------------------------------------------
    try:
        elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        if elevated:
            checks.append(
                _check(
                    "Elevation",
                    INFO,
                    "PitWall is running as administrator.",
                    "Fine. If iRacing is NOT elevated, prefer running both the same "
                    "way so window messages (camera control) are not blocked by UAC.",
                )
            )
        else:
            checks.append(
                _check(
                    "Elevation",
                    OK,
                    "PitWall is running unelevated, which is the normal case.",
                    "If iRacing runs as administrator, camera and replay control will "
                    "silently do nothing. Run PitWall as administrator too, or stop "
                    "running iRacing elevated.",
                )
            )
    except Exception:
        pass

    # --- GPUs -----------------------------------------------------------
    gpus = _list_gpus()
    if len(gpus) > 1:
        checks.append(
            _check(
                "Graphics adapters",
                WARN,
                "More than one GPU is present: " + ", ".join(gpus) + ".",
                "If Windows composites the desktop on the integrated GPU while "
                "iRacing renders on the discrete one, every overlay frame is copied "
                "across the bus and the sim will stutter. Disable the iGPU in Device "
                "Manager, or force both apps onto the discrete GPU in Windows "
                "Graphics settings.",
            )
        )
    elif gpus:
        checks.append(_check("Graphics adapters", OK, gpus[0], ""))

    checks.append(
        _check(
            "G-SYNC / VRR",
            INFO,
            "Cannot be read reliably from here.",
            "If the sim stutters only when overlays are on: set G-SYNC to "
            "'fullscreen only' globally, then use NVIDIA Profile Inspector to force "
            "'fullscreen and windowed' for iRacing specifically. This is the single "
            "most common cause of overlay stutter and it is not PitWall's fault "
            "or any other overlay's.",
        )
    )

    # --- network --------------------------------------------------------
    checks.append(_check("Web server", OK, f"Listening on port {port}.", ""))
    return {"checks": checks, "summary": _summarise(checks)}


def _list_gpus() -> List[str]:
    if not IS_WINDOWS:
        return []
    try:
        out = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "(Get-CimInstance Win32_VideoController).Name",
            ],
            capture_output=True, text=True, timeout=6,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return [l.strip() for l in out.stdout.splitlines() if l.strip()]
    except Exception:
        return []


def _summarise(checks: List[Dict[str, str]]) -> Dict[str, Any]:
    fails = [c for c in checks if c["status"] == FAIL]
    warns = [c for c in checks if c["status"] == WARN]
    if fails:
        return {"status": FAIL, "headline": fails[0]["detail"], "fix": fails[0]["fix"]}
    if warns:
        return {"status": WARN, "headline": warns[0]["detail"], "fix": warns[0]["fix"]}
    return {"status": OK, "headline": "Everything checks out.", "fix": ""}


def port_free(port: int, host: str = "0.0.0.0") -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()
