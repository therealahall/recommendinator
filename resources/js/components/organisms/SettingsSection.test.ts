import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import { reactive } from 'vue'
import SettingsSection from './SettingsSection.vue'
import type {
  SettingsSection as SettingsSectionType,
  SettingView,
  SettingViewValue,
} from '@/types/api'

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

function textSetting(key: string, value: string, extra: Partial<SettingView> = {}): SettingView {
  return {
    key,
    // A real registry section, so the heading these fixtures produce is one the
    // settings page actually renders.
    section: 'enrichment',
    label: key,
    help: '',
    type: 'string',
    widget: 'text',
    choices: null,
    validation: null,
    advanced: false,
    restart_required: false,
    sensitive: false,
    value,
    db_overridden: false,
    has_stored_value: false,
    ...extra,
  } as SettingView
}

function numberSetting(key: string, value: number): SettingView {
  return { ...textSetting(key, ''), type: 'int', widget: 'number', value } as SettingView
}

// SettingsSection resolves focus targets with document.getElementById, which returns
// the FIRST match in tree order — so a leaked node from an earlier test would satisfy
// a focus assertion in a later one, and the assertion could not tell the difference.
enableAutoUnmount(afterEach)

function mountSection(section: SettingsSectionType) {
  return mount(SettingsSection, { props: { section }, attachTo: document.body })
}

