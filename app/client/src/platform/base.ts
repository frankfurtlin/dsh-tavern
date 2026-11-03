// Runtime deployment base (e.g. '/tavern' when hosted below a subpath).
// Build-time BASE_URL is authoritative for production; dev falls back to ''.
export const APP_BASE = (import.meta.env.BASE_URL || '/').replace(/\/$/, '')
export function withBase(path: string) {
  if (!path.startsWith('/')) return path
  return APP_BASE + path
}
