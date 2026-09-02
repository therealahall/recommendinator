<script setup lang="ts">
import { computed } from 'vue'
import type { RecommendationResponse } from '@/types/api'
import { formatScore, formatScorerName } from '@/utils/format'

const props = defineProps<{
  rec: RecommendationResponse
  open?: boolean
}>()

const rows = computed(() =>
  Object.entries(props.rec.score_breakdown)
    .sort(([, one], [, two]) => two - one)
    .map(([key, value]) => ({ key, label: formatScorerName(key), percent: formatScore(value) })),
)
const varietyPenalty = computed(() => props.rec.variety_penalty ?? 0)
const varietyPenaltyPct = computed(() => formatScore(varietyPenalty.value))
const total = computed(() => formatScore(props.rec.score))
</script>

<template>
  <div v-if="rows.length > 0 || varietyPenalty > 0" class="score-details" :hidden="!open">
    <p class="score-details-title">How {{ total }}% was reached</p>
    <dl class="score-breakdown">
      <div v-for="row in rows" :key="row.key" class="score-row">
        <dt class="score-label">{{ row.label }}</dt>
        <dd class="score-bar-bg" aria-hidden="true">
          <div class="score-bar-fill" :style="{ width: `${row.percent}%` }" />
        </dd>
        <dd class="score-value">{{ row.percent }}%</dd>
      </div>
      <div v-if="varietyPenalty > 0" class="score-row score-row-penalty">
        <dt class="score-label">Variety penalty</dt>
        <dd class="score-bar-bg" aria-hidden="true">
          <div
            class="score-bar-fill score-bar-fill-penalty"
            :style="{ width: `${varietyPenaltyPct}%` }"
          />
        </dd>
        <dd class="score-value">&minus;{{ varietyPenaltyPct }}%</dd>
      </div>
    </dl>
  </div>
</template>
