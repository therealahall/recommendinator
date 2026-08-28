<script setup lang="ts">
import { computed, ref } from 'vue'

const props = defineProps<{
  inputId: string
  label: string
  file: File | null
}>()

// A drop is the one path with nothing to announce it: the native input keeps
// the name it was given.
const emit = defineEmits<{
  'update:file': [file: File | null, dropped: boolean]
}>()

const dragging = ref(false)
// dragenter/dragleave fire again for every child the pointer crosses, so a
// plain boolean flickers off the moment the pointer reaches the label.
let dragDepth = 0

function onDragEnter(): void {
  dragDepth += 1
  dragging.value = true
}

function onDragLeave(): void {
  dragDepth = Math.max(0, dragDepth - 1)
  if (dragDepth === 0) dragging.value = false
}

function onDrop(event: DragEvent): void {
  dragDepth = 0
  dragging.value = false
  const dropped = event.dataTransfer?.files?.[0]
  if (dropped) emit('update:file', dropped, true)
}

function onChange(event: Event): void {
  const chosen = (event.target as HTMLInputElement).files?.[0] ?? null
  emit('update:file', chosen, false)
}

// The drop never reaches the native input — assigning a FileList to it is not
// portable — so this line, not the input's own value, is what names a dropped
// file.
const selection = computed(() =>
  props.file ? `Selected file: ${props.file.name}` : 'No file selected yet.',
)
</script>

<template>
  <div
    class="drop-zone"
    :class="{ 'drop-zone-over': dragging }"
    @dragenter.prevent="onDragEnter"
    @dragover.prevent
    @dragleave.prevent="onDragLeave"
    @drop.prevent="onDrop"
  >
    <label class="drop-zone-label" :for="inputId">{{ label }}</label>
    <input
      :id="inputId"
      type="file"
      class="drop-zone-input"
      :aria-describedby="`${inputId}-hint ${inputId}-selection`"
      @change="onChange"
    />
    <p :id="`${inputId}-hint`" class="drop-zone-hint">
      Or drag a file onto this panel.
    </p>
    <p
      :id="`${inputId}-selection`"
      class="drop-zone-selection"
      data-testid="drop-zone-selection"
    >{{ selection }}</p>
  </div>
</template>

<style scoped>
.drop-zone {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4);
  border: 2px dashed var(--border-default);
  border-radius: var(--radius-lg);
  background: var(--bg-elevated);
}

/* Drag feedback only. Nothing here is the sole carrier of information: the
   selection line below states what was picked either way. */
.drop-zone-over {
  border-color: var(--accent);
  border-style: solid;
  background: var(--bg-active);
}

.drop-zone-label {
  font-size: var(--text-sm);
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--text-primary);
}

.drop-zone-input {
  font: inherit;
  font-size: var(--text-sm);
  color: var(--text-primary);
  max-width: 100%;
}

.drop-zone-input::file-selector-button {
  margin-right: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--border-interactive);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  color: var(--text-primary);
  font: inherit;
  font-size: var(--text-sm);
  cursor: pointer;
}

.drop-zone-input:focus-visible {
  outline: 2px solid var(--border-focus);
  outline-offset: 2px;
}

.drop-zone-hint {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.drop-zone-selection {
  margin: 0;
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--text-primary);
  overflow-wrap: anywhere;
}
</style>
