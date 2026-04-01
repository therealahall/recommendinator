<script setup lang="ts">
import { computed } from 'vue'
import type { RecommendationResponse } from '@/types/api'
import { formatScorerName } from '@/utils/format'

const props = defineProps<{
  rec: RecommendationResponse
  defaultOpen?: boolean
}>()

const sortedKeys = computed(() => Object.keys(props.rec.score_breakdown).sort())
</script>

<template>
  <details
    v-if="sortedKeys.length > 0 || (rec.llm_reasoning && rec.reasoning)"
    class="score-details"
    :open="defaultOpen"
  >
    <summary>Score Details</summary>
    <div v-if="rec.llm_reasoning && rec.reasoning" class="rec-reasoning rec-reasoning-folded">
      {{ rec.reasoning }}
    </div>
    <div v-if="sortedKeys.length > 0" class="score-breakdown">
      <div v-for="key in sortedKeys" :key="key" class="score-row">
        <span class="score-label">{{ formatScorerName(key) }}</span>
        <div class="score-bar-bg">
          <div class="score-bar-fill" :style="{ width: `${Math.round(rec.score_breakdown[key] * 100)}%` }" />
        </div>
        <span class="score-value">{{ rec.score_breakdown[key].toFixed(2) }}</span>
      </div>
    </div>
  </details>
</template>
