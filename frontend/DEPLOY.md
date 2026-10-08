# Deploying the app to Vercel

## Project settings

| Setting | Value |
|---|---|
| Root Directory | `frontend` |
| Framework Preset | Vite |
| Build Command | `npm run build` (the default from `vercel.json`) |
| Output Directory | `dist` |
| Node version | 20 or 22 |

`vercel.json` sets the rest. Two parts of it are load-bearing:

**The rewrite.** This is a single-page app: the server has no `/app/dashboard`
file. Without the rewrite, the first visit works — React Router handles it —
and then refreshing that page, or opening a link to it, returns Vercel's 404.
Every path that is not an asset falls through to `index.html` so the router can
resolve it. The `(?!assets/)` is what keeps a missing script returning a real
404 instead of silently serving HTML, which otherwise shows up as a baffling
`Unexpected token '<'` in the console.

**The cache headers.** Vite fingerprints filenames in `assets/`, so those are
immutable and can be cached for a year. `index.html` is deliberately not in
that rule: it must be revalidated, or a returning visitor keeps an old page
that points at asset names the new deploy no longer has.

## Environment variable

Set one, under Settings → Environment Variables:

```
VITE_API_URL = https://<your-render-service>.onrender.com
```

Set it for Production, Preview and Development. Two things worth knowing:

- **It is baked in at build time.** Changing it needs a redeploy, not a
  restart — `import.meta.env` is substituted by Vite during the build, and
  nothing reads the variable at runtime.
- **Nothing secret belongs here.** Every `VITE_` value ends up in the bundle
  and is readable by anyone who opens the site. API keys, tokens and the
  Supabase service key stay in the backend.

Leave `VITE_API_URL` unset and the app runs entirely on its in-memory fixtures,
which is a reasonable way to publish a preview of the design with no backend
behind it.

## The matching backend setting

The API refuses cross-origin requests from anywhere not on its allowlist, so
add the Vercel domain to `FRONTEND_URL` on Render:

```
FRONTEND_URL=https://calling-agent.vercel.app
```

Comma-separate to add more. A browser treats every spelling as a separate
origin, so include each domain the app is actually served from — the
`*.vercel.app` production domain, and any custom domain. Preview deployments
get a new URL per commit; if you need those to reach the API, add the stable
branch alias rather than trying to list each one.
