import { useCallback, useEffect, useState } from 'react'

export type ThemePref = 'light' | 'dark' | 'system'

function apply(pref: ThemePref) {
  const dark = pref === 'dark' || (pref === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.dataset.theme = dark ? 'dark' : 'light'
}

export function useTheme() {
  const [pref, setPref] = useState<ThemePref>(() => (localStorage.getItem('theme') as ThemePref) || 'system')
  useEffect(() => {
    apply(pref)
    localStorage.setItem('theme', pref)
    if (pref !== 'system') return
    const mql = window.matchMedia('(prefers-color-scheme: dark)')
    const cb = () => apply('system')
    mql.addEventListener('change', cb)
    return () => mql.removeEventListener('change', cb)
  }, [pref])
  const cycle = useCallback(() => setPref((p) => (p === 'light' ? 'dark' : p === 'dark' ? 'system' : 'light')), [])
  return { pref, cycle }
}
