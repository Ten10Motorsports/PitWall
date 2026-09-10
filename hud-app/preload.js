/*
 * PitWall HUD preload.
 *
 * When edit mode is on, the page gets a drag handle and a visible outline so
 * you can see and grab a widget that is otherwise invisible chrome-less
 * transparency. When it is off, the window is click-through and the page has
 * no idea it is in Electron at all — which is why the same HTML works
 * unchanged in OBS and in a browser tab.
 */
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('pitwallHud', {
  onEdit: cb => ipcRenderer.on('pitwall:edit', (_e, editing) => cb(editing))
});

ipcRenderer.on('pitwall:edit', (_e, editing) => {
  document.documentElement.classList.toggle('hud-editing', !!editing);
  let bar = document.getElementById('hud-drag');
  if (editing && !bar) {
    const style = document.createElement('style');
    style.id = 'hud-edit-style';
    style.textContent = `
      html.hud-editing { outline: 2px dashed rgba(232,68,58,0.9); outline-offset: -2px; }
      html.hud-editing body { background: rgba(0,0,0,0.35) !important; }
      #hud-drag {
        position: fixed; inset: 0 0 auto 0; height: 22px;
        -webkit-app-region: drag; cursor: move; z-index: 99999;
        background: rgba(232,68,58,0.85); color: #fff;
        font: 600 11px/22px system-ui, sans-serif; text-align: center;
        letter-spacing: .04em;
      }`;
    document.head.appendChild(style);
    bar = document.createElement('div');
    bar.id = 'hud-drag';
    bar.textContent = 'DRAG TO MOVE  ·  Ctrl+Shift+E TO LOCK';
    document.body.appendChild(bar);
  } else if (!editing) {
    const s = document.getElementById('hud-edit-style');
    if (s) s.remove();
    if (bar) bar.remove();
  }
});
