<script setup lang="ts">
import { computed } from 'vue'
import type { RecommendationResponse } from '@/types/api'
import { formatScorerName, scoreShares } from '@/utils/format'

const props = defineProps<{
  rec: RecommendationResponse
  open?: boolean
}>()

const shares = computed(() => scoreShares(props.rec.score_breakdown, props.rec.scorer_weights))
// A zero-weight scorer contributed nothing, so its own strength would read as a
// near-full bar for a signal the operator switched off in the weights dialog.
const rows = computed(() =>
  shares.value.map(({ key, value, weight }) => {
    const off = weight === 0
    return {
      key,
      label: formatScorerName(key),
      off,
      percent: off ? '0' : (value * 100).toFixed(0),
      readout: off ? 'Off' : `${(value * 100).toFixed(0)}%`,
    }
  }),
)
const varietyPenalty = computed(() => props.rec.variety_penalty ?? 0)
const penaltyPercent = computed(() => (varietyPenalty.value * 100).toFixed(0))
</script>

<template>
  <div v-if="rows.length > 0 || varietyPenalty > 0" class="score-details" :hidden="!open">
    <p class="score-details-title">How each signal scored</p>
    <dl class="score-breakdown">
      <div
        v-for="row in rows"
        :key="row.key"
        class="score-row"
        :class="{ 'score-row-off': row.off }"
      >
        <dt class="score-label">{{ row.label }}</dt>
        <dd class="score-bar-bg" aria-hidden="true">
          <div class="score-bar-fill" :style="{ width: `${row.percent}%` }" />
        </dd>
        <dd class="score-value">{{ row.readout }}</dd>
      </div>
      <div v-if="varietyPenalty > 0" class="score-row score-row-penalty">
        <dt class="score-label">Variety penalty</dt>
        <dd class="score-bar-bg" aria-hidden="true">
          <div
            class="score-bar-fill score-bar-fill-penalty"
            :style="{ width: `${penaltyPercent}%` }"
          />
        </dd>
        <dd class="score-value">&minus;{{ penaltyPercent }}%</dd>
      </div>
    </dl>
  </div>
</template>
