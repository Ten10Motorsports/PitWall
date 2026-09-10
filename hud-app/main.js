/*
 * PitWall HUD shell.
 *
 * Transparent, click-through, always-on-top windows that sit over iRacing.
 * Each window just loads one of the HUD pages the Python server already
 * serves, so there is exactly one implementation of every widget and it is
 * the same code that feeds OBS and the second screen.
 *
 * Deliberately contains no native modules and no iRacing code. It is a
 * browser in a frameless window. That means:
 *   - nothing to compile, on any machine
 *   - nothing injected into the sim, so nothing for anti-cheat to look at
 *   - the HUD keeps working if you restart the sim, and the server keeps
 *     working if you close the HUD
 *
 * Edit mode (Ctrl+Shift+E) makes every window draggable and resizable.
 * Leaving edit mode makes them click-through again so they never steal a
 * mouse click meant for the sim.
 */

const { app, BrowserWindow, globalShortcut, screen, Tray, Menu, ipcMain } = require('electron');
const fs = require('fs');
const path = require('path');
const http = require('http');

const CONFIG_PATH = path.join(app.getPath('userData'), 'layout.json');

const DEFAULTS = {
  server: 'http://127.0.0.1:8099',
  editMode: false,
  widgets: {
    relative: { page: 'hud/relative.html', x: 40, y: 300, w: 300, h: 240, on: true },
    standings: { page: 'hud/standings.html', x: 40, y: 40, w: 300, h: 340, on: false },
    inputs: { page: 'hud/inputs.html', x: 40, y: 780, w: 320, h: 160, on: true },
    fuel: { page: 'hud/fuel.html', x: 1580, y: 300, w: 270, h: 300, on: true },
    delta: { page: 'hud/delta.html', x: 800, y: 40, w: 320, h: 110, on: true },
    flags: { page: 'hud/flags.html', x: 760, y: 960, w: 400, h: 58, on: true }
  }
};

let config = load();
const windows = new Map();
let tray = null;

function load() {
  try {
    const raw = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
    return Object.assign({}, DEFAULTS, raw, {
      widgets: Object.assign({}, DEFAULTS.widgets, raw.widgets || {})
    });
  } catch (e) {
    return JSON.parse(JSON.stringify(DEFAULTS));
  }
}

function save() {
  try {
    fs.mkdirSync(path.dirname(CONFIG_PATH), { recursive: true });
    fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2));
  } catch (e) {
    console.error('[PitWall HUD] could not save layout:', e.message);
  }
}

/* ------------------------------------------------------------------ server */

function serverUp(base) {
  return new Promise(resolve => {
    const req = http.get(base + '/api/status', { timeout: 1500 }, res => {
      res.resume();
      resolve(res.statusCode === 200);
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
  });
}

async function waitForServer(base, attempts = 40) {
  for (let i = 0; i < attempts; i++) {
    if (await serverUp(base)) return true;
    await new Promise(r => setTimeout(r, 500));
  }
  return false;
}

/* ----------------------------------------------------------------- windows */

function createWidget(name, spec) {
  if (windows.has(name)) return windows.get(name);

  const display = screen.getPrimaryDisplay();
  const bounds = display.bounds;

  const win = new BrowserWindow({
    x: clamp(spec.x, bounds.x, bounds.x + bounds.width - 80),
    y: clamp(spec.y, bounds.y, bounds.y + bounds.height - 40),
    width: spec.w,
    height: spec.h,
    frame: false,
    transparent: true,
    resizable: true,
    movable: true,
    minimizable: false,
    maximizable: false,
    skipTaskbar: true,
    alwaysOnTop: true,
    focusable: false,
    hasShadow: false,
    // iRacing runs fullscreen-ish; the HUD must float above it without
    // pulling focus or appearing in alt-tab.
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false
    }
  });

  // "screen-saver" is the level that reliably sits above a borderless
  // fullscreen game window on Windows.
  win.setAlwaysOnTop(true, 'screen-saver');
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });

  const url = spec.url || (config.server + '/' + spec.page + (spec.query ? '?' + spec.query : ''));
  win.loadURL(url);

  win.on('moved', () => persistBounds(name, win));
  win.on('resized', () => persistBounds(name, win));
  win.on('closed', () => windows.delete(name));

  windows.set(name, win);
  applyEditMode(win);
  return win;
}

