const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("astock", {
  apiVersion: 3,
  runDiagnosis(payload) {
    return ipcRenderer.invoke("diagnosis:run", payload);
  },
  getRuntimeStatus() {
    return ipcRenderer.invoke("runtime:status");
  },
  runCapability(request) {
    return ipcRenderer.invoke("capability:run", request);
  },
  runLabTurn(request) {
    return ipcRenderer.invoke("lab:run", request);
  },
});
