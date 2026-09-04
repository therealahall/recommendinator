import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises, type VueWrapper } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import ImportPanel from './ImportPanel.vue'
import type { ImportResponse } from '@/types/api'

const mockGet = vi.fn()
const mockUpload = vi.fn()

vi.mock('@/composables/useApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/composables/useApi')>()),
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
    upload: (...args: unknown[]) => mockUpload(...args),
  }),
}))

const IMPORTERS = [
  {
    name: 'goodreads_csv',
    display_name: 'Goodreads export',
    description: 'A Goodreads library export, as Goodreads writes it.',
    requires_content_type: false,
  },
  {
    name: 'csv_import',
    display_name: 'CSV',
    description: 'A generic CSV with the template columns.',
    requires_content_type: true,
  },
]

const TEMPLATES = [
  { importer: 'csv_import', content_type: 'book', filename: 'books.csv' },
  { importer: 'csv_import', content_type: 'video_game', filename: 'video_games.csv' },
]

const RESULT: ImportResponse = {
  importer: 'goodreads_csv',
  content_type: 'book',
  filename: 'goodreads_library_export.csv',
  added: 12,
  updated: 3,
  unchanged: 240,
  skipped: 2,
  failed: 0,
  total_rows: 257,
  errors: [
    'Skipped line 14: no title',
    'Skipped line 88: 6 fields short of the header',
  ],
  notes: [],
}

const SUMMARY =
  'Imported goodreads_library_export.csv: added 12, updated 3, ' +
  'unchanged 240, skipped 2, failed 0. 257 rows read. 2 rows did not import.'

function exportFile(): File {
  return new File(['title,author\n'], 'goodreads_library_export.csv', {
    type: 'text/csv',
  })
}

async function mountPanel() {
  const wrapper = mount(ImportPanel, { attachTo: document.body })
  await flushPromises()
  return wrapper
}

function disclosure(wrapper: VueWrapper) {
  return wrapper.get('button[aria-expanded]')
}

async function toggle(wrapper: VueWrapper): Promise<void> {
  await disclosure(wrapper).trigger('click')
  await flushPromises()
}

async function openPanel() {
  const opening = await mountPanel()
  await toggle(opening)
  return opening
}

async function chooseFile(wrapper: VueWrapper, file: File): Promise<void> {
  const input = wrapper.get('input[type="file"]')
  Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
  await input.trigger('change')
}

async function dropFile(wrapper: VueWrapper, file: File): Promise<void> {
  await wrapper.get('.drop-zone').trigger('drop', { dataTransfer: { files: [file] } })
}

function uploadedForm(): FormData {
  return mockUpload.mock.calls[0][1] as FormData
}

