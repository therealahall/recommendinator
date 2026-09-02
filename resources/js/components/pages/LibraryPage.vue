<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch, onUnmounted } from 'vue'
import { RouterLink } from 'vue-router'
import { useLibraryStore } from '@/stores/library'
import { useDataStore } from '@/stores/data'
import { rescueFocus } from '@/utils/focus'
import LibraryFilters from '@/components/organisms/LibraryFilters.vue'
import LibraryCard from '@/components/molecules/LibraryCard.vue'
import EditModal from '@/components/molecules/EditModal.vue'
import AppIcon from '@/components/atoms/AppIcon.vue'

const lib = useLibraryStore()
const data = useDataStore()
const sentinel = ref<HTMLDivElement | null>(null)
const editTrigger = ref<HTMLElement | null>(null)
const heading = ref<HTMLElement | null>(null)

// Not showIgnored: it only ever adds rows, so it cannot be what emptied the list.
const filtered = computed(() =>
  Boolean(lib.typeFilter || lib.statusFilter || lib.enrichmentFilter),
)

const emptyTitle = computed(() => {
  if (lib.needsRating) return 'Nothing needs a rating'
  return filtered.value ? 'Nothing matches these filters' : 'Your library is empty'
})

const emptyHint = computed(() => {
  if (lib.needsRating) return 'Every item you have finished already carries one.'
  return filtered.value
    ? 'Widen the filters above, or clear them to see the whole library.'
    : 'Sync a source or import a file, and what it brings back lands here.'
})

// Each empty-state action destroys the block that holds it, so the keyboard
// lands on the heading rather than <body> (WCAG 2.4.3).
async function onClearSearch() {
  lib.setFilter('search', '')
  await nextTick()
  rescueFocus(heading.value)
}

async function onClearFilters() {
  await lib.clearFilters()
  await nextTick()
  rescueFocus(heading.value)
}

async function onRestoreEnrichment(dbId: number) {
  lib.editError = ''
  try {
    await data.restoreItemEnrichment(dbId)
    await lib.openEdit(dbId)
  } catch (err) {
    lib.editError = err instanceof Error ? err.message : 'Failed to restore enrichment'
  }
}

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

function setupInfiniteScroll() {
  observer = new IntersectionObserver(
    (entries) => {
      if (entries[0].isIntersecting) {
        lib.loadMore()
      }
    },
    { rootMargin: '200px' },
  )
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
    <div class="page-header library-header">
      <div class="library-heading">
        <h2 ref="heading" tabindex="-1">Library</h2>
        <p class="page-description">Browse and manage your content collection.</p>
      </div>
      <RouterLink class="btn btn-secondary library-duplicates" :to="{ name: 'duplicates' }">
        <AppIcon name="copy" />
        Review duplicates
      </RouterLink>
    </div>

    <LibraryFilters
      :type-filter="lib.typeFilter"
      :status-filter="lib.statusFilter"
      :enrichment-filter="lib.enrichmentFilter"
      :show-ignored="lib.showIgnored"
      :needs-rating="lib.needsRating"
      :sort-by="lib.sortBy"
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
        class="state state--empty"
      >
        <span class="state-mark"><AppIcon name="search" :size="20" /></span>
        <p class="state-title">No items match “{{ lib.searchQuery }}”</p>
        <p class="state-hint">Try a different title, or check your spelling.</p>
        <div class="state-actions">
          <button
            type="button"
            class="btn btn-primary"
            data-testid="library-clear-search"
            @click="onClearSearch"
          >Clear search</button>
        </div>
      </div>

      <div
        v-else-if="lib.items.length === 0 && !lib.loading"
        class="state state--empty"
        data-testid="library-empty"
      >
        <span class="state-mark">
          <AppIcon :name="lib.needsRating ? 'star' : filtered ? 'search' : 'book'" :size="20" />
        </span>
        <p class="state-title">{{ emptyTitle }}</p>
        <p class="state-hint">{{ emptyHint }}</p>
        <div class="state-actions">
          <button
            v-if="lib.needsRating || filtered"
            type="button"
            class="btn btn-primary"
            data-testid="library-clear-filters"
            @click="onClearFilters"
          >Show everything</button>
          <RouterLink v-else class="btn btn-primary" :to="{ name: 'data' }">
            Sync a source
          </RouterLink>
        </div>
      </div>
    </div>

    <div v-if="lib.items.length > 0" class="library-grid">
      <LibraryCard
        v-for="(item, index) in lib.items"
        :key="item.db_id ?? index"
        :item="item"
        @edit="onEdit"
        @toggle-ignore="(dbId: number, ignored: boolean) => lib.toggleIgnore(dbId, ignored)"
      />
    </div>

    <div v-if="lib.loading" class="state state--loading">
      <span class="spinner" /> Loading…
    </div>

    <div v-if="!lib.hasMore && lib.items.length > 0" class="library-end">
      All {{ lib.totalLoaded }} items loaded
    </div>

    <div v-if="lib.hasMore && !lib.loading" ref="sentinel" />

    <EditModal
      v-if="lib.editingItem"
      :item="lib.editingItem"
      :saving="lib.editSaving"
      :save-error="lib.editError"
      @save="lib.saveEdit"
      @restore-enrichment="onRestoreEnrichment"
      @close="onCloseEdit"
    />
  </div>
</template>

<style scoped>
.library-header {
  display: flex;
  align-items: flex-start;
  gap: var(--space-4);
  flex-wrap: wrap;
}

.library-heading {
  flex: 1 1 16rem;
  min-width: 0;
}

.library-duplicates {
  min-height: 44px;
}
</style>
