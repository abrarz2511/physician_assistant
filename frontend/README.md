# Physician Assistant Frontend

Vue 3 + Vite frontend for the FastAPI backend.

## Run locally

```powershell
cd frontend
npm install
npm run dev
```

Keep the backend running on `http://127.0.0.1:8000`. Vite proxies `/api` and
`/ws` to the FastAPI app during development.

Optional overrides:

```powershell
$env:VITE_API_BASE="http://127.0.0.1:8000"
$env:VITE_WS_BASE="ws://127.0.0.1:8000/ws"
```
