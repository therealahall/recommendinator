<script setup lang="ts">
import { computed } from 'vue'
import type { RecommendationResponse } from '@/types/api'
import RecScoreDetails from '@/components/molecules/RecScoreDetails.vue'
import { useMarkdown } from '@/composables/useMarkdown'

const props = defineProps<{
  rec: RecommendationResponse
  rank: number
  streaming: boolean
}>()

const emit = defineEmits<{
  ignore: [dbId: number]
}>()

const { renderMarkdown } = useMarkdown()

const hasLlmReasoning = computed(() => !!props.rec.llm_reasoning?.trim())
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
          class="btn btn-small btn-ignore"
          @click="emit('ignore', rec.db_id!)"
        >Ignore</button>
      </div>
    </div>

    <!-- LLM reasoning (rendered as markdown) -->
    <div
      v-if="hasLlmReasoning"
      class="rec-llm-reasoning"
      v-html="renderMarkdown(rec.llm_reasoning!)"
    />
    <!-- Loading dots while streaming -->
    <div v-else-if="streaming && !rec.llm_reasoning" class="loading-dots">
      <span /><span /><span />
    </div>

    <!-- Pipeline reasoning (when no LLM reasoning) -->
    <div
      v-if="!hasLlmReasoning && !streaming && rec.reasoning"
      class="rec-reasoning"
    >{{ rec.reasoning }}</div>

    <RecScoreDetails :rec="rec" :default-open="!hasLlmReasoning && !streaming" />
  </div>
</template>
