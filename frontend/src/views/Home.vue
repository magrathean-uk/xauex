<template>
  <div class="home-container">
    <!-- 顶部导航栏 -->
    <nav class="navbar">
      <div class="nav-brand">MIROFISH</div>
      <div class="nav-links">
        <LanguageSwitcher />
        <a href="https://github.com/666ghj/MiroFish" target="_blank" class="github-link">
          {{ $t('nav.visitGithub') }} <span class="arrow">↗</span>
        </a>
      </div>
    </nav>

    <div class="main-content">
      <!-- 上半部分：Hero 区域 -->
      <section class="hero-section">
        <div class="hero-left">
          <div class="tag-row">
            <span class="orange-tag">{{ $t('home.tagline') }}</span>
            <span class="version-text">{{ $t('home.version') }}</span>
          </div>
          
          <h1 class="main-title">
            {{ $t('home.heroTitle1') }}<br>
            <span class="gradient-text">{{ $t('home.heroTitle2') }}</span>
          </h1>
          
          <div class="hero-desc">
            <p>
              <i18n-t keypath="home.heroDesc" tag="span">
                <template #brand><span class="highlight-bold">{{ $t('home.heroDescBrand') }}</span></template>
                <template #agentScale><span class="highlight-orange">{{ $t('home.heroDescAgentScale') }}</span></template>
                <template #optimalSolution><span class="highlight-code">{{ $t('home.heroDescOptimalSolution') }}</span></template>
              </i18n-t>
            </p>
            <p class="slogan-text">
              {{ $t('home.slogan') }}<span class="blinking-cursor">_</span>
            </p>
          </div>
           
          <div class="decoration-square"></div>
        </div>
        
        <div class="hero-right">
          <!-- Logo 区域 -->
          <div class="logo-container">
            <img src="../assets/logo/MiroFish_logo_left.jpeg" alt="MiroFish Logo" class="hero-logo" />
          </div>
          
          <button class="scroll-down-btn" @click="scrollToBottom">
            ↓
          </button>
        </div>
      </section>

      <!-- 下半部分：双栏布局 -->
      <!-- Trading Signal Banner -->
      <section class="signal-banner" v-if="signal.loaded">
        <div class="signal-card" :class="signalClass">
          <div class="signal-action">
            <span class="signal-icon">{{ signalIcon }}</span>
            <span class="signal-label">{{ signal.action }}</span>
          </div>
          <div class="signal-meta">
            <span class="signal-symbol">{{ signal.symbol }}</span>
            <span class="signal-confidence" v-if="signal.confidence">
              {{ Math.round(signal.confidence * 100) }}% confidence
            </span>
            <span class="signal-stale" v-if="signal.stale">⚠ stale</span>
          </div>
          <div class="signal-sltp" v-if="signal.action !== 'HOLD' && signal.stop_loss_distance > 0">
            <span class="sl-badge">SL ${{ signal.stop_loss_distance }} away</span>
            <span class="tp-badge">TP ${{ signal.take_profit_distance }} away</span>
            <span class="rr-badge" v-if="signal.risk_reward">RR 1:{{ signal.risk_reward }}</span>
          </div>
          <div class="signal-rationale" v-if="signal.rationale">{{ signal.rationale }}</div>
          <div class="signal-time" v-if="signal.generated_at_utc">
            Generated: {{ formatSignalTime(signal.generated_at_utc) }}
          </div>
          <div class="signal-links" v-if="latestCompleted">
            <button class="signal-link-btn" @click="openLatestReport" :disabled="!latestCompleted.report_id">
              Open latest report
            </button>
            <button class="signal-link-btn" @click="openLatestSimulation" :disabled="!latestCompleted.simulation_id">
              Open latest simulation
            </button>
          </div>
        </div>
      </section>

      <section class="dashboard-section">
        <!-- 左栏：状态与步骤 -->
        <div class="left-panel">
          <div class="panel-header">
            <span class="status-dot">■</span> {{ $t('home.systemStatus') }}
          </div>
          
          <h2 class="section-title">{{ $t('home.systemReady') }}</h2>
          <p class="section-desc">
            {{ $t('home.systemReadyDesc') }}
          </p>
          
          <!-- 数据指标卡片 -->
          <div class="metrics-row">
            <div class="metric-card">
              <div class="metric-value">{{ $t('home.metricLowCost') }}</div>
              <div class="metric-label">{{ $t('home.metricLowCostDesc') }}</div>
            </div>
            <div class="metric-card">
              <div class="metric-value">{{ $t('home.metricHighAvail') }}</div>
              <div class="metric-label">{{ $t('home.metricHighAvailDesc') }}</div>
            </div>
          </div>

          <!-- 项目模拟步骤介绍 (新增区域) -->
          <div class="steps-container">
            <div class="steps-header">
               <span class="diamond-icon">◇</span> {{ $t('home.workflowSequence') }}
            </div>
            <div class="workflow-list">
              <div class="workflow-item">
                <span class="step-num">01</span>
                <div class="step-info">
                  <div class="step-title">{{ $t('home.step01Title') }}</div>
                  <div class="step-desc">{{ $t('home.step01Desc') }}</div>
                </div>
              </div>
              <div class="workflow-item">
                <span class="step-num">02</span>
                <div class="step-info">
                  <div class="step-title">{{ $t('home.step02Title') }}</div>
                  <div class="step-desc">{{ $t('home.step02Desc') }}</div>
                </div>
              </div>
              <div class="workflow-item">
                <span class="step-num">03</span>
                <div class="step-info">
                  <div class="step-title">{{ $t('home.step03Title') }}</div>
                  <div class="step-desc">{{ $t('home.step03Desc') }}</div>
                </div>
              </div>
              <div class="workflow-item">
                <span class="step-num">04</span>
                <div class="step-info">
                  <div class="step-title">{{ $t('home.step04Title') }}</div>
                  <div class="step-desc">{{ $t('home.step04Desc') }}</div>
                </div>
              </div>
              <div class="workflow-item">
                <span class="step-num">05</span>
                <div class="step-info">
                  <div class="step-title">{{ $t('home.step05Title') }}</div>
                  <div class="step-desc">{{ $t('home.step05Desc') }}</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <!-- 右栏：交互控制台 -->
        <div class="right-panel">
          <div class="console-box">
            <div class="console-section">
              <div class="console-header">
                <span class="console-label">Oracle Control Surface</span>
                <span class="console-meta">Scheduled autonomous mode</span>
              </div>

              <div class="ops-panel">
                <div class="ops-summary">
                  <div class="ops-title">Manual simulation launch is disabled on the homepage.</div>
                  <div class="ops-text">
                    The site is configured for scheduled oracle runs. Use the latest signal and saved run artifacts to review outcomes instead of creating ad hoc simulations here.
                  </div>
                </div>

                <div class="ops-grid">
                  <div class="ops-card">
                    <div class="ops-card-label">Latest completed run</div>
                    <div class="ops-card-value">
                      {{ latestCompleted ? formatSimulationLabel(latestCompleted.simulation_id) : 'No completed run yet' }}
                    </div>
                    <div class="ops-card-subtle" v-if="latestCompleted?.created_at">
                      {{ formatSignalTime(latestCompleted.created_at) }}
                    </div>
                  </div>

                  <div class="ops-card">
                    <div class="ops-card-label">Latest report</div>
                    <div class="ops-card-value">
                      {{ latestCompleted?.report_id || 'No report yet' }}
                    </div>
                    <div class="ops-card-subtle" v-if="latestCompleted?.status">
                      {{ latestCompleted.status }}
                    </div>
                  </div>
                </div>

                <div class="ops-actions">
                  <button class="start-engine-btn" @click="openLatestReport" :disabled="!latestCompleted?.report_id">
                    <span>Open latest report</span>
                    <span class="btn-arrow">→</span>
                  </button>
                  <button class="secondary-action-btn" @click="openLatestSimulation" :disabled="!latestCompleted?.simulation_id">
                    <span>Open latest simulation</span>
                  </button>
                  <button class="secondary-action-btn" @click="scrollToHistory">
                    <span>Browse history</span>
                  </button>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <!-- 历史项目数据库 -->
      <HistoryDatabase />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import HistoryDatabase from '../components/HistoryDatabase.vue'
