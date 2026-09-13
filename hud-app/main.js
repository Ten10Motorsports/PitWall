/*
 * PitWall HUD shell.
 *
 * Transparent, click-through, always-on-top windows that sit over iRacing.
 * Each window loads one of the HUD pages the engine already serves, so there
 * is exactly one implementation of every widget and it is the same code that
 * feeds OBS and the second screen.
 *
 * Contains no native modules and no iRacing code of its own. It is a browser
 * in a frameless window, which means nothing to compile, nothing injected
 * into the sim, and nothing for anti-cheat to look at.
 *
 * THREE THINGS THIS GETS RIGHT THAT THE OBVIOUS VERSION DOES NOT
 * --------------------------------------------------------------
 * 1. Positions are stored per screen arrangement. Defaults are computed from
 *    the actual display at first run rather than baked in at 1920x1080, so a
 *    triple-screen or ultrawide driver does not open it to find every widget
 *    stacked in a corner or off-screen entirely. Plug in a different monitor
 *    and the old layout is still there when you plug it back.
 *
 * 2. Settings and positions are separate. A setting is a preference and
 *    follows you between setups; a position belongs to one arrangement. Mixing
 *    them means changing monitors resets how you like your widgets to look.
 *
 * 3. It starts the engine itself. A driver downloads one file and runs it.
 *    There is no second program to start in the right order and no console
 *    window, because a driver should never have to know the broadcast side of
 *    this exists.
 */

const { app, BrowserWindow, globalShortcut, screen, Tray, Menu, ipcMain, shell } =
  require('electron');
const fs = require('fs');
const path = require('path');
const http = require('http');
const { spawn } = require('child_process');

const CONFIG_PATH = path.join(app.getPath('userData'), 'layout.json');
const DEFAULT_SERVER = 'http://127.0.0.1:8099';

/* ==========================================================================
 * The widget registry.
 *
 * One entry per widget: where its page lives, how big it wants to be, where
 * to put it on a screen it has never seen, and what it can be configured
 * with. The settings window renders itself from this, so adding a widget is
 * one entry here and nothing else.
 *
 * `place` is an anchor on the work area rather than a coordinate, because a
 * coordinate is only meaningful on the screen it was written for.
 * ========================================================================== */

