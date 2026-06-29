import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import ImportFileModal from './ImportFileModal.vue'
import { useDataStore } from '@/stores/data'
import { ApiError } from '@/composables/useApi'
import type { ImportResultResponse, ImportSourceResponse } from '@/types/api'

const csvSource: ImportSourceResponse = {
  name: 'csv_import',
  display_name: 'CSV Import',
  description: 'Import a generic CSV file.',
  content_types: ['book', 'movie', 'tv_show', 'video_game'],
  fields: [
    {
      name: 'content_type',
      field_type: 'str',
      required: true,
      default: 'book',
      description: 'Content type for the imported rows.',
      sensitive: false,
    },
  ],
}

const goodreadsSource: ImportSourceResponse = {
  name: 'goodreads',
  display_name: 'Goodreads',
  description: 'Import a Goodreads CSV export.',
  content_types: ['book'],
  fields: [],
}

const jsonSource: ImportSourceResponse = {
  name: 'json_import',
  display_name: 'JSON Import',
  description: 'Import a generic JSON file.',
  content_types: ['book', 'movie'],
  fields: [
    {
      name: 'content_type',
      field_type: 'str',
      required: true,
      default: 'book',
      description: 'Content type for the imported items.',
      sensitive: false,
    },
  ],
}

function importResult(
  overrides: Partial<ImportResultResponse> = {},
): ImportResultResponse {
  return {
    message: 'Imported 2 item(s) from CSV Import.',
    source: 'Import: CSV Import',
    items_synced: 2,
    total_items: 2,
    errors: [],
    ...overrides,
  }
}

// The component loads import sources in onMounted, so the store must be primed
// (and any runImport spy installed) BEFORE mounting.
async function setup(sources: ImportSourceResponse[]) {
  const store = useDataStore()
  const load = vi
    .spyOn(store, 'loadImportSources')
    .mockImplementation(async () => {
      store.importSources = sources
      return sources
    })
  const wrapper = mount(ImportFileModal, { attachTo: document.body })
  await flushPromises()
  return { wrapper, store, load }
}

function setFile(
  wrapper: ReturnType<typeof mount>,
  name = 'books.csv',
  type = 'text/csv',
): File {
  const input = wrapper.find('#import-file').element as HTMLInputElement
  const file = new File(['title,author\nDune,Herbert'], name, { type })
  Object.defineProperty(input, 'files', { value: [file], configurable: true })
  wrapper.find('#import-file').trigger('change')
  return file
}

