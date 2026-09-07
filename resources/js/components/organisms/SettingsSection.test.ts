import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises, enableAutoUnmount, type VueWrapper } from '@vue/test-utils'
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

enableAutoUnmount(afterEach)

/** Every panel is shut on arrival, so a test that drives a control opens its
 *  way in the way the operator does. Collapsed panels are hidden, not
 *  unmounted, so one pass reaches the nested triggers too. */
async function openEverything(wrapper: VueWrapper): Promise<void> {
  for (const trigger of wrapper.findAll('button[aria-expanded="false"]')) {
    await trigger.trigger('click')
  }
}

function renderSection(section: SettingsSectionType, initiallyExpanded = false): VueWrapper {
  return mount(SettingsSection, {
    props: { section, initiallyExpanded },
    attachTo: document.body,
  })
}

async function mountSection(section: SettingsSectionType): Promise<VueWrapper> {
  const wrapper = renderSection(section)
  await openEverything(wrapper)
  return wrapper
}

/** What the operator can actually reach: a collapsed panel carries `hidden`. */
function reachable(wrapper: VueWrapper, testid: string): boolean {
  const found = wrapper.find(`[data-testid="${testid}"]`)
  return found.exists() && found.element.closest('[hidden]') === null
}

function accordionTrigger(wrapper: VueWrapper, label: string) {
  const found = wrapper.findAll('button').find((button) => button.text().includes(label))
  if (!found) throw new Error(`no accordion trigger labelled ${label}`)
  return found
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
    const wrapper = await mountSection(section)

    expect(wrapper.find('[data-testid="save-enrichment"]').text()).toBe('Save Enrichment')
    await wrapper.find('[data-testid="setting-enrichment.providers.tmdb.language"]').setValue('de-DE')
    await wrapper.find('[data-testid="save-enrichment"]').trigger('click')
    await flushPromises()

    expect(mockPut).toHaveBeenCalledWith('/settings', { updates: { 'enrichment.providers.tmdb.language': 'de-DE' } })
  })

  it('announces each landed save through the region already mounted for it', async () => {
    mockPut.mockResolvedValue({ sections: [] })
    const wrapper = await mountSection({
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
    const wrapper = await mountSection(section)

    await wrapper.find('[data-testid="setting-enrichment.providers.tmdb.language"]').setValue('!!')
    await wrapper.find('[data-testid="save-enrichment"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="setting-error-enrichment.providers.tmdb.language"]').text()).toBe('invalid language tag')
    expect(document.activeElement).toBe(
      wrapper.find('[data-testid="setting-enrichment.providers.tmdb.language"]').element,
    )
  })

  it('does not PUT or claim a save when nothing was edited', async () => {
    const wrapper = await mountSection({
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
    const wrapper = await mountSection(section)

    await wrapper.find('[data-testid="reset-enrichment.providers.tmdb.language"]').trigger('click')
    await flushPromises()

    expect(mockDelete).toHaveBeenCalledWith('/settings/enrichment.providers.tmdb.language')
  })

  it('catches the focus the Reset button takes with it when the override is gone', async () => {
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
    const wrapper = await mountSection({ section: 'enrichment', settings: [overridden] })
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
    const wrapper = await mountSection(section)

    expect(wrapper.find('.source-form-secrets legend').text()).toBe('Secrets')
    await wrapper.find('[data-testid="secret-replace-enrichment.providers.tmdb.api_key"]').trigger('click')
    await wrapper.find('#secret-input-enrichment\\.providers\\.tmdb\\.api_key').setValue('sk-999')
    await wrapper.find('[data-testid="secret-save-enrichment.providers.tmdb.api_key"]').trigger('click')
    await flushPromises()

    expect(mockPut).toHaveBeenCalledWith('/settings/secret', { key: 'enrichment.providers.tmdb.api_key', value: 'sk-999' })
  })

  describe('grouping', () => {
    const ZZZTEST: SettingsSectionType = {
      section: 'enrichment',
      settings: [
        textSetting('enrichment.enabled', 'on'),
        textSetting('enrichment.providers.zzztest.language', 'en-US'),
        textSetting('enrichment.providers.zzztest.region', 'us'),
      ],
    }

    it('groups a provider no frontend code names, leaving section keys above it', async () => {
      const wrapper = renderSection(ZZZTEST, true)

      expect(reachable(wrapper, 'setting-enrichment.enabled')).toBe(true)
      expect(reachable(wrapper, 'setting-enrichment.providers.zzztest.language')).toBe(false)

      await accordionTrigger(wrapper, 'Zzztest').trigger('click')

      expect(reachable(wrapper, 'setting-enrichment.providers.zzztest.language')).toBe(true)
      expect(reachable(wrapper, 'setting-enrichment.providers.zzztest.region')).toBe(true)
    })

    it('renders a provider holding one setting inline, not behind a disclosure of one', () => {
      const wrapper = renderSection(
        {
          section: 'enrichment',
          settings: [
            textSetting('enrichment.enabled', 'on'),
            textSetting('enrichment.providers.openlibrary.enabled', 'on'),
          ],
        },
        true,
      )

      expect(reachable(wrapper, 'setting-enrichment.providers.openlibrary.enabled')).toBe(true)
      expect(wrapper.findAll('button.accordion-trigger')).toHaveLength(1)
    })

    it('renders an all-advanced section in its panel, with no empty list above it', () => {
      const wrapper = renderSection(
        {
          section: 'logging',
          settings: [
            textSetting('logging.level', 'INFO', { advanced: true }),
            textSetting('logging.file', 'app.log', { advanced: true }),
          ],
        },
        true,
      )

      expect(reachable(wrapper, 'setting-logging.level')).toBe(true)
      expect(reachable(wrapper, 'setting-logging.file')).toBe(true)
      expect(wrapper.findAll('.settings-field-list')).toHaveLength(1)
    })

    it('opens the shut section holding an advanced value the server refused', async () => {
      const key = 'logging.level'
      mockPut.mockRejectedValue(
        new MockApiError(422, 'Unprocessable Entity', {
          detail: { key, reason: 'unknown level' },
        }),
      )
      const wrapper = renderSection(
        { section: 'logging', settings: [textSetting(key, 'INFO', { advanced: true })] },
        true,
      )
      await wrapper.find(`[data-testid="setting-${key}"]`).setValue('LOUD')
      await accordionTrigger(wrapper, 'Logging').trigger('click')
      expect(reachable(wrapper, `setting-${key}`)).toBe(false)

      await wrapper.find('[data-testid="save-logging"]').trigger('click')
      await flushPromises()

      expect(reachable(wrapper, `setting-${key}`)).toBe(true)
      expect(document.activeElement).toBe(wrapper.find(`[data-testid="setting-${key}"]`).element)
    })

    it('nests every subgroup heading one level under the section heading', () => {
      const wrapper = renderSection(ZZZTEST, true)

      const levels = wrapper
        .findAll('button.accordion-trigger')
        .map((trigger) => Number(trigger.element.closest('h1,h2,h3,h4,h5,h6')?.tagName.slice(1)))
      const [section, ...subgroups] = levels

      expect(subgroups.length).toBeGreaterThan(0)
      expect(subgroups.every((level) => level === section + 1)).toBe(true)
    })

    it('keeps an opened subgroup open when the saved section comes back as a new object', async () => {
      const key = 'enrichment.providers.zzztest.language'
      mockPut.mockResolvedValue({ sections: [] })
      const wrapper = renderSection(ZZZTEST, true)
      await accordionTrigger(wrapper, 'Zzztest').trigger('click')
      await wrapper.find(`[data-testid="setting-${key}"]`).setValue('de-DE')

      await wrapper.find('[data-testid="save-enrichment"]').trigger('click')
      await flushPromises()
      // The store replaces `sections` with the PUT response, so the section
      // arrives as a fresh object every time a write lands.
      await wrapper.setProps({ section: structuredClone(ZZZTEST) })

      expect(reachable(wrapper, `setting-${key}`)).toBe(true)
    })

    it('opens the shut group holding a value the server refused', async () => {
      const key = 'enrichment.providers.zzztest.language'
      mockPut.mockRejectedValue(
        new MockApiError(422, 'Unprocessable Entity', {
          detail: { key, reason: 'invalid language tag' },
        }),
      )
      const wrapper = renderSection(ZZZTEST, true)
      const group = accordionTrigger(wrapper, 'Zzztest')
      await group.trigger('click')
      await wrapper.find(`[data-testid="setting-${key}"]`).setValue('!!')
      await group.trigger('click')
      expect(reachable(wrapper, `setting-${key}`)).toBe(false)

      await wrapper.find('[data-testid="save-enrichment"]').trigger('click')
      await flushPromises()

      expect(reachable(wrapper, `setting-${key}`)).toBe(true)
      expect(reachable(wrapper, `setting-error-${key}`)).toBe(true)
      expect(document.activeElement).toBe(
        wrapper.find(`[data-testid="setting-${key}"]`).element,
      )
    })
  })

  describe('advanced caution copy', () => {
    async function noteFor(section: string, key: string) {
      const wrapper = await mountSection({
        section,
        settings: [textSetting(key, 'x', { advanced: true })],
      })
      return wrapper.find('[role="note"]')
    }

    it('warns about CORS in the web panel', async () => {
      expect((await noteFor('web', 'web.allowed_origins')).text()).toContain('CORS')
    })

    it('does not mention CORS in the logging panel', async () => {
      const note = await noteFor('logging', 'logging.level')
      expect(note.text()).not.toContain('CORS')
      expect(note.text()).toContain('records')
    })
  })

  describe('when a reset or secret action fails', () => {
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
      const wrapper = await mountSection(OVERRIDDEN)

      const reset = wrapper.get('[data-testid="reset-enrichment.providers.tmdb.language"]')
      const pressed = reset.element as HTMLButtonElement
      pressed.focus()

      await reset.trigger('click')
      await flushPromises()

      expect(wrapper.find('p.sr-only').text()).toContain('Reset failed.')
      expect(document.activeElement).toBe(pressed)
      expect(reset.attributes('disabled')).toBeUndefined()
    })

    it('announces a failed secret save instead of doing nothing', async () => {
      mockPut.mockRejectedValue(new MockApiError(503, 'Service Unavailable'))
      const wrapper = await mountSection(SECRET)

      await wrapper.find('[data-testid="secret-replace-enrichment.providers.tmdb.api_key"]').trigger('click')
      await wrapper.find('#secret-input-enrichment\\.providers\\.tmdb\\.api_key').setValue('sk-999')
      await wrapper.find('[data-testid="secret-save-enrichment.providers.tmdb.api_key"]').trigger('click')
      await flushPromises()

      expect(wrapper.find('p.sr-only').text()).toContain('Saving the secret failed.')
    })

    it('announces a failed secret clear instead of doing nothing', async () => {
      mockDelete.mockRejectedValue(new MockApiError(503, 'Service Unavailable'))
      const wrapper = await mountSection(SECRET)

      await wrapper.find('[data-testid="secret-clear-enrichment.providers.tmdb.api_key"]').trigger('click')
      await flushPromises()

      expect(wrapper.find('p.sr-only').text()).toContain('Clearing the secret failed.')
    })
  })
})
