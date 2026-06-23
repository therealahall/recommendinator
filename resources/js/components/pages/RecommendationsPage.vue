<script setup lang="ts">
import { ref, nextTick } from 'vue'
import { useRecommendationsStore } from '@/stores/recommendations'
import type { ItemEditRequest } from '@/types/api'
import RecControls from '@/components/organisms/RecControls.vue'
import RecCard from '@/components/molecules/RecCard.vue'
import EditModal from '@/components/molecules/EditModal.vue'

const recs = useRecommendationsStore()
const editTrigger = ref<HTMLElement | null>(null)
const recList = ref<HTMLElement | null>(null)
const heading = ref<HTMLElement | null>(null)

function onComplete(dbId: number) {
  const active = document.activeElement
  editTrigger.value = active instanceof HTMLElement && active !== document.body ? active : null
  // Intentionally not awaited: the triggering control must be captured before
  // openEdit's async GET resolves and the focus trap moves focus into the modal.
  recs.openEdit(dbId)
}

// Return focus when the modal closes. On cancel the triggering card is still in
// the DOM, so focus it. On a successful save the card is removed, leaving a
// detached trigger; in that case land focus on the next remaining card action
// or the page heading so keyboard users are not stranded at <body>.
function restoreFocus() {
  const trigger = editTrigger.value
  editTrigger.value = null
  if (trigger && document.contains(trigger)) {
    trigger.focus()
    return
  }
  const nextAction = recList.value?.querySelector<HTMLElement>('.btn-complete')
  ;(nextAction ?? heading.value)?.focus()
}

async function onCloseEdit() {
  recs.closeEdit()
  // Wait for the modal to unmount before restoring focus, matching the save
  // path so focus never lands on a control that is about to be torn down.
  await nextTick()
  restoreFocus()
}

async function onSave(dbId: number, data: ItemEditRequest) {
  try {
    await recs.markComplete(dbId, data)
  } catch {
    // A failed save keeps the modal open for retry (the store recorded the
    // error); leave focus in the form. Swallow here to avoid an unhandled
    // rejection — mirrors LibraryPage's save handler.
    return
  }
  // Success: the store closed the modal and removed the card. Move focus to the
  // next remaining card action or the heading so keyboard users are not stranded.
  await nextTick()
  restoreFocus()
}
</script>

<template>
  <div>
    <div class="page-header">
      <h2 ref="heading" tabindex="-1">Recommendations</h2>
      <p class="page-description">Get personalized recommendations based on your library and preferences.</p>
    </div>

    <RecControls />

    <div v-if="recs.error" class="status-bar error" style="display: block">
      Failed to load recommendations: {{ recs.error }}
    </div>

    <div v-if="recs.loading && recs.items.length === 0" class="empty-state">
      <span class="spinner" /> Loading recommendations...
    </div>

    <div v-if="recs.items.length === 0 && !recs.loading && !recs.error" class="empty-state">
      No recommendations yet. Click Generate to get started.
    </div>

    <div v-if="recs.items.length > 0" ref="recList">
      <RecCard
        v-for="(rec, index) in recs.items"
        :key="rec.db_id ?? index"
        :rec="rec"
        :rank="index + 1"
        :streaming="recs.streaming"
        @ignore="recs.ignoreItem($event)"
        @complete="onComplete"
      />
    </div>

    <!-- Edit Modal (shared with the library) -->
    <EditModal
      v-if="recs.editingItem"
      :item="recs.editingItem"
      :saving="recs.editSaving"
      @save="onSave"
      @close="onCloseEdit"
    />
  </div>
</template>
