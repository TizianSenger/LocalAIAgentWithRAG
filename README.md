# natMSS Code Intelligence Agent

Eine vollständig **lokale** KI-Plattform zur automatischen Code-Analyse, interaktiven Code-Suche und autonomen Software-Qualitätsprüfung — komplett ohne Cloud, ohne API-Kosten, mit deinen eigenen Modellen via [Ollama](https://ollama.com).

---

## Was die Software kann

### 🔍 Code Indexer
Analysiert automatisch jede Quellcode-Datei im Repository mit einem lokalen LLM und schreibt strukturierte Markdown-Notizen in deinen Obsidian Vault. Nur **geänderte Dateien** werden bei jedem Lauf neu analysiert (Hash-basiertes Inkremental-Indexing). Unterstützt parallele Verarbeitung mit konfigurierbarer Worker-Anzahl.

### 💬 Chat (RAG-basierte Code-Suche)
Stellt einen lokalen HTTP-Server (Flask, Port 5001) bereit, über den du in natürlicher Sprache Fragen zu deinem Code stellen kannst. Antworten basieren auf einem **Retrieval-Augmented Generation (RAG)**-System: Relevante Notizen aus dem Obsidian Vault werden via ChromaDB semantisch gesucht und als Kontext an das LLM übergeben. Der Vault-Index wird persistent auf Disk gespeichert (`chrome_langchain_db/`) — beim Start wird nichts neu eingebettet, außer der Index ist leer.

### 🤖 Autonomer Code-Analyse-Agent
Ein eigenständiger Agent der dein Repository nach konkreten Code-Problemen durchsucht. Der Agent arbeitet mit einem konfigurierbaren **Budget** (Minuten) oder im **Full-Scan-Modus** (kein Zeitlimit) und mehreren **Strategien**:
- **Security** — SQL-Injection, fehlende Validierung, Hardcoded Secrets
- **Performance** — N+1-Queries, blockierendes I/O, überflüssige Objekterzeugung
- **Potential Bugs** — Null-Pointer-Risiken, Resource Leaks, Race Conditions
- **Architecture** — Zirkuläre Dependencies, God Classes, SOLID-Verletzungen
- **Code Smell** — Duplikate, tote Code-Pfade, Magic Numbers, schlechtes Naming

Der Agent nutzt dafür echte Tools und schreibt am Ende einen Markdown-Report direkt in den Obsidian Vault (`AgentReports/`).

**Verfügbare Tools des Agents:**

| Tool | Beschreibung |
|---|---|
| `SEARCH_NOTES(query)` | Semantische Suche im Obsidian Vault via ChromaDB |
| `GREP(pattern, .ext)` | Regex-Suche über alle Dateien mit gegebener Endung |
| `LIST_FILES(dir, .ext)` | Verzeichnis-Listing im Repo (für große Projekte) |
| `READ_FILE(path)` | Liest bis zu 120 Zeilen einer Datei |
| `READ_FILE(path, offset)` | Liest ab Zeile `offset` (für sehr große Dateien sektionsweise) |
| `GET_DEPENDENTS(Class)` | Alle Dateien die eine Klasse importieren/erweitern |
| `GET_CLASS_INFO(Class)` | Strukturinfo einer Klasse (Package, Extends, Injects) |
| `WRITE_FINDING(sev, cat, file, desc)` | Schreibt ein Finding in den Report |

**Robustheit:**
- LLM-Fehler: 3 Versuche mit exponentiellem Backoff
- Repeat-Schutz: nach 2 identischen Tool-Calls wird das Modell umgeleitet, nach 5 wird die Strategie abgebrochen
- Invalid-Output-Schutz: nach 4 ungültigen Antworten hintereinander bricht die Strategie ab
- Finding-Deduplizierung: gleiches Findings wird nie doppelt gespeichert
- Context-Trimming: ältere Tool-Ergebnisse werden kondensiert damit das Kontextfenster nicht überläuft
- VRAM-Freigabe: Modell wird nach Abschluss automatisch aus dem VRAM entladen (`keep_alive=0`)

### 📊 Dependency Graph
Generiert einen interaktiven Abhängigkeitsgraphen des gesamten Repositories. Nodes sind Klassen/Module, Kanten sind Import-/Vererbungs-Beziehungen. Einfärbt nach Ordner/Modul. Filterbar, durchsuchbar, zoombar — direkt in der App.

### 🖥️ Electron Desktop-App
Alle Features sind über eine einheitliche Desktop-Oberfläche steuerbar:
- **Logs-Tab** — Echtzeit-Ausgaben aller Prozesse (Ollama, Indexer, Update, Chat, Agent) mit Filterbuttons pro Quelle
- **Chat-Tab** — Direkte Konversation mit dem Code-Assistenten inkl. konfigurierbarem RAG-k-Wert
- **Graph-Tab** — Interaktiver Dependency-Graph
- **Agent-Tab** — Konfiguration und Echtzeit-Findings des Analyse-Agents mit Live-Statusleiste
- **Sidebar** — Start/Stop aller Prozesse, Model-Auswahl pro Komponente (Indexer LLM, Chat LLM, Agent LLM, Embed), Parallel-Worker-Konfiguration

---

## Voraussetzungen

- Python 3.11+
- [Ollama](https://ollama.com) installiert und im PATH
- Node.js + npm (für die Electron-App)
- Obsidian Vault unter `C:\natMSSObsidian\natMSS`
- Repository unter `C:\natMSSProjects\mss`

---

## Einmalige Einrichtung

### 1. Python-Abhängigkeiten installieren
```powershell
cd C:\Users\Sim2\Documents\GitHub\LocalAIAgentWithRAG
pip install -r requirements.txt
pip install langchain-text-splitters
```

### 2. Electron-Abhängigkeiten installieren
```powershell
cd ui
npm install
```

### 3. Ollama-Modelle herunterladen
```powershell
ollama pull qwen2.5-coder:32b   # LLM für Indexer, Chat und Agent
ollama pull mxbai-embed-large   # Embedding-Modell für RAG
```

### 4. Windows Task Scheduler einrichten (optional — täglich 19:00 Uhr)
PowerShell **als Administrator** öffnen:
```powershell
cd C:\Users\Sim2\Documents\GitHub\LocalAIAgentWithRAG
.\register_task.ps1
```
Ab jetzt läuft `update.py` jeden Abend automatisch — nur geänderte Dateien werden neu indexiert.

---

## App starten

```powershell
.\start_ui.bat
```

Oder manuell:
```powershell
cd ui
npx electron .
```

---

## Workflow

1. **Ollama starten** — Sidebar → *Start Ollama*
2. **Indexer starten** — Sidebar → *Start Indexer* (beim ersten Mal dauert das je nach Projektgröße mehrere Stunden)
3. **Chat API starten** — Sidebar → *Start Chat API* → Chat-Tab öffnen und Fragen stellen
4. **Agent starten** — Agent-Tab → Budget (oder "Full Scan" für kein Zeitlimit) und Strategie wählen → *Start Analysis*
5. **Graph generieren** — Graph-Tab → *Generate Graph*

---

## Konfiguration (`config.py`)

| Variable | Bedeutung |
|---|---|
| `REPO_PATH` | Pfad zum analysierten Repository |
| `VAULT_PATH` | Pfad zum Obsidian Vault |
| `LLM_MODEL` | Standard-LLM (kann in der App pro Komponente überschrieben werden) |
| `EMBED_MODEL` | Embedding-Modell für ChromaDB |
| `INDEXER_WORKERS` | Parallelität beim Indexieren (Richtwert: 4–6 bei einer GPU) |
| `CODE_EXTENSIONS` | Dateitypen die indexiert werden |
| `SKIP_DIRS` | Verzeichnisse die übersprungen werden (node_modules, .git, …) |

Model-Auswahl in der App überschreibt `config.py` zur Laufzeit via Umgebungsvariablen:
- `OVERRIDE_LLM_MODEL` — Indexer LLM
- `OVERRIDE_CHAT_MODEL` — Chat LLM
- `OVERRIDE_AGENT_MODEL` — Agent LLM
- `OVERRIDE_EMBED_MODEL` — Embedding-Modell

---

## Vault leeren

Sidebar → *Clear Vault* — löscht nach Bestätigung:
- `C:\natMSSObsidian\natMSS\Code\` (alle generierten Notizen)
- `C:\natMSSObsidian\natMSS\.indexer_state.json` (Hash-Status)
- `chrome_langchain_db/` (ChromaDB-Index — wird beim nächsten Chat-Start neu aufgebaut)

---

## Dateien im Überblick

| Datei | Zweck |
|---|---|
| `config.py` | Zentrale Konfiguration (Pfade, Modelle, Worker-Anzahl) |
| `indexer.py` | Kern-Logik: Repo scannen, LLM-Analyse, Vault befüllen |
| `update.py` | `git pull` + inkrementeller Re-Index |
| `vector.py` | ChromaDB-Index aufbauen / laden (persistent auf Disk) |
| `chat_api.py` | Flask-Server für RAG-Chat (Port 5001) |
| `agent.py` | Autonomer Code-Analyse-Agent mit Tool-Use |
| `dep_graph.py` | Dependency-Graph-Daten einlesen |
| `graph_gen.py` | Interaktiven HTML-Graphen generieren |
| `main.py` | Einfacher CLI-Chat (Legacy) |
| `config.py` | Zentrale Konfiguration (Pfade, Modelle, Worker-Anzahl) |
| `ui/` | Electron Desktop-App (main.js, renderer.js, preload.js) |
| `start_ui.bat` | App starten (Doppelklick) |
| `start_indexer.bat` | Ollama + Indexer ohne UI starten |
| `clear_vault.bat` | Vault und State-Datei leeren (CLI-Alternative) |
| `register_task.ps1` | Windows Task Scheduler einrichten (einmalig als Admin) |

---

## Modell-Empfehlungen

| Zweck | Modell | VRAM |
|---|---|---|
| Code-Analyse / Chat / Agent (beste Qualität) | `qwen2.5-coder:32b` | ~20 GB |
| Code-Analyse / Chat / Agent (schneller) | `qwen2.5-coder:14b` | ~9 GB |
| Embeddings (beste Qualität) | `bge-m3` | ~1 GB |
| Embeddings (schnell) | `mxbai-embed-large` | ~0.7 GB |
| Embeddings (sehr schnell) | `nomic-embed-text` | ~0.5 GB |

> **Tipp:** Indexer, Chat und Agent können unabhängig voneinander unterschiedliche Modelle verwenden — einfach in der App pro Dropdown wählen.

---

## Wie funktioniert das inkrementelle Update?

Jede analysierte Datei wird mit ihrem SHA-256 Hash in `.indexer_state.json` gespeichert. Beim nächsten Lauf wird der Hash verglichen — nur Dateien die sich geändert haben oder neu sind werden neu analysiert. Gelöschte Dateien werden auch aus dem Vault entfernt.

---

## Struktur im Obsidian Vault

```
C:\natMSSObsidian\natMSS\
├── Code\                        ← generierte Notizen (spiegelt Repo-Struktur)
│   ├── src\
│   │   ├── services\
│   │   │   └── UserService.md
│   │   └── ...
│   └── ...
├── AgentReports\                ← Analyse-Reports des autonomen Agents
│   └── agent_report_YYYYMMDD_HHMMSS.md
└── .indexer_state.json          ← interner Hash-Status (nicht bearbeiten)
```

Jede Notiz enthält:
- **Purpose** — was die Datei / Klasse macht
- **Classes & Functions** — alle Klassen und wichtigen Methoden
- **Relationships** — Abhängigkeiten als Obsidian Wikilinks (`[[KlassenName]]`)
- **Notes** — Design-Patterns, TODOs, Besonderheiten
