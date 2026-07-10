<template>
  <main class="auth-page">
    <section class="auth-card forbidden-card">
      <div class="auth-brand-mark">403</div>
      <h1>无权访问管理员后台</h1>
      <p>当前账号没有管理员权限。用户业务功能请返回用户端。</p>
      <router-link class="back-button" to="/">返回用户端</router-link>
    </section>
  </main>
</template>

<style scoped>
.forbidden-card {
  display: grid;
  justify-items: start;
  gap: 16px;
}

.back-button {
  padding: 11px 15px;
  border-radius: 11px;
  background: #2563eb;
  color: #ffffff;
  font-weight: 800;
  text-decoration: none;
}
</style>