const WIDGETS = {
  relative: {
    label: 'Relative',
    blurb: 'Cars around you on track, with the gap in seconds.',
    page: 'hud/relative.html',
    size: [300, 250], place: 'left-middle', on: true,
    settings: [
      { key: 'ahead',   label: 'Cars ahead',      type: 'number', min: 0, max: 8, def: 3 },
      { key: 'behind',  label: 'Cars behind',     type: 'number', min: 0, max: 8, def: 3 },
      { key: 'class',   label: 'Class chips',     type: 'bool',   def: true },
      { key: 'irating', label: 'Show iRating',    type: 'bool',   def: false },
      { key: 'inc',     label: 'Show incidents',  type: 'bool',   def: false }
    ]
  },
  leaderboard: {
    label: 'Leaderboard',
    blurb: 'The broadcast timing tower, sized for in-car. Pin the leaders and rotate the rest.',
    page: 'broadcast/tower.html',
    size: [380, 560], place: 'right-top', on: false,
    settings: [
      { key: 'rows',   label: 'Rows',        type: 'number', min: 4, max: 40, def: 16 },
      { key: 'top',    label: 'Pin top N',   type: 'number', min: 0, max: 20, def: 0 },
      { key: 'cycle',  label: 'Rotate every (s), 0 is off', type: 'number', min: 0, max: 120, def: 0 },
      { key: 'col',    label: 'Column',      type: 'choice', def: 'gap',
        options: ['gap', 'int', 'last', 'best', 'delta'] },
      { key: 'col2',   label: 'Second column', type: 'choice', def: '',
        options: ['', 'gap', 'int', 'last', 'best', 'delta'] },
      { key: 'group',  label: 'Group by class', type: 'choice', def: 'none', options: ['none', 'class'] },
      { key: 'focus',  label: 'Follow my car', type: 'bool', def: true },
      { key: 'names',  label: 'Names',       type: 'choice', def: 'short',
        options: ['short', 'surname', 'full', 'first'] }
    ]
  },
  flags: {
    label: 'Flags & status',
    blurb: 'Flag state, position, incidents, laps to go and the warnings that end races.',
    page: 'hud/flags.html',
    size: [440, 58], place: 'bottom-centre', on: true,
    settings: [
      { key: 'spotter',  label: 'Edge spotter bars', type: 'bool', def: true },
      { key: 'warnings', label: 'Engine warnings',   type: 'bool', def: true },
      { key: 'h',        label: 'Strip height',      type: 'number', min: 34, max: 120, def: 58 }
    ]
  },
  blindspot: {
    label: 'Blindspot',
    blurb: 'Something alongside, and which side. Built for peripheral vision.',
    page: 'hud/blindspot.html',
    size: [320, 120], place: 'bottom-centre-above', on: true,
    settings: [
      { key: 'sides', label: 'Sides', type: 'choice', def: 'both', options: ['both', 'left', 'right'] },
      { key: 'hold',  label: 'Hold after clear (ms)', type: 'number', min: 0, max: 3000, def: 700 },
      { key: 'near',  label: 'Counts as alongside (s)', type: 'number', min: 0.2, max: 5, step: 0.1, def: 1.2 },
      { key: 'num',   label: 'Show car number', type: 'bool', def: true },
      { key: 'dist',  label: 'Show distance',   type: 'bool', def: true }
    ]
  },
  lapgraph: {
    label: 'Lap time graph',
    blurb: 'Your last laps as a chart. Best in purple, pit laps greyed out.',
    page: 'hud/lapgraph.html',
    size: [380, 200], place: 'right-middle', on: true,
    settings: [
      { key: 'laps',  label: 'Laps shown', type: 'number', min: 3, max: 40, def: 12 },
      { key: 'style', label: 'Style',      type: 'choice', def: 'bars', options: ['bars', 'line'] },
      { key: 'best',  label: 'Best-lap line',  type: 'bool', def: true },
      { key: 'avg',   label: 'Average line',   type: 'bool', def: true },
      { key: 'pits',  label: 'Include pit laps', type: 'bool', def: true },
      { key: 'range', label: 'Fixed span (s), 0 auto', type: 'number', min: 0, max: 30, def: 0 }
    ]
  },
  delta: {
    label: 'Delta bar',
    blurb: 'How this lap compares, with the sector breakdown.',
    page: 'hud/delta.html',
    size: [320, 120], place: 'top-centre', on: false,
    settings: [
      { key: 'ref',     label: 'Compared to', type: 'choice', def: 'session',
        options: ['session', 'best', 'optimal'] },
      { key: 'range',   label: 'Bar range (s)', type: 'number', min: 0.5, max: 10, step: 0.5, def: 2 },
      { key: 'bar',     label: 'Show the bar',  type: 'bool', def: true },
      { key: 'sectors', label: 'Show sectors',  type: 'bool', def: true }
    ]
  },
  fuel: {
    label: 'Fuel & tyres',
    blurb: 'Fuel per lap, laps left, and what to add to finish.',
    page: 'hud/fuel.html',
    size: [270, 300], place: 'right-bottom', on: false,
    settings: [
      { key: 'units',  label: 'Units',        type: 'choice', def: 'l', options: ['l', 'gal'] },
      { key: 'margin', label: 'Spare laps',   type: 'number', min: 0, max: 5, step: 0.5, def: 1 },
      { key: 'tyres',  label: 'Show tyres',   type: 'bool', def: true }
    ]
  },
  inputs: {
    label: 'Inputs trace',
    blurb: 'Throttle, brake and steering, traced.',
    page: 'hud/inputs.html',
    size: [340, 170], place: 'bottom-left', on: false,
    settings: [
      { key: 'seconds', label: 'Trace seconds', type: 'number', min: 2, max: 20, def: 6 },
      { key: 'speed',   label: 'Speed units',   type: 'choice', def: 'kph', options: ['kph', 'mph'] },
      { key: 'bars',    label: 'Show bars',     type: 'bool', def: true },
      { key: 'trace',   label: 'Show trace',    type: 'bool', def: true },
      { key: 'clutch',  label: 'Show clutch',   type: 'bool', def: false }
    ]
  }
};

/* Settings every widget has, appended to each one's own. */
const COMMON = [
  { key: 'scale',   label: 'Scale',   type: 'number', min: 0.5, max: 2.5, step: 0.05, def: 1 },
  { key: 'opacity', label: 'Opacity', type: 'number', min: 0.15, max: 1, step: 0.05, def: 0.9 }
];

