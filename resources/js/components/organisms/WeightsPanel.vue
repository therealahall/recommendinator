<script setup lang="ts">
import AppIcon from '@/components/atoms/AppIcon.vue'
import WeightsDialog from '@/components/organisms/WeightsDialog.vue'
import { ref } from 'vue'

const open = ref(false)
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

  <WeightsDialog v-if="open" @close="open = false" />
</template>

<style scoped>
.weights-trigger {
  min-height: 44px;
}
</style>
