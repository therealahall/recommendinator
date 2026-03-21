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
      path: '/chat',
      name: 'chat',
      component: () => import('@/components/pages/ChatPage.vue'),
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
  ],
})

export default router
