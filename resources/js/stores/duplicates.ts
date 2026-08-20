import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import { useApi } from '@/composables/useApi'
import { useAppStore } from '@/stores/app'
import type {
  DeclinedPair,
  DuplicateSuggestion,
  DuplicateSuggestionPage,
  MergeRecord,
} from '@/types/api'

export const SUGGESTION_LIMITS = [10, 25, 50, 100] as const
const DEFAULT_LIMIT = 25

/** A pair in either order, so a decision on it is keyed the same way twice. */
export function pairKey(one: number, other: number): string {
  return one < other ? `${one}:${other}` : `${other}:${one}`
}

export interface MergeRow {
  record: MergeRecord
  /** Empty when the undo is legal, else the merge to deal with first: merges
   *  undo newest first per survivor and the server refuses any other order. */
  blocked: string
}

export const useDuplicatesStore = defineStore('duplicates', () => {
  const api = useApi()

  const suggestions = ref<DuplicateSuggestion[]>([])
  const total = ref(0)
  const merges = ref<MergeRecord[]>([])
  const declined = ref<DeclinedPair[]>([])

  const typeFilter = ref('')
  const limit = ref<number>(DEFAULT_LIMIT)

  const loading = ref(false)
  const error = ref('')
  const announcement = ref('')
  const pending = ref<string[]>([])

  const summary = computed(() => {
    if (total.value === 0) return 'No suspected duplicates.'
    if (suggestions.value.length < total.value) {
      return `Showing ${suggestions.value.length} of ${total.value} suspected duplicates.`
    }
    return total.value === 1
      ? '1 suspected duplicate.'
      : `${total.value} suspected duplicates.`
  })

  const mergeRows = computed<MergeRow[]>(() => {
    const newestFirst = [...merges.value].sort((a, b) => b.id - a.id)
    const absorbedBy = new Map(newestFirst.map((one) => [one.absorbed_id, one.id]))
    const nextForSurvivor = new Map<number, number>()
    return newestFirst.map((record) => {
      const later = nextForSurvivor.get(record.survivor_id)
      nextForSurvivor.set(record.survivor_id, record.id)
      const hiding = absorbedBy.get(record.survivor_id)
      let blocked = ''
      if (later !== undefined) {
        blocked = `Undo merge ${later} first — it was made into “${record.survivor_title}” after this one.`
      } else if (hiding !== undefined) {
        blocked = `Undo merge ${hiding} first — it absorbed “${record.survivor_title}”.`
      }
      return { record, blocked }
    })
  })

  function isPending(key: string): boolean {
    return pending.value.includes(key)
  }

  function userParams(): Record<string, string | number> {
    return { user_id: useAppStore().currentUserId }
  }

  function message(err: unknown, fallback: string): string {
    return err instanceof Error && err.message ? err.message : fallback
  }

  async function loadSuggestions(): Promise<void> {
    loading.value = true
    try {
      const params: Record<string, string | number> = {
        ...userParams(),
        limit: limit.value,
      }
      if (typeFilter.value) params.type = typeFilter.value
      const page = await api.get<DuplicateSuggestionPage>('/duplicates', params)
      suggestions.value = page.suggestions
      total.value = page.total
    } catch (err) {
      error.value = message(err, 'Failed to load suspected duplicates.')
    } finally {
      loading.value = false
    }
  }

  async function loadMerges(): Promise<void> {
    try {
      merges.value = await api.get<MergeRecord[]>('/merges', userParams())
    } catch (err) {
      error.value = message(err, 'Failed to load past merges.')
    }
  }

  async function loadDeclined(): Promise<void> {
    try {
      declined.value = await api.get<DeclinedPair[]>('/duplicates/declined', userParams())
    } catch (err) {
      error.value = message(err, 'Failed to load declined pairs.')
    }
  }

  function loadAll(): Promise<unknown> {
    return Promise.all([loadSuggestions(), loadMerges(), loadDeclined()])
  }

  async function decide(
    key: string,
    act: () => Promise<string>,
    reload: () => Promise<unknown>,
  ): Promise<void> {
    if (isPending(key)) return
    pending.value = [...pending.value, key]
    error.value = ''
    try {
      const outcome = await act()
      await reload()
      announcement.value = `${outcome} ${summary.value}`
    } catch (err) {
      // Storage names the row or the merge to deal with first, so its words go
      // through whole rather than into one generic failure.
      error.value = message(err, 'That change did not go through.')
      announcement.value = ''
    } finally {
      pending.value = pending.value.filter((one) => one !== key)
    }
  }

  function merge(survivorId: number, absorbedId: number): Promise<void> {
    return decide(
      `merge:${pairKey(survivorId, absorbedId)}`,
      async () => {
        const record = await api.post<MergeRecord>(
          '/merges',
          { survivor_id: survivorId, absorbed_id: absorbedId },
          userParams(),
        )
        return `Merged “${record.absorbed_title}” into “${record.survivor_title}”.`
      },
      () => Promise.all([loadSuggestions(), loadMerges()]),
    )
  }

  function declinePair(oneId: number, otherId: number): Promise<void> {
    return decide(
      `decline:${pairKey(oneId, otherId)}`,
      async () => {
        const pair = await api.post<DeclinedPair>(
          '/duplicates/declined',
          { one_id: oneId, other_id: otherId },
          userParams(),
        )
        return `“${pair.one_title}” and “${pair.other_title}” will not be offered again.`
      },
      () => Promise.all([loadSuggestions(), loadDeclined()]),
    )
  }

  function undoMerge(mergeId: number): Promise<void> {
    return decide(
      `undo:${mergeId}`,
      async () => {
        const record = await api.delete<MergeRecord>(`/merges/${mergeId}`, userParams())
        return `Put “${record.absorbed_title}” back beside “${record.survivor_title}”.`
      },
      () => Promise.all([loadSuggestions(), loadMerges()]),
    )
  }

  function offerAgain(oneId: number, otherId: number): Promise<void> {
    return decide(
      `undecline:${pairKey(oneId, otherId)}`,
      async () => {
        const pair = await api.delete<DeclinedPair>(
          `/duplicates/declined/${oneId}/${otherId}`,
          userParams(),
        )
        return `“${pair.one_title}” and “${pair.other_title}” may be offered again.`
      },
      () => Promise.all([loadSuggestions(), loadDeclined()]),
    )
  }

  async function setFilter(key: 'type' | 'limit', value: string): Promise<void> {
    if (key === 'type') typeFilter.value = value
    else limit.value = Number(value)
    await loadSuggestions()
    announcement.value = summary.value
  }

  return {
    suggestions,
    total,
    merges,
    declined,
    typeFilter,
    limit,
    loading,
    error,
    announcement,
    summary,
    mergeRows,
    isPending,
    loadAll,
    loadSuggestions,
    loadMerges,
    loadDeclined,
    merge,
    declinePair,
    undoMerge,
    offerAgain,
    setFilter,
  }
})