import LanguageSwitcher from '../components/LanguageSwitcher.vue'

const router = useRouter()

// ── Trading Signal ─────────────────────────────────────────────────
const signal = ref({ loaded: false, action: 'HOLD', symbol: 'XAUUSD', confidence: 0, rationale: '', stale: false, generated_at_utc: '' })

const signalClass = computed(() => ({
  'signal-buy':  signal.value.action === 'BUY',
  'signal-sell': signal.value.action === 'SELL',
  'signal-hold': signal.value.action === 'HOLD',
  'signal-stale-card': signal.value.stale,
}))

const signalIcon = computed(() => ({
  BUY:  '▲',
  SELL: '▼',
  HOLD: '●',
}[signal.value.action] ?? '●'))

const formatSignalTime = (iso) => {
  try {
    return new Date(iso).toLocaleString('en-GB', { timeZone: 'Asia/Singapore', hour12: false })
  } catch { return iso }
}

const formatSimulationLabel = (simulationId) => {
  if (!simulationId) return 'SIM_UNKNOWN'
  return `SIM_${simulationId.replace('sim_', '').slice(0, 6).toUpperCase()}`
}

const latestCompleted = ref(null)
let signalInterval = null

const fetchSignal = async () => {
  try {
    const res = await fetch('/api/report/signal')
    if (!res.ok) return
    const data = await res.json()
    if (data.status === 'ok') {
      signal.value = { loaded: true, ...data }
    }
  } catch { /* backend not yet started */ }
}

