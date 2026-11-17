<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJson } from '../api'

const data = ref(null)
const err = ref('')

onMounted(async () => {
  try {
    data.value = await getJson('/api/dashboard')
  } catch (e) {
    err.value = String(e.message || e)
  }
})

function yuan(cents) {
  return (Number(cents || 0) / 100).toFixed(2)
}

const maxCount = computed(() => {
  if (!data.value) return 1
  return Math.max(data.value.正向笔数, data.value.冲正笔数, data.value.待归并, 1)
})
</script>

<template>
  <div>
    <p v-if="err" class="msg">{{ err }}</p>
    <template v-if="data">
      <section class="hero-balance">
        <div>
          <p class="label">已对外余额合计 · 对外口径</p>
          <p class="amount">¥ {{ yuan(data.已对外余额合计分) }}</p>
          <p class="hint">
            配平暂存 ¥{{ yuan(data.暂存余额合计分) }}（待合并 {{ data.待合并账户数 }} 户）
            · 合计 ¥{{ yuan(data.余额合计分) }}
            <br />
            待归并 {{ data.待归并 }} 笔
            <template v-if="data.最近归并">
              · 最近归并 #{{ data.最近归并.id }}（隔离 {{ data.最近归并.quarantine_count }}）
            </template>
            <template v-if="data.最近合并">
              · 最近合并 #{{ data.最近合并.id }}（并入 ¥{{ yuan(data.最近合并.staged_total_cents) }}）
            </template>
          </p>
        </div>
        <div class="meter">
          <div class="meter-row">
            <span class="n">正向</span>
            <div class="bar"><span :style="{ width: (data.正向笔数 / maxCount * 100) + '%' }"></span></div>
            <span class="v">{{ data.正向笔数 }}</span>
          </div>
          <div class="meter-row">
            <span class="n">冲正</span>
            <div class="bar"><span :style="{ width: (data.冲正笔数 / maxCount * 100) + '%' }"></span></div>
            <span class="v">{{ data.冲正笔数 }}</span>
          </div>
          <div class="meter-row">
            <span class="n">未平</span>
            <div class="bar"><span :style="{ width: (data.未平隔离 / maxCount * 100) + '%' }"></span></div>
            <span class="v">{{ data.未平隔离 }}</span>
          </div>
        </div>
      </section>

      <h2 class="section-title">账户余额（已对外 / 暂存分列）</h2>
      <div class="panel">
        <table>
          <thead>
            <tr>
              <th>编码</th><th>名称</th><th>已对外（元）</th><th>暂存（元）</th><th>合计（元）</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="a in data.账户" :key="a.id">
              <td>{{ a.code }}</td>
              <td>{{ a.name }}</td>
              <td>{{ yuan(a.posted_cents) }}</td>
              <td>{{ yuan(a.staging_cents) }}</td>
              <td>{{ yuan(a.total_cents) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </div>
</template>
