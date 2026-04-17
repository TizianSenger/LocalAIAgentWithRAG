// --- Tab switching ---
document.querySelectorAll('.tab-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    if (btn.disabled) return
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'))
    document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'))
    btn.classList.add('active')
    document.getElementById(`pane-${btn.dataset.tab}`).classList.add('active')
  })
})

function setChatTabLocked (locked) {
  const btn = document.getElementById('tab-btn-chat')
  btn.disabled = locked
  btn.title    = locked ? 'Chat ist gesperrt während der Indexer läuft' : 'Chat öffnen'
  btn.textContent = locked ? 'Chat 🔒' : 'Chat ✓'
  if (locked) {
    // Wenn Chat-Tab gerade aktiv ist → zurück zu Logs
    if (btn.classList.contains('active')) {
      document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'))
      document.querySelectorAll('.tab-pane').forEach(p => p.classList.remove('active'))
      document.querySelector('[data-tab="logs"]').classList.add('active')
      document.getElementById('pane-logs').classList.add('active')
    }
  }
}

// --- Window controls ---
document.getElementById('win-minimize').addEventListener('click', () => window.api.minimize())
document.getElementById('win-maximize').addEventListener('click', () => window.api.maximize())
document.getElementById('win-close').addEventListener('click',    () => window.api.close())

// --- Log output ---
const logOutput  = document.getElementById('log-output')
let   logFilter  = 'all'
const logEntries = []

