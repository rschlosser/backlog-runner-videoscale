# Backlog Runner

Autonomer Task-Runner für Claude Code. Liest Tasks aus `BACKLOG.md` und verarbeitet sie im Headless-Modus.

## Quick Start

```bash
# 1. Konfiguration
cp .env.example .env
# .env editieren: PROJECT_DIR setzen

# 2. CLAUDE.md an dein Projekt anpassen

# 3. Tasks in BACKLOG.md definieren

# 4. Runner starten
chmod +x runner.sh
./runner.sh
```

## Dateien

| Datei | Beschreibung |
|---|---|
| `runner.sh` | Hauptskript — parst Backlog, führt Tasks aus, handled Fehler |
| `BACKLOG.md` | Task-Queue mit Prioritäten und Dependencies |
| `CLAUDE.md` | Projekt-Kontext und Coding-Standards für Claude |
| `Dockerfile` | Container-Image für 24/7-Betrieb |
| `docker-compose.yml` | Multi-Worker Setup |
| `.env.example` | Konfigurationsvorlage |

## Task-Format

```markdown
## [TODO] TASK-001 | P1 | Mein Task-Titel
depends: TASK-000

Beschreibung was zu tun ist.
```

**Status**: `TODO` → `IN_PROGRESS` → `DONE` / `FAILED`
**Priorität**: P0 (kritisch) > P1 (hoch) > P2 (mittel) > P3 (niedrig)
**Dependencies**: Kommagetrennte Task-IDs oder `none`

## CLI-Optionen

```bash
./runner.sh              # Kontinuierlicher Betrieb
./runner.sh --dry-run    # Tasks anzeigen ohne auszuführen
./runner.sh --single     # Einen Task abarbeiten, dann beenden
./runner.sh --status     # Backlog-Status anzeigen
./runner.sh --worker-id worker-2  # Worker-ID setzen
```

## Docker

```bash
# Einzelner Worker
docker compose up -d

# Logs ansehen
docker compose logs -f

# Stoppen
docker compose down
```

Für parallele Worker den zweiten Service in `docker-compose.yml` aktivieren.

## Features

- **Prioritäts-basierte Abarbeitung** — P0 vor P1 vor P2 vor P3
- **Dependency-Tracking** — Tasks warten auf Abhängigkeiten
- **Rate-Limit-Handling** — Exponentieller Backoff bei 429/Rate Limits
- **Crash-Recovery** — State-Files ermöglichen Wiederaufnahme nach Absturz
- **Lock-Mechanismus** — Verhindert Konflikte bei parallelen Runnern
- **Logging** — Alle Aktionen werden in `logs/` protokolliert
