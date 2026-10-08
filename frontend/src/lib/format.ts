/**
 * Plain formatting helpers.
 *
 * These live apart from `app.tsx` on purpose. React Fast Refresh can only
 * hot-update a module whose exports are all components, so a single ordinary
 * function alongside them forces a full page reload on every edit — losing the
 * signed-in state and whatever you were looking at.
 */

export function formatDate(value?: string) {
  return value
    ? new Date(value).toLocaleString(undefined, {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'})
    : '—';
}

export function formatDuration(seconds: number) {
  return Math.floor(seconds / 60) + ':' + String(seconds % 60).padStart(2, '0');
}
