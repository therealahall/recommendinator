<script setup lang="ts">
import { ref, nextTick } from 'vue'
import { useRecommendationsStore } from '@/stores/recommendations'
import { useDataStore } from '@/stores/data'
import type { ItemEditRequest } from '@/types/api'
import RecControls from '@/components/organisms/RecControls.vue'
import RecCard from '@/components/molecules/RecCard.vue'
import EditModal from '@/components/molecules/EditModal.vue'

const recs = useRecommendationsStore()
const data = useDataStore()
const editTrigger = ref<HTMLElement | null>(null)
const recList = ref<HTMLElement | null>(null)
const heading = ref<HTMLElement | null>(null)

function onComplete(dbId: number) {
  const active = document.activeElement
  editTrigger.value = active instanceof HTMLElement && active !== document.body ? active : null
  // Not awaited: the trigger must be captured before the trap takes focus.
  recs.openEdit(dbId)
}

// A saved card is removed, leaving a detached trigger: focus then lands on the
// next card's action or the heading, never at <body>.
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
  // Let the modal unmount first, so focus never lands on a torn-down control.
  await nextTick()
  restoreFocus()
}

async function onRestoreEnrichment(dbId: number) {
  recs.editError = ''
  try {
    await data.restoreItemEnrichment(dbId)
    await recs.openEdit(dbId)
  } catch (err) {
    recs.editError = err instanceof Error ? err.message : 'Failed to restore enrichment'
  }
}

async function onSave(dbId: number, edits: ItemEditRequest) {
  try {
    await recs.markComplete(dbId, edits)
  } catch {
    // The dialog stays open on the refusal it now shows; swallowed here only
    // to avoid an unhandled rejection.
    return
  }
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

    <div v-if="recs.error" class="status-bar error" role="alert" style="display: block">
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
        @ignore="recs.ignoreItem($event)"
        @complete="onComplete"
      />
    </div>

    <EditModal
      v-if="recs.editingItem"
      :item="recs.editingItem"
      :saving="recs.editSaving"
      :save-error="recs.editError"
      initial-status="completed"
      @save="onSave"
      @restore-enrichment="onRestoreEnrichment"
      @close="onCloseEdit"
    />
  </div>
</template>
