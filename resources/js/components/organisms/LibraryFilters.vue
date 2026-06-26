<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import TypePills from '@/components/atoms/TypePills.vue'
import TypeSelect from '@/components/atoms/TypeSelect.vue'
import ToggleSwitch from '@/components/atoms/ToggleSwitch.vue'
import SearchInput from '@/components/atoms/SearchInput.vue'

const props = defineProps<{
  typeFilter: string
  statusFilter: string
  enrichmentFilter: string
  showIgnored: boolean
  needsRating: boolean
  searchQuery: string
  searchLoading: boolean
}>()

const emit = defineEmits<{
  filterChange: [
    key: 'type' | 'status' | 'enrichment' | 'showIgnored' | 'search' | 'needsRating',
    value: string | boolean,
  ]
  export: [format: 'csv' | 'json']
}>()

const exportOpen = ref(false)
const dropdownRef = ref<HTMLElement | null>(null)

const statusLabels: Record<string, Record<string, string>> = {
  unread: { book: 'Unread', movie: 'Unwatched', tv_show: 'Unwatched', video_game: 'Unplayed', default: 'Not Started' },
}

const unreadLabel = computed(() =>
  statusLabels.unread[props.typeFilter] ?? statusLabels.unread.default
)

function doExport(format: 'csv' | 'json') {
  exportOpen.value = false
  emit('export', format)
}

function onClickOutside(e: MouseEvent) {
  if (e.target === null) return
  if (dropdownRef.value && !dropdownRef.value.contains(e.target as Node)) {
    exportOpen.value = false
  }
}

function onKeyDown(e: KeyboardEvent) {
  if (e.key === 'Escape') exportOpen.value = false
}

onMounted(() => {
  document.addEventListener('click', onClickOutside)
  document.addEventListener('keydown', onKeyDown)
})

onUnmounted(() => {
  document.removeEventListener('click', onClickOutside)
  document.removeEventListener('keydown', onKeyDown)
})
</script>

<template>
  <div class="card">
    <div class="library-toolbar">
      <SearchInput
        class="lib-search"
        :model-value="searchQuery"
        :loading="searchLoading"
        placeholder="Search by title or creator"
        @update:model-value="emit('filterChange', 'search', $event)"
      />

      <!-- Desktop: Type pills -->
      <TypePills
        class="lib-pills"
        :model-value="typeFilter"
        @update:model-value="emit('filterChange', 'type', $event)"
      />

      <div class="toolbar-divider" />

      <!-- Type select + Status select (mobile: row 1; desktop: inline in toolbar) -->
      <div class="lib-filter-row">
        <TypeSelect
          class="toolbar-select lib-type-select"
          :model-value="typeFilter"
          @update:model-value="emit('filterChange', 'type', $event)"
        />
        <select
          class="toolbar-select"
          aria-label="Status"
          :value="needsRating ? 'completed' : statusFilter"
          :disabled="needsRating"
          :aria-describedby="needsRating ? 'status-locked-hint' : undefined"
          @change="emit('filterChange', 'status', ($event.target as HTMLSelectElement).value)"
        >
          <option value="">All Statuses</option>
          <option value="unread">{{ unreadLabel }}</option>
          <option value="currently_consuming">In Progress</option>
          <option value="completed">Completed</option>
        </select>
        <span v-if="needsRating" id="status-locked-hint" class="sr-only">Locked to Completed while Needs rating is on.</span>
        <select class="toolbar-select" aria-label="Enrichment" :value="enrichmentFilter" @change="emit('filterChange', 'enrichment', ($event.target as HTMLSelectElement).value)">
          <option value="">All Items</option>
          <option value="enriched">Enriched</option>
          <option value="not_enriched">Not enriched</option>
        </select>
      </div>

      <div class="toolbar-divider" />

      <!-- Ignored toggle + Export (mobile: row 2; desktop: inline in toolbar) -->
      <div class="lib-actions-row">
        <ToggleSwitch
          :model-value="needsRating"
          label="Needs rating"
          @update:model-value="emit('filterChange', 'needsRating', $event)"
        />
        <ToggleSwitch
          :model-value="showIgnored"
          label="Show ignored"
          @update:model-value="emit('filterChange', 'showIgnored', $event)"
        />
        <div ref="dropdownRef" class="dropdown-wrap toolbar-right">
          <button
            class="btn btn-secondary"
            title="Export library items"
            :aria-expanded="exportOpen"
            @click="exportOpen = !exportOpen"
          >
            Export
          </button>
          <div v-if="exportOpen" class="dropdown-menu">
            <button class="dropdown-menu-item" @click="doExport('csv')">CSV</button>
            <button class="dropdown-menu-item" @click="doExport('json')">JSON</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.library-toolbar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.lib-search {
  flex: 1 1 100%;
  order: -1;
}

.lib-filter-row,
.lib-actions-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.lib-type-select {
  display: none;
}

.toolbar-right {
  margin-left: auto;
}

@media (max-width: 640px) {
  .card {
    position: sticky;
    top: 0;
    z-index: 20;
  }

  .lib-pills,
  .toolbar-divider {
    display: none;
  }

  .lib-filter-row,
  .lib-actions-row {
    width: 100%;
    gap: var(--space-2);
  }

  .lib-type-select {
    display: block;
  }

  .toolbar-right {
    margin-left: 0;
  }
}
</style>
