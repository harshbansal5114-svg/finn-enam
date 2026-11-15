# FINN frontend (Next.js 14 App Router + TS + Tailwind)

    npm install
    cp .env.example .env.local      # NEXT_PUBLIC_API_URL (default http://localhost:8000)
    npm run dev                     # predev auto-syncs public/fallback_bundle.json

Fallback bundle: `npm run sync:bundle` copies ../backend/data/fallback_bundle.json, else downloads
`$NEXT_PUBLIC_API_URL/api/fallback-bundle`. Or set `FINN_BUNDLE_OUT=../frontend/public/fallback_bundle.json` on the backend.
**Commit public/fallback_bundle.json** before deploying to Vercel (Vercel can't reach localhost).

Vercel: root dir `frontend`, env `NEXT_PUBLIC_API_URL=https://<public-backend>`; backend CORS is open by default.
