import { describe, it, expect } from 'vitest'
import { groupSettings } from './settingsGroups'
import type { SettingView } from '@/types/api'

function setting(key: string, label: string): SettingView {
  return {
    key,
    section: key.split('.')[0],
    label,
    help: '',
    type: 'string',
    widget: 'text',
    choices: null,
    validation: null,
    advanced: false,
    restart_required: false,
    sensitive: false,
    value: '',
    db_overridden: false,
    has_stored_value: false,
  }
}

describe('groupSettings', () => {
  it('leaves a section-level key ungrouped and groups everything deeper', () => {
    const { ungrouped, groups } = groupSettings([
      setting('enrichment.enabled', 'Enrichment enabled'),
      setting('enrichment.batch_size', 'Enrichment batch size'),
      setting('enrichment.providers.tmdb.api_key', 'TMDB API key'),
      setting('enrichment.providers.tmdb.language', 'TMDB language'),
    ])

    expect(ungrouped.map((entry) => entry.key)).toEqual([
      'enrichment.enabled',
      'enrichment.batch_size',
    ])
    expect(groups).toHaveLength(1)
    expect(groups[0].settings.map((entry) => entry.key)).toEqual([
      'enrichment.providers.tmdb.api_key',
      'enrichment.providers.tmdb.language',
    ])
  })

  it('keeps every key sharing a provider path in one group', () => {
    const { groups } = groupSettings([
      setting('enrichment.providers.tmdb.api_key', 'TMDB API key'),
      setting('enrichment.providers.rawg.enabled', 'RAWG enabled'),
      setting('enrichment.providers.tmdb.language', 'TMDB language'),
      setting('enrichment.providers.rawg.api_key', 'RAWG API key'),
      setting('enrichment.providers.tmdb.include_keywords', 'TMDB keywords as tags'),
    ])

    expect(groups).toHaveLength(2)
    expect(groups[0].settings.map((entry) => entry.key)).toEqual([
      'enrichment.providers.tmdb.api_key',
      'enrichment.providers.tmdb.language',
      'enrichment.providers.tmdb.include_keywords',
    ])
    expect(groups[1].settings).toHaveLength(2)
  })

  it('folds a provider holding one setting back in among the ungrouped ones', () => {
    const { ungrouped, groups } = groupSettings([
      setting('enrichment.enabled', 'Enrichment enabled'),
      setting('enrichment.providers.openlibrary.enabled', 'Open Library enabled'),
      setting('enrichment.batch_size', 'Enrichment batch size'),
    ])

    expect(groups).toHaveLength(0)
    expect(ungrouped.map((entry) => entry.key)).toEqual([
      'enrichment.enabled',
      'enrichment.providers.openlibrary.enabled',
      'enrichment.batch_size',
    ])
  })

  it('groups a provider this file has never been told about', () => {
    const { groups } = groupSettings([
      setting('enrichment.providers.zzztest.enabled', 'Zzztest enabled'),
      setting('enrichment.providers.zzztest.api_key', 'Zzztest API key'),
    ])

    expect(groups).toHaveLength(1)
    expect(groups[0].label).toBe('Zzztest')
    expect(groups[0].settings).toHaveLength(2)
  })

  it('takes the heading spelling from the labels rather than the key segment', () => {
    const { groups } = groupSettings([
      setting('enrichment.providers.tmdb.api_key', 'TMDB API key'),
      setting('enrichment.providers.tmdb.language', 'TMDB language'),
      setting('enrichment.providers.openlibrary.enabled', 'Open Library enabled'),
      setting('enrichment.providers.openlibrary.timeout', 'Open Library timeout'),
    ])

    expect(groups.map((group) => group.label)).toEqual(['TMDB', 'Open Library'])
  })

  it('falls back to the humanized segment when no label spells it out', () => {
    const { groups } = groupSettings([
      setting('recommendations.scorer_weights.genre_match', 'Genre match weight'),
      setting('recommendations.scorer_weights.adaptation', 'Adaptation weight'),
    ])

    expect(groups).toHaveLength(1)
    expect(groups[0].label).toBe('Scorer Weights')
  })
})
