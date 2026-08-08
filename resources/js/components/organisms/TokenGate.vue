<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()

const draft = ref('')
const tokenInput = ref<HTMLInputElement | null>(null)

// Mounted persistently and bound to a computed, because a live region inserted
// with v-if once it has content is read as page content and skipped.
const announcement = computed(() =>
  auth.rejected ? 'That token was not accepted. Check it and try again.' : '',
)

function submit(): void {
  const value = draft.value.trim()
  if (!value) return
  auth.setToken(value)
  draft.value = ''
}

onMounted(() => tokenInput.value?.focus())
</script>

<template>
  <main class="token-gate" aria-labelledby="token-gate-heading">
    <form class="card token-gate-card" @submit.prevent="submit">
      <h1 id="token-gate-heading">Recommendinator</h1>
      <p>
        This server requires an API token. It is in your
        <code>config/config.yaml</code> under <code>web.api_token</code>, and the
        container prints it once on first start.
      </p>

      <label for="token-gate-input">API token</label>
      <input
        id="token-gate-input"
        ref="tokenInput"
        v-model="draft"
        type="password"
        autocomplete="current-password"
        :aria-describedby="auth.rejected ? 'token-gate-error' : undefined"
        :aria-invalid="auth.rejected ? 'true' : undefined"
      />

      <!-- One region, mounted whether or not it has anything to say, and
           carrying the live role itself rather than duplicating into an
           sr-only twin that would announce the same words twice. -->
      <p id="token-gate-error" class="token-gate-error" role="status">{{ announcement }}</p>

      <button type="submit" class="btn btn-primary" :disabled="draft.trim() === ''">
        Unlock
      </button>
    </form>
  </main>
</template>

<style scoped>
.token-gate {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: var(--space-4);
}

.token-gate-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  max-width: 30rem;
}

.token-gate-card input {
  padding: var(--space-2) var(--space-3);
  background: var(--bg-input);
  border: 1px solid var(--border-default);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  font: inherit;
}

.token-gate-card input:focus-visible {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 30%, transparent);
}

/* Zero-height when it has nothing to say, rather than removed: display:none
   takes it out of the accessibility tree, which is the bug the persistent
   mount exists to avoid. */
.token-gate-error {
  margin: 0;
  color: var(--color-error);
}
</style>
