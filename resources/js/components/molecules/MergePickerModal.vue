<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import type { ContentItemResponse } from '@/types/api'
import { MAX_SEARCH_LENGTH } from '@/constants/library'
import { formatContentType } from '@/utils/format'
import { rescueFocus } from '@/utils/focus'
import ItemCover from '@/components/atoms/ItemCover.vue'
import ModalDialog from '@/components/atoms/ModalDialog.vue'
import ConfirmPanel from '@/components/molecules/ConfirmPanel.vue'

const props = defineProps<{
  anchor: ContentItemResponse
  query: string
  candidates: ContentItemResponse[]
  searching: boolean
  merging: boolean
  mergeError: string
}>()

const emit = defineEmits<{
  search: [query: string]
  merge: [survivorId: number, absorbedId: number]
  close: []
}>()

const chosen = ref<ContentItemResponse | null>(null)
const keepId = ref<number | null>(null)
const refusal = ref<HTMLElement | null>(null)
const searchBox = ref<HTMLElement | null>(null)
const sidesEl = ref<HTMLElement | null>(null)

const typeLabel = computed(() => formatContentType(props.anchor.content_type))

function describe(item: ContentItemResponse): string {
  const sources = item.external_ids.map((one) => one.display_name).join(', ')
  return [item.author, item.release_year, sources].filter(Boolean).join(' · ')
}

// Pre-computed rather than formatted in the v-for, which re-runs every render.
const rows = computed(() =>
  props.candidates.map((item) => ({ item, meta: describe(item) })),
)

const sides = computed(() => {
  const other = chosen.value
  if (other === null) return []
  return [
    { item: props.anchor, other, meta: describe(props.anchor) },
    { item: other, other: props.anchor, meta: describe(other) },
  ]
})

const pair = computed(() => {
  const other = chosen.value
  if (other === null || keepId.value === null) return null
  const keepsAnchor = keepId.value === props.anchor.db_id
  return {
    keep: keepsAnchor ? props.anchor : other,
    drop: keepsAnchor ? other : props.anchor,
  }
})

const resultsNote = computed(() => {
  if (!props.query.trim()) return ''
  if (props.searching) return 'Searching…'
  if (rows.value.length === 0) return `No other ${typeLabel.value} matches “${props.query}”`
  return rows.value.length === 1
    ? `1 match for “${props.query}”`
    : `${rows.value.length} matches for “${props.query}”`
})

watch(
  () => props.mergeError,
  async (said) => {
    if (!said) return
    await nextTick()
    rescueFocus(refusal.value)
  },
)

// Each step unmounts the button that left it, so the keyboard follows the step
// rather than dropping to <body> (WCAG 2.4.3).
async function choose(item: ContentItemResponse | null) {
  chosen.value = item
  keepId.value = null
  await nextTick()
  rescueFocus(
    item === null ? searchBox.value : sidesEl.value?.querySelector<HTMLElement>('button'),
  )
}

function chooseSurvivor(dbId: number | null) {
  if (!props.merging) keepId.value = dbId
}

function confirm() {
  if (props.merging || pair.value === null) return
  emit('merge', pair.value.keep.db_id!, pair.value.drop.db_id!)
}
</script>

<template>
  <ModalDialog labelled-by="merge-picker-title" wide @dismiss="emit('close')">
    <h3 id="merge-picker-title">Merge another item into “{{ anchor.title }}”</h3>
    <p class="merge-picker-note">
      For the same work held twice under different names. Only another
      {{ typeLabel }} can be merged with this one. Nothing is deleted, and every
      merge can be undone.
    </p>

    <template v-if="chosen === null">
      <div class="edit-field">
        <label for="merge-picker-search">Search the library</label>
        <input
          id="merge-picker-search"
          ref="searchBox"
          class="field"
          type="search"
          :value="query"
          :maxlength="MAX_SEARCH_LENGTH"
          placeholder="Title, creator or series…"
          @input="emit('search', ($event.target as HTMLInputElement).value)"
        >
      </div>
      <ul v-if="rows.length" class="merge-picker-results" role="list">
        <li v-for="row in rows" :key="row.item.db_id!" class="merge-picker-row">
          <ItemCover
            :cover-url="row.item.cover_url"
            :content-type="row.item.content_type"
            :title="row.item.title"
          />
          <div class="merge-picker-ident">
            <p class="merge-picker-title">{{ row.item.title }}</p>
            <p class="merge-picker-meta">{{ row.meta }}</p>
          </div>
          <button
            type="button"
            class="btn btn-secondary merge-picker-pick"
            @click="choose(row.item)"
          >Choose “{{ row.item.title }}”</button>
        </li>
      </ul>
    </template>

    <!-- Mounted while silent: inserted populated it reads as content (4.1.3). -->
    <p class="sr-only" role="status" aria-live="polite">{{ resultsNote }}</p>

    <template v-if="chosen !== null">
      <p class="merge-picker-note">
        Which one survives? The other is folded into it and keeps no row of its own.
      </p>
      <div ref="sidesEl" class="merge-picker-sides">
        <div v-for="side in sides" :key="side.item.db_id!" class="merge-picker-side">
          <p class="merge-picker-title">{{ side.item.title }}</p>
          <p class="merge-picker-meta">{{ side.meta }}</p>
          <button
            type="button"
            class="btn btn-secondary merge-picker-keep"
            :aria-disabled="merging || undefined"
            @click="chooseSurvivor(side.item.db_id)"
          >Keep “{{ side.item.title }}”, absorbing “{{ side.other.title }}”</button>
        </div>
      </div>
      <ConfirmPanel
        v-if="pair"
        :message="`Keep “${pair.keep.title}” and absorb “${pair.drop.title}”. “${pair.drop.title}” stops holding a row of its own until the merge is undone.`"
        :confirm-label="merging ? 'Merging…' : 'Merge'"
        cancel-label="Choose again"
        @cancel="chooseSurvivor(null)"
        @confirm="confirm"
      />
      <button
        v-else
        type="button"
        class="btn btn-ghost"
        @click="choose(null)"
      >Back to search</button>
    </template>

    <!-- Mounted while silent: inserted populated it reads as content (4.1.3). -->
    <p
      ref="refusal"
      class="state state--error merge-picker-error focus-fallback"
      role="alert"
      tabindex="-1"
    >{{ mergeError }}</p>

    <template #actions>
      <button class="btn btn-secondary" @click="emit('close')">Cancel</button>
    </template>
  </ModalDialog>
</template>

<style scoped>
.merge-picker-note {
  margin-top: var(--space-2);
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.merge-picker-results {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-3);
  padding: 0;
  list-style: none;
}

.merge-picker-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--bg-card);
}

.merge-picker-ident {
  flex: 1 1 10rem;
  min-width: 0;
}

.merge-picker-title {
  font-weight: var(--weight-semibold);
  color: var(--text-primary);
}

.merge-picker-meta {
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.merge-picker-sides {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--space-3);
  margin: var(--space-3) 0;
}

.merge-picker-side {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md);
  background: var(--bg-card);
}

/* The label carries both titles, which a phone cannot fit on one line, and
   shortening it would take the survivor out of the accessible name. */
.merge-picker-keep,
.merge-picker-pick {
  margin-top: auto;
  text-align: left;
  white-space: normal;
  overflow-wrap: anywhere;
}

.merge-picker-error:not(:empty) {
  margin-top: var(--space-3);
}
</style>
