const { app, BrowserWindow, ipcMain, dialog } = require('electron')
const { spawn, exec }                          = require('child_process')
const path  = require('path')
const os    = require('os')
const fs    = require('fs')
const http  = require('http')

// ─── Settings file ────────────────────────────────────────────────────────────
const SETTINGS_FILE = path.join(app.getPath('userData'), 'settings.json')
function readSettings () {
  try { return JSON.parse(fs.readFileSync(SETTINGS_FILE, 'utf8')) } catch { return {} }
}
function writeSettings (data) {
  try { fs.writeFileSync(SETTINGS_FILE, JSON.stringify(data, null, 2), 'utf8') } catch (_) {}
}

// ─── Paths ────────────────────────────────────────────────────────────────────
const SCRIPTS_DIR  = path.resolve(__dirname, '..')
const VAULT_CODE   = 'C:\\natMSSObsidian\\natMSS\\Code'
const STATE_FILE   = 'C:\\natMSSObsidian\\natMSS\\.indexer_state.json'
const CHROMA_DIR   = path.join(SCRIPTS_DIR, 'chrome_langchain_db')
const PYTHON       = 'C:\\Users\\Sim2\\AppData\\Local\\Programs\\Python\\Python313\\python.exe'
const CHAT_PORT    = 5001

// ─── Process registry ─────────────────────────────────────────────────────────
const procs = { ollama: null, indexer: null, chatApi: null, update: null }

let mainWindow
let statsPollId    = null
let ollamaHealthId = null
let lastCpuInfo    = null

// ─── Model selection ─────────────────────────────────────────────────────────
let selectedLlmModel   = 'qwen2.5-coder:32b'
let selectedChatModel  = 'qwen2.5-coder:32b'
let selectedEmbedModel = 'mxbai-embed-large'
let selectedAgentModel = 'qwen2.5-coder:32b'

function fetchOllamaModels () {
  return new Promise(resolve => {
    http.get('http://127.0.0.1:11434/api/tags', res => {
      let data = ''
      res.on('data', d => data += d)
      res.on('end', () => {
        try {
          const models = JSON.parse(data).models || []
          resolve(models.map(m => ({ name: m.name, size: m.size || 0 })))
        } catch { resolve([]) }
      })
    }).on('error', () => resolve([]))
  })
}

// ─── Window ───────────────────────────────────────────────────────────────────
function createWindow () {
  mainWindow = new BrowserWindow({
    width: 1360, height: 860,
    minWidth: 1000, minHeight: 680,
    frame: false,
    backgroundColor: '#0f172a',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })
  mainWindow.loadFile(path.join(__dirname, 'index.html'))
  mainWindow.on('maximize',   () => mainWindow.webContents.send('window-maximized', true))
  mainWindow.on('unmaximize', () => mainWindow.webContents.send('window-maximized', false))
  mainWindow.webContents.once('did-finish-load', () => {
    startPolling()
    // Start auto-mode scheduler if enabled
    const amCfg = getAutoSettings()
    if (amCfg.enabled) scheduleAutoMode()
  })
}

app.whenReady().then(createWindow)
app.on('window-all-closed', () => { stopPolling(); killAll(); app.quit() })
// ─── CPU usage ────────────────────────────────────────────────────────────────
function getCpuPercent () {
  const cpus = os.cpus()
  if (!lastCpuInfo) { lastCpuInfo = cpus; return 0 }
  let totalIdle = 0, totalTick = 0
  cpus.forEach((cpu, i) => {
    const prev = lastCpuInfo[i]
    for (const t in cpu.times) totalTick += cpu.times[t] - (prev.times[t] || 0)
    totalIdle += cpu.times.idle - (prev.times.idle || 0)
  })
  lastCpuInfo = cpus
  return totalTick > 0 ? Math.round((1 - totalIdle / totalTick) * 100) : 0
}

