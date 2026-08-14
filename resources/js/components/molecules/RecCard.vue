<script setup lang="ts">
import type { RecommendationResponse } from '@/types/api'
import RecScoreDetails from '@/components/molecules/RecScoreDetails.vue'

defineProps<{
  rec: RecommendationResponse
  rank: number
}>()

const emit = defineEmits<{
  ignore: [dbId: number]
  complete: [dbId: number]
}>()
</script>

<template>
  <div class="rec-card">
    <div class="rec-header">
      <div>
        <div class="rec-title">
          <span class="rec-rank">{{ rank }}.</span>
          {{ rec.title }}
        </div>
        <div v-if="rec.author" class="rec-author">by {{ rec.author }}</div>
      </div>
      <div class="rec-actions">
        <span class="badge badge-score">{{ rec.score.toFixed(2) }}</span>
        <button
          v-if="rec.db_id"
          class="btn btn-small btn-complete"
          :aria-label="`Mark complete: ${rec.title}`"
          @click="emit('complete', rec.db_id)"
        >Mark complete</button>
        <button
          v-if="rec.db_id"
          class="btn btn-small btn-ignore"
          :aria-label="`Ignore: ${rec.title}`"
          @click="emit('ignore', rec.db_id)"
        >Ignore</button>
      </div>
    </div>

    <div v-if="rec.reasoning" class="rec-reasoning">{{ rec.reasoning }}</div>

    <RecScoreDetails :rec="rec" default-open />
  </div>
</template>
