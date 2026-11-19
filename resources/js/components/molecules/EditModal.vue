<script setup lang="ts">
import { ref, computed, nextTick, shallowRef, watch } from 'vue'
import type {
  ContentItemResponse,
  EnrichmentCandidate,
  FieldWriters,
  ItemEditRequest,
} from '@/types/api'
import {
  MAX_CREATOR_LENGTH,
  MAX_SEARCH_LENGTH,
  MAX_TITLE_LENGTH,
  RELEASE_YEAR_TYPES,
} from '@/constants/library'
import { capitalize, formatContentType, formatStatusForContentType } from '@/utils/format'
import { useDiscardGuard } from '@/composables/useDiscardGuard'
import { rescueFocus } from '@/utils/focus'
import ModalDialog from '@/components/atoms/ModalDialog.vue'
import StarRating from '@/components/atoms/StarRating.vue'
import SeasonChecklist from '@/components/molecules/SeasonChecklist.vue'
import TagInput from '@/components/atoms/TagInput.vue'
import ConfirmPanel from '@/components/molecules/ConfirmPanel.vue'
import FieldWriterList from '@/components/molecules/FieldWriterList.vue'
import ToggleSwitch from '@/components/atoms/ToggleSwitch.vue'
import { resetQuestion } from '@/utils/enrichmentReset'

const props = defineProps<{
  item: ContentItemResponse
  saving: boolean
  /** Why the server refused the last save, '' while it has refused nothing. */
  saveError: string
  /** The status the opening action means, where it is not the item's own. */
  initialStatus?: string
  /** Left out where the host has not wired the pin, which hides the section. */
  pinned?: Record<string, string>
  pinCandidates?: EnrichmentCandidate[]
  pinSearching?: boolean
  /** What the server said about the last pin, retry or reset, '' before any. */
  pinMessage?: string
  /** Why the last provider search failed, '' while it has not. */
  pinSearchError?: string
  /** Left out where the host has not wired the switcher, which hides it. */
  fieldWriters?: FieldWriters[]
}>()

const emit = defineEmits<{
  save: [dbId: number, data: ItemEditRequest]
  clearManual: [dbId: number, field: string]
  chooseWriter: [dbId: number, field: string, writer: string | null]
  pinSearch: [dbId: number, query: string]
  pin: [dbId: number, provider: string, recordId: string | null]
  retryEnrichment: [dbId: number]
  resetEnrichment: [dbId: number, hard: boolean]
  close: []
}>()

const dialog = ref<InstanceType<typeof ModalDialog> | null>(null)
const modalContent = computed(() => dialog.value?.surface ?? null)

// Up to the highest watched season too: a show whose source states no count,
// or too few, still lists every season it can be rated on.
const seasonCount = Math.max(props.item.total_seasons ?? 0, ...(props.item.seasons_watched ?? []))

const isTvShow = computed(() => props.item.content_type === 'tv_show' && seasonCount > 0)

const title = ref(props.item.title)
const status = ref(props.initialStatus || props.item.status)
const rating = ref<number | null>(props.item.rating)
const review = ref(props.item.review || '')
const creator = ref(props.item.author ?? '')
const releaseYear = ref(props.item.release_year?.toString() ?? '')
const seasonsWatched = ref<number[]>([...(props.item.seasons_watched ?? [])])
const seasonRatings = ref<Record<number, number | null>>({ ...props.item.season_ratings })
const genres = ref<string[]>([...(props.item.genres ?? [])])
const tags = ref<string[]>([...(props.item.tags ?? [])])
const description = ref(props.item.description ?? '')

function baseline(item: ContentItemResponse) {
  return {
    title: item.title.trim(),
    status: item.status,
    rating: item.rating,
    review: (item.review || '').trim(),
    creator: (item.author ?? '').trim(),
    releaseYear: item.release_year?.toString() ?? '',
    seasons: [...(item.seasons_watched ?? [])],
    seasonRatings: { ...item.season_ratings },
    genres: [...(item.genres ?? [])],
    tags: [...(item.tags ?? [])],
    description: (item.description ?? '').trim(),
  }
}

const loaded = shallowRef(baseline(props.item))

const refusal = ref<HTMLElement | null>(null)
const manualHeading = ref<HTMLElement | null>(null)

watch(
  () => props.saveError,
  async (said) => {
    if (!said) return
    await nextTick()
    rescueFocus(refusal.value)
  },
)

