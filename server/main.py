"""
PitWall - entry point.

    python -m server.main              read live iRacing telemetry
    python -m server.main --demo       synthetic race, no sim needed
    python -m server.main --port 8099 --host 0.0.0.0

One process. One port. Everything else is a browser tab.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import webbrowser
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FROZEN = getattr(sys, "frozen", False)


def bundle_root() -> str:
    """Where the read-only shipped files live (the web/ folder).

    In a PyInstaller one-file build these are unpacked to a temporary
    directory that is deleted on exit, so never write here.
    """
    if FROZEN:
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return ROOT


def workspace_root() -> str:
    """Where things we WRITE live: the roster, uploads, cached track maps and
    the config file.

    Next to the .exe, so a league admin can see and back up their data, and so
    it survives an upgrade to a newer .exe dropped in the same folder.
    """
    if FROZEN:
        return os.path.dirname(os.path.abspath(sys.executable))
    return ROOT

from server import diagnostics  # noqa: E402
from server.control import Control  # noqa: E402
from server.demo import DemoFeed  # noqa: E402
from server.engine import Engine  # noqa: E402
from server.irsdk import IRSDK  # noqa: E402
from server.leagues import LeagueStore  # noqa: E402
from server.roster import RosterStore  # noqa: E402
from server.sessionyaml import parse as parse_session  # noqa: E402
from server.wanted import WANTED  # noqa: E402
from server.trackmap import TrackMapBuilder, synthetic as synthetic_map  # noqa: E402
from server.webserver import Server, lan_addresses  # noqa: E402

DEFAULT_CONFIG = {
    "port": 8099,
    "host": "0.0.0.0",
    "tickRate": 20,
    "openBrowser": True,
    "league": {
        "name": "",
        "logo": "",
        "accent": "#e8443a",
        "secondary": "#12b8ff",
        "sponsorBar": [],
        "hashtag": "",
    },
    "battleThreshold": 1.2,
    "roster": {
        "mode": "local",
        "repo": "",
        "branch": "main",
        "path": "roster.json",
        "url": "",
        "token": "",
        "autoPullSeconds": 0,
    },
}


def load_config(path: str) -> Dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                user = json.load(fh)
            _deep_update(cfg, user)
        except Exception as exc:
            print(f"[PitWall] Could not read {path}: {exc}. Using defaults.")
    return cfg


def _deep_update(base: Dict[str, Any], extra: Dict[str, Any]) -> None:
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v


class PitWall:
    def __init__(self, args) -> None:
        self.args = args
        self.config_path = args.config or os.path.join(workspace_root(), "config", "pitwall.json")
        self.config = load_config(self.config_path)
        if args.port:
            self.config["port"] = args.port
        if args.host:
            self.config["host"] = args.host

        self.data_dir = args.data or os.path.join(workspace_root(), "data")
        os.makedirs(self.data_dir, exist_ok=True)

        self.demo = args.demo
        self.roster = RosterStore(self.data_dir, self.config)
        self.leagues = LeagueStore(self.data_dir)
        self.engine = Engine(self.config)
        self.engine.roster_lookup = self.roster.lookup
        self.control = Control()
        self.maps = TrackMapBuilder(self.data_dir)

        self.sdk: Optional[IRSDK] = None
        self.feed: Optional[DemoFeed] = None
        self.running = True
        self.stats = {"ticks": 0, "sent": 0, "started": time.time()}
        self.last_vars: Dict[str, Any] = {}
        self.connected = False
        self.sdk_error: Optional[str] = None

        self.web_root = os.path.join(bundle_root(), "web")
        self.server: Optional[Server] = None

    def _build_server(self) -> bool:
        """Bind the port. Returns False with a readable explanation if taken."""
        port = int(self.config["port"])
        try:
            self.server = Server(self.config["host"], port, self.web_root, self.data_dir)
        except OSError as exc:
            print(
                f"\n[PitWall] Could not listen on port {port}: {exc}\n"
                f"          Another copy of PitWall is probably already running.\n"
                f"          Close it, or start this one with:  --port {port + 1}\n"
            )
            return False
        self.server.hub.hello = {
            "app": "PitWall",
            "version": "1.2.0",
            "demo": self.demo,
            "league": self.config.get("league", {}),
            "event": self.leagues.event_payload(),
            "controlAvailable": self.control.available,
        }
        self._routes()
        return True

    # -- HTTP / WS routes -------------------------------------------------

    def _save_config(self) -> None:
        """Write config to disk. Used by the settings form and by activating
        a league profile, which changes the same file."""
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, "w", encoding="utf-8") as fh:
            json.dump(self.config, fh, indent=2)

    def _routes(self) -> None:
        r = self.server.router

        def api_status(h, q):
            h._json(
                {
                    "connected": self.connected,
                    "demo": self.demo,
                    "error": self.sdk_error,
                    "clients": self.server.hub.count(),
                    "ticks": self.stats["ticks"],
                    "uptime": round(time.time() - self.stats["started"], 1),
                    "league": self.config.get("league", {}),
                    "leagues": self.leagues.status(),
                    "urls": lan_addresses(int(self.config["port"])),
                    "roster": self.roster.status(),
                    "controlAvailable": self.control.available,
                    "trackmap": self.maps.status(),
                }
            )

        def api_diag(h, q):
            h._json(
                diagnostics.run(
                    int(self.config["port"]), self.connected, self.sdk_error, self.demo
                )
            )

        def api_state(h, q):
            if not self.last_vars:
                return h._json({"error": "no data yet"}, 503)
            h._json(
                {
                    "meta": self.engine.build_meta(),
                    "tick": self.engine.build_state(self.last_vars),
                }
            )

        def api_config_get(h, q):
            safe = json.loads(json.dumps(self.config))
            safe.get("roster", {}).pop("token", None)
            h._json(safe)

        def api_config_set(h, body):
            _deep_update(self.config, body or {})
            try:
                self._save_config()
            except OSError as exc:
                return h._json({"error": str(exc)}, 500)
            self.server.hub.hello["league"] = self.config.get("league", {})
            self.roster.config = self.config.get("roster", {})
            self.engine.config = self.config
            h._json({"ok": True})

        # -- roster -------------------------------------------------------

        def roster_get(h, q):
            h._json({"drivers": self.roster.entries, "status": self.roster.status()})

        def roster_submit(h, body):
            entry = self.roster.submit(body or {})
            self.engine.meta_serial += 1
            self.engine.apply_session_info(self.engine.session_info)
            h._json({"ok": True, "entry": entry})

        def roster_delete(h, q):
            cid = (q.get("id") or [""])[0]
            h._json({"ok": self.roster.delete(cid)})

        def roster_pull(h, body):
            res = self.roster.pull()
            if res.get("ok"):
                self.engine.apply_session_info(self.engine.session_info)
            h._json(res)

        def roster_push(h, body):
            h._json(self.roster.push((body or {}).get("message")))

        def roster_import(h, body):
            drivers = (body or {}).get("drivers")
            if not isinstance(drivers, list):
                raise ValueError("Expected a 'drivers' list.")
            self.roster.replace_all(drivers)
            self.engine.apply_session_info(self.engine.session_info)
            h._json({"ok": True, "count": len(drivers)})

        # -- sim control ---------------------------------------------------

        def api_control(h, body):
            action = (body or {}).get("action", "")
            ok = self._do_control(action, body or {})
            h._json({"ok": ok, "error": None if ok else self.control.last_error})

        def api_trackmap(h, q):
            if self.demo:
                return h._json(synthetic_map(self.engine.track.get("name") or "Demo Circuit"))
            data = self.maps.current()
            if not data:
                return h._json({"error": "no map yet", "status": self.maps.status()}, 404)
            h._json(data)

        # -- league profiles ----------------------------------------------

        def _push_league_change():
            """
            Tell every connected overlay the branding and the event changed,
            so a scene switch is not needed to pick it up. Clients handle a
            "hello" already, and everything subscribes to meta by default.
            """
            self.server.hub.hello["league"] = self.config.get("league", {})
            self.server.hub.hello["event"] = self.leagues.event_payload()
            self.server.hub.broadcast("meta", {"type": "hello", **self.server.hub.hello})

        def _apply_league(league):
            """Push a saved profile into the live config: colours, then roster."""
            lg = self.config.setdefault("league", {})
            lg["name"] = league.get("name") or ""
            lg["accent"] = league.get("accent") or lg.get("accent")
            lg["secondary"] = league.get("secondary") or lg.get("secondary")
            lg["logo"] = league.get("logo") or ""
            lg["hashtag"] = league.get("hashtag") or ""

            pulled = None
            url = (league.get("rosterUrl") or "").strip()
            if url:
                rc = self.config.setdefault("roster", {})
                rc["mode"] = "url"
                rc["url"] = url
                self.roster.config = rc
                try:
                    pulled = self.roster.pull()
                except Exception as exc:
                    # A roster that will not download must not stop the
                    # broadcast: names and numbers come from the sim anyway.
                    pulled = {"error": str(exc)}

            self._save_config()
            self.engine.config = self.config
            self.engine.meta_serial += 1
            self.engine.apply_session_info(self.engine.session_info)
            _push_league_change()
            return pulled

        def leagues_get(h, q):
            h._json(
                {
                    "leagues": self.leagues.leagues,
                    "active": self.leagues.active_id,
                    "event": self.leagues.event_payload(),
                    "suggested": {
                        l["id"]: self.leagues.suggest_round(l) for l in self.leagues.leagues
                    },
                }
            )

        def leagues_save(h, body):
            saved = self.leagues.upsert(body or {})
            # Saving the league you are currently broadcasting applies the
            # edit immediately. That is the whole point of being able to fix
            # a track name at five to eight.
            if saved["id"] == self.leagues.active_id:
                _apply_league(saved)
            h._json({"ok": True, "league": saved})

        def leagues_delete(h, body):
            lid = str((body or {}).get("id") or "")
            h._json({"ok": self.leagues.delete(lid)})

        def leagues_duplicate(h, body):
            copy = self.leagues.duplicate(str((body or {}).get("id") or ""))
            if not copy:
                return h._json({"error": "No league profile with that name is saved."}, 404)
            h._json({"ok": True, "league": copy})

        def leagues_activate(h, body):
            body = body or {}
            rnd = body.get("round")
            league = self.leagues.activate(
                str(body.get("id") or ""), int(rnd) if rnd not in (None, "") else None
            )
            pulled = _apply_league(league)
            h._json(
                {
                    "ok": True,
                    "league": league,
                    "event": self.leagues.event_payload(),
                    "roster": pulled,
                    "rosterStatus": self.roster.status(),
                }
            )

        def leagues_round(h, body):
            rnd = (body or {}).get("round")
            self.leagues.set_round(int(rnd) if rnd not in (None, "") else None)
            _push_league_change()
            h._json({"ok": True, "event": self.leagues.event_payload()})

        r.add(("GET", "/api/leagues"), leagues_get)
        r.add(("POST", "/api/leagues"), leagues_save)
        r.add(("POST", "/api/leagues/delete"), leagues_delete)
        r.add(("POST", "/api/leagues/duplicate"), leagues_duplicate)
        r.add(("POST", "/api/leagues/activate"), leagues_activate)
        r.add(("POST", "/api/leagues/round"), leagues_round)

        r.add(("GET", "/api/trackmap"), api_trackmap)
        r.add(("GET", "/api/status"), api_status)
        r.add(("GET", "/api/diagnostics"), api_diag)
        r.add(("GET", "/api/state"), api_state)
        r.add(("GET", "/api/config"), api_config_get)
        r.add(("POST", "/api/config"), api_config_set)
        r.add(("GET", "/api/roster"), roster_get)
        r.add(("POST", "/api/roster/submit"), roster_submit)
        r.add(("DELETE", "/api/roster"), roster_delete)
        r.add(("POST", "/api/roster/pull"), roster_pull)
        r.add(("POST", "/api/roster/push"), roster_push)
        r.add(("POST", "/api/roster/import"), roster_import)
        r.add(("POST", "/api/control"), api_control)
        r.add(("WS", "control"), lambda msg: self._do_control(msg.get("action", ""), msg))

    def _do_control(self, action: str, p: Dict[str, Any]) -> bool:
        c = self.control
        if action == "camera.car":
            num = str(p.get("carNumber") or "")
            grp = int(p.get("group") or 0)
            return c.camera_by_number(num, grp, int(p.get("camera") or 0))
        if action == "camera.position":
            return c.camera_by_position(int(p.get("position") or 1), int(p.get("group") or 0))
        if action == "camera.focus":
            return c.camera_focus(str(p.get("mode") or "leader"), int(p.get("group") or 0))
        if action == "replay.speed":
            return c.replay_speed(int(p.get("speed") or 1), bool(p.get("slow")))
        if action == "replay.search":
            return c.replay_search(str(p.get("what") or "toEnd"))
        if action == "replay.live":
            return c.replay_search("toEnd")
        if action == "replay.position":
            return c.replay_position(str(p.get("mode") or "current"), int(p.get("frame") or 0))
        if action == "replay.sessionTime":
            return c.replay_to_session_time(
                int(p.get("session") or 0), float(p.get("seconds") or 0)
            )
        if action == "capture":
            return c.video(str(p.get("what") or "screenshot"))
        if action == "pit":
            return c.pit(str(p.get("command") or "clear"), int(p.get("value") or 0))
        if action == "chat":
            return c.chat(str(p.get("command") or "macro"), int(p.get("sub") or 0))
        if action == "telemetry":
            return c.telemetry(str(p.get("command") or "restart"))
        return False

    # -- main loop --------------------------------------------------------

    def run(self) -> None:
        if not self._build_server():
            return
        assert self.server is not None
        self.server.start()
        self._banner()

        if self.config.get("openBrowser") and not self.args.no_browser:
            threading.Timer(
                0.8, lambda: _open(f"http://127.0.0.1:{self.config['port']}/control")
            ).start()

        if self.demo:
            self.feed = DemoFeed(seed=self.args.seed, scenario=self.args.scenario,
                                 speed=self.args.speed)
            if self.args.demo_roster and not self.roster.entries:
                self.roster.replace_all(self.feed.sample_profiles())
            self.engine.apply_session_info(self.feed.session_info())
            self.server.hub.set_meta(self.engine.build_meta())
        else:
            self.sdk = IRSDK()

        interval = 1.0 / max(1.0, float(self.config.get("tickRate", 20)))
        last_send = 0.0
        last_meta_check = 0.0
        last_reconnect = 0.0
        last_pull = time.time()
        auto_pull = float(self.config.get("roster", {}).get("autoPullSeconds") or 0)

        try:
            while self.running:
                now = time.time()

                if self.demo:
                    assert self.feed is not None
                    self.last_vars = self.feed.tick()
                    self.connected = True
                    if now - last_meta_check > 2.0:
                        last_meta_check = now
                        self.engine.apply_session_info(self.feed.session_info())
                        self.server.hub.set_meta(self.engine.build_meta())
                    self.engine.update(self.last_vars)
                    self.stats["ticks"] += 1
                    time.sleep(0.016)
                else:
                    assert self.sdk is not None
                    if not self.sdk.connected:
                        if now - last_reconnect > 1.0:
                            last_reconnect = now
                            self.connected = self.sdk.open()
                            self.sdk_error = self.sdk.last_error
                            if self.connected:
                                print("[PitWall] Connected to iRacing.")
                        else:
                            time.sleep(0.05)
                        if not self.connected:
                            self._idle_broadcast()
                            time.sleep(0.2)
                            continue
                    if self.sdk.wait_for_tick(32):
                        self.last_vars = self.sdk.snapshot(WANTED)
                        self.stats["ticks"] += 1
                        if self.sdk.session_info_dirty:
                            info = parse_session(self.sdk.session_info_raw())
                            if info:
                                self.engine.apply_session_info(info)
                                self.server.hub.set_meta(self.engine.build_meta())
                        self.engine.update(self.last_vars)
                        self.maps.set_track(
                            self.engine.track.get("id"), self.engine.track.get("name", "")
                        )
                        self.maps.sample(self.last_vars)
                        self.connected = True
                        self.sdk_error = None
                    elif not self.sdk.connected:
                        self.connected = False
                        self.sdk_error = "iRacing stopped sending telemetry."
                        print("[PitWall] Lost the sim. Waiting for it to come back.")
                        self.sdk.close()
                        continue

                if now - last_send >= interval and self.last_vars:
                    last_send = now
                    state = self.engine.build_state(self.last_vars)
                    self.server.hub.broadcast("tick", state)
                    self.stats["sent"] += 1
                    samples = self.engine.drain_inputs()
                    if samples:
                        self.server.hub.broadcast(
                            "inputs", {"type": "inputs", "s": samples[-40:]}
                        )
                    events = self.engine.drain_events()
                    if events:
                        self.server.hub.broadcast("events", {"type": "events", "e": events})

                if auto_pull and now - last_pull > auto_pull:
                    last_pull = now
                    threading.Thread(target=self.roster.pull, daemon=True).start()

        except KeyboardInterrupt:
            pass
        finally:
            print("\n[PitWall] Shutting down.")
            self.running = False
            if self.sdk:
                self.sdk.close()
            self.server.stop()

    def _idle_broadcast(self) -> None:
        self.server.hub.broadcast(
            "tick",
            {
                "type": "waiting",
                "t": round(time.time(), 2),
                "message": self.sdk_error or "Waiting for iRacing.",
            },
        )

    def _banner(self) -> None:
        port = self.config["port"]
        urls = lan_addresses(int(port))
        base = urls[0]
        lan = urls[1] if len(urls) > 1 else None
        print()
        print("  PitWall  -  iRacing live timing, broadcast graphics and HUD")
        print("  " + "-" * 62)
        print(f"  Control panel     {base}/control")
        print(f"  Second screen     {base}/timing")
        print(f"  Driver sign-up    {base}/register")
        print()
        print("  OBS browser sources:")
        for name, size in (
            ("broadcast/tower.html", "460 x 1080"),
            ("broadcast/session-bar.html", "1920 x 120"),
            ("broadcast/driver-card.html", "1920 x 1080"),
            ("broadcast/battle.html", "1920 x 1080"),
            ("broadcast/trackmap.html", "600 x 600"),
            ("broadcast/standings.html", "1920 x 1080"),
        ):
            print(f"    {base}/{name:<32} {size}")
        if lan:
            print()
            print(f"  On your network:  {lan}/timing")
            print("  (If another device cannot reach it, allow Python through the")
            print("   Windows firewall on private networks.)")
        print()
        if self.demo:
            print("  DEMO MODE: synthetic 24-car race. No iRacing needed.")
        print("  Ctrl+C to stop.")
        print()


def _open(url: str) -> None:
    try:
        webbrowser.open(url)
    except Exception:
        pass


def _hold_open_on_error(exc: BaseException) -> None:
    """A double-clicked .exe closes its window the instant it exits, taking
    the error with it. Keep it open long enough to read."""
    import traceback
    print("\n[PitWall] Stopped with an error:\n")
    traceback.print_exception(type(exc), exc, exc.__traceback__)
    print("\nPress Enter to close.")
    try:
        input()
    except Exception:
        time.sleep(30)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="pitwall", description="iRacing live timing and overlays")
    p.add_argument("--demo", action="store_true", help="run a synthetic race, no iRacing needed")
    p.add_argument("--scenario", default="race",
                   choices=["race", "practice", "qualify", "warmup"])
    p.add_argument("--seed", type=int, default=7, help="demo random seed")
    p.add_argument("--speed", type=float, default=1.0,
                   help="demo time compression, e.g. 10 to run a race in minutes")
    p.add_argument("--demo-roster", action="store_true",
                   help="seed the roster with demo driver profiles")
    p.add_argument("--port", type=int, default=None)
    p.add_argument("--host", default=None)
    p.add_argument("--config", default=None)
    p.add_argument("--data", default=None)
    p.add_argument("--no-browser", action="store_true")
    args = p.parse_args(argv)
    try:
        PitWall(args).run()
    except KeyboardInterrupt:
        pass
    except BaseException as exc:  # noqa: BLE001 - last resort for a GUI launch
        if FROZEN:
            _hold_open_on_error(exc)
            return 1
        raise
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