const fetchLatestCompleted = async () => {
  try {
    const res = await fetch('/api/simulation/history?limit=20')
    if (!res.ok) return
    const payload = await res.json()
    if (!payload.success || !Array.isArray(payload.data)) return
    latestCompleted.value = payload.data.find((item) => item.report_id) || null
  } catch { /* backend not yet started */ }
}

onMounted(() => {
  fetchSignal()
  fetchLatestCompleted()
  signalInterval = setInterval(() => {
    fetchSignal()
    fetchLatestCompleted()
  }, 60_000)
})

onUnmounted(() => {
  if (signalInterval) {
    clearInterval(signalInterval)
    signalInterval = null
  }
})

// 滚动到底部
const scrollToBottom = () => {
  window.scrollTo({
    top: document.body.scrollHeight,
    behavior: 'smooth'
  })
}

const scrollToHistory = () => {
  const historySection = document.querySelector('.history-database')
  if (historySection) {
    historySection.scrollIntoView({ behavior: 'smooth', block: 'start' })
    return
  }
  scrollToBottom()
}

const openLatestReport = () => {
  if (!latestCompleted.value?.report_id) return
  router.push({
    name: 'Report',
    params: { reportId: latestCompleted.value.report_id }
  })
}

const openLatestSimulation = () => {
  if (!latestCompleted.value?.simulation_id) return
  router.push({
    name: 'Simulation',
    params: { simulationId: latestCompleted.value.simulation_id }
  })
}
</script>

<style scoped>
/* 全局变量与重置 */
:root {
  --black: #000000;
  --white: #FFFFFF;
  --orange: #FF4500;
  --gray-light: #F5F5F5;
  --gray-text: #666666;
  --border: #E5E5E5;
  /* 
    使用 Space Grotesk 作为主要标题字体，JetBrains Mono 作为代码/标签字体
    确保已在 index.html 引入这些 Google Fonts 
  */
  --font-mono: 'JetBrains Mono', monospace;
  --font-sans: 'Space Grotesk', 'Noto Sans SC', system-ui, sans-serif;
  --font-cn: 'Noto Sans SC', system-ui, sans-serif;
}

