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
    raw: vi.fn(),
  }),
}))

function view(overrides: Record<string, unknown> = {}) {
  return {
    sections: [
      {
        section: 'web',
        settings: [
          {
            key: 'web.port',
            section: 'web',
            label: 'Port',
            help: '',
            type: 'int',
            widget: 'number',
            choices: null,
            validation: { min: 1, max: 65535, max_length: null, pattern: null },
            advanced: false,
            restart_required: true,
            sensitive: false,
            value: 8000,
            db_overridden: false,
          },
        ],
      },
    ],
    ...overrides,
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
    expect(store.sections[0].section).toBe('web')
    expect(store.loadError).toBe('')
  })

  it('load records loadError on failure', async () => {
    mockGet.mockRejectedValue(new Error('down'))
    const store = useSettingsStore()

    await store.load()

    expect(store.loadError).toBe('down')
    expect(store.sections).toEqual([])
  })

  it('saveSection PUTs the given updates and applies the refreshed view', async () => {
    mockPut.mockResolvedValue(
      view({
        sections: [
          {
            section: 'web',
            settings: [
              {
                key: 'web.port',
                section: 'web',
                label: 'Port',
                help: '',
                type: 'int',
                widget: 'number',
                choices: null,
                validation: null,
                advanced: false,
                restart_required: true,
                sensitive: false,
                value: 9000,
                db_overridden: true,
              },
            ],
          },
        ],
      }),
    )
    const store = useSettingsStore()

    const ok = await store.saveSection('web', { 'web.port': 9000 })

    expect(ok).toBe(true)
    expect(mockPut).toHaveBeenCalledWith('/settings', { updates: { 'web.port': 9000 } })
    expect(store.saveStatus.web).toBe('saved')
    expect(store.sections[0].settings[0]).toMatchObject({ value: 9000, db_overridden: true })
  })

  it('saveSection maps a 422 body to the offending field error', async () => {
    mockPut.mockRejectedValue(
      new MockApiError(422, 'Unprocessable Entity', {
        detail: { key: 'web.port', reason: 'must be between 1 and 65535' },
      }),
    )
    const store = useSettingsStore()

    const ok = await store.saveSection('web', { 'web.port': -1 })

    expect(ok).toBe(false)
    expect(store.saveStatus.web).toBe('error')
    expect(store.fieldErrors['web.port']).toBe('must be between 1 and 65535')
  })

  it('saveSection records the error message when the failure is a plain Error', async () => {
    mockPut.mockRejectedValue(new Error('network down'))
    const store = useSettingsStore()

    const ok = await store.saveSection('web', { 'web.port': 9000 })

    expect(ok).toBe(false)
    expect(store.saveStatus.web).toBe('error')
    expect(store.saveError.web).toBe('network down')
    expect(store.fieldErrors['web.port']).toBeUndefined()
  })

  it('saveSection falls back to the message on a non-422 ApiError', async () => {
    mockPut.mockRejectedValue(new MockApiError(500, 'Internal Server Error'))
    const store = useSettingsStore()

    const ok = await store.saveSection('web', { 'web.port': 9000 })

    expect(ok).toBe(false)
    expect(store.saveStatus.web).toBe('error')
    expect(store.saveError.web).toBe('500 Internal Server Error')
  })

  it('clearSaveStatus resets a section back to idle', () => {
    const store = useSettingsStore()
    store.saveStatus.web = 'saved'

    store.clearSaveStatus('web')

    expect(store.saveStatus.web).toBe('idle')
  })

  it('resetSetting DELETEs the key and applies the refreshed view', async () => {
    mockDelete.mockResolvedValue(view())
    const store = useSettingsStore()

    await store.resetSetting('web.port')

    expect(mockDelete).toHaveBeenCalledWith('/settings/web.port')
    expect(store.sections).toHaveLength(1)
  })

  it('setSecret PUTs the secret then refetches', async () => {
    mockPut.mockResolvedValue(undefined)
    mockGet.mockResolvedValue(view())
    const store = useSettingsStore()

    await store.setSecret('llm.api_key', 'sk-123')

    expect(mockPut).toHaveBeenCalledWith('/settings/secret', {
      key: 'llm.api_key',
      value: 'sk-123',
    })
    expect(mockGet).toHaveBeenCalledWith('/settings')
  })

  it('clearSecret DELETEs the secret then refetches', async () => {
    mockDelete.mockResolvedValue(undefined)
    mockGet.mockResolvedValue(view())
    const store = useSettingsStore()

    await store.clearSecret('llm.api_key')

    expect(mockDelete).toHaveBeenCalledWith('/settings/secret/llm.api_key')
    expect(mockGet).toHaveBeenCalledWith('/settings')
  })
})
