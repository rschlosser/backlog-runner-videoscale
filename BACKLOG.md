# Backlog

> Tasks werden vom Runner automatisch abgearbeitet.
> Format: `## [STATUS] TASK-XXX | P0-P3 | Titel`
> Status: `TODO`, `IN_PROGRESS`, `DONE`, `FAILED`
> Priorität: P0 (kritisch) > P1 (hoch) > P2 (mittel) > P3 (niedrig)

---

## [TODO] TASK-001 | P1 | Projekt-Struktur aufsetzen
depends: none

Erstelle die grundlegende Projektstruktur mit den nötigen Verzeichnissen
und Konfigurationsdateien. Stelle sicher, dass alle Abhängigkeiten
definiert sind.

---

## [TODO] TASK-002 | P2 | Unit Tests einrichten
depends: TASK-001

Richte ein Test-Framework ein und schreibe erste Unit Tests
für die Kernfunktionalität.

---

## [TODO] TASK-003 | P2 | CI/CD Pipeline konfigurieren
depends: TASK-001

Erstelle eine GitHub Actions Pipeline mit Linting, Tests und Build-Steps.

---