// Pre-computed rather than formatted in the v-for, which re-runs every render.
const heldFields = computed(() =>
  (props.item.manual_fields ?? []).map((field) => ({
    field,
    label: capitalize(field.replace(/_/g, ' ')),
  })),
)

const clearedField = ref('')

// A cleared row takes its button with it, so focus lands on the heading rather
// than dropping to <body> (WCAG 2.4.3). Not on the note, which announces its own
// new words: focus landing there reads them a second time.
watch(heldFields, async (now, before) => {
  const dropped = before.find((held) => !now.some((one) => one.field === held.field))
  if (!dropped) return
  clearedField.value = dropped.label
  await nextTick()
  rescueFocus(manualHeading.value)
})

const manualNote = computed(() => {
  if (clearedField.value) return `${clearedField.value} is no longer held.`
  return heldFields.value.length
    ? 'A sync and enrichment leave these alone.'
    : 'Editing a field above, status aside, holds it against its source.'
})

const REFUSED_FIELDS: [string, string][] = [
  ['Title', 'edit-title'],
  ['Review', 'edit-review'],
  ['Creator', 'edit-creator'],
  ['Release year', 'edit-release-year'],
]

// The server words each refusal with the field's own label first, so the
// sentence at the foot of the dialog can point back at the box it is about.
const refusedField = computed(
  () => REFUSED_FIELDS.find(([label]) => props.saveError.startsWith(label))?.[1] ?? '',
)

function refusalFor(inputId: string) {
  if (refusedField.value !== inputId) return {}
  return { 'aria-invalid': true, 'aria-describedby': 'edit-save-error' }
}

const hasReleaseYear = computed(() => RELEASE_YEAR_TYPES.includes(props.item.content_type))

function onSeasonRate(season: number, value: number | null) {
  seasonRatings.value = { ...seasonRatings.value, [season]: value }
}

function ratingOf(ratings: Record<number, number | null>, season: number): number | null {
  return ratings[season] ?? null
}

// Read off watched seasons only: un-watching one drops its rating on save.
const ratedSeasons = computed(() =>
  seasonsWatched.value
    .map((season) => ratingOf(seasonRatings.value, season))
    .filter((value): value is number => value !== null),
)

// The server's rule, so the dialog shows what saving will store: the mean
// rounded half up, which Math.round already does for a positive mean.
const derivedRating = computed(() => {
  const count = ratedSeasons.value.length
  if (count === 0) return null
  return Math.round(ratedSeasons.value.reduce((sum, value) => sum + value, 0) / count)
})

const derivedNote = computed(() =>
  derivedRating.value === null ? '' : `${derivedRating.value} out of 5, the mean of its season ratings`,
)

// Handlers, not watchers: a watcher each way retriggers the other.
// Status and seasons derive from each other only against a stated count: with
// none, no number of watched seasons is all of them. A season ticked on an
// abandoned show leaves it abandoned, as the API does.
function onSeasonsChange(watched: number[]) {
  seasonsWatched.value = watched
  const total = props.item.total_seasons
  if (!isTvShow.value || !total || status.value === 'dnf') return
  if (watched.length === 0) {
    status.value = 'unread'
  } else if (watched.length >= total) {
    status.value = 'completed'
  } else {
    status.value = 'currently_consuming'
  }
}

function seasonsForStatus() {
  const total = props.item.total_seasons
  if (!isTvShow.value || !total) return
  // "In progress" says nothing about which seasons, so it leaves them alone.
  if (status.value === 'completed') {
    const every = Array.from({ length: total }, (_, i) => i + 1)
    seasonsWatched.value = [...new Set([...seasonsWatched.value, ...every])].sort((a, b) => a - b)
  } else if (status.value === 'unread') {
    seasonsWatched.value = []
  }
}

function onStatusChange(event: Event) {
  status.value = (event.target as HTMLSelectElement).value
  seasonsForStatus()
}

// A preselected status derives the checklist as picking it by hand does, so a
// show is never completed without its seasons shown first.
if (props.initialStatus) seasonsForStatus()

// Compared as sets: a rebuild that reorders a list states the same thing, and
// reading that as an edit would save a hold nobody asked for.
function sameList(one: readonly (string | number)[], other: readonly (string | number)[]) {
  if (one.length !== other.length) return false
  const ordered = [...other].sort()
  return [...one].sort().every((value, index) => value === ordered[index])
}

