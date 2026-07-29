import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createTestingPinia } from '@pinia/testing'
import DataPage from './DataPage.vue'
import { useDataStore } from '@/stores/data'
import type { SyncSourceResponse } from '@/types/api'

function source(overrides: Partial<SyncSourceResponse>): SyncSourceResponse {
  return {
    id: 'steam',
    display_name: 'Steam',
    plugin_display_name: 'Steam',
    enabled: true,
    is_file_import: false,
    ...overrides,
  }
}

function mountPage(syncSources: SyncSourceResponse[]) {
  const wrapper = mount(DataPage, {
    attachTo: document.body,
    global: {
      plugins: [createTestingPinia({ createSpy: vi.fn })],
      stubs: {
        SyncSourceAccordion: {
          props: ['source'],
          emits: ['removed'],
          template:
            '<div class="accordion-stub" @click="$emit(\'removed\', source.display_name)">{{ source.id }}</div>',
        },
        EnrichmentCard: true,
        AddSourceModal: true,
        ImportFileModal: {
          emits: ['close'],
          template: '<div class="import-modal-stub" @click="$emit(\'close\')" />',
        },
      },
    },
  })
  const data = useDataStore()
  Object.assign(data, { syncSources, syncLoading: false })
  return wrapper
}

describe('DataPage source ordering', () => {
  it('sorts enabled, then disabled, then leftover file-import entries', async () => {
    const wrapper = mountPage([
      source({ id: 'legacy_books', is_file_import: true }),
      source({ id: 'radarr', enabled: false }),
      source({ id: 'sonarr' }),
    ])
    await wrapper.vm.$nextTick()

    const ids = wrapper
      .findAll('.accordion-stub')
      .map((node) => node.text())

    // A file-import entry keeps enabled=true (nothing rewrites the stored
    // flag), so ordering on `enabled` alone would float it above real,
    // syncable sources — it belongs below even the disabled ones.
    expect(ids).toEqual(['sonarr', 'radarr', 'legacy_books'])
  })

  it('preserves the API ordering within each group', async () => {
    const wrapper = mountPage([
      source({ id: 'a_source' }),
      source({ id: 'b_source' }),
      source({ id: 'c_source' }),
    ])
    await wrapper.vm.$nextTick()

    const ids = wrapper
      .findAll('.accordion-stub')
      .map((node) => node.text())

    expect(ids).toEqual(['a_source', 'b_source', 'c_source'])
  })
})

describe('DataPage import dialog', () => {
  // The button and its `v-if` are the entry point to the whole import feature,
  // and a wrong @click target or v-if condition would ship green with the modal
  // stubbed and never opened.
  it('mounts the modal only after the Import button is clicked', async () => {
    const wrapper = mountPage([source({})])
    expect(wrapper.find('.import-modal-stub').exists()).toBe(false)

    await wrapper.find('[data-testid="import-file-btn"]').trigger('click')

    expect(wrapper.find('.import-modal-stub').exists()).toBe(true)
    wrapper.unmount()
  })

  it('unmounts the modal when it emits close', async () => {
    const wrapper = mountPage([source({})])
    await wrapper.find('[data-testid="import-file-btn"]').trigger('click')

    await wrapper.find('.import-modal-stub').trigger('click')

    expect(wrapper.find('.import-modal-stub').exists()).toBe(false)
    wrapper.unmount()
  })

  it('announces a removed source and takes focus off the vanished row', async () => {
    // Regression: deleteSource filtered the row out, so the accordion and the
    // Remove button holding focus unmounted together. Nothing was announced
    // and keyboard users were dumped back at <body> (WCAG 2.4.3 / 4.1.3).
    const wrapper = mountPage([source({ id: 'legacy', display_name: 'Legacy' })])
    await wrapper.vm.$nextTick()

    const live = wrapper.find('p.sr-only[role="status"]')
    // The region is mounted and empty BEFORE the event, so the announcement
    // arrives as a text change rather than as a freshly inserted node.
    expect(live.exists()).toBe(true)
    expect(live.text()).toBe('')

    // Read the live region at the exact moment focus moves. Several screen
    // readers drop a queued polite announcement when a focus change arrives
    // alongside it, and this announcement is the only signal a non-sighted
    // user gets — so the focus move has to land first, on its own.
    const addSource = wrapper.find('[data-testid="add-source-btn"]')
      .element as HTMLButtonElement
    let liveTextAtFocus: string | null = null
    vi.spyOn(addSource, 'focus').mockImplementation(function (this: HTMLElement) {
      liveTextAtFocus = live.text()
      HTMLElement.prototype.focus.call(this)
    })

    await wrapper.find('.accordion-stub').trigger('click')
    await wrapper.vm.$nextTick()

    expect(liveTextAtFocus).toBe('')
    expect(live.text()).toBe('Removed Legacy from the database.')
    expect(document.activeElement).toBe(addSource)
    wrapper.unmount()
  })

  it('marks both header buttons as opening a dialog', async () => {
    const wrapper = mountPage([source({})])

    expect(
      wrapper.find('[data-testid="add-source-btn"]').attributes('aria-haspopup'),
    ).toBe('dialog')
    expect(
      wrapper.find('[data-testid="import-file-btn"]').attributes('aria-haspopup'),
    ).toBe('dialog')
    wrapper.unmount()
  })
})
