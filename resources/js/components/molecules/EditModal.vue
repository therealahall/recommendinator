<script setup lang="ts">
import { ref, computed, nextTick, watch } from 'vue'
import type { ContentItemResponse, ItemEditRequest } from '@/types/api'
import { MAX_CREATOR_LENGTH, RELEASE_YEAR_TYPES } from '@/constants/library'
import { formatContentType, formatStatusForContentType } from '@/utils/format'
import { useFocusTrap } from '@/composables/useFocusTrap'
import { useDiscardGuard } from '@/composables/useDiscardGuard'
import StarRating from '@/components/atoms/StarRating.vue'
import SeasonChecklist from '@/components/molecules/SeasonChecklist.vue'
import TagInput from '@/components/atoms/TagInput.vue'
import DiscardConfirm from '@/components/molecules/DiscardConfirm.vue'

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
  restoreEnrichment: [dbId: number]
  close: []
}>()

const modalContent = ref<HTMLElement | null>(null)

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

// The dialog covers the page, so focus is what carries a refusal to a user who
// cannot see the whole screen.
watch(
  () => props.saveError,
  async (said) => {
    if (!said) return
    await nextTick()
    refusal.value?.focus()
  },
)

const hasReleaseYear = computed(() => RELEASE_YEAR_TYPES.includes(props.item.content_type))

// Status and the checklist are two views of one fact, so each derives the
// other. Handlers, not watchers: a watcher each way retriggers the other.
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

// Only what changed is sent: storage stamps an item manually enriched for any
// genres, tags or description it receives, dropping it out of the automatic
// queue — so an untouched box must not travel with a rating.
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
const { confirming, requestClose, keepEditing } = useDiscardGuard(dirty, () => emit('close'))

useFocusTrap(modalContent, requestClose)

function save() {
  emit('save', props.item.db_id!, edits.value)
}

function onBackdropClick(event: MouseEvent) {
  if (event.target === event.currentTarget) {
    requestClose()
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
          v-model="creator"
          type="text"
          :maxlength="MAX_CREATOR_LENGTH"
          placeholder="Author, director or developer..."
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
          inputmode="numeric"
        >
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
      <p class="edit-modal-note">Editing these opts the item out of automatic enrichment.</p>

      <div v-if="item.manually_enriched" class="edit-field">
        <p class="edit-modal-note">This item's metadata is manual, so automatic enrichment skips it.</p>
        <button class="btn btn-secondary" @click="emit('restoreEnrichment', item.db_id!)">
          Restore automatic enrichment
        </button>
      </div>

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

      <!-- Mounted while silent: inserted populated it reads as content (4.1.3). -->
      <p ref="refusal" class="edit-save-error focus-fallback" role="alert" tabindex="-1">{{ saveError }}</p>

      <DiscardConfirm v-if="confirming" @keep="keepEditing" @discard="emit('close')" />

      <div class="edit-modal-actions">
        <button class="btn btn-secondary" @click="emit('close')">Cancel</button>
        <button class="btn btn-primary" :disabled="saving" @click="save">
          {{ saving ? 'Saving...' : 'Save' }}
        </button>
      </div>
    </div>
  </div>
</template>
