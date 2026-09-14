import { describe, it, expect, afterEach } from 'vitest'
import { mount, enableAutoUnmount, type VueWrapper } from '@vue/test-utils'
import { nextTick, reactive } from 'vue'
import EnrichmentProviders from './EnrichmentProviders.vue'
import { providerGroups } from '@/utils/settingsGroups'
import type { SettingBufferValue } from '@/composables/useSettingsBuffer'
import type { SettingView, SettingViewValue } from '@/types/api'

enableAutoUnmount(afterEach)

function setting(key: string, label: string, overrides: Partial<SettingView> = {}): SettingView {
  return {
    key,
    section: 'enrichment',
    label,
    help: '',
    type: 'bool',
    widget: 'toggle',
    choices: null,
    validation: null,
    advanced: false,
    restart_required: false,
    sensitive: false,
    value: false,
    db_overridden: false,
    has_stored_value: false,
    ...overrides,
  } as SettingView
}

function toggleSetting(name: string, label: string): SettingView {
  return setting(`enrichment.providers.${name}.enabled`, `${label} enabled`)
}

const ORDER_SETTING = setting('enrichment.provider_order', 'Provider precedence', {
  type: 'list',
  widget: 'provider-order',
  help: 'Providers are tried in this order and the first one to match an item enriches it.',
  value: [],
}) as SettingViewValue

const DEFAULT_SETTINGS = [
  toggleSetting('hardcover', 'Hardcover'),
  toggleSetting('rawg', 'RAWG'),
  toggleSetting('tmdb', 'TMDB'),
]

interface Options {
  settings?: SettingView[]
  order?: string[]
  on?: string[]
  orderSetting?: SettingViewValue | null
  disabled?: boolean
}

function enabledKey(name: string): string {
  return `enrichment.providers.${name}.enabled`
}

/** Stands in for SettingsSection: the buffer write lands synchronously inside
 *  the handler, which is what the component's own announcement reads back. */
function render(options: Options = {}): VueWrapper {
  const settings = options.settings ?? DEFAULT_SETTINGS
  const on = options.on ?? ['hardcover', 'rawg', 'tmdb']
  const values = reactive<Record<string, SettingBufferValue>>({})
  for (const entry of settings) {
    values[entry.key] = on.some((name) => entry.key === enabledKey(name))
  }
  // Mutated in place rather than replaced: the child holds the reference, and a
  // reassignment here would not reach it the way the real buffer does.
  const order = reactive<string[]>([...(options.order ?? ['hardcover', 'rawg', 'tmdb'])])
  return mount(EnrichmentProviders, {
    attachTo: document.body,
    props: {
      providers: providerGroups(settings).providers,
      order,
      orderSetting: options.orderSetting === undefined ? ORDER_SETTING : options.orderSetting,
      values,
      errors: {},
      resetting: {},
      secretBusy: {},
      expanded: {},
      disabled: options.disabled ?? false,
      'onUpdate:order': (next: string[]) => {
        order.splice(0, order.length, ...next)
      },
      onUpdate: (key: string, value: SettingBufferValue) => {
        values[key] = value
      },
    },
  })
}

function rankedNames(wrapper: VueWrapper): string[] {
  return wrapper
    .findAll('[data-testid="provider-order"] [data-provider]')
    .map((row) => row.attributes('data-provider')!)
}

function unrankedNames(wrapper: VueWrapper): string[] {
  return wrapper
    .findAll('[data-testid="provider-unranked"] [data-provider]')
    .map((row) => row.attributes('data-provider')!)
}

function moveButton(wrapper: VueWrapper, name: string, direction: 'up' | 'down') {
  return wrapper.get(`[data-provider="${name}"] [data-direction="${direction}"]`)
}

function announcements(wrapper: VueWrapper): string[] {
  return (wrapper.emitted('announce') ?? []).map((event) => event[0] as string)
}