// Only what changed is sent: storage holds every field but the status against
// its source, so an untouched box must not travel with a rating.
const edits = computed<ItemEditRequest>(() => {
  const data: ItemEditRequest = {}
  const was = loaded.value
  if (title.value.trim() !== was.title) data.title = title.value.trim()
  if (status.value !== was.status) data.status = status.value
  // A show with rated seasons takes its rating from them, so it sends none.
  if (derivedRating.value === null && rating.value !== was.rating) data.rating = rating.value
  if (review.value.trim() !== was.review) data.review = review.value.trim() || null
  if (creator.value.trim() !== was.creator) data.creator = creator.value.trim()
  if (releaseYear.value.trim() !== was.releaseYear) {
    data.release_year = releaseYear.value.trim()
  }
  if (isTvShow.value && !sameList(seasonsWatched.value, was.seasons)) {
    data.seasons_watched = seasonsWatched.value
  }
  const rerated = seasonsWatched.value.filter(
    (season) => ratingOf(seasonRatings.value, season) !== ratingOf(was.seasonRatings, season),
  )
  if (isTvShow.value && rerated.length > 0) {
    data.season_ratings = Object.fromEntries(
      rerated.map((season) => [season, ratingOf(seasonRatings.value, season)]),
    )
  }
  if (!sameList(genres.value, was.genres)) data.genres = genres.value
  if (!sameList(tags.value, was.tags)) data.tags = tags.value
  if (description.value.trim() !== was.description) {
    data.description = description.value.trim()
  }
  return data
})

// A reset or a writer choice rebuilds the item under the open dialog. Each field
// not edited here takes the rebuilt value: kept stale, the next save would
// write back what the rebuild dropped, as a hold.
watch(
  () => props.item,
  (item) => {
    const edited = edits.value
    const now = baseline(item)
    if (!('title' in edited)) title.value = item.title
    if (!('status' in edited)) status.value = now.status
    if (!('rating' in edited)) rating.value = now.rating
    if (!('review' in edited)) review.value = item.review || ''
    if (!('creator' in edited)) creator.value = item.author ?? ''
    if (!('release_year' in edited)) releaseYear.value = now.releaseYear
    if (!('seasons_watched' in edited)) seasonsWatched.value = [...now.seasons]
    if (!('season_ratings' in edited)) seasonRatings.value = { ...now.seasonRatings }
    if (!('genres' in edited)) genres.value = [...now.genres]
    if (!('tags' in edited)) tags.value = [...now.tags]
    if (!('description' in edited)) description.value = item.description ?? ''
    loaded.value = now
  },
)

const dirty = computed(() => Object.keys(edits.value).length > 0)
const { confirming, requestClose, keepEditing } = useDiscardGuard(
  dirty,
  () => emit('close'),
  modalContent,
)

function save() {
  if (props.saving) return
  emit('save', props.item.db_id!, edits.value)
}

const pinQuery = ref('')
const searched = ref(false)
const pinHeading = ref<HTMLElement | null>(null)

// Pre-computed rather than formatted in the v-for, which re-runs every render.
const pinRows = computed(() =>
  (props.pinCandidates ?? []).map((candidate) => ({
    key: `${candidate.provider}:${candidate.record_id}`,
    candidate,
    label: [candidate.provider, candidate.title, candidate.creator, candidate.year]
      .filter(Boolean)
      .join(' · '),
  })),
)

const pinnedRows = computed(() =>
  Object.entries(props.pinned ?? {}).map(([provider, recordId]) => ({ provider, recordId })),
)

const pinNote = computed(() => {
  if (props.pinSearching) return 'Searching the providers…'
  if (props.pinMessage) return props.pinMessage
  if (!searched.value) return ''
  // A failed search empties the list and says why in the refusal below, so
  // "nothing offered" would contradict it. A refused save says nothing about
  // the offer, so only the search's own failure counts.
  if (pinRows.value.length === 0) return props.pinSearchError ? '' : 'No provider offered a record'
  return pinRows.value.length === 1 ? '1 record offered' : `${pinRows.value.length} records offered`
})

// Unpinning takes its own button with it, so focus lands on the words heading
// the section rather than dropping to <body> (WCAG 2.4.3). Not on the note,
// which announces its own new sentence: landing there reads it a second time.
watch(
  () => pinnedRows.value.length,
  async (now, before) => {
    if (now >= before) return
    await nextTick()
    rescueFocus(pinHeading.value)
  },
)

