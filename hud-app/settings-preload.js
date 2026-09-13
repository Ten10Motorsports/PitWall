/*
 * Preload for the settings window.
 *
 * A deliberately narrow bridge: the settings page can read the current state
 * and call a fixed set of named actions, and that is all. It cannot reach the
 * filesystem, spawn anything, or touch Electron directly, so the window stays
 * a plain web page that happens to have a remote control.
 */
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('hud', {
  state:       ()               => ipcRenderer.invoke('hud:state'),
  setEnabled:  (name, on)       => ipcRenderer.invoke('hud:setEnabled', name, on),
  setEdit:     (on)             => ipcRenderer.invoke('hud:setEdit', on),
  setSettings: (name, values)   => ipcRenderer.invoke('hud:setSettings', name, values),
  setDisplay:  (name, id)       => ipcRenderer.invoke('hud:setDisplay', name, id),
  reset:       ()               => ipcRenderer.invoke('hud:reset'),
  reload:      ()               => ipcRenderer.invoke('hud:reload'),
  openControl: ()               => ipcRenderer.invoke('hud:openControl'),
  quit:        ()               => ipcRenderer.invoke('hud:quit'),

  /* The main process pushes a fresh state whenever something changes it from
     outside this window, such as the tray menu or the keyboard shortcut. */
  onState: cb => ipcRenderer.on('hud:state', (_e, s) => cb(s))
});