describe('ImportPanel', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockUpload.mockReset()
    mockGet.mockImplementation((path: string) => {
      if (path === '/importers') return Promise.resolve(IMPORTERS)
      if (path === '/import/templates') return Promise.resolve(TEMPLATES)
      return Promise.resolve({})
    })
    mockUpload.mockResolvedValue(RESULT)
  })

  it('mounts the live region empty, before an import gives it anything to say', async () => {
    const wrapper = await mountPanel()

    const region = wrapper.get('[data-testid="import-status"]')
    expect(region.attributes('aria-live')).toBe('polite')
    expect(region.text()).toBe('')
    wrapper.unmount()
  })

  it('keeps the live region announcing after the panel is collapsed', async () => {
    const wrapper = await openPanel()

    await chooseFile(wrapper, exportFile())
    await wrapper.get('[data-testid="import-submit"]').trigger('click')
    await flushPromises()
    await toggle(wrapper)

    const region = wrapper.get('[data-testid="import-status"]')
    expect(region.element.isConnected).toBe(true)
    expect(region.element.closest('[hidden]')).toBeNull()
    expect(region.text()).toBe(SUMMARY)
    wrapper.unmount()
  })

  it('announces the five counts through the live region it already mounted', async () => {
    const wrapper = await openPanel()

    await chooseFile(wrapper, exportFile())
    await wrapper.get('[data-testid="import-submit"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-testid="import-status"]').text()).toBe(SUMMARY)
    wrapper.unmount()
  })

  it('announces the misses without presuming they are lines', async () => {
    const wrapper = await openPanel()
    mockUpload.mockResolvedValue({
      ...RESULT,
      importer: 'json_import',
      skipped: 1,
      errors: ['Skipped entry 2: no title'],
    })

    await chooseFile(wrapper, exportFile())
    await wrapper.get('[data-testid="import-submit"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-testid="import-status"]').text()).toContain(
      '1 row did not import.',
    )
    wrapper.unmount()
  })

  it('announces the file in flight, so a repeat import is not silent', async () => {
    const wrapper = await openPanel()
    let settle: (value: ImportResponse) => void = () => {}
    mockUpload.mockReturnValue(
      new Promise<ImportResponse>((resolve) => {
        settle = resolve
      }),
    )

    await chooseFile(wrapper, exportFile())
    await wrapper.get('[data-testid="import-submit"]').trigger('click')
    await wrapper.vm.$nextTick()

    expect(wrapper.get('[data-testid="import-status"]').text()).toBe(
      'Importing goodreads_library_export.csv…',
    )
    settle(RESULT)
    await flushPromises()
    wrapper.unmount()
  })

  it('announces a dropped file, which no input value speaks', async () => {
    const wrapper = await openPanel()

    await dropFile(wrapper, exportFile())

    expect(wrapper.get('[data-testid="import-status"]').text()).toBe(
      'Selected file: goodreads_library_export.csv',
    )
    wrapper.unmount()
  })

  it('drops a failed import from the live region when a replacement file is picked', async () => {
    const wrapper = await openPanel()
    mockUpload.mockRejectedValue(new Error('CSV needs a content type.'))

    await chooseFile(wrapper, exportFile())
    await wrapper.get('[data-testid="import-submit"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="import-status"]').text()).toContain('failed')

    await chooseFile(wrapper, exportFile())

    expect(wrapper.get('[data-testid="import-status"]').text()).toBe('')
    wrapper.unmount()
  })

  it('keeps the format-load failure on screen after a file is picked', async () => {
    mockGet.mockImplementation((path: string) =>
      path === '/importers'
        ? Promise.reject(new Error('Service Unavailable'))
        : Promise.resolve([]),
    )
    const wrapper = await openPanel()

    await chooseFile(wrapper, exportFile())

    expect(wrapper.get('[data-testid="import-format-error"]').text()).toContain(
      'Failed to load import formats',
    )
    expect(wrapper.find('[data-testid="import-error"]').exists()).toBe(false)
    expect(
      wrapper.get('[data-testid="import-submit"]').attributes('disabled'),
    ).toBeDefined()
    wrapper.unmount()
  })

  it('drops the format-load failure once a reopen loads the formats', async () => {
    let attempts = 0
    mockGet.mockImplementation((path: string) => {
      if (path !== '/importers') return Promise.resolve(TEMPLATES)
      attempts += 1
      return attempts === 1
        ? Promise.reject(new Error('Service Unavailable'))
        : Promise.resolve(IMPORTERS)
    })
    const wrapper = await openPanel()
    expect(wrapper.find('[data-testid="import-format-error"]').exists()).toBe(true)

    await toggle(wrapper)
    await toggle(wrapper)
    expect(wrapper.get('[data-testid="import-status"]').text()).toBe('')

    await chooseFile(wrapper, exportFile())

    expect(
      wrapper.get('[data-testid="import-submit"]').attributes('disabled'),
    ).toBeUndefined()
    expect(wrapper.find('[data-testid="import-format-error"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('names whichever of the two conditions is refusing the Import button', async () => {
    const wrapper = await openPanel()
    const button = wrapper.get('[data-testid="import-submit"]')

    expect(button.attributes('aria-label')).toBe('Import — choose a file first')

    await chooseFile(wrapper, exportFile())

    expect(button.attributes('aria-label')).toBeUndefined()
    wrapper.unmount()

    mockGet.mockImplementation((path: string) =>
      path === '/importers'
        ? Promise.reject(new Error('Service Unavailable'))
        : Promise.resolve([]),
    )
    const unloaded = await openPanel()
    await chooseFile(unloaded, exportFile())

    expect(
      unloaded.get('[data-testid="import-submit"]').attributes('aria-label'),
    ).toBe('Import — import formats could not be loaded')
    unloaded.unmount()
  })

  it('counts the refused rows the file actually had, not the ones it lists', async () => {
    const wrapper = await openPanel()
    mockUpload.mockResolvedValue({
      ...RESULT,
      added: 0,
      updated: 0,
      unchanged: 0,
      skipped: 10000,
      total_rows: 10000,
      errors: ['Skipped line 2: no title', '… and 9999 more'],
    })

    await chooseFile(wrapper, exportFile())
    await wrapper.get('[data-testid="import-submit"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('.import-misses-title').text()).toContain('10000')
    expect(wrapper.get('[data-testid="import-status"]').text()).toContain(
      '10000 rows did not import.',
    )
    wrapper.unmount()
  })

  it('shows and announces a file-level note when every row imported', async () => {
    const wrapper = await openPanel()
    const note = 'Saved 255 item(s) but could not queue them for enrichment'
    mockUpload.mockResolvedValue({ ...RESULT, skipped: 0, errors: [], notes: [note] })

    await chooseFile(wrapper, exportFile())
    await wrapper.get('[data-testid="import-submit"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-testid="import-note"]').text()).toBe(note)
    expect(wrapper.get('[data-testid="import-status"]').text()).toContain(note)
    wrapper.unmount()
  })

  it('shows the same result whether the file was dropped or chosen from the input', async () => {
    const chosen = await openPanel()
    await chooseFile(chosen, exportFile())
    await chosen.get('[data-testid="import-submit"]').trigger('click')
    await flushPromises()

    const dropped = await openPanel()
    await dropFile(dropped, exportFile())
    await dropped.get('[data-testid="import-submit"]').trigger('click')
    await flushPromises()

    expect(dropped.get('[data-testid="import-result"]').text()).toBe(
      chosen.get('[data-testid="import-result"]').text(),
    )
    expect(dropped.get('[data-testid="import-status"]').text()).toBe(SUMMARY)
    chosen.unmount()
    dropped.unmount()
  })

  it('posts the file and the chosen format as one multipart upload', async () => {
    const wrapper = await openPanel()
    const file = exportFile()

    await chooseFile(wrapper, file)
    await wrapper.get('[data-testid="import-submit"]').trigger('click')
    await flushPromises()

    expect(mockUpload.mock.calls[0][0]).toBe('/import')
    expect(uploadedForm().get('file')).toBe(file)
    expect(uploadedForm().get('importer')).toBe('goodreads_csv')
    wrapper.unmount()
  })

  it('omits content_type for a format that decides its own', async () => {
    const wrapper = await openPanel()

    await chooseFile(wrapper, exportFile())
    await wrapper.get('[data-testid="import-submit"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('#import-content-type').exists()).toBe(false)
    expect(uploadedForm().has('content_type')).toBe(false)
    wrapper.unmount()
  })

  it('sends the picked content type for a format that needs one', async () => {
    const wrapper = await openPanel()

    await wrapper.get('#import-format').setValue('csv_import')
    await wrapper.get('#import-content-type').setValue('video_game')
    await chooseFile(wrapper, exportFile())
    await wrapper.get('[data-testid="import-submit"]').trigger('click')
    await flushPromises()

    expect(uploadedForm().get('importer')).toBe('csv_import')
    expect(uploadedForm().get('content_type')).toBe('video_game')
    wrapper.unmount()
  })

  it('links to the template for the format and content type currently chosen', async () => {
    const wrapper = await openPanel()

    expect(wrapper.find('[data-testid="import-template-link"]').exists()).toBe(false)

    await wrapper.get('#import-format').setValue('csv_import')
    const link = wrapper.get('[data-testid="import-template-link"]')
    expect(link.attributes('href')).toBe(
      '/api/import/templates/download?importer=csv_import&content_type=book',
    )
    expect(link.text()).toBe('Download the books.csv template')

    await wrapper.get('#import-content-type').setValue('video_game')
    expect(
      wrapper.get('[data-testid="import-template-link"]').attributes('href'),
    ).toBe('/api/import/templates/download?importer=csv_import&content_type=video_game')
    wrapper.unmount()
  })

  it('keeps the Import button focused while the upload is in flight', async () => {
    const wrapper = await openPanel()
    let settle: (value: ImportResponse) => void = () => {}
    mockUpload.mockReturnValue(
      new Promise<ImportResponse>((resolve) => {
        settle = resolve
      }),
    )

    await chooseFile(wrapper, exportFile())
    const button = wrapper.get('[data-testid="import-submit"]')
    ;(button.element as HTMLButtonElement).focus()
    await button.trigger('click')
    await wrapper.vm.$nextTick()

    expect(button.attributes('disabled')).toBeUndefined()
    expect(button.attributes('aria-disabled')).toBe('true')
    expect(document.activeElement).toBe(button.element)

    await button.trigger('click')
    expect(mockUpload).toHaveBeenCalledTimes(1)
    settle(RESULT)
    await flushPromises()
    wrapper.unmount()
  })

  it('does not reload the sync source list after an import', async () => {
    const wrapper = await openPanel()

    await chooseFile(wrapper, exportFile())
    await wrapper.get('[data-testid="import-submit"]').trigger('click')
    await flushPromises()

    expect(mockGet.mock.calls.map(([path]) => path)).toEqual([
      '/importers',
      '/import/templates',
    ])
    wrapper.unmount()
  })

  it('shows the refusal message when the server rejects the file', async () => {
    const wrapper = await openPanel()
    mockUpload.mockRejectedValue(new Error('CSV needs a content type.'))

    await chooseFile(wrapper, exportFile())
    await wrapper.get('[data-testid="import-submit"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-testid="import-error"]').text()).toBe(
      'CSV needs a content type.',
    )
    expect(wrapper.get('[data-testid="import-status"]').text()).toBe(
      'Import failed. CSV needs a content type.',
    )
    expect(wrapper.find('[data-testid="import-result"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('still imports when the template listing is unavailable', async () => {
    mockGet.mockImplementation((path: string) => {
      if (path === '/importers') return Promise.resolve(IMPORTERS)
      return Promise.reject(new Error('No import templates directory'))
    })
    const wrapper = await openPanel()

    await chooseFile(wrapper, exportFile())
    await wrapper.get('[data-testid="import-submit"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="import-template-link"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="import-result"]').text()).toContain('240')
    wrapper.unmount()
  })
})
