<script setup lang="ts">
import { computed, useId } from 'vue'
import type { ContentItemResponse } from '@/types/api'
import AppIcon from '@/components/atoms/AppIcon.vue'
import ItemCover from '@/components/atoms/ItemCover.vue'
import { contentTypeGlyph } from '@/constants/contentTypes'
import { formatContentType, formatSeries, formatStatusForContentType } from '@/utils/format'

const props = defineProps<{
  item: ContentItemResponse
}>()

const series = computed(() => formatSeries(props.item.series, props.item.series_index))
const glyph = computed(() => contentTypeGlyph(props.item.content_type))

// A merge can leave two ids from one source, but that is still one place to go
// and fix the data, so the source is named once — keyed on the id, not the name.
const sources = computed(() => {
  const named = new Map<string, string>()
  for (const entry of props.item.external_ids) {
    named.set(entry.source, entry.display_name)
  }
  return [...named].map(([id, name]) => ({ id, name }))
})

const sourcesLabelId = useId()

const emit = defineEmits<{
  edit: [dbId: number]
  toggleIgnore: [dbId: number, ignored: boolean]
}>()

// A status the API has not taught this build about gets the neutral tone rather
// than the "completed" green the old fallthrough painted it.
const STATUS_TONES: Record<string, string> = {
  unread: 'warning',
  currently_consuming: 'accent',
  completed: 'success',
}

function statusTone(status: string): string | undefined {
  return STATUS_TONES[status]
}
</script>

<template>
  <div class="library-item" :class="{ ignored: item.ignored }">
    <div class="library-item-head">
      <ItemCover
        :cover-url="item.cover_url"
        :content-type="item.content_type"
        :title="item.title"
      />
      <div class="library-item-ident">
        <h3>
          <AppIcon :name="glyph" class="type-glyph" />
          <span class="sr-only">{{ formatContentType(item.content_type) }}. </span>{{ item.title }}
        </h3>
        <div v-if="series.shown" class="item-series">
          <span aria-hidden="true">{{ series.shown }}</span>
          <span class="sr-only">{{ series.spoken }}</span>
        </div>
        <div v-if="item.author" class="item-author">{{ item.author }}</div>
      </div>
    </div>
    <div class="library-meta">
      <span class="badge" :data-tone="statusTone(item.status)" data-testid="status-badge">
        {{ formatStatusForContentType(item.status, item.content_type) }}
      </span>
      <span v-if="!item.enriched" class="badge">Not enriched</span>
      <span v-if="item.ignored" class="badge" data-tone="warning">Ignored</span>
    </div>
    <div v-if="item.rating !== null" class="library-meta-secondary">
      <span v-if="item.rating !== null" class="rating-stars">
        <span aria-hidden="true">
          <span
            v-for="star in 5"
            :key="star"
            class="star"
            :class="star <= item.rating ? 'filled' : 'empty'"
          >{{ star <= item.rating ? '★' : '☆' }}</span>
        </span>
        <span class="value" aria-hidden="true">{{ item.rating }}/5</span>
        <span class="sr-only">Rated {{ item.rating }} out of 5</span>
      </span>
    </div>
    <div v-if="sources.length > 0" class="library-sources">
      <span :id="sourcesLabelId" class="library-sources-label">Data sources</span>
      <ul class="library-source-list" role="list" :aria-labelledby="sourcesLabelId">
        <li v-for="source in sources" :key="source.id">
          <span class="badge" data-testid="source-badge">{{ source.name }}</span>
        </li>
      </ul>
    </div>
    <div v-if="item.db_id" class="library-item-actions">
      <button
        class="btn btn-small btn-secondary"
        :aria-label="`Edit: ${item.title}`"
        @click="emit('edit', item.db_id!)"
      >Edit</button>
      <button
        class="btn btn-small"
        :class="item.ignored ? 'btn-unignore' : 'btn-ignore'"
        :aria-label="`${item.ignored ? 'Unignore' : 'Ignore'}: ${item.title}`"
        @click="emit('toggleIgnore', item.db_id!, !item.ignored)"
      >
        {{ item.ignored ? 'Unignore' : 'Ignore' }}
      </button>
    </div>
  </div>
</template>
