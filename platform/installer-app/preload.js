// Preload — minimal IPC surface for the renderer (contextIsolation on).
"use strict";
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("ta", {
  getConfig: () => ipcRenderer.invoke("get-config"),
  install: () => ipcRenderer.invoke("install"),
  signin: () => ipcRenderer.invoke("signin"),
  launch: () => ipcRenderer.invoke("launch"),
  onInstallLog: (cb) => ipcRenderer.on("install:log", (_e, line) => cb(line)),
  onSigninLog: (cb) => ipcRenderer.on("signin:log", (_e, line) => cb(line)),
});
