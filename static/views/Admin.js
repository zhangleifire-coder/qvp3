// 系统管理（仅 admin）
const AdminView = {
  data() {
    return { st: null, error: '', msg: '', timer: null, logs: [], showLogs: false,
             costs: null, costTask: null };
  },
  computed: {
    isAdmin() { const u = getUser(); return u && u.role === 'admin'; },
    cycleText() {
      const c = this.st && this.st.cycle;
      if (!c) return '-';
      return c.mode === 'work' ? '工作中' : '检修停机';
    },
    remainText() {
      const c = this.st && this.st.cycle;
      if (!c || c.remaining_seconds == null) return '-';
      const s = Math.max(0, Math.round(c.remaining_seconds));
      const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
      return `${h}h ${m}m`;
    },
    statusCells() {
      const bs = (this.st && this.st.tasks && this.st.tasks.by_status) || {};
      return Object.keys(STATUS).map(k => ({ label: STATUS[k].label, cls: STATUS[k].cls, count: bs[k] || 0 }));
    },
  },
  methods: {
    async load() {
      try { this.st = await api.get('/api/admin/status'); this.error = ''; }
      catch (e) { this.error = e.message; }
    },
    async act(fn, confirmText) {
      if (confirmText && !confirm(confirmText)) return;
      this.error = ''; this.msg = '';
      try { await fn(); this.msg = '操作成功'; this.load(); }
      catch (e) { this.error = e.message; }
    },
    mtStart() { this.act(() => api.post('/api/admin/maintenance/start?actor=' + encodeURIComponent(getUser().name)), '确定进入手动检修？生产将暂停。'); },
    mtEnd() { this.act(() => api.post('/api/admin/maintenance/end?actor=' + encodeURIComponent(getUser().name)), '确定结束检修、恢复生产？'); },
    exportCsv() { location.href = '/api/admin/export?actor=' + encodeURIComponent(getUser().name); },
    async clearAll() {
      if (!confirm('危险操作：将删除全部任务与内容数据！确定继续？')) return;
      if (!confirm('再次确认：此操作不可恢复，确定清空？')) return;
      this.act(() => api.post('/api/admin/clear?actor=' + encodeURIComponent(getUser().name)));
    },
    async loadLogs() {
      this.showLogs = !this.showLogs;
      if (this.showLogs) {
        try { const r = await api.get('/api/admin/logs'); this.logs = r.logs || r || []; }
        catch (e) { this.error = e.message; }
      }
    },
    async loadCosts() {
      try { this.costs = await api.get('/api/admin/costs'); }
      catch (e) { this.error = e.message; }
    },
    toggleCostTask(id) { this.costTask = this.costTask === id ? null : id; },
    fmtTime(s) { return s ? new Date(s).toLocaleString('zh-CN', { hour12: false }) : '-'; },
    fmtMoney(v) { return '¥' + Number(v || 0).toFixed(4); },
    costModeLabel(m) { return (typeof MODE !== 'undefined' && MODE[m]) ? MODE[m].label : (m || '-'); },
    costStatusLabel(s) { return (typeof STATUS !== 'undefined' && STATUS[s]) ? STATUS[s].label : (s || '-'); },
  },
  mounted() {
    if (!this.isAdmin) return;
    this.load(); this.loadCosts(); this.timer = setInterval(this.load, 3000);
  },
  beforeUnmount() { clearInterval(this.timer); },
  template: `
  <app-layout title="系统管理">
    <div v-if="!isAdmin" class="card empty">无权限：仅管理员可访问系统管理。</div>
    <template v-else>
      <p v-if="error" class="form-error">{{ error }}</p>
      <p v-if="msg" class="form-ok">{{ msg }}</p>
      <div class="grid grid-2">
        <div class="card">
          <h2>工作周期</h2>
          <p><span class="tag" :class="st && st.cycle && st.cycle.mode==='work' ? 'tag-green' : 'tag-yellow'">{{ cycleText }}</span></p>
          <p class="muted" style="margin-top:8px" v-if="st && st.cycle">
            距下一事件（{{ st.cycle.next_event || '-' }}）：{{ remainText }}<br>
            周期配置：工作 {{ st.cycle.work_hours }}h / 检修 {{ st.cycle.maintenance_hours }}h
            <span v-if="st.cycle.reason"><br>原因：{{ st.cycle.reason }}</span>
          </p>
          <div style="margin-top:12px;display:flex;gap:8px">
            <button class="btn btn-outline" @click="mtStart">手动检修</button>
            <button class="btn btn-outline" @click="mtEnd">结束检修</button>
          </div>
        </div>
        <div class="card">
          <h2>任务概览</h2>
          <div class="status-row">
            <div v-for="c in statusCells" :key="c.label" class="status-cell">
              <span class="tag" :class="c.cls">{{ c.label }}</span><span class="status-count">{{ c.count }}</span>
            </div>
          </div>
          <p class="muted" style="margin-top:10px" v-if="st && st.tasks">总数：{{ st.tasks.total }}</p>
        </div>
      </div>
      <div class="card">
        <h2>运维操作</h2>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn btn-outline" @click="exportCsv">导出 CSV</button>
          <button class="btn btn-outline" @click="loadLogs">{{ showLogs ? '收起日志' : '查看日志' }}</button>
          <a class="btn btn-outline" href="/api/admin/logs/download" style="text-decoration:none">下载日志</a>
          <button class="btn btn-danger" @click="clearAll">删除全部内容</button>
        </div>
        <pre v-if="showLogs" class="log-box">{{ logs.join('\\n') }}</pre>
      </div>

      <div class="card" v-if="costs">
        <h2>成本明细 <span class="muted" style="font-weight:normal;font-size:13px">全部计费事件逐项拆分，供成本决策（仅管理员可见）</span>
          <button class="btn btn-outline btn-sm" style="float:right" @click="loadCosts">刷新</button></h2>
        <div class="grid grid-4" style="margin:12px 0">
          <div class="stat"><div class="n">{{ fmtMoney(costs.summary.total_cny) }}</div><div class="l">累计总成本</div></div>
          <div class="stat"><div class="n">{{ fmtMoney(costs.summary.total_24h_cny) }}</div><div class="l">24h 成本</div></div>
          <div class="stat"><div class="n">{{ costs.summary.task_count }}</div><div class="l">计费任务数</div></div>
          <div class="stat"><div class="n">{{ fmtMoney(costs.summary.avg_per_task_cny) }}</div><div class="l">单任务平均成本</div></div>
        </div>
        <div class="grid grid-2">
          <div>
            <h3>按环节汇总</h3>
            <table class="table">
              <thead><tr><th>环节</th><th>计费次数</th><th>费用</th><th>占比</th></tr></thead>
              <tbody><tr v-for="n in costs.by_node" :key="n.node">
                <td>{{ n.label }}</td><td>{{ n.count }}</td><td>{{ fmtMoney(n.cost) }}</td>
                <td>{{ costs.summary.total_cny ? Math.round(n.cost / costs.summary.total_cny * 100) : 0 }}%</td>
              </tr></tbody>
            </table>
          </div>
          <div>
            <h3>按模型汇总</h3>
            <table class="table">
              <thead><tr><th>模型</th><th>费用</th><th>占比</th></tr></thead>
              <tbody><tr v-for="m in costs.by_model" :key="m.model">
                <td class="muted">{{ m.model }}</td><td>{{ fmtMoney(m.cost) }}</td>
                <td>{{ costs.summary.total_cny ? Math.round(m.cost / costs.summary.total_cny * 100) : 0 }}%</td>
              </tr></tbody>
            </table>
          </div>
        </div>
        <h3 style="margin-top:14px">按任务明细（点击行展开逐项费用）</h3>
        <div v-if="!costs.tasks.length" class="empty">暂无计费记录</div>
        <template v-else>
          <table class="table" style="margin-bottom:0">
            <thead><tr><th>Query</th><th>模式</th><th>状态</th><th>总费用</th><th></th></tr></thead>
          </table>
          <div v-for="t in costs.tasks" :key="t.task_id">
            <table class="table" style="margin-bottom:0">
              <tbody>
                <tr @click="toggleCostTask(t.task_id)" style="cursor:pointer">
                  <td class="q-cell">{{ t.query }}</td>
                  <td style="width:90px">{{ costModeLabel(t.mode) }}</td>
                  <td style="width:90px">{{ costStatusLabel(t.status) }}</td>
                  <td style="width:110px">{{ fmtMoney(t.total) }}</td>
                  <td class="muted" style="width:30px">{{ costTask === t.task_id ? '▲' : '▼' }}</td>
                </tr>
              </tbody>
            </table>
            <table v-if="costTask === t.task_id" class="table" style="background:#fafbfc">
              <thead><tr><th>环节</th><th>模型/引擎</th><th>费用</th><th>完成时间</th></tr></thead>
              <tbody><tr v-for="(it, i) in t.items" :key="i">
                <td>{{ it.label }}</td><td class="muted">{{ it.model || '-' }}</td>
                <td>{{ fmtMoney(it.cost) }}</td><td class="muted">{{ fmtTime(it.finished_at) }}</td>
              </tr></tbody>
            </table>
          </div>
        </template>
      </div>
    </template>
  </app-layout>`,
};
