/** Human-readable content type labels */
const CONTENT_TYPE_LABELS: Record<string, string> = {
  book: 'Book',
  movie: 'Movie',
  tv_show: 'TV Show',
  video_game: 'Video Game',
}

/** Human-readable status labels */
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

export function formatDate(dateStr: string): string {
  return new Date(dateStr).toLocaleDateString()
}

export function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`
  const minutes = Math.floor(seconds / 60)
  const secs = Math.round(seconds % 60)
  return `${minutes}m ${secs}s`
}

/** Content-type-aware status label (e.g., "Unplayed" for video games) */
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

/** Title-case a scorer key (e.g., "genre_match" -> "Genre Match") */
export function formatScorerName(key: string): string {
  return key.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

/** Capitalize first letter */
export function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}
