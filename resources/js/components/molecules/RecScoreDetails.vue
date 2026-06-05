<script setup lang="ts">
import { computed } from 'vue'
import type { RecommendationResponse } from '@/types/api'
import { formatScorerName } from '@/utils/format'

const props = defineProps<{
  rec: RecommendationResponse
  defaultOpen?: boolean
}>()

const sortedKeys = computed(() => Object.keys(props.rec.score_breakdown).sort())
const varietyPenalty = computed(() => props.rec.variety_penalty ?? 0)
const varietyPenaltyPct = computed(() => Math.round(varietyPenalty.value * 100))
</script>

<template>
  <details
    v-if="sortedKeys.length > 0 || varietyPenalty > 0 || (rec.llm_reasoning && rec.reasoning)"
    class="score-details"
    :open="defaultOpen"
  >
    <summary>Score Details</summary>
    <div v-if="rec.llm_reasoning && rec.reasoning" class="rec-reasoning rec-reasoning-folded">
      {{ rec.reasoning }}
    </div>
    <div v-if="sortedKeys.length > 0 || varietyPenalty > 0" class="score-breakdown">
      <div v-for="key in sortedKeys" :key="key" class="score-row">
        <span class="score-label">{{ formatScorerName(key) }}</span>
        <div class="score-bar-bg" aria-hidden="true">
          <div class="score-bar-fill" :style="{ width: `${Math.round(rec.score_breakdown[key] * 100)}%` }" />
        </div>
        <span class="score-value">{{ rec.score_breakdown[key].toFixed(2) }}</span>
      </div>
      <div v-if="varietyPenalty > 0" class="score-row score-row-penalty">
        <span class="score-label">Variety penalty</span>
        <div class="score-bar-bg" aria-hidden="true">
          <div class="score-bar-fill score-bar-fill-penalty" :style="{ width: `${varietyPenaltyPct}%` }" />
        </div>
        <span class="score-value">&minus;{{ varietyPenaltyPct }}%</span>
      </div>
    </div>
  </details>
</template>