describe('SettingsSection', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockPut.mockReset()
    mockDelete.mockReset()
  })

  it('saves only the changed keys and labels the Save button by section', async () => {
    mockPut.mockResolvedValue({ sections: [] })
    const section: SettingsSectionType = {
      section: 'enrichment',
      settings: [
        textSetting('enrichment.providers.tmdb.language', 'en-US'),
        numberSetting('enrichment.batch_size', 50),
      ],
    }
    const wrapper = mountSection(section)

    expect(wrapper.find('[data-testid="save-enrichment"]').text()).toBe('Save Enrichment')
    await wrapper.find('[data-testid="setting-enrichment.providers.tmdb.language"]').setValue('de-DE')
    await wrapper.find('[data-testid="save-enrichment"]').trigger('click')
    await flushPromises()

    expect(mockPut).toHaveBeenCalledWith('/settings', { updates: { 'enrichment.providers.tmdb.language': 'de-DE' } })
  })

  it('announces each landed save through the region already mounted for it', async () => {
    // The "Saved ✓" pill enters the DOM already populated, which reads as page
    // content, and a second save writes the sentence the first left in place:
    // silence either way unless the region mounts empty and is blanked between.
    mockPut.mockResolvedValue({ sections: [] })
    const wrapper = mountSection({
      section: 'enrichment',
      settings: [textSetting('enrichment.providers.tmdb.language', 'en-US')],
    })
    const region = wrapper.get('p.sr-only')
    const changes: string[] = []
    new MutationObserver(() => changes.push(region.text())).observe(region.element, {
      characterData: true,
      childList: true,
      subtree: true,
    })
    async function save(language: string): Promise<void> {
      await wrapper
        .find('[data-testid="setting-enrichment.providers.tmdb.language"]')
        .setValue(language)
      await wrapper.find('[data-testid="save-enrichment"]').trigger('click')
      await flushPromises()
    }
    expect(region.text()).toBe('')

    await save('de-DE')

    expect(region.text()).toContain('saved')
    expect(
      wrapper.get('[data-testid="save-status-enrichment"]').attributes('role'),
    ).toBeUndefined()
    expect(changes).not.toContain('')
    changes.length = 0

    await save('fr-FR')

    expect(changes.length).toBeGreaterThan(0)
    expect(region.text()).toContain('saved')
  })

  it('maps a 422 to the offending field and moves focus to it', async () => {
    mockPut.mockRejectedValue(
      new MockApiError(422, 'Unprocessable Entity', {
        detail: { key: 'enrichment.providers.tmdb.language', reason: 'invalid language tag' },
      }),
    )
    const section: SettingsSectionType = {
      section: 'enrichment',
      settings: [textSetting('enrichment.providers.tmdb.language', 'en-US')],
    }
    const wrapper = mountSection(section)

    await wrapper.find('[data-testid="setting-enrichment.providers.tmdb.language"]').setValue('!!')
    await wrapper.find('[data-testid="save-enrichment"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="setting-error-enrichment.providers.tmdb.language"]').text()).toBe('invalid language tag')
    // Identity, not id: an id comparison is satisfied by any element carrying
    // that id, including one leaked into document.body by an earlier test.
    expect(document.activeElement).toBe(
      wrapper.find('[data-testid="setting-enrichment.providers.tmdb.language"]').element,
    )
  })

  it('does not PUT or claim a save when nothing was edited', async () => {
    // Regression: Save always issued PUT /settings with {updates: {}} and then
    // showed "Saved ✓", telling the user a write happened that did not.
    const wrapper = mountSection({
      section: 'enrichment',
      settings: [textSetting('enrichment.providers.tmdb.language', 'en-US')],
    })

    await wrapper.find('[data-testid="save-enrichment"]').trigger('click')
    await flushPromises()

    expect(mockPut).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="save-status-enrichment"]').exists()).toBe(false)
    expect(wrapper.find('p.sr-only').text()).toBe('No changes to save.')
  })

  it('resets an overridden setting via DELETE', async () => {
    mockDelete.mockResolvedValue({ sections: [] })
    const section: SettingsSectionType = {
      section: 'enrichment',
      settings: [
        textSetting('enrichment.providers.tmdb.language', 'en-US', {
          db_overridden: true,
          has_stored_value: true,
        }),
      ],
    }
    const wrapper = mountSection(section)

    await wrapper.find('[data-testid="reset-enrichment.providers.tmdb.language"]').trigger('click')
    await flushPromises()

    expect(mockDelete).toHaveBeenCalledWith('/settings/enrichment.providers.tmdb.language')
  })

  it('catches the focus the Reset button takes with it when the override is gone', async () => {
    // The refusal path keeps focus by leaving the button standing, so nothing
    // else covers the path where the button unmounts (WCAG 2.4.3).
    const overridden = reactive(
      textSetting('enrichment.providers.tmdb.language', 'en-US', {
        db_overridden: true,
        has_stored_value: true,
      }) as SettingViewValue,
    )
    mockDelete.mockImplementation(async () => {
      overridden.db_overridden = false
      overridden.has_stored_value = false
      return { sections: [] }
    })
    const wrapper = mountSection({ section: 'enrichment', settings: [overridden] })
    const reset = wrapper.get('[data-testid="reset-enrichment.providers.tmdb.language"]')
    ;(reset.element as HTMLButtonElement).focus()

    await reset.trigger('click')
    await flushPromises()

    expect(
      wrapper.find('[data-testid="reset-enrichment.providers.tmdb.language"]').exists(),
    ).toBe(false)
    expect(document.activeElement).toBe(
      wrapper.get('[data-testid="setting-enrichment.providers.tmdb.language"]').element,
    )
  })

  it('renders secrets in a Secrets fieldset and saves them out of band', async () => {
    mockPut.mockResolvedValue(undefined)
    mockGet.mockResolvedValue({ sections: [] })
    const section: SettingsSectionType = {
      section: 'enrichment',
      settings: [
        {
          key: 'enrichment.providers.tmdb.api_key',
          section: 'enrichment',
          label: 'API Key',
          help: '',
          type: 'string',
          widget: 'text',
          choices: null,
          validation: null,
          advanced: false,
          restart_required: false,
          sensitive: true,
          has_secret: false,
        } as SettingView,
      ],
    }
    const wrapper = mountSection(section)

    expect(wrapper.find('.source-form-secrets legend').text()).toBe('Secrets')
    await wrapper.find('[data-testid="secret-replace-enrichment.providers.tmdb.api_key"]').trigger('click')
    await wrapper.find('#secret-input-enrichment\\.providers\\.tmdb\\.api_key').setValue('sk-999')
    await wrapper.find('[data-testid="secret-save-enrichment.providers.tmdb.api_key"]').trigger('click')
    await flushPromises()

    expect(mockPut).toHaveBeenCalledWith('/settings/secret', { key: 'enrichment.providers.tmdb.api_key', value: 'sk-999' })
  })

  describe('advanced caution copy', () => {
    // Regression: a single shared caution string was rendered in every section's
    // Advanced panel, so the Logging panel warned about CORS allowed origins and
    // bind hosts — neither of which it contains. The copy is now per section.
    function mountAdvanced(section: string, key: string) {
      return mountSection({
        section,
        settings: [textSetting(key, 'x', { advanced: true })],
      })
    }

    it('warns about CORS in the web panel', () => {
      const note = mountAdvanced('web', 'web.allowed_origins').find('[role="note"]')
      expect(note.text()).toContain('CORS')
    })

    it('does not mention CORS in the logging panel', () => {
      const note = mountAdvanced('logging', 'logging.level').find('[role="note"]')
      expect(note.text()).not.toContain('CORS')
      expect(note.text()).toContain('records')
    })
  })

  describe('when a reset or secret action fails', () => {
    // Regression: onReset/onSetSecret/onClearSecret used try/finally with no
    // catch, and neither the store nor useApi swallows a non-2xx.
    const OVERRIDDEN: SettingsSectionType = {
      section: 'enrichment',
      settings: [
        textSetting('enrichment.providers.tmdb.language', 'en-US', {
          db_overridden: true,
          has_stored_value: true,
        }),
      ],
    }

    const SECRET: SettingsSectionType = {
      section: 'enrichment',
      settings: [
        {
          key: 'enrichment.providers.tmdb.api_key',
          section: 'enrichment',
          label: 'API Key',
          help: '',
          type: 'string',
          widget: 'text',
          choices: null,
          validation: null,
          advanced: false,
          restart_required: false,
          sensitive: true,
          has_secret: true,
        } as SettingView,
      ],
    }

    it('announces a failed reset instead of doing nothing', async () => {
      mockDelete.mockRejectedValue(new MockApiError(503, 'Service Unavailable'))
      const wrapper = mountSection(OVERRIDDEN)

      const reset = wrapper.get('[data-testid="reset-enrichment.providers.tmdb.language"]')
      const pressed = reset.element as HTMLButtonElement
      pressed.focus()

      await reset.trigger('click')
      await flushPromises()

      expect(wrapper.find('p.sr-only').text()).toContain('Reset failed.')
      // A refusal leaves the button standing, so the user is neither dumped at
      // <body> nor moved off the control they pressed — and can press it again.
      expect(document.activeElement).toBe(pressed)
      expect(reset.attributes('disabled')).toBeUndefined()
    })

    it('announces a failed secret save instead of doing nothing', async () => {
      mockPut.mockRejectedValue(new MockApiError(503, 'Service Unavailable'))
      const wrapper = mountSection(SECRET)

      await wrapper.find('[data-testid="secret-replace-enrichment.providers.tmdb.api_key"]').trigger('click')
      await wrapper.find('#secret-input-enrichment\\.providers\\.tmdb\\.api_key').setValue('sk-999')
      await wrapper.find('[data-testid="secret-save-enrichment.providers.tmdb.api_key"]').trigger('click')
      await flushPromises()

      expect(wrapper.find('p.sr-only').text()).toContain('Saving the secret failed.')
    })

    it('announces a failed secret clear instead of doing nothing', async () => {
      mockDelete.mockRejectedValue(new MockApiError(503, 'Service Unavailable'))
      const wrapper = mountSection(SECRET)

      await wrapper.find('[data-testid="secret-clear-enrichment.providers.tmdb.api_key"]').trigger('click')
      await flushPromises()

      expect(wrapper.find('p.sr-only').text()).toContain('Clearing the secret failed.')
    })
  })
})
