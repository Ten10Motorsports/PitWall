/*
 * PitWall HUD preload.
 *
 * When edit mode is on the page grows a drag bar and a resize grip, plus a
 * visible outline, because a transparent chrome-less window is otherwise
 * impossible to grab. When it is off the window is click-through and the page
 * has no idea it is inside Electron at all, which is why the same HTML runs
 * unchanged here, in OBS and in a browser tab.
 *
 * Resizing is done by hand rather than by leaving the window resizable and
 * letting the operating system draw handles. A frameless window's OS grab area
 * is a few pixels wide and invisible, which on a 60 Hz stream at night with a
 * wheel in your hands is not a control, it is a guess.
 */
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('pitwallHud', {
  onEdit: cb => ipcRenderer.on('pitwall:edit', (_e, editing) => cb(editing))
});

const ID_STYLE = 'hud-edit-style';
const ID_BAR = 'hud-drag';
const ID_GRIP = 'hud-grip';

function addChrome() {
  if (document.getElementById(ID_BAR)) return;

  const style = document.createElement('style');
  style.id = ID_STYLE;
  style.textContent = `
    html.hud-editing { outline: 2px dashed rgba(232,68,58,0.9); outline-offset: -2px; }
    html.hud-editing body { background: rgba(0,0,0,0.35) !important; }
    #${ID_BAR} {
      position: fixed; inset: 0 0 auto 0; height: 22px;
      -webkit-app-region: drag; cursor: move; z-index: 2147483646;
      background: rgba(232,68,58,0.85); color: #fff;
      font: 600 11px/22px system-ui, sans-serif; text-align: center;
      letter-spacing: .04em; user-select: none;
    }
    #${ID_GRIP} {
      position: fixed; right: 0; bottom: 0; width: 18px; height: 18px;
      z-index: 2147483647; cursor: nwse-resize;
      -webkit-app-region: no-drag;
      background:
        linear-gradient(135deg, transparent 0 46%, rgba(255,255,255,.85) 46% 54%, transparent 54%),
        linear-gradient(135deg, transparent 0 70%, rgba(255,255,255,.85) 70% 78%, transparent 78%);
      background-color: rgba(232,68,58,0.85);
    }`;
  document.head.appendChild(style);

  const bar = document.createElement('div');
  bar.id = ID_BAR;
  bar.textContent = 'DRAG TO MOVE  ·  CORNER TO RESIZE  ·  Ctrl+Shift+E TO LOCK';
  document.body.appendChild(bar);

  const grip = document.createElement('div');
  grip.id = ID_GRIP;
  grip.title = 'Drag to resize';
  document.body.appendChild(grip);

  /*
   * Resize by absolute pointer position, not by accumulating deltas.
   *
   * Accumulating is the obvious way and it drifts: every frame the window
   * moves under the cursor, so the next delta is measured against a window
   * that has already moved. Sending where the pointer actually is in screen
   * space and letting the main process work out the size from the window's
   * own origin has no feedback loop in it.
   */
  let dragging = false;
  grip.addEventListener('mousedown', ev => {
    ev.preventDefault();
    dragging = true;
    grip.setPointerCapture && grip.setPointerCapture(ev.pointerId);
  });
  window.addEventListener('mousemove', ev => {
    if (!dragging) return;
    ipcRenderer.send('pitwall:resize', { x: ev.screenX, y: ev.screenY });
  });
  window.addEventListener('mouseup', () => { dragging = false; });
  window.addEventListener('blur', () => { dragging = false; });
}

function removeChrome() {
  [ID_STYLE, ID_BAR, ID_GRIP].forEach(id => {
    const n = document.getElementById(id);
    if (n) n.remove();
  });
}

ipcRenderer.on('pitwall:edit', (_e, editing) => {
  document.documentElement.classList.toggle('hud-editing', !!editing);
  if (editing) addChrome(); else removeChrome();
});
