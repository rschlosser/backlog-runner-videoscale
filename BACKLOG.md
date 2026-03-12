# Backlog

> Tasks werden vom Runner automatisch abgearbeitet.
> Format: `## [STATUS] TASK-XXX | P0-P3 | Titel`
> Status: `TODO`, `IN_PROGRESS`, `DONE`, `FAILED`
> Priorität: P0 (kritisch) > P1 (hoch) > P2 (mittel) > P3 (niedrig)

---

## [DONE] TASK-001 | P1 | E2E-Tests erweitern und stabilisieren
depends: none

Analysiere die bestehenden E2E-Tests im Verzeichnis `e2e/` und erweitere sie:
- Prüfe welche kritischen User-Flows noch nicht abgedeckt sind (z.B. Projekt erstellen, Voice auswählen, Hook generieren, Render starten)
- Stabilisiere bestehende Tests (robuste Selektoren, angemessene Timeouts)
- Stelle sicher, dass die Tests mit `npx playwright test` erfolgreich durchlaufen
- Orientiere dich an der bestehenden playwright.config.ts

---

## [DONE] TASK-002 | P2 | API-Error-Handling vereinheitlichen
depends: none

Analysiere die API-Routen in `backend/app/routes/` und vereinheitliche das Error-Handling:
- Erstelle ein konsistentes Error-Response-Format (z.B. `{"error": "...", "code": "...", "detail": "..."}`)
- Ersetze inkonsistente HTTPException-Nutzung durch ein einheitliches Pattern
- Stelle sicher, dass alle Endpoints sinnvolle HTTP-Statuscodes zurückgeben
- Schreibe Tests für die Error-Cases in `tests/`

---

## [DONE] TASK-003 | P2 | Backend Unit-Test Coverage erhöhen
depends: none

Erhöhe die Test-Abdeckung für die Backend-Module:
- Analysiere mit `pytest --cov=backend --cov=shared` welche Module schlecht abgedeckt sind
- Schreibe Unit-Tests für die wichtigsten ungetesteten Funktionen in `shared/` und `backend/app/`
- Fokussiere auf Business-Logik (pipeline.py, director.py, credit_costs.py, storage.py)
- Tests sollen mit `pytest` erfolgreich durchlaufen

---

## [DONE] TASK-004 | P3 | Docstrings für shared/ Module ergänzen
depends: none

Ergänze Docstrings für alle öffentlichen Funktionen und Klassen in `shared/`:
- Verwende Google-Style Docstrings
- Beschreibe Parameter, Return-Werte und wichtige Seiteneffekte
- Fokussiere auf die Haupt-Module: pipeline.py, director.py, veed_client.py, elevenlabs_client.py, storage.py, render_project.py
- Keine bestehende Logik ändern, nur Dokumentation ergänzen

---

## [DONE] TASK-005 | P1 | AI-Chat-Kontext bei Projektwechsel zurücksetzen
depends: none

Bug: Wenn der User zwischen Projekten wechselt, bleibt der AI-Agent/Chat im selben Kontext des vorherigen Projekts. Die Chat-History wird nicht geleert — alte Nachrichten bleiben sichtbar und werden teilweise doppelt angezeigt, obwohl der Projektname im Header korrekt wechselt.

Reproduktion: Projekt A öffnen → AI Agent fragen ("What's the status of my project?") → Projekt B wechseln → Chat zeigt noch die Antworten von Projekt A, teilweise dupliziert.

- Identifiziere im Frontend (`frontend/src/`), wo der Projektwechsel stattfindet (z.B. Projekt-Auswahl, Navigation)
- Finde die State-Verwaltung des AI-Chat/Agent (Chat-History, Kontext, Session)
- Stelle sicher, dass beim Wechsel des aktiven Projekts der Chat-State vollständig zurückgesetzt wird (History leeren, neuen Kontext mit Projektdaten initialisieren)
- Der Projektname im Header wechselt bereits korrekt — das Problem liegt nur bei der Chat-Message-History
- Falls es eine Backend-Session gibt: stelle sicher, dass auch dort eine neue Session gestartet wird
- Schreibe einen Test, der verifiziert, dass nach einem Projektwechsel der Chat-Kontext leer ist bzw. zum neuen Projekt gehört

---

## [TODO] TASK-006 | P3 | Stripe Production-Keys integrieren
depends: TASK-005, TASK-007, TASK-008, TASK-009, TASK-010

Stripe ist aktuell nur mit Test-Keys konfiguriert. Neue Accounts können den vollen Workflow nicht durchlaufen, weil Payments in Production nicht funktionieren. Auf Production-Stripe umstellen.

HINWEIS: Erfordert manuelle Konfiguration — Remo muss Live-Keys aus dem Stripe Dashboard holen (Secret Key, Webhook Secret, Price IDs) und in Railway setzen.

