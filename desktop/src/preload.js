const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('motiqDesktop', {
  getConfig: () => ipcRenderer.invoke('motiq:get-config'),
  saveFirstRun: (payload) => ipcRenderer.invoke('motiq:save-first-run', payload),
  pickRobotFile: () => ipcRenderer.invoke('motiq:pick-robot-file'),
  openExternal: (url) => ipcRenderer.invoke('motiq:open-external', url),
});
