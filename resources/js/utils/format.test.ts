import { describe, it, expect } from 'vitest'
import {
  formatContentType, formatStatus, formatScore, formatElapsed,
  formatStatusForContentType, formatScorerName, formatDate, capitalize, truncate,
} from './format'

describe('formatContentType', () => {
  it('formats known content types', () => {
    expect(formatContentType('book')).toBe('Book')
    expect(formatContentType('movie')).toBe('Movie')
    expect(formatContentType('tv_show')).toBe('TV Show')
    expect(formatContentType('video_game')).toBe('Video Game')
  })

  it('returns raw value for unknown types', () => {
    expect(formatContentType('podcast')).toBe('podcast')
  })
})

describe('formatStatus', () => {
  it('formats known statuses', () => {
    expect(formatStatus('completed')).toBe('Completed')
    expect(formatStatus('in_progress')).toBe('In Progress')
    expect(formatStatus('plan_to_consume')).toBe('Plan to Consume')
  })

  it('returns raw value for unknown statuses', () => {
    expect(formatStatus('archived')).toBe('archived')
  })
})

describe('formatScore', () => {
  it('formats scores as percentages', () => {
    expect(formatScore(0.856)).toBe('86')
    expect(formatScore(1.0)).toBe('100')
    expect(formatScore(0)).toBe('0')
  })
})

describe('formatElapsed', () => {
  it('formats seconds under a minute', () => {
    expect(formatElapsed(45)).toBe('45s')
  })

  it('formats minutes and seconds', () => {
    expect(formatElapsed(125)).toBe('2m 5s')
  })
})

describe('formatStatusForContentType', () => {
  it('returns In Progress for currently_consuming', () => {
    expect(formatStatusForContentType('currently_consuming', 'book')).toBe('In Progress')
  })

  it('returns Completed for completed', () => {
    expect(formatStatusForContentType('completed', 'movie')).toBe('Completed')
  })

  it('returns Unplayed for video game unread', () => {
    expect(formatStatusForContentType('unread', 'video_game')).toBe('Unplayed')
  })

  it('returns Unwatched for movie unread', () => {
    expect(formatStatusForContentType('unread', 'movie')).toBe('Unwatched')
  })

  it('returns Unwatched for tv_show unread', () => {
    expect(formatStatusForContentType('unread', 'tv_show')).toBe('Unwatched')
  })

  it('returns Unread for book unread', () => {
    expect(formatStatusForContentType('unread', 'book')).toBe('Unread')
  })

  it('passes through unknown statuses', () => {
    expect(formatStatusForContentType('on_hold', 'book')).toBe('on_hold')
  })
})

describe('formatScorerName', () => {
  it('converts snake_case to Title Case', () => {
    expect(formatScorerName('genre_match')).toBe('Genre Match')
  })

  it('handles single word', () => {
    expect(formatScorerName('popularity')).toBe('Popularity')
  })
})

describe('formatDate', () => {
  it('formats ISO date string to a non-empty locale string', () => {
    const result = formatDate('2024-01-15')
    expect(result).toContain('2024')
    expect(result.length).toBeGreaterThan(0)
  })
})

describe('capitalize', () => {
  it('capitalizes first letter', () => {
    expect(capitalize('hello')).toBe('Hello')
  })

  it('handles already capitalized strings', () => {
    expect(capitalize('World')).toBe('World')
  })
})

describe('truncate', () => {
  it('returns short strings unchanged', () => {
    expect(truncate('hello', 10)).toBe('hello')
  })

  it('truncates long strings with ellipsis', () => {
    expect(truncate('a very long string here', 10)).toBe('a very ...')
  })

  it('returns exact-length strings unchanged', () => {
    expect(truncate('12345', 5)).toBe('12345')
  })
})

