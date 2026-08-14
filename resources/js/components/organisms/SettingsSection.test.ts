import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { nextTick } from 'vue'
import { mount, flushPromises, enableAutoUnmount } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import SettingsSection from './SettingsSection.vue'
import type { SettingsSection as SettingsSectionType, SettingView } from '@/types/api'

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
    ...extra,
  } as SettingView
}

function secretSetting(key: string, extra: Partial<SettingView> = {}): SettingView {
  return {
    key,
    section: 'enrichment',
    label: 'TMDB API key',
    help: '',
    type: 'string',
    widget: 'text',
    choices: null,
    validation: null,
    advanced: false,
    restart_required: false,
    sensitive: true,
    has_secret: false,
    ...extra,
  } as SettingView
}

// Every mount here uses attachTo (focus() is inert on a detached element), so
// each one leaks its DOM into document.body until unmounted. That matters more
// than usual: SettingsSection resolves focus targets with
// document.getElementById, which returns the FIRST match in tree order — so a
// leaked node from an earlier test would satisfy a focus assertion in a later
// one, and the assertion could not tell the difference. Unmount everything
// between tests so each starts from an empty body.
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

  it('renders one control per non-advanced value setting under a humanized heading', () => {
    const section: SettingsSectionType = {
      section: 'enrichment',
      settings: [textSetting('enrichment.base_url', 'http://localhost:11434'), textSetting('enrichment.model', 'x')],
    }
    const wrapper = mountSection(section)
    expect(wrapper.find('h3').text()).toBe('Enrichment')
    expect(wrapper.find('[data-testid="setting-enrichment.base_url"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="setting-enrichment.model"]').exists()).toBe(true)
  })

  it('saves only the changed keys and labels the Save button by section', async () => {
    mockPut.mockResolvedValue({ sections: [] })
    const section: SettingsSectionType = {
      section: 'enrichment',
      settings: [textSetting('enrichment.base_url', 'http://localhost:11434'), textSetting('enrichment.model', 'x')],
    }
    const wrapper = mountSection(section)

    expect(wrapper.find('[data-testid="save-enrichment"]').text()).toBe('Save Enrichment')
    await wrapper.find('[data-testid="setting-enrichment.base_url"]').setValue('http://enrichment:11434')
    await wrapper.find('[data-testid="save-enrichment"]').trigger('click')
    await flushPromises()

    expect(mockPut).toHaveBeenCalledWith('/settings', { updates: { 'enrichment.base_url': 'http://enrichment:11434' } })
  })

  it('maps a 422 to the offending field and moves focus to it', async () => {
    mockPut.mockRejectedValue(
      new MockApiError(422, 'Unprocessable Entity', {
        detail: { key: 'enrichment.base_url', reason: 'invalid host' },
      }),
    )
    const section: SettingsSectionType = {
      section: 'enrichment',
      settings: [textSetting('enrichment.base_url', 'http://localhost:11434')],
    }
    const wrapper = mountSection(section)

    await wrapper.find('[data-testid="setting-enrichment.base_url"]').setValue('!!')
    await wrapper.find('[data-testid="save-enrichment"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="setting-error-enrichment.base_url"]').text()).toBe('invalid host')
    // Identity, not id: an id comparison is satisfied by any element carrying
    // that id, including one leaked into document.body by an earlier test.
    expect(document.activeElement).toBe(
      wrapper.find('[data-testid="setting-enrichment.base_url"]').element,
    )
  })

  it('names the offending field in the section-level save banner', async () => {
    // The service's pattern message is deictic ("see this setting's help"), and
    // the banner sits in the footer naming no field — so unprefixed it points at
    // nothing a screen reader user can resolve.
    mockPut.mockRejectedValue(
      new MockApiError(422, 'Unprocessable Entity', {
        detail: { key: 'enrichment.base_url', reason: 'see this setting\'s help' },
      }),
    )
    // The label must differ from the key here: textSetting defaults label to the
    // key, which would let a buggy `offending.key` prefix satisfy the assertion.
    // A dotted path read aloud in a role="alert" region is exactly the outcome
    // this guards against.
    const wrapper = mountSection({
      section: 'enrichment',
      settings: [
        textSetting('enrichment.base_url', 'http://localhost:11434', {
          label: 'Enrichment base URL',
        }),
      ],
    })

    await wrapper.find('[data-testid="setting-enrichment.base_url"]').setValue('!!')
    await wrapper.find('[data-testid="save-enrichment"]').trigger('click')
    await flushPromises()

    const banner = wrapper.find('[data-testid="save-status-enrichment"]')
    expect(banner.text()).toContain('Enrichment base URL:')
    expect(banner.text()).not.toContain('enrichment.base_url:')
    expect(banner.attributes('role')).toBe('alert')
  })

  it('does not nest the save status inside a second live region', () => {
    // role="status"/role="alert" on the status span are implicit live regions.
    // Wrapping them in aria-live double-announces, and aria-atomic drags the
    // button's own label into every announcement — so Save → Saving… re-reads
    // the whole group.
    const wrapper = mountSection({
      section: 'enrichment',
      settings: [textSetting('enrichment.base_url', 'http://localhost:11434')],
    })

    const group = wrapper.find('.settings-section-save-group')
    expect(group.attributes('aria-live')).toBeUndefined()
    expect(group.attributes('aria-atomic')).toBeUndefined()
    wrapper.unmount()
  })

  it('drops a second Save activation while the first is in flight', async () => {
    // The button stays focusable via aria-disabled (see the focus test below),
    // and aria-disabled does not block activation — so the guard in onSave is
    // the only thing preventing a duplicate PUT.
    let resolvePut: (value: unknown) => void = () => {}
    mockPut.mockReturnValue(new Promise((resolve) => { resolvePut = resolve }))
    const wrapper = mountSection({
      section: 'enrichment',
      settings: [textSetting('enrichment.base_url', 'http://localhost:11434')],
    })

    await wrapper.find('[data-testid="setting-enrichment.base_url"]').setValue('http://enrichment:11434')
    const save = wrapper.find('[data-testid="save-enrichment"]')
    await save.trigger('click')
    expect(save.attributes('aria-disabled')).toBe('true')
    await save.trigger('click')

    expect(mockPut).toHaveBeenCalledTimes(1)
    resolvePut({ sections: [] })
    await flushPromises()
    wrapper.unmount()
  })

  it('keeps focus on the Save button through a successful save', async () => {
    // Regression: the button was `:disabled="saving"`, and `saving` flips true
    // before the await — disabling the element the user just activated blurs it,
    // dropping focus to <body> for the whole request with nothing restoring it.
    // A keyboard user pressed Enter, heard "Saved", and found their next Tab
    // restarted from the top of the document (WCAG 2.4.3).
    mockPut.mockResolvedValue({ sections: [] })
    const section: SettingsSectionType = {
      section: 'enrichment',
      settings: [textSetting('enrichment.base_url', 'http://localhost:11434')],
    }
    const wrapper = mountSection(section)

    const save = wrapper.find('[data-testid="save-enrichment"]')
    ;(save.element as HTMLButtonElement).focus()
    await save.trigger('click')
    await flushPromises()

    expect(document.activeElement).toBe(save.element)
    wrapper.unmount()
  })

  it('does not PUT or claim a save when nothing was edited', async () => {
    // Regression: Save always issued PUT /settings with {updates: {}} and then
    // showed "Saved ✓", telling the user a write happened that did not.
    const wrapper = mountSection({
      section: 'enrichment',
      settings: [textSetting('enrichment.base_url', 'http://localhost:11434')],
    })

    await wrapper.find('[data-testid="save-enrichment"]').trigger('click')
    await flushPromises()

    expect(mockPut).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="save-status-enrichment"]').exists()).toBe(false)
    expect(wrapper.find('p.sr-only').text()).toBe('No changes to save.')
  })

  it('expands the Advanced disclosure and focuses an offending advanced field', async () => {
    // Every advanced setting now lives exclusively behind the collapsed
    // disclosure, so a 422 on one must open the panel before focusing it —
    // otherwise a keyboard user is left on Save with the error out of sight and
    // focus dropped to <body> (WCAG 2.4.3 Focus Order).
    mockPut.mockRejectedValue(
      new MockApiError(422, 'Unprocessable Entity', {
        detail: { key: 'web.allowed_origins', reason: 'invalid origin' },
      }),
    )
    const section: SettingsSectionType = {
      section: 'enrichment',
      settings: [
        textSetting('enrichment.model', 'x'),
        textSetting('web.allowed_origins', 'http://localhost:18473', { advanced: true }),
      ],
    }
    const wrapper = mountSection(section)

    expect(wrapper.find('.accordion-trigger').attributes('aria-expanded')).toBe('false')

    // Edit the non-advanced field so the save has a real diff — Save is a no-op
    // when nothing changed, and this test is about the 422 response.
    await wrapper.find('[data-testid="setting-enrichment.model"]').setValue('mistral:7b')
    await wrapper.find('[data-testid="save-enrichment"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('.accordion-trigger').attributes('aria-expanded')).toBe('true')
    expect(document.activeElement).toBe(
      wrapper.find('[data-testid="setting-web.allowed_origins"]').element,
    )
  })

  it('resets an overridden setting via DELETE', async () => {
    mockDelete.mockResolvedValue({ sections: [] })
    const section: SettingsSectionType = {
      section: 'enrichment',
      settings: [textSetting('enrichment.base_url', 'http://localhost:11434', { db_overridden: true })],
    }
    const wrapper = mountSection(section)

    await wrapper.find('[data-testid="reset-enrichment.base_url"]').trigger('click')
    await flushPromises()

    expect(mockDelete).toHaveBeenCalledWith('/settings/enrichment.base_url')
  })

  it('renders restart and overridden pills for the relevant settings', () => {
    const section: SettingsSectionType = {
      section: 'enrichment',
      settings: [
        textSetting('enrichment.base_url', 'http://localhost:11434', { restart_required: true, db_overridden: true }),
      ],
    }
    const wrapper = mountSection(section)
    expect(wrapper.find('[data-testid="restart-badge-enrichment.base_url"]').text()).toContain('Requires restart')
    expect(wrapper.find('[data-testid="overridden-badge-enrichment.base_url"]').text()).toContain('Overridden')
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

  it('collapses advanced settings into a keyboard-operable disclosure with a caution note', async () => {
    const section: SettingsSectionType = {
      section: 'enrichment',
      settings: [
        textSetting('enrichment.base_url', 'http://localhost:11434'),
        textSetting('web.allowed_origins', 'x', { advanced: true }),
      ],
    }
    const wrapper = mountSection(section)

    const trigger = wrapper.find('.accordion-trigger')
    expect(trigger.text()).toContain('Advanced · 1 setting')
    expect(trigger.attributes('aria-expanded')).toBe('false')
    // Panel is hidden while collapsed.
    expect(wrapper.find('.accordion-panel').attributes('hidden')).toBeDefined()
    expect(wrapper.find('[role="note"]').exists()).toBe(true)

    // Native button trigger toggles on activation (keyboard Enter/Space).
    await trigger.trigger('click')
    expect(wrapper.find('.accordion-trigger').attributes('aria-expanded')).toBe('true')
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

    it('falls back to generic copy for a section with no bespoke caution', () => {
      const note = mountAdvanced('sync', 'sync.max_workers').find('[role="note"]')
      expect(note.text()).toContain('how this instance runs')
      // The fallback must not assert a restart, which it cannot guarantee.
      expect(note.text()).not.toContain('restart')
    })
  })

  it('nests the Advanced disclosure heading at h4 under the section h3', () => {
    const section: SettingsSectionType = {
      section: 'enrichment',
      settings: [
        textSetting('enrichment.base_url', 'http://localhost:11434'),
        textSetting('web.allowed_origins', 'x', { advanced: true }),
      ],
    }
    const wrapper = mountSection(section)
    expect(wrapper.find('h4.accordion-heading .accordion-trigger').exists()).toBe(true)
    expect(wrapper.find('h3.accordion-heading').exists()).toBe(false)
  })

  it('marks the control as resetting while the DELETE is in flight', async () => {
    let resolveDelete: (v: unknown) => void = () => {}
    mockDelete.mockReturnValue(
      new Promise((resolve) => {
        resolveDelete = resolve
      }),
    )
    const section: SettingsSectionType = {
      section: 'enrichment',
      settings: [textSetting('enrichment.base_url', 'http://localhost:11434', { db_overridden: true })],
    }
    const wrapper = mountSection(section)

    await wrapper.find('[data-testid="reset-enrichment.base_url"]').trigger('click')
    await nextTick()
    const btn = wrapper.find('[data-testid="reset-enrichment.base_url"]')
    expect(btn.text()).toBe('Resetting…')
    expect(btn.attributes('disabled')).toBeDefined()

    resolveDelete({ sections: [] })
    await flushPromises()
    wrapper.unmount()
  })

  describe('when a reset or secret action fails', () => {
    // Regression: onReset/onSetSecret/onClearSecret used try/finally with no
    // catch, and neither the store nor useApi swallows a non-2xx. So a 503 or a
    // dropped connection produced a button that flickered busy and back, an
    // empty live region, and an unhandled promise rejection — a silent no-op.
    // Only the save path had a failure branch, which is why this survived.
    const OVERRIDDEN: SettingsSectionType = {
      section: 'enrichment',
      settings: [
        textSetting('enrichment.base_url', 'http://localhost:11434', {
          db_overridden: true,
        }),
      ],
    }
    const SECRET: SettingsSectionType = {
      section: 'enrichment',
      settings: [secretSetting('enrichment.providers.tmdb.api_key', { has_secret: true })],
    }

    it('announces a failed reset instead of doing nothing', async () => {
      mockDelete.mockRejectedValue(new MockApiError(503, 'Service Unavailable'))
      const wrapper = mountSection(OVERRIDDEN)

      await wrapper.find('[data-testid="reset-enrichment.base_url"]').trigger('click')
      await flushPromises()

      expect(wrapper.find('p.sr-only').text()).toContain('Reset failed.')
      // The control is still there and still focused — the user has not been
      // dumped at <body> with no explanation.
      expect(document.activeElement).toBe(
        wrapper.find('[data-testid="setting-enrichment.base_url"]').element,
      )
      // And the button is usable again for a retry.
      expect(
        wrapper.find('[data-testid="reset-enrichment.base_url"]').attributes('disabled'),
      ).toBeUndefined()
    })

    it('announces a failed secret save', async () => {
      const wrapper = mountSection(SECRET)
      await wrapper
        .find('[data-testid="secret-replace-enrichment.providers.tmdb.api_key"]')
        .trigger('click')
      await nextTick()
      await wrapper
        .find('#secret-input-enrichment\\.providers\\.tmdb\\.api_key')
        .setValue('sk-1')

      mockPut.mockRejectedValue(new MockApiError(400, 'Bad Request'))
      await wrapper
        .find('[data-testid="secret-save-enrichment.providers.tmdb.api_key"]')
        .trigger('click')
      await flushPromises()

      expect(wrapper.find('p.sr-only').text()).toContain('Saving the secret failed.')
    })

    it('announces a failed secret clear', async () => {
      mockDelete.mockRejectedValue(new Error('network down'))
      const wrapper = mountSection(SECRET)

      await wrapper
        .find('[data-testid="secret-clear-enrichment.providers.tmdb.api_key"]')
        .trigger('click')
      await flushPromises()

      const announcement = wrapper.find('p.sr-only').text()
      expect(announcement).toContain('Clearing the secret failed.')
      // The reason is carried through so the message is actionable.
      expect(announcement).toContain('network down')
    })
  })

  it('announces the reset and returns focus to the control', async () => {
    mockDelete.mockResolvedValue({ sections: [] })
    const section: SettingsSectionType = {
      section: 'enrichment',
      settings: [textSetting('enrichment.base_url', 'http://localhost:11434', { db_overridden: true })],
    }
    const wrapper = mountSection(section)

    await wrapper.find('[data-testid="reset-enrichment.base_url"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('p.sr-only').text()).toBe('Reset to default.')
    expect(document.activeElement).toBe(
      wrapper.find('[data-testid="setting-enrichment.base_url"]').element,
    )
  })

  it('re-announces an identical repeated action by re-mutating the live region', async () => {
    mockDelete.mockResolvedValue({ sections: [] })
    const section: SettingsSectionType = {
      section: 'enrichment',
      settings: [textSetting('enrichment.base_url', 'http://localhost:11434', { db_overridden: true })],
    }
    const wrapper = mountSection(section)
    const region = wrapper.find('p.sr-only').element

    await wrapper.find('[data-testid="reset-enrichment.base_url"]').trigger('click')
    await flushPromises()
    expect(region.textContent).toBe('Reset to default.')

    // Reassigning the same string is a no-op for a persistent status region, so
    // AT stays silent. The fix blanks the region for one tick before re-setting
    // it, so a repeated identical reset passes through '' — a real mutation the
    // screen reader re-announces. Pump microtasks until the message re-settles,
    // capturing that transient blank without depending on a fixed tick count
    // (which could miss it if the mocked promise resolves at a different depth).
    const seen = new Set<string>()
    void wrapper.find('[data-testid="reset-enrichment.base_url"]').trigger('click')
    for (let i = 0; i < 25; i++) {
      await nextTick()
      seen.add(region.textContent ?? '')
      if (seen.has('') && region.textContent === 'Reset to default.') break
    }
    await flushPromises()

    expect([...seen]).toContain('')
    expect(region.textContent).toBe('Reset to default.')
    wrapper.unmount()
  })

  it('disables the Replace button and announces success while a secret set is in flight', async () => {
    let resolvePut: (v: unknown) => void = () => {}
    mockPut.mockReturnValue(
      new Promise((resolve) => {
        resolvePut = resolve
      }),
    )
    mockGet.mockResolvedValue({ sections: [] })
    const section: SettingsSectionType = {
      section: 'enrichment',
      settings: [secretSetting('enrichment.providers.tmdb.api_key')],
    }
    const wrapper = mountSection(section)

    await wrapper.find('[data-testid="secret-replace-enrichment.providers.tmdb.api_key"]').trigger('click')
    await wrapper.find('#secret-input-enrichment\\.providers\\.tmdb\\.api_key').setValue('sk-1')
    await wrapper.find('[data-testid="secret-save-enrichment.providers.tmdb.api_key"]').trigger('click')
    await nextTick()

    expect(
      wrapper.find('[data-testid="secret-replace-enrichment.providers.tmdb.api_key"]').attributes('disabled'),
    ).toBeDefined()

    resolvePut(undefined)
    await flushPromises()
    expect(wrapper.find('p.sr-only').text()).toBe('Secret saved.')
    wrapper.unmount()
  })

  it('announces "Secret cleared." after clearing a secret', async () => {
    mockDelete.mockResolvedValue(undefined)
    mockGet.mockResolvedValue({ sections: [] })
    const section: SettingsSectionType = {
      section: 'enrichment',
      settings: [secretSetting('enrichment.providers.tmdb.api_key', { has_secret: true })],
    }
    const wrapper = mountSection(section)

    await wrapper.find('[data-testid="secret-clear-enrichment.providers.tmdb.api_key"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('p.sr-only').text()).toBe('Secret cleared.')
    wrapper.unmount()
  })

  it('auto-clears the saved status after the timeout elapses', async () => {
    vi.useFakeTimers()
    try {
      mockPut.mockResolvedValue({ sections: [] })
      const section: SettingsSectionType = {
        section: 'enrichment',
        settings: [textSetting('enrichment.base_url', 'http://localhost:11434')],
      }
      const wrapper = mountSection(section)

      await wrapper.find('[data-testid="setting-enrichment.base_url"]').setValue('http://enrichment:11434')
      await wrapper.find('[data-testid="save-enrichment"]').trigger('click')
      await vi.advanceTimersByTimeAsync(0)
      await nextTick()
      expect(wrapper.find('[data-testid="save-status-enrichment"]').text()).toBe('Saved ✓')

      await vi.advanceTimersByTimeAsync(2500)
      await nextTick()
      expect(wrapper.find('[data-testid="save-status-enrichment"]').exists()).toBe(false)
      wrapper.unmount()
    } finally {
      vi.useRealTimers()
    }
  })
})
