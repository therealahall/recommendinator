<script setup lang="ts">
import { useChatStore } from '@/stores/chat'

const chat = useChatStore()
</script>

<template>
  <div class="profile-panel">
    <h4>Your Profile</h4>
    <div class="profile-summary">
      <template v-if="chat.profile">
        <div v-if="Object.keys(chat.profile.genre_affinities).length > 0" class="profile-section">
          <h5>Genres You Love</h5>
          <div class="profile-tags">
            <span v-for="g in Object.keys(chat.profile.genre_affinities).slice(0, 6)" :key="g" class="profile-tag">{{ g }}</span>
          </div>
        </div>
        <div v-if="chat.profile.anti_preferences.length > 0" class="profile-section">
          <h5>Not Your Style</h5>
          <div class="profile-tags">
            <span v-for="p in chat.profile.anti_preferences.slice(0, 6)" :key="p" class="profile-tag anti">{{ p }}</span>
          </div>
        </div>
        <div v-if="chat.profile.cross_media_patterns.length > 0" class="profile-section">
          <h5>Patterns</h5>
          <p v-for="p in chat.profile.cross_media_patterns.slice(0, 3)" :key="p" class="text-muted profile-pattern">{{ p }}</p>
        </div>
      </template>
      <div v-else class="empty-state">No profile generated</div>
    </div>
    <button
      class="btn btn-small btn-secondary mt-2"
      :disabled="chat.profileRegenerating"
      @click="chat.regenerateProfile()"
    >{{ chat.profileRegenerating ? 'Generating...' : 'Regenerate' }}</button>
  </div>
</template>