- Prüfe die aktuelle Stripe-Integration im Backend (`backend/app/`) — welche Env-Vars werden verwendet (z.B. `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`, `STRIPE_PRICE_ID`)
- Stelle sicher, dass die Konfiguration zwischen Test- und Prod-Keys über Environment-Variablen gesteuert wird (NICHT hardcoded)
- Prüfe, ob Webhook-Endpoints für Production korrekt konfiguriert sind (Stripe Dashboard muss separat konfiguriert werden — dokumentiere was dort eingestellt werden muss)
- Aktualisiere `.env.example` mit den benötigten Production-Variablen (ohne echte Secrets!)
- Prüfe, ob es Stellen im Code gibt, die explizit auf Stripe-Test-Mode prüfen oder Test-spezifische Logik enthalten
- Stelle sicher, dass die Checkout-Session, Subscription-Handling und Webhook-Verarbeitung mit Live-Keys kompatibel sind
- WICHTIG: Keine echten Stripe Production-Keys committen — nur Env-Var-Referenzen

---

## [DONE] TASK-007 | P0 | Render-Fehler: Body-Video nicht gefunden (FileNotFoundError)
depends: none

Bug: Render schlägt bei 10% fehl mit `FileNotFoundError: No avatar/base body video found in media/<project-id>/videoscale`. Der Fehler tritt in `shared/render_project.py:416` auf, aufgerufen von `worker/app/tasks.py:789`.

Traceback:
```
File "/app/worker/app/tasks.py", line 789, in render_project
    result = await render_project_pipeline(
File "/app/shared/render_project.py", line 416, in render_project_pipeline
    raise FileNotFoundError(
FileNotFoundError: No avatar/base body video found in media/99ab8fbf-98d8-46b9-ba71-0c47e89cad0e/videoscale
```

- Analysiere `shared/render_project.py` Zeile 416 — welches Video wird dort erwartet und in welchem Pfad?
- Prüfe, ob das Body-Video korrekt hochgeladen/gespeichert wird (Storage-Pfad, R2 vs lokal)
- Prüfe, ob die Body Variation "Version A" mit "0 seg" (0 Segmente) das Problem verursacht — möglicherweise wurde kein Video-Content erstellt
- Stelle sicher, dass die Pipeline vor dem Render prüft, ob alle benötigten Assets vorhanden sind, und eine verständliche Fehlermeldung liefert statt eines Tracebacks
- Schreibe einen Test für den Fall, dass das Body-Video fehlt

---

## [DONE] TASK-008 | P1 | CTAs werden im Rendering-Prozess nicht berücksichtigt
depends: none

Bug: CTAs (Call-to-Actions) werden im Rendering nicht eingebaut, obwohl sie im Projekt konfiguriert sind. Der Tester berichtet, dass CTAs im fertigen Video fehlen.

- Analysiere die Render-Pipeline in `shared/render_project.py` — wo werden CTAs in die Timeline/das Video eingefügt?
- Prüfe, ob CTAs aus der Datenbank korrekt geladen werden (Supabase Query, Projekt-Zuordnung)
- Prüfe die FFmpeg-Kommandos — werden CTA-Overlays/Segmente korrekt ins Video gerendert?
- Falls CTAs noch nicht in der Pipeline implementiert sind: implementiere die CTA-Integration (Text-Overlay oder Segment am Video-Ende)
- Schreibe Tests, die verifizieren, dass CTAs im Render-Output vorhanden sind

---

## [DONE] TASK-009 | P2 | B-Roll-Preview zeigt leere/blaue Frames bis Storyline neu geladen wird
depends: none

Bug: B-Rolls werden im Studio als blaue/leere Frames angezeigt. Nach dem Neuladen der Storyline im Studio funktioniert es wieder korrekt. Das deutet auf ein Caching- oder State-Synchronisierungs-Problem hin.

- Analysiere im Frontend, wie B-Roll-Assets geladen und im Studio/Preview angezeigt werden
- Prüfe, ob B-Roll-URLs korrekt aufgelöst werden beim ersten Laden (signed URLs abgelaufen? Race Condition beim Asset-Loading?)
- Prüfe, ob die Storyline-Daten vollständig geladen sind bevor die Preview gerendert wird — möglicherweise fehlen B-Roll-Referenzen beim initialen Load
- Stelle sicher, dass B-Roll-Assets nach dem Generieren/Uploaden sofort korrekt im Studio angezeigt werden, ohne manuelles Neuladen
- Schreibe einen Test, der verifiziert, dass B-Roll-Previews nach dem ersten Laden korrekt dargestellt werden

---

## [DONE] TASK-010 | P2 | Mobile-optimierte Views für die Anwendung
depends: none

Die Anwendung ist aktuell nur für Desktop optimiert. Das UI soll mobile-freundlich gestaltet werden.

- Analysiere das bestehende Layout im Frontend (`frontend/src/`) — Sidebar, Content-Bereich, Preview-Panel
- Implementiere responsive Breakpoints (TailwindCSS 4): Sidebar als Hamburger-Menu auf Mobile, Content einspaltig
- Prüfe alle Hauptseiten: Home, Projects, Studio, Render, Media — auf mobile Darstellung optimieren
- Stelle sicher, dass der AI-Agent-Chat auf Mobile als Fullscreen-Overlay oder Bottom-Sheet dargestellt wird
- Preview-Player muss auf kleinen Screens korrekt skalieren (kein Overflow, kein abgeschnittenes Video)
- Output-Format-Selector (9:16, 4:5, 1:1, 1.91:1) muss auf Mobile touch-freundlich sein
- Teste mit gängigen Viewports (375px, 390px, 428px) und schreibe Playwright-Tests für Mobile-Viewport

---
