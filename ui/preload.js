const { contextBridge, ipcRenderer } = require('electron')

contextBridge.exposeInMainWorld('api', {
  // Window controls
  minimize:   ()  => ipcRenderer.send('win-minimize'),
  maximize:   ()  => ipcRenderer.send('win-maximize'),
  close:      ()  => ipcRenderer.send('win-close'),

  // Ollama
  startOllama: (workers) => ipcRenderer.send('start-ollama', workers),
  stopOllama:  ()        => ipcRenderer.send('stop-ollama'),

  // Indexer
  startIndexer:      () => ipcRenderer.send('start-indexer'),
  startIndexerForce: () => ipcRenderer.send('start-indexer-force'),
  stopIndexer:       () => ipcRenderer.send('stop-indexer'),

  // Update
  startUpdate: () => ipcRenderer.send('start-update'),
  stopUpdate:  () => ipcRenderer.send('stop-update'),

  // Chat API
  startChatApi: () => ipcRenderer.send('start-chat-api'),
  stopChatApi:  () => ipcRenderer.send('stop-chat-api'),
  // Chat streaming via IPC
  streamChat:        (q)  => ipcRenderer.send('chat-stream-start', q),
  abortChat:         ()   => ipcRenderer.send('chat-stream-abort'),
  onChatStreamEvent: (cb) => {
    const wrapped = (_, d) => cb(d)
    ipcRenderer.on('chat-stream-event', wrapped)
    return () => ipcRenderer.removeListener('chat-stream-event', wrapped)
  },

  // System
  stopAll:       () => ipcRenderer.send('stop-all'),
  clearVault:    () => ipcRenderer.send('clear-vault'),
  requestStatus: () => ipcRenderer.send('request-status'),

  // Model selection
  getModels:      ()  => ipcRenderer.invoke('get-models'),
  setLlmModel:    (m) => ipcRenderer.send('set-llm-model', m),
  setChatModel:   (m) => ipcRenderer.send('set-chat-model', m),
  setEmbedModel:  (m) => ipcRenderer.send('set-embed-model', m),
  // Settings
  getSettings:    ()      => ipcRenderer.invoke('get-settings'),
  saveSettings:   (patch) => ipcRenderer.send('save-settings', patch),
  // Graph
  generateGraph:  ()      => ipcRenderer.invoke('generate-graph'),

  // Events from main
  onOutput:       (cb) => ipcRenderer.on('process-output',   (_, d) => cb(d)),
  onStatus:       (cb) => ipcRenderer.on('process-status',   (_, d) => cb(d)),
  onVaultCleared: (cb) => ipcRenderer.on('vault-cleared',    ()     => cb()),
  onProgress:     (cb) => ipcRenderer.on('indexer-progress', (_, d) => cb(d)),
  onIndexerDone:  (cb) => ipcRenderer.on('indexer-done',     (_, d) => cb(d)),
  onStats:        (cb) => ipcRenderer.on('system-stats',     (_, d) => cb(d)),
  onOllamaHealth: (cb) => ipcRenderer.on('ollama-health',    (_, d) => cb(d)),
})
