import { useEffect, useState } from 'react'

/**
 * false once the tab has been hidden for `graceMs`. Background tabs then release their SSE
 * connections (browsers allow only ~6 HTTP/1.1 connections per origin across ALL tabs);
 * on return the normal load/recovery flow (§9.3) runs again, so nothing is lost.
 */
export function usePageActive(graceMs = 30_000): boolean {
  const [active, setActive] = useState(() => typeof document === 'undefined' || !document.hidden)
  useEffect(() => {
    let timer: number | null = null
    const onChange = () => {
      if (timer) window.clearTimeout(timer)
      timer = null
      if (document.hidden) timer = window.setTimeout(() => setActive(false), graceMs)
      else setActive(true)
    }
    document.addEventListener('visibilitychange', onChange)
    return () => {
      document.removeEventListener('visibilitychange', onChange)
      if (timer) window.clearTimeout(timer)
    }
  }, [graceMs])
  return active
}
