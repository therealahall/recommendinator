<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { usePreferencesStore } from '@/stores/preferences'
import { useAppStore } from '@/stores/app'
import ThemeSelector from '@/components/organisms/ThemeSelector.vue'
import ScorerWeights from '@/components/organisms/ScorerWeights.vue'
import TogglePrefs from '@/components/organisms/TogglePrefs.vue'
import LengthPrefs from '@/components/organisms/LengthPrefs.vue'
import CustomRules from '@/components/organisms/CustomRules.vue'

const prefs = usePreferencesStore()
const app = useAppStore()

onMounted(() => {
  prefs.load()
})

watch(() => app.currentUserId, () => {
  prefs.load()
})
</script>

<template>
  <div>
    <div class="page-header">
      <h2>Preferences</h2>
      <p class="page-description">Customize how recommendations are generated.</p>
    </div>
    <div class="card">
      <div v-if="prefs.loading" class="empty-state">Loading preferences...</div>
      <template v-else>
        <ThemeSelector v-model="prefs.pendingTheme" />
        <ScorerWeights />
        <TogglePrefs />
        <LengthPrefs />
        <CustomRules />
        <div class="pref-actions">
          <button class="btn btn-primary" :disabled="prefs.saving" @click="prefs.save()">
            {{ prefs.saving ? 'Saving...' : 'Save Preferences' }}
          </button>
          <div aria-live="polite" aria-atomic="true">
            <span
              v-if="prefs.saveStatus === 'saved'"
              class="save-status text-success"
            >Saved!</span>
            <span
              v-else-if="prefs.saveStatus === 'error'"
              class="save-status text-error"
            >Error: {{ prefs.saveError }}</span>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>

<style scoped>
.pref-actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.save-status {
  font-size: var(--text-sm);
}

.text-success {
  color: var(--color-success);
}

.text-error {
  color: var(--color-error);
}
</style>
