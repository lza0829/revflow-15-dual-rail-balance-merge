<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJson, postJson } from '../api'

const rows = ref([])
const err = ref('')
const msg = ref('')
const busy = ref(false)
const latest = ref(null)

async function load() {
  rows.value = await getJson('/api/verify-reports')
}

onMounted(async () => {
  try {
    await load()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

async function runVerify() {
  busy.value = true
  err.value = ''
  msg.value = ''
  try {
    const r = await postJson('/api/verify-reports')
    latest.value = r
    msg.value = r.consistent
      ? `互证一致：流水净额 ${yuan(r.ledger_net_cents)} = 已对外 ${yuan(r.posted_cents)} + 暂存 ${yuan(r.staging_cents)}`
      : `互证不一致：流水 ${yuan(r.ledger_net_cents)} vs 已对外+暂存 ${yuan(r.total_cents)}`
    await load()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

const mismatch = computed(() => (latest.value ? latest.value.detail['分户不一致'] || [] : []))

function yuan(cents) {
  return (Number(cents || 0) / 100).toFixed(2)
}
</script>

<template>
  <div>
    <h1>净额互证</h1>
    <p class="lead">
      分别给出已对外余额、配平暂存与合计，并与已归并流水净额互证。
      <strong>对外口径以已对外余额为准</strong>，暂存不对外展示，合计仅在内部核对使用。
    </p>
    <p class="note-strip">数字同源：账户双列（posted_cents / staging_cents）直接取自账户表，流水净额取自已归并流水。</p>
    <div class="row-actions">
      <button class="btn" :disabled="busy" @click="runVerify">跑净额互证</button>
    </div>
    <p v-if="msg" :class="msg.includes('不一致') ? 'msg' : 'ok'">{{ msg }}</p>
    <p v-if="err" class="msg">{{ err }}</p>

    <div v-if="latest" class="stats">
      <div class="stat">
        <div class="k">已对外余额（元）· 对外口径</div>
        <div class="v">{{ yuan(latest.posted_cents) }}</div>
      </div>
      <div class="stat">
        <div class="k">配平暂存（元）</div>
        <div class="v">{{ yuan(latest.staging_cents) }}</div>
      </div>
      <div class="stat">
        <div class="k">合计（元）= 已对外 + 暂存</div>
        <div class="v">{{ yuan(latest.total_cents) }}</div>
      </div>
    </div>

    <div v-if="latest && mismatch.length" class="panel">
      <h2>分户不一致</h2>
      <table>
        <thead>
          <tr><th>账户</th><th>流水净额（元）</th><th>已对外（元）</th><th>暂存（元）</th><th>合计（元）</th></tr>
        </thead>
        <tbody>
          <tr v-for="m in mismatch" :key="m.account_id">
            <td>{{ m.account_code }}</td>
            <td>{{ yuan(m.流水净额分) }}</td>
            <td>{{ yuan(m.已对外分) }}</td>
            <td>{{ yuan(m.暂存分) }}</td>
            <td>{{ yuan(m.合计分) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h2>互证报告</h2>
      <table>
        <thead>
          <tr>
            <th>#</th><th>归并#</th><th>流水净额（元）</th><th>已对外（元）</th>
            <th>暂存（元）</th><th>合计（元）</th><th>隔离数</th><th>一致</th><th>对外口径</th><th>时间</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id">
            <td>{{ r.id }}</td>
            <td>{{ r.settle_run_id }}</td>
            <td>{{ yuan(r.ledger_net_cents) }}</td>
            <td>{{ yuan(r.posted_cents) }}</td>
            <td>{{ yuan(r.staging_cents) }}</td>
            <td>{{ yuan(r.total_cents) }}</td>
            <td>{{ r.quarantine_count }}</td>
            <td>
              <span class="tag" :class="r.consistent ? 'on' : 'bad'">
                {{ r.consistent ? '是' : '否' }}
              </span>
            </td>
            <td><span class="tag warn">{{ r.basis }}</span></td>
            <td>{{ r.created_at }}</td>
          </tr>
          <tr v-if="!rows.length"><td colspan="10">尚无报告</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
