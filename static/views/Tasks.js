// Agent 大节点内部子阶段（工作流 7 步，监控用；idx 由 stage_hint 推断）
// 与 Monitor.js 共享 window.AGENT_STEPS，避免两文件同页加载重复声明
const AGENT_STEPS = window.AGENT_STEPS || (window.AGENT_STEPS =
  ['检索证据', '风格判定', '正文创作', '分页文案', '搜参考图', '生成配图', 'OCR自检']);

// 任务中心：列表（筛选/轮询/SSE 实时）+ 详情抽屉（节点进度/明细 + Agent 实时工作台 + 全部产物）
const TasksView = {
  data() {
    return {
      list: [], total: 0, error: '', loading: false,
      approvedCount: 0,      // 审核通过的任务数（>0 才可导出内容包）
      fStatus: '', fMode: '', fRisk: '', auto: true,
      nodes: [], timer: null, es: null, sseTimer: null, agentTimer: null,
      live: {},              // task_id -> 内存实时态（current_node/debug/imgs）
      detail: null, detailError: '', retrying: false,
      exportJob: null, exportTimer: null,   // 任务式导出进度 {id,status,total,done,detail}
      showExport: false,     // 导出弹窗开关（关闭不中断后台打包）
      exportLog: [],         // 流式控制台日志行 {t, m, k}
      exportT0: 0,           // 打包开始时间（算用时/预估）
      exportTick: 0,         // 1s 心跳：驱动用时/速率每秒刷新
      recycleItems: [],      // 导出回收站（仅 admin 可见）
      showRecycle: false,    // 回收站展开开关
      refKeep: {},           // 参考图勾选 {assetId: true}
      confirmingRefs: false, // 确认中防抖
      zoom: null,            // 图片放大浏览 {src, title, text}
      search: '',            // 关键词搜索（Query 模糊匹配）
      rowMenu: null,         // 展开操作菜单的行任务 id
      editForm: null,        // 编辑弹窗 {id, query, mode, priority, status}
      editError: '', saving: false,
      deleting: '',          // 删除中的任务 id（按钮防抖）
    };
  },
  computed: {
    exportPct() {
      const j = this.exportJob;
      if (!j || !j.total) return 5;
      if (j.status === 'done') return 100;
      return Math.max(5, Math.round(j.done / j.total * 100));
    },
    nodeLabels() {
      const m = {}; this.nodes.forEach(n => { m[n.name] = n.label; }); return m;
    },
    detailTask() { return this.detail && this.detail.task; },
    refKeepCount() { return Object.keys(this.refKeep).filter(k => this.refKeep[k]).length; },
    actorName() { return (getUser() || {}).name || 'anonymous'; },
    liveOfDetail() {
      return this.detailTask ? (this.live[this.detailTask.id] || null) : null;
    },
    canRetry() {
      return this.detailTask && ['failed', 'rejected', 'cancelled'].includes(this.detailTask.status);
    },
    agentStepIdx() {
      // 由实时 stage_hint 推断 Agent 内部走到第几步（-1 = 非 Agent 生产中）
      const lv = this.liveOfDetail;
      if (!lv || lv.status !== 'processing') return -1;
      const s = lv.stage_hint || '';
      if (s.includes('OCR')) return 6;
      if (s.includes('配图')) return 5;
      if (s.includes('参考图')) return 4;
      if (s.includes('检索')) return 0;
      if (s) return 1;                       // 其它工具阶段归入风格/正文段
      return lv.current_node === 'agent_production' ? 1 : -1;
    },
    retryLabel() {
      if (!this.detailTask) return '';
      if (this.detailTask.status === 'cancelled') {
        return '↻ 继续生产该任务（已完成节点跳过）';
      }
      if (this.detailTask.status === 'rejected') {
        const n = ((this.detail && this.detail.reject_marks) || []).length;
        return n ? `↻ 定点重生成 ${n} 项（其余内容保留）` : '↻ 按驳回意见重新生产（全链重跑）';
      }
      return '↻ 重试该任务（跳过已完成节点）';
    },
    // 对比/单品任务：交付配图只展示 AI 生成图，抓取的实景参考图单独成区
    genAssets() {
      return ((this.detail && this.detail.assets) || []).filter(a => a.source_type !== 'official');
    },
    refAssets() {
      return ((this.detail && this.detail.assets) || []).filter(a => a.source_type === 'official');
    },
  },
  methods: {
    fmtTime, roleName,
    statusTag(s) { return STATUS[s] || { label: s, cls: 'tag-gray' }; },
    modeLabel(m) { return MODE[m] ? MODE[m].label : (m || '-'); },
    riskTag(r) { return RISK[r] || null; },
    nodeLabel(name) { return this.nodeLabels[name] || name || '-'; },
    buildQuery() {
      const p = new URLSearchParams();
      if (this.fStatus) p.set('status', this.fStatus);
      if (this.fMode) p.set('mode', this.fMode);
      if (this.fRisk) p.set('risk_level', this.fRisk);
      if (this.search.trim()) p.set('q', this.search.trim());
      p.set('limit', '100');
      return p.toString();
    },
    async load() {
      try {
        const [r, s] = await Promise.all([
          api.get('/api/tasks?' + this.buildQuery()),
          api.get('/api/tasks/stats'),
        ]);
        this.list = r.items; this.total = r.total; this.error = '';
        this.approvedCount = (s.by_status || {}).approved || 0;
      } catch (e) { this.error = e.message; }
    },
    async loadLive() {
      try {
        const r = await api.get('/api/stream/state');
        const m = {};
        (r.tasks || []).forEach(t => { m[t.id] = t; });
        this.live = m;
      } catch (e) { /* 内存快照不可用时静默 */ }
    },
    async open(t) {
      this.detailError = ''; this.detail = null;
      try {
        this.detail = await api.get(`/api/tasks/${t.id}/detail`);
        if (this.detailTask && this.detailTask.status === 'awaiting_refs') this.initRefKeep();
      } catch (e) { this.detailError = e.message; }
    },
    close() { this.detail = null; },
    pageCopyOf(i) {
      const pcs = (this.detail && this.detail.page_copies) || [];
      const p = pcs.find(x => x.page_index === i);
      return p ? p.body : '';
    },
    zoomItems() {
      const gen = this.genAssets.map(a => ({
        src: a.display_url || a.image_url,
        title: `P${a.page_index} · AI 生成`,
        text: this.pageCopyOf(a.page_index),
      }));
      const ref = this.refAssets.map(a => ({
        src: a.display_url || a.image_url,
        title: `参考 ${a.page_index} · 实景抓取`,
        text: '',
      }));
      return gen.concat(ref);
    },
    openZoom(a, isRef) {
      const list = this.zoomItems();
      const src = a.display_url || a.image_url;
      const index = Math.max(0, list.findIndex(x => x.src === src));
      this.zoom = { list, index };
    },
    async retry() {
      if (!this.detailTask) return;
      const marks = (this.detail && this.detail.reject_marks) || [];
      let msg;
      if (this.detailTask.status === 'rejected' && marks.length) {
        msg = `确定定点重生成该任务？\n\n仅重做被标记的 ${marks.length} 项（${marks.map(m => (m.item_type === 'page' ? '文案' : '配图') + 'P' + m.page_index).join('、')}），其余已认可内容保留不重跑，只产生标记项的生成费用。`;
      } else if (this.detailTask.status === 'rejected') {
        msg = '确定重新生产该任务？\n\n上一轮生成的文案和配图将被清除，审核驳回理由会注入提示词重新生成全部内容（会产生完整的生成费用）。';
      } else {
        msg = '确定重试该任务？已完成的节点会自动跳过。';
      }
      if (!confirm(msg)) return;
      this.retrying = true;
      try {
        await api.post(`/api/tasks/${this.detailTask.id}/retry?actor=` + encodeURIComponent((getUser() || {}).name || 'anonymous'));
        await this.load();
        await this.open(this.detailTask);
      } catch (e) { this.detailError = e.message; }
      finally { this.retrying = false; }
    },
    onSse() {
      // 任意任务/节点事件 → 节流刷新列表与打开的详情
      if (this.sseTimer) return;
      this.sseTimer = setTimeout(() => {
        this.sseTimer = null;
        this.load(); this.loadLive();
        if (this.detailTask) this.open(this.detailTask);
      }, 800);
    },
    onAgentProgress() {
      // Agent 生产过程事件（长节点期间唯一信号）：只刷内存实时态，列表行
      // 经 live 覆盖即时反映当前节点/状态，不打 DB
      if (this.agentTimer) return;
      this.agentTimer = setTimeout(() => {
        this.agentTimer = null;
        this.loadLive();
      }, 800);
    },
    // ── 列表行实时覆盖：DB 行数据 + 内存实时态（live）合并 ──
    rowStatus(t) {
      const lv = this.live[t.id];
      // live.status: queued/processing/done/failed；DB 的 draft/review 等为准终态
      if (lv && lv.status === 'processing') return 'processing';
      if (lv && lv.status === 'queued') return 'draft';
      return t.status;
    },
    rowNode(t) {
      const lv = this.live[t.id];
      return (lv && lv.current_node) || t.current_node || '';
    },
    rowLiveMsg(t) {
      // Agent 生产中的最近一条过程消息（debug 尾部），列表行悬浮提示
      const lv = this.live[t.id];
      if (!lv || lv.status !== 'processing' || !lv.debug || !lv.debug.length) return '';
      const d = lv.debug[lv.debug.length - 1];
      return d ? (d.msg || '') : '';
    },
    isAdmin() { const u = getUser(); return u && u.role === 'admin'; },
    refCandidates() {
      // 待确认参考图候选（awaiting_refs 状态展示）
      return ((this.detail && this.detail.assets) || [])
        .filter(a => a.source_type === 'official' && a.selection_status === 'candidate');
    },
    initRefKeep() {
      // 默认全勾选（OCR 命中的排前，人工可减选）
      this.refKeep = {};
      this.refCandidates().forEach(a => { this.refKeep[a.id] = true; });
    },
    async confirmRefs() {
      if (this.confirmingRefs) return;
      const keep = Object.keys(this.refKeep).filter(k => this.refKeep[k]);
      if (!keep.length) { alert('至少保留一张参考图（全部不要请用中断任务）'); return; }
      if (!confirm(`确认保留 ${keep.length} 张参考图并继续生产？
未勾选的候选将被剔除。`)) return;
      this.confirmingRefs = true;
      try {
        await api.post(`/api/tasks/${this.detailTask.id}/refs/confirm`,
          { keep_ids: keep, actor: this.actorName });
        this.showExport = false;
        await this.open({ id: this.detailTask.id });
        this.load(); this.loadLive();
      } catch (e) { alert('确认失败：' + e.message); }
      finally { this.confirmingRefs = false; }
    },
    async deleteExportPart(jobId, part) {
      if (!confirm(`确定删除第 ${part} 包？\n删除后进入回收站（仅管理员可见，72 小时后自动清理）。`)) return;
      try {
        await api.delete(`/api/export/${jobId}/part/${part}?actor=` + encodeURIComponent(this.actorName));
        const j = this.exportJob;
        if (j) {
          j.parts_done = (j.parts_done || []).filter(p => p.part !== part);
          j.parts = (j.parts || []).filter(p => p.part !== part);
          this._exportLog('part', `第 ${part} 包已删除（进入回收站，72h 后自动清理）`);
          if (!(j.parts_done || []).length && !(j.parts || []).length && j.status === 'done') {
            this.exportJob = null; this.showExport = false;
          }
        }
        if (this.isAdmin) this.loadRecycle();
      } catch (e) { alert('删除失败：' + e.message); }
    },
    async clearExportParts() {
      const j = this.exportJob;
      if (!j) return;
      const n = (j.parts_done || []).length || (j.parts || []).length;
      if (!n || !confirm(`确定一键清理全部 ${n} 个分包？\n清理后进入回收站（仅管理员可见，72 小时后自动清理）。`)) return;
      try {
        const r = await api.post(`/api/export/${j.id}/clear?actor=` + encodeURIComponent(this.actorName));
        j.parts_done = []; j.parts = [];
        this._exportLog('part', `已一键清理 ${r.recycled} 个分包（进入回收站，72h 后自动清理）`);
        if (j.status === 'done') { this.exportJob = null; this.showExport = false; }
        if (this.isAdmin) this.loadRecycle();
      } catch (e) { alert('清理失败：' + e.message); }
    },
    async loadRecycle() {
      try {
        const r = await api.get('/api/export/recycle');
        this.recycleItems = r.items || [];
      } catch (e) { /* 静默 */ }
    },
    async purgeRecycleItem(f) {
      if (!confirm(`彻底删除「${f}」？\n彻底删除后不可恢复（回收站 72h 自动清理前的手动清理）。`)) return;
      try {
        await api.delete('/api/export/recycle/' + encodeURIComponent(f));
        this.loadRecycle();
      } catch (e) { alert('删除失败：' + e.message); }
    },
    async startExport() {
      this.error = '';
      try {
        const r = await api.post('/api/export/approved/start?actor=' + encodeURIComponent(this.actorName));
        this.exportJob = { id: r.job_id, status: 'running', total: r.total,
                           done: 0, detail: '启动打包…', parts: [],
                           parts_done: [], parts_expected: r.parts_expected || 1,
                           images_done: 0 };
        this.exportLog = [];
        this.exportT0 = Date.now();
        this._exportLog('info', `开始打包：共 ${r.total} 条已通过内容，预计 ${r.parts_expected || 1} 包`);
        this.showExport = true;
        this.exportTimer = setInterval(this.pollExport, 1000);
      } catch (e) { this.error = e.message; }
    },
    _exportLog(kind, msg) {
      // 流式控制台：追加一行并自动滚到底（上限 300 行防长任务爆内存）
      this.exportLog.push({ t: new Date().toTimeString().slice(0, 8), m: msg, k: kind });
      if (this.exportLog.length > 300) this.exportLog.splice(0, this.exportLog.length - 300);
      this.$nextTick(() => {
        const el = this.$refs.exportConsole;
        if (el) el.scrollTop = el.scrollHeight;
      });
    },
    onExportStream(d) {
      // SSE 流式进度（export_progress 事件）：即时刷新，与 1s 轮询互为补充
      if (!this.exportJob || d.job_id !== this.exportJob.id) return;
      const j = this.exportJob;
      if (d.phase === 'start') {
        j.total = d.total; j.status = 'running';
      } else if (d.phase === 'task') {
        this._exportLog('info', `打包 ${d.done + 1}/${d.total}：${d.query}（第 ${d.part} 包）`);
      } else if (d.phase === 'image') {
        j.images_done = d.images_done;
        if (!d.ok) this._exportLog('err', `　└ 图片 ${d.img}/${d.img_total} 下载失败，已写入占位说明`);
        else if (d.img === d.img_total) this._exportLog('dim', `　└ ${d.img_total} 张配图就绪`);
      } else if (d.phase === 'task_done') {
        j.done = d.done; j.total = d.total;
      } else if (d.phase === 'part') {
        j.parts_done = [...(j.parts_done || []),
                        { part: d.part, tasks: d.tasks, size: d.size }];
        this._exportLog('part', `✓ 第 ${d.part} 包就绪（${d.tasks} 条 · ${this.fmtSize(d.size)}），已可下载`);
      } else if (d.phase === 'done') {
        j.status = 'done'; j.parts = d.parts; j.done = d.total;
        j.detail = d.detail; j.images_done = d.images_done;
        if (this.exportTimer) { clearInterval(this.exportTimer); this.exportTimer = null; }
        const secs = Math.round((Date.now() - this.exportT0) / 1000);
        this._exportLog('done', `打包完成：${d.total} 条 / ${d.parts.length} 包 · 用时 ${secs}s · 共 ${d.images_done} 张配图，逐包下载即可`);
      } else if (d.phase === 'error') {
        j.status = 'error';
        this._exportLog('err', `打包失败：${d.error}`);
        if (this.exportTimer) { clearInterval(this.exportTimer); this.exportTimer = null; }
      }
    },
    exportElapsedStr() {
      if (!this.exportT0 || !this.exportJob || this.exportJob.status !== 'running') return '';
      return Math.round((Date.now() - this.exportT0) / 1000) + 's';
    },
    exportSpeed() {
      // 实时速率 + 剩余预估（基于已打包条数）
      const j = this.exportJob;
      if (!this.exportT0 || !j || j.status !== 'running' || !j.done) return '';
      const secs = Math.max(1, (Date.now() - this.exportT0) / 1000);
      const eta = Math.round((j.total - j.done) / (j.done / secs));
      return `${(j.done / secs).toFixed(1)} 条/s · 预计剩余 ${eta}s`;
    },
    async pollExport() {
      if (!this.exportJob) return;
      this.exportTick++;   // 心跳：值变化触发重渲染，用时/速率每秒跳动
      try {
        const s = await api.get('/api/export/' + this.exportJob.id);
        const grew = (s.parts_done || []).length > (this.exportJob.parts_done || []).length;
        if (grew) this.exportJob.parts_done = s.parts_done;   // SSE 丢帧时兜底补分包状态
        Object.assign(this.exportJob, { status: s.status, total: s.total,
                                        done: s.done, detail: s.detail,
                                        images_done: s.images_done });
        if (s.status === 'done') {
          // 打包完成：展示分包下载按钮，用户逐包下载（不自动触发）
          this.exportJob.parts = s.parts || [];
          if (!this.exportLog.some(l => l.k === 'done')) this._exportLog('done', s.detail);
          clearInterval(this.exportTimer); this.exportTimer = null;
        } else if (s.status === 'error') {
          clearInterval(this.exportTimer); this.exportTimer = null;
          this._exportLog('err', '导出失败：' + (s.error || '未知错误'));
        }
      } catch (e) { /* 单次轮询失败静默，下轮重试 */ }
    },
    fmtSize(b) {
      if (!b) return '0B';
      return b >= 1048576 ? (b / 1048576).toFixed(1) + 'MB' : Math.round(b / 1024) + 'KB';
    },
    // ── 编辑 / 删除 ──
    canEdit(t) { return ['draft', 'failed', 'rejected', 'cancelled'].includes(t.status); },
    canDelete(t) { return t.status !== 'processing'; },
    toggleMenu(t) { this.rowMenu = this.rowMenu === t.id ? null : t.id; },
    openEdit(t) {
      this.editError = '';
      this.editForm = { id: t.id, query: t.query, mode: t.mode || 'general',
                        priority: t.priority || 'normal', status: t.status };
    },
    async saveEdit() {
      if (!this.editForm) return;
      const q = this.editForm.query.trim();
      if (!q) { this.editError = 'Query 不能为空'; return; }
      this.saving = true; this.editError = '';
      try {
        await api.patch(`/api/tasks/${this.editForm.id}`, {
          query: q, mode: this.editForm.mode, priority: this.editForm.priority,
        });
        this.editForm = null;
        await this.load();
        if (this.detailTask) await this.open(this.detailTask);
      } catch (e) { this.editError = e.message; }
      finally { this.saving = false; }
    },
    async removeTask(t) {
      if (this.deleting) return;
      if (t.status === 'processing') { alert('任务正在生产中，请先在实时监控页中断后再删除'); return; }
      if (!confirm(`确定删除该任务？\n\n「${t.query}」\n\n将一并删除其正文、分页、配图、审核记录等全部产物，不可恢复。`)) return;
      this.deleting = t.id;
      try {
        await api.delete(`/api/tasks/${t.id}?actor=` + encodeURIComponent(this.actorName));
        if (this.detailTask && this.detailTask.id === t.id) this.close();
        await this.load();
      } catch (e) { alert('删除失败：' + e.message); }
      finally { this.deleting = ''; }
    },
  },
  watch: {
    fStatus() { this.load(); }, fMode() { this.load(); }, fRisk() { this.load(); },
  },
  async mounted() {
    this.load(); this.loadLive();
    try { this.nodes = (await api.get('/api/meta/nodes')).nodes; } catch (e) { /* 降级显示英文名 */ }
    this.timer = setInterval(() => { if (this.auto) { this.load(); this.loadLive(); } }, 5000);
    this.es = new EventSource('/api/stream/events');
    this.es.onmessage = (ev) => {
      try {
        const d = JSON.parse(ev.data);
        if (d.type === 'agent_progress') this.onAgentProgress();
        else if (d.type === 'export_progress') this.onExportStream(d.data || {});
        else if (d.type && (d.type.startsWith('task_') || d.type.startsWith('node_'))) this.onSse();
      } catch (e) { /* ping 等非 JSON 帧忽略 */ }
    };
    // 导入页刚完成导入的即时联动（本视图挂载后注册；跨页由路由重挂载自然刷新）
    this._onImported = () => { this.load(); this.loadLive(); };
    window.addEventListener('qvp:imported', this._onImported);
    // 点击空白处收起行操作菜单
    this._closeMenu = (e) => { if (!e.target.closest('.row-menu')) this.rowMenu = null; };
    document.addEventListener('click', this._closeMenu);
  },
  beforeUnmount() {
    clearInterval(this.timer);
    clearInterval(this.exportTimer);
    clearTimeout(this.sseTimer);
    clearTimeout(this.agentTimer);
    if (this.es) this.es.close();
    if (this._onImported) window.removeEventListener('qvp:imported', this._onImported);
    if (this._closeMenu) document.removeEventListener('click', this._closeMenu);
  },
  template: `
  <app-layout title="任务中心">
    <div class="card filter-bar">
      <select v-model="fStatus">
        <option value="">全部状态</option>
        <option v-for="(s, k) in STATUS" :key="k" :value="k">{{ s.label }}</option>
      </select>
      <select v-model="fMode">
        <option value="">全部模式</option>
        <option v-for="(m, k) in MODE" :key="k" :value="k">{{ m.label }}</option>
      </select>
      <select v-model="fRisk">
        <option value="">全部风险</option>
        <option value="green">绿</option><option value="yellow">黄</option><option value="red">红</option>
      </select>
      <label class="auto-refresh"><input type="checkbox" v-model="auto" style="width:auto"> 自动刷新</label>
      <input v-model="search" @keyup.enter="load" placeholder="🔍 搜索 Query…" style="width:170px">
      <button v-if="search" class="btn btn-outline btn-sm" @click="search=''; load()">清除</button>
      <template v-if="approvedCount > 0">
        <button v-if="!exportJob" class="btn btn-outline btn-sm" @click="startExport">📦 导出已通过内容包（{{ approvedCount }}）</button>
        <button v-else-if="exportJob.status !== 'done'" class="btn btn-outline btn-sm" @click="showExport = true">📦 打包中… {{ exportPct }}%</button>
        <button v-else class="btn btn-primary btn-sm" @click="showExport = true">📦 下载内容包（{{ (exportJob.parts || []).length }} 包）</button>
      </template>
      <span v-else class="btn btn-outline btn-sm" style="opacity:.55;cursor:not-allowed" title="任务经审核角色（A/B/C 任一）审核通过后，即进入导出通道">📦 导出已通过内容包（0）</span>
      <span class="muted" style="margin-left:auto">共 {{ total }} 条</span>
    </div>
    <p v-if="error" class="form-error">{{ error }}</p>
    <div class="card">
      <div v-if="!list.length" class="empty">暂无任务，<router-link to="/import">去导入 →</router-link></div>
      <table v-else class="table">
        <thead><tr><th>Query</th><th>模式</th><th>状态</th><th>风险</th><th>当前节点</th><th>创建时间</th><th style="text-align:right">操作</th></tr></thead>
        <tbody>
          <tr v-for="t in list" :key="t.id" @click="open(t)" :class="{selected: detailTask && detailTask.id === t.id}" :title="rowLiveMsg(t)">
            <td class="q-cell">{{ t.query }}</td>
            <td><span class="tag tag-blue">{{ modeLabel(t.mode) }}</span></td>
            <td>
              <span class="tag" :class="statusTag(rowStatus(t)).cls">{{ statusTag(rowStatus(t)).label }}</span>
              <span v-if="rowStatus(t) === 'processing'" class="live-dot" title="生产进行中（实时）"></span>
            </td>
            <td><span v-if="riskTag(t.risk_level)" class="tag" :class="riskTag(t.risk_level).cls">{{ riskTag(t.risk_level).label }}</span><span v-else class="muted">-</span></td>
            <td>
              {{ nodeLabel(rowNode(t)) }}
              <span v-if="rowNode(t) === 'agent_production' && rowStatus(t) === 'processing'" class="muted" style="font-size:12px">· 创作中…</span>
            </td>
            <td class="muted">{{ fmtTime(t.created_at) }}</td>
            <td style="white-space:nowrap;text-align:right;position:relative">
              <button class="btn btn-outline btn-sm" title="操作"
                      @click.stop="toggleMenu(t)">⋯</button>
              <div v-if="rowMenu === t.id" class="row-menu" @click.stop>
                <button class="row-menu-item" :disabled="!canEdit(t)"
                        :title="canEdit(t) ? '' : '仅排队/失败/驳回/中断状态可编辑'"
                        @click="rowMenu=null; openEdit(t)">✎ 编辑任务</button>
                <button class="row-menu-item danger" :disabled="!canDelete(t)"
                        :title="canDelete(t) ? '删除任务及其全部产物' : '生产中请先到实时监控页中断'"
                        @click="rowMenu=null; removeTask(t)">🗑 删除任务</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="detail || detailError" class="drawer-mask" @click.self="close">
      <div class="drawer">
        <p v-if="detailError" class="form-error">{{ detailError }}</p>
        <template v-if="detail">
          <div class="drawer-head">
            <h2>{{ detailTask.query }}</h2>
            <button class="btn btn-outline" @click="close">关闭</button>
          </div>
          <p>
            <span class="tag" :class="statusTag(detailTask.status).cls">{{ statusTag(detailTask.status).label }}</span>
            <span class="tag tag-blue">{{ modeLabel(detailTask.mode) }}</span>
            <span v-if="detailTask.gen_style" class="tag tag-blue">风格：{{ detailTask.gen_style }}</span>
            <span v-if="detailTask.gen_category" class="tag tag-blue">垂类：{{ detailTask.gen_category }}</span>
            <span v-if="detailTask.gen_image_style" class="tag tag-blue">配图：{{ detailTask.gen_image_style }}</span>
            <span v-if="detail.risk" class="tag" :class="riskTag(detail.risk.level).cls">风险：{{ riskTag(detail.risk.level).label }}</span>
            <span class="muted" style="margin-left:8px">{{ fmtTime(detailTask.created_at) }}</span>
          </p>
          <p v-if="detailTask.source_query" class="muted" style="margin:4px 0 0; white-space:pre-wrap; background:#f6f8fb; border-radius:8px; padding:8px 10px; font-size:13px">
            组合生成 · 原始提问：{{ detailTask.source_query }}
          </p>

          <h3>生产进度</h3>
          <steps-bar :nodes="nodes" :completed="detail.completed_nodes" :current="detailTask.status === 'failed' ? detail.current_node : (liveOfDetail && liveOfDetail.current_node) || detail.current_node" :failed="detailTask.status === 'failed'"></steps-bar>

          <!-- Agent 实时工作台：大节点内部子阶段 + 流式输出（生产中随时可看细节） -->
          <div v-if="agentStepIdx >= 0" class="agent-live">
            <div class="stage-row">
              <span v-for="(s, i) in AGENT_STEPS" :key="s"
                    class="stage-chip" :class="{done: i < agentStepIdx, doing: i === agentStepIdx}">
                {{ i < agentStepIdx ? '✓ ' : '' }}{{ s }}
              </span>
            </div>
            <p class="muted" style="margin:8px 0 0; font-size:13px">
              当前子阶段：<b>{{ liveOfDetail.stage_hint || '创作输出中…' }}</b>
              <template v-if="liveOfDetail.stream">
               　·　已输出 {{ liveOfDetail.stream.chars || 0 }} 字符 ≈ {{ liveOfDetail.stream.tokens_est || 0 }} tokens
              </template>
            </p>
            <div v-if="liveOfDetail.stream && liveOfDetail.stream.preview" class="stream-box">
              <div class="stream-head">Agent 流式输出（尾部实时预览）<span class="ec-cursor">▊</span></div>
              <p class="stream-text">{{ liveOfDetail.stream.preview }}</p>
            </div>
          </div>

          <!-- 节点明细：每个环节的状态/耗时/成本/模型/错误（两路径通用） -->
          <template v-if="detail.node_timeline && detail.node_timeline.length">
            <h3 style="margin-top:16px">节点明细</h3>
            <table class="table tl-table">
              <thead><tr><th>节点</th><th>状态</th><th>耗时</th><th>成本(¥)</th><th>模型/提示词</th></tr></thead>
              <tbody>
                <tr v-for="e in detail.node_timeline" :key="e.node">
                  <td>{{ nodeLabels[e.node] || e.node }}</td>
                  <td>
                    <span class="tag" :class="{done:'tag-green', failed:'tag-red', running:'tag-blue'}[e.status] || ''">
                      {{ {done:'完成', failed:'失败', running:'进行中', pending:'待开始'}[e.status] || e.status }}
                    </span>
                    <span v-if="e.error" class="muted" style="font-size:12px">　{{ e.error }}</span>
                  </td>
                  <td>{{ e.duration_s != null ? e.duration_s + 's' : '—' }}</td>
                  <td>{{ e.cost_cny != null ? e.cost_cny.toFixed(3) : '—' }}</td>
                  <td class="muted" style="font-size:12px">{{ e.model_version || '—' }}{{ e.prompt_version ? ' · ' + e.prompt_version : '' }}</td>
                </tr>
              </tbody>
            </table>
          </template>

          <!-- 参考图确认关卡：候选网格（OCR 命中在前）+ 勾选 + 确认继续 -->
          <template v-if="detailTask.status === 'awaiting_refs' && refCandidates().length">
            <h3 style="margin-top:16px">实景参考图确认 <span class="tag tag-yellow">待人工确认</span></h3>
            <p class="muted" style="font-size:13px;margin:4px 0 10px">
              已搜集 {{ refCandidates().length }} 张候选（系统按 OCR 命中排序，勾选保留后继续生产；未勾选将剔除）
            </p>
            <div class="img-grid ref-grid">
              <figure v-for="a in refCandidates()" :key="a.id"
                      :class="{unchecked: !refKeep[a.id]}">
                <img :src="a.display_url || a.image_url" loading="lazy" alt="" @click="openZoom(a, true)">
                <label class="ref-check">
                  <input type="checkbox" v-model="refKeep[a.id]" style="width:auto">
                  <span v-if="a.ocr_hit" class="tag tag-green" style="font-size:11px">OCR命中: {{ a.ocr_hit.slice(0, 14) }}</span>
                  <span v-else class="tag tag-gray" style="font-size:11px">无文字命中</span>
                </label>
              </figure>
            </div>
            <div style="margin:12px 0">
              <button class="btn btn-primary" :disabled="confirmingRefs"
                      @click="confirmRefs">{{ confirmingRefs ? '确认中…' : '✓ 确认保留 ' + refKeepCount + ' 张并继续生产' }}</button>
            </div>
          </template>

          <div v-if="canRetry" style="margin:12px 0">
            <button class="btn btn-primary" :disabled="retrying" @click="retry">{{ retrying ? '处理中…' : retryLabel }}</button>
          </div>

          <template v-if="detail.reject_marks && detail.reject_marks.length">
            <h3>定点驳回标记（重试时仅重生成这些项）</h3>
            <ul class="plain-list">
              <li v-for="m in detail.reject_marks" :key="m.item_type + m.page_index">
                <span class="tag tag-yellow">{{ m.item_type === 'page' ? '文案' : '配图' }} P{{ m.page_index }}</span> {{ m.reason }}
              </li>
            </ul>
          </template>

          <template v-if="detail.risk && detail.risk.reasons && detail.risk.reasons.length">
            <h3>风险原因</h3>
            <ul class="plain-list"><li v-for="r in detail.risk.reasons" :key="r">{{ r }}</li></ul>
          </template>

          <template v-if="detail.draft">
            <h3>正文（{{ detail.draft.model_version }}）</h3>
            <p class="article-body">{{ detail.draft.body }}</p>
          </template>

          <template v-if="detail.page_copies && detail.page_copies.length">
            <h3>分页文案</h3>
            <div v-for="p in detail.page_copies" :key="p.page_index" class="page-copy">
              <b>P{{ p.page_index }}</b>
              <p class="muted">{{ p.body }}</p>
            </div>
          </template>

          <template v-if="genAssets.length">
            <h3>交付配图（{{ genAssets.length }}）</h3>
            <div class="img-grid">
              <figure v-for="a in genAssets" :key="a.page_index">
                <img :src="a.display_url || a.image_url" loading="lazy" alt="" @click="openZoom(a, false)">
                <figcaption class="muted">P{{ a.page_index }} · AI 生成</figcaption>
              </figure>
            </div>
          </template>

          <template v-if="refAssets.length">
            <h3>实景参考图（{{ refAssets.length }}）<span class="muted" style="font-weight:normal;font-size:13px">仅作生图参考，不随内容交付</span></h3>
            <div class="img-grid">
              <figure v-for="a in refAssets" :key="a.page_index">
                <img :src="a.display_url || a.image_url" loading="lazy" alt="" @click="openZoom(a, true)">
                <figcaption class="muted">参考 {{ a.page_index }} · 实景抓取</figcaption>
              </figure>
            </div>
          </template>

          <template v-if="detail.claims && detail.claims.length">
            <h3>事实点</h3>
            <ul class="plain-list"><li v-for="c in detail.claims" :key="c.claim_text">{{ c.claim_text }} <span class="tag tag-gray">{{ c.risk_level }}</span></li></ul>
          </template>

          <template v-if="detail.evidences && detail.evidences.length">
            <h3>证据来源</h3>
            <ul class="plain-list"><li v-for="e in detail.evidences" :key="e.source_url"><a :href="e.source_url" target="_blank">{{ e.source_url }}</a></li></ul>
          </template>

          <h3>审核进度</h3>
          <div class="review-progress">
            <div v-for="r in detail.review" :key="r.role" class="review-cell">
              <span class="tag tag-blue">{{ r.role }} · {{ roleName(r.role) }}</span>
              <span v-if="r.action === 'approve'" class="tag tag-green">已通过</span>
              <span v-else-if="r.action === 'reject'" class="tag tag-red">已驳回</span>
              <span v-else class="tag tag-gray">待审</span>
              <span v-if="r.reviewer" class="muted">{{ r.reviewer }}</span>
            </div>
          </div>

          <template v-if="liveOfDetail && liveOfDetail.debug && liveOfDetail.debug.length">
            <h3>运行日志（本次运行）</h3>
            <pre class="log-box">{{ liveOfDetail.debug.map(d => '[' + d.node + '] ' + d.phase + ' ' + (d.elapsed || '') + 's ' + (d.msg || '') + (d.trace ? '\\n' + d.trace : '')).join('\\n') }}</pre>
          </template>
        </template>
        <div v-else-if="!detailError" class="empty">加载中…</div>
      </div>
    </div>
    <div v-if="showExport && exportJob" class="drawer-mask" @click.self="showExport = false">
      <div class="export-modal export-modal-lg">
        <div class="drawer-head">
          <h2>📦 导出已通过内容包</h2>
          <span class="tag" :class="exportJob.status === 'done' ? 'tag-green' : (exportJob.status === 'error' ? 'tag-red' : 'tag-blue')">
            {{ exportJob.status === 'done' ? '完成' : (exportJob.status === 'error' ? '失败' : '打包中') }}
          </span>
          <button class="btn btn-outline btn-sm" @click="showExport = false">关闭</button>
        </div>

        <div class="export-stats">
          <span>📊 {{ exportJob.done || 0 }} / {{ exportJob.total }} 条</span>
          <span>🖼 {{ exportJob.images_done || 0 }} 张配图</span>
          <span>📦 {{ (exportJob.parts_done || []).length }} / {{ exportJob.parts_expected || '?' }} 包就绪</span>
          <span v-if="exportJob.status === 'running'">⏱ {{ exportElapsedStr() }} · {{ exportSpeed() }}</span>
        </div>

        <div class="export-progress-row">
          <div class="bar-track"><div class="bar-fill bar-anim" :style="{width: exportPct + '%'}"></div></div>
          <span class="bar-count">{{ exportPct }}%</span>
        </div>

        <div ref="exportConsole" class="export-console">
          <div v-for="(l, i) in exportLog" :key="i" class="ec-line" :class="'ec-' + l.k">
            <span class="ec-ts">{{ l.t }}</span> {{ l.m }}
          </div>
          <div v-if="exportJob.status === 'running'" class="ec-line ec-dim"><span class="ec-ts">--:--:--</span> <span class="ec-cursor">▊</span></div>
        </div>

        <div v-if="(exportJob.parts_done || []).length || (exportJob.status === 'done' && (exportJob.parts || []).length)" class="export-parts">
          <div v-if="(exportJob.parts_done || []).length || (exportJob.parts || []).length"
               style="width:100%;display:flex;justify-content:flex-end;margin-bottom:2px">
            <button class="btn btn-outline btn-sm" @click="clearExportParts">🧹 一键清理全部分包（入回收站）</button>
          </div>
          <template v-if="exportJob.status === 'done'">
            <span v-for="p in exportJob.parts" :key="p.part" class="part-btn-wrap">
              <a class="btn btn-primary btn-sm"
                 :href="'/api/export/' + exportJob.id + '/download/' + p.part" style="text-decoration:none">⬇ 第{{ p.part }}包（{{ p.tasks }}条 · {{ fmtSize(p.size) }}）</a>
              <button class="btn btn-sm btn-danger-ghost part-del" title="删除该包（进入回收站）"
                      @click="deleteExportPart(exportJob.id, p.part)">✕</button>
            </span>
          </template>
          <template v-else>
            <span v-for="p in exportJob.parts_done" :key="p.part" class="part-btn-wrap">
              <a class="btn btn-outline btn-sm"
                 :href="'/api/export/' + exportJob.id + '/download/' + p.part" style="text-decoration:none">⬇ 第{{ p.part }}包（{{ p.tasks }}条 · {{ fmtSize(p.size) }}）</a>
              <button class="btn btn-sm btn-danger-ghost part-del" title="删除该包（进入回收站）"
                      @click="deleteExportPart(exportJob.id, p.part)">✕</button>
            </span>
            <span class="part-chip pending">第 {{ (exportJob.parts_done || []).length + 1 }} 包打包中…</span>
            <span v-for="n in Math.max(0, (exportJob.parts_expected || 1) - (exportJob.parts_done || []).length - 1)"
                  :key="'w' + n" class="part-chip">第 {{ (exportJob.parts_done || []).length + 1 + n }} 包 待生成</span>
          </template>
        </div>

        <!-- 回收站（仅 admin）：删除/清理的分包在此暂存，72h 自动清理 -->
        <div v-if="isAdmin" class="recycle-box">
          <div class="mlog-head" @click="showRecycle = !showRecycle; if (showRecycle) loadRecycle()">
            <span class="mlog-toggle">{{ showRecycle ? '▾' : '▸' }}</span>
            🗑 回收站（{{ recycleItems.length }} 项 · 仅管理员可见 · 72 小时自动清理）
          </div>
          <div v-if="showRecycle" class="recycle-list">
            <div v-if="!recycleItems.length" class="muted" style="padding:8px 2px">回收站为空</div>
            <div v-for="r in recycleItems" :key="r.filename" class="recycle-row">
              <span class="muted" style="font-size:12px">{{ new Date(r.deleted_ts * 1000).toLocaleString('zh-CN', {hour12:false}) }}</span>
              <span style="font-size:13px">{{ r.filename }}</span>
              <span class="muted" style="font-size:12px">{{ r.tasks }}条 · {{ fmtSize(r.size) }} · 删于{{ r.deleted_by }}</span>
              <span class="tag tag-yellow" style="font-size:11px">剩 {{ r.expires_in_hours }}h</span>
              <button class="btn btn-sm btn-danger-ghost" style="margin-left:auto" @click="purgeRecycleItem(r.filename)">彻底删除</button>
            </div>
          </div>
        </div>

        <p class="muted" style="font-size:12.5px">打包在服务器后台进行，分包就绪即可先行下载；关闭本窗口不会中断，可稍后再点开查看。内容包保留约 1 小时；删除/清理的分包进回收站（仅管理员可见，72h 自动清理）。</p>
        <div v-if="exportJob.status === 'done'" style="text-align:right;margin-top:10px">
          <button class="btn btn-primary btn-sm" @click="exportJob = null; showExport = false">完成</button>
        </div>
      </div>
    </div>
    <img-lightbox :img="zoom" @close="zoom=null" />

    <div v-if="editForm" class="drawer-mask" @click.self="editForm=null">
      <div class="card" style="width:440px;margin:16vh auto 0">
        <h2>编辑任务</h2>
        <p class="muted" style="font-size:13px">当前状态：{{ statusTag(editForm.status).label }}（排队/失败/驳回/中断状态可编辑）</p>
        <form @submit.prevent="saveEdit">
          <label>Query</label>
          <textarea v-model="editForm.query" rows="3" required></textarea>
          <label style="margin-top:10px">生产模式</label>
          <select v-model="editForm.mode" style="width:100%">
            <option value="general">通用（纯文生图）</option>
            <option value="single">单品（搜参考图·图生图）</option>
            <option value="compare">对比（搜参考图·图生图）</option>
          </select>
          <label style="margin-top:10px">优先级</label>
          <select v-model="editForm.priority" style="width:100%">
            <option value="urgent">加急（优先调度）</option>
            <option value="normal">普通</option>
            <option value="scheduled">定时（靠后调度）</option>
          </select>
          <p v-if="editError" class="form-error">{{ editError }}</p>
          <div style="display:flex;gap:8px;margin-top:14px">
            <button class="btn btn-primary" :disabled="saving">{{ saving ? '保存中…' : '保存修改' }}</button>
            <button type="button" class="btn btn-outline" @click="editForm=null">取消</button>
          </div>
        </form>
      </div>
    </div>
  </app-layout>`,
  created() { this.STATUS = STATUS; this.MODE = MODE; this.AGENT_STEPS = AGENT_STEPS; },
};
