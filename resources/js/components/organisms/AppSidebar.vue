<script setup lang="ts">
import { computed } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import AppIcon from '@/components/atoms/AppIcon.vue'
import type { UserResponse } from '@/types/api'

const SECTIONS = [
  { route: 'recommendations', label: 'Recommendations', icon: 'star' },
  { route: 'library', label: 'Library', icon: 'book' },
  { route: 'duplicates', label: 'Duplicates', icon: 'copy' },
  { route: 'data', label: 'Data', icon: 'activity' },
  { route: 'preferences', label: 'Preferences', icon: 'cog' },
  { route: 'settings', label: 'Settings', icon: 'sliders' },
] as const

const props = defineProps<{
  /** Absent until the session is resolved; the footer waits rather than
   *  rendering a nameless row. */
  user?: UserResponse | null
  /** Closed on a narrow viewport: on screen it is gone, but without this every
   *  control in it is still tabbable and still read out. */
  offscreen?: boolean
}>()

const router = useRouter()
const route = useRoute()

const emit = defineEmits<{
  navigate: []
}>()

// Display name is optional, so the username is what is left to identify the
// account by. There is no switcher: a second person signs in as themselves.
const userLabel = computed(() =>
  props.user ? props.user.display_name || props.user.username : '',
)

function navigate(name: string) {
  router.push({ name })
  emit('navigate')
}

function isActive(name: string): boolean {
  return route.name === name
}
</script>

<template>
  <aside
    class="sidebar"
    id="sidebar"
    :inert="offscreen || undefined"
    :aria-hidden="offscreen || undefined"
  >
    <nav class="sidebar-nav">
      <button
        v-for="section in SECTIONS"
        :key="section.route"
        class="nav-item"
        :class="{ active: isActive(section.route) }"
        :aria-current="isActive(section.route) ? 'page' : undefined"
        @click="navigate(section.route)"
      >
        <AppIcon :name="section.icon" :size="20" />
        {{ section.label }}
      </button>
    </nav>
    <div v-if="userLabel" class="sidebar-footer">
      <p class="sidebar-user" data-testid="sidebar-user">
        <span class="sidebar-user-label">Signed in as</span>
        <span class="sidebar-user-name">{{ userLabel }}</span>
      </p>
    </div>
  </aside>
</template>
