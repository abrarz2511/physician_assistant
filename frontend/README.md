# Physician Assistant Frontend

Vue 3 + Vite frontend for the FastAPI backend.

## Run locally

```powershell
cd frontend
npm install
npm run dev
```

Vite proxies `/api` and `/ws` to the deployed FastAPI app at
`https://physician-assistant-srck5q.fly.dev` during development. Production
builds call the same Fly deployment directly.

Optional overrides:

```powershell
$env:VITE_API_BASE="http://127.0.0.1:8000"
$env:VITE_WS_BASE="ws://127.0.0.1:8000/ws"
npm run dev
```

The overrides above switch local development back to a locally running
FastAPI server when needed.
