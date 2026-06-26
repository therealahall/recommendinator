<script setup lang="ts">
import { ref } from 'vue'
import { usePreferencesStore } from '@/stores/preferences'

const prefs = usePreferencesStore()
const newRule = ref('')

function addRule() {
  if (newRule.value.trim()) {
    prefs.addRule(newRule.value)
    newRule.value = ''
  }
}

function onKeypress(event: KeyboardEvent) {
  if (event.key === 'Enter') addRule()
}
</script>

<template>
  <div>
    <h4>Custom rules</h4>
    <p class="help-text">Natural language rules like "avoid horror" or "prefer sci-fi".</p>
    <div>
      <div v-if="prefs.customRules.length === 0" class="empty-rules">
        No custom rules defined
      </div>
      <ul v-else class="rule-list" role="list">
        <li v-for="(rule, index) in prefs.customRules" :key="index" class="rule-item">
          <span class="rule-text">{{ rule }}</span>
          <button
            class="btn btn-small btn-danger"
            :aria-label="`Remove rule: ${rule}`"
            @click="prefs.removeRule(index)"
          >Remove</button>
        </li>
      </ul>
    </div>
    <div class="add-rule-form">
      <label for="new-rule-input" class="sr-only">New custom rule</label>
      <input
        id="new-rule-input"
        type="text"
        v-model="newRule"
        placeholder='e.g., avoid horror, prefer sci-fi'
        @keypress="onKeypress"
      >
      <button class="btn btn-small btn-primary" @click="addRule">Add Rule</button>
    </div>
  </div>
</template>
