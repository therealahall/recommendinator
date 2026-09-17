<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import type { FieldWriters } from '@/types/api'
import { capitalize } from '@/utils/format'

const props = defineProps<{ fieldWriters: FieldWriters[] }>()

const emit = defineEmits<{
  choose: [field: string, writer: string | null]
}>()

//: The band the operator's own entry is recorded under, which outranks every
//: choice: it is the option in force, not a writer to follow.
const HELD_BAND = 'manual'

interface WriterRow {
  field: string
  label: string
  /** Their own entry, '' where they have made none for this field. */
  held: string
  selected: string
  /** Whether a choice stands for the default order to take back. */
  clearable: boolean
  options: { writer: string; label: string }[]
}

function labelFor(field: string) {
  return capitalize(field.replace(/_/g, ' '))
}

// Pre-computed rather than formatted in the v-for, which re-runs every render.
const rows = computed<WriterRow[]>(() =>
  props.fieldWriters
    .filter((offered) => offered.writers.length)
    .map((offered) => {
      const held = offered.writers.find((one) => one.band === HELD_BAND)?.writer ?? ''
      return {
        field: offered.field,
        label: labelFor(offered.field),
        held,
        selected:
          held || (offered.writers.find((one) => one.writer === offered.chosen)?.writer ?? ''),
        // A clear releases nothing where no choice stands, and the host refuses
        // one, so offering it is offering a button that can only fail.
        clearable: Boolean(offered.chosen) || !held,
        options: offered.writers.map((one) => ({
          writer: one.writer,
          label:
            one.band === HELD_BAND
              ? `Your own entry — ${one.value}`
              : `${one.writer} — ${one.value}`,
        })),
      }
    }),
)

// Said rather than dropped: a field two writers filled but no choice can move
// would otherwise read as one nobody has stated.
const settled = computed(() =>
  props.fieldWriters.filter((offered) => offered.note).map((offered) => offered.note),
)

const hint = computed(() => {
  if (rows.value.length) {
    return 'Picking a writer follows their later corrections. Your own entry wins for a field until you pick one.'
  }
  return settled.value.length ? '' : 'No writer has stated a field on this item yet.'
})

const note = ref('')

function noteFor(offered: FieldWriters) {
  const label = labelFor(offered.field)
  if (!offered.chosen) return `${label} is back to the default order.`
  return `${label} now follows ${offered.chosen}.`
}

// Announced from what came back rather than from what was picked: a refused
// choice leaves the field following whatever it followed before.
watch(
  () => props.fieldWriters,
  (now, before) => {
    const moved = now.find((offered) =>
      before?.some((was) => was.field === offered.field && was.chosen !== offered.chosen),
    )
    if (moved) note.value = noteFor(moved)
  },
)

function onChoose(row: WriterRow, event: Event) {
  const picked = (event.target as HTMLSelectElement).value
  // Their own entry is already in force, and a choice recorded under it would
  // release the very hold the option names.
  if (picked && picked === row.held) return
  emit('choose', row.field, picked || null)
}
</script>

<template>
  <div class="edit-field">
    <p class="edit-modal-note">{{ hint }}</p>
    <ul class="edit-manual-list" aria-label="Where each field comes from">
      <li v-for="row in rows" :key="row.field" class="edit-manual-held">
        <label :for="`edit-writer-${row.field}`">{{ row.label }}</label>
        <select
          :id="`edit-writer-${row.field}`"
          class="field edit-writer-pick"
          :value="row.selected"
          @change="onChoose(row, $event)"
        >
          <option v-if="row.clearable" value="">Default order</option>
          <option v-for="option in row.options" :key="option.writer" :value="option.writer">
            {{ option.label }}
          </option>
        </select>
      </li>
      <li v-for="said in settled" :key="said" class="edit-manual-held">{{ said }}</li>
    </ul>
    <!-- Mounted whether or not it has anything to say: a region inserted
         already populated reads as content rather than a status change. -->
    <p id="edit-writer-note" class="edit-modal-note" role="status" aria-live="polite">{{ note }}</p>
  </div>
</template>
