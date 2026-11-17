<script setup>
import { computed, onMounted, ref } from 'vue'
import { getJson, postJson } from '../api'

const runs = ref([])
const accounts = ref([])
const merges = ref([])
const err = ref('')
const msg = ref('')
const busy = ref(false)

async function load() {
  const [rs, as, ms] = await Promise.all([
    getJson('/api/settle-runs'),
    getJson('/api/accounts'),
    getJson('/api/balance-merges'),
  ])
  runs.value = rs
  accounts.value = as
  merges.value = ms
}

onMounted(async () => {
  try {
    await load()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

const totals = computed(() => {
  const posted = accounts.value.reduce((s, a) => s + Number(a.posted_cents || 0), 0)
  const staging = accounts.value.reduce((s, a) => s + Number(a.staging_cents || 0), 0)
  return { posted, staging, total: posted + staging }
})

const stagedAccounts = computed(() => accounts.value.filter((a) => a.staging_cents !== 0))

async function runSettle(failure) {
  busy.value = true
  err.value = ''
  msg.value = ''
  try {
    const url = failure ? '/api/settle-runs?simulate_failure=true' : '/api/settle-runs'
    const r = await postJson(url)
    msg.value = `归并完成 #${r.id}：净额 ${yuan(r.net_cents)} 已写入配平暂存，隔离 ${r.quarantine_count}；已对外余额未动`
    await load()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

async function merge(failure) {
  busy.value = true
  err.value = ''
  msg.value = ''
  try {
    const url = failure ? '/api/balance-merges?simulate_failure=true' : '/api/balance-merges'
    const m = await postJson(url)
    msg.value = `合并完成 #${m.id}：已对外 ${yuan(m.posted_before_cents)} → ${yuan(m.posted_after_cents)}，暂存清零 ${yuan(m.staged_total_cents)}`
    await load()
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}

function yuan(cents) {
  return (Number(cents || 0) / 100).toFixed(2)
}
</script>

<template>
  <div>
    <h1>归并台</h1>
    <p class="lead">
      跑一轮净额归并：配平结果先写入<strong>配平暂存</strong>，本趟结束前不改已对外余额；
      显式合并才把暂存并入<strong>已对外余额</strong>并清零暂存。
    </p>
    <p class="note-strip">
      本轮只处理「待归并」流水；隔离项不计入任何余额。两套余额分列，对外以已对外余额为准。
    </p>

    <div class="stats">
      <div class="stat">
        <div class="k">已对外余额（元）</div>
        <div class="v">{{ yuan(totals.posted) }}</div>
      </div>
      <div class="stat">
        <div class="k">配平暂存（元）</div>
        <div class="v">{{ yuan(totals.staging) }}</div>
      </div>
      <div class="stat">
        <div class="k">合计（元）· 待合并 {{ stagedAccounts.length }} 户</div>
        <div class="v">{{ yuan(totals.total) }}</div>
      </div>
    </div>

    <div class="row-actions">
      <button class="btn" :disabled="busy" @click="runSettle(false)">跑净额归并（写暂存）</button>
      <button class="btn secondary" :disabled="busy" @click="runSettle(true)">演练归并失败</button>
    </div>
    <p v-if="msg" class="ok">{{ msg }}</p>
    <p v-if="err" class="msg">{{ err }}</p>

    <div class="panel">
      <h2>暂存合并</h2>
      <p class="note-strip">
        合并与清零在同一事务、同一条更新内完成；失败整体回滚，暂存回到合并前，已对外不变。
      </p>
      <div class="row-actions">
        <button class="btn" :disabled="busy || !stagedAccounts.length" @click="merge(false)">
          合并暂存到已对外
        </button>
        <button class="btn secondary" :disabled="busy || !stagedAccounts.length" @click="merge(true)">
          演练合并失败
        </button>
      </div>
      <table>
        <thead>
          <tr><th>账户</th><th>已对外（元）</th><th>暂存（元）</th><th>合计（元）</th></tr>
        </thead>
        <tbody>
          <tr v-for="a in stagedAccounts" :key="a.id">
            <td>{{ a.code }}</td>
            <td>{{ yuan(a.posted_cents) }}</td>
            <td>{{ yuan(a.staging_cents) }}</td>
            <td>{{ yuan(a.total_cents) }}</td>
          </tr>
          <tr v-if="!stagedAccounts.length"><td colspan="4">无待合并暂存</td></tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h2>归并运行</h2>
      <table>
        <thead>
          <tr>
            <th>#</th><th>状态</th><th>正向</th><th>冲正</th><th>已归并</th><th>隔离</th>
            <th>本趟净额写入暂存（元）</th><th>结束</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in runs" :key="r.id">
            <td>{{ r.id }}</td>
            <td>{{ r.status }}</td>
            <td>{{ r.forward_count }}</td>
            <td>{{ r.reversal_count }}</td>
            <td>{{ r.settled_count }}</td>
            <td>{{ r.quarantine_count }}</td>
            <td>{{ yuan(r.net_cents) }}</td>
            <td>{{ r.finished_at || '—' }}</td>
          </tr>
          <tr v-if="!runs.length"><td colspan="8">尚无归并运行</td></tr>
        </tbody>
      </table>
    </div>

    <div class="panel">
      <h2>合并批次</h2>
      <table>
        <thead>
          <tr>
            <th>#</th><th>状态</th><th>账户数</th><th>暂存合计（元）</th>
            <th>已对外前（元）</th><th>已对外后（元）</th><th>结束</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="m in merges" :key="m.id">
            <td>{{ m.id }}</td>
            <td>{{ m.status }}</td>
            <td>{{ m.account_count }}</td>
            <td>{{ yuan(m.staged_total_cents) }}</td>
            <td>{{ yuan(m.posted_before_cents) }}</td>
            <td>{{ yuan(m.posted_after_cents) }}</td>
            <td>{{ m.finished_at || '—' }}</td>
          </tr>
          <tr v-if="!merges.length"><td colspan="7">尚无合并批次</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