// ─── GPU stats via nvidia-smi ─────────────────────────────────────────────────
function getGpuStats () {
  return new Promise(resolve => {
    exec(
      'nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu --format=csv,noheader,nounits',
      (err, stdout) => {
        if (err || !stdout.trim()) return resolve(null)
        const [gpuUtil, vramUsed, vramTotal, temp] = stdout.trim().split(',').map(s => s.trim())
        resolve({ gpuUtil: parseInt(gpuUtil)||0, vramUsed: parseInt(vramUsed)||0, vramTotal: parseInt(vramTotal)||0, temp: parseInt(temp)||0 })
      }
    )
  })
}

// ─── Stats polling ────────────────────────────────────────────────────────────
async function pollStats () {
  const totalMem = os.totalmem()
  const freeMem  = os.freemem()
  const cpu      = getCpuPercent()
  const gpu      = await getGpuStats()
  send('system-stats', {
    cpu,
    ramUsed:  Math.round((totalMem - freeMem) / 1073741824 * 10) / 10,
    ramTotal: Math.round(totalMem             / 1073741824 * 10) / 10,
    gpu: gpu || { gpuUtil: 0, vramUsed: 0, vramTotal: 0, temp: 0 },
  })
}

function pollOllamaHealth () {
  const req = http.request(
    { hostname: '127.0.0.1', port: 11434, path: '/api/tags', method: 'GET', timeout: 2000 },
    res => { send('ollama-health', { running: res.statusCode === 200 }); res.resume() }
  )
  req.on('error',   () => send('ollama-health', { running: false }))
  req.on('timeout', () => { req.destroy(); send('ollama-health', { running: false }) })
  req.end()
}

function startPolling () {
  pollStats(); pollOllamaHealth()
  statsPollId    = setInterval(pollStats,        2000)
  ollamaHealthId = setInterval(pollOllamaHealth, 3000)
}

function stopPolling () {
  clearInterval(statsPollId)
  clearInterval(ollamaHealthId)
}

// ─── Helpers ──────────────────────────────────────────────────────────────────
function send (channel, data) {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send(channel, data)
}

function log (source, text, type = 'stdout') {
  send('process-output', { source, text, type })
}

function setStatus (source, status) {
  send('process-status', { source, status })
}

function spawnTracked (key, cmd, args, opts = {}) {
  if (procs[key]) {
    log('system', `[${key}] already running`, 'info')
    return
  }
  const proc = spawn(cmd, args, {
    cwd: SCRIPTS_DIR,
    shell: true,
    env: { ...process.env, ...(opts.env || {}) },
  })
  procs[key] = proc
  setStatus(key, 'running')
  log('system', `[${key}] started (pid ${proc.pid})`, 'info')

  let stdoutBuf = ''
  proc.stdout.on('data', d => {
    stdoutBuf += d.toString()
    const lines = stdoutBuf.split('\n')
    stdoutBuf = lines.pop()
    lines.forEach(line => {
      if (line.startsWith('PROGRESS:')) {
        try { send('indexer-progress', JSON.parse(line.slice(9))) } catch (_) {}
      } else if (line.trim()) {
        log(key, line)
      }
    })
  })

  proc.stderr.on('data', d => log(key, d.toString(), 'stderr'))
  proc.on('close', code => {
    procs[key] = null
    // Special case: ollama exits with code 1 because port is already bound by an
    // external instance — treat as "running externally" instead of error
    if (key === 'ollama' && code === 1) {
      const testSock = new (require('net').Socket)()
      testSock.setTimeout(500)
      testSock.connect(11434, '127.0.0.1', () => {
        testSock.destroy()
        setStatus('ollama', 'running')
        log('system', '[ollama] external instance detected — using it', 'info')
      })
      testSock.on('error',   () => { testSock.destroy(); setStatus('ollama', 'error') })
      testSock.on('timeout', () => { testSock.destroy(); setStatus('ollama', 'error') })
      return
    }
    setStatus(key, code === 0 ? 'stopped' : 'error')
    log('system', `[${key}] exited with code ${code}`, code === 0 ? 'info' : 'error')
    if (key === 'indexer') send('indexer-done', { code })
  })
}

