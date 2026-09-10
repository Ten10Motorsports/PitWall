"""
Build PitWall into a single Windows .exe.

Run by GitHub Actions on every tagged release, so nobody in a league ever
installs Python. You can also run it locally:

    pip install pyinstaller
    python scripts/build_exe.py

The result is dist/PitWall.exe — one file, no runtime dependencies.

Two things this gets right that a naive PyInstaller invocation does not:

  * The web/ folder is bundled as data and resolved at runtime through
    sys._MEIPASS (see server/main.py: bundle_root). Forget this and every page
    404s in the built app while working perfectly from source.
  * The roster, uploads, cached track maps and config are written next to the
    .exe rather than into the one-file temp directory, which is deleted the
    moment the app exits. Forget this and a league loses its whole roster every
    time it closes the app.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEP = ";" if os.name == "nt" else ":"


def main() -> int:
    os.chdir(ROOT)

    for stale in ("build", "dist"):
        shutil.rmtree(os.path.join(ROOT, stale), ignore_errors=True)

    icon = os.path.join(ROOT, "hud-app", "icon.png")
    args = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", "PitWall",
        "--console",
        # Everything the app serves has to travel with it.
        "--add-data", f"web{SEP}web",
        # Nothing here needs these, and leaving them out keeps the binary small
        # and the startup fast.
        "--exclude-module", "tkinter",
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc",
        "--exclude-module", "doctest",
        "--exclude-module", "email",
        "--exclude-module", "xml",
        "--exclude-module", "pdb",
        "--noconfirm",
        "--clean",
        os.path.join("server", "main.py"),
    ]

    # PyInstaller wants .ico on Windows; skip rather than fail the build.
    ico = os.path.join(ROOT, "hud-app", "icon.ico")
    if os.path.isfile(ico):
        args[args.index("--console"):args.index("--console")] = ["--icon", ico]
    elif os.path.isfile(icon):
        print("[build] no .ico found, building without a custom icon")

    print("[build]", " ".join(args))
    rc = subprocess.call(args)
    if rc != 0:
        return rc

    out = os.path.join(ROOT, "dist", "PitWall.exe" if os.name == "nt" else "PitWall")
    if not os.path.isfile(out):
        print("[build] expected output missing:", out)
        return 1
    size = os.path.getsize(out) / (1024 * 1024)
    print(f"[build] ok: {out}  ({size:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