.home-container {
  min-height: 100vh;
  background: var(--white);
  font-family: var(--font-sans);
  color: var(--black);
}

/* 顶部导航 */
.navbar {
  height: 60px;
  background: var(--black);
  color: var(--white);
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0 40px;
}

.nav-brand {
  font-family: var(--font-mono);
  font-weight: 800;
  letter-spacing: 1px;
  font-size: 1.2rem;
}

.nav-links {
  display: flex;
  align-items: center;
  gap: 16px;
}

.github-link {
  color: var(--white);
  text-decoration: none;
  font-family: var(--font-mono);
  font-size: 0.9rem;
  font-weight: 500;
  display: flex;
  align-items: center;
  gap: 8px;
  transition: opacity 0.2s;
}

.github-link:hover {
  opacity: 0.8;
}

.arrow {
  font-family: sans-serif;
}

/* 主要内容区 */
.main-content {
  max-width: 1400px;
  margin: 0 auto;
  padding: 60px 40px;
}

/* Hero 区域 */
.hero-section {
  display: flex;
  justify-content: space-between;
  margin-bottom: 80px;
  position: relative;
}

.hero-left {
  flex: 1;
  padding-right: 60px;
}

.tag-row {
  display: flex;
  align-items: center;
  gap: 15px;
  margin-bottom: 25px;
  font-family: var(--font-mono);
  font-size: 0.8rem;
}

.orange-tag {
  background: var(--orange);
  color: var(--white);
  padding: 4px 10px;
  font-weight: 700;
  letter-spacing: 1px;
  font-size: 0.75rem;
}

.version-text {
  color: #999;
  font-weight: 500;
  letter-spacing: 0.5px;
}

.main-title {
  font-size: 4.5rem;
  line-height: 1.2;
  font-weight: 500;
  margin: 0 0 40px 0;
  letter-spacing: -2px;
  color: var(--black);
}

.gradient-text {
  background: linear-gradient(90deg, #000000 0%, #444444 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  display: inline-block;
}

.hero-desc {
  font-size: 1.05rem;
  line-height: 1.8;
  color: var(--gray-text);
  max-width: 640px;
  margin-bottom: 50px;
  font-weight: 400;
  text-align: justify;
}

.hero-desc p {
  margin-bottom: 1.5rem;
}

.highlight-bold {
  color: var(--black);
  font-weight: 700;
}

.highlight-orange {
  color: var(--orange);
  font-weight: 700;
  font-family: var(--font-mono);
}

.highlight-code {
  background: rgba(0, 0, 0, 0.05);
  padding: 2px 6px;
  border-radius: 2px;
  font-family: var(--font-mono);
  font-size: 0.9em;
  color: var(--black);
  font-weight: 600;
}

.slogan-text {
  font-size: 1.2rem;
  font-weight: 520;
  color: var(--black);
  letter-spacing: 1px;
  border-left: 3px solid var(--orange);
  padding-left: 15px;
  margin-top: 20px;
}

.blinking-cursor {
  color: var(--orange);
  animation: blink 1s step-end infinite;
  font-weight: 700;
}

@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0; }
}

.decoration-square {
  width: 16px;
  height: 16px;
  background: var(--orange);
}

.hero-right {
  flex: 0.8;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  align-items: flex-end;
}

.logo-container {
  width: 100%;
  display: flex;
  justify-content: flex-end;
  padding-right: 40px;
}

.hero-logo {
  max-width: 500px; /* 调整logo大小 */
  width: 100%;
}

.scroll-down-btn {
  width: 40px;
  height: 40px;
  border: 1px solid var(--border);
  background: transparent;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  color: var(--orange);
  font-size: 1.2rem;
  transition: all 0.2s;
}

.scroll-down-btn:hover {
  border-color: var(--orange);
}

