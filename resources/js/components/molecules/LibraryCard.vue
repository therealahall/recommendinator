<script setup lang="ts">
import type { ContentItemResponse } from '@/types/api'
import { formatContentType, formatStatusForContentType } from '@/utils/format'

const props = defineProps<{
  item: ContentItemResponse
}>()

const emit = defineEmits<{
  edit: [dbId: number]
  toggleIgnore: [dbId: number, ignored: boolean]
}>()

function renderStars(rating: number): string {
  return '★'.repeat(rating)
}

function statusClass(status: string): string {
  const valid = ['unread', 'currently_consuming', 'completed']
  return valid.includes(status) ? status : 'unknown'
}
</script>

<template>
  <div class="library-item" :class="{ ignored: item.ignored }">
    <h3>{{ item.title }}</h3>
    <div v-if="item.author" class="item-author">{{ item.author }}</div>
    <div class="library-item-badges">
      <span class="badge badge-type">{{ formatContentType(item.content_type) }}</span>
      <span class="badge badge-status" :class="statusClass(item.status)">
        {{ formatStatusForContentType(item.status, item.content_type) }}
      </span>
      <span v-if="item.rating" class="badge badge-rating">{{ renderStars(item.rating) }}</span>
      <span v-if="item.ignored" class="badge badge-ignored">Ignored</span>
    </div>
    <div v-if="item.db_id" class="library-item-actions">
      <button class="btn btn-small btn-secondary" @click="emit('edit', item.db_id!)">Edit</button>
      <button
        class="btn btn-small"
        :class="item.ignored ? 'btn-unignore' : 'btn-ignore'"
        @click="emit('toggleIgnore', item.db_id!, !item.ignored)"
      >
        {{ item.ignored ? 'Unignore' : 'Ignore' }}
      </button>
    </div>
  </div>
</template>