function pad2 (n) { return String(n).padStart(2, '0') }
function timestamp () {
  const d = new Date()
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`
}

function appendLog (entry) {
  logEntries.push(entry)
  if (logFilter !== 'all' && entry.source !== logFilter) return
  renderLogEntry(entry)
}

function renderLogEntry (entry) {
  const atBottom = logOutput.scrollTop + logOutput.clientHeight >= logOutput.scrollHeight - 20
  const lines = entry.text.replace(/\r/g, '').split('\n')
  lines.forEach(line => {
    if (!line.trim()) return
    const div = document.createElement('div')
    div.className = `log-line ${entry.type}`
    div.innerHTML =
      `<span class="ts">${entry.ts}</span>` +
      `<span class="src ${entry.source}">[${entry.source}]</span>` +
      escapeHtml(line)
    logOutput.appendChild(div)
  })
  if (atBottom) logOutput.scrollTop = logOutput.scrollHeight
}

function escapeHtml (s) {
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
}

function reRenderLog () {
  logOutput.innerHTML = ''
  logEntries.forEach(e => {
    if (logFilter === 'all' || e.source === logFilter) renderLogEntry(e)
  })
}

document.querySelectorAll('.filter-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'))
    btn.classList.add('active')
    logFilter = btn.dataset.filter
    reRenderLog()
  })
})

document.getElementById('btn-clear-log').addEventListener('click', () => {
  logEntries.length = 0
  logOutput.innerHTML = ''
})

// --- Status dots ---
function setDot (key, status) {
  const dot = document.getElementById(`status-${key}`)
  if (dot) dot.className = `status-dot ${status}`
}

// --- Stats bar ---
function setBar (id, pct) {
  const el = document.getElementById(id)
  if (!el) return
  el.style.width = Math.min(100, Math.max(0, pct)) + '%'
  // colour gradient: green to yellow to red
  if (pct < 60)       el.style.background = '#22c55e'
  else if (pct < 85)  el.style.background = '#f59e0b'
  else                el.style.background = '#ef4444'
}

window.api.onStats(({ cpu, ramUsed, ramTotal, gpu }) => {
  // CPU
  document.getElementById('val-cpu').textContent = `${cpu}%`
  setBar('bar-cpu', cpu)

  // RAM
  const ramPct = ramTotal > 0 ? (ramUsed / ramTotal) * 100 : 0
  document.getElementById('val-ram').textContent = `${ramUsed}/${ramTotal} GB`
  setBar('bar-ram', ramPct)

  // GPU
  document.getElementById('val-gpu').textContent = `${gpu.gpuUtil}%`
  setBar('bar-gpu', gpu.gpuUtil)

  // VRAM
  const vramGB   = v => (v / 1024).toFixed(1)
  const vramPct  = gpu.vramTotal > 0 ? (gpu.vramUsed / gpu.vramTotal) * 100 : 0
  document.getElementById('val-vram').textContent = `${vramGB(gpu.vramUsed)}/${vramGB(gpu.vramTotal)} GB`
  setBar('bar-vram', vramPct)

  // Temp
  const tempEl = document.getElementById('val-temp')
  tempEl.textContent = `${gpu.temp}\u00B0C`
  tempEl.style.color = gpu.temp > 85 ? '#ef4444' : gpu.temp > 70 ? '#f59e0b' : '#e2e8f0'
})

// --- Ollama health ---
window.api.onOllamaHealth(({ running }) => {
  const pill = document.getElementById('ollama-pill')
  const text = document.getElementById('ollama-pill-text')
  pill.className = `ollama-pill${running ? ' online' : ''}`
  text.textContent = running ? 'Ollama Online' : 'Ollama Offline'
  if (running) refreshModels()
})

// --- Progress bar ---
const banner        = document.getElementById('progress-banner')
let   progressStart = null

function fmtTime (secs) {
  if (!secs || secs <= 0) return '--'
  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  const s = secs % 60
  if (h > 0) return `${h}h ${pad2(m)}m`
  if (m > 0) return `${m}m ${pad2(s)}s`
  return `${s}s`
}

window.api.onProgress(({ done, total, file, elapsed, eta }) => {
  if (!progressStart) progressStart = Date.now()
  banner.classList.add('visible')

  const pct = total > 0 ? Math.round((done / total) * 100) : 0
  document.getElementById('progress-fill').style.width   = pct + '%'
  document.getElementById('progress-pct').textContent    = pct + '%'
  document.getElementById('progress-counts').textContent = `${done} / ${total} files`
  document.getElementById('progress-file').textContent   = file ? `> ${file}` : 'Starting...'
  document.getElementById('progress-elapsed').textContent = fmtTime(elapsed)
  document.getElementById('progress-eta').textContent     = done > 0 ? fmtTime(eta) : '--'

  // files/min
  const rate = elapsed > 0 ? ((done / elapsed) * 60).toFixed(1) : '--'
  document.getElementById('progress-speed').textContent = rate !== '--' ? `${rate} files/min` : '--'
})

window.api.onIndexerDone(() => {
  progressStart = null
  setTimeout(() => banner.classList.remove('visible'), 3000)
  setChatTabLocked(false)
  appendLog({ source: 'system', text: 'Indexer fertig — Chat ist jetzt verfügbar.', type: 'info', ts: timestamp() })
})

// --- IPC events ---
window.api.onOutput(({ source, text, type }) => {
  appendLog({ source, text, type, ts: timestamp() })
})

window.api.onStatus(({ source, status }) => {
  setDot(source, status)
  if (source === 'indexer') {
    if (status === 'running') {
      setChatTabLocked(true)
    } else {
      setTimeout(() => banner.classList.remove('visible'), 3000)
    }
  }
})

window.api.onVaultCleared(() => {
  appendLog({ source: 'system', text: 'Vault cleared successfully.', type: 'info', ts: timestamp() })
})

// --- Sidebar buttons ---
function workers () { return parseInt(document.getElementById('workers-input').value, 10) || 4 }

document.getElementById('btn-start-ollama').addEventListener('click', () => window.api.startOllama(workers()))
document.getElementById('btn-stop-ollama').addEventListener('click',  () => window.api.stopOllama())

document.getElementById('btn-stop-all').addEventListener('click', () => {
  window.api.stopAll()
  appendLog({ source: 'system', text: 'Stop all sent.', type: 'info', ts: timestamp() })
})

document.getElementById('btn-start-indexer').addEventListener('click', () => {
  progressStart = null
  window.api.startIndexer()
})
document.getElementById('btn-force-indexer').addEventListener('click', () => {
  if (confirm('Re-analyse every file from scratch?')) {
    progressStart = null
    window.api.startIndexerForce()
  }
})
document.getElementById('btn-stop-indexer').addEventListener('click', () => window.api.stopIndexer())

document.getElementById('btn-start-update').addEventListener('click', () => window.api.startUpdate())
document.getElementById('btn-stop-update').addEventListener('click',  () => window.api.stopUpdate())

document.getElementById('btn-start-chat').addEventListener('click', () => window.api.startChatApi())
document.getElementById('btn-stop-chat').addEventListener('click',  () => window.api.stopChatApi())

document.getElementById('btn-clear-vault').addEventListener('click', () => window.api.clearVault())

// --- Graph tab ---
const graphFrame  = document.getElementById('graph-frame')
const graphStatus = document.getElementById('graph-status')

// Listen for settings changes posted from the graph iframe
window.addEventListener('message', (e) => {
  if (e.data && e.data.type === 'save-graph-settings') {
    saveSettings({ graph: e.data.settings })
  }
})

document.getElementById('btn-generate-graph').addEventListener('click', async () => {
  graphStatus.textContent = 'Generating graph…'
  const btn = document.getElementById('btn-generate-graph')
  btn.disabled = true
  const graphSettings = (_settings && _settings.graph) ? _settings.graph : {}
  const result = await window.api.generateGraph(graphSettings)
  btn.disabled = false
  if (result.error) {
    graphStatus.textContent = 'Error: ' + result.error
    return
  }
  graphFrame.srcdoc = result.html
  graphStatus.textContent = 'Graph ready — drag nodes, scroll to zoom, search above.'
})

// --- Chat ---
const chatMessages = document.getElementById('chat-messages')
const chatInput    = document.getElementById('chat-input')
const sendBtn      = document.getElementById('btn-send')
const stopBtn      = document.getElementById('btn-stop-chat')

function setChatStreaming (streaming) {
  sendBtn.disabled    = streaming
  stopBtn.style.display = streaming ? 'inline-block' : 'none'
}

function addMessage (role, text, loading = false) {
  const wrapper = document.createElement('div')
  wrapper.className = `msg ${role}`
  const avatar  = document.createElement('div')
  avatar.className  = 'msg-avatar'
  avatar.textContent = role === 'user' ? 'U' : 'A'
  const bubble  = document.createElement('div')
  bubble.className  = `msg-bubble${loading ? ' loading' : ''}`
  bubble.textContent = text
  wrapper.appendChild(avatar)
  wrapper.appendChild(bubble)
  chatMessages.appendChild(wrapper)
  chatMessages.scrollTop = chatMessages.scrollHeight
  return bubble
}

async function sendChat () {
  const q = chatInput.value.trim()
  if (!q) return
  chatInput.value  = ''
  setChatStreaming(true)
  addMessage('user', q)

  // Create agent bubble
  const wrapper  = document.createElement('div')
  wrapper.className = 'msg agent'
  const avatar   = document.createElement('div')
  avatar.className  = 'msg-avatar'
  avatar.textContent = 'A'
  const outer    = document.createElement('div')
  outer.className   = 'msg-bubble-outer'
  const srcBar   = document.createElement('div')
  srcBar.className  = 'msg-sources'
  srcBar.style.display = 'none'
  const bubble   = document.createElement('div')
  bubble.className  = 'msg-bubble loading'
  bubble.textContent = 'Thinking...'
  outer.appendChild(srcBar)
  outer.appendChild(bubble)
  wrapper.appendChild(avatar)
  wrapper.appendChild(outer)
  chatMessages.appendChild(wrapper)
  chatMessages.scrollTop = chatMessages.scrollHeight

  let started = false

  // One-time listener for this request
  function onEvent (evt) {
    if (evt.type === 'sources' && evt.sources && evt.sources.length) {
      srcBar.textContent = 'Sources: ' + evt.sources.map(s => s.split('\\').pop().replace(/\.md$/, '')).join(', ')
      srcBar.style.display = 'block'
    } else if (evt.type === 'token') {
      if (!started) { bubble.textContent = ''; bubble.classList.remove('loading'); started = true }
      bubble.textContent += evt.text
      chatMessages.scrollTop = chatMessages.scrollHeight
    } else if (evt.type === 'done') {
      if (!started) { bubble.textContent = '(empty response)'; bubble.classList.remove('loading') }
      chatMessages.scrollTop = chatMessages.scrollHeight
      setChatStreaming(false)
      chatInput.focus()
      cleanup()
    } else if (evt.type === 'error') {
      bubble.classList.remove('loading')
      bubble.textContent = `Error: ${evt.text}\n\nMake sure the Chat API is running.`
      bubble.style.color = 'var(--danger)'
      setChatStreaming(false)
      chatInput.focus()
      cleanup()
    }
  }

  // Register listener then send request
  const unlisten = window.api.onChatStreamEvent(onEvent)
  function cleanup () { if (unlisten) unlisten() }
  window.api.streamChat(q)
}

sendBtn.addEventListener('click', sendChat)
chatInput.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat() } })
stopBtn.addEventListener('click', () => { window.api.abortChat(); setChatStreaming(false) })

// --- Model selectors ---
const selectLlm     = document.getElementById('select-llm')
const selectChatLlm = document.getElementById('select-chat-llm')
const selectEmbed   = document.getElementById('select-embed')

const DEFAULT_LLM   = 'qwen2.5-coder:32b'
const DEFAULT_CHAT  = 'qwen2.5-coder:32b'
const DEFAULT_EMBED = 'mxbai-embed-large'

// Persist settings via settings.json (via IPC)
let _settings = {}
async function loadSettings () {
  _settings = (await window.api.getSettings()) || {}
  return _settings
}
function saveSettings (patch) {
  _settings = { ..._settings, ...patch }
  window.api.saveSettings(patch)
}

function populateSelect (sel, models, defaultVal) {
  const prev = sel.value || defaultVal
  sel.innerHTML = ''
  models.forEach(m => {
    const opt = document.createElement('option')
    opt.value = opt.textContent = m
    if (m === prev) opt.selected = true
    sel.appendChild(opt)
  })
  // If previous selection not in list, add it as first option
  if (!models.includes(prev)) {
    const opt = document.createElement('option')
    opt.value = opt.textContent = prev
    opt.selected = true
    sel.insertBefore(opt, sel.firstChild)
  }
}

async function refreshModels () {
  const models = await window.api.getModels()
  if (!models || models.length === 0) {
    // Leave dropdowns with placeholder so user knows Ollama isn't up
    return
  }
  const s = _settings
  populateSelect(selectLlm,     models, s.llm   || DEFAULT_LLM)
  populateSelect(selectChatLlm, models, s.chat  || DEFAULT_CHAT)
  populateSelect(selectEmbed,   models, s.embed || DEFAULT_EMBED)
  window.api.setLlmModel(selectLlm.value)
  window.api.setChatModel(selectChatLlm.value)
  window.api.setEmbedModel(selectEmbed.value)
}

selectLlm.addEventListener('change', () => {
  saveSettings({ llm: selectLlm.value })
  window.api.setLlmModel(selectLlm.value)
})
selectChatLlm.addEventListener('change', () => {
  saveSettings({ chat: selectChatLlm.value })
  window.api.setChatModel(selectChatLlm.value)
})
selectEmbed.addEventListener('change', () => {
  saveSettings({ embed: selectEmbed.value })
  window.api.setEmbedModel(selectEmbed.value)
})

document.getElementById('btn-refresh-models').addEventListener('click', () => refreshModels())

// --- Init ---
window.api.requestStatus()
loadSettings().then(() => refreshModels())
appendLog({ source: 'system', text: 'natMSS Agent UI ready.', type: 'info', ts: timestamp() })

