// 工作台（合并入口）：总览 / 任务中心 / 实时监控 三个平行栏目（tab 切换，
// v-show 保持各自的轮询与 SSE 连接不中断；旧路由 /tasks /monitor 重定向到对应 tab）
const DashboardView = {
  data() {
    return { tab: 'overview',
             metrics: null, stats: null, nodes: [], timer: null, error: '', updatedAt: '' };
  },
  computed: {
    // 惰性取组件：Dashboard.js 先于 Tasks/Monitor 加载，渲染时才解析（顺序无关）
    monitorComp() { return typeof MonitorView !== 'undefined' ? MonitorView : null; },
    statusList() {
      const bs = (this.stats && this.stats.by_status) || {};
      return Object.keys(STATUS).map(k => ({ key: k, count: bs[k] || 0, label: STATUS[k].label, cls: STATUS[k].cls }));
    },
    nodeBars() {
      const nc = (this.stats && this.stats.nodes_completed) || {};
      const rows = this.nodes.map(n => ({ label: n.label, count: nc[n.name] || 0 }));
      const max = Math.max(1, ...rows.map(r => r.count));
      return rows.map(r => ({ ...r, pct: Math.round(r.count / max * 100) }));
    },
    metricCards() {
      const m = this.metrics || {};
      const pct = v => (v == null ? '-' : (v <= 1 ? Math.round(v * 100) + '%' : Math.round(v) + '%'));
      const cards = [
        { n: m.throughput_per_hour ?? '-', l: '每小时吞吐（条）' },
        { n: pct(m.first_pass_rate_24h), l: '24h 一次通过率' },
        { n: pct(m.human_touch_rate_24h), l: '24h 人工介入率' },
        { n: m.p95_node_seconds != null ? Math.round(m.p95_node_seconds) + 's' : '-', l: '节点耗时 P95' },
      ];
      // 算力成本仅对管理员展示（明细在系统管理页），普通用户不可见
      const u = getUser();
      if (u && u.role === 'admin') {
        cards.push({ n: m.cost_per_task_24h_cny != null ? '¥' + Number(m.cost_per_task_24h_cny).toFixed(3) : '-', l: '24h 单任务成本' });
      }
      return cards;
    },
    metricGridClass() {
      return this.metricCards.length >= 5 ? 'grid grid-5' : 'grid grid-4';
    },
    queueDepth() {
      const q = (this.metrics && this.metrics.queue_depth) || {};
      return ['A', 'B', 'C'].map(r => ({ role: r, label: roleName(r), count: q[r] || 0 }));
    },
    errorTop() {
      const list = (this.metrics && this.metrics.error_top_5) || [];
      return list.map(e => ({ error: e.error ?? e[0] ?? '', count: e.count ?? e[1] ?? 0 }));
    },
  },
  methods: {
    async load() {
      try {
        const [m, s] = await Promise.all([api.get('/api/dashboard/metrics'), api.get('/api/tasks/stats')]);
        this.metrics = m; this.stats = s; this.error = '';
        this.updatedAt = new Date().toLocaleTimeString('zh-CN', { hour12: false });
      } catch (e) { this.error = e.message; }
    },
  },
  created() {
    // 旧书签直达：#/?tab=tasks / monitor
    const q = new URLSearchParams(location.hash.split('?')[1] || '');
    const t = q.get('tab');
    if (t === 'monitor') this.tab = t;
  },
  async mounted() {
    this.load();
    this.timer = setInterval(this.load, 15000);
    try { this.nodes = (await api.get('/api/meta/nodes')).nodes; } catch (e) { /* 节点标签缺失时降级 */ }
  },
  beforeUnmount() { clearInterval(this.timer); },
  template: `
  <app-layout title="工作台">
    <div class="tabs" style="margin-bottom:14px">
      <button class="tab" :class="{on: tab==='overview'}" @click="tab='overview'">📊 总览</button>
      <button class="tab" :class="{on: tab==='monitor'}" @click="tab='monitor'">📡 实时监控</button>
    </div>
    <div v-show="tab==='monitor'"><component :is="monitorComp" /></div>
    <template v-if="tab==='overview'">
    <p v-if="error" class="form-error">{{ error }}</p>
    <div :class="metricGridClass">
      <div v-for="c in metricCards" :key="c.l" class="stat"><div class="n">{{ c.n }}</div><div class="l">{{ c.l }}</div></div>
    </div>
    <div class="grid grid-2" style="margin-top:16px">
      <div class="card">
        <h2>任务状态</h2>
        <div class="status-row">
          <div v-for="s in statusList" :key="s.key" class="status-cell">
            <span class="tag" :class="s.cls">{{ s.label }}</span>
            <span class="status-count">{{ s.count }}</span>
          </div>
        </div>
        <p class="muted" style="margin-top:10px">任务总数：{{ stats ? stats.total : '-' }}　<span v-if="updatedAt">更新于 {{ updatedAt }}</span></p>
      </div>
      <div class="card">
        <h2>审核队列积压</h2>
        <div class="status-row">
          <div v-for="q in queueDepth" :key="q.role" class="status-cell">
            <span class="tag tag-blue">{{ q.role }} · {{ q.label }}</span>
            <span class="status-count">{{ q.count }}</span>
          </div>
        </div>
        <h2 style="margin-top:18px">失败类型 Top5</h2>
        <div v-if="!errorTop.length" class="empty" style="padding:16px 0">暂无失败记录</div>
        <div v-for="e in errorTop" :key="e.error" class="err-row">
          <span class="err-name">{{ e.error }}</span><span class="tag tag-red">{{ e.count }}</span>
        </div>
      </div>
    </div>
    <div class="card">
      <h2>流水线节点完成数</h2>
      <div v-for="b in nodeBars" :key="b.label" class="bar-row">
        <span class="bar-label">{{ b.label }}</span>
        <div class="bar-track"><div class="bar-fill" :style="{width: b.pct + '%'}"></div></div>
        <span class="bar-count">{{ b.count }}</span>
      </div>
    </div>
    </template>
  </app-layout>`,
};
