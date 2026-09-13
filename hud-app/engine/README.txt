The engine goes here at build time.

The release workflow copies the freshly built PitWall.exe into this folder
before packaging the HUD, and electron-builder carries it into the app as
resources/engine/PitWall.exe. main.js looks for it there and starts it hidden,
so a driver downloads one file rather than two and never sees a console
window.

The folder is kept in the repository with this file so electron-builder always
has something to copy. A local build without an engine here still works: the
HUD just expects a PitWall already running on port 8099.
