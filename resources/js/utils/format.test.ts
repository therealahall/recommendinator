import { describe, it, expect } from 'vitest'
import {
  formatRelativeTime, formatSeries, formatStatusForContentType, humanizeSection,
  truncate, domId,
} from './format'

describe('domId', () => {
  it('keeps the two separators a source id may use apart', () => {
    expect(domId('oauth-code', 'gog_work')).toBe('oauth-code-gog_work')
    expect(domId('oauth-code', 'gog_work')).not.toBe(
      domId('oauth-code', 'gog-work'),
    )
  })

  it('replaces every character an id may not carry', () => {
    expect(domId('oauth-code', 'my gog:2 (work)')).toBe(
      'oauth-code-my-gog-2--work-',
    )
  })
})

describe('formatStatusForContentType', () => {
  it('returns Unplayed for video game unread', () => {
    expect(formatStatusForContentType('unread', 'video_game')).toBe('Unplayed')
  })

  it('passes through unknown statuses', () => {
    expect(formatStatusForContentType('on_hold', 'book')).toBe('on_hold')
  })
})

describe('humanizeSection', () => {
  it('keeps allow-listed acronyms fully uppercase', () => {
    expect(humanizeSection('cors')).toBe('CORS')
    expect(humanizeSection('api')).toBe('API')
  })

  it('splits underscores and hyphens into words', () => {
    expect(humanizeSection('api_key')).toBe('API Key')
    expect(humanizeSection('log-level')).toBe('Log Level')
  })
})

describe('formatRelativeTime', () => {
  const now = new Date('2026-08-17T12:00:00+00:00')

  it('reads a past sync in the largest unit that fits', () => {
    expect(formatRelativeTime('2026-08-17T10:00:00+00:00', now)).toBe('2 hours ago')
    expect(formatRelativeTime('2026-08-17T11:59:30+00:00', now)).toBe('30 seconds ago')
  })

  it('reads a due time as future', () => {
    expect(formatRelativeTime('2026-08-17T18:00:00+00:00', now)).toBe('in 6 hours')
  })

  it('reads the offset the API sends rather than the host zone', () => {
    expect(formatRelativeTime('2026-08-17T12:00:00+02:00', now)).toBe('2 hours ago')
  })
})

describe('formatSeries', () => {
  it('says in words what "#4" leaves to punctuation a screen reader may drop', () => {
    expect(formatSeries('The Murderbot Diaries', 4)).toEqual({
      shown: 'The Murderbot Diaries #4',
      spoken: 'Series: The Murderbot Diaries, number 4',
    })
  })

  it('states a series with no position without inventing one', () => {
    expect(formatSeries('Discworld', null)).toEqual({
      shown: 'Discworld',
      spoken: 'Series: Discworld',
    })
  })
})

describe('truncate', () => {
  it('truncates long strings with ellipsis', () => {
    expect(truncate('a very long string here', 10)).toBe('a very ...')
  })
})