function killProc (key) {
  const p = procs[key]
  if (!p) return
  try {
    process.platform === 'win32'
      ? exec(`taskkill /pid ${p.pid} /T /F`)
      : p.kill('SIGTERM')
  } catch (_) {}
  procs[key] = null
  setStatus(key, 'stopped')
  log('system', `[${key}] killed`, 'info')
}

function killAll () {
  Object.keys(procs).forEach(killProc)
  // also kill any stray ollama.exe
  if (process.platform === 'win32') exec('taskkill /f /im ollama.exe')
}

// ─── IPC Handlers ─────────────────────────────────────────────────────────────

// Window controls
ipcMain.on('win-minimize', () => mainWindow.minimize())
ipcMain.on('win-maximize', () => {
  if (mainWindow.isMaximized()) { mainWindow.unmaximize() } else { mainWindow.maximize() }
})
ipcMain.on('win-close',    () => { stopPolling(); killAll(); app.quit() })

// Ollama
ipcMain.on('start-ollama', (_, workers) => {
  if (procs.ollama) { log('system', '[ollama] already running', 'info'); return }
  const n = workers || 1
  indexerWorkers = n

  // Check if Ollama is already listening on port 11434 (started externally)
  const testSock = new (require('net').Socket)()
  testSock.setTimeout(500)
  testSock.connect(11434, '127.0.0.1', () => {
    testSock.destroy()
    log('system', '[ollama] already running externally — skipping spawn', 'info')
    setStatus('ollama', 'running')
  })
  testSock.on('error', () => {
    testSock.destroy()
    spawnTracked('ollama', 'ollama', ['serve'], {
      env: { OLLAMA_NUM_PARALLEL: String(n), OLLAMA_MAX_LOADED_MODELS: '1' },
    })
  })
  testSock.on('timeout', () => {
    testSock.destroy()
    spawnTracked('ollama', 'ollama', ['serve'], {
      env: { OLLAMA_NUM_PARALLEL: String(n), OLLAMA_MAX_LOADED_MODELS: '1' },
    })
  })
})

ipcMain.on('stop-ollama', () => {
  killProc('ollama')
  if (process.platform === 'win32') exec('taskkill /f /im ollama.exe')
})

// Indexer
let chatRagK = 20
ipcMain.on('set-chat-rag-k', (_, k) => { chatRagK = parseInt(k, 10) || 20 })

const pyEnv = () => ({
  PYTHONUNBUFFERED:  '1',
  PYTHONIOENCODING:  'utf-8',
  OVERRIDE_LLM_MODEL:   selectedLlmModel,
  OVERRIDE_CHAT_MODEL:  selectedChatModel,
  OVERRIDE_EMBED_MODEL: selectedEmbedModel,
  OVERRIDE_AGENT_MODEL: selectedAgentModel,
  CHAT_RAG_K: String(chatRagK),
})
let indexerWorkers = 1
ipcMain.on('start-indexer',       () => spawnTracked('indexer', PYTHON, ['-u', 'indexer.py', '--workers', String(indexerWorkers)],              { env: pyEnv() }))
ipcMain.on('start-indexer-force', () => spawnTracked('indexer', PYTHON, ['-u', 'indexer.py', '--force', '--workers', String(indexerWorkers)], { env: pyEnv() }))
ipcMain.on('stop-indexer',        () => killProc('indexer'))

// Update (git pull + incremental index)
ipcMain.on('start-update', () => spawnTracked('update', PYTHON, ['-u', 'update.py'], { env: pyEnv() }))
ipcMain.on('stop-update',  () => killProc('update'))

