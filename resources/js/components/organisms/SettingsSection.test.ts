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

function boolSetting(key: string, value: boolean): SettingView {
  return { ...textSetting(key, ''), type: 'bool', widget: 'toggle', value } as SettingView
}

function orderSetting(value: string[], extra: Partial<SettingView> = {}): SettingView {
  return {
    ...textSetting('enrichment.provider_order', ''),
    label: 'Provider precedence',
    type: 'list',
    widget: 'provider-order',
    value,
    ...extra,
  } as SettingView
}

function secretSetting(key: string, hasSecret: boolean): SettingView {
  return { ...textSetting(key, ''), sensitive: true, has_secret: hasSecret } as SettingView
}

function enrichment(...settings: SettingView[]): SettingsSectionType {
  return { section: 'enrichment', settings }
}

function deferRefresh(mock: ReturnType<typeof vi.fn>): () => void {
  let land = (): void => {}
  mock.mockReturnValue(
    new Promise((resolve) => {
      land = () => resolve({ sections: [] })
    }),
  )
  return () => land()
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

/** What is on the button rather than what is read off it. */
function visibleText(element: Element): string {
  return Array.from(element.childNodes)
    .filter((node) => !(node instanceof HTMLElement && node.classList.contains('sr-only')))
    .map((node) => node.textContent ?? '')
    .join('')
    .trim()
}

/** What the operator can actually reach: a collapsed panel carries `hidden`. */
function reachable(wrapper: VueWrapper, testid: string): boolean {
  return wrapper.get(`[data-testid="${testid}"]`).element.closest('[hidden]') === null
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

  it('keeps every unsaved edit in a section a secret save refreshed', async () => {
    mockPut.mockResolvedValue({ sections: [] })
    mockGet.mockResolvedValue({ sections: [] })
    const secret = 'enrichment.providers.igdb.client_secret'
    const served = () =>
      enrichment(
        numberSetting('enrichment.batch_size', 20),
        boolSetting('enrichment.providers.igdb.enabled', false),
        secretSetting(secret, false),
      )
    const wrapper = await mountSection(served())
    await wrapper.find('[data-testid="setting-enrichment.batch_size"] input').setValue('40')
    await wrapper.find('[data-testid="setting-enrichment.providers.igdb.enabled"]').trigger('click')

    await wrapper.find(`[data-testid="secret-replace-${secret}"]`).trigger('click')
    await wrapper.find(`#secret-input-${secret.replace(/\./g, '\\.')}`).setValue('sk-999')
    await wrapper.find(`[data-testid="secret-save-${secret}"]`).trigger('click')
    await flushPromises()
    // setSecret refreshes, and the store swaps `sections` for the response, so
    // the section arrives back as a fresh object of server truth.
    await wrapper.setProps({ section: served() })

    const save = wrapper.get('[data-testid="save-enrichment"]')
    expect(visibleText(save.element)).toBe('Save')
    // Six sections each carry one, so the section it saves stays in its
    // accessible name rather than leaving six identical buttons (WCAG 2.4.6).
    expect(save.get('span.sr-only').text()).toBe('Enrichment')
    await wrapper.find('[data-testid="save-enrichment"]').trigger('click')
    await flushPromises()

    expect(mockPut).toHaveBeenCalledWith('/settings', {
      updates: { 'enrichment.batch_size': 40, 'enrichment.providers.igdb.enabled': true },
    })
    const toggle = wrapper.get(
      '[data-testid="setting-enrichment.providers.igdb.enabled"] [role="switch"]',
    )
    expect(toggle.attributes('aria-checked')).toBe('true')
  })

  it('refuses a section save while a secret save in the same section is still refreshing it, and says why it cannot be used', async () => {
    mockPut.mockResolvedValue({ sections: [] })
    const landRefresh = deferRefresh(mockGet)
    const secret = 'enrichment.providers.igdb.client_secret'
    const served = () =>
      enrichment(
        { ...numberSetting('enrichment.batch_size', 20), has_stored_value: true } as SettingView,
        secretSetting(secret, false),
      )
    const wrapper = await mountSection(served())
    await wrapper.find('[data-testid="setting-enrichment.batch_size"] input').setValue('40')
    await wrapper.find(`[data-testid="secret-replace-${secret}"]`).trigger('click')
    await wrapper.find(`#secret-input-${secret.replace(/\./g, '\\.')}`).setValue('sk-999')
    await wrapper.find(`[data-testid="secret-save-${secret}"]`).trigger('click')
    await flushPromises()

    const save = wrapper.get('[data-testid="save-enrichment"]')
    await save.trigger('click')
    // Reset claims a key the same way a section save does, so it is shut in the
    // same window.
    await wrapper.get('[data-testid="reset-enrichment.batch_size"]').trigger('click')
    await flushPromises()

    expect(mockPut).toHaveBeenCalledTimes(1)
    expect(mockDelete).not.toHaveBeenCalled()
    expect(save.attributes('aria-disabled')).toBe('true')
    // Dimmed with its label unchanged reads as "Save, dimmed", so the
    // description is where the reason lives.
    expect(wrapper.get(`[id="${save.attributes('aria-describedby')}"]`).text()).toContain(
      'in flight',
    )
    // The refresh carries pre-save truth for every value key, and the edit above
    // has to outlive it.
    landRefresh()
    await flushPromises()
    await wrapper.setProps({ section: served() })

    await save.trigger('click')
    await flushPromises()

    expect(mockPut).toHaveBeenCalledWith('/settings', {
      updates: { 'enrichment.batch_size': 40 },
    })
  })

  it('refuses a save and a second reset while a reset in the same section is in flight', async () => {
    mockPut.mockResolvedValue({ sections: [] })
    const landReset = deferRefresh(mockDelete)
    const language = 'enrichment.providers.tmdb.language'
    const served = () =>
      enrichment(
        { ...numberSetting('enrichment.batch_size', 20), has_stored_value: true } as SettingView,
        textSetting(language, 'en-US', { has_stored_value: true }),
      )
    const wrapper = await mountSection(served())
    await wrapper.find('[data-testid="setting-enrichment.batch_size"] input').setValue('40')

    await wrapper.get(`[data-testid="reset-${language}"]`).trigger('click')
    const save = wrapper.get('[data-testid="save-enrichment"]')
    await save.trigger('click')
    await wrapper.get('[data-testid="reset-enrichment.batch_size"]').trigger('click')
    await flushPromises()

    expect(mockDelete).toHaveBeenCalledTimes(1)
    expect(mockPut).not.toHaveBeenCalled()
    expect(save.attributes('aria-disabled')).toBe('true')
    // The DELETE answers with a refresh carrying pre-save truth for every key.
    landReset()
    await flushPromises()
    await wrapper.setProps({ section: served() })

    await save.trigger('click')
    await flushPromises()

    expect(mockPut).toHaveBeenCalledWith('/settings', {
      updates: { 'enrichment.batch_size': 40 },
    })
  })

  it('stops reporting a key as changed once the section save that wrote it lands', async () => {
    mockPut.mockResolvedValue({ sections: [] })
    const served = (batchSize: number) => enrichment(numberSetting('enrichment.batch_size', batchSize))
    const wrapper = await mountSection(served(20))
    await wrapper.find('[data-testid="setting-enrichment.batch_size"] input').setValue('40')

    await wrapper.find('[data-testid="save-enrichment"]').trigger('click')
    await flushPromises()
    // The PUT answers with the refreshed view the store swaps `sections` for.
    await wrapper.setProps({ section: served(40) })

    await wrapper.find('[data-testid="save-enrichment"]').trigger('click')
    await flushPromises()

    expect(mockPut).toHaveBeenCalledTimes(1)
    expect(wrapper.find('p.sr-only').text()).toBe('No changes to save.')
  })

  it('keeps a value the server refused when a later refresh arrives', async () => {
    const key = 'enrichment.providers.tmdb.language'
    mockPut.mockRejectedValue(
      new MockApiError(422, 'Unprocessable Entity', {
        detail: { key, reason: 'invalid language tag' },
      }),
    )
    const served = () => enrichment(textSetting(key, 'en-US'))
    const wrapper = await mountSection(served())
    await wrapper.find(`[data-testid="setting-${key}"]`).setValue('!!')

    await wrapper.find('[data-testid="save-enrichment"]').trigger('click')
    await flushPromises()
    // A refused save stores nothing, so the refresh re-serves pre-save truth.
    await wrapper.setProps({ section: served() })

    const field = wrapper.get(`[data-testid="setting-${key}"]`)
    expect((field.element as HTMLInputElement).value).toBe('!!')
  })

  it('keeps a typed value when a refused reset is followed by a refresh', async () => {
    mockDelete.mockRejectedValue(new MockApiError(503, 'Service Unavailable'))
    mockPut.mockResolvedValue({ sections: [] })
    const key = 'enrichment.providers.tmdb.language'
    const served = () =>
      enrichment(textSetting(key, 'de-DE', { db_overridden: true, has_stored_value: true }))
    const wrapper = await mountSection(served())
    await wrapper.find(`[data-testid="setting-${key}"]`).setValue('fr-FR')

    await wrapper.find(`[data-testid="reset-${key}"]`).trigger('click')
    await flushPromises()
    await wrapper.setProps({ section: served() })

    const field = wrapper.get(`[data-testid="setting-${key}"]`)
    expect((field.element as HTMLInputElement).value).toBe('fr-FR')
    await wrapper.find('[data-testid="save-enrichment"]').trigger('click')
    await flushPromises()
    expect(mockPut).toHaveBeenCalledWith('/settings', { updates: { [key]: 'fr-FR' } })
  })

  it('clears a dirtied field on a reset whose default is the value already served', async () => {
    mockDelete.mockResolvedValue({ sections: [] })
    // A stored row equal to the default stays resettable on purpose (see
    // setting_view), so this reset lands without moving the served value.
    const served = (stored: boolean) =>
      enrichment({
        ...numberSetting('enrichment.batch_size', 50),
        has_stored_value: stored,
      } as SettingView)
    const wrapper = await mountSection(served(true))
    await wrapper.find('[data-testid="setting-enrichment.batch_size"] input').setValue('90')

    await wrapper.find('[data-testid="reset-enrichment.batch_size"]').trigger('click')
    await flushPromises()
    await wrapper.setProps({ section: served(false) })

    expect(mockDelete).toHaveBeenCalledWith('/settings/enrichment.batch_size')
    const field = wrapper.get('[data-testid="setting-enrichment.batch_size"] input')
    expect((field.element as HTMLInputElement).value).toBe('50')
    await wrapper.find('[data-testid="save-enrichment"]').trigger('click')
    await flushPromises()
    expect(mockPut).not.toHaveBeenCalled()
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

  it('rescues focus to Save when a landed save strands the pointer operator on <body>', async () => {
    mockPut.mockResolvedValue({ sections: [] })
    const key = 'enrichment.providers.tmdb.language'
    const wrapper = await mountSection({
      section: 'enrichment',
      settings: [textSetting(key, 'en-US')],
    })
    const field = wrapper.get(`[data-testid="setting-${key}"]`)
    ;(field.element as HTMLInputElement).focus()
    await field.setValue('de-DE')
    // What a pointer operator who never tabbed out meets: the field disables for
    // the request and takes their focus with it.
    ;(field.element as HTMLInputElement).blur()
    expect(document.activeElement).toBe(document.body)

    await wrapper.get('[data-testid="save-enrichment"]').trigger('click')
    await flushPromises()

    expect(document.activeElement).toBe(wrapper.get('[data-testid="save-enrichment"]').element)
  })

  it('renders secrets in a Secrets fieldset and saves them out of band', async () => {
    mockPut.mockResolvedValue(undefined)
    mockGet.mockResolvedValue({ sections: [] })
    const section: SettingsSectionType = {
      section: 'enrichment',
      settings: [secretSetting('enrichment.providers.tmdb.api_key', false)],
    }
    const wrapper = await mountSection(section)

    expect(wrapper.find('.source-form-secrets legend').text()).toBe('Secrets')
    await wrapper.find('[data-testid="secret-replace-enrichment.providers.tmdb.api_key"]').trigger('click')
    await wrapper.find('#secret-input-enrichment\\.providers\\.tmdb\\.api_key').setValue('sk-999')
    await wrapper.find('[data-testid="secret-save-enrichment.providers.tmdb.api_key"]').trigger('click')
    await flushPromises()

    expect(mockPut).toHaveBeenCalledWith('/settings/secret', { key: 'enrichment.providers.tmdb.api_key', value: 'sk-999' })
  })

  it('marks the section unsaved while a pasted secret is still a draft, and lets go on Cancel', async () => {
    const key = 'enrichment.providers.tmdb.api_key'
    const wrapper = await mountSection(enrichment(secretSetting(key, false)))
    await wrapper.find(`[data-testid="secret-replace-${key}"]`).trigger('click')

    await wrapper.find(`#secret-input-${key.replace(/\./g, '\\.')}`).setValue('sk-999')

    expect(wrapper.emitted('update:dirty')).toEqual([[true]])
    expect(wrapper.find('[data-testid="dirty-enrichment"]').exists()).toBe(true)

    await wrapper.find(`[data-testid="secret-cancel-${key}"]`).trigger('click')

    expect(wrapper.emitted('update:dirty')).toEqual([[true], [false]])
    expect(wrapper.find('[data-testid="dirty-enrichment"]').exists()).toBe(false)
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

    it('gives a provider holding one setting an accordion of its own, like every other provider', async () => {
      const wrapper = renderSection(
        {
          section: 'enrichment',
          settings: [
            textSetting('enrichment.enabled', 'on'),
            textSetting('enrichment.providers.openlibrary.enabled', 'on', {
              label: 'Open Library enabled',
            }),
          ],
        },
        true,
      )

      expect(reachable(wrapper, 'setting-enrichment.enabled')).toBe(true)
      expect(reachable(wrapper, 'setting-enrichment.providers.openlibrary.enabled')).toBe(false)

      await accordionTrigger(wrapper, 'Open Library').trigger('click')

      expect(reachable(wrapper, 'setting-enrichment.providers.openlibrary.enabled')).toBe(true)
    })

    it('renders the provider order as a list of providers rather than a field of tags', () => {
      const wrapper = renderSection(
        {
          section: 'enrichment',
          settings: [
            orderSetting(['tmdb']),
            boolSetting('enrichment.providers.tmdb.enabled', true),
          ],
        },
        true,
      )

      expect(wrapper.find('[data-testid="setting-enrichment.provider_order"]').exists()).toBe(false)
      expect(wrapper.find('[data-testid="provider-order"]').exists()).toBe(true)
    })

    it('saves a reordered precedence through the same section save as every other edit', async () => {
      mockPut.mockResolvedValue({ sections: [] })
      const wrapper = renderSection(
        {
          section: 'enrichment',
          settings: [
            orderSetting(['tmdb', 'rawg']),
            boolSetting('enrichment.providers.tmdb.enabled', true),
            boolSetting('enrichment.providers.rawg.enabled', true),
          ],
        },
        true,
      )

      await wrapper.get('[data-provider="rawg"] [data-direction="up"]').trigger('click')
      await wrapper.get('[data-testid="save-enrichment"]').trigger('click')
      await flushPromises()

      expect(mockPut).toHaveBeenCalledWith('/settings', {
        updates: { 'enrichment.provider_order': ['rawg', 'tmdb'] },
      })
    })

    it('saves an order a provider registered since it was stored is missing from', async () => {
      mockPut.mockResolvedValue({ sections: [] })
      const wrapper = renderSection(
        {
          section: 'enrichment',
          settings: [
            orderSetting(['tmdb', 'rawg']),
            boolSetting('enrichment.providers.tmdb.enabled', true),
            boolSetting('enrichment.providers.rawg.enabled', true),
            boolSetting('enrichment.providers.igdb.enabled', true),
          ],
        },
        true,
      )

      await wrapper.get('[data-provider="rawg"] [data-direction="up"]').trigger('click')
      await wrapper.get('[data-testid="save-enrichment"]').trigger('click')
      await flushPromises()

      // The service refuses an order leaving a provider unranked, so igdb has to
      // leave with it or the section can never be saved again.
      expect(mockPut).toHaveBeenCalledWith('/settings', {
        updates: { 'enrichment.provider_order': ['rawg', 'tmdb', 'igdb'] },
      })
    })

    it('catches the focus a landed order reset takes with it, having no field to land on', async () => {
      const order = reactive(
        orderSetting(['tmdb'], {
          db_overridden: true,
          has_stored_value: true,
        }) as SettingViewValue,
      )
      mockDelete.mockImplementation(async () => {
        order.db_overridden = false
        order.has_stored_value = false
        return { sections: [] }
      })
      const wrapper = renderSection(
        {
          section: 'enrichment',
          settings: [order, boolSetting('enrichment.providers.tmdb.enabled', true)],
        },
        true,
      )
      const reset = wrapper.get('[data-testid="reset-enrichment.provider_order"]')
      ;(reset.element as HTMLButtonElement).focus()

      await reset.trigger('click')
      await flushPromises()

      expect(wrapper.find('[data-testid="reset-enrichment.provider_order"]').exists()).toBe(false)
      expect(document.activeElement).toBe(wrapper.get('[data-testid="save-enrichment"]').element)
    })

    it('catches the focus a refused order save leaves, having no field to land on', async () => {
      mockPut.mockRejectedValue(
        new MockApiError(422, 'Unprocessable Entity', {
          detail: {
            key: 'enrichment.provider_order',
            reason: 'names no installed enrichment provider',
          },
        }),
      )
      const wrapper = renderSection(
        {
          section: 'enrichment',
          settings: [
            orderSetting(['tmdb', 'rawg']),
            boolSetting('enrichment.providers.tmdb.enabled', true),
            boolSetting('enrichment.providers.rawg.enabled', true),
          ],
        },
        true,
      )
      await wrapper.get('[data-provider="rawg"] [data-direction="up"]').trigger('click')
      ;(document.activeElement as HTMLElement).blur()
      expect(document.activeElement).toBe(document.body)

      await wrapper.get('[data-testid="save-enrichment"]').trigger('click')
      await flushPromises()

      expect(document.activeElement).toBe(wrapper.get('[data-testid="save-enrichment"]').element)
    })

    it('opens the shut provider holding a value the server refused', async () => {
      const key = 'enrichment.providers.zzztest.language'
      mockPut.mockRejectedValue(
        new MockApiError(422, 'Unprocessable Entity', {
          detail: { key, reason: 'invalid language tag' },
        }),
      )
      const wrapper = renderSection(ZZZTEST, true)
      await accordionTrigger(wrapper, 'Zzztest').trigger('click')
      await wrapper.find(`[data-testid="setting-${key}"]`).setValue('!!')
      await accordionTrigger(wrapper, 'Zzztest').trigger('click')
      expect(reachable(wrapper, `setting-${key}`)).toBe(false)

      await wrapper.find('[data-testid="save-enrichment"]').trigger('click')
      await flushPromises()

      expect(reachable(wrapper, `setting-${key}`)).toBe(true)
      expect(document.activeElement).toBe(wrapper.find(`[data-testid="setting-${key}"]`).element)
    })

    it('announces a reorder through the region the section already mounts for it', async () => {
      const wrapper = renderSection(
        {
          section: 'enrichment',
          settings: [
            orderSetting(['tmdb', 'rawg']),
            boolSetting('enrichment.providers.tmdb.enabled', true),
            boolSetting('enrichment.providers.rawg.enabled', true),
          ],
        },
        true,
      )

      await wrapper.get('[data-provider="rawg"] [data-direction="up"]').trigger('click')
      await flushPromises()

      expect(wrapper.get('p.sr-only[role="status"]').text()).toContain('moved to position 1 of 2')
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
      settings: [secretSetting('enrichment.providers.tmdb.api_key', true)],
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
