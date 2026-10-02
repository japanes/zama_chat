<template>
  <NuxtLayout>
    <NuxtPage />
  </NuxtLayout>
</template>

<script setup lang="ts">
// the theme is applied before the first paint of a page (ThemeToggle keeps it in sync)
if (import.meta.client) {
  try {
    const saved = localStorage.getItem('theme')
    const dark = saved ? saved === 'dark' : window.matchMedia('(prefers-color-scheme: dark)').matches
    document.documentElement.classList.toggle('dark', dark)
  } catch {
    // storage blocked: the light theme stays
  }
}
</script>
