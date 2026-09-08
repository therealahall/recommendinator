<script setup lang="ts">
import { ref, computed, nextTick, watch } from 'vue'
import type { ContentItemResponse, ItemEditRequest } from '@/types/api'
import { MAX_CREATOR_LENGTH, RELEASE_YEAR_TYPES } from '@/constants/library'
import { formatContentType, formatStatusForContentType } from '@/utils/format'
import { useDiscardGuard } from '@/composables/useDiscardGuard'
import { rescueFocus } from '@/utils/focus'
import ModalDialog from '@/components/atoms/ModalDialog.vue'
import StarRating from '@/components/atoms/StarRating.vue'
import SeasonChecklist from '@/components/molecules/SeasonChecklist.vue'
import TagInput from '@/components/atoms/TagInput.vue'
import ConfirmPanel from '@/components/molecules/ConfirmPanel.vue'

const props = defineProps<{
  item: ContentItemResponse
  saving: boolean
  /** Why the server refused the last save, '' while it has refused nothing. */
  saveError: string
  /** The status the opening action means, where it is not the item's own. */
  initialStatus?: string
}>()

const emit = defineEmits<{
  save: [dbId: number, data: ItemEditRequest]
  clearManual: [dbId: number, field: string]
  close: []
}>()

const dialog = ref<InstanceType<typeof ModalDialog> | null>(null)
const modalContent = computed(() => dialog.value?.surface ?? null)

const isTvShow = computed(() => props.item.content_type === 'tv_show' && props.item.total_seasons)

const status = ref(props.initialStatus || props.item.status)
const rating = ref<number | null>(props.item.rating)
const review = ref(props.item.review || '')
const creator = ref(props.item.author ?? '')
const releaseYear = ref(props.item.release_year?.toString() ?? '')
const seasonsWatched = ref<number[]>([...(props.item.seasons_watched ?? [])])
const genres = ref<string[]>([...(props.item.genres ?? [])])
const tags = ref<string[]>([...(props.item.tags ?? [])])
const description = ref(props.item.description ?? '')

const loaded = {
  status: props.item.status,
  rating: props.item.rating,
  review: review.value.trim(),
  creator: creator.value.trim(),
  releaseYear: releaseYear.value.trim(),
  seasons: [...seasonsWatched.value],
  genres: [...genres.value],
  tags: [...tags.value],
  description: description.value.trim(),
}

const refusal = ref<HTMLElement | null>(null)
const manualNote = ref<HTMLElement | null>(null)

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
  (props.item.manual_fields ?? []).map((held) => ({
    field: held.field,
    label: held.drifted
      ? `${held.field}: ${held.value ?? 'nothing'} — its source now says ${held.source_value ?? 'nothing'}`
      : `${held.field}: ${held.value ?? 'nothing'}`,
  })),
)

// A cleared row takes its button with it, so focus lands on the line above
// rather than dropping to <body> (WCAG 2.4.3).
watch(
  () => heldFields.value.length,
  async (now, before) => {
    if (now >= before) return
    await nextTick()
    rescueFocus(manualNote.value)
  },
)

const REFUSED_FIELDS: [string, string][] = [
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

// Handlers, not watchers: a watcher each way retriggers the other.
function onSeasonsChange(watched: number[]) {
  seasonsWatched.value = watched
  if (!isTvShow.value) return
  const total = props.item.total_seasons!
  const rendered = watched.filter((season) => season <= total).length
  if (rendered === 0) {
    status.value = 'unread'
  } else if (rendered >= total) {
    status.value = 'completed'
  } else {
    status.value = 'currently_consuming'
  }
}

function seasonsForStatus() {
  if (!isTvShow.value) return
  // "In progress" says nothing about which seasons, so it leaves them alone.
  if (status.value === 'completed') {
    seasonsWatched.value = Array.from({ length: props.item.total_seasons! }, (_, i) => i + 1)
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

function sameList(one: readonly (string | number)[], other: readonly (string | number)[]) {
  return one.length === other.length && one.every((value, index) => value === other[index])
}

// Only what changed is sent: storage holds every field it receives against its
// source, so an untouched box must not travel with a rating.
const edits = computed<ItemEditRequest>(() => {
  const data: ItemEditRequest = {}
  if (status.value !== loaded.status) data.status = status.value
  if (rating.value !== loaded.rating) data.rating = rating.value
  if (review.value.trim() !== loaded.review) data.review = review.value.trim() || null
  if (creator.value.trim() !== loaded.creator) data.creator = creator.value.trim()
  if (releaseYear.value.trim() !== loaded.releaseYear) {
    data.release_year = releaseYear.value.trim()
  }
  if (isTvShow.value && !sameList(seasonsWatched.value, loaded.seasons)) {
    data.seasons_watched = seasonsWatched.value
  }
  if (!sameList(genres.value, loaded.genres)) data.genres = genres.value
  if (!sameList(tags.value, loaded.tags)) data.tags = tags.value
  if (description.value.trim() !== loaded.description) {
    data.description = description.value.trim()
  }
  return data
})

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
</script>

<template>
  <ModalDialog ref="dialog" labelled-by="edit-modal-title" @dismiss="requestClose">
    <h3 id="edit-modal-title" class="edit-modal-title">{{ item.title }}</h3>
    <div class="edit-modal-subtitle">
      <span v-if="item.author">{{ item.author }} </span>
      <span class="badge" data-tone="accent">{{ formatContentType(item.content_type) }}</span>
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
      </select>
    </div>

    <div class="edit-field">
      <label id="edit-rating-label">Rating</label>
      <StarRating v-model="rating" aria-labelledby="edit-rating-label" />
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
        :total-seasons="item.total_seasons!"
        @update:model-value="onSeasonsChange"
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

    <hr class="edit-modal-divider">
    <h4 class="edit-modal-section">Your corrections</h4>

    <div class="edit-field">
      <!-- Mounted whether or not anything is held: a region inserted already
           populated reads as content rather than a status change (4.1.3). -->
      <p ref="manualNote" class="edit-modal-note focus-fallback" role="status" tabindex="-1">
        {{ heldFields.length
          ? 'A sync records what its source says about these, and leaves them alone.'
          : 'Editing a field above holds it against its source.' }}
      </p>
      <ul class="edit-manual-list">
        <li v-for="held in heldFields" :key="held.field" class="edit-manual-held">
          <span>{{ held.label }}</span>
          <button class="btn btn-secondary" @click="emit('clearManual', item.db_id!, held.field)">
            Use the source's value
          </button>
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
