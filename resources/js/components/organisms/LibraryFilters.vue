<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import TypePills from '@/components/atoms/TypePills.vue'
import TypeSelect from '@/components/atoms/TypeSelect.vue'
import ToggleSwitch from '@/components/atoms/ToggleSwitch.vue'

const props = defineProps<{
  typeFilter: string
  statusFilter: string
  showIgnored: boolean
}>()

const emit = defineEmits<{
  filterChange: [key: 'type' | 'status' | 'showIgnored', value: string | boolean]
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
        <select class="toolbar-select" aria-label="Status" :value="statusFilter" @change="emit('filterChange', 'status', ($event.target as HTMLSelectElement).value)">
          <option value="">All Statuses</option>
          <option value="unread">{{ unreadLabel }}</option>
          <option value="currently_consuming">In Progress</option>
          <option value="completed">Completed</option>
        </select>
      </div>

      <div class="toolbar-divider" />

      <!-- Ignored toggle + Export (mobile: row 2; desktop: inline in toolbar) -->
      <div class="lib-actions-row">
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
