<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useAuthStore, type AuthStatus } from '@/stores/auth'

const MESSAGES: Partial<Record<AuthStatus, string>> = {
  verifying: 'Checking token…',
  rejected: 'That token was not accepted. Check it and try again.',
  // Covers both causes of 'unreachable': a 500 reached the server, so wording
  // that asserts it could not be reached describes a failure that did not happen.
  unreachable: 'The server did not confirm the token. Check that it is running, then try again.',
}

const auth = useAuthStore()

const draft = ref('')
const tokenInput = ref<HTMLInputElement | null>(null)

// Mounted persistently and bound to a computed, because a live region inserted
// with v-if once it has content is read as page content and skipped.
const announcement = computed(() => MESSAGES[auth.status] ?? '')

const verifying = computed(() => auth.status === 'verifying')
const refused = computed(() => auth.status === 'rejected')
const failed = computed(() => refused.value || auth.status === 'unreachable')

// The draft survives a refusal: re-pasting a long secret into a masked field
// nobody can proofread is the whole cost of getting one character wrong.
async function submit(): Promise<void> {
  if (await auth.submitToken(draft.value)) draft.value = ''
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
        :aria-describedby="announcement ? 'token-gate-status' : undefined"
        :aria-invalid="refused ? 'true' : undefined"
      />

      <!-- One region, mounted whether or not it has anything to say, and
           carrying the live role itself rather than duplicating into an
           sr-only twin that would announce the same words twice. -->
      <p
        id="token-gate-status"
        class="token-gate-status"
        :class="{ failed }"
        role="status"
      >{{ announcement }}</p>

      <button
        type="submit"
        class="btn btn-primary"
        :disabled="draft.trim() === ''"
        :aria-disabled="verifying ? 'true' : undefined"
      >
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

/* --border-default is only 1.36:1 on the card, which leaves the field with no
   visible edge. --text-muted clears the 3:1 a control boundary needs (4.26:1
   on Nord, 3.58:1 on Snowstorm). */
.token-gate-card input {
  padding: var(--space-2) var(--space-3);
  background: var(--bg-input);
  border: 1px solid var(--text-muted);
  border-radius: var(--radius-sm);
  color: var(--text-primary);
  font: inherit;
}

/* Zero-height when it has nothing to say, rather than removed: display:none
   takes it out of the accessibility tree, which is the bug the persistent
   mount exists to avoid. */
.token-gate-status {
  margin: 0;
  color: var(--text-secondary);
}

.token-gate-status.failed {
  color: var(--color-error-text);
}
</style>