// Chat API
ipcMain.on('start-chat-api', () => {
  // Kill any stale process on CHAT_PORT before starting
  exec(`netstat -ano | findstr :${CHAT_PORT}`, (err, stdout) => {
    if (!err && stdout) {
      stdout.trim().split('\n').forEach(line => {
        const parts = line.trim().split(/\s+/)
        const pid = parts[parts.length - 1]
        if (pid && /^\d+$/.test(pid)) {
          exec(`taskkill /pid ${pid} /F`, () => {})
        }
      })
    }
    setTimeout(() => spawnTracked('chatApi', PYTHON, ['-u', '-B', 'chat_api.py', String(CHAT_PORT)], { env: pyEnv() }), 500)
  })
})
ipcMain.on('stop-chat-api',  () => killProc('chatApi'))

// Agent
ipcMain.on('start-agent', (_, { budgetMinutes, focus, maxCalls, grepLimit, notesK, mode }) => {
  const args = [
    '-u', '-B', path.join(SCRIPTS_DIR, 'agent.py'),
    '--budget-minutes', String(budgetMinutes != null && budgetMinutes > 0 ? budgetMinutes : 0),
    '--focus',          focus || 'all',
    '--max-calls',      String(maxCalls  || 60),
    '--grep-limit',     String(grepLimit || 50),
    '--notes-k',        String(notesK    || 8),
    '--mode',           mode || 'explore',
  ]
  const proc = spawn(PYTHON, args, { cwd: SCRIPTS_DIR, shell: true, env: { ...process.env, ...pyEnv() } })
  procs.agent = proc

  proc.stdout.on('data', raw => {
    raw.toString('utf8').split('\n').forEach(line => {
      if (!line.trim()) return
      if (line.startsWith('AGENT_PROGRESS:')) {
        try { mainWindow.webContents.send('agent-progress', JSON.parse(line.slice(15))) } catch {}
      } else if (line.startsWith('AGENT_FINDING:')) {
        try { mainWindow.webContents.send('agent-finding', JSON.parse(line.slice(14))) } catch {}
      } else if (line.startsWith('AGENT_DONE:')) {
        try { mainWindow.webContents.send('agent-done', JSON.parse(line.slice(11))) } catch {}
      } else {
        mainWindow.webContents.send('process-output', { source: 'agent', text: line, type: 'stdout' })
      }
    })
  })
  proc.stderr.on('data', raw => {
    mainWindow.webContents.send('process-output', { source: 'agent', text: raw.toString(), type: 'stderr' })
  })
  proc.on('close', code => {
    procs.agent = null
    mainWindow.webContents.send('process-status', { source: 'agent', status: code === 0 ? 'stopped' : 'error' })
  })
  mainWindow.webContents.send('process-status', { source: 'agent', status: 'running' })
})
ipcMain.on('stop-agent', () => {
  if (procs.agent) { killProc('agent') }
})

// Model selection
ipcMain.on('set-llm-model',   (_, m) => { selectedLlmModel   = m; log('system', `Indexer LLM set to: ${m}`, 'info') })
ipcMain.on('set-chat-model',  (_, m) => { selectedChatModel  = m; log('system', `Chat LLM set to: ${m}`, 'info') })
ipcMain.on('set-embed-model', (_, m) => { selectedEmbedModel = m; log('system', `Embed model set to: ${m}`, 'info') })
ipcMain.on('set-agent-model', (_, m) => { selectedAgentModel = m; log('system', `Agent LLM set to: ${m}`, 'info') })
ipcMain.handle('get-models',    async () => fetchOllamaModels())
ipcMain.handle('get-settings',  async () => readSettings())
ipcMain.on('save-settings', (_, patch) => {
  const current = readSettings()
  writeSettings({ ...current, ...patch })
})

// Graph generation
ipcMain.handle('generate-graph', async (_, graphSettings) => {
  return new Promise((resolve) => {
    const VAULT = 'C:\\natMSSObsidian\\natMSS'
    const settingsArg = JSON.stringify(graphSettings || {})
    const proc = spawn(PYTHON, ['-u', '-B', path.join(SCRIPTS_DIR, 'graph_gen.py'), VAULT, settingsArg])
    let html = ''
    let err  = ''
    proc.stdout.on('data', d => { html += d.toString('utf8') })
    proc.stderr.on('data', d => { err  += d.toString() })
    proc.on('close', code => {
      if (code !== 0 || !html) resolve({ error: err || 'graph_gen.py failed' })
      else resolve({ html })
    })
  })
})

