import { nextTick } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    {
      path: '/',
      redirect: '/recommendations',
    },
    {
      path: '/recommendations',
      name: 'recommendations',
      component: () => import('@/components/pages/RecommendationsPage.vue'),
    },
    {
      path: '/library',
      name: 'library',
      component: () => import('@/components/pages/LibraryPage.vue'),
    },
    {
      path: '/duplicates',
      name: 'duplicates',
      component: () => import('@/components/pages/DuplicatesPage.vue'),
    },
    {
      path: '/data',
      name: 'data',
      component: () => import('@/components/pages/DataPage.vue'),
    },
    {
      path: '/preferences',
      name: 'preferences',
      component: () => import('@/components/pages/PreferencesPage.vue'),
    },
    {
      path: '/settings',
      name: 'settings',
      component: () => import('@/components/pages/SettingsPage.vue'),
    },
  ],
})

router.afterEach(() => {
  nextTick(() => {
    document.getElementById('main-content')?.focus()
  })
})

export default router
