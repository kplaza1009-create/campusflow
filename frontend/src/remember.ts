// Small per-device memory for form defaults (the last course used, and so on).
// Browser storage can be unavailable (private mode, blocked site data), so
// every access is guarded and the app works without it.

export function remembered(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

export function remember(key: string, value: string) {
  try {
    localStorage.setItem(key, value)
  } catch {
    /* storage unavailable; nothing to remember */
  }
}

/** True when a key press is going into a text field, so global shortcuts should ignore it. */
export function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null
  return !!el && (el.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(el.tagName))
}
