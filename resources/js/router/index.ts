import { nextTick } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'

declare module 'vue-router' {
  interface RouteMeta {
    title?: string
  }
}

export const APP_NAME = 'Recommendinator'

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
      meta: { title: 'Recommendations' },
      component: () => import('@/components/pages/RecommendationsPage.vue'),
    },
    {
      path: '/library',
      name: 'library',
      meta: { title: 'Library' },
      component: () => import('@/components/pages/LibraryPage.vue'),
    },
    {
      path: '/duplicates',
      name: 'duplicates',
      meta: { title: 'Duplicates' },
      component: () => import('@/components/pages/DuplicatesPage.vue'),
    },
    {
      path: '/data',
      name: 'data',
      meta: { title: 'Data' },
      component: () => import('@/components/pages/DataPage.vue'),
    },
    {
      path: '/preferences',
      name: 'preferences',
      meta: { title: 'Preferences' },
      component: () => import('@/components/pages/PreferencesPage.vue'),
    },
    {
      path: '/settings',
      name: 'settings',
      meta: { title: 'Settings' },
      component: () => import('@/components/pages/SettingsPage.vue'),
    },
  ],
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · ${APP_NAME}` : APP_NAME
  nextTick(() => {
    document.getElementById('main-content')?.focus()
  })
})

export default router
