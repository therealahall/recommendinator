const CONTENT_TYPE_LABELS: Record<string, string> = {
  book: 'Book',
  movie: 'Movie',
  tv_show: 'TV Show',
  video_game: 'Video Game',
}

const STATUS_LABELS: Record<string, string> = {
  completed: 'Completed',
  in_progress: 'In Progress',
  plan_to_consume: 'Plan to Consume',
  dropped: 'Dropped',
  on_hold: 'On Hold',
}

export function formatContentType(type: string): string {
  return CONTENT_TYPE_LABELS[type] ?? type
}

export function formatStatus(status: string): string {
  return STATUS_LABELS[status] ?? status
}

export function formatScore(score: number): string {
  return (score * 100).toFixed(0)
}

export interface ScoreSegment {
  key: string
  percent: number
  tone: 'lead' | 'rest' | 'penalty'
}

export interface ScoreShare {
  key: string
  value: number
  weight: number
  points: number
}

/** Heaviest signal first: a row states how well the item did, not how far that
 *  moved the score, so order is the only thing left saying which counted more. */
export function scoreShares(
  breakdown: Record<string, number>,
  weights: Record<string, number>,
): ScoreShare[] {
  const weightOf = (key: string) => weights[key] ?? 1
  const total = Object.keys(breakdown).reduce((sum, key) => sum + weightOf(key), 0)
  if (total === 0) return []
  return Object.entries(breakdown)
    .map(([key, value]) => {
      const weight = weightOf(key)
      return { key, value, weight, points: (value * weight * 100) / total }
    })
    .sort((one, two) => two.weight - one.weight || two.value - one.value)
}

function penaltyPoints(shares: ScoreShare[], varietyPenalty: number): number {
  return shares.reduce((sum, share) => sum + share.points, 0) * varietyPenalty
}

const SPINE_LEADS = 4

export function scoreSpine(shares: ScoreShare[], varietyPenalty: number): ScoreSegment[] {
  // Widest slice first, unlike the panel: this is one bar of the composite.
  const ranked = shares
    .filter((share) => share.points > 0)
    .sort((one, two) => two.points - one.points)
  const parts: ScoreSegment[] = ranked
    .slice(0, SPINE_LEADS)
    .map(({ key, points }) => ({ key, percent: points, tone: 'lead' as const }))
  const tail = ranked.slice(SPINE_LEADS).reduce((sum, share) => sum + share.points, 0)
  if (tail > 0) parts.push({ key: 'rest', percent: tail, tone: 'rest' })
  const lost = penaltyPoints(shares, varietyPenalty)
  if (lost > 0) parts.push({ key: 'penalty', percent: lost, tone: 'penalty' })

  const total = parts.reduce((sum, part) => sum + part.percent, 0)
  if (total === 0) return []
  return parts.map((part) => ({ ...part, percent: (part.percent / total) * 100 }))
}

export function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString()
}

/** Largest unit first, so the caller below stops at the first that fits. */
const RELATIVE_UNITS: ReadonlyArray<[Intl.RelativeTimeFormatUnit, number]> = [
  ['year', 31557600],
  ['month', 2629800],
  ['day', 86400],
  ['hour', 3600],
  ['minute', 60],
]

export function formatRelativeTime(iso: string, now: Date = new Date()): string {
  const seconds = (new Date(iso).getTime() - now.getTime()) / 1000
  // Pinned to 'en' like every other string in this UI, so the line does not
  // change language with the host locale while the words around it do not.
  const format = new Intl.RelativeTimeFormat('en', { numeric: 'auto' })
  for (const [unit, size] of RELATIVE_UNITS) {
    if (Math.abs(seconds) >= size) {
      return format.format(Math.round(seconds / size), unit)
    }
  }
  return format.format(Math.round(seconds), 'second')
}

export function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  const secs = Math.round(seconds % 60)
  return `${minutes}m ${secs}s`
}

export function formatStatusForContentType(status: string, contentType: string): string {
  if (status === 'currently_consuming') return 'In Progress'
  if (status === 'completed') return 'Completed'
  if (status === 'unread') {
    if (contentType === 'video_game') return 'Unplayed'
    if (contentType === 'movie' || contentType === 'tv_show') return 'Unwatched'
    return 'Unread'
  }
  return status
}

/** The series line as it is shown and as it is read out: "#4" alone is
 *  punctuation a screen reader may drop. */
export function formatSeries(
  name: string | null,
  index: number | null,
): { shown: string; spoken: string } {
  if (!name) return { shown: '', spoken: '' }
  if (index === null) return { shown: name, spoken: `Series: ${name}` }
  return { shown: `${name} #${index}`, spoken: `Series: ${name}, number ${index}` }
}

export function formatScorerName(key: string): string {
  return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

const SECTION_ACRONYMS: Record<string, string> = {
  api: 'API',
  url: 'URL',
  cors: 'CORS',
  db: 'DB',
}

export function humanizeSection(section: string): string {
  return section
    .replace(/[_-]+/g, ' ')
    .trim()
    .split(/\s+/)
    .map((word) => SECTION_ACRONYMS[word.toLowerCase()] ?? capitalize(word))
    .join(' ')
}

export function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}

/** Keyed on the source because `for`/`aria-describedby` resolve a duplicated id
 *  to the first match, unnaming the second panel's control. `_` survives: a
 *  source id may carry either separator, so collapsing it collided
 *  `gog_work` with `gog-work`. */
export function domId(prefix: string, sourceId: string): string {
  return `${prefix}-${sourceId.replace(/[^a-zA-Z0-9_-]/g, '-')}`
}

/** A per-item failure hits every item or none, so an uncapped list is hundreds
 *  of lines in a region the Data page always shows. */
const MAX_SHOWN_ERRORS = 5

/** Bound a sync error list, keeping the total the server stated: the client
 *  never infers one from a length, which is what let two "and N more" tails
 *  land under the same list. */
export function boundSyncErrors(
  messages: string[],
  omitted: number,
): { shown: string[]; total: number; hidden: number } {
  const shown = messages.slice(0, MAX_SHOWN_ERRORS)
  const total = messages.length + omitted
  return { shown, total, hidden: total - shown.length }
}

/** Poll-driven progress coarsened to the crossings worth speaking. Announcing
 *  every tick queues a sentence behind every other one, and a screen reader
 *  hears nothing else on the page for the length of the run (WCAG 4.1.3). */
const ANNOUNCE_STEP_PERCENT = 25

export function progressMilestone(percent: number): number {
  return Math.min(
    100,
    Math.floor(Math.max(0, percent) / ANNOUNCE_STEP_PERCENT) * ANNOUNCE_STEP_PERCENT,
  )
}

export function truncate(str: string, maxLen: number): string {
  return str.length <= maxLen ? str : str.substring(0, maxLen - 3) + '...'
}
