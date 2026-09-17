<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { useDataStore } from '@/stores/data'
import { progressMilestone, truncate } from '@/utils/format'

const data = useDataStore()
const busy = ref(false)
const error = ref('')
const message = ref('')
const startButton = ref<HTMLElement | null>(null)
const errorNotice = ref<HTMLElement | null>(null)

const job = computed(() => data.rebuildJob)
const running = computed(() => job.value?.running === true)
const jobErrors = computed<string[]>(() => job.value?.errors ?? [])
const percent = computed(() => {
  const current = job.value
  if (!current || current.total_items === 0) return 0
  return (current.items_processed / current.total_items) * 100
})

const watchedRun = ref(false)
watch(
  running,
  (isRunning) => {
    if (isRunning) watchedRun.value = true
  },
  { immediate: true },
)

const progressAnnouncement = computed(() => {
  const current = job.value
  if (!current) return ''
  if (current.running) {
    const reached = progressMilestone(percent.value)
    return reached === 0
      ? 'Library rebuild running.'
      : `Library rebuild ${reached}% complete.`
  }
  if (!watchedRun.value) return ''
  if (current.cancelled) {
    return `Library rebuild stopped. ${current.items_changed} items changed.`
  }
  if (current.completed) {
    return `Library rebuild finished. ${current.items_changed} items changed.`
  }
  return 'Library rebuild stopped on an error.'
})

async function run(action: () => Promise<string>): Promise<void> {
  if (busy.value) return
  busy.value = true
  error.value = ''
  message.value = ''
  try {
    message.value = await action()
  } catch (err) {
    error.value = err instanceof Error ? err.message : 'Unknown error'
  } finally {
    busy.value = false
  }
  await nextTick()
  const focused = document.activeElement
  if (focused === null || focused === document.body || error.value) {
    ;(error.value ? errorNotice.value : startButton.value)?.focus()
  }
}

const onRebuild = () =>
  running.value
    ? Promise.resolve()
    : run(() => data.startRebuild().then(() => 'Library rebuild started.'))
const onStop = () => run(() => data.stopRebuild())
</script>

<template>
  <div class="card">
    <h3 class="section-title">Rebuild stored fields</h3>
    <p class="help-text">
      A rebuild re-resolves every item's stored fields from what each writer has
      already said — your sources, the metadata providers and your own edits. It
      makes no provider calls, and it is safe to stop and start again.
    </p>
    <p class="help-text">
      It also runs on its own when you reorder the providers or turn one on or off.
    </p>

    <div class="rebuild-status">
      <template v-if="job?.running">
        <span class="spinner" aria-hidden="true" />
        <span class="sr-only">Rebuilding: </span>
        {{ job.current_item ? truncate(job.current_item, 50) : 'Processing...' }}
        ({{ job.items_processed }}/{{ job.total_items }}
        - {{ Math.round(percent) }}%)
      </template>
    </div>

    <div class="rebuild-actions">
      <button
        v-if="running"
        type="button"
        class="btn btn-secondary"
        data-testid="library-rebuild-stop"
        :aria-disabled="busy || undefined"
        @click="onStop"
      >Stop</button>
      <button
        ref="startButton"
        type="button"
        class="btn btn-primary"
        data-testid="library-rebuild-start"
        :aria-disabled="busy || running || undefined"
        @click="onRebuild"
      >Rebuild</button>
    </div>

    <div v-if="jobErrors.length" class="state state--error rebuild-errors">
      <p id="rebuild-errors-title" class="rebuild-errors-title">
        Errors reported by this rebuild
      </p>
      <ul
        class="rebuild-errors-list"
        data-testid="library-rebuild-errors"
        role="list"
        aria-labelledby="rebuild-errors-title"
      >
        <li v-for="(line, index) in jobErrors" :key="index">{{ line }}</li>
      </ul>
    </div>

    <p
      ref="errorNotice"
      class="state state--error rebuild-error focus-fallback"
      data-testid="library-rebuild-error"
      role="alert"
      tabindex="-1"
    >{{ error }}</p>
    <p
      class="rebuild-message"
      data-testid="library-rebuild-message"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >{{ message }}</p>
    <p
      class="sr-only"
      data-testid="library-rebuild-progress-status"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >{{ progressAnnouncement }}</p>
  </div>
</template>

<style scoped>
.rebuild-status {
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

.rebuild-status:not(:empty) {
  margin-top: var(--space-3);
}

.rebuild-actions {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-top: var(--space-4);
}

.rebuild-errors {
  display: block;
  margin-top: var(--space-3);
}

.rebuild-errors-title {
  margin: 0 0 var(--space-1);
  font-weight: var(--weight-semibold);
}

.rebuild-errors-list {
  margin: 0;
  padding: 0;
  list-style: none;
}

.rebuild-errors-list li + li {
  margin-top: var(--space-1);
}

.rebuild-error:not(:empty) {
  margin-top: var(--space-3);
}

.rebuild-message {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--text-primary);
}

.rebuild-message:not(:empty) {
  margin-top: var(--space-3);
}
</style>
