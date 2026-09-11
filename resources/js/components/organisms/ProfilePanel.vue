<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useProfileStore } from '@/stores/profile'
import { formatDate } from '@/utils/format'

const profileStore = useProfileStore()

const genres = computed(() =>
  (profileStore.profile?.genre_affinities ?? []).map((entry) => ({
    genre: entry.genre,
    label: entry.score === null ? entry.genre : `${entry.genre} ${entry.score.toFixed(1)}`,
    anti: entry.anti,
  })),
)

const authors = computed(() =>
  (profileStore.profile?.author_affinities ?? []).map((entry) => ({
    author: entry.author,
    label: `${entry.author} ${entry.score.toFixed(1)}`,
  })),
)

const generatedAt = computed(() => {
  const stamp = profileStore.profile?.generated_at
  return stamp ? formatDate(stamp) : ''
})

onMounted(() => {
  profileStore.load()
})

const regenerated = ref(false)

// Bound to a computed on a persistently mounted region, because one inserted
// with v-if once it has content is read as page content and skipped. Clearing
// it announces nothing, so a finished run needs words of its own.
const announcement = computed(() => {
  if (profileStore.error) return profileStore.error
  if (profileStore.regenerating) return 'Generating…'
  return regenerated.value ? 'Profile regenerated.' : ''
})

async function regenerate(): Promise<void> {
  if (profileStore.regenerating) return
  regenerated.value = false
  await profileStore.regenerate()
  regenerated.value = true
}
</script>

<template>
  <div class="pref-section">
    <h3>Your profile</h3>
    <p class="help-text">
      Derived from your library.
      <span v-if="generatedAt" data-testid="profile-generated">Generated {{ generatedAt }}.</span>
      Regenerate after a large sync.
    </p>
    <div class="profile-summary">
      <template v-if="profileStore.profile">
        <div v-if="genres.length > 0" class="profile-section">
          <h4>Genres</h4>
          <div class="profile-tags">
            <span v-for="g in genres" :key="g.genre" class="badge" :data-tone="g.anti ? 'error' : 'accent'">{{ g.label }}</span>
          </div>
        </div>
        <div v-if="authors.length > 0" class="profile-section">
          <h4>Authors and creators</h4>
          <div class="profile-tags">
            <span v-for="a in authors" :key="a.author" class="badge" data-tone="accent">{{ a.label }}</span>
          </div>
        </div>
        <div v-if="profileStore.profile.theme_preferences.length > 0" class="profile-section">
          <h4>Themes you enjoy</h4>
          <div class="profile-tags">
            <span v-for="t in profileStore.profile.theme_preferences" :key="t" class="badge" data-tone="accent">{{ t }}</span>
          </div>
        </div>
        <div v-if="profileStore.profile.cross_media_patterns.length > 0" class="profile-section">
          <h4>Patterns</h4>
          <p v-for="p in profileStore.profile.cross_media_patterns" :key="p" class="text-muted profile-pattern">{{ p }}</p>
        </div>
      </template>
      <div v-else class="state state--empty" data-testid="profile-empty">
        <p class="state-title">No profile yet</p>
        <p class="state-hint">
          Regenerate to read your library into the genres, themes and patterns
          the scorers weigh.
        </p>
      </div>
    </div>
    <p
      class="profile-status"
      :class="{ failed: Boolean(profileStore.error) }"
      role="status"
      aria-live="polite"
    >{{ announcement }}</p>
    <!-- aria-disabled, never native disabled: a button that goes disabled under
         the finger that just pressed Enter throws focus to <body> (WCAG 2.4.3). -->
    <button
      type="button"
      class="btn btn-small btn-secondary mt-2"
      :aria-disabled="profileStore.regenerating ? 'true' : undefined"
      @click="regenerate"
    >Regenerate</button>
  </div>
</template>

<style scoped>
.profile-summary {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  font-size: var(--text-sm);
}

.profile-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.profile-section + .profile-section {
  padding-top: var(--space-4);
  border-top: 1px solid var(--border-subtle);
}

.profile-section h4 {
  margin: 0;
  font-size: var(--text-xs);
  font-weight: var(--weight-semibold);
  letter-spacing: var(--tracking-wider);
  text-transform: uppercase;
  color: var(--text-muted);
}

.profile-tags {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}

/* A theme phrase that cannot wrap sets the row's floor (WCAG 1.4.10). */
.profile-tags .badge {
  max-width: 100%;
  white-space: normal;
}

.profile-pattern {
  line-height: var(--leading-snug);
}

.profile-status {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--text-secondary);
}

/* Mounted while silent (WCAG 4.1.3), so it earns its spacing only when it speaks. */
.profile-status:not(:empty) {
  margin-top: var(--space-4);
}

.profile-status.failed {
  color: var(--color-error-text);
}

/* The panel's one action: the small variant leaves it under a thumb target. */
.pref-section > .btn {
  min-height: 44px;
  margin-top: var(--space-4);
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
}

@media (max-width: 768px) {
  .pref-section > .btn {
    width: 100%;
  }
}
</style>
