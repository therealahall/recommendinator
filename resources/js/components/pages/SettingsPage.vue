<script setup lang="ts">
import { onMounted } from 'vue'
import { useSettingsStore } from '@/stores/settings'
import SettingsSection from '@/components/organisms/SettingsSection.vue'

const store = useSettingsStore()

onMounted(() => {
  store.load()
})
</script>

<template>
  <div>
    <div class="page-header">
      <h2>Settings</h2>
      <p class="page-description">Application configuration for this instance.</p>
    </div>

    <div v-if="store.loading" class="card" aria-busy="true">
      <div class="empty-state">Loading settings…</div>
    </div>

    <div v-else-if="store.loadError" class="card">
      <div class="empty-state" role="alert">
        Couldn't load settings.
        <button class="btn btn-secondary" data-testid="settings-retry" @click="store.load()">Retry</button>
      </div>
    </div>

    <div v-else-if="store.sections.length === 0" class="card">
      <div class="empty-state">No configurable settings.</div>
    </div>

    <template v-else>
      <SettingsSection
        v-for="section in store.sections"
        :key="section.section"
        :section="section"
      />
    </template>
  </div>
</template>