/* Dashboard 双栏布局 */
.dashboard-section {
  display: flex;
  gap: 60px;
  border-top: 1px solid var(--border);
  padding-top: 60px;
  align-items: flex-start;
}

.dashboard-section .left-panel,
.dashboard-section .right-panel {
  display: flex;
  flex-direction: column;
}

/* 左侧面板 */
.left-panel {
  flex: 0.8;
}

.panel-header {
  font-family: var(--font-mono);
  font-size: 0.8rem;
  color: #999;
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 20px;
}

.status-dot {
  color: var(--orange);
  font-size: 0.8rem;
}

.section-title {
  font-size: 2rem;
  font-weight: 520;
  margin: 0 0 15px 0;
}

.section-desc {
  color: var(--gray-text);
  margin-bottom: 25px;
  line-height: 1.6;
}

.metrics-row {
  display: flex;
  gap: 20px;
  margin-bottom: 15px;
}

.metric-card {
  border: 1px solid var(--border);
  padding: 20px 30px;
  min-width: 150px;
}

.metric-value {
  font-family: var(--font-mono);
  font-size: 1.8rem;
  font-weight: 520;
  margin-bottom: 5px;
}

.metric-label {
  font-size: 0.85rem;
  color: #999;
}

/* 项目模拟步骤介绍 */
.steps-container {
  border: 1px solid var(--border);
  padding: 30px;
  position: relative;
}

.steps-header {
  font-family: var(--font-mono);
  font-size: 0.8rem;
  color: #999;
  margin-bottom: 25px;
  display: flex;
  align-items: center;
  gap: 8px;
}

.diamond-icon {
  font-size: 1.2rem;
  line-height: 1;
}

