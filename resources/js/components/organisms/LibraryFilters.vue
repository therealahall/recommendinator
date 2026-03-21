<script setup lang="ts">
import { ref } from 'vue'

const props = defineProps<{
  typeFilter: string
  statusFilter: string
  showIgnored: boolean
}>()

const emit = defineEmits<{
  filterChange: [key: 'type' | 'status' | 'showIgnored', value: string | boolean]
  export: [format: 'csv' | 'json']
}>()

const exportFormat = ref('csv')

const statusLabels: Record<string, Record<string, string>> = {
  unread: { book: 'Unread', movie: 'Unwatched', tv_show: 'Unwatched', video_game: 'Unplayed', default: 'Not Started' },
}

function getUnreadLabel(): string {
  if (props.typeFilter && statusLabels.unread[props.typeFilter]) return statusLabels.unread[props.typeFilter]
  return statusLabels.unread.default
}
</script>

<template>
  <div class="card">
    <div class="library-filters">
      <div class="form-group">
        <label for="libType">Type</label>
        <select id="libType" :value="typeFilter" @change="emit('filterChange', 'type', ($event.target as HTMLSelectElement).value)">
          <option value="">All Types</option>
          <option value="book">Book</option>
          <option value="movie">Movie</option>
          <option value="tv_show">TV Show</option>
          <option value="video_game">Video Game</option>
        </select>
      </div>
      <div class="form-group">
        <label for="libStatus">Status</label>
        <select id="libStatus" :value="statusFilter" @change="emit('filterChange', 'status', ($event.target as HTMLSelectElement).value)">
          <option value="">All Statuses</option>
          <option value="unread">{{ getUnreadLabel() }}</option>
          <option value="currently_consuming">In Progress</option>
          <option value="completed">Completed</option>
        </select>
      </div>
      <div class="form-group">
        <label class="checkbox-label">
          <input type="checkbox" :checked="showIgnored" @change="emit('filterChange', 'showIgnored', ($event.target as HTMLInputElement).checked)">
          Show ignored
        </label>
      </div>
      <div class="library-export-group">
        <div class="form-group">
          <label for="exportFormat">Format</label>
          <select id="exportFormat" v-model="exportFormat">
            <option value="csv">CSV</option>
            <option value="json">JSON</option>
          </select>
        </div>
        <button
          class="btn btn-secondary"
          :disabled="!typeFilter"
          :title="typeFilter ? 'Export library items' : 'Select a content type to export'"
          @click="emit('export', exportFormat as 'csv' | 'json')"
        >Export</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.library-filters {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-4);
  align-items: flex-end;
}

.library-export-group {
  display: flex;
  gap: var(--space-2);
  align-items: flex-end;
  margin-left: auto;
}

.checkbox-label {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
  color: var(--text-secondary);
  cursor: pointer;
  padding-top: var(--space-5);
}
</style>
