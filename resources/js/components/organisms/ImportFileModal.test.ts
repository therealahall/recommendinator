import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import ImportFileModal from './ImportFileModal.vue'
import { useDataStore } from '@/stores/data'
import { ApiError } from '@/composables/useApi'
import { MAX_UPLOAD_BYTES, MAX_UPLOAD_MB } from '@/constants/upload'
import type {
  ImportResultResponse,
  ImportSourceResponse,
  SyncJobResponse,
} from '@/types/api'

const csvSource: ImportSourceResponse = {
  name: 'csv_import',
  display_name: 'CSV Import',
  description: 'Import a generic CSV file.',
  content_types: ['book', 'movie', 'tv_show', 'video_game'],
  accepted_extensions: ['.csv'],
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

// Mirrors what GET /api/import/sources actually returns for the plugin: the
// id is `goodreads_csv` (the CSV half of the goodreads split) and the display
// name is `Goodreads (CSV Export)`.
const goodreadsSource: ImportSourceResponse = {
  name: 'goodreads_csv',
  display_name: 'Goodreads (CSV Export)',
  description: 'Import books from a Goodreads library CSV export',
  content_types: ['book'],
  accepted_extensions: ['.csv'],
  fields: [],
}

const jsonSource: ImportSourceResponse = {
  name: 'json_import',
  display_name: 'JSON Import',
  description: 'Import a generic JSON file.',
  content_types: ['book', 'movie'],
  accepted_extensions: ['.json', '.jsonl'],
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
    // `source` is the plugin name the caller supplied, NOT the internal
    // "Import: <display name>" job label that appears in sync status.
    source: 'csv_import',
    items_synced: 2,
    total_items: 2,
    errors: [],
    warning: null,
    ...overrides,
  }
}

// The accepted extensions now arrive on the payload, so this is a list of
// shapes the picker has to render, not a list of plugin names to recognise.
const EXTENSION_CASES: [string[], string, string][] = [
  [['.csv'], '.csv', 'Accepted file type: .csv'],
  [['.json', '.jsonl'], '.json,.jsonl', 'Accepted file types: .json, .jsonl'],
  [['.md', '.markdown'], '.md,.markdown', 'Accepted file types: .md, .markdown'],
  // A format nobody has written yet. The old name-substring heuristic gave
  // this ".csv" and "Accepted file type: .csv"; the payload is now the source
  // of truth, so it gets what the server declared.
  [['.opml'], '.opml', 'Accepted file type: .opml'],
]

// The wording belongs to the server (import_service.NO_ITEMS_WARNING); the
// modal renders whatever it is handed, so the test only needs a stand-in.
const NO_ITEMS_WARNING = 'No items were found in the file.'

