<script setup lang="ts">
defineProps<{admin?:boolean}>()
</script>
<template><nav class="product-nav" aria-label="账号服务"><RouterLink to="/product/wallet">积分与会员</RouterLink><RouterLink v-if="admin" to="/product/admin">运营管理</RouterLink></nav></template>
