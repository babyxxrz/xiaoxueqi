<template>
  <div class="forbidden-page">
    <div class="forbidden-card">
      <div class="forbidden-icon">&#x1f6ab;</div>
      <h2>403 - 访问被拒绝</h2>
      <p>抱歉，您没有权限访问此页面。</p>
      <router-link to="/" class="forbidden-link">返回首页</router-link>
    </div>
  </div>
</template>

<script setup>
// 无额外逻辑
</script>

<style scoped>
.forbidden-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  padding: 24px;
  background:
    radial-gradient(circle at top left, rgba(239, 68, 68, 0.06), transparent 34%),
    linear-gradient(135deg, #eef3f8 0%, #f7fafc 100%);
}

.forbidden-card {
  text-align: center;
  max-width: 420px;
  padding: 48px 32px;
  background: rgba(255, 255, 255, 0.92);
  border: 1px solid rgba(211, 222, 235, 0.9);
  border-radius: 24px;
  box-shadow: 0 18px 48px rgba(15, 23, 42, 0.10);
}

.forbidden-icon {
  font-size: 64px;
  margin-bottom: 16px;
}

.forbidden-card h2 {
  font-size: 24px;
  color: #0f172a;
  margin: 0 0 8px;
}

.forbidden-card p {
  color: #64748b;
  margin: 0 0 24px;
  font-size: 15px;
}

.forbidden-link {
  display: inline-block;
  padding: 11px 24px;
  background: #2563eb;
  color: white;
  border-radius: 12px;
  text-decoration: none;
  font-weight: 700;
  box-shadow: 0 10px 22px rgba(37, 99, 235, 0.18);
  transition: transform 0.15s ease;
}

.forbidden-link:hover {
  transform: translateY(-1px);
}
</style>
