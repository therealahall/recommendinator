<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useProfileStore } from '@/stores/profile'

const profileStore = useProfileStore()

onMounted(() => {
  profileStore.load()
})

// Bound to a computed on a persistently mounted region, because one inserted
// with v-if once it has content is read as page content and skipped.
const announcement = computed(() => {
  if (profileStore.error) return profileStore.error
  return profileStore.regenerating ? 'Generating…' : ''
})

function regenerate(): void {
  if (profileStore.regenerating) return
  profileStore.regenerate()
}
</script>

<template>
  <div class="pref-section">
    <h3>Your Profile</h3>
    <p class="help-text">Derived from your library. Regenerate after a large sync.</p>
    <div class="profile-summary">
      <template v-if="profileStore.profile">
        <div v-if="Object.keys(profileStore.profile.genre_affinities).length > 0" class="profile-section">
          <h4>Genres You Love</h4>
          <div class="profile-tags">
            <span v-for="g in Object.keys(profileStore.profile.genre_affinities).slice(0, 6)" :key="g" class="profile-tag">{{ g }}</span>
          </div>
        </div>
        <div v-if="profileStore.profile.theme_preferences.length > 0" class="profile-section">
          <h4>Themes You Enjoy</h4>
          <div class="profile-tags">
            <span v-for="t in profileStore.profile.theme_preferences.slice(0, 6)" :key="t" class="profile-tag">{{ t }}</span>
          </div>
        </div>
        <div v-if="profileStore.profile.anti_preferences.length > 0" class="profile-section">
          <h4>Not Your Style</h4>
          <div class="profile-tags">
            <span v-for="p in profileStore.profile.anti_preferences.slice(0, 6)" :key="p" class="profile-tag anti">{{ p }}</span>
          </div>
        </div>
        <div v-if="profileStore.profile.cross_media_patterns.length > 0" class="profile-section">
          <h4>Patterns</h4>
          <p v-for="p in profileStore.profile.cross_media_patterns.slice(0, 3)" :key="p" class="text-muted profile-pattern">{{ p }}</p>
        </div>
      </template>
      <div v-else class="empty-state">No profile generated</div>
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
