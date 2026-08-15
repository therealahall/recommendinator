import { describe, it, expect } from 'vitest'
import {
  formatStatusForContentType, humanizeSection, truncate, domId,
} from './format'

describe('domId', () => {
  it('keeps the two separators a source id may use apart', () => {
    // Both match SOURCE_ID_PATTERN and both run the gog plugin, so collapsing
    // `_` to `-` handed two live panels the same element id.
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

describe('truncate', () => {
  it('truncates long strings with ellipsis', () => {
    expect(truncate('a very long string here', 10)).toBe('a very ...')
  })
})
