"""
PitWall - remote control of the sim via irsdk_broadcastMsg.

This is iRacing's own sanctioned control surface: camera selection, replay
transport, pit service checkboxes, chat macros, telemetry recording, FFB.
It is a Windows broadcast window message, not memory writing or injection.

wParam = (var1 << 16) | msg
lParam = var2 | (var3 << 16)      (or a full 32-bit var2)

Notes that bite people:

  * Use SendNotifyMessage, never SendMessage. A blocking SendMessage to
    HWND_BROADCAST hangs your UI thread on the first unresponsive app on the
    machine.
  * There is no acknowledgement. Verify camera changes by reading back
    CamCarIdx / CamGroupNumber / CamCameraNumber.
  * There is no rate limiting either. Spamming CamSwitchNum every frame makes
    the director camera stutter, so this module debounces.
  * If iRacing runs elevated and PitWall does not, UAC's UIPI silently drops
    every message. Match the sim's elevation.
"""

from __future__ import annotations

import ctypes
import sys
import time
from typing import Optional

IS_WINDOWS = sys.platform == "win32"
HWND_BROADCAST = 0xFFFF

# irsdk_BroadcastMsg
CAM_SWITCH_POS = 0
CAM_SWITCH_NUM = 1
CAM_SET_STATE = 2
REPLAY_SET_PLAY_SPEED = 3
REPLAY_SET_PLAY_POSITION = 4
REPLAY_SEARCH = 5
REPLAY_SET_STATE = 6
RELOAD_TEXTURES = 7
CHAT_COMMAND = 8
PIT_COMMAND = 9
TELEM_COMMAND = 10
FFB_COMMAND = 11
REPLAY_SEARCH_SESSION_TIME = 12
VIDEO_CAPTURE = 13

# CamSwitchMode
FOCUS_AT_INCIDENT = -3
FOCUS_AT_LEADER = -2
FOCUS_AT_EXCITING = -1
FOCUS_AT_DRIVER = 0

# RpySrchMode
RPY_SEARCH = {
    "toStart": 0, "toEnd": 1, "prevSession": 2, "nextSession": 3,
    "prevLap": 4, "nextLap": 5, "prevFrame": 6, "nextFrame": 7,
    "prevIncident": 8, "nextIncident": 9,
}
RPY_POS = {"begin": 0, "current": 1, "end": 2}

PIT_COMMANDS = {
    "clear": 0, "tearoff": 1, "fuel": 2, "lf": 3, "rf": 4, "lr": 5, "rr": 6,
    "clearTires": 7, "fastRepair": 8, "clearWS": 9, "clearFR": 10,
    "clearFuel": 11, "tireCompound": 12,
}

CHAT_COMMANDS = {"macro": 0, "beginChat": 1, "reply": 2, "cancel": 3}
TELEM_COMMANDS = {"stop": 0, "start": 1, "restart": 2}
VIDEO_COMMANDS = {
    "screenshot": 0, "startCapture": 1, "endCapture": 2, "toggleCapture": 3,
    "showTimer": 4, "hideTimer": 5,
}


