<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import AppIcon from '@/components/atoms/AppIcon.vue'
import type { UserResponse } from '@/types/api'

/** `within` names the routes a section owns without linking to them: duplicates
 *  is a Library function, so the rail says Library while it is on screen. */
const SECTIONS = [
  { route: 'recommendations', label: 'For you', icon: 'star', within: [] },
  { route: 'library', label: 'Library', icon: 'book', within: ['duplicates'] },
  { route: 'data', label: 'Data', icon: 'activity', within: [] },
  { route: 'preferences', label: 'Preferences', icon: 'cog', within: [] },
  { route: 'settings', label: 'Settings', icon: 'sliders', within: [] },
] as const

const props = defineProps<{
  /** Absent until the session is resolved; the footer waits rather than
   *  rendering a nameless row. */
  user?: UserResponse | null
}>()

const route = useRoute()

// Display name is optional, so the username is what is left to identify the
// account by. There is no switcher: a second person signs in as themselves.
const userLabel = computed(() =>
  props.user ? props.user.display_name || props.user.username : '',
)

/** `page` for the route the link goes to, `true` for one it only contains: the
 *  Library link does not resolve to the duplicates screen it marks. */
function current(section: (typeof SECTIONS)[number]): 'page' | 'true' | undefined {
  const name = route.name
  if (name === section.route) return 'page'
  return section.within.some((held) => held === name) ? 'true' : undefined
}
</script>

<template>
  <nav class="app-nav" aria-label="Sections">
    <ul class="nav-list">
      <li v-for="section in SECTIONS" :key="section.route">
        <RouterLink v-slot="{ href, navigate }" :to="{ name: section.route }" custom>
          <a :href="href" class="nav-item" :aria-current="current(section)" @click="navigate">
            <AppIcon :name="section.icon" :size="20" />
            {{ section.label }}
          </a>
        </RouterLink>
      </li>
    </ul>
    <p v-if="userLabel" class="nav-user" data-testid="nav-user">
      <span class="sr-only">Signed in as </span>{{ userLabel }}
    </p>
  </nav>
</template>
