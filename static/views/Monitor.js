// 实时监控：SSE 事件流 + 进行中任务的逐节点动态
// Debug 开关：输入 admin 密码解锁，可看到每个任务会话的完整细节（含 traceback），
// 并支持下载 Markdown 格式 debug 日志（文件名：日期+时间+debug.md）。
const MonitorView = {
  data() {
    return {
      counts: {}, limiter: {}, tasks: [], events: [],
      connected: false, es: null, nodes: [],
      debugOn: false, showPw: false, pw: '', pwError: '', pwLoading: false,
      cancelling: '',   // 正在中断的 task_id（按钮防抖）
    };
  },
  computed: {
    nodeLabels() {
      const m = {}; this.nodes.forEach(n => { m[n.name] = n.label; }); return m;
    },
    activeTasks() {
      return this.tasks.filter(t => t.status === 'processing' || t.status === 'queued')
        .sort((a, b) => (a.status === 'processing' ? -1 : 1));
    },
    // debug 模式：展示全部会话（含完成/失败/中断），生产中排前
    debugTasks() {
      const rank = { processing: 0, queued: 1, failed: 2, cancelled: 3, done: 4 };
      return [...this.tasks].sort((a, b) => (rank[a.status] ?? 9) - (rank[b.status] ?? 9));
    },
    shownTasks() { return this.debugOn ? this.debugTasks : this.activeTasks; },
  },
  methods: {
    nodeLabel(name) { return this.nodeLabels[name] || name || '-'; },
    nodeState(t, name) {
      if ((t.nodes || []).includes(name)) return 'done';
      if (t.current_node === name) return t.status === 'failed' ? 'fail' : 'doing';
      return 'todo';
    },
    statusText(s) {
      return { queued: '排队中', processing: '生产中', done: '已完成', failed: '失败',
               cancelled: '已中断' }[s] || s;
    },
    statusCls(s) {
      return { queued: 'tag-gray', processing: 'tag-blue', done: 'tag-green',
               failed: 'tag-red', cancelled: 'tag-yellow' }[s] || 'tag-gray';
    },
    queryOf(id) {
      const t = this.tasks.find(x => x.id === id);
      return t ? t.query : '';
    },
    // 业务阶段文案：节点 + Agent 子阶段（工具调用/流式输出）
    stageText(t) {
      if (t.status === 'queued') return '等待调度';
      if (t.status !== 'processing') return '';
      const node = t.current_node ? this.nodeLabel(t.current_node) : '';
      if (t.current_node === 'agent_production') {
        if (t.stage_hint) return `${node} · ${t.stage_hint}`;
        if (t.stream) return `${node} · 流式创作中（约 ${t.stream.tokens || 0} token）`;
        return node + ' · 启动中…';
      }
      return node || '启动中…';
    },
    eventText(e) {
      const label = e.node ? this.nodeLabel(e.node) : '';
      const q = e.task_id ? this.queryOf(e.task_id) : '';
      const who = q ? `「${q.slice(0, 12)}${q.length > 12 ? '…' : ''}」` : '';
      const map = {
        task_enqueued: '入队', task_started: '开始生产', task_finished: '生产完成',
        task_failed: '失败', task_cancelled: '人工中断', node_started: '节点开始',
        node_finished: '节点完成', node_failed: '节点失败', rate_limit: '触发限流',
        concurrency: '并发调整', maintenance: '检修切换',
        agent_progress: e.tokens_est ? `流式输出 +${e.chars || 0}字（累计约 ${e.tokens_est} token）`
                                     : 'Agent 启动',
        agent_tool: `工具 · ${e.tool || ''}` + (e.page ? ` P${e.page}/${e.total || '?'}` : ''),
      };
      const detail = map[e.type] || e.type;
      return `${who}${detail}${label ? ' · ' + label : ''}${e.msg ? ' · ' + e.msg : ''}`;
    },
    async cancelTask(t) {
      if (this.cancelling) return;
      const stage = this.stageText(t);
      if (!confirm(`确定中断该任务？\n\n「${t.query}」\n当前阶段：${stage || t.status}\n\n已产生的产物会保留，中断后可在任务中心重试（已完成节点跳过）。`)) return;
      this.cancelling = t.id;
      try {
        await api.post(`/api/tasks/${t.id}/cancel?actor=` + encodeURIComponent((getUser() || {}).name || 'anonymous'));
        this.loadSnapshot();
      } catch (e) { alert('中断失败：' + e.message); }
      finally { this.cancelling = ''; }
    },
    // ---------- debug 开关 ----------
    askDebug() {
      if (this.debugOn) { this.debugOn = false; return; }
      this.showPw = true; this.pw = ''; this.pwError = '';
    },
    async submitPw() {
      this.pwError = ''; this.pwLoading = true;
      try {
        const r = await api.post('/api/auth/verify_admin', { password: this.pw });
        if (!r.ok) throw new Error(r.error || '密码错误');
        this.debugOn = true; this.showPw = false; this.pw = '';
        this.loadSnapshot();
      } catch (e) { this.pwError = e.message; }
      finally { this.pwLoading = false; }
    },
    // ---------- debug 日志下载 ----------
    async downloadDebug() {
      let logs = [];
      try { logs = (await api.get('/api/admin/logs')).log || []; } catch (e) { /* 忽略 */ }
      const now = new Date();
      const p = n => String(n).padStart(2, '0');
      const stamp = `${now.getFullYear()}${p(now.getMonth() + 1)}${p(now.getDate())}`
        + `_${p(now.getHours())}${p(now.getMinutes())}${p(now.getSeconds())}`;
      const lines = [`# Debug 日志 ${now.toLocaleString('zh-CN', { hour12: false })}`, ''];
      lines.push(`队列：排队 ${this.counts.queued || 0} / 生产中 ${this.counts.processing || 0}`
        + ` / 完成 ${this.counts.done || 0} / 失败 ${this.counts.failed || 0}`
        + ` · 当前并发 ${this.limiter.capacity || '-'}（上限 ${this.limiter.max_c || '-'}）`, '');
      lines.push('## 任务会话细节', '');
      for (const t of this.debugTasks) {
        lines.push(`### ${t.query}（${(t.id || '').slice(0, 8)}）`);
        lines.push(`- 状态：${this.statusText(t.status)}`
          + (t.current_node ? ` · 当前节点：${this.nodeLabel(t.current_node)}` : '')
          + (t.model ? ` · 模型：${t.model}` : ''));
        if (t.nodes && t.nodes.length)
          lines.push(`- 已完成节点：${t.nodes.map(n => this.nodeLabel(n)).join(' → ')}`);
        if (t.error) lines.push(`- 错误：${t.error}`);
        if (t.preview) lines.push(`- 预览：${t.preview}`);
        if (t.conflicts && t.conflicts.length)
          lines.push(`- 证据争议：${t.conflicts.join('；')}`);
        if (t.debug && t.debug.length) {
          lines.push('', '| 时间 | 节点 | 结果 | 耗时 | 信息 |', '|---|---|---|---|---|');
          for (const d of t.debug) {
            const msg = String(d.msg || '').replace(/\|/g, '\\|').replace(/\n/g, ' ');
            lines.push(`| ${d.ts} | ${this.nodeLabel(d.node)} | ${d.phase === 'error' ? '✗ 失败' : '✓ 完成'} | ${d.elapsed ?? '-'}s | ${msg} |`);
          }
          const traces = t.debug.filter(d => d.trace);
          for (const d of traces) {
            lines.push('', `**${this.nodeLabel(d.node)} traceback：**`, '', '```', d.trace, '```');
          }
        }
        lines.push('');
      }
      lines.push('## 全局运行日志', '', '```', ...logs, '```');
      const blob = new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' });
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = `${stamp}_debug.md`;
      a.click();
      URL.revokeObjectURL(a.href);
    },
    async loadSnapshot() {
      try {
        const d = await api.get('/api/stream/state');
        this.counts = d.counts; this.limiter = d.limiter; this.tasks = d.tasks || [];
      } catch (e) { /* 静默，等 SSE */ }
    },
    applyAgentEvent(d) {
      // Agent 流式/工具事件：直接更新本地任务对象（高频，不重拉快照）
      const t = this.tasks.find(x => x.id === d.task_id);
      if (!t) { this.loadSnapshot(); return; }
      if (d.type === 'agent_progress' && d.chars) {
        t.stream = { chars: d.chars, tokens: d.tokens_est || 0, tail: d.preview || '' };
      } else if (d.type === 'agent_tool') {
        const m = { web_search: '检索证据', image_search: '搜索实景参考图',
                    image_gen_progress: `生成配图 P${d.page}/${d.total || '?'}`,
                    image_gen: '配图生成完成', ocr: 'OCR 图文自检' };
        t.stage_hint = m[d.tool] || `调用工具 ${d.tool || ''}`;
      }
    },
    connect() {
      this.es = new EventSource('/api/stream/events');
      this.es.onopen = () => { this.connected = true; };
      this.es.onerror = () => { this.connected = false; };
      this.es.onmessage = (ev) => {
        try {
          const d = JSON.parse(ev.data);
          if (!d.type || d.type === 'ping' || d.type === 'snapshot') {
            if (d.type === 'snapshot') this.loadSnapshot();
            return;
          }
          // 流式输出事件按 1500 字里程碑进事件流（避免刷屏），流式框实时更新
          if (d.type === 'agent_progress') {
            this.applyAgentEvent(d);
            if ((d.chars || 0) % 1500 >= 300) return;
          } else if (d.type === 'agent_tool') {
            this.applyAgentEvent(d);
          } else if (d.type.startsWith('task_') || d.type.startsWith('node_')) {
            this.loadSnapshot();
          }
          this.events.unshift({ ts: new Date().toLocaleTimeString('zh-CN', { hour12: false }), ...d });
          if (this.events.length > 100) this.events.pop();
        } catch (e) { /* 非 JSON 帧忽略 */ }
      };
    },
  },
  async mounted() {
    this.loadSnapshot();
    this.connect();
    try { this.nodes = (await api.get('/api/meta/nodes')).nodes; } catch (e) { /* 降级 */ }
  },
  beforeUnmount() { if (this.es) this.es.close(); },
  template: `
  <app-layout title="实时监控">
    <div class="grid grid-5">
      <div class="stat"><div class="n">{{ counts.queued || 0 }}</div><div class="l">排队中</div></div>
      <div class="stat"><div class="n" style="color:var(--primary)">{{ counts.processing || 0 }}</div><div class="l">生产中</div></div>
      <div class="stat"><div class="n" style="color:var(--green)">{{ counts.done || 0 }}</div><div class="l">本次完成</div></div>
      <div class="stat"><div class="n" style="color:var(--red)">{{ counts.failed || 0 }}</div><div class="l">本次失败</div></div>
      <div class="stat"><div class="n">{{ limiter.capacity || '-' }}</div><div class="l">当前并发（上限 {{ limiter.max_c || '-' }}）</div></div>
    </div>

    <div class="card" style="margin-top:16px">
      <div style="display:flex;align-items:center;gap:10px">
        <h2 style="margin:0">{{ debugOn ? '全部会话（Debug）' : '进行中的任务' }}
          <span class="live-dot" :class="{off: !connected}"></span>
          <span class="muted" style="font-size:12px">{{ connected ? 'SSE 已连接' : '连接断开，重连中…' }}</span>
        </h2>
        <span style="margin-left:auto;display:flex;gap:8px">
          <button class="btn btn-sm" :class="debugOn ? 'btn-danger' : 'btn-outline'" @click="askDebug">
            {{ debugOn ? '关闭 Debug' : 'Debug' }}
          </button>
          <button class="btn btn-outline btn-sm" v-if="debugOn" @click="downloadDebug">下载 debug 日志</button>
        </span>
      </div>
      <div v-if="!shownTasks.length" class="empty" style="padding:20px 0">
        {{ debugOn ? '暂无任务会话' : '当前没有排队或生产中的任务' }}
      </div>
      <div v-for="t in shownTasks" :key="t.id" class="monitor-task">
        <div class="monitor-task-head">
          <b>{{ t.query }}</b>
          <span class="tag" :class="statusCls(t.status)">{{ statusText(t.status) }}</span>
          <span v-if="stageText(t)" class="tag tag-blue" style="white-space:nowrap">{{ stageText(t) }}</span>
          <span v-if="debugOn && t.model" class="muted">模型：{{ t.model }}</span>
          <button v-if="t.status === 'processing' || t.status === 'queued'"
                  class="btn btn-danger btn-sm" style="margin-left:auto;white-space:nowrap"
                  :disabled="cancelling === t.id"
                  @click="cancelTask(t)">{{ cancelling === t.id ? '中断中…' : '✕ 中断任务' }}</button>
        </div>
        <div class="mini-steps">
          <span v-for="n in nodes" :key="n.name" class="mini-step" :class="nodeState(t, n.name)" :title="n.label"></span>
        </div>
        <div v-if="t.status === 'processing'" class="stage-row">
          <span class="muted" style="font-size:12.5px">阶段：</span>
          <span v-for="n in nodes" :key="'s'+n.name" class="stage-chip" :class="nodeState(t, n.name)">{{ n.label }}</span>
        </div>
        <div v-if="t.stream && t.status === 'processing'" class="stream-box">
          <div class="stream-head">
            <span class="live-dot"></span> 流式输出 · {{ t.stream.chars }} 字 · 约 {{ t.stream.tokens }} token
          </div>
          <pre class="stream-text">{{ t.stream.tail || '等待模型输出…' }}</pre>
        </div>
        <template v-if="debugOn">
          <div v-if="t.error" class="form-error" style="margin-top:8px">错误：{{ t.error }}</div>
          <div v-if="t.preview" class="muted" style="margin-top:6px;font-size:13px;white-space:pre-wrap">{{ t.preview }}</div>
          <div v-if="t.conflicts && t.conflicts.length" class="muted" style="margin-top:4px;font-size:13px">
            证据争议：{{ t.conflicts.join('；') }}
          </div>
          <div v-if="t.debug && t.debug.length" class="monitor-debug">
            <div v-for="(d, i) in t.debug" :key="i" class="event-row" :class="{err: d.phase === 'error'}">
              <span class="muted">{{ d.ts }}</span> [{{ nodeLabel(d.node) }}]
              {{ d.phase === 'error' ? '✗' : '✓' }}
              <span v-if="d.elapsed != null" class="muted">{{ d.elapsed }}s</span> {{ d.msg }}
              <pre v-if="d.trace" class="log-box" style="max-height:200px">{{ d.trace }}</pre>
            </div>
          </div>
          <div v-else class="muted" style="margin-top:6px;font-size:13px">暂无节点细节</div>
        </template>
        <div v-else-if="t.debug && t.debug.length" class="monitor-debug">
          <div v-for="(d, i) in t.debug.slice(-3)" :key="i" class="muted">
            {{ d.ts }} [{{ nodeLabel(d.node) }}] {{ d.phase === 'error' ? '✗' : '✓' }} {{ d.msg }}
          </div>
        </div>
      </div>
    </div>

    <div class="card">
      <h2>实时事件流</h2>
      <div v-if="!events.length" class="empty" style="padding:20px 0">等待事件…</div>
      <div v-for="(e, i) in events" :key="i" class="event-row" :class="{err: e.type.includes('failed') || e.type === 'rate_limit'}">
        <span class="muted">{{ e.ts }}</span>　{{ eventText(e) }}
      </div>
    </div>

    <div v-if="showPw" class="drawer-mask" @click.self="showPw=false">
      <div class="card" style="width:360px;margin:18vh auto 0">
        <h2>开启 Debug</h2>
        <p class="muted">请输入 admin 密码，解锁后可查看每个会话的完整节点细节与错误堆栈。</p>
        <form @submit.prevent="submitPw">
          <p><input v-model="pw" type="password" required placeholder="admin 密码" autofocus></p>
          <p v-if="pwError" class="form-error">{{ pwError }}</p>
          <div style="display:flex;gap:8px">
            <button class="btn btn-primary" :disabled="pwLoading">{{ pwLoading ? '验证中…' : '验证并开启' }}</button>
            <button type="button" class="btn btn-outline" @click="showPw=false">取消</button>
          </div>
        </form>
      </div>
    </div>
  </app-layout>`,
};