.workflow-list {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.workflow-item {
  display: flex;
  align-items: flex-start;
  gap: 20px;
}

.step-num {
  font-family: var(--font-mono);
  font-weight: 700;
  color: var(--black);
  opacity: 0.3;
}

.step-info {
  flex: 1;
}

.step-title {
  font-weight: 520;
  font-size: 1rem;
  margin-bottom: 4px;
}

.step-desc {
  font-size: 0.85rem;
  color: var(--gray-text);
}

/* 右侧交互控制台 */
.right-panel {
  flex: 1.2;
}

.console-box {
  border: 1px solid #CCC; /* 外部实线 */
  padding: 8px; /* 内边距形成双重边框感 */
}

.console-section {
  padding: 20px;
}

.console-section.btn-section {
  padding-top: 0;
}

.console-header {
  display: flex;
  justify-content: space-between;
  margin-bottom: 15px;
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: #666;
}

.upload-zone {
  border: 1px dashed #CCC;
  height: 200px;
  overflow-y: auto;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  transition: all 0.3s;
  background: #FAFAFA;
}

.upload-zone.has-files {
  align-items: flex-start;
}

.upload-zone:hover {
  background: #F0F0F0;
  border-color: #999;
}

.upload-placeholder {
  text-align: center;
}

.upload-icon {
  width: 40px;
  height: 40px;
  border: 1px solid #DDD;
  display: flex;
  align-items: center;
  justify-content: center;
  margin: 0 auto 15px;
  color: #999;
}

.upload-title {
  font-weight: 500;
  font-size: 0.9rem;
  margin-bottom: 5px;
}

.upload-hint {
  font-family: var(--font-mono);
  font-size: 0.75rem;
  color: #999;
}

.file-list {
  width: 100%;
  padding: 15px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.file-item {
  display: flex;
  align-items: center;
  background: var(--white);
  padding: 8px 12px;
  border: 1px solid #EEE;
  font-family: var(--font-mono);
  font-size: 0.85rem;
}

.file-name {
  flex: 1;
  margin: 0 10px;
}

.remove-btn {
  background: none;
  border: none;
  cursor: pointer;
  font-size: 1.2rem;
  color: #999;
}

.console-divider {
  display: flex;
  align-items: center;
  margin: 10px 0;
}

.console-divider::before,
.console-divider::after {
  content: '';
  flex: 1;
  height: 1px;
  background: #EEE;
}

.console-divider span {
  padding: 0 15px;
  font-family: var(--font-mono);
  font-size: 0.7rem;
  color: #BBB;
  letter-spacing: 1px;
}

.input-wrapper {
  position: relative;
  border: 1px solid #DDD;
  background: #FAFAFA;
}

.code-input {
  width: 100%;
  border: none;
  background: transparent;
  padding: 20px;
  font-family: var(--font-mono);
  font-size: 0.9rem;
  line-height: 1.6;
  resize: vertical;
  outline: none;
  min-height: 150px;
}

.model-badge {
  position: absolute;
  bottom: 10px;
  right: 15px;
  font-family: var(--font-mono);
  font-size: 0.7rem;
  color: #AAA;
}

.start-engine-btn {
  width: 100%;
  background: var(--black);
  color: var(--white);
  border: none;
  padding: 20px;
  font-family: var(--font-mono);
  font-weight: 700;
  font-size: 1.1rem;
  display: flex;
  justify-content: space-between;
  align-items: center;
  cursor: pointer;
  transition: all 0.3s ease;
  letter-spacing: 1px;
  position: relative;
  overflow: hidden;
}

/* 可点击状态（非禁用） */
.start-engine-btn:not(:disabled) {
  background: var(--black);
  border: 1px solid var(--black);
  animation: pulse-border 2s infinite;
}

.start-engine-btn:hover:not(:disabled) {
  background: var(--orange);
  border-color: var(--orange);
  transform: translateY(-2px);
}

.start-engine-btn:active:not(:disabled) {
  transform: translateY(0);
}

.start-engine-btn:disabled {
  background: #E5E5E5;
  color: #999;
  cursor: not-allowed;
  transform: none;
  border: 1px solid #E5E5E5;
}

/* 引导动画：微妙的边框脉冲 */
@keyframes pulse-border {
  0% { box-shadow: 0 0 0 0 rgba(0, 0, 0, 0.2); }
  70% { box-shadow: 0 0 0 6px rgba(0, 0, 0, 0); }
  100% { box-shadow: 0 0 0 0 rgba(0, 0, 0, 0); }
}

/* 响应式适配 */
@media (max-width: 1024px) {
  .dashboard-section {
    flex-direction: column;
  }
  
  .hero-section {
    flex-direction: column;
  }
  
  .hero-left {
    padding-right: 0;
    margin-bottom: 40px;
  }
  
  .hero-logo {
    max-width: 200px;
    margin-bottom: 20px;
  }
}
</style>

<style>
/* English locale adjustments (unscoped to target html[lang]) */
html[lang="en"] .main-title {
  font-size: 3.5rem;
  font-family: 'Space Grotesk', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  letter-spacing: -1px;
}

html[lang="en"] .hero-desc {
  text-align: left;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  letter-spacing: 0;
}

html[lang="en"] .slogan-text {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  letter-spacing: 0;
}

html[lang="en"] .tag-row {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

html[lang="en"] .navbar .nav-links {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

/* Left pane: system status + workflow */
html[lang="en"] .status-section {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

html[lang="en"] .status-section .status-ready {
  font-size: 1.6rem;
}

html[lang="en"] .status-section .metric-value {
  font-family: 'Space Grotesk', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  font-size: 1.4rem;
}

html[lang="en"] .workflow-list .step-title {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

html[lang="en"] .workflow-list .step-desc {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif !important;
  font-size: 0.72rem !important;
  line-height: 1.4 !important;
}

html[lang="en"] .workflow-list {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}

/* ── Signal Banner ─────────────────────────────────── */
.signal-banner {
  padding: 0 2rem 1.5rem;
}
.signal-card {
  border-radius: 12px;
  padding: 1.25rem 1.5rem;
  border: 1px solid rgba(255,255,255,0.1);
  background: rgba(255,255,255,0.04);
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.75rem;
}
.signal-buy   { border-color: #22c55e; background: rgba(34,197,94,0.08); }
.signal-sell  { border-color: #ef4444; background: rgba(239,68,68,0.08); }
.signal-hold  { border-color: #f59e0b; background: rgba(245,158,11,0.06); }
.signal-stale-card { opacity: 0.65; }
.signal-action {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.signal-icon { font-size: 1.4rem; }
.signal-label {
  font-size: 1.6rem;
  font-weight: 700;
  letter-spacing: 0.06em;
}
.signal-buy  .signal-label { color: #22c55e; }
.signal-sell .signal-label { color: #ef4444; }
.signal-hold .signal-label { color: #f59e0b; }
.signal-meta {
  display: flex;
  gap: 0.75rem;
  align-items: center;
  font-size: 0.9rem;
  color: #aaa;
}
.signal-symbol { font-weight: 600; color: #ccc; }
.signal-confidence { color: #888; }
.signal-stale { color: #f59e0b; }
.signal-sltp {
  display: flex;
  gap: 0.6rem;
  flex-wrap: wrap;
  align-items: center;
  width: 100%;
}
.sl-badge, .tp-badge, .rr-badge {
  padding: 0.2rem 0.6rem;
  border-radius: 6px;
  font-size: 0.8rem;
  font-weight: 600;
  letter-spacing: 0.03em;
}
.sl-badge { background: rgba(239,68,68,0.15); color: #f87171; border: 1px solid rgba(239,68,68,0.3); }
.tp-badge { background: rgba(34,197,94,0.12); color: #4ade80; border: 1px solid rgba(34,197,94,0.3); }
.rr-badge { background: rgba(168,85,247,0.12); color: #c084fc; border: 1px solid rgba(168,85,247,0.3); }
.signal-rationale {
  width: 100%;
  font-size: 0.85rem;
  color: #999;
  line-height: 1.4;
}
.signal-time {
  width: 100%;
  font-size: 0.75rem;
  color: #666;
}
.signal-links {
  width: 100%;
  display: flex;
  gap: 0.75rem;
  flex-wrap: wrap;
}
.signal-link-btn,
.secondary-action-btn {
  border: 1px solid #d6d6d6;
  background: #fff;
  color: #111;
  border-radius: 10px;
  padding: 0.7rem 1rem;
  font-weight: 600;
  cursor: pointer;
  transition: border-color 0.2s ease, transform 0.2s ease, background 0.2s ease;
}
.signal-link-btn:hover:not(:disabled),
.secondary-action-btn:hover:not(:disabled) {
  border-color: #111;
  transform: translateY(-1px);
}
.signal-link-btn:disabled,
.secondary-action-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.ops-panel {
  border: 1px solid var(--border);
  border-radius: 18px;
  background: linear-gradient(180deg, #fff, #fbfbfb);
  padding: 24px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}
.ops-summary {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ops-title {
  font-size: 1.1rem;
  font-weight: 700;
  color: var(--black);
}
.ops-text {
  color: var(--gray-text);
  line-height: 1.6;
  font-size: 0.95rem;
}
.ops-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}
.ops-card {
  border: 1px solid var(--border);
  border-radius: 14px;
  padding: 16px;
  background: #fff;
}
.ops-card-label {
  font-family: var(--font-mono);
  font-size: 0.78rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--gray-text);
  margin-bottom: 8px;
}
.ops-card-value {
  font-size: 1rem;
  font-weight: 700;
  color: var(--black);
  line-height: 1.4;
}
.ops-card-subtle {
  margin-top: 8px;
  font-size: 0.85rem;
  color: var(--gray-text);
}
.ops-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}
.ops-actions .start-engine-btn {
  min-width: 220px;
}
@media (max-width: 900px) {
  .ops-grid {
    grid-template-columns: 1fr;
  }
  .signal-links,
  .ops-actions {
    flex-direction: column;
  }
}
</style>