// Stop all
ipcMain.on('stop-all', () => killAll())

// Clear vault
ipcMain.on('clear-vault', async () => {
  // Confirmation is handled in-app (custom modal in renderer)
  let cleared = 0
  try {
    if (fs.existsSync(VAULT_CODE)) {
      fs.rmSync(VAULT_CODE, { recursive: true, force: true })
      log('system', `[clear] Deleted ${VAULT_CODE}`, 'info')
      cleared++
    }
    if (fs.existsSync(STATE_FILE)) {
      fs.rmSync(STATE_FILE)
      log('system', `[clear] Deleted ${STATE_FILE}`, 'info')
      cleared++
    }
    // Also wipe the ChromaDB cache so chat API re-embeds on next start
    if (fs.existsSync(CHROMA_DIR)) {
      fs.rmSync(CHROMA_DIR, { recursive: true, force: true })
      log('system', `[clear] Deleted ChromaDB cache ${CHROMA_DIR}`, 'info')
      cleared++
    }
    log('system', `[clear] Vault cleared (${cleared} items removed)`, 'info')
    send('vault-cleared')
  } catch (err) {
    log('system', `[clear] Error: ${err.message}`, 'error')
  }
})

// Chat streaming → main process makes HTTP request, streams events to renderer
let activeStreamReq = null

ipcMain.on('chat-stream-abort', () => {
  if (activeStreamReq) {
    try { activeStreamReq.destroy() } catch (_) {}
    activeStreamReq = null
    send('chat-stream-event', { type: 'done' })
  }
})

ipcMain.on('chat-stream-start', (_, question) => {
  log('system', `[chat] streaming request: "${question}"`, 'info')
  const body = Buffer.from(JSON.stringify({ question }))
  const req  = http.request({
    hostname: '127.0.0.1', port: CHAT_PORT,
    path: '/stream', method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Content-Length': body.length },
  }, res => {
    activeStreamReq = null
    log('system', `[chat] /stream response status: ${res.statusCode}`, 'info')
    if (res.statusCode !== 200) {
      // fallback to /chat
      log('system', '[chat] /stream failed, falling back to /chat', 'info')
      const body2 = Buffer.from(JSON.stringify({ question }))
      const req2  = http.request({
        hostname: '127.0.0.1', port: CHAT_PORT,
        path: '/chat', method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Content-Length': body2.length },
      }, res2 => {
        let data = ''
        res2.on('data', c => { data += c })
        res2.on('end', () => {
          try {
            const parsed = JSON.parse(data)
            if (parsed.answer) {
              send('chat-stream-event', { type: 'token', text: parsed.answer })
            } else {
              send('chat-stream-event', { type: 'error', text: parsed.error || 'empty response' })
            }
          } catch { send('chat-stream-event', { type: 'error', text: `Parse error: ${data.slice(0,100)}` }) }
          send('chat-stream-event', { type: 'done' })
        })
      })
      req2.on('error', err => { send('chat-stream-event', { type: 'error', text: err.message }) })
      req2.write(body2)
      req2.end()
      res.resume()
      return
    }
    let buf = ''
    res.setEncoding('utf8')
    res.on('data', chunk => {
      buf += chunk
      const lines = buf.split('\n')
      buf = lines.pop()
      for (const line of lines) {
        if (!line.startsWith('data: ')) continue
        try { send('chat-stream-event', JSON.parse(line.slice(6))) } catch (_) {}
      }
    })
    res.on('end', () => { send('chat-stream-event', { type: 'done' }) })
  })
  req.on('error', err => {
    activeStreamReq = null
    if (err.code === 'ECONNRESET') return // aborted intentionally
    log('system', `[chat] http.request error: ${err.message}`, 'error')
    send('chat-stream-event', { type: 'error', text: err.message })
  })
  activeStreamReq = req
  req.write(body)
  req.end()
})

