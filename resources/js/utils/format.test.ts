import { describe, it, expect } from 'vitest'
import { formatContentType, formatStatus, formatScore, formatElapsed } from './format'

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