function persistBounds(name, win) {
  if (win.isDestroyed()) return;
  const b = win.getBounds();
  const w = config.widgets[name];
  if (!w) return;
  w.x = b.x; w.y = b.y; w.w = b.width; w.h = b.height;
  save();
}

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

function applyEditMode(win) {
  if (win.isDestroyed()) return;
  const editing = !!config.editMode;
  // Click-through when not editing, so a mouse click meant for the sim is
  // never swallowed by an overlay. This is the behaviour every overlay tool
  // gets wrong at least once.
  win.setIgnoreMouseEvents(!editing, { forward: true });
  win.setFocusable(editing);
  win.setResizable(editing);
  win.webContents.send('pitwall:edit', editing);
}

function setEditMode(on) {
  config.editMode = on;
  save();
  windows.forEach(applyEditMode);
  buildTray();
}

function toggleWidget(name) {
  const spec = config.widgets[name];
  if (!spec) return;
  spec.on = !spec.on;
  save();
  if (spec.on) {
    createWidget(name, spec);
  } else {
    const w = windows.get(name);
    if (w && !w.isDestroyed()) w.close();
    windows.delete(name);
  }
  buildTray();
}

function reloadAll() {
  windows.forEach(w => { if (!w.isDestroyed()) w.reload(); });
}

/* -------------------------------------------------------------------- tray */

function buildTray() {
  if (!tray) {
    try {
      tray = new Tray(path.join(__dirname, 'icon.png'));
    } catch (e) {
      // No icon shipped is not fatal; the shortcuts still work.
      return;
    }
  }
  const items = Object.keys(config.widgets).map(name => ({
    label: name.charAt(0).toUpperCase() + name.slice(1),
    type: 'checkbox',
    checked: !!config.widgets[name].on,
    click: () => toggleWidget(name)
  }));
  tray.setToolTip('PitWall HUD — ' + config.server);
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'PitWall HUD', enabled: false },
    { type: 'separator' },
    ...items,
    { type: 'separator' },
    {
      label: config.editMode ? 'Lock overlays (stop editing)' : 'Move / resize overlays',
      accelerator: 'Ctrl+Shift+E',
      click: () => setEditMode(!config.editMode)
    },
    { label: 'Reload all', accelerator: 'Ctrl+Shift+R', click: reloadAll },
    { label: 'Open control panel', click: () => require('electron').shell.openExternal(config.server + '/control') },
    { type: 'separator' },
    { label: 'Quit', click: () => app.quit() }
  ]));
}

/* -------------------------------------------------------------------- boot */

app.disableHardwareAcceleration();  // avoids the Chromium transparency artefacts
                                    // that plague this class of overlay

app.on('ready', async () => {
  const argServer = process.argv.find(a => a.startsWith('--server='));
  if (argServer) config.server = argServer.split('=')[1];

  const up = await waitForServer(config.server);
  if (!up) {
    console.error(
      '[PitWall HUD] Could not reach the PitWall server at ' + config.server + '\n' +
      '              Start it first (run.bat / run.sh), then start the HUD.'
    );
  }

  Object.entries(config.widgets).forEach(([name, spec]) => {
    if (spec.on) createWidget(name, spec);
  });

  globalShortcut.register('Ctrl+Shift+E', () => setEditMode(!config.editMode));
  globalShortcut.register('Ctrl+Shift+R', reloadAll);
  globalShortcut.register('Ctrl+Shift+H', () => {
    const anyVisible = [...windows.values()].some(w => !w.isDestroyed() && w.isVisible());
    windows.forEach(w => { if (!w.isDestroyed()) anyVisible ? w.hide() : w.show(); });
  });

  buildTray();
});

app.on('window-all-closed', e => { /* stay alive in the tray */ });
app.on('will-quit', () => globalShortcut.unregisterAll());

ipcMain.on('pitwall:persist', (ev, name) => {
  const win = BrowserWindow.fromWebContents(ev.sender);
  if (win) persistBounds(name, win);
});
