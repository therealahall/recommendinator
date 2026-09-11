<script setup lang="ts">
import { computed, onMounted, ref, useId } from 'vue'
import { useProfileStore } from '@/stores/profile'
import { formatDate } from '@/utils/format'

const profileStore = useProfileStore()

// A bare number reads as part of the name it follows.
function spokenScore(name: string, score: number | null): string {
  return score === null ? name : `${name}, rated ${score.toFixed(1)} out of 5 on average`
}

const genres = computed(() =>
  (profileStore.profile?.genre_affinities ?? []).map((entry) => ({
    genre: entry.genre,
    shown: entry.score === null ? entry.genre : `${entry.genre} ${entry.score.toFixed(1)}`,
    spoken: spokenScore(entry.genre, entry.score),
    anti: entry.anti,
  })),
)

const likedGenres = computed(() => genres.value.filter((entry) => !entry.anti))
const antiGenres = computed(() => genres.value.filter((entry) => entry.anti))

const authors = computed(() =>
  (profileStore.profile?.author_affinities ?? []).map((entry) => ({
    author: entry.author,
    shown: `${entry.author} ${entry.score.toFixed(1)}`,
    spoken: spokenScore(entry.author, entry.score),
  })),
)

const likedGenresLabel = useId()
const antiGenresLabel = useId()
const authorsLabel = useId()
const themesLabel = useId()

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

const emptyHint = computed(() =>
  regenerated.value
    ? 'Nothing in your library is rated yet, so there is nothing to read a profile from. Rate a few items, then regenerate.'
    : 'Regenerate to read your library into the genres, themes and patterns the scorers weigh.',
)

async function regenerate(): Promise<void> {
  if (profileStore.regenerating) return
  regenerated.value = false
  await profileStore.regenerate()
  // A failed run leaves the state a never-generated profile has, not an unrated library.
  regenerated.value = profileStore.error === ''
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
        <div v-if="likedGenres.length > 0" class="profile-section">
          <h4 :id="likedGenresLabel">Genres you love</h4>
          <ul class="profile-tags" role="list" :aria-labelledby="likedGenresLabel">
            <li v-for="g in likedGenres" :key="g.genre">
              <span class="badge" data-tone="accent">
                <span aria-hidden="true">{{ g.shown }}</span>
                <span class="sr-only">{{ g.spoken }}</span>
              </span>
            </li>
          </ul>
        </div>
        <div v-if="antiGenres.length > 0" class="profile-section">
          <h4 :id="antiGenresLabel">Not your style</h4>
          <ul class="profile-tags" role="list" :aria-labelledby="antiGenresLabel">
            <li v-for="g in antiGenres" :key="g.genre">
              <span class="badge">
                <span aria-hidden="true">{{ g.shown }}</span>
                <span class="sr-only">{{ g.spoken }} — not your style</span>
              </span>
            </li>
          </ul>
        </div>
        <div v-if="authors.length > 0" class="profile-section">
          <h4 :id="authorsLabel">Authors and creators</h4>
          <ul class="profile-tags" role="list" :aria-labelledby="authorsLabel">
            <li v-for="a in authors" :key="a.author">
              <span class="badge" data-tone="accent">
                <span aria-hidden="true">{{ a.shown }}</span>
                <span class="sr-only">{{ a.spoken }}</span>
              </span>
            </li>
          </ul>
        </div>
        <div v-if="profileStore.profile.theme_preferences.length > 0" class="profile-section">
          <h4 :id="themesLabel">Themes you enjoy</h4>
          <ul class="profile-tags" role="list" :aria-labelledby="themesLabel">
            <li v-for="t in profileStore.profile.theme_preferences" :key="t">
              <span class="badge" data-tone="accent">{{ t }}</span>
            </li>
          </ul>
        </div>
        <div v-if="profileStore.profile.cross_media_patterns.length > 0" class="profile-section">
          <h4>Patterns</h4>
          <p v-for="p in profileStore.profile.cross_media_patterns" :key="p" class="text-muted profile-pattern">{{ p }}</p>
        </div>
      </template>
      <div v-else class="state state--empty" data-testid="profile-empty">
        <p class="state-title">No profile yet</p>
        <p class="state-hint">{{ emptyHint }}</p>
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
  list-style: none;
}

/* A theme phrase that cannot wrap sets the row's floor (WCAG 1.4.10). */
.profile-tags li,
.profile-tags .badge {
  max-width: 100%;
}

.profile-tags .badge {
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
