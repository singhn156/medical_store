DoseDeck — Deploy to Render (Quick Guide)

This file explains the easiest way to deploy DoseDeck to Render using the Python web service option (backend serves frontend).

1) Push your repo to GitHub/GitLab
- Ensure the repository contains `backend/`, `frontend/`, and `render.yaml` at the repo root.

2) Create a new Web Service on Render
- In Render: New → Web Service → Connect your repo → Select branch (e.g., `main`).
- Environment: select `Python` (not Docker).
- Root Directory: leave blank (repo root).

3) Build & Start commands
- Build Command:
  pip install --no-cache-dir -r requirements.txt
- Start Command:
  uvicorn backend.main:app --host 0.0.0.0 --port $PORT

4) Environment variables (add these in Render service settings)
- `DATABASE_URL`: for production use a Postgres connection (e.g. `postgres://user:pass@host:5432/dbname`).
  - NOTE: the default SQLite file is ephemeral on Render; use Postgres for persistent data.
- `AI_MODE`: `mock` (quick demo) or `auto`/`groq` for real AI.
- `DEMO_MODE`: `true` or `false`.
- `PRESENTATION_MODE`: `true` (optional).
- `GROQ_API_KEY`: set only if using Groq/AI provider.

5) Health check
- Health Path: `/api/health` (Render health check or just test endpoint after deploy).

6) Optional: use Docker on Render
- If you prefer Docker, choose New → Web Service → Docker and point to the repo.
- Edit the `backend/Dockerfile` to start with `--port $PORT` or set the container port accordingly.

7) Optional: host only frontend as a static site
- New → Static Site → Publish Directory: `frontend`
- If you do this, update frontend code or set an env var so the UI calls the backend service URL (CORS).

8) Post-deploy checks
- Visit `https://<your-service>.onrender.com/api/health`.
- Open root URL — it should serve the frontend index and the app should function.
- If using AI features, confirm `/api/ai/status` returns enabled when `GROQ_API_KEY` is set.

9) `render.yaml`
- A `render.yaml` was added to the repo for convenience. You can edit it before creating services via the Render Dashboard or use it with `render` CLI.

If you want, I can:
- Patch `backend/Dockerfile` to use `$PORT` for Docker deployments, or
- Create a small `README` demo script with exact environment variable values to paste into Render.

