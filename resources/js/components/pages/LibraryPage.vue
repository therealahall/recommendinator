<script setup lang="ts">
import { onMounted, ref, watch, onUnmounted } from 'vue'
import { useLibraryStore } from '@/stores/library'
import { useAppStore } from '@/stores/app'
import LibraryFilters from '@/components/organisms/LibraryFilters.vue'
import LibraryCard from '@/components/molecules/LibraryCard.vue'
import EditModal from '@/components/molecules/EditModal.vue'

const lib = useLibraryStore()
const app = useAppStore()
const sentinel = ref<HTMLDivElement | null>(null)
const editTrigger = ref<HTMLElement | null>(null)

function onEdit(dbId: number) {
  const active = document.activeElement
  editTrigger.value = active instanceof HTMLElement && active !== document.body ? active : null
  lib.openEdit(dbId)
}

function onCloseEdit() {
  lib.closeEdit()
  editTrigger.value?.focus()
}

let observer: IntersectionObserver | null = null

onMounted(() => {
  lib.resetAndLoad()
  setupInfiniteScroll()
})

watch(() => app.currentUserId, () => {
  lib.resetAndLoad()
})

function setupInfiniteScroll() {
  observer = new IntersectionObserver(
    (entries) => {
      if (entries[0].isIntersecting) {
        lib.loadMore()
      }
    },
    { rootMargin: '200px' },
  )
  // Will observe sentinel once it renders
  watch(sentinel, (el) => {
    if (el && observer) observer.observe(el)
  })
}

onUnmounted(() => {
  observer?.disconnect()
  lib.cleanup()
})

</script>

<template>
  <div>
    <div class="page-header">
      <h2>Library</h2>
      <p class="page-description">Browse and manage your content collection.</p>
    </div>

    <LibraryFilters
      :type-filter="lib.typeFilter"
      :status-filter="lib.statusFilter"
      :enrichment-filter="lib.enrichmentFilter"
      :show-ignored="lib.showIgnored"
      :needs-rating="lib.needsRating"
      :search-query="lib.searchQuery"
      :search-loading="lib.searchLoading"
      @filter-change="lib.setFilter"
      @export="lib.exportLibrary"
    />

    <p class="sr-only" role="status" aria-live="polite">{{ lib.searchAnnouncement }}</p>

    <div v-if="lib.error" class="status-bar error" role="alert" style="display: block">
      Failed to load library: {{ lib.error }}
    </div>

    <div aria-live="polite" aria-atomic="true">
      <div
        v-if="lib.items.length === 0 && !lib.loading && lib.searchQuery"
        class="empty-state empty-state-search"
      >
        <svg class="empty-state-icon" aria-hidden="true" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="11" cy="11" r="8" />
          <line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
        <p class="empty-state-title">No items match “{{ lib.searchQuery }}”</p>
        <p class="empty-state-hint">Try a different title, or check your spelling.</p>
        <button class="btn btn-secondary" @click="lib.setFilter('search', '')">Clear search</button>
      </div>

      <div v-else-if="lib.items.length === 0 && !lib.loading" class="empty-state">
        <template v-if="lib.needsRating">Nothing needs a rating. All your completed items are rated.</template>
        <template v-else>No items found. Try syncing your sources.</template>
      </div>
    </div>

    <div v-if="lib.items.length > 0" class="library-grid">
      <LibraryCard
        v-for="(item, index) in lib.items"
        :key="item.db_id ?? item.id ?? index"
        :item="item"
        @edit="onEdit"
        @toggle-ignore="(dbId: number, ignored: boolean) => lib.toggleIgnore(dbId, ignored)"
      />
    </div>

    <div v-if="lib.loading" class="library-load-more">
      <span class="spinner" /> Loading...
    </div>

    <div v-if="!lib.hasMore && lib.items.length > 0" class="library-end">
      All {{ lib.totalLoaded }} items loaded
    </div>

    <!-- Infinite scroll sentinel -->
    <div v-if="lib.hasMore && !lib.loading" ref="sentinel" />

    <!-- Edit Modal -->
    <EditModal
      v-if="lib.editingItem"
      :item="lib.editingItem"
      :saving="lib.editSaving"
      @save="lib.saveEdit"
      @close="onCloseEdit"
    />
  </div>
</template>
