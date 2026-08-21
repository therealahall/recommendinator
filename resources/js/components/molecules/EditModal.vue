<script setup lang="ts">
import { ref, computed, nextTick } from 'vue'
import type { ContentItemResponse, ItemEditRequest } from '@/types/api'
import { formatContentType, formatStatusForContentType } from '@/utils/format'
import { useFocusTrap } from '@/composables/useFocusTrap'
import StarRating from '@/components/atoms/StarRating.vue'
import SeasonChecklist from '@/components/molecules/SeasonChecklist.vue'
import TagInput from '@/components/atoms/TagInput.vue'

const props = defineProps<{
  item: ContentItemResponse
  saving: boolean
}>()

const emit = defineEmits<{
  save: [dbId: number, data: ItemEditRequest]
  close: []
}>()

const modalContent = ref<HTMLElement | null>(null)
useFocusTrap(modalContent, () => emit('close'))

const status = ref(props.item.status)
const rating = ref<number | null>(props.item.rating)
const review = ref(props.item.review || '')
const creator = ref(props.item.author ?? '')
const releaseYear = ref(props.item.release_year?.toString() ?? '')
const seasonsWatched = ref<number[]>(props.item.seasons_watched || [])
const genres = ref<string[]>(props.item.genres ?? [])
const tags = ref<string[]>(props.item.tags ?? [])
const description = ref(props.item.description ?? '')

const creatorInput = ref<HTMLInputElement | null>(null)
const yearInput = ref<HTMLInputElement | null>(null)
const creatorError = ref('')
const yearError = ref('')

const MIN_RELEASE_YEAR = 1800
const MAX_RELEASE_YEAR = 2200
const CREATOR_EMPTY = 'Creator cannot be empty.'
const YEAR_OUT_OF_RANGE = `Enter a year between ${MIN_RELEASE_YEAR} and ${MAX_RELEASE_YEAR}.`

const isTvShow = computed(() => props.item.content_type === 'tv_show' && props.item.total_seasons)

const hasReleaseYear = computed(() => props.item.content_type !== 'book')

// Both fields set a value and clear none, so an emptied box asks for something
// `library edit` refuses too — unless the item states none to begin with.
function creatorComplaint(): string {
  return !creator.value.trim() && props.item.author ? CREATOR_EMPTY : ''
}

function yearComplaint(): string {
  if (!hasReleaseYear.value) return ''
  const stated = releaseYear.value.trim()
  if (!stated) return props.item.release_year === null ? '' : YEAR_OUT_OF_RANGE
  if (!/^\d+$/.test(stated)) return YEAR_OUT_OF_RANGE
  const year = Number(stated)
  return year >= MIN_RELEASE_YEAR && year <= MAX_RELEASE_YEAR ? '' : YEAR_OUT_OF_RANGE
}

// Status and the checklist are two views of one fact, so each edit derives the
// other. Explicit handlers, not a watcher each way: those retrigger each other.
function onSeasonsChange(watched: number[]) {
  seasonsWatched.value = watched
  if (!isTvShow.value) return
  const total = props.item.total_seasons!
  if (watched.length === 0) {
    status.value = 'unread'
  } else if (watched.length >= total) {
    status.value = 'completed'
  } else {
    status.value = 'currently_consuming'
  }
}

function onStatusChange(event: Event) {
  status.value = (event.target as HTMLSelectElement).value
  if (!isTvShow.value) return
  // "In progress" says nothing about which seasons, so it leaves them alone.
  if (status.value === 'completed') {
    seasonsWatched.value = Array.from({ length: props.item.total_seasons! }, (_, i) => i + 1)
  } else if (status.value === 'unread') {
    seasonsWatched.value = []
  }
}

function save() {
  // The only other check is the server's, and its 400 renders on the page
  // behind this dialog's overlay, so a refusal is said here or not at all.
  creatorError.value = creatorComplaint()
  yearError.value = yearComplaint()
  if (creatorError.value || yearError.value) {
    const offending = creatorError.value ? creatorInput : yearInput
    void nextTick(() => offending.value?.focus())
    return
  }

  const data: ItemEditRequest = {
    status: status.value,
    rating: rating.value,
    // Blank clears the review alone: a stored "" reads as one the user wrote.
    // A blank creator was refused above, so null here means the item has none.
    review: review.value.trim() ? review.value : null,
    creator: creator.value.trim() || null,
    genres: genres.value,
    tags: tags.value,
    description: description.value || null,
  }
  const year = releaseYear.value.trim()
  if (hasReleaseYear.value && year) {
    data.release_year = Number(year)
  }
  if (isTvShow.value) {
    data.seasons_watched = seasonsWatched.value
  }
  emit('save', props.item.db_id!, data)
}

function onBackdropClick(event: MouseEvent) {
  if (event.target === event.currentTarget) {
    emit('close')
  }
}

</script>

<template>
  <div class="edit-modal" @click="onBackdropClick">
    <div ref="modalContent" class="edit-modal-content" role="dialog" aria-modal="true" aria-labelledby="edit-modal-title" tabindex="-1">
      <h3 id="edit-modal-title">{{ item.title }}</h3>
      <div class="edit-modal-subtitle">
        <span v-if="item.author">{{ item.author }} </span>
        <span class="badge badge-type">{{ formatContentType(item.content_type) }}</span>
      </div>

      <div class="edit-field">
        <label for="edit-creator">Creator</label>
        <input
          id="edit-creator"
          ref="creatorInput"
          v-model="creator"
          type="text"
          maxlength="500"
          placeholder="Author, director or developer..."
          :aria-invalid="creatorError ? 'true' : undefined"
          :aria-describedby="creatorError ? 'edit-creator-error' : undefined"
        >
        <p v-if="creatorError" id="edit-creator-error" class="edit-field-error" role="alert">
          {{ creatorError }}
        </p>
      </div>

      <div v-if="hasReleaseYear" class="edit-field">
        <label for="edit-release-year">Release year</label>
        <!-- Text, not number: a number input silently discards a pasted
             "2016 (remaster)" and rolls the year under a stray mouse wheel. -->
        <input
          id="edit-release-year"
          ref="yearInput"
          v-model="releaseYear"
          type="text"
          inputmode="numeric"
          :aria-invalid="yearError ? 'true' : undefined"
          :aria-describedby="yearError ? 'edit-release-year-error' : undefined"
        >
        <p v-if="yearError" id="edit-release-year-error" class="edit-field-error" role="alert">
          {{ yearError }}
        </p>
      </div>

      <div class="edit-field">
        <label for="edit-status">Status</label>
        <select id="edit-status" :value="status" @change="onStatusChange">
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
        <textarea id="edit-review" v-model="review" placeholder="Write a review..." />
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
        <textarea id="edit-description" v-model="description" maxlength="10000" placeholder="Add a description..." />
      </div>

      <div class="edit-modal-actions">
        <button class="btn btn-secondary" @click="emit('close')">Cancel</button>
        <button class="btn btn-primary" :disabled="saving" @click="save">
          {{ saving ? 'Saving...' : 'Save' }}
        </button>
      </div>
    </div>
  </div>
</template>
