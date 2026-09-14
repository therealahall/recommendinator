<script setup lang="ts">
import { computed } from 'vue'
import type { SettingViewValue } from '@/types/api'

const props = withDefaults(
  defineProps<{
    setting: SettingViewValue
    disabled?: boolean
    resetting?: boolean
  }>(),
  { disabled: false, resetting: false },
)

const emit = defineEmits<{ reset: [] }>()

const lockId = computed(() => `reset-locked-${props.setting.key}`)

// aria-disabled, not disabled: the lock closes on the button the user has just
// activated, and a disabled button is blurred and unreachable by Tab, so the
// reason it refuses is never read out (WCAG 2.4.3).
function onReset(): void {
  if (props.disabled || props.resetting) return
  emit('reset')
}
</script>

<template>
  <div class="setting-row-meta">
    <span
      v-if="setting.restart_required"
      class="badge"
      data-tone="warning"
      title="This setting takes effect after a restart"
      :data-testid="`restart-badge-${setting.key}`"
    >Requires restart<span class="sr-only"> to take effect</span></span>
    <span
      v-if="setting.db_overridden"
      class="badge"
      data-tone="accent"
      :data-testid="`overridden-badge-${setting.key}`"
    >Overridden<span class="sr-only"> — differs from the built-in default</span></span>
    <button
      v-if="setting.has_stored_value"
      type="button"
      class="btn btn-secondary btn-small"
      :aria-disabled="disabled || resetting || undefined"
      :aria-describedby="disabled && !resetting ? lockId : undefined"
      :data-testid="`reset-${setting.key}`"
      @click="onReset"
    >{{ resetting ? 'Resetting…' : 'Reset to default' }}<span class="sr-only"> — {{ setting.label }}</span></button>
    <span v-if="disabled && !resetting" :id="lockId" class="sr-only"
      >Unavailable while this section has a change in flight.</span
    >
  </div>
</template>

<style scoped>
.setting-row-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.setting-row-meta:empty {
  display: none;
}

/* .badge and .btn are shared primitives in base.css. */
</style>
