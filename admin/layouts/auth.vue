<template>
  <div class="min-h-screen flex flex-col bg-slate-50 dark:bg-slate-900">
    <header class="h-16 flex items-center justify-between px-4 md:px-8">
      <div class="flex items-center gap-2.5">
        <span class="grid place-items-center w-9 h-9 rounded-button bg-gradient-to-br from-secondary to-primary text-white font-heading font-bold">Z</span>
        <span class="font-heading font-bold text-slate-900 dark:text-white">Zama Chat</span>
      </div>
      <div class="flex items-center gap-2">
        <LanguageSwitcher />
        <ThemeToggle />
      </div>
    </header>
    <main class="flex-1 grid place-items-center px-4 py-8">
      <div class="w-full max-w-sm card p-6 md:p-8 rounded-modal shadow-modal">
        <slot />
      </div>
    </main>
  </div>
</template>
