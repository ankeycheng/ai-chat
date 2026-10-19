/** Redis Stream ID helpers — always compare numerically (spec C7 / T14). */
export function parseStreamId(id: string): [number, number] {
  const m = /^(\d+)-(\d+)$/.exec(id)
  if (!m) throw new Error(`invalid stream id ${id}`)
  return [Number(m[1]), Number(m[2])]
}

export function compareStreamIds(a: string, b: string): number {
  const [am, as] = parseStreamId(a)
  const [bm, bs] = parseStreamId(b)
  if (am !== bm) return am < bm ? -1 : 1
  if (as !== bs) return as < bs ? -1 : 1
  return 0
}

export function isNewer(id: string, than: string | null | undefined): boolean {
  return !than || compareStreamIds(id, than) > 0
}