// A running job for the CSV source, keyed by the "Import: <display name>"
// label the modal looks up in the store.
function runningJob(overrides: Partial<SyncJobResponse> = {}): SyncJobResponse {
  return {
    source: 'Import: CSV Import',
    status: 'running',
    started_at: null,
    completed_at: null,
    items_processed: 45,
    total_items: 100,
    current_item: 'Dune',
    current_source: null,
    error_message: null,
    progress_percent: 45,
    error_count: 0,
    errors: [],
    sources: [],
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

// Holds runImport open so assertions can run against the in-flight state,
// which is where the focus trap used to go inert.
function deferImport(store: ReturnType<typeof useDataStore>) {
  let resolve!: (value: ImportResultResponse) => void
  let reject!: (reason: unknown) => void
  const pending = new Promise<ImportResultResponse>((res, rej) => {
    resolve = res
    reject = rej
  })
  vi.spyOn(store, 'runImport').mockReturnValue(pending)
  return { resolve, reject }
}

function setFile(
  wrapper: ReturnType<typeof mount>,
  name = 'books.csv',
  type = 'text/csv',
  size?: number,
): File {
  const input = wrapper.find('#import-file').element as HTMLInputElement
  const file = new File(['title,author\nDune,Herbert'], name, { type })
  if (size !== undefined) {
    // jsdom derives File.size from the content; overriding it exercises the
    // size gate without materialising tens of megabytes in the test.
    Object.defineProperty(file, 'size', { value: size })
  }
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
    expect(options[1].text()).toBe('Goodreads (CSV Export)')
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

  // Regression: the picker used to infer the format by substring-matching the
  // plugin name (`includes('json')`, `includes('markdown')`, else CSV), which
  // was right for today's five importers and silently wrong for the sixth —
  // a future `opml_import` would have been offered `accept=".csv"` and told
  // the user to upload a CSV. `accepted_extensions` on the payload replaces it.
  it.each(EXTENSION_CASES)(
    'renders the picker from accepted_extensions %j',
    async (extensions, accept, helpText) => {
      const source: ImportSourceResponse = {
        name: 'some_import',
        display_name: 'Some Import',
        description: '',
        content_types: ['book'],
        accepted_extensions: extensions,
        fields: [],
      }
      const { wrapper } = await setup([source])

      expect(wrapper.find('#import-file').attributes('accept')).toBe(accept)
      expect(wrapper.find('#import-file-accepted').text()).toBe(helpText)
      // The hint only reaches a screen reader through this association; a
      // visible-but-unreferenced paragraph is silent on focus (WCAG 3.3.2).
      expect(wrapper.find('#import-file').attributes('aria-describedby')).toBe(
        'import-file-accepted',
      )
      wrapper.unmount()
    },
  )

  it('associates an option field with its description', async () => {
    // Regression: `field.description` was rendered but wired to nothing, so it
    // was visible to sighted users and silent on focus (WCAG 3.3.2 / 1.3.1).
    const { wrapper } = await setup([csvSource])

    const control = wrapper.find('#import-field-content_type')
    expect(control.attributes('aria-describedby')).toBe(
      'import-field-content_type-desc',
    )
    expect(wrapper.find('#import-field-content_type-desc').text()).toBe(
      'Content type for the imported rows.',
    )
    wrapper.unmount()
  })

  it('resets the file control when the source changes', async () => {
    // Regression: switching source cleared the `file` ref but left the native
    // input showing the old filename, so the control said "books.csv" while
    // the hint below said "Choose a file to import" — and re-picking the same
    // file fired no `change` event at all.
    const { wrapper } = await setup([csvSource, jsonSource])
    setFile(wrapper)
    await flushPromises()
    expect(wrapper.text()).toContain('Selected file: books.csv')

    await wrapper.find('#import-source').setValue('json_import')
    await flushPromises()

    const input = wrapper.find('#import-file').element as HTMLInputElement
    expect(input.files?.length ?? 0).toBe(0)
    expect(wrapper.text()).not.toContain('Selected file:')
    expect(wrapper.text()).toContain('Choose a file to import.')
    wrapper.unmount()
  })

  it('keeps Import disabled until a file is chosen and required fields are filled', async () => {
    const { wrapper } = await setup([csvSource])

    const submit = wrapper.find('[data-testid="import-submit"]')
    expect(submit.attributes('aria-disabled')).toBe('true')
    expect(submit.attributes('aria-describedby')).toBe('import-disabled-reason')
    expect(wrapper.text()).toContain('Choose a file to import.')

    setFile(wrapper)
    await flushPromises()

    // content_type defaults to 'book', so once the file is chosen the
    // required field is already satisfied and Import enables.
    expect(submit.attributes('aria-disabled')).toBe('false')
    wrapper.unmount()
  })

  it('disables Import while another job is running', async () => {
    const { wrapper, store } = await setup([csvSource])
    setFile(wrapper)
    store.syncStatus = 'running'
    await flushPromises()

    const submit = wrapper.find('[data-testid="import-submit"]')
    expect(submit.attributes('aria-disabled')).toBe('true')
    expect(wrapper.text()).toContain('Wait for the running job to finish')
    wrapper.unmount()
  })

  it('rejects a file over the cap without uploading it', async () => {
    const { wrapper, store } = await setup([csvSource])
    const run = vi.spyOn(store, 'runImport')

    setFile(wrapper, 'huge.csv', 'text/csv', MAX_UPLOAD_BYTES + 1)
    await flushPromises()

    const banner = wrapper.find('.sync-status-error')
    expect(banner.text()).toContain(`larger than the ${MAX_UPLOAD_MB} MB limit`)
    // The size message rides the same fixed alert node as every other error.
    expect(banner.attributes('role')).toBe('alert')
    expect(
      wrapper.find('[data-testid="import-submit"]').attributes('aria-disabled'),
    ).toBe('true')
    // aria-disabled leaves the button clickable, so submit() itself has to
    // refuse the oversized file rather than relying on the DOM to block it.
    await wrapper.find('[data-testid="import-submit"]').trigger('click')
    await flushPromises()
    expect(run).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('allows a file of exactly the cap and clears the size error on reselect', async () => {
    // The server rejects on `total > MAX_UPLOAD_BYTES`, so the boundary value
    // must pass the client check too or the two layers would disagree.
    const { wrapper } = await setup([csvSource])

    setFile(wrapper, 'huge.csv', 'text/csv', MAX_UPLOAD_BYTES + 1)
    await flushPromises()
    setFile(wrapper, 'books.csv', 'text/csv', MAX_UPLOAD_BYTES)
    await flushPromises()

    expect(wrapper.find('.sync-status-error').isVisible()).toBe(false)
    const submit = wrapper.find('[data-testid="import-submit"]')
    expect(submit.attributes('aria-disabled')).toBe('false')
    // With the error cleared there is nothing left to describe the button with.
    expect(submit.attributes('aria-describedby')).toBeUndefined()
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
    // Success/progress live on a fixed polite-status node, separate from errors.
    expect(banner.attributes('role')).toBe('status')
    expect(banner.attributes('aria-live')).toBe('polite')
    expect(wrapper.find('[data-testid="import-done"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="import-submit"]').exists()).toBe(false)
    // An import that produced items shows no warning styling or text.
    expect(wrapper.find('.sync-status-warning').exists()).toBe(false)
    expect(wrapper.find('[data-testid="import-warning"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('shows the warning in the polite status banner when the import yielded no items', async () => {
    // Zero items is still a success, so the warning must ride the fixed
    // role="status" node rather than flipping it (or any node) to role="alert".
    const { wrapper, store } = await setup([csvSource])
    vi.spyOn(store, 'runImport').mockResolvedValue(
      importResult({
        message: 'Imported 0 item(s) from CSV Import.',
        items_synced: 0,
        total_items: 0,
        warning: NO_ITEMS_WARNING,
      }),
    )
    setFile(wrapper)
    await flushPromises()
    await wrapper.find('[data-testid="import-submit"]').trigger('click')
    await flushPromises()

    const banner = wrapper.find('[data-testid="import-status"]')
    expect(banner.attributes('role')).toBe('status')
    expect(banner.attributes('aria-live')).toBe('polite')
    expect(banner.text()).toContain('Imported 0 of 0 items.')
    expect(banner.find('[data-testid="import-warning"]').text()).toBe(
      NO_ITEMS_WARNING,
    )
    // Warned results are styled as a warning, never as a plain success.
    expect(banner.classes()).toContain('sync-status-warning')
    expect(banner.classes()).not.toContain('sync-status-success')
    expect(wrapper.find('.sync-status-error').isVisible()).toBe(false)
    expect(wrapper.find('[data-testid="import-done"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('moves focus to the Done button after a successful import', async () => {
    // The Import button is removed when the result arrives; focus must follow
    // to Done so keyboard users stay inside the trap rather than dropping to
    // <body> (WCAG 2.4.3).
    const { wrapper, store } = await setup([csvSource])
    vi.spyOn(store, 'runImport').mockResolvedValue(importResult())
    setFile(wrapper)
    await flushPromises()
    await wrapper.find('[data-testid="import-submit"]').trigger('click')
    await flushPromises()

    const done = wrapper.find('[data-testid="import-done"]').element
    expect(document.activeElement).toBe(done)
    wrapper.unmount()
  })

  it('keeps focus and a populated focus trap while the import is in flight', async () => {
    // Regression: every control carried :disabled="submitting", so activating
    // Import emptied the dialog of focusable elements. The browser blurred the
    // just-clicked button to <body> and the trap — which bailed out when it
    // found nothing — let Tab escape to the page behind an aria-modal dialog
    // for the whole upload (WCAG 2.4.3).
    const { wrapper, store } = await setup([csvSource])
    deferImport(store)
    setFile(wrapper)
    await flushPromises()

    const submit = wrapper.find('[data-testid="import-submit"]')
    ;(submit.element as HTMLButtonElement).focus()
    await submit.trigger('click')
    await flushPromises()

    const dialog = wrapper.find('[role="dialog"]').element
    expect(dialog.contains(document.activeElement)).toBe(true)
    // Import stays in the tab order and conveys its state with aria-disabled,
    // so the trap never runs dry and its description stays reachable.
    expect(submit.attributes('disabled')).toBeUndefined()
    expect(submit.attributes('aria-disabled')).toBe('true')
    expect(
      wrapper.find('.btn-secondary').attributes('disabled'),
    ).toBeUndefined()
    wrapper.unmount()
  })

  it('marks only the progress region busy while the import runs', async () => {
    // Regression: aria-busy sat on the dialog, an ancestor of both live
    // regions. AT may ignore updates to a busy element AND its descendants, so
    // the "Importing…" polite update — which lands in the same DOM flush that
    // sets aria-busy — could be swallowed for the whole upload, leaving a
    // screen reader user with no signal the import started (WCAG 4.1.3).
    const { wrapper, store } = await setup([csvSource])
    const settle = deferImport(store)
    setFile(wrapper)
    await flushPromises()

    const dialog = wrapper.find('[role="dialog"]')
    const region = wrapper.find('.import-modal-progress-region')
    expect(region.attributes('aria-busy')).toBe('false')

    await wrapper.find('[data-testid="import-submit"]').trigger('click')
    await flushPromises()
    expect(region.attributes('aria-busy')).toBe('true')
    // The status banner announcing "Importing…" must not sit inside anything
    // marked busy.
    expect(dialog.attributes('aria-busy')).toBeUndefined()
    const status = wrapper.find('[data-testid="import-status"]')
    expect(status.text()).toBe('Importing…')
    expect(region.element.contains(status.element)).toBe(false)

    settle.resolve(importResult())
    await flushPromises()
    expect(region.attributes('aria-busy')).toBe('false')
    wrapper.unmount()
  })

  it('does not re-announce fine-grained progress on every poll', async () => {
    // Regression: the progress counts sat in an aria-live="polite" region that
    // the 2s sync poll rewrote, queueing an announcement every couple of
    // seconds and burying the eventual result behind the backlog. The
    // progressbar carries the value for on-demand reading instead (WCAG 4.1.3).
    const { wrapper, store } = await setup([csvSource])
    deferImport(store)
    store.syncJobs = [runningJob()]
    setFile(wrapper)
    await flushPromises()
    await wrapper.find('[data-testid="import-submit"]').trigger('click')
    await flushPromises()

    const region = wrapper.find('.import-modal-progress-region')
    expect(region.isVisible()).toBe(true)
    expect(region.attributes('aria-live')).toBeUndefined()
    expect(region.text()).toContain('45/100')

    const bar = wrapper.find('[role="progressbar"]')
    expect(bar.attributes('aria-valuenow')).toBe('45')
    // A static name plus aria-valuetext — the old aria-label baked the
    // percentage into the name, reading it twice and going stale.
    expect(bar.attributes('aria-label')).toBe('Import progress')
    expect(bar.attributes('aria-valuetext')).toBe('45% complete')
    wrapper.unmount()
  })

  it('restores focus into the dialog after a failed import', async () => {
    // Regression: the failure path leaves `result` null, so the watcher that
    // moves focus to Done never fired. Focus stayed wherever the submit
    // transition dropped it and the user had to re-traverse the page to reach
    // Retry after hearing the error (WCAG 2.4.3).
    const { wrapper, store } = await setup([csvSource])
    const settle = deferImport(store)
    setFile(wrapper)
    await flushPromises()
    await wrapper.find('[data-testid="import-submit"]').trigger('click')
    await flushPromises()

    const stray = document.createElement('button')
    document.body.appendChild(stray)
    stray.focus()
    expect(document.activeElement).toBe(stray)

    settle.reject(new ApiError(400, 'bad file'))
    await flushPromises()

    expect(document.activeElement).toBe(
      wrapper.find('[data-testid="import-submit"]').element,
    )
    stray.remove()
    wrapper.unmount()
  })

  it('describes the Import button with the oversize error', async () => {
    // Regression: an oversized file makes canSubmit false but disabledReason
    // empty, so aria-describedby vanished and the only explanation was a
    // one-shot alert the user could never get back to (WCAG 3.3.1).
    const { wrapper } = await setup([csvSource])

    setFile(wrapper, 'huge.csv', 'text/csv', MAX_UPLOAD_BYTES + 1)
    await flushPromises()

    const describedBy = wrapper
      .find('[data-testid="import-submit"]')
      .attributes('aria-describedby')
    expect(describedBy).toBe('import-error')
    expect(wrapper.find('.sync-status-error').attributes('id')).toBe(
      'import-error',
    )
    wrapper.unmount()
  })

  it('drops the stale running-job hint once an error is showing', async () => {
    // Regression: syncStatus can still read 'running' for up to one 2s poll
    // after a failure, so "Wait for the running job to finish" appeared beside
    // a fresh error and contradicted it.
    const { wrapper, store } = await setup([csvSource])
    vi.spyOn(store, 'runImport').mockRejectedValue(new ApiError(400, 'err'))
    setFile(wrapper)
    await flushPromises()
    await wrapper.find('[data-testid="import-submit"]').trigger('click')
    store.syncStatus = 'running'
    await flushPromises()

    expect(wrapper.find('.sync-status-error').text()).toContain(
      "We couldn't read that file.",
    )
    expect(wrapper.text()).not.toContain('Wait for the running job to finish')
    wrapper.unmount()
  })

  it('labels option fields with a humanized name, not the raw schema key', async () => {
    const { wrapper } = await setup([csvSource])

    const label = wrapper.find('label[for="import-field-content_type"]')
    expect(label.text()).toContain('Content Type')
    expect(label.text()).not.toContain('content_type')
    // The raw key still drives the id/for pairing and the request payload.
    expect(wrapper.find('#import-field-content_type').exists()).toBe(true)
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

  // Every status POST /api/import can answer with, and the copy it must
  // produce. A rejection missing from this table falls through to the generic
  // "please try again", which is wrong advice for anything the user can act
  // on — hence the row per status rather than a spot check.
  it.each([
    [422, "That import source isn't available."],
    [400, "We couldn't read that file."],
    // The server guard keys on the plugin's import label, so it only rejects a
    // second import of the SAME source — the copy must not promise more.
    [409, 'An import from this source is already running.'],
    [413, `larger than the ${MAX_UPLOAD_MB} MB limit`],
    // The concurrency cap in RequestBodySizeLimitMiddleware. Retrying is the
    // right advice here, but only after something else finishes.
    [429, 'Too many imports are already running.'],
    // _require_storage / _require_config: retrying changes nothing until the
    // server itself is fixed, so the copy must point at the server, not at a
    // retry.
    [503, "the server's storage or configuration didn't load"],
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

  it('prefers the server detail on a 400 over the canned file copy', async () => {
    // A rejected import option is the one failure only the server can name,
    // and the canned 400 copy misdiagnoses it: it blames the file, when the
    // file is fine and an option value is not.
    const detail =
      "Unknown import option(s) for 'csv_import': shelf. This source accepts: content_type."
    const { wrapper, store } = await setup([csvSource])
    vi.spyOn(store, 'runImport').mockRejectedValue(
      new ApiError(400, 'Bad Request', { detail }),
    )
    setFile(wrapper)
    await flushPromises()
    await wrapper.find('[data-testid="import-submit"]').trigger('click')
    await flushPromises()

    const banner = wrapper.find('.sync-status-error')
    expect(banner.text()).toContain(detail)
    expect(banner.text()).not.toContain("We couldn't read that file")
    wrapper.unmount()
  })

  it('ignores a non-string detail and falls back to the canned copy', async () => {
    // FastAPI's own request validation sends `detail` as an array of error
    // objects. That is diagnostic output, not user-facing copy — rendering it
    // would put "[object Object]" in the banner.
    const { wrapper, store } = await setup([csvSource])
    vi.spyOn(store, 'runImport').mockRejectedValue(
      new ApiError(400, 'Bad Request', {
        detail: [{ loc: ['body', 'source'], msg: 'Field required' }],
      }),
    )
    setFile(wrapper)
    await flushPromises()
    await wrapper.find('[data-testid="import-submit"]').trigger('click')
    await flushPromises()

    const banner = wrapper.find('.sync-status-error')
    expect(banner.text()).toContain("We couldn't read that file")
    expect(banner.text()).not.toContain('object Object')
    wrapper.unmount()
  })

  it('shows a generic error banner when runImport throws a non-ApiError', async () => {
    const { wrapper, store } = await setup([csvSource])
    vi.spyOn(store, 'runImport').mockRejectedValue(new Error('boom'))
    setFile(wrapper)
    await flushPromises()
    await wrapper.find('[data-testid="import-submit"]').trigger('click')
    await flushPromises()

    const banner = wrapper.find('.sync-status-error')
    expect(banner.exists()).toBe(true)
    expect(banner.text()).toContain('Something went wrong during the import.')
    // The error lives on its own fixed-role node so AT re-announces it.
    expect(banner.attributes('role')).toBe('alert')
    expect(banner.attributes('aria-live')).toBe('assertive')
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

  it('routes a load failure through the status table instead of the status line', async () => {
    // Regression: the mount-time catch interpolated `err.message`, so a 503
    // rendered "Couldn't load import sources: 503 Service Unavailable" into
    // the assertive alert — a status code in the user's face, beside a table
    // built precisely so that never happens (WCAG 3.3.1).
    const store = useDataStore()
    vi.spyOn(store, 'loadImportSources').mockRejectedValue(
      new ApiError(503, 'Service Unavailable'),
    )
    const wrapper = mount(ImportFileModal, { attachTo: document.body })
    await flushPromises()

    const banner = wrapper.find('.sync-status-error')
    expect(banner.text()).toContain("Couldn't load import sources.")
    expect(banner.text()).toContain("the server's storage or configuration")
    expect(banner.text()).not.toContain('503')
    wrapper.unmount()
  })

  it('keeps Import disabled and explains itself when no import sources loaded', async () => {
    // Regression: with an empty list `sourceName` stays '' and `requiredFilled`
    // is vacuously true, so choosing a file left Import looking enabled while
    // submit() returned immediately — no banner, no reason, nothing.
    const { wrapper } = await setup([])

    setFile(wrapper)
    await flushPromises()

    const submit = wrapper.find('[data-testid="import-submit"]')
    expect(submit.attributes('aria-disabled')).toBe('true')
    expect(submit.attributes('aria-describedby')).toBe('import-disabled-reason')
    expect(wrapper.text()).toContain('No import sources are available.')
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
    expect(wrapper.emitted('close')).toHaveLength(1)
    wrapper.unmount()
  })

  it('backdrop click emits close', async () => {
    const { wrapper } = await setup([csvSource])

    await wrapper.find('.import-modal').trigger('click')
    expect(wrapper.emitted('close')).toHaveLength(1)
    wrapper.unmount()
  })
})
