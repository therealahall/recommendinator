<script setup lang="ts">
import { useRecommendationsStore } from '@/stores/recommendations'
import RecControls from '@/components/organisms/RecControls.vue'
import RecCard from '@/components/molecules/RecCard.vue'

const recs = useRecommendationsStore()
</script>

<template>
  <div>
    <div class="page-header">
      <h2>Recommendations</h2>
      <p class="page-description">Get personalized recommendations based on your library and preferences.</p>
    </div>

    <RecControls />

    <div v-if="recs.error" class="status-bar error" style="display: block">
      Failed to load recommendations: {{ recs.error }}
    </div>

    <div v-if="recs.loading && recs.items.length === 0" class="empty-state">
      <span class="spinner" /> Loading recommendations...
    </div>

    <div v-if="recs.items.length === 0 && !recs.loading && !recs.error" class="empty-state">
      No recommendations yet. Click Generate to get started.
    </div>

    <div v-if="recs.items.length > 0">
      <RecCard
        v-for="(rec, index) in recs.items"
        :key="rec.db_id ?? index"
        :rec="rec"
        :rank="index + 1"
        :streaming="recs.streaming"
        @ignore="recs.ignoreItem($event)"
      />
    </div>
  </div>
</template>
