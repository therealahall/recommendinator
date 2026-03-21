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
      :show-ignored="lib.showIgnored"
      @filter-change="lib.setFilter"
      @export="lib.exportLibrary"
    />

    <div v-if="lib.error" class="status-bar error" style="display: block">
      Failed to load library: {{ lib.error }}
    </div>

    <div v-if="lib.items.length === 0 && !lib.loading" class="empty-state">
      No items found. Try syncing your sources.
    </div>

    <div v-if="lib.items.length > 0" class="library-grid">
      <LibraryCard
        v-for="(item, index) in lib.items"
        :key="item.db_id ?? item.id ?? index"
        :item="item"
        @edit="(dbId: number) => lib.openEdit(dbId)"
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
      @close="lib.closeEdit()"
    />
  </div>
</template>
