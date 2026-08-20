import { describe, it, expect, vi, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { pairKey, useDuplicatesStore } from './duplicates'
import { ApiError } from '@/composables/useApi'
import type { DuplicateSuggestion, MergeRecord } from '@/types/api'

const mockGet = vi.fn()
const mockPost = vi.fn()
const mockDelete = vi.fn()

// Only the transport is faked. ApiError is the real class so the store meets
// the message the app actually gets, including the server's ``detail``.
vi.mock('@/composables/useApi', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/composables/useApi')>()),
  useApi: () => ({
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
    put: vi.fn(),
    patch: vi.fn(),
    delete: (...args: unknown[]) => mockDelete(...args),
  }),
}))

function suggestion(survivorId: number, absorbedId: number): DuplicateSuggestion {
  const side = (db_id: number) => ({
    db_id,
    title: `Row ${db_id}`,
    source: 'calibre',
    creator: null,
    release_year: null,
  })
  return {
    content_type: 'book',
    evidence: 'normalized_title',
    evidence_detail: 'row',
    survivor: side(survivorId),
    absorbed: side(absorbedId),
  }
}

function merge(id: number, survivorId: number, absorbedId: number): MergeRecord {
  return {
    id,
    survivor_id: survivorId,
    survivor_title: `Row ${survivorId}`,
    absorbed_id: absorbedId,
    absorbed_title: `Row ${absorbedId}`,
    evidence: 'manual',
    evidence_detail: null,
    merged_at: '2026-08-20 00:00:00',
  }
}

function pageOf(...pairs: DuplicateSuggestion[]) {
  return { total: pairs.length, suggestions: pairs }
}

describe('useDuplicatesStore', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    mockGet.mockReset()
    mockPost.mockReset()
    mockDelete.mockReset()
  })

  it('keys a pair the same way round either way, so one decision is one key', () => {
    expect(pairKey(9, 4)).toBe(pairKey(4, 9))
    expect(pairKey(4, 9)).not.toBe(pairKey(4, 10))
  })

  it('blocks the undo of a merge a later one into the same survivor sits on', () => {
    // The server refuses that order, so offering the button loses the click.
    const store = useDuplicatesStore()
    store.merges = [merge(1, 10, 11), merge(2, 10, 12)]

    const [newest, older] = store.mergeRows

    expect(newest.record.id).toBe(2)
    expect(newest.blocked).toBe('')
    expect(older.blocked).toContain('Undo merge 2 first')
  })

  it('blocks the undo of a merge whose survivor has since been absorbed', () => {
    const store = useDuplicatesStore()
    store.merges = [merge(1, 11, 12), merge(2, 10, 11)]

    const blocked = store.mergeRows.find((row) => row.record.id === 1)

    expect(blocked!.blocked).toContain('Undo merge 2 first')
  })

  it('surfaces the server’s own refusal rather than a generic failure', async () => {
    // It names the row or merge to deal with first; a generic line does not.
    const store = useDuplicatesStore()
    mockPost.mockRejectedValue(
      new ApiError(409, 'Conflict', { detail: 'A book cannot absorb a video_game.' }),
    )

    await store.merge(10, 11)

    expect(store.error).toBe('A book cannot absorb a video_game.')
    expect(store.announcement).toBe('')
  })

  it('ignores a second decision on a pair already in flight', async () => {
    // Two clicks would merge, then try to merge the row the first one hid.
    const store = useDuplicatesStore()
    let settle = (): void => {}
    mockPost.mockReturnValue(new Promise((resolve) => (settle = () => resolve(merge(1, 10, 11)))))
    mockGet.mockResolvedValue(pageOf())

    const first = store.merge(10, 11)
    await store.merge(11, 10)
    settle()
    await first

    expect(mockPost).toHaveBeenCalledTimes(1)
  })

  it('announces what changed together with what is left to review', async () => {
    const store = useDuplicatesStore()
    mockPost.mockResolvedValue(merge(1, 10, 11))
    mockGet.mockResolvedValue(pageOf(suggestion(12, 13)))

    await store.merge(10, 11)

    expect(store.announcement).toBe('Merged “Row 11” into “Row 10”. 1 suspected duplicate.')
  })

  it('says how much of the set the limit is showing, not just how much it showed', () => {
    const store = useDuplicatesStore()
    store.suggestions = [suggestion(10, 11)]
    store.total = 40

    expect(store.summary).toBe('Showing 1 of 40 suspected duplicates.')
  })

  it('omits the type parameter entirely when no type is chosen', async () => {
    // Sending type='' would ask the API for a content type named the empty
    // string, which it refuses with a 400.
    const store = useDuplicatesStore()
    mockGet.mockResolvedValue(pageOf())

    await store.setFilter('type', '')

    expect(mockGet).toHaveBeenCalledWith('/duplicates', { user_id: 1, limit: 25 })
  })

  it('sends a chosen limit as a number the API can range-check', async () => {
    const store = useDuplicatesStore()
    mockGet.mockResolvedValue(pageOf())

    await store.setFilter('limit', '100')

    expect(mockGet).toHaveBeenCalledWith('/duplicates', { user_id: 1, limit: 100 })
  })

  it('offers a refused pair again through the pair path, not an item id', async () => {
    const store = useDuplicatesStore()
    mockDelete.mockResolvedValue({
      one_id: 10,
      one_title: 'Row 10',
      other_id: 11,
      other_title: 'Row 11',
    })
    mockGet.mockResolvedValue(pageOf())

    await store.offerAgain(10, 11)

    expect(mockDelete).toHaveBeenCalledWith('/duplicates/declined/10/11', { user_id: 1 })
    expect(store.error).toBe('')
  })
})