function settingSpecs(name) {
  return (WIDGETS[name].settings || []).concat(COMMON);
}

function defaultSettings(name) {
  const out = {};
  settingSpecs(name).forEach(s => { out[s.key] = s.def; });
  return out;
}

/* ==========================================================================
 * Config
 * ========================================================================== */

let config = load();
const windows = new Map();
let tray = null;
let settingsWin = null;
let engine = null;

function blankConfig() {
  const settings = {};
  Object.keys(WIDGETS).forEach(n => { settings[n] = defaultSettings(n); });
  return {
    server: DEFAULT_SERVER,
    editMode: false,
    enabled: Object.fromEntries(Object.keys(WIDGETS).map(n => [n, !!WIDGETS[n].on])),
    settings,
    layouts: {}          // screen signature -> { widget: {x,y,w,h,display} }
  };
}

function load() {
  const base = blankConfig();
  let raw = null;
  try {
    raw = JSON.parse(fs.readFileSync(CONFIG_PATH, 'utf8'));
  } catch (e) {
    return base;
  }
  base.server = raw.server || base.server;
  base.editMode = false;                    // never start locked out of the sim
  Object.keys(WIDGETS).forEach(n => {
    if (raw.enabled && typeof raw.enabled[n] === 'boolean') base.enabled[n] = raw.enabled[n];
    // Merge rather than replace, so a widget that gains a setting in a later
    // version picks up its default instead of arriving undefined.
    base.settings[n] = Object.assign(base.settings[n], (raw.settings || {})[n] || {});
  });
  base.layouts = raw.layouts || {};
  return base;
}

function save() {
  try {
    fs.mkdirSync(path.dirname(CONFIG_PATH), { recursive: true });
    fs.writeFileSync(CONFIG_PATH, JSON.stringify(config, null, 2));
  } catch (e) {
    console.error('[PitWall HUD] could not save layout:', e.message);
  }
}

/* ==========================================================================
 * Screens
 *
 * A layout belongs to an arrangement of monitors, not to a machine. The
 * signature is every display's size and offset, so docking a laptop or adding
 * a screen gets its own layout and the old one is waiting when you go back.
 * ========================================================================== */

function displays() { return screen.getAllDisplays(); }

function screenSignature() {
  return displays()
    .map(d => `${d.size.width}x${d.size.height}@${d.bounds.x},${d.bounds.y}`)
    .sort()
    .join('|');
}

/**
 * The screen a driver is most likely looking at: the one with the sim on it.
 * We cannot know that, but on a triple setup the centre display is a far
 * better guess than the primary, which is often the left-hand screen.
 */
function mainDisplay() {
  const all = displays();
  if (all.length < 2) return screen.getPrimaryDisplay();
  const xs = all.map(d => d.bounds.x + d.bounds.width / 2);
  const mid = (Math.min(...xs) + Math.max(...xs)) / 2;
  let best = all[0], bestD = Infinity;
  all.forEach((d, i) => {
    const dist = Math.abs(xs[i] - mid);
    if (dist < bestD) { bestD = dist; best = d; }
  });
  return best;
}

function displayById(id) {
  return displays().find(d => d.id === id) || mainDisplay();
}

/** Turn a named anchor into coordinates on a given display's work area. */
function anchorTo(place, w, h, display) {
  const a = display.workArea;
  const m = 24;                            // margin from the screen edge
  const cx = a.x + Math.round((a.width - w) / 2);
  const map = {
    'left-middle':          [a.x + m, a.y + Math.round((a.height - h) / 2)],
    'left-top':             [a.x + m, a.y + m],
    'bottom-left':          [a.x + m, a.y + a.height - h - m],
    'right-top':            [a.x + a.width - w - m, a.y + m],
    'right-middle':         [a.x + a.width - w - m, a.y + Math.round((a.height - h) / 2)],
    'right-bottom':         [a.x + a.width - w - m, a.y + a.height - h - m],
    'top-centre':           [cx, a.y + m],
    'bottom-centre':        [cx, a.y + a.height - h - m],
    'bottom-centre-above':  [cx, a.y + a.height - h - m - 78]
  };
  const p = map[place] || map['left-middle'];
  return { x: p[0], y: p[1] };
}

