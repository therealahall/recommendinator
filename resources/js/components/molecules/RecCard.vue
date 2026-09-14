<script setup lang="ts">
import { computed, ref } from 'vue'
import type { RecommendationResponse, RelatedItemResponse } from '@/types/api'
import AppIcon from '@/components/atoms/AppIcon.vue'
import ItemCover from '@/components/atoms/ItemCover.vue'
import RecScoreDetails from '@/components/molecules/RecScoreDetails.vue'
import { contentTypeGlyph } from '@/constants/contentTypes'
import {
  formatContentType,
  formatScore,
  formatSeries,
  scoreShares,
  scoreSpine,
} from '@/utils/format'

const props = defineProps<{
  rec: RecommendationResponse
  rank: number
}>()

const emit = defineEmits<{
  ignore: [dbId: number]
  complete: [dbId: number]
}>()

interface EvidencePill {
  key: string
  title: string
  glyph: ReturnType<typeof contentTypeGlyph>
  relation: string
  inferred: boolean
}

/** A pill is as wide as the title in it, so no count is exactly two rows. Four
 *  is about that at the card's measure, and the rest are one press away. */
const COLLAPSED_PILLS = 4

const expanded = ref(false)
const evidenceOpen = ref(false)
const detailsId = computed(() => `rec-breakdown-${props.rec.db_id ?? props.rank}`)
const evidenceId = computed(() => `rec-evidence-${props.rec.db_id ?? props.rank}`)

const series = computed(() => formatSeries(props.rec.series, props.rec.series_index))
const typeLabel = computed(() => formatContentType(props.rec.content_type))
const glyph = computed(() => contentTypeGlyph(props.rec.content_type))
const percent = computed(() => formatScore(props.rec.score))
const spine = computed(() =>
  scoreSpine(
    scoreShares(props.rec.score_breakdown, props.rec.scorer_weights),
    props.rec.variety_penalty,
  ),
)
const hasBreakdown = computed(
  () => Object.keys(props.rec.score_breakdown).length > 0 || props.rec.variety_penalty > 0,
)

function pills(items: RelatedItemResponse[], relation: string, inferred: boolean): EvidencePill[] {
  return items.map((item, index) => ({
    key: `${relation}-${item.db_id ?? index}`,
    title: item.title,
    glyph: contentTypeGlyph(item.content_type),
    relation,
    inferred,
  }))
}

const evidence = computed(() => {
  const adapted = new Set(props.rec.adaptations.map((item) => item.db_id))
  const directOnly = props.rec.contributing_items.filter(
    (item) => item.db_id === null || !adapted.has(item.db_id),
  )
  return [
    ...pills(props.rec.adaptations, 'same story, inferred', true),
    ...pills(directOnly, 'contributed directly', false),
  ]
})
const shownEvidence = computed(() =>
  evidenceOpen.value ? evidence.value : evidence.value.slice(0, COLLAPSED_PILLS),
)
const hiddenEvidence = computed(() => Math.max(evidence.value.length - COLLAPSED_PILLS, 0))
const hasInferred = computed(() => shownEvidence.value.some((pill) => pill.inferred))
const evidenceToggleLabel = computed(() =>
  evidenceOpen.value ? 'Show fewer' : `+${hiddenEvidence.value} more`,
)

// Leads on the visible words, so speech input reaches it (WCAG 2.5.3).
const scoreLabel = computed(() =>
  hasBreakdown.value
    ? `Why this: ${percent.value} percent match. ${expanded.value ? 'Hide' : 'Show'} the score breakdown.`
    : `${percent.value} percent match`,
)

function toggleBreakdown() {
  if (hasBreakdown.value) expanded.value = !expanded.value
}
</script>

<template>
  <article class="rec-card">
    <div class="rec-gutter">
      <ItemCover
        :cover-url="rec.cover_url"
        :content-type="rec.content_type"
        :title="rec.title"
      />
    </div>

    <div class="rec-body">
      <div class="rec-header">
        <div class="rec-identity">
          <h3 class="rec-title">
            <AppIcon :name="glyph" class="type-glyph" />
            <span class="sr-only">{{ typeLabel }}. </span>{{ rec.title }}
          </h3>
          <div v-if="series.shown" class="rec-series">
            <span aria-hidden="true">{{ series.shown }}</span>
            <span class="sr-only">{{ series.spoken }}</span>
          </div>
          <div v-if="rec.author" class="rec-author">by {{ rec.author }}</div>
        </div>

        <component
          :is="hasBreakdown ? 'button' : 'span'"
          class="rec-score"
          :type="hasBreakdown ? 'button' : undefined"
          :aria-expanded="hasBreakdown ? String(expanded) : undefined"
          :aria-controls="hasBreakdown ? detailsId : undefined"
          @click="toggleBreakdown"
        >
          <span class="rec-score-number" aria-hidden="true">
            <b>{{ percent }}%</b>
            <span class="rec-score-caption">match</span>
          </span>
          <span v-if="spine.length > 0" class="rec-spine" aria-hidden="true">
            <i
              v-for="segment in spine"
              :key="segment.key"
              class="rec-spine-segment"
              :class="{
                'rec-spine-segment--rest': segment.tone === 'rest',
                'rec-spine-segment--penalty': segment.tone === 'penalty',
              }"
              :style="{ width: `${segment.percent}%` }"
            />
          </span>
          <span v-if="hasBreakdown" class="rec-score-cue" aria-hidden="true">
            <span class="rec-score-cue-label">Why this</span><AppIcon name="chevron" />
          </span>
          <span class="sr-only">{{ scoreLabel }}</span>
        </component>
      </div>

      <!-- Directly under its trigger, so what the reader just opened cannot
           land off the bottom of a tall card. -->
      <RecScoreDetails :id="detailsId" :rec="rec" :open="expanded" />

      <p v-if="rec.reasoning" class="rec-reasoning">{{ rec.reasoning }}</p>

      <div v-if="evidence.length > 0" class="rec-citations">
        <ul :id="evidenceId" class="rec-evidence" role="list">
          <li v-for="pill in shownEvidence" :key="pill.key">
            <span class="badge badge--wrap" :class="{ 'badge--inferred': pill.inferred }">
              <AppIcon :name="pill.glyph" />
              <b>{{ pill.title }}</b>
              <span class="sr-only"> — {{ pill.relation }}</span>
            </span>
          </li>
        </ul>
        <p v-if="hasInferred" class="rec-evidence-legend" aria-hidden="true">
          Dashed: a different version of the same story
        </p>
        <button
          v-if="hiddenEvidence > 0"
          type="button"
          class="btn btn-ghost rec-evidence-toggle"
          :aria-expanded="evidenceOpen"
          :aria-controls="evidenceId"
          @click="evidenceOpen = !evidenceOpen"
        >
          <AppIcon name="chevron" />{{ evidenceToggleLabel }}<span class="sr-only"> cited items</span>
        </button>
      </div>

      <div class="rec-actions">
        <button
          v-if="rec.db_id"
          type="button"
          class="btn btn-complete"
          :data-testid="`complete-btn-${rec.db_id}`"
          :aria-label="`Mark complete: ${rec.title}`"
          @click="emit('complete', rec.db_id)"
        >
          <AppIcon name="check" />Mark complete
        </button>
        <span class="rec-actions-spacer" />
        <button
          v-if="rec.db_id"
          type="button"
          class="btn btn-ignore"
          :data-testid="`ignore-btn-${rec.db_id}`"
          :aria-label="`Ignore: ${rec.title}`"
          @click="emit('ignore', rec.db_id)"
        >Ignore</button>
      </div>
    </div>
  </article>
</template>
