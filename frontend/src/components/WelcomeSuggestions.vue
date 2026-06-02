<script setup lang="ts">
import { ref, onMounted } from 'vue'

defineProps<{ onSelect: (text: string) => void }>()

const POOL = [
  '资讯：英伟达 Vera Rubin 平台量产对 AI 算力格局的影响',
  '资讯：青盾计划深度解读——国内未成年人网络保护政策演进',
  '资讯：美国大厂反思 AI Token 消耗背后的成本与效率博弈',
  '资讯：国内大模型价格战对行业生态的深远影响',
  '深度对比 Transformer 与 Mamba 架构的优劣及适用场景',
  '全球主要国家 AI 监管政策对比分析报告',
  'RISC-V 与 ARM 生态现状及未来趋势调研',
  '6G 技术研发进展及关键候选技术综述',
  '2026 年大模型行业发展趋势调研',
  'Sora 等视频生成模型的技术原理与当前局限',
  'LLM 推理优化技术综述（量化 / 蒸馏 / MoE）',
  'AI Agent 落地应用现状与挑战',
  '帮我整理一下全球芯片制造工艺演进时间线',
  '低空经济产业链全景图及政策梳理',
  '开源大模型与闭源大模型的性能差距还有多大？',
  'AIGC 在游戏开发中的落地案例调研',
  '脑机接口技术最新进展综述',
  '量子计算与 AI 结合的潜在突破方向',
  '新能源车智能驾驶方案对比分析',
  '帮我做一个 AI 编程助手市场格局调研',
]

function isNews(text: string) {
  return text.startsWith('资讯：')
}

function shuffle(arr: string[]) {
  const a = [...arr]
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]]
  }
  return a
}

const cards = ref<string[]>([])

onMounted(() => {
  cards.value = shuffle(POOL).slice(0, 10)
})
</script>

<template>
  <div class="welcome-container">
    <div class="welcome-title">有什么我能帮你的吗？</div>
    <div class="grid">
      <div
        v-for="text in cards"
        :key="text"
        class="card"
        :class="{ 'news-card': isNews(text) }"
        @click="onSelect(text)"
      >
        <div v-if="isNews(text)" class="news-tag">热点</div>
        {{ text }}
      </div>
    </div>
  </div>
</template>

<style scoped>
.welcome-container {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px 16px;
  overflow-y: auto;
}
.welcome-title {
  font-size: 22px;
  font-weight: 700;
  color: var(--color-text-primary);
  margin-bottom: 28px;
  letter-spacing: -0.3px;
}
.grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  max-width: 680px;
  width: 100%;
}
.card {
  padding: 14px 16px;
  border-radius: var(--radius-md);
  cursor: pointer;
  font-size: 13px;
  line-height: 1.5;
  color: var(--color-text-primary);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  transition: all var(--transition-fast);
}
.card:hover {
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
  border-color: var(--color-primary);
}
.news-card {
  border-left: 3px solid var(--color-primary);
}
.news-tag {
  font-size: 11px;
  color: var(--color-primary);
  font-weight: 600;
  margin-bottom: 3px;
}
</style>
