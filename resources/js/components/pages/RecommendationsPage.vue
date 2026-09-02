<script setup lang="ts">
import { computed, ref, nextTick } from 'vue'
import { RouterLink } from 'vue-router'
import { useRecommendationsStore } from '@/stores/recommendations'
import { useDataStore } from '@/stores/data'
import { rescueFocus } from '@/utils/focus'
import { formatContentType } from '@/utils/format'
import type { ItemEditRequest } from '@/types/api'
import AppIcon from '@/components/atoms/AppIcon.vue'
import RecControls from '@/components/organisms/RecControls.vue'
import RecCard from '@/components/molecules/RecCard.vue'
import RecSetAside from '@/components/molecules/RecSetAside.vue'
import EditModal from '@/components/molecules/EditModal.vue'

const recs = useRecommendationsStore()
const data = useDataStore()
const editTrigger = ref<HTMLElement | null>(null)
const recList = ref<HTMLElement | null>(null)
const heading = ref<HTMLElement | null>(null)
const announcement = ref('')
const ignoreError = ref('')

const typeLabel = computed(() => formatContentType(recs.contentType).toLowerCase())
const ranLabel = computed(() => formatContentType(recs.ranType).toLowerCase())
const scopeMoved = computed(() => recs.ranType !== '' && recs.ranType !== recs.contentType)

function onRun() {
  if (recs.loading) return
  recs.fetch()
}

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

// The button that was pressed is replaced by its opposite in the same slot, so
// the keyboard follows it rather than dropping to <body> (WCAG 2.4.3).
async function setIgnored(dbId: number, title: string, value: boolean) {
  announcement.value = ''
  ignoreError.value = ''
  try {
    await recs.setIgnored(dbId, value)
  } catch (err) {
    ignoreError.value = err instanceof Error ? err.message : 'Failed to save'
    await nextTick()
    rescueFocus(recList.value?.querySelector<HTMLElement>('#rec-ignore-error'))
    return
  }
  announcement.value = value
    ? `Ignored “${title}”. Undo is beside it.`
    : `Restored “${title}”.`
  await nextTick()
  const testid = value ? `undo-ignore-${dbId}` : `ignore-btn-${dbId}`
  rescueFocus(recList.value?.querySelector<HTMLElement>(`[data-testid="${testid}"]`))
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

    <div v-if="recs.loading && recs.items.length === 0" class="state state--loading">
      <span class="spinner" /> Loading recommendations…
    </div>

    <div
      v-if="recs.items.length === 0 && !recs.loading && !recs.error"
      class="state state--empty"
      data-testid="recs-empty"
    >
      <span class="state-mark"><AppIcon :name="recs.hasRun ? 'search' : 'star'" :size="20" /></span>
      <p class="state-title">
        {{ recs.hasRun ? `No ${typeLabel} left to rank` : 'Nothing ranked yet' }}
      </p>
      <p class="state-hint">
        <template v-if="recs.hasRun">
          Every {{ typeLabel }} in the pool is finished, in progress or set
          aside. Recommendations come from what you have not consumed yet, so
          syncing a source or adding items is what gives the next run something
          to rank.
        </template>
        <template v-else>
          A run scores the items you have not consumed against the library you
          have already rated, and shows the signals behind every score.
        </template>
      </p>
      <div class="state-actions">
        <button
          type="button"
          class="btn btn-primary"
          data-testid="recs-empty-run"
          :aria-disabled="recs.loading || undefined"
          @click="onRun"
        >{{ recs.hasRun ? 'Rank again' : 'Rank my library' }}</button>
        <RouterLink v-if="recs.hasRun" class="btn btn-secondary" :to="{ name: 'data' }">
          Sync a source
        </RouterLink>
      </div>
    </div>

    <p v-if="recs.items.length > 0" class="run-line">
      <span><b>{{ recs.items.length }}</b> ranked · {{ ranLabel }}</span>
      <template v-if="scopeMoved">
        <span class="run-sep" aria-hidden="true">·</span>
        <span>the filter above scopes the next run</span>
      </template>
    </p>

    <div v-if="recs.items.length > 0" ref="recList">
      <!-- Mounted while silent: inserted populated they read as content (4.1.3). -->
      <p
        id="rec-ignore-error"
        class="state state--error rec-ignore-error focus-fallback"
        role="alert"
        tabindex="-1"
      >{{ ignoreError }}</p>
      <p class="sr-only" role="status" aria-live="polite">{{ announcement }}</p>

      <template v-for="(rec, index) in recs.items" :key="rec.db_id ?? index">
        <RecSetAside
          v-if="rec.db_id && recs.ignored.has(rec.db_id)"
          :db-id="rec.db_id"
          :title="rec.title"
          :rank="index + 1"
          @undo="setIgnored($event, rec.title, false)"
        />
        <RecCard
          v-else
          :rec="rec"
          :rank="index + 1"
          @ignore="setIgnored($event, rec.title, true)"
          @complete="onComplete"
        />
      </template>
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

<style scoped>
.run-line {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--space-1) var(--space-3);
  margin-bottom: var(--space-4);
  font-size: var(--text-sm);
  color: var(--text-muted);
}

.run-line b {
  color: var(--text-secondary);
  font-weight: var(--weight-semibold);
  font-variant-numeric: tabular-nums;
}

.run-sep {
  opacity: 0.6;
}

.rec-ignore-error {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--color-error-text);
}

.rec-ignore-error:not(:empty) {
  margin-bottom: var(--space-3);
}
</style>