// Initial status ping
ipcMain.on('request-status', () => {
  Object.entries(procs).forEach(([key, p]) =>
    setStatus(key, p ? 'running' : 'stopped')
  )
})

// ─── Auto Mode ────────────────────────────────────────────────────────────────
let autoModeTimer  = null
let autoModeActive = false

function getAutoSettings () {
  const s = readSettings()
  return s.autoMode || {
    enabled:   false,
    time:      '19:00',
    days:      [1, 2, 3, 4, 5],   // Mon–Fri
    agentModel: selectedAgentModel,
    embedModel: selectedEmbedModel,
    budget:    60,
    focus:     'all',
    maxCalls:  25,
  }
}

function autoModeRunWorkflow () {
  const cfg = getAutoSettings()
  log('system', '[auto] Starting scheduled workflow…', 'info')
  send('auto-mode-workflow-start', {})

  // Step 1: git pull + incremental index via update.py
  const env = {
    ...process.env,
    PYTHONUNBUFFERED:  '1',
    PYTHONIOENCODING:  'utf-8',
    OVERRIDE_LLM_MODEL:   selectedLlmModel,
    OVERRIDE_CHAT_MODEL:  selectedChatModel,
    OVERRIDE_EMBED_MODEL: cfg.embedModel || selectedEmbedModel,
    OVERRIDE_AGENT_MODEL: cfg.agentModel || selectedAgentModel,
    CHAT_RAG_K: String(chatRagK),
  }

  log('system', '[auto] Step 1/3 — git pull + update…', 'info')
  const update = spawn(PYTHON, ['-u', 'update.py'], { cwd: SCRIPTS_DIR, shell: true, env })
  procs.update = update
  setStatus('update', 'running')

  update.stdout.on('data', d => d.toString().split('\n').forEach(l => l.trim() && log('update', l)))
  update.stderr.on('data', d => log('update', d.toString(), 'stderr'))

  update.on('close', code => {
    procs.update = null
    setStatus('update', code === 0 ? 'stopped' : 'error')
    log('system', `[auto] update.py exited (${code})`, code === 0 ? 'info' : 'error')
    send('indexer-done', { code })

    if (code !== 0) {
      log('system', '[auto] Workflow aborted — update failed.', 'error')
      send('auto-mode-workflow-done', { success: false, step: 'update' })
      return
    }

    // Step 2: incremental indexer
    log('system', '[auto] Step 2/3 — indexer…', 'info')
    const idx = spawn(PYTHON, ['-u', 'indexer.py', '--workers', String(indexerWorkers)], { cwd: SCRIPTS_DIR, shell: true, env })
    procs.indexer = idx
    setStatus('indexer', 'running')

    idx.stdout.on('data', d => {
      d.toString().split('\n').forEach(line => {
        if (line.startsWith('PROGRESS:')) {
          try { send('indexer-progress', JSON.parse(line.slice(9))) } catch (_) {}
        } else if (line.trim()) { log('indexer', line) }
      })
    })
    idx.stderr.on('data', d => log('indexer', d.toString(), 'stderr'))

    idx.on('close', idxCode => {
      procs.indexer = null
      setStatus('indexer', idxCode === 0 ? 'stopped' : 'error')
      log('system', `[auto] indexer.py exited (${idxCode})`, idxCode === 0 ? 'info' : 'error')
      send('indexer-done', { code: idxCode })

      if (idxCode !== 0) {
        log('system', '[auto] Workflow aborted — indexer failed.', 'error')
        send('auto-mode-workflow-done', { success: false, step: 'indexer' })
        return
      }

      // Step 3: agent
      log('system', `[auto] Step 3/3 — agent (${cfg.fullScan ? 'Full Scan' : cfg.budget + ' min'}, focus: ${cfg.focus || 'all'})…`, 'info')
      const agentArgs = [
        '-u', '-B', path.join(SCRIPTS_DIR, 'agent.py'),
        '--budget-minutes', cfg.fullScan ? '0' : String(cfg.budget || 60),
        '--focus',          cfg.focus    || 'all',
        '--max-calls',      String(cfg.maxCalls || 25),
        '--grep-limit',     '50',
        '--notes-k',        '8',
      ]
      const agent = spawn(PYTHON, agentArgs, { cwd: SCRIPTS_DIR, shell: true, env })
      procs.agent = agent
      setStatus('agent', 'running')
      send('process-status', { source: 'agent', status: 'running' })

      agent.stdout.on('data', raw => {
        raw.toString('utf8').split('\n').forEach(line => {
          if (!line.trim()) return
          if (line.startsWith('AGENT_PROGRESS:')) {
            try { send('agent-progress', JSON.parse(line.slice(15))) } catch {}
          } else if (line.startsWith('AGENT_FINDING:')) {
            try { send('agent-finding', JSON.parse(line.slice(14))) } catch {}
          } else if (line.startsWith('AGENT_DONE:')) {
            try { send('agent-done', JSON.parse(line.slice(11))) } catch {}
          } else {
            send('process-output', { source: 'agent', text: line, type: 'stdout' })
          }
        })
      })
      agent.stderr.on('data', raw => send('process-output', { source: 'agent', text: raw.toString(), type: 'stderr' }))
      agent.on('close', agentCode => {
        procs.agent = null
        setStatus('agent', agentCode === 0 ? 'stopped' : 'error')
        send('process-status', { source: 'agent', status: agentCode === 0 ? 'stopped' : 'error' })
        log('system', `[auto] agent.py exited (${agentCode})`, agentCode === 0 ? 'info' : 'error')
        send('auto-mode-workflow-done', { success: agentCode === 0 })
      })
    })
  })
}