class Control:
    def __init__(self) -> None:
        self._msg_id: Optional[int] = None
        self._last_cam = 0.0
        self.available = IS_WINDOWS
        self.last_error: Optional[str] = None

    def _id(self) -> Optional[int]:
        if not IS_WINDOWS:
            return None
        if self._msg_id is None:
            try:
                self._msg_id = ctypes.windll.user32.RegisterWindowMessageW(  # type: ignore[attr-defined]
                    "IRSDK_BROADCASTMSG"
                )
            except Exception as exc:  # pragma: no cover
                self.last_error = str(exc)
                return None
        return self._msg_id

    def _send(self, msg: int, var1: int = 0, var2: int = 0, var3: Optional[int] = None) -> bool:
        mid = self._id()
        if not mid:
            self.last_error = "Sim control needs Windows and a running iRacing."
            return False
        wparam = (int(var1) & 0xFFFF) << 16 | (int(msg) & 0xFFFF)
        if var3 is None:
            lparam = int(var2) & 0xFFFFFFFF
        else:
            lparam = (int(var2) & 0xFFFF) | ((int(var3) & 0xFFFF) << 16)
        try:
            ctypes.windll.user32.SendNotifyMessageW(  # type: ignore[attr-defined]
                HWND_BROADCAST, mid, wparam, lparam
            )
            return True
        except Exception as exc:  # pragma: no cover
            self.last_error = str(exc)
            return False

    def _send_float(self, msg: int, var1: int, value: float) -> bool:
        return self._send(msg, var1, int(value * 65536.0))

    # -- camera ----------------------------------------------------------

    def camera_by_position(self, position: int, group: int = 0, camera: int = 0) -> bool:
        if not self._debounce_cam():
            return False
        return self._send(CAM_SWITCH_POS, position, group, camera)

    def camera_by_number(self, car_number: str, group: int = 0, camera: int = 0) -> bool:
        """Switch to a car by its number, preserving leading zeros."""
        if not self._debounce_cam():
            return False
        return self._send(CAM_SWITCH_NUM, _pad_car_num(car_number), group, camera)

    def camera_focus(self, mode: str, group: int = 0, camera: int = 0) -> bool:
        m = {
            "leader": FOCUS_AT_LEADER,
            "incident": FOCUS_AT_INCIDENT,
            "exciting": FOCUS_AT_EXCITING,
        }.get(mode)
        if m is None:
            return False
        if not self._debounce_cam():
            return False
        return self._send(CAM_SWITCH_POS, m & 0xFFFF, group, camera)

    def camera_state(self, state: int) -> bool:
        return self._send(CAM_SET_STATE, state)

    def _debounce_cam(self, gap: float = 0.25) -> bool:
        now = time.time()
        if now - self._last_cam < gap:
            return False
        self._last_cam = now
        return True

    # -- replay ----------------------------------------------------------

    def replay_speed(self, speed: int, slow_motion: bool = False) -> bool:
        return self._send(REPLAY_SET_PLAY_SPEED, speed, 1 if slow_motion else 0)

    def replay_position(self, mode: str, frame: int) -> bool:
        return self._send(REPLAY_SET_PLAY_POSITION, RPY_POS.get(mode, 1), frame)

    def replay_search(self, what: str) -> bool:
        m = RPY_SEARCH.get(what)
        if m is None:
            return False
        return self._send(REPLAY_SEARCH, m)

    def replay_to_session_time(self, session_num: int, seconds: float) -> bool:
        return self._send(REPLAY_SEARCH_SESSION_TIME, session_num, int(seconds * 1000))

    def replay_erase_tape(self) -> bool:
        return self._send(REPLAY_SET_STATE, 0)

    # -- pit / chat / telemetry / capture --------------------------------

    def pit(self, command: str, value: int = 0) -> bool:
        c = PIT_COMMANDS.get(command)
        if c is None:
            return False
        return self._send(PIT_COMMAND, c, value)

    def chat(self, command: str, sub: int = 0) -> bool:
        c = CHAT_COMMANDS.get(command)
        if c is None:
            return False
        return self._send(CHAT_COMMAND, c, sub)

    def telemetry(self, command: str) -> bool:
        c = TELEM_COMMANDS.get(command)
        if c is None:
            return False
        return self._send(TELEM_COMMAND, c)

    def video(self, command: str) -> bool:
        c = VIDEO_COMMANDS.get(command)
        if c is None:
            return False
        return self._send(VIDEO_CAPTURE, c)

    def ffb_max_force(self, nm: float) -> bool:
        return self._send_float(FFB_COMMAND, 0, nm)

    def reload_textures(self, car_idx: Optional[int] = None) -> bool:
        if car_idx is None:
            return self._send(RELOAD_TEXTURES, 0)
        return self._send(RELOAD_TEXTURES, 1, car_idx)


def _pad_car_num(number: str) -> int:
    """Encode a car number so leading zeros survive.

    #1 -> 1, #01 -> 1001, #001 -> 2001. Mirrors irsdk_padCarNum.
    """
    s = str(number).strip()
    if not s:
        return 0
    zeros = len(s) - len(s.lstrip("0"))
    try:
        num = int(s)
    except ValueError:
        return 0
    if zeros == 0:
        return num
    places = len(str(num)) if num > 0 else 1
    return num + 1000 * (places + zeros)
