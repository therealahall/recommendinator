<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import type { ContentItemResponse, ItemEditRequest } from '@/types/api'
import { formatContentType, formatStatusForContentType } from '@/utils/format'
import { useFocusTrap } from '@/composables/useFocusTrap'
import StarRating from '@/components/atoms/StarRating.vue'
import SeasonChecklist from '@/components/molecules/SeasonChecklist.vue'

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
const seasonsWatched = ref<number[]>(props.item.seasons_watched || [])

const isTvShow = computed(() => props.item.content_type === 'tv_show' && props.item.total_seasons)

// Auto-derive TV status from season checklist
watch(seasonsWatched, (watched) => {
  if (!isTvShow.value) return
  const total = props.item.total_seasons!
  if (watched.length === 0) {
    status.value = 'unread'
  } else if (watched.length >= total) {
    status.value = 'completed'
  } else {
    status.value = 'currently_consuming'
  }
})

function save() {
  const data: ItemEditRequest = {
    status: status.value,
    rating: rating.value,
    review: review.value || null,
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
        <label for="edit-status">Status</label>
        <select id="edit-status" v-model="status">
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
          v-model="seasonsWatched"
          :total-seasons="item.total_seasons!"
        />
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