function scheduleAutoMode () {
  if (autoModeTimer) { clearInterval(autoModeTimer); autoModeTimer = null }
  const cfg = getAutoSettings()
  if (!cfg.enabled) { autoModeActive = false; return }

  autoModeActive = true
  log('system', `[auto] Scheduler active — runs at ${cfg.time} on days ${cfg.days.join(',')}`, 'info')

  // Check every 60 seconds
  let lastFiredDate = ''
  autoModeTimer = setInterval(() => {
    const now  = new Date()
    const hhmm = now.getHours().toString().padStart(2, '0') + ':' + now.getMinutes().toString().padStart(2, '0')
    const day  = now.getDay()   // 0=Sun … 6=Sat
    const date = now.toDateString()

    if (hhmm === cfg.time && cfg.days.includes(day) && date !== lastFiredDate) {
      lastFiredDate = date
      // Don't start if something is already running
      if (procs.update || procs.indexer || procs.agent) {
        log('system', '[auto] Skipping — a process is already running.', 'info')
        return
      }
      autoModeRunWorkflow()
    }

    // Update status label every tick
    send('auto-mode-status', { enabled: true, time: cfg.time, days: cfg.days })
  }, 60_000)

  // Send initial status immediately
  send('auto-mode-status', { enabled: true, time: cfg.time, days: cfg.days })
}

ipcMain.on('auto-mode-set-enabled', (_, enabled) => {
  const s = readSettings()
  const am = s.autoMode || {}
  writeSettings({ ...s, autoMode: { ...am, enabled } })
  scheduleAutoMode()
  send('auto-mode-status', { enabled, time: am.time || '19:00', days: am.days || [1,2,3,4,5] })
})

ipcMain.on('auto-mode-save', (_, cfg) => {
  const s = readSettings()
  writeSettings({ ...s, autoMode: cfg })
  scheduleAutoMode()
})

ipcMain.handle('auto-mode-get', async () => getAutoSettings())