describe('EnrichmentProviders', () => {
  it('ranks the providers that are on and leaves the rest out of the order', () => {
    const wrapper = render({ on: ['tmdb', 'hardcover'] })

    expect(rankedNames(wrapper)).toEqual(['hardcover', 'tmdb'])
    expect(unrankedNames(wrapper)).toEqual(['rawg'])
  })

  it('reads the precedence run as one ordered list', () => {
    const wrapper = render()

    const list = wrapper.get('[data-testid="provider-order"]')
    expect(list.element.tagName).toBe('OL')
    expect(list.findAll('li')).toHaveLength(3)
  })

  it('moves a provider past the one above it and leaves the names between them in place', async () => {
    const wrapper = render({
      settings: [...DEFAULT_SETTINGS, toggleSetting('openlibrary', 'Open Library')],
      order: ['hardcover', 'openlibrary', 'rawg', 'tmdb'],
      on: ['hardcover', 'rawg', 'tmdb'],
    })

    await moveButton(wrapper, 'rawg', 'up').trigger('click')

    // openlibrary is off, so it sits between the two in the stored order and
    // must not be carried along by a move made over the top of it.
    expect(wrapper.emitted('update:order')).toEqual([
      [['rawg', 'openlibrary', 'hardcover', 'tmdb']],
    ])
    expect(rankedNames(wrapper)).toEqual(['rawg', 'hardcover', 'tmdb'])
  })

  it('says which provider moved and where it landed', async () => {
    const wrapper = render()

    await moveButton(wrapper, 'tmdb', 'up').trigger('click')

    expect(announcements(wrapper)).toEqual(['TMDB moved to position 2 of 3.'])
  })

  it('keeps focus on the arrow that moved a provider once its row has moved', async () => {
    const wrapper = render()
    const button = moveButton(wrapper, 'tmdb', 'up')
    ;(button.element as HTMLButtonElement).focus()

    await button.trigger('click')
    await nextTick()

    expect(rankedNames(wrapper)).toEqual(['hardcover', 'tmdb', 'rawg'])
    expect(document.activeElement).toBe(
      wrapper.get('[data-provider="tmdb"] [data-direction="up"]').element,
    )
  })

  it('refuses to move the first provider up without dropping the focus that pressed it', async () => {
    const wrapper = render()
    const button = moveButton(wrapper, 'hardcover', 'up')
    ;(button.element as HTMLButtonElement).focus()

    expect(button.attributes('aria-disabled')).toBe('true')
    expect(button.attributes('disabled')).toBeUndefined()

    await button.trigger('click')

    expect(wrapper.emitted('update:order')).toBeUndefined()
    expect(document.activeElement).toBe(button.element)
  })

  it('marks the last provider as unable to move down', () => {
    const wrapper = render()

    expect(moveButton(wrapper, 'tmdb', 'down').attributes('aria-disabled')).toBe('true')
    expect(moveButton(wrapper, 'hardcover', 'down').attributes('aria-disabled')).toBeUndefined()
  })

  it('ranks a provider the stored order never named instead of pinning it under the arrows', async () => {
    const wrapper = render({ order: ['hardcover', 'tmdb'] })

    expect(rankedNames(wrapper)).toEqual(['hardcover', 'tmdb', 'rawg'])

    await moveButton(wrapper, 'rawg', 'up').trigger('click')

    expect(wrapper.emitted('update:order')).toEqual([[['hardcover', 'rawg', 'tmdb']]])
  })

  it('drops a name the stored order holds for a provider that is no longer installed', async () => {
    const wrapper = render({ order: ['hardcover', 'personal_shelf', 'rawg', 'tmdb'] })

    await moveButton(wrapper, 'rawg', 'up').trigger('click')

    // Nothing renders the stale name, so an order that keeps carrying it is a
    // save the service refuses with no control left to fix it.
    expect(wrapper.emitted('update:order')).toEqual([[['rawg', 'hardcover', 'tmdb']]])
  })

  it('gives a provider that is out of the order no arrows to reorder it with', () => {
    const wrapper = render({ on: ['hardcover'] })

    expect(wrapper.find('[data-provider="rawg"] [data-direction="up"]').exists()).toBe(false)
    expect(wrapper.find('[data-provider="hardcover"] [data-direction="up"]').exists()).toBe(true)
  })

  it('refuses a move while a write in the section is in flight, and says why it cannot be used', async () => {
    const wrapper = render({ disabled: true })
    const button = moveButton(wrapper, 'tmdb', 'up')

    expect(button.attributes('aria-disabled')).toBe('true')
    expect(wrapper.get(`[id="${button.attributes('aria-describedby')}"]`).text()).toContain(
      'in flight',
    )

    await button.trigger('click')

    expect(wrapper.emitted('update:order')).toBeUndefined()
  })

  it('names the provider each arrow moves, so two rows of arrows are told apart', () => {
    const wrapper = render()

    expect(moveButton(wrapper, 'tmdb', 'up').attributes('aria-label')).toBe('Move TMDB up')
    expect(moveButton(wrapper, 'tmdb', 'down').attributes('aria-label')).toBe('Move TMDB down')
  })

  it('carries the position of a provider in the accessible name of its row', () => {
    const wrapper = render()

    const trigger = wrapper.get('[data-provider="rawg"] button.accordion-trigger')
    expect(trigger.text()).toContain('RAWG')
    expect(trigger.text()).toContain('Position 2 of 3')
  })

  it('says a provider is off in text rather than in colour alone', () => {
    const wrapper = render({ on: ['hardcover', 'tmdb'] })

    expect(wrapper.get('[data-provider="rawg"] button.accordion-trigger').text()).toContain('Off')
  })

  it('says where a provider landed when its own switch put it in the order', async () => {
    const wrapper = render({ on: ['hardcover'] })

    await wrapper
      .get(`[data-testid="setting-${enabledKey('rawg')}"] [role="switch"]`)
      .trigger('click')
    await nextTick()

    expect(rankedNames(wrapper)).toEqual(['hardcover', 'rawg'])
    expect(announcements(wrapper)).toEqual(['RAWG on, at position 2 of 2.'])
  })

  it('says a provider left the order when its own switch turned it off', async () => {
    const wrapper = render({ on: ['hardcover', 'rawg'] })

    await wrapper
      .get(`[data-testid="setting-${enabledKey('rawg')}"] [role="switch"]`)
      .trigger('click')
    await nextTick()

    expect(rankedNames(wrapper)).toEqual(['hardcover'])
    expect(announcements(wrapper)).toEqual(['RAWG off, and out of the precedence order.'])
  })

  it('keeps focus on the switch that moved its own row between the two lists', async () => {
    const wrapper = render({ on: ['hardcover', 'rawg'] })
    const key = enabledKey('rawg')
    const toggle = wrapper.get(`[data-testid="setting-${key}"] [role="switch"]`)
    ;(toggle.element as HTMLButtonElement).focus()

    await toggle.trigger('click')
    await nextTick()

    expect(rankedNames(wrapper)).toEqual(['hardcover'])
    expect(unrankedNames(wrapper)).toContain('rawg')
    expect(document.activeElement).toBe(document.getElementById(`setting-${key}`))
  })

  it('renders one plain list when the section serves no precedence order', () => {
    const wrapper = render({ orderSetting: null })

    expect(wrapper.find('[data-testid="provider-order"]').exists()).toBe(false)
    expect(unrankedNames(wrapper)).toEqual(['hardcover', 'rawg', 'tmdb'])
    expect(wrapper.findAll('[data-direction="up"]')).toHaveLength(0)
  })

  it('offers the same order reset the CLI has', async () => {
    const wrapper = render({
      orderSetting: { ...ORDER_SETTING, has_stored_value: true, db_overridden: true },
    })

    await wrapper.get('[data-testid="reset-enrichment.provider_order"]').trigger('click')

    expect(wrapper.emitted('reset')).toEqual([['enrichment.provider_order']])
    expect(
      wrapper.find('[data-testid="overridden-badge-enrichment.provider_order"]').exists(),
    ).toBe(true)
  })
})
