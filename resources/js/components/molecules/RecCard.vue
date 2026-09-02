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

/** Three fits the measure; the rest are counted so the citation is not silently
 *  truncated to look like the whole reason. */
const MAX_PILLS = 3

const expanded = ref(false)
const detailsId = computed(() => `rec-breakdown-${props.rec.db_id ?? props.rank}`)

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
const shownEvidence = computed(() => evidence.value.slice(0, MAX_PILLS))
const hiddenEvidence = computed(() => evidence.value.length - shownEvidence.value.length)

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
      <span class="rec-rank" aria-hidden="true">{{ rank }}</span>
      <ItemCover
        :cover-url="rec.cover_url"
        :content-type="rec.content_type"
        :title="rec.title"
      />
    </div>

    <div class="rec-body">
      <div class="rec-header">
        <div class="rec-heading">
          <p class="kind-line"><AppIcon :name="glyph" />{{ typeLabel }}</p>
          <h3 class="rec-title">{{ rec.title }}</h3>
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
            Why this<AppIcon name="chevron" />
          </span>
          <span class="sr-only">{{ scoreLabel }}</span>
        </component>
      </div>

      <p v-if="rec.reasoning" class="rec-reasoning">{{ rec.reasoning }}</p>

      <ul v-if="shownEvidence.length > 0" class="rec-evidence" role="list">
        <li v-for="pill in shownEvidence" :key="pill.key">
          <span class="badge badge--wrap" :class="{ 'badge--inferred': pill.inferred }">
            <AppIcon :name="pill.glyph" />
            <b>{{ pill.title }}</b>
            <span>{{ pill.relation }}</span>
          </span>
        </li>
        <li v-if="hiddenEvidence > 0" class="rec-evidence-more">+{{ hiddenEvidence }} more</li>
      </ul>

      <RecScoreDetails :id="detailsId" :rec="rec" :open="expanded" />

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
