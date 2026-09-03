import { nextTick } from 'vue'
import { createRouter, createWebHashHistory } from 'vue-router'

declare module 'vue-router' {
  interface RouteMeta {
    title?: string
  }
}

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
    // A Library function rather than a section of its own: it is reached from
    // the Library screen, and the nav marks Library while it is open.
    {
      path: '/library/duplicates',
      name: 'duplicates',
      meta: { title: 'Duplicates' },
      component: () => import('@/components/pages/DuplicatesPage.vue'),
    },
    { path: '/duplicates', redirect: '/library/duplicates' },
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
  // Where a page starts is owned here, so the focus call below need not decide.
  scrollBehavior(_to, _from, savedPosition) {
    return savedPosition ?? { top: 0 }
  },
})

// Focusing a <main> taller than the viewport scrolls its top edge up to meet it.
router.afterEach(() => {
  nextTick(() => {
    document.getElementById('main-content')?.focus({ preventScroll: true })
  })
})

export default router
