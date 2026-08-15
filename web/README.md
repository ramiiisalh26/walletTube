# YTSearch Web

Next.js 14 (App Router) frontend for the YTSearch semantic search API.

## Setup

```bash
cd web
npm install
cp .env.local.example .env.local   # already created for local dev
npm run dev
```

Open http://localhost:3000

## Backend

This app calls the FastAPI backend (`python/api/main.py`). Start it first:

```bash
cd ../python
uvicorn api.main:app --host 0.0.0.0 --port 8080 --reload
```

`NEXT_PUBLIC_API_URL` in `.env.local` must point at that server (default
`http://localhost:8080`). The backend's `cors_origins` already allows
`http://localhost:3000`.

## Structure

```
src/
  app/
    layout.tsx          # shell: header + fonts
    page.tsx            # landing page with hero search
    search/page.tsx     # /search?q=... results route
    globals.css
  components/
    SearchBar.tsx       # input that routes to /search
    SearchView.tsx      # fetch + render states (client)
    ResultCard.tsx      # one hit: thumbnail, inline player, snippet
  lib/
    api.ts              # typed client for POST /api/search
    session.ts          # anonymous session id
    format.ts           # timestamp / view-count helpers
  types/
    api.ts              # mirrors python/api/schemas/search.py
```

## API contract

- `POST /api/search` with `{ query, session_id?, size?, page? }` → `SearchResponse`
- Results deep-link to the timestamp via `youtube_url` / `embed_url`.

## Known gaps / next steps

- **Click tracking** (`POST /api/search/click`) needs the DB integer `video_id`
  and `chunk_id`, which `SearchResult` does not yet expose. `recordClick()` is
  implemented in `lib/api.ts` but not wired into the UI until the backend adds
  those ids.
- Auth, pricing page, and account/usage UI are not built yet.
