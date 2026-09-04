import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useSettingsStore } from './settings'

const mockGet = vi.fn()
const mockPut = vi.fn()
const mockDelete = vi.fn()

const { MockApiError } = vi.hoisted(() => {
  class MockApiError extends Error {
    constructor(
      public status: number,
      public statusText: string,
      public body?: unknown,
    ) {
      super(`${status} ${statusText}`)
      this.name = 'ApiError'
    }
  }
  return { MockApiError }
})

vi.mock('@/composables/useApi', () => ({
  ApiError: MockApiError,
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
    put: (...args: unknown[]) => mockPut(...args),
    patch: vi.fn(),
    delete: (...args: unknown[]) => mockDelete(...args),
  }),
}))

function view(overrides: Record<string, unknown> = {}) {
  return {
    sections: [
      {
        section: 'recommendations',
        settings: [
          {
            key: 'recommendations.default_count',
            section: 'recommendations',
            label: 'Default count',
            help: '',
            type: 'int',
            widget: 'number',
            choices: null,
            validation: { min: 1, max: null, max_length: null, pattern: null },
            advanced: false,
            restart_required: false,
            sensitive: false,
            value: 5,
            db_overridden: false,
            has_stored_value: false,
          },
        ],
      },
    ],
    ...overrides,
  }
}

function twoSettingView() {
  const base = view()
  const first = base.sections[0].settings[0]
  return {
    sections: [
      {
        section: 'recommendations',
        settings: [first, { ...first, key: 'recommendations.max_count', label: 'Max count' }],
      },
    ],
  }
}

describe('useSettingsStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockPut.mockReset()
    mockDelete.mockReset()
  })

  it('load populates sections from GET /settings', async () => {
    mockGet.mockResolvedValue(view())
    const store = useSettingsStore()

    await store.load()

    expect(mockGet).toHaveBeenCalledWith('/settings')
    expect(store.sections).toHaveLength(1)
    expect(store.sections[0].section).toBe('recommendations')
    expect(store.loadError).toBe('')
  })

  it('saveSection PUTs the given updates and applies the refreshed view', async () => {
    mockPut.mockResolvedValue(
      view({
        sections: [
          {
            section: 'recommendations',
            settings: [
              {
                key: 'recommendations.default_count',
                section: 'recommendations',
                label: 'Default count',
                help: '',
                type: 'int',
                widget: 'number',
                choices: null,
                validation: null,
                advanced: false,
                restart_required: false,
                sensitive: false,
                value: 9000,
                db_overridden: true,
                has_stored_value: true,
              },
            ],
          },
        ],
      }),
    )
    const store = useSettingsStore()

    const ok = await store.saveSection('recommendations', { 'recommendations.default_count': 9000 })

    expect(ok).toBe(true)
    expect(mockPut).toHaveBeenCalledWith('/settings', { updates: { 'recommendations.default_count': 9000 } })
    expect(store.saveStatus.recommendations).toBe('saved')
    expect(store.sections[0].settings[0]).toMatchObject({ value: 9000, db_overridden: true })
  })

  it('saveSection maps a 422 body to the offending field error', async () => {
    mockPut.mockRejectedValue(
      new MockApiError(422, 'Unprocessable Entity', {
        detail: { key: 'recommendations.default_count', reason: 'must be >= 1' },
      }),
    )
    const store = useSettingsStore()

    const ok = await store.saveSection('recommendations', { 'recommendations.default_count': -1 })

    expect(ok).toBe(false)
    expect(store.saveStatus.recommendations).toBe('error')
    expect(store.fieldErrors['recommendations.default_count']).toBe('must be >= 1')
  })

  it('saveSection clears stale field errors for keys absent from this save', async () => {
    mockGet.mockResolvedValue(twoSettingView())
    const store = useSettingsStore()
    await store.load()

    mockPut.mockRejectedValueOnce(
      new MockApiError(422, 'Unprocessable Entity', {
        detail: { key: 'recommendations.default_count', reason: 'must be >= 1' },
      }),
    )
    await store.saveSection('recommendations', { 'recommendations.default_count': -1 })
    expect(store.fieldErrors['recommendations.default_count']).toBe('must be >= 1')

    mockPut.mockResolvedValueOnce(twoSettingView())
    await store.saveSection('recommendations', { 'recommendations.max_count': 50 })

    expect(store.fieldErrors['recommendations.default_count']).toBeUndefined()
  })

  it('saveSection records the error message when the failure is a plain Error', async () => {
    mockPut.mockRejectedValue(new Error('network down'))
    const store = useSettingsStore()

    const ok = await store.saveSection('recommendations', { 'recommendations.default_count': 9000 })

    expect(ok).toBe(false)
    expect(store.saveStatus.recommendations).toBe('error')
    expect(store.saveError.recommendations).toBe('network down')
    expect(store.fieldErrors['recommendations.default_count']).toBeUndefined()
  })

  it('resetSetting DELETEs the key and applies the refreshed view', async () => {
    mockDelete.mockResolvedValue(view())
    const store = useSettingsStore()

    await store.resetSetting('recommendations.default_count')

    expect(mockDelete).toHaveBeenCalledWith('/settings/recommendations.default_count')
    expect(store.sections).toHaveLength(1)
  })

  it('setSecret PUTs the secret then refetches', async () => {
    mockPut.mockResolvedValue(undefined)
    mockGet.mockResolvedValue(view())
    const store = useSettingsStore()

    await store.setSecret('enrichment.providers.tmdb.api_key', 'sk-123')

    expect(mockPut).toHaveBeenCalledWith('/settings/secret', {
      key: 'enrichment.providers.tmdb.api_key',
      value: 'sk-123',
    })
    expect(mockGet).toHaveBeenCalledWith('/settings')
  })

  it('clearSecret DELETEs the secret then refetches', async () => {
    mockDelete.mockResolvedValue(undefined)
    mockGet.mockResolvedValue(view())
    const store = useSettingsStore()

    await store.clearSecret('enrichment.providers.tmdb.api_key')

    expect(mockDelete).toHaveBeenCalledWith('/settings/secret/enrichment.providers.tmdb.api_key')
    expect(mockGet).toHaveBeenCalledWith('/settings')
  })
})
