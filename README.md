# natMSS Code Intelligence Agent

Ein lokaler KI-Agent der dein Repository automatisch analysiert, die Ergebnisse als Markdown-Notizen in deinen Obsidian Vault schreibt und dir ermöglicht, Fragen zum Code zu stellen.

---

## Voraussetzungen

- Python 3.11+
- [Ollama](https://ollama.com) installiert
- Obsidian Vault unter `C:\natMSSObsidian\natMSS`
- Repository unter `C:\natMSSProjects\mss`

---

## Einmalige Einrichtung

### 1. Python-Abhängigkeiten installieren
```powershell
cd C:\Users\Sim2\Documents\GitHub\LocalAIAgentWithRAG
pip install -r requirements.txt
```

### 2. Ollama-Modelle herunterladen
```powershell
ollama pull qwen2.5-coder:32b   # LLM für Code-Analyse
ollama pull mxbai-embed-large   # Embedding-Modell
```

### 3. Windows Task Scheduler einrichten (täglich 19:00 Uhr)
PowerShell **als Administrator** öffnen, dann:
```powershell
cd C:\Users\Sim2\Documents\GitHub\LocalAIAgentWithRAG
.\register_task.ps1
```
Ab jetzt läuft `update.py` jeden Abend automatisch um 19:00 Uhr.

---

## Tägliche Nutzung

### Repository indexieren (erster Start / manuelles Update)
Doppelklick auf **`start_indexer.bat`**

Das Script:
1. Startet Ollama neu mit paralleler Verarbeitung
2. Wartet bis Ollama bereit ist
3. Startet den Indexer — nur **geänderte Dateien** werden neu analysiert
4. Schreibt Markdown-Notizen in `C:\natMSSObsidian\natMSS\Code\` mit der exakten Ordnerstruktur des Repos

Beim **allerersten Start** werden alle Dateien analysiert — das dauert je nach Projektgröße mehrere Stunden.

### Alles neu indexieren (force)
```powershell
start_indexer.bat --force
```

### Den KI-Assistenten starten (Fragen zum Code stellen)
```powershell
python main.py
```
Der Agent lädt den Vault in den Arbeitsspeicher und beantwortet Fragen auf Basis der Notizen.

---

## Vault leeren und neu starten

Doppelklick auf **`clear_vault.bat`** — löscht nach Bestätigung:
- `C:\natMSSObsidian\natMSS\Code\` (alle generierten Notizen)
- `C:\natMSSObsidian\natMSS\.indexer_state.json` (Hash-Status)

Danach `start_indexer.bat` starten um alles neu aufzubauen.

---

## Dateien im Überblick

| Datei | Zweck |
|---|---|
| `config.py` | Zentrale Konfiguration (Pfade, Modelle, Worker-Anzahl) |
| `indexer.py` | Kern-Logik: Repo scannen, analysieren, Vault befüllen |
| `update.py` | `git pull` + inkrementeller Re-Index |
| `vector.py` | Lädt Vault-Notizen in den In-Memory Vektorspeicher |
| `main.py` | Interaktiver Chat-Agent |
| `start_indexer.bat` | Ollama starten + Indexer ausführen (Doppelklick) |
| `clear_vault.bat` | Vault und State-Datei leeren (Doppelklick) |
| `register_task.ps1` | Windows Task Scheduler einrichten (einmalig als Admin) |

---

## Konfiguration anpassen (`config.py`)

```python
LLM_MODEL        = "qwen2.5-coder:32b"  # Modell für Code-Analyse
EMBED_MODEL      = "mxbai-embed-large"  # Modell für Suche/Embeddings
INDEXER_WORKERS  = 4                    # Parallele Analyse-Threads
```

**INDEXER_WORKERS tunen:**
- GPU-Auslastung unter 70% → Wert erhöhen
- Ollama-Fehler / Timeouts → Wert verringern
- Wert in `start_indexer.bat` (Zeile `set WORKERS=`) immer gleich setzen!

**Alternative Modelle:**

| Zweck | Modell | VRAM |
|---|---|---|
| Code-Analyse (beste Qualität) | `qwen2.5-coder:32b` | ~20 GB |
| Code-Analyse (schneller) | `qwen2.5-coder:14b` | ~9 GB |
| Embeddings (beste Qualität) | `bge-m3` | ~1 GB |
| Embeddings (schnell) | `nomic-embed-text` | ~0.5 GB |

---

## Wie funktioniert das inkrementelle Update?

Jede analysierte Datei wird mit ihrem SHA-256 Hash in `.indexer_state.json` gespeichert. Beim nächsten Lauf wird der Hash verglichen — nur Dateien die sich geändert haben (oder neu sind) werden neu analysiert. Gelöschte Dateien werden auch aus dem Vault entfernt.

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
└── .indexer_state.json          ← interner Hash-Status (nicht bearbeiten)
```

Jede Notiz enthält:
- **Purpose** — was die Datei / Klasse macht
- **Classes & Functions** — alle Klassen und wichtigen Methoden
- **Relationships** — Abhängigkeiten als Obsidian Wikilinks (`[[KlassenName]]`)
- **Notes** — Design-Patterns, TODOs, Besonderheiten
