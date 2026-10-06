# VoxOps frontend

A polished, browser-only React, TypeScript, Vite and Tailwind frontend for voice operations. The public website now includes Home, Product, Solutions, Pricing, About us, Resources and Contact pages with original image assets. The signed-in workspace includes simulated authentication, onboarding, dashboard, agents, knowledge, channels, live calls, history, messages, guides, team, usage and settings.

## Run

1. Install dependencies: npm install
2. Start the app: npm run dev
3. Open http://localhost:5173/
4. Build a production bundle: npm run build

Sign in with any email and a password of at least six characters, or use the demo sign-in button. The demo opens the Northstar University workspace; use the sidebar selector to switch to Harbor Health.

## Demo behavior

- All product data goes through src/lib/api/index.ts and its in-memory mock store. USE_MOCK is the only shipped mode.
- About 400 seeded calls cover 90 days and multiple recording, channel and call states.
- Browser refresh preserves the simulated session and theme. Product mutations reset to the fixture data.
- Credential inputs are write-only. The input is cleared on submit; the store receives only the final four characters and audit metadata.
- Uploads, indexing, calls, messages, connections, live events and the public contact form are simulations. No API, database, auth provider, telephony, cloud service or actual message is connected.
- READY recordings use a locally generated sample audio asset. They are not customer recordings.
- CSV and XLSX exports are generated in the browser.

## Structure

- src/marketing.tsx and src/marketing.css: public pages and responsive marketing design
- src/pages.tsx: signed-in workspace screens
- src/workspace.css: workspace visual design
- src/style.css: shared tokens, controls and light/dark themes
- src/lib/types.ts: entity types
- src/lib/fixtures/: seed data and guides
- src/lib/api/: typed mock adapter and mutations
- src/lib/exportXlsx.ts: client-side spreadsheet export
- public/*.png: original site imagery

The app remains local and is not deployed.
