# Project Configuration for Claude

## Projekt
- **Name**: VideoScale AI
- **Sprache**: Python 3.12 / TypeScript
- **Framework**: FastAPI (Backend), Next.js 16 (Frontend), Arq (Worker)
- **Repo**: git@github.com:rschlosser/videoscale.git

## Architektur
- Monorepo mit Backend + Worker + Frontend + Shared
- **Backend**: FastAPI + Uvicorn → Railway
- **Worker**: Arq async task processor → Railway
- **Frontend**: Next.js 16 + React 19 + TailwindCSS 4 + shadcn/ui → Vercel
- **Datenbank**: Supabase PostgreSQL mit RLS
- **Storage**: Cloudflare R2 (Prod) / lokales `media/` (Dev)
- **Queue**: Redis (Arq)
- **Auth**: Supabase Auth (Google OAuth + email/password)
- **Payments**: Stripe

## Coding Standards
- Sprache für Code: Englisch
- Sprache für Kommentare: Englisch
- Commit Messages: Conventional Commits (feat:, fix:, chore:, etc.)
- Immer Tests schreiben für neue Features
- Keine Secrets in den Code committen
- FFmpeg via subprocess — kein MoviePy/OpenCV
- Backend Tests: pytest (`tests/`)
- E2E Tests: Playwright (`e2e/`)

## Wichtige Pfade
- Backend: `backend/app/`
- Worker: `worker/app/`
- Shared Modules: `shared/`
- Frontend: `frontend/src/`
- Tests: `tests/`
- E2E Tests: `e2e/`
- Deploy Scripts: `deploy-integration.sh`, `deploy-production.sh`, `deploy-stable.sh`
- Docker: `docker-compose.yml`
- Projekt-CLAUDE.md: `CLAUDE.md` (im Projekt-Root, enthält detaillierte Architektur-Doku)

## Regeln für den Backlog Runner
- Arbeite immer nur an dem zugewiesenen Task
- Arbeite auf dem **dev** Branch — Commits auf dev lösen ein Deployment auf INT aus
- Erstelle einen Commit pro abgeschlossenem Task (Conventional Commits)
- Pushe den Commit auf origin/dev nach Abschluss
- Bei Unklarheiten: Task als FAILED markieren statt zu raten
- Halte dich an die bestehende Code-Struktur
- Beachte die CLAUDE.md im Projekt-Root für detaillierte Architektur-Infos
- Keine .env oder Secrets committen