/** The saved layout for the current arrangement, creating it if new. */
function layout() {
  const sig = screenSignature();
  if (!config.layouts[sig]) config.layouts[sig] = {};
  return config.layouts[sig];
}

/**
 * Where a widget goes right now. A widget with no saved place for this
 * arrangement is laid out from its anchor on the main display, which is what
 * makes first run look right on a screen we have never seen.
 */
function placement(name) {
  const lay = layout();
  const def = WIDGETS[name];
  if (lay[name]) {
    const p = lay[name];
    const d = displayById(p.display);
    return { x: p.x, y: p.y, w: p.w, h: p.h, display: d };
  }
  const d = mainDisplay();
  const w = def.size[0], h = def.size[1];
  const at = anchorTo(def.place, w, h, d);
  const p = { x: at.x, y: at.y, w, h, display: d.id };
  lay[name] = p;
  save();
  return { x: p.x, y: p.y, w, h, display: d };
}

/* ==========================================================================
 * The engine
 *
 * A driver runs one file. If nothing is already serving, start the engine we
 * ship beside us, hidden, and stop it when we quit. If something IS serving,
 * use it: that covers the case where the broadcaster's PitWall is already up
 * on the same machine, and we should not start a second one.
 * ========================================================================== */

function engineCandidates() {
  const exe = process.platform === 'win32' ? 'PitWall.exe' : 'PitWall';
  return [
    path.join(process.resourcesPath || '', 'engine', exe),
    path.join(path.dirname(app.getPath('exe')), exe),
    path.join(__dirname, '..', 'dist', exe)
  ];
}

function startEngine() {
  const found = engineCandidates().find(p => { try { return fs.existsSync(p); } catch (e) { return false; } });
  if (!found) {
    console.error('[PitWall HUD] No engine found beside the app. Looked in:\n  ' +
                  engineCandidates().join('\n  '));
    return false;
  }
  try {
    engine = spawn(found, ['--no-browser', '--host', '127.0.0.1'], {
      cwd: path.dirname(found),
      windowsHide: true,
      stdio: 'ignore'
    });
    engine.on('exit', () => { engine = null; });
    return true;
  } catch (e) {
    console.error('[PitWall HUD] Could not start the engine:', e.message);
    return false;
  }
}

function stopEngine() {
  if (engine && !engine.killed) {
    try { engine.kill(); } catch (e) { /* going away anyway */ }
  }
  engine = null;
}

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

/* ==========================================================================
 * Widget windows
 * ========================================================================== */

function widgetUrl(name) {
  const def = WIDGETS[name];
  const s = config.settings[name] || {};
  const q = [];
  settingSpecs(name).forEach(spec => {
    let v = s[spec.key];
    if (v === undefined || v === null || v === '') return;
    if (spec.type === 'bool') v = v ? '1' : '0';
    // Only send what differs from the page's own default, so the URL stays
    // readable and a page's default can change without every saved config
    // pinning the old one.
    if (String(v) === String(spec.type === 'bool' ? (spec.def ? '1' : '0') : spec.def)) return;
    q.push(encodeURIComponent(spec.key) + '=' + encodeURIComponent(v));
  });
  return config.server + '/' + def.page + (q.length ? '?' + q.join('&') : '');
}

