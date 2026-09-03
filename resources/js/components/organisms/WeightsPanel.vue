<script setup lang="ts">
import AppIcon from '@/components/atoms/AppIcon.vue'
import WeightsDialog from '@/components/organisms/WeightsDialog.vue'
import { SCORER_KEYS } from '@/stores/preferences'
import { ref } from 'vue'

const open = ref(false)

// The dialog still counts what it holds; the trigger does not need to.
const summary = `${SCORER_KEYS.length + 1} signals`
</script>

<template>
  <button
    type="button"
    class="btn btn-secondary weights-trigger"
    aria-haspopup="dialog"
    :aria-expanded="open"
    :aria-controls="open ? 'weights-panel' : undefined"
    data-testid="weights-trigger"
    @click="open = true"
  >
    <AppIcon name="sliders" />
    Scoring<span class="sr-only"> weights</span>
  </button>

  <WeightsDialog v-if="open" :summary="summary" @close="open = false" />
</template>

<style scoped>
.weights-trigger {
  min-height: 44px;
}
</style>
