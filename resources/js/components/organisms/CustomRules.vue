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
  <div class="pref-section">
    <h3>Custom Rules</h3>
    <p class="help-text">Natural language rules like "avoid horror" or "prefer sci-fi".</p>
    <div id="customRulesList">
      <div v-if="prefs.customRules.length === 0" class="empty-rules">
        No custom rules defined
      </div>
      <div v-for="(rule, index) in prefs.customRules" :key="index" class="rule-item">
        <span class="rule-text">{{ rule }}</span>
        <button class="btn btn-small btn-danger" @click="prefs.removeRule(index)">Remove</button>
      </div>
    </div>
    <div class="add-rule-form">
      <input
        type="text"
        v-model="newRule"
        placeholder='e.g., avoid horror, prefer sci-fi'
        @keypress="onKeypress"
      >
      <button class="btn btn-small btn-primary" @click="addRule">Add Rule</button>
    </div>
  </div>
</template>