function createWidget(name) {
  if (windows.has(name)) return windows.get(name);
  const p = placement(name);
  const b = p.display.bounds;

  const win = new BrowserWindow({
    x: clamp(p.x, b.x - 40, b.x + b.width - 60),
    y: clamp(p.y, b.y - 20, b.y + b.height - 40),
    width: p.w,
    height: p.h,
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
  win.loadURL(widgetUrl(name));

  win.on('moved', () => persistBounds(name, win));
  win.on('resized', () => persistBounds(name, win));
  win.on('closed', () => windows.delete(name));

  windows.set(name, win);
  applyEditMode(win);
  return win;
}

function destroyWidget(name) {
  const w = windows.get(name);
  if (w && !w.isDestroyed()) w.close();
  windows.delete(name);
}

function persistBounds(name, win) {
  if (win.isDestroyed()) return;
  const b = win.getBounds();
  const lay = layout();
  const centre = { x: b.x + b.width / 2, y: b.y + b.height / 2 };
  const d = screen.getDisplayNearestPoint(centre);
  lay[name] = { x: b.x, y: b.y, w: b.width, h: b.height, display: d.id };
  save();
}

function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }

function applyEditMode(win) {
  if (win.isDestroyed()) return;
  const editing = !!config.editMode;
  // Click-through when not editing, so a click meant for the sim is never
  // swallowed by an overlay. This is the behaviour every overlay tool gets
  // wrong at least once.
  win.setIgnoreMouseEvents(!editing, { forward: true });
  win.setFocusable(editing);
  win.setResizable(editing);
  win.webContents.send('pitwall:edit', editing);
}

function setEditMode(on) {
  config.editMode = !!on;
  save();
  windows.forEach(applyEditMode);
  buildTray();
  pushState();
}

function setEnabled(name, on) {
  if (!WIDGETS[name]) return;
  config.enabled[name] = !!on;
  save();
  if (on) createWidget(name); else destroyWidget(name);
  buildTray();
  pushState();
}

function applySettings(name, values) {
  if (!WIDGETS[name]) return;
  config.settings[name] = Object.assign(config.settings[name] || {}, values || {});
  save();
  const w = windows.get(name);
  if (w && !w.isDestroyed()) w.loadURL(widgetUrl(name));
}

function moveToDisplay(name, displayId) {
  const d = displayById(displayId);
  const def = WIDGETS[name];
  const lay = layout();
  const cur = lay[name] || {};
  const w = cur.w || def.size[0], h = cur.h || def.size[1];
  const at = anchorTo(def.place, w, h, d);
  lay[name] = { x: at.x, y: at.y, w, h, display: d.id };
  save();
  const win = windows.get(name);
  if (win && !win.isDestroyed()) win.setBounds({ x: at.x, y: at.y, width: w, height: h });
  pushState();
}

function resetLayout() {
  delete config.layouts[screenSignature()];
  save();
  windows.forEach((w, name) => destroyWidget(name));
  Object.keys(WIDGETS).forEach(n => { if (config.enabled[n]) createWidget(n); });
  pushState();
}

function reloadAll() {
  windows.forEach((w, name) => { if (!w.isDestroyed()) w.loadURL(widgetUrl(name)); });
}

function hideShowAll() {
  const anyVisible = [...windows.values()].some(w => !w.isDestroyed() && w.isVisible());
  windows.forEach(w => { if (!w.isDestroyed()) anyVisible ? w.hide() : w.show(); });
}

/* ==========================================================================
 * Settings window
 * ========================================================================== */

function openSettings() {
  if (settingsWin && !settingsWin.isDestroyed()) {
    settingsWin.show();
    settingsWin.focus();
    return;
  }
  const d = mainDisplay();
  settingsWin = new BrowserWindow({
    width: 880,
    height: Math.min(820, d.workArea.height - 60),
    x: d.workArea.x + Math.round((d.workArea.width - 880) / 2),
    y: d.workArea.y + 40,
    title: 'PitWall HUD',
    backgroundColor: '#15171b',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'settings-preload.js'),
      contextIsolation: true,
      nodeIntegration: false
    }
  });
  settingsWin.loadFile(path.join(__dirname, 'settings.html'));
  settingsWin.on('closed', () => { settingsWin = null; });
}

/** Everything the settings window needs to draw itself. */
function state() {
  const lay = layout();
  return {
    server: config.server,
    editMode: !!config.editMode,
    connected: true,
    widgets: Object.keys(WIDGETS).map(name => ({
      name,
      label: WIDGETS[name].label,
      blurb: WIDGETS[name].blurb,
      on: !!config.enabled[name],
      settings: settingSpecs(name),
      values: config.settings[name] || {},
      display: (lay[name] && lay[name].display) || mainDisplay().id
    })),
    displays: displays().map((d, i) => ({
      id: d.id,
      label: `Screen ${i + 1} · ${d.size.width} x ${d.size.height}` +
             (d.id === screen.getPrimaryDisplay().id ? ' (primary)' : ''),
      bounds: d.bounds
    }))
  };
}

function pushState() {
  if (settingsWin && !settingsWin.isDestroyed()) {
    settingsWin.webContents.send('hud:state', state());
  }
}

/* ==========================================================================
 * Tray
 * ========================================================================== */

