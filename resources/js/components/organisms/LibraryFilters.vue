<script setup lang="ts">
import { ref, computed, nextTick, onMounted, onUnmounted } from 'vue'
import TypePills from '@/components/atoms/TypePills.vue'
import TypeSelect from '@/components/atoms/TypeSelect.vue'
import ToggleSwitch from '@/components/atoms/ToggleSwitch.vue'
import SearchInput from '@/components/atoms/SearchInput.vue'
import { MAX_SEARCH_LENGTH, SORT_OPTIONS } from '@/constants/library'
import { rescueFocus } from '@/utils/focus'

const props = defineProps<{
  typeFilter: string
  statusFilter: string
  enrichmentFilter: string
  showIgnored: boolean
  needsRating: boolean
  sortBy: string
  searchQuery: string
  searchLoading: boolean
}>()

const emit = defineEmits<{
  filterChange: [
    key: 'type' | 'status' | 'enrichment' | 'showIgnored' | 'search' | 'needsRating' | 'sort',
    value: string | boolean,
  ]
  export: [format: 'csv' | 'json']
}>()

const exportOpen = ref(false)
const dropdownRef = ref<HTMLElement | null>(null)
const exportTrigger = ref<HTMLElement | null>(null)

const statusLabels: Record<string, Record<string, string>> = {
  unread: { book: 'Unread', movie: 'Unwatched', tv_show: 'Unwatched', video_game: 'Unplayed', default: 'Not Started' },
}

const unreadLabel = computed(() =>
  statusLabels.unread[props.typeFilter] ?? statusLabels.unread.default
)

const statusLockNotice = computed(() =>
  props.needsRating
    ? 'Status filter removed. Status is locked to Completed while Needs rating is on.'
    : ''
)

const exportScope = computed(() =>
  props.typeFilter
    ? 'Exports every item of this type. Your other filters do not apply.'
    : 'Exports your whole library, every type. Your other filters do not apply.'
)

// Every way out unmounts the menu holding the pressed button, so the trigger
// takes the keyboard back — unless the click that closed it landed on a control.
function closeExport() {
  if (!exportOpen.value) return
  exportOpen.value = false
  void nextTick(() => rescueFocus(exportTrigger.value))
}

function doExport(format: 'csv' | 'json') {
  closeExport()
  emit('export', format)
}

function onClickOutside(e: MouseEvent) {
  if (e.target === null) return
  if (dropdownRef.value && !dropdownRef.value.contains(e.target as Node)) {
    closeExport()
  }
}

function onKeyDown(e: KeyboardEvent) {
  if (e.key === 'Escape') closeExport()
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
        :maxlength="MAX_SEARCH_LENGTH"
        placeholder="Search by title, creator or series"
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
          class="field toolbar-select lib-type-select"
          :model-value="typeFilter"
          @update:model-value="emit('filterChange', 'type', $event)"
        />
        <select
          v-if="!needsRating"
          class="field toolbar-select"
          aria-label="Status"
          :value="statusFilter"
          @change="emit('filterChange', 'status', ($event.target as HTMLSelectElement).value)"
        >
          <option value="">All Statuses</option>
          <option value="unread">{{ unreadLabel }}</option>
          <option value="currently_consuming">In Progress</option>
          <option value="completed">Completed</option>
        </select>
        <span v-else class="help-text">Status: Completed while Needs rating is on.</span>
        <select class="field toolbar-select" aria-label="Enrichment" :value="enrichmentFilter" @change="emit('filterChange', 'enrichment', ($event.target as HTMLSelectElement).value)">
          <option value="">All Items</option>
          <option value="enriched">Enriched</option>
          <option value="not_enriched">Not enriched</option>
        </select>
        <select class="field toolbar-select" aria-label="Sort" :value="sortBy" @change="emit('filterChange', 'sort', ($event.target as HTMLSelectElement).value)">
          <option v-for="option in SORT_OPTIONS" :key="option.value" :value="option.value">
            Sort: {{ option.label }}
          </option>
        </select>
      </div>

      <p
        class="sr-only"
        data-testid="status-lock-announcement"
        role="status"
        aria-live="polite"
        aria-atomic="true"
      >{{ statusLockNotice }}</p>

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
            ref="exportTrigger"
            class="btn btn-secondary"
            title="Export library items"
            :aria-expanded="exportOpen"
            :aria-controls="exportOpen ? 'export-menu' : undefined"
            @click="exportOpen = !exportOpen"
          >
            Export
          </button>
          <div v-if="exportOpen" id="export-menu" class="dropdown-menu">
            <p id="export-scope" class="export-scope">{{ exportScope }}</p>
            <button class="dropdown-menu-item" aria-describedby="export-scope" @click="doExport('csv')">CSV</button>
            <button class="dropdown-menu-item" aria-describedby="export-scope" @click="doExport('json')">JSON</button>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.export-scope {
  max-width: 16rem;
  margin: 0;
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-xs);
  color: var(--text-secondary);
}

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

.lib-filter-row .help-text {
  margin: 0;
}

.lib-type-select {
  display: none;
}

.toolbar-right {
  margin-left: auto;
}

@media (max-width: 640px) {
  /* Under the top strip, not at the viewport edge: the strip is sticky too and
     wins the stacking order, so top: 0 would park these filters behind it. */
  .card {
    position: sticky;
    top: var(--topbar-h);
    z-index: var(--z-sticky);
  }

  .lib-pills,
  .toolbar-divider {
    display: none;
  }

  .lib-filter-row,
  .lib-actions-row {
    width: 100%;
    flex-wrap: wrap;
    gap: var(--space-2);
  }

  /* A select cannot shrink past its widest option ("All Statuses") plus
     .toolbar-select's arrow padding, so three of them widened the page itself.
     An explicit basis puts two on a row and lets the third fill the next. */
  .lib-filter-row .toolbar-select {
    flex: 1 1 calc(50% - var(--space-2));
    min-width: 0;
  }

  .lib-type-select {
    display: block;
  }

  .toolbar-right {
    margin-left: 0;
  }
}
</style>