const resetHard = ref(false)
const confirmingReset = ref(false)

const resetAsked = computed(() =>
  resetQuestion(
    `Re-queue “${props.item.title}” for enrichment?`,
    'wherever this item holds one',
    resetHard.value,
  ),
)

function answerReset(reset: boolean) {
  confirmingReset.value = false
  if (reset) emit('resetEnrichment', props.item.db_id!, resetHard.value)
}

function searchRecords() {
  if (props.pinSearching) return
  searched.value = true
  emit('pinSearch', props.item.db_id!, pinQuery.value)
}
</script>

<template>
  <ModalDialog ref="dialog" labelled-by="edit-modal-title" @dismiss="requestClose">
    <h3 id="edit-modal-title" class="edit-modal-title">{{ item.title }}</h3>
    <div class="edit-modal-subtitle">
      <span v-if="item.author">{{ item.author }} </span>
      <span class="badge" data-tone="accent">{{ formatContentType(item.content_type) }}</span>
    </div>

    <div class="edit-field">
      <label for="edit-title">Title</label>
      <input
        id="edit-title"
        v-model="title"
        type="text"
        class="field"
        :maxlength="MAX_TITLE_LENGTH"
        v-bind="refusalFor('edit-title')"
      >
    </div>

    <div class="edit-field">
      <label for="edit-creator">Creator</label>
      <input
        id="edit-creator"
        v-model="creator"
        type="text"
        class="field"
        :maxlength="MAX_CREATOR_LENGTH"
        placeholder="Author, director or developer..."
        v-bind="refusalFor('edit-creator')"
      >
    </div>

    <div v-if="hasReleaseYear" class="edit-field">
      <label for="edit-release-year">Release year</label>
      <!-- Text: a number input eats a pasted "2016 (remaster)" and rolls
           the year under a stray mouse wheel. -->
      <input
        id="edit-release-year"
        v-model="releaseYear"
        type="text"
        class="field"
        inputmode="numeric"
        v-bind="refusalFor('edit-release-year')"
      >
    </div>

    <div class="edit-field">
      <label for="edit-status">Status</label>
      <select id="edit-status" class="field" :value="status" @change="onStatusChange">
        <option value="unread">{{ formatStatusForContentType('unread', item.content_type) }}</option>
        <option value="currently_consuming">In Progress</option>
        <option value="completed">Completed</option>
        <option value="dnf">Did not finish</option>
      </select>
    </div>

    <div class="edit-field">
      <label id="edit-rating-label">Rating</label>
      <!-- Mounted while silent: inserted populated it reads as content (4.1.3). -->
      <p v-if="isTvShow" class="edit-modal-note" data-testid="derived-rating" role="status" aria-live="polite">{{ derivedNote }}</p>
      <StarRating v-if="derivedRating === null" v-model="rating" aria-labelledby="edit-rating-label" />
    </div>

    <div class="edit-field">
      <label for="edit-review">Review</label>
      <textarea
        id="edit-review"
        v-model="review"
        class="field"
        placeholder="Write a review..."
        v-bind="refusalFor('edit-review')"
      />
    </div>

    <div v-if="isTvShow" class="edit-field">
      <SeasonChecklist
        :model-value="seasonsWatched"
        :total-seasons="seasonCount"
        :ratings="seasonRatings"
        @update:model-value="onSeasonsChange"
        @rate="onSeasonRate"
      />
    </div>

    <hr class="edit-modal-divider">
    <h4 class="edit-modal-section">Enrichment metadata</h4>

    <div class="edit-field">
      <TagInput
        v-model="genres"
        label="Genres"
        input-id="edit-genres"
        placeholder="Add a genre..."
        empty-text="No genres yet"
      />
    </div>

    <div class="edit-field">
      <TagInput
        v-model="tags"
        label="Tags"
        input-id="edit-tags"
        placeholder="Add a tag..."
        empty-text="No tags yet"
      />
    </div>

    <div class="edit-field">
      <label for="edit-description">Description</label>
      <textarea id="edit-description" v-model="description" class="field" maxlength="10000" placeholder="Add a description..." />
    </div>

    <div v-if="pinned" class="edit-field">
      <label
        ref="pinHeading"
        class="focus-fallback"
        for="edit-pin-query"
        tabindex="-1"
      >Which record enriches this</label>
      <p class="edit-modal-note">
        Search each provider and pick the right record. Enrichment then reads
        that one instead of matching by title.
      </p>
      <input
        id="edit-pin-query"
        v-model="pinQuery"
        type="search"
        class="field"
        :maxlength="MAX_SEARCH_LENGTH"
        placeholder="A title or a provider link; empty searches this item's own title..."
        @keydown.enter.prevent="searchRecords"
      >
      <div class="edit-pin-actions">
        <button class="btn btn-secondary" :aria-disabled="pinSearching || undefined" @click="searchRecords">
          {{ pinSearching ? 'Searching…' : 'Search providers' }}
        </button>
        <button class="btn btn-secondary" @click="emit('retryEnrichment', item.db_id!)">
          Enrich this again
        </button>
        <ToggleSwitch v-model="resetHard" label="Drop unclaimed values too" />
        <button type="button" class="btn btn-danger" @click="confirmingReset = true">
          Reset enrichment
        </button>
      </div>
      <ConfirmPanel
        v-if="confirmingReset"
        :message="resetAsked"
        confirm-label="Reset"
        cancel-label="Keep it"
        destructive
        @cancel="answerReset(false)"
        @confirm="answerReset(true)"
      />
      <!-- Mounted whether or not it has anything to say: a region inserted
           already populated reads as content rather than a status change. -->
      <p id="edit-pin-note" class="edit-modal-note" role="status" aria-live="polite">{{ pinNote }}</p>
      <ul v-if="pinRows.length" class="edit-manual-list" aria-label="Records the providers offered">
        <li v-for="row in pinRows" :key="row.key" class="edit-manual-held">
          <span>{{ row.label }}</span>
          <button
            class="btn btn-secondary"
            :aria-label="`Use ${row.label}`"
            @click="emit('pin', item.db_id!, row.candidate.provider, row.candidate.record_id)"
          >Use this record</button>
        </li>
      </ul>
      <ul v-if="pinnedRows.length" class="edit-manual-list" aria-label="Records this item is pinned to">
        <li v-for="row in pinnedRows" :key="row.provider" class="edit-manual-held">
          <span>Pinned: {{ row.provider }} {{ row.recordId }}</span>
          <button
            class="btn btn-secondary"
            :aria-label="`Match ${row.provider} by title again`"
            @click="emit('pin', item.db_id!, row.provider, null)"
          >Match by title again</button>
        </li>
      </ul>
    </div>

    <template v-if="fieldWriters">
      <hr class="edit-modal-divider">
      <h4 class="edit-modal-section">Where each field comes from</h4>
      <FieldWriterList
        :field-writers="fieldWriters"
        @choose="(field: string, writer: string | null) => emit('chooseWriter', item.db_id!, field, writer)"
      />
    </template>

    <hr class="edit-modal-divider">
    <h4
      ref="manualHeading"
      class="edit-modal-section focus-fallback"
      tabindex="-1"
    >Your corrections</h4>

    <div class="edit-field">
      <!-- Mounted whether or not anything is held: a region inserted already
           populated reads as content rather than a status change (4.1.3). -->
      <p class="edit-modal-note" role="status">{{ manualNote }}</p>
      <ul class="edit-manual-list" aria-label="Fields held against their source">
        <li v-for="held in heldFields" :key="held.field" class="edit-manual-held">
          <span>{{ held.label }}</span>
          <button
            class="btn btn-secondary"
            :aria-label="`Stop holding ${held.label}`"
            @click="emit('clearManual', item.db_id!, held.field)"
          >Stop holding this field</button>
        </li>
      </ul>
    </div>

    <!-- Mounted while silent: inserted populated it reads as content (4.1.3). -->
    <p
      id="edit-save-error"
      ref="refusal"
      class="state state--error edit-save-error focus-fallback"
      role="alert"
      tabindex="-1"
    >{{ saveError }}</p>

    <ConfirmPanel
      v-if="confirming"
      message="Discard your unsaved changes?"
      confirm-label="Discard"
      cancel-label="Keep editing"
      @cancel="keepEditing"
      @confirm="emit('close')"
    />

    <template #actions>
      <button class="btn btn-secondary" @click="requestClose">Cancel</button>
      <!-- aria-disabled, not disabled: locking the button the user just
           pressed drops focus to <body> and moves the trap's wrap boundary. -->
      <button class="btn btn-primary" :aria-disabled="saving || undefined" @click="save">
        {{ saving ? 'Saving...' : 'Save' }}
      </button>
    </template>
  </ModalDialog>
</template>
