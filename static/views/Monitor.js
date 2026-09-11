// 实时监控：SSE 事件流 + 进行中任务的逐节点动态 + 全量工作日志流
// 设计：所有工作细节（节点/工具/流式输出/入队完成失败…）实时追加到任务卡片内的
// 工作控制台逐行显示，Agent 大节点内部再展开 7 子阶段芯片与流式输出预览。
// Debug 开关：输入 admin 密码解锁，可看到每个任务会话的完整细节（含 traceback），
// 并支持下载 Markdown 格式 debug 日志（文件名：日期+时间+debug.md）。
// 子阶段清单与 Tasks.js 共享（那边用顶层 const 声明 AGENT_STEPS；
// 这里只读 window.AGENT_STEPS，不再顶层 const，避免两文件同页重复声明）
const AGENT_STEPS_REF = window.AGENT_STEPS || (window.AGENT_STEPS =
  ['检索证据', '风格判定', '正文创作', '分页文案', '搜参考图', '生成配图', 'OCR自检']);

const MonitorView = {
  data() {
    return {
      counts: {}, limiter: {}, tasks: [], events: [],
      connected: false, es: null, nodes: [],
      debugOn: false, showPw: false, pw: '', pwError: '', pwLoading: false,
      cancelling: '',      // 正在中断的 task_id（按钮防抖）
      taskLogs: {},        // task_id -> [{t,k,m,node}] 工作日志（逐行追加，上限 200）
      openLog: {},         // task_id -> bool 工作日志展开（默认进行中展开）
      nodeView: {},        // task_id -> 节点过程直播窗选中的节点名（空=跟随当前节点）
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
    agentStepIdx(t) {
      // 由 stage_hint 推断 Agent 内部子阶段位置（-1 = 非 Agent 生产中）
      if (t.status !== 'processing' || t.current_node !== 'agent_production') return -1;
      const s = t.stage_hint || '';
      if (s.includes('OCR')) return 6;
      if (s.includes('配图')) return 5;
      if (s.includes('参考图')) return 4;
      if (s.includes('检索')) return 0;
      if (s) return 1;
      return 1;
    },
    // ── 工作日志：所有细节逐行追加 ──
    pushLog(tid, kind, msg, node) {
      const arr = this.taskLogs[tid] || (this.taskLogs[tid] = []);
      arr.push({ t: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
                 k: kind, m: msg, node: node || '' });
      if (arr.length > 200) arr.splice(0, arr.length - 200);
      if (this.openLog[tid] === undefined) this.openLog[tid] = true;
      this.$nextTick(() => {
        const el = document.getElementById('mlog-' + tid);
        if (el) el.scrollTop = el.scrollHeight;
      });
    },
    seedLogs() {
      // 快照加载：把内存 debug（最近节点事件）铺进工作日志，让历史轮也能回看
      for (const t of this.shownTasks) {
        if (!this.taskLogs[t.id] && t.debug && t.debug.length) {
          const arr = t.debug.slice(-8).map(d => ({
            t: d.ts, k: d.phase === 'error' ? 'err' : 'dim', node: d.node || '',
            m: `[${this.nodeLabel(d.node)}] ${d.phase === 'error' ? '✗ ' : ''}${d.msg || ''}`,
          }));
          this.taskLogs[t.id] = arr;
        }
      }
    },
    logOf(t) { return this.taskLogs[t.id] || []; },
    // ── 节点过程直播窗：每节点最新一帧流状态（快照 node_streams + 事件增量）──
    nsMap(t) { return t.node_streams || {}; },
    nsNodes(t) {
      // 有过程数据的节点（按步骤条顺序排），供芯片切换
      const m = this.nsMap(t);
      return this.nodes.map(n => n.name).filter(name =>
        m[name] && (m[name].msg || m[name].preview || m[name].chars));
    },
    selNode(t) {
      const pick = this.nodeView[t.id];
      if (pick && this.nsMap(t)[pick]) return pick;
      return t.current_node || (this.nsNodes(t)[0] || '');
    },
    pickNode(t, name) {
      this.nodeView = { ...this.nodeView,
                        [t.id]: (this.nodeView[t.id] === name ? '' : name) };
    },
    nsFrame(t) { return this.nsMap(t)[this.selNode(t)] || null; },
    nsLogs(t) {
      const sel = this.selNode(t);
      return (this.taskLogs[t.id] || []).filter(l => l.node === sel).slice(-12);
    },
    _nsFrameData(node, d) {
      return { node, chars: d.chars || 0, preview: (d.preview || '').slice(-300),
               msg: d.msg || d.message || '',
               ts: new Date().toLocaleTimeString('zh-CN', { hour12: false }) };
    },
    applyNodeStream(tid, node, d) {
      // node_progress / agent_progress → 任务的 node_streams 映射；
      // 按字段合并：msg 帧不冲掉流式 chars/preview（与后端快照口径一致）
      if (!tid || !node) return;
      const t = this.tasks.find(x => x.id === tid);
      if (!t) return;
      const prev = (t.node_streams || {})[node] || {};
      const cur = this._nsFrameData(node, d);
      const frame = { node, ts: cur.ts,
                      chars: cur.chars || prev.chars || 0,
                      preview: cur.preview || prev.preview || '',
                      msg: cur.msg || prev.msg || '' };
      t.node_streams = { ...(t.node_streams || {}), [node]: frame };
    },
    eventText(e) {
      if (e.type === '__seed') return e.text;   // 快照播种帧（全局工作直播）
      const label = e.node ? this.nodeLabel(e.node) : '';
      const who = e.who || (e.task_id ? this._taskName(e.task_id) : '');
      // 流式帧带实际内容尾部（全局工作直播：同步实际工作内容，2026-09-08）
      const tail = (e.preview || '').replace(/\s+/g, ' ').slice(-60);
      const map = {
        task_enqueued: '入队', task_started: '开始生产', task_finished: '生产完成',
        task_failed: '失败', task_cancelled: '人工中断', node_started: '节点开始',
        node_finished: '节点完成', node_failed: '节点失败', rate_limit: '触发限流',
        concurrency: '并发调整', maintenance: '检修切换',
        node_progress: e.chars
            ? `${label} · 流式 ${e.chars} 字${tail ? '：…' + tail : ''}`
            : `${label} · ${e.msg || '进行中'}`,
        agent_progress: e.chars
            ? `创作Agent · 流式 ${e.chars} 字 ≈${e.tokens_est || 0}token${tail ? '：…' + tail : ''}`
            : '创作Agent · 启动',
        agent_tool: this._toolLog(e),
        export_progress: `打包 ${e.phase || ''}`,
      };
      const detail = map[e.type] || e.type;
      return `${who}${detail}${label && !e.type.startsWith('node_progress') && e.type !== 'agent_progress' && e.type !== 'agent_tool' ? ' · ' + label : ''}`;
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
    // task_*/node_* 事件后的快照重拉节流：2s 内多次事件合并为一次请求（P0-3），
    // 高频 agent_progress/node_progress 本就走增量更新，不触发重拉
    scheduleSnapshot() {
      if (this._snapTimer) return;
      this._snapTimer = setTimeout(() => { this._snapTimer = null; this.loadSnapshot(); }, 2000);
    },
    async loadSnapshot() {
      try {
        const d = await api.get('/api/stream/state');
        this.counts = d.counts; this.limiter = d.limiter; this.tasks = d.tasks || [];
        this.seedLogs();
        this.seedLiveFeed();   // 全局工作直播：快照播种当前各节点最新帧（开页即见工作内容）
      } catch (e) { /* 静默，等 SSE */ }
    },
    seedLiveFeed() {
      // 每个进行中任务的当前节点最新帧 → 全局直播播种一行（幂等：相同内容不重复顶入）
      for (const t of this.shownTasks) {
        const m = t.node_streams || {};
        const name = t.current_node && m[t.current_node] ? t.current_node : null;
        if (!name) continue;
        const f = m[name];
        const text = `${this._taskName(t.id)}${this.nodeLabel(name)} · `
          + (f.chars ? `流式 ${f.chars} 字：…${(f.preview || '').replace(/\s+/g, ' ').slice(-60)}`
                     : (f.msg || '进行中'));
        if (!this.events.find(e => e.type === '__seed' && e.text === text)) {
          this.events.unshift({ ts: f.ts || '', type: '__seed', text });
        }
      }
      if (this.events.length > 150) this.events.length = 150;
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
    _taskName(tid) {
      const t = this.tasks.find(x => x.id === tid);
      return t ? `「${t.query.slice(0, 10)}${t.query.length > 10 ? '…' : ''}」` : tid.slice(0, 8) + '…';
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
          const tid = d.task_id;
          if (d.type === 'agent_progress') {
            this.applyAgentEvent(d);
            this.applyNodeStream(tid, 'agent_production', d);
            if (tid && d.chars) {
              const t = this.tasks.find(x => x.id === tid);
              const last = (this.taskLogs[tid] || []).slice(-1)[0];
              const line = { t: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
                             k: 'stream',
                             m: `流式输出 ${d.chars} 字 ≈ ${d.tokens_est || 0} token` };
              if (last && last.k === 'stream') Object.assign(last, line);
              else this.pushLog(tid, 'stream', line.m, 'agent_production');
              if (t) t.stream = { chars: d.chars, tokens: d.tokens_est || 0, tail: d.preview || '' };
            } else if (tid && d.message) {
              this.pushLog(tid, 'info', d.message, 'agent_production');
            }
          } else if (d.type === 'agent_tool') {
            this.applyAgentEvent(d);
            this.pushLog(tid, 'tool', this._toolLog(d), 'agent_production');
          } else if (d.type === 'node_progress') {
            // 直连路径节点子步骤/流式增量：功能名 + 正在做什么（含预览尾部）
            const lbl = this.nodeLabel(d.node);
            this.applyNodeStream(tid, d.node, d);
            if (d.chars) {
              const t2 = this.tasks.find(x => x.id === tid);
              if (t2) t2.stream = { chars: d.chars, tokens: Math.round(d.chars / 1.7),
                                    tail: (d.preview || '').slice(-120) };
              const last = (this.taskLogs[tid] || []).slice(-1)[0];
              const line = `${lbl} · 流式生成 ${d.chars} 字`;
              if (last && last.k === 'stream' && last.node === d.node)
                Object.assign(last, { m: line });
              else this.pushLog(tid, 'stream', line, d.node);
            } else {
              this.pushLog(tid, 'info', `${lbl} · ${d.msg || '进行中'}`, d.node);
            }
            const t3 = this.tasks.find(x => x.id === tid);
            if (t3 && d.msg) t3.stage_hint = d.msg;
          } else if (d.type.startsWith('task_') || d.type.startsWith('node_')) {
            if (tid) this.pushLog(tid, d.type.includes('failed') ? 'err' : 'info',
                                  this._nodeLog(d), d.node);
            // 节点（重）开始：前端同步重置该节点过程帧（与后端快照口径一致）
            if (d.type === 'node_started' && d.node) {
              const t4 = this.tasks.find(x => x.id === tid);
              if (t4) t4.node_streams = { ...(t4.node_streams || {}),
                [d.node]: { node: d.node, chars: 0, preview: '', msg: '',
                            ts: new Date().toLocaleTimeString('zh-CN', { hour12: false }) } };
            }
            this.scheduleSnapshot();
          }
          // 全局工作直播：所有事件都进（含流式帧内容尾部，见 eventText）
          this.events.unshift({ ts: new Date().toLocaleTimeString('zh-CN', { hour12: false }),
                                ...d, who: tid ? this._taskName(tid) : '' });
          if (this.events.length > 150) this.events.pop();
        } catch (e) { console.warn('monitor event handle failed', e, ev.data && ev.data.slice(0, 200)); }
      };
    },
    _toolLog(d) {
      const tool = d.tool || '';
      if (tool === 'web_search') return `工具：检索证据（${d.count || ''} 条结果）`;
      if (tool === 'image_search') return '工具：搜索实景参考图';
      if (tool === 'image_gen_progress') return `工具：生成配图 P${d.page}/${d.total || '?'}`;
      if (tool === 'image_gen') return `工具：配图生成完成（共 ${d.pages || d.total || ''} 张）`;
      if (tool === 'ocr') return '工具：OCR 图文自检';
      return `工具：${tool}`;
    },
    _nodeLog(d) {
      const map = { task_enqueued: '任务入队', task_started: '开始生产',
                    task_finished: '✓ 生产完成', task_failed: '✗ 生产失败',
                    task_cancelled: '人工中断', node_started: `节点开始：${this.nodeLabel(d.node)}`,
                    node_finished: `节点完成：${this.nodeLabel(d.node)}`,
                    node_failed: `节点失败：${this.nodeLabel(d.node)}` };
      let s = map[d.type] || d.type;
      if (d.elapsed != null) s += `（${d.elapsed}s）`;
      if (d.msg) s += ` · ${d.msg}`;
      return s;
    },
  },
  async mounted() {
    this.loadSnapshot();
    this.connect();
    try { this.nodes = (await api.get('/api/meta/nodes')).nodes; } catch (e) { /* 降级 */ }
  },
  beforeUnmount() { if (this.es) this.es.close(); if (this._snapTimer) clearTimeout(this._snapTimer); },
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

        <!-- Agent 大节点内部：7 子阶段芯片 -->
        <div v-if="agentStepIdx(t) >= 0" class="agent-substeps">
          <span v-for="(s, i) in AGENT_STEPS_REF" :key="s" class="stage-chip sm"
                :class="{done: i < agentStepIdx(t), doing: i === agentStepIdx(t)}">
            {{ i < agentStepIdx(t) ? '✓ ' : '' }}{{ s }}
          </span>
        </div>

        <!-- 节点过程直播窗：每个工作节点的过程输出（msg + 流式预览尾 300 字 + 该节点日志行） -->
        <div v-if="nsFrame(t) && (t.status === 'processing' || debugOn)" class="ns-panel">
          <div class="ns-chips">
            <span class="ns-chip" :class="{on: !nodeView[t.id]}"
                  @click="nodeView = { ...nodeView, [t.id]: '' }">跟随当前</span>
            <span v-for="n in nsNodes(t)" :key="'ns' + n" class="ns-chip"
                  :class="{on: selNode(t) === n && nodeView[t.id],
                           cur: t.current_node === n && t.status === 'processing'}"
                  @click="pickNode(t, n)">{{ nodeLabel(n) }}</span>
          </div>
          <div class="ns-body">
            <div class="ns-head">
              <span v-if="t.current_node === selNode(t) && t.status === 'processing'"
                    class="live-dot"></span>
              节点过程 · {{ nodeLabel(selNode(t)) }}
              <span v-if="nsFrame(t).chars" class="muted">流式 {{ nsFrame(t).chars }} 字</span>
              <span class="muted" style="margin-left:auto">{{ nsFrame(t).ts }}</span>
            </div>
            <div v-if="nsFrame(t).msg" class="ns-msg">{{ nsFrame(t).msg }}</div>
            <pre v-if="nsFrame(t).preview" class="ns-preview">{{ nsFrame(t).preview }}</pre>
            <div v-if="nsLogs(t).length" class="ns-logs">
              <div v-for="(l, i) in nsLogs(t)" :key="'nl' + i" class="ec-line" :class="'ec-' + l.k">
                <span class="ec-ts">{{ l.t }}</span> {{ l.m }}
              </div>
            </div>
          </div>
        </div>

        <!-- 流式输出实时预览（全量透传，逐字符） -->
        <div v-if="t.stream && t.status === 'processing'" class="stream-box">
          <div class="stream-head">
            <span class="live-dot"></span> 流式输出 · {{ t.stream.chars }} 字 · 约 {{ t.stream.tokens }} token
            <span class="ec-cursor">▊</span>
          </div>
          <pre class="stream-text">{{ t.stream.tail || '等待模型输出…' }}</pre>
        </div>

        <!-- 工作日志：所有细节逐行（进行中默认展开） -->
        <div class="mlog-head" @click="openLog[t.id] = !openLog[t.id]">
          <span class="mlog-toggle">{{ openLog[t.id] ? '▾' : '▸' }}</span>
          工作日志（{{ logOf(t).length }} 条）
          <span v-if="t.status === 'processing'" class="live-dot" style="margin-left:6px"></span>
        </div>
        <div v-if="openLog[t.id]" :id="'mlog-' + t.id" class="mlog-console">
          <div v-for="(l, i) in logOf(t)" :key="i" class="ec-line" :class="'ec-' + l.k">
            <span class="ec-ts">{{ l.t }}</span> {{ l.m }}
          </div>
          <div v-if="t.status === 'processing'" class="ec-line ec-dim"><span class="ec-ts">--:--:--</span> <span class="ec-cursor">▊</span></div>
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
      </div>
    </div>

    <div class="card">
      <h2>全局工作直播 <span class="muted" style="font-weight:normal;font-size:13px">跨任务滚动：各节点实际工作内容（流式帧/工具调用/节点摘要）</span></h2>
      <div v-if="!events.length" class="empty" style="padding:20px 0">等待工作数据…</div>
      <div v-for="(e, i) in events" :key="i" class="event-row" :class="{err: e.type && (e.type.includes('failed') || e.type === 'rate_limit')}">
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
  created() { this.AGENT_STEPS_REF = AGENT_STEPS_REF; },
};