describe('ImportFileModal', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('loads import sources on mount and renders them as options', async () => {
    const { wrapper, load } = await setup([csvSource, goodreadsSource])

    expect(load).toHaveBeenCalledTimes(1)
    const options = wrapper.findAll('#import-source option')
    expect(options).toHaveLength(2)
    expect(options[0].text()).toBe('CSV Import')
    expect(options[1].text()).toBe('Goodreads')
    wrapper.unmount()
  })

  it('renders a content_type select for a generic format, intersected with content_types', async () => {
    const { wrapper } = await setup([jsonSource])

    const select = wrapper.find('#import-field-content_type')
    expect(select.exists()).toBe(true)
    const labels = select.findAll('option').map((o) => o.text())
    // jsonSource only allows book + movie — tv_show / game must not appear.
    expect(labels).toEqual(['Book', 'Movie'])
    wrapper.unmount()
  })

  it('renders no option field for a source with an empty schema (Goodreads)', async () => {
    const { wrapper } = await setup([goodreadsSource])

    expect(wrapper.find('#import-field-content_type').exists()).toBe(false)
    wrapper.unmount()
  })

  it('keeps Import disabled until a file is chosen and required fields are filled', async () => {
    const { wrapper } = await setup([csvSource])

    const submit = wrapper.find('[data-testid="import-submit"]')
    expect(submit.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('Choose a file to import.')

    setFile(wrapper)
    await flushPromises()

    // content_type defaults to 'book', so once the file is chosen the
    // required field is already satisfied and Import enables.
    expect(submit.attributes('disabled')).toBeUndefined()
    wrapper.unmount()
  })

  it('disables Import while another job is running', async () => {
    const { wrapper, store } = await setup([csvSource])
    setFile(wrapper)
    store.syncStatus = 'running'
    await flushPromises()

    const submit = wrapper.find('[data-testid="import-submit"]')
    expect(submit.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('Wait for the running job to finish')
    wrapper.unmount()
  })

  it('submits the selected source, file, and option values to runImport', async () => {
    const { wrapper, store } = await setup([csvSource])
    const run = vi.spyOn(store, 'runImport').mockResolvedValue(importResult())

    const file = setFile(wrapper)
    await flushPromises()
    await wrapper.find('[data-testid="import-submit"]').trigger('click')
    await flushPromises()

    expect(run).toHaveBeenCalledTimes(1)
    expect(run.mock.calls[0][0]).toBe('csv_import')
    expect(run.mock.calls[0][1]).toBe(file)
    expect(run.mock.calls[0][2]).toEqual({ content_type: 'book' })
    wrapper.unmount()
  })

  it('shows the success banner with counts and a Done button after a successful import', async () => {
    const { wrapper, store } = await setup([csvSource])
    vi.spyOn(store, 'runImport').mockResolvedValue(
      importResult({ items_synced: 5, total_items: 6 }),
    )
    setFile(wrapper)
    await flushPromises()
    await wrapper.find('[data-testid="import-submit"]').trigger('click')
    await flushPromises()

    const banner = wrapper.find('.sync-status-success')
    expect(banner.exists()).toBe(true)
    expect(banner.text()).toContain('Imported 5 of 6 items.')
    expect(wrapper.find('[data-testid="import-done"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="import-submit"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('renders per-item errors in a disclosure when the import skips rows', async () => {
    const { wrapper, store } = await setup([csvSource])
    vi.spyOn(store, 'runImport').mockResolvedValue(
      importResult({
        items_synced: 1,
        total_items: 3,
        errors: ['Row 2: missing title', 'Row 3: bad rating'],
      }),
    )
    setFile(wrapper)
    await flushPromises()
    await wrapper.find('[data-testid="import-submit"]').trigger('click')
    await flushPromises()

    const details = wrapper.find('details.import-modal-errors')
    expect(details.exists()).toBe(true)
    expect(details.find('summary').text()).toBe('2 rows skipped')
    const items = details.findAll('li')
    expect(items).toHaveLength(2)
    expect(items[0].text()).toBe('Row 2: missing title')
    wrapper.unmount()
  })

  it.each([
    [422, "That import source isn't available."],
    [400, "We couldn't read that file."],
    [409, 'An import or sync is already running.'],
  ])('maps a %i error to its banner message', async (status, expected) => {
    const { wrapper, store } = await setup([csvSource])
    vi.spyOn(store, 'runImport').mockRejectedValue(new ApiError(status, 'err'))
    setFile(wrapper)
    await flushPromises()
    await wrapper.find('[data-testid="import-submit"]').trigger('click')
    await flushPromises()

    const banner = wrapper.find('.sync-status-error')
    expect(banner.exists()).toBe(true)
    expect(banner.text()).toContain(expected)
    expect(banner.attributes('role')).toBe('alert')
    expect(banner.attributes('aria-live')).toBe('assertive')
    // A 400 must keep the form populated so the user can retry.
    expect(wrapper.find('#import-file').exists()).toBe(true)
    wrapper.unmount()
  })

  it('surfaces a load failure in the result banner', async () => {
    const store = useDataStore()
    vi.spyOn(store, 'loadImportSources').mockRejectedValue(
      new Error('network down'),
    )
    const wrapper = mount(ImportFileModal, { attachTo: document.body })
    await flushPromises()

    const banner = wrapper.find('.sync-status-error')
    expect(banner.exists()).toBe(true)
    expect(banner.text()).toContain("Couldn't load import sources")
    wrapper.unmount()
  })

  it('wires the dialog role, labelledby, and labelled controls for a11y', async () => {
    const { wrapper } = await setup([csvSource])

    const dialog = wrapper.find('[role="dialog"]')
    expect(dialog.attributes('aria-modal')).toBe('true')
    expect(dialog.attributes('aria-labelledby')).toBe('import-modal-title')
    expect(wrapper.find('#import-modal-title').text()).toBe('Import from file')
    expect(wrapper.find('label[for="import-source"]').exists()).toBe(true)
    expect(wrapper.find('label[for="import-file"]').exists()).toBe(true)
    expect(
      wrapper.find('label[for="import-field-content_type"]').exists(),
    ).toBe(true)
    wrapper.unmount()
  })

  it('Escape key emits close', async () => {
    const { wrapper } = await setup([csvSource])

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    expect(wrapper.emitted('close')).toBeTruthy()
    wrapper.unmount()
  })

  it('backdrop click emits close', async () => {
    const { wrapper } = await setup([csvSource])

    await wrapper.find('.import-modal').trigger('click')
    expect(wrapper.emitted('close')).toBeTruthy()
    wrapper.unmount()
  })
})