function buildTray() {
  if (!tray) {
    try {
      tray = new Tray(path.join(__dirname, 'icon.png'));
      tray.on('click', openSettings);
    } catch (e) {
      return;   // no icon is not fatal; shortcuts and settings still work
    }
  }
  const items = Object.keys(WIDGETS).map(name => ({
    label: WIDGETS[name].label,
    type: 'checkbox',
    checked: !!config.enabled[name],
    click: () => setEnabled(name, !config.enabled[name])
  }));
  tray.setToolTip('PitWall HUD');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Settings and layout…', click: openSettings },
    { type: 'separator' },
    ...items,
    { type: 'separator' },
    {
      label: config.editMode ? 'Lock widgets' : 'Unlock widgets to move them',
      accelerator: 'Ctrl+Shift+E',
      click: () => setEditMode(!config.editMode)
    },
    { label: 'Hide / show all', accelerator: 'Ctrl+Shift+H', click: hideShowAll },
    { label: 'Reload all', accelerator: 'Ctrl+Shift+R', click: reloadAll },
    { type: 'separator' },
    { label: 'Quit', click: () => app.quit() }
  ]));
}

/* ==========================================================================
 * Boot
 * ========================================================================== */

app.disableHardwareAcceleration();   // avoids the Chromium transparency
                                     // artefacts that plague this class of
                                     // overlay

if (!app.requestSingleInstanceLock()) {
  // Two copies would fight over the same windows and the same layout file.
  app.quit();
} else {
  app.on('second-instance', openSettings);

  app.on('ready', async () => {
    // Read this before anything writes: laying the widgets out saves a
    // layout, which would make every run look like a returning one.
    const firstRun = !fs.existsSync(CONFIG_PATH);

    const argServer = process.argv.find(a => a.startsWith('--server='));
    if (argServer) config.server = argServer.split('=')[1];

    // Use a server that is already running; otherwise start our own.
    let up = await serverUp(config.server);
    if (!up && config.server === DEFAULT_SERVER) {
      if (startEngine()) up = await waitForServer(config.server);
    }
    if (!up) {
      console.error('[PitWall HUD] No PitWall engine at ' + config.server);
    }

    Object.keys(WIDGETS).forEach(n => { if (config.enabled[n]) createWidget(n); });

    globalShortcut.register('Ctrl+Shift+E', () => setEditMode(!config.editMode));
    globalShortcut.register('Ctrl+Shift+R', reloadAll);
    globalShortcut.register('Ctrl+Shift+H', hideShowAll);
    globalShortcut.register('Ctrl+Shift+S', openSettings);

    buildTray();

    // First run on this machine: show the settings window, because a driver
    // who double-clicks an exe and sees only a tray icon assumes it failed.
    if (firstRun) openSettings();
  });

  app.on('window-all-closed', () => { /* stay alive in the tray */ });
  app.on('will-quit', () => { globalShortcut.unregisterAll(); stopEngine(); });

  // Monitors changing mid-session must not leave widgets stranded off-screen.
  app.on('ready', () => {
    const relayout = () => {
      windows.forEach((w, name) => destroyWidget(name));
      Object.keys(WIDGETS).forEach(n => { if (config.enabled[n]) createWidget(n); });
      pushState();
    };
    screen.on('display-added', relayout);
    screen.on('display-removed', relayout);
    screen.on('display-metrics-changed', relayout);
  });
}

/* --------------------------------------------------------------------- IPC */

ipcMain.on('pitwall:persist', (ev, name) => {
  const win = BrowserWindow.fromWebContents(ev.sender);
  if (win) persistBounds(name, win);
});

ipcMain.handle('hud:state', () => state());
ipcMain.handle('hud:setEnabled', (_e, name, on) => { setEnabled(name, on); return state(); });
ipcMain.handle('hud:setEdit', (_e, on) => { setEditMode(on); return state(); });
ipcMain.handle('hud:setSettings', (_e, name, values) => { applySettings(name, values); return state(); });
ipcMain.handle('hud:setDisplay', (_e, name, id) => { moveToDisplay(name, id); return state(); });
ipcMain.handle('hud:reset', () => { resetLayout(); return state(); });
ipcMain.handle('hud:reload', () => { reloadAll(); return state(); });
ipcMain.handle('hud:openControl', () => { shell.openExternal(config.server + '/control'); });
ipcMain.handle('hud:quit', () => { app.quit(); });
