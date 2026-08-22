// 任务中心：列表（筛选/轮询/SSE 实时）+ 详情抽屉（13 节点进度 + 全部产物 + debug 日志）
const TasksView = {
  data() {
    return {
      list: [], total: 0, error: '', loading: false,
      approvedCount: 0,      // 审核通过的任务数（>0 才可导出内容包）
      fStatus: '', fMode: '', fRisk: '', auto: true,
      nodes: [], timer: null, es: null, sseTimer: null,
      live: {},              // task_id -> 内存实时态（current_node/debug/imgs）
      detail: null, detailError: '', retrying: false,
      exportJob: null, exportTimer: null,   // 任务式导出进度 {id,status,total,done,detail}
      showExport: false,     // 导出弹窗开关（关闭不中断后台打包）
      zoom: null,            // 图片放大浏览 {src, title, text}
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
    actorName() { return (getUser() || {}).name || 'anonymous'; },
    liveOfDetail() {
      return this.detailTask ? (this.live[this.detailTask.id] || null) : null;
    },
    canRetry() {
      return this.detailTask && ['failed', 'rejected'].includes(this.detailTask.status);
    },
    retryLabel() {
      if (!this.detailTask) return '';
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
      try { this.detail = await api.get(`/api/tasks/${t.id}/detail`); }
      catch (e) { this.detailError = e.message; }
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
    async startExport() {
      this.error = '';
      try {
        const r = await api.post('/api/export/approved/start?actor=' + encodeURIComponent(this.actorName));
        this.exportJob = { id: r.job_id, status: 'running', total: r.total, done: 0, detail: '启动打包…' };
        this.showExport = true;
        this.exportTimer = setInterval(this.pollExport, 1000);
      } catch (e) { this.error = e.message; }
    },
    async pollExport() {
      if (!this.exportJob) return;
      try {
        const s = await api.get('/api/export/' + this.exportJob.id);
        Object.assign(this.exportJob, s);
        if (s.status === 'done') {
          // 打包完成：展示分包下载按钮，用户逐包下载（不自动触发）
          clearInterval(this.exportTimer); this.exportTimer = null;
        } else if (s.status === 'error') {
          clearInterval(this.exportTimer); this.exportTimer = null;
          this.error = '导出失败：' + (s.error || '未知错误');
          this.exportJob = null;
          this.showExport = false;
        }
      } catch (e) { /* 单次轮询失败静默，下轮重试 */ }
    },
    fmtSize(b) {
      if (!b) return '0B';
      return b >= 1048576 ? (b / 1048576).toFixed(1) + 'MB' : Math.round(b / 1024) + 'KB';
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
        if (d.type && (d.type.startsWith('task_') || d.type.startsWith('node_'))) this.onSse();
      } catch (e) { /* ping 等非 JSON 帧忽略 */ }
    };
  },
  beforeUnmount() {
    clearInterval(this.timer);
    clearInterval(this.exportTimer);
    clearTimeout(this.sseTimer);
    if (this.es) this.es.close();
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
        <thead><tr><th>Query</th><th>模式</th><th>状态</th><th>风险</th><th>当前节点</th><th>创建时间</th></tr></thead>
        <tbody>
          <tr v-for="t in list" :key="t.id" @click="open(t)" :class="{selected: detailTask && detailTask.id === t.id}">
            <td class="q-cell">{{ t.query }}</td>
            <td><span class="tag tag-blue">{{ modeLabel(t.mode) }}</span></td>
            <td><span class="tag" :class="statusTag(t.status).cls">{{ statusTag(t.status).label }}</span></td>
            <td><span v-if="riskTag(t.risk_level)" class="tag" :class="riskTag(t.risk_level).cls">{{ riskTag(t.risk_level).label }}</span><span v-else class="muted">-</span></td>
            <td>{{ nodeLabel(t.current_node) }}</td>
            <td class="muted">{{ fmtTime(t.created_at) }}</td>
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
            <span v-if="detail.risk" class="tag" :class="riskTag(detail.risk.level).cls">风险：{{ riskTag(detail.risk.level).label }}</span>
            <span class="muted" style="margin-left:8px">{{ fmtTime(detailTask.created_at) }}</span>
          </p>

          <h3>生产进度</h3>
          <steps-bar :nodes="nodes" :completed="detail.completed_nodes" :current="detailTask.status === 'failed' ? detail.current_node : (liveOfDetail && liveOfDetail.current_node) || detail.current_node" :failed="detailTask.status === 'failed'"></steps-bar>

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
      <div class="export-modal">
        <div class="drawer-head">
          <h2>📦 导出已通过内容包</h2>
          <button class="btn btn-outline btn-sm" @click="showExport = false">关闭</button>
        </div>
        <template v-if="exportJob.status !== 'done'">
          <div class="export-progress-row">
            <div class="bar-track"><div class="bar-fill" :style="{width: exportPct + '%'}"></div></div>
            <span class="bar-count">{{ exportPct }}%</span>
          </div>
          <p class="muted export-detail">{{ exportJob.detail }}（{{ exportJob.done }}/{{ exportJob.total }}）</p>
          <p class="muted">打包在服务器后台进行，关闭本窗口不会中断，可稍后再点开查看。</p>
        </template>
        <template v-else>
          <p class="export-detail">{{ exportJob.detail }}</p>
          <div class="export-parts">
            <a v-for="p in exportJob.parts" :key="p.part" class="btn btn-outline btn-sm"
               :href="'/api/export/' + exportJob.id + '/download/' + p.part" style="text-decoration:none">⬇ 第{{ p.part }}包（{{ p.tasks }}条 · {{ fmtSize(p.size) }}）</a>
          </div>
          <p class="muted">每 10 条打成一个 zip，逐包点击下载；下载进度由浏览器管理。内容包在服务器保留约 1 小时。</p>
          <div style="text-align:right;margin-top:14px">
            <button class="btn btn-primary btn-sm" @click="exportJob = null; showExport = false">完成</button>
          </div>
        </template>
      </div>
    </div>
    <img-lightbox :img="zoom" @close="zoom=null" />
  </app-layout>`,
  created() { this.STATUS = STATUS; this.MODE = MODE; },
};
