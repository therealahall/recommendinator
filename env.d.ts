/// <reference types="vite/client" />

declare const __BUNDLE_VERSION__: string | undefined

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<object, object, unknown>
  export default component
}
