# Vercel Rollout Checklist and Env Matrix (5 Frontends -> 1 Unified Backend)

Date: 2026-03-27

## Current Deployment Position
- MandirMitra frontend: already on Vercel
- GruhaMitra frontend: already on Vercel
- InvestMitra frontend: already on Vercel
- MitraBooks frontend: not yet deployed (target Vercel)
- LegalMitra frontend: currently on Render (frontend + backend legacy path)

## Recommendation
- Keep one unified backend on Render.
- Keep/front-load all frontends on Vercel for consistency.
- Deploy MitraBooks frontend to Vercel next.
- LegalMitra frontend can remain on Render temporarily, but moving to Vercel is cleaner long-term.

## Shared Pre-Deploy Checklist (Must Pass)
1. Unified backend URL finalized (example: `https://api.sanmitra.com` or Render URL).
2. Backend `ALLOWED_ORIGINS` includes all Vercel frontend domains (exact origins, comma-separated).
3. Backend Google config set: `GOOGLE_OAUTH_CLIENT_IDS` contains valid web client IDs.
4. Frontend-to-backend connectivity test passes from each Vercel domain.
5. CORS + credentials behavior validated for apps using `withCredentials`.
6. API smoke tests: login, `/users/me`, and one core app flow for each frontend.

## Critical Unified-Backend Gap to Address
- Frontends currently do **not** consistently send `X-App-Key` headers.
- For a true multi-app unified backend, each frontend should send a fixed app key:
  - LegalMitra: `legalmitra`
  - GruhaMitra: `gruhamitra`
  - MandirMitra: `mandirmitra`
  - MitraBooks: `mitrabooks`
  - InvestMitra: `investmitra`

Without this, backend app-scoping may default incorrectly in mixed traffic.

## App-by-App Vercel Matrix

| App | Repo Path | Vercel Root | Framework/Build | Router Mode | Required Env Vars | Notes |
|---|---|---|---|---|---|---|
| MandirMitra | `external-repos/MandirMitra/frontend` | same | CRA (`npm run build`) | SPA rewrite already present in `vercel.json` | `REACT_APP_API_URL=https://<UNIFIED_BACKEND_ORIGIN>`; `REACT_APP_FALLBACK_API_URL=https://<UNIFIED_BACKEND_ORIGIN>`; `REACT_APP_GOOGLE_CLIENT_ID=<google_web_client_id>`; `REACT_APP_DEFAULT_TENANT_ID=<optional>` | Existing `vercel.json` CSP currently locks `script-src` to self; Google Sign-In may require allowing `https://accounts.google.com` in CSP. |
| GruhaMitra (web) | `external-repos/GharMitra/web` | preferably `external-repos/GharMitra` (uses root `vercel.json` build/output mapping) | Vite (`npm run build`) output `web/dist` | BrowserRouter + rewrite exists | `VITE_API_URL=https://<UNIFIED_BACKEND_ORIGIN>/api`; `VITE_SUPABASE_URL=<supabase_url>`; `VITE_SUPABASE_ANON_KEY=<supabase_anon_key>` | Keep Supabase keys configured or login UX degrades. |
| InvestMitra | `external-repos/InvestMitra/frontend` | same | CRA/CRACO (`npm run build`) | HashRouter (no deep-route rewrite required) | `REACT_APP_API_URL=https://<UNIFIED_BACKEND_ORIGIN>/api` | Current Google flow is via external OAuth redirect service; verify compatibility or migrate to backend `/api/v1/auth/google` path. |
| MitraBooks | `external-repos/MitraBooks/frontend` | same | Vite (`npm run build`) output `dist` | BrowserRouter (needs rewrite) | `VITE_API_URL=https://<UNIFIED_BACKEND_ORIGIN>` | First deploy pending. Add `vercel.json` SPA rewrite (`/:path* -> /index.html`). |
| LegalMitra | `external-repos/LegalMitra/frontend` | same (static multi-page) | Static HTML/JS (no npm build required) | Multi-page static | No env system currently; update `config.js` for prod backend base OR add API rewrite/proxy | Current `config.js` uses `/api/v1` in non-local. If hosted on Vercel with separate backend domain, configure explicit backend origin or rewrite `/api/v1/*` to backend. |

## Exact Value Template (Copy-Use)
- `UNIFIED_BACKEND_ORIGIN = https://<your-render-or-custom-domain>`
- MandirMitra:
  - `REACT_APP_API_URL=${UNIFIED_BACKEND_ORIGIN}`
  - `REACT_APP_FALLBACK_API_URL=${UNIFIED_BACKEND_ORIGIN}`
- GruhaMitra:
  - `VITE_API_URL=${UNIFIED_BACKEND_ORIGIN}/api`
- InvestMitra:
  - `REACT_APP_API_URL=${UNIFIED_BACKEND_ORIGIN}/api`
- MitraBooks:
  - `VITE_API_URL=${UNIFIED_BACKEND_ORIGIN}`
- LegalMitra (if moved to Vercel):
  - Set `API_BASE_URL` in `config.js` to `${UNIFIED_BACKEND_ORIGIN}/api/v1` (or configure equivalent rewrite)

## MitraBooks First-Time Vercel Deploy Checklist
1. Create Vercel project with root `external-repos/MitraBooks/frontend`.
2. Framework preset: Vite.
3. Build command: `npm run build`.
4. Output directory: `dist`.
5. Add env: `VITE_API_URL=https://<UNIFIED_BACKEND_ORIGIN>`.
6. Add SPA rewrite config (`vercel.json`).
7. Smoke test: login, companies, parties, invoices, transactions screens.

## Final Go-Live Validation (All Frontends)
1. Open each frontend domain and login successfully.
2. Confirm correct backend traffic namespace by app key.
3. Validate Google login where enabled.
4. Verify no CORS errors in browser console.
5. Verify primary API flows + one write transaction per app.
