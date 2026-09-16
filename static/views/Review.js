// 审核工作台：按当前用户角色的待审队列 → 领取 → 审 → 通过/驳回
const ReviewView = {
  data() {
    return {
      queue: [], error: '', msg: '',
      current: null,        // /api/review/task/{id} 结果
      currentId: null,      // 当前选中任务 id（/api/review/task 的 task 不含 id）
      detail: null,         // /api/tasks/{id}/detail（三方进度/节点）
      claimed: false, lockedBy: '',
      seconds: 0, hbTimer: null, tickTimer: null,
      showReject: false, rejectReason: '', acting: false,
      marks: {},            // 定点驳回标记 {"page:2": {item_type, page_index, reason}}
      zoom: null,           // 图片放大浏览 {src, title, text}
      allAccess: false,     // 试运行期全员开放三角色（ROLE_ALL_ACCESS）
      activeRole: '',       // 当前审核角色（allAccess 时可切换，默认账号自身角色）
      sortOrder: 'desc',    // 左侧队列排序：desc=最新在前，asc=最早在前
      selected: {},         // task_id -> bool（左侧队列批量选择）
      showBatchMenu: false, // 批量操作菜单展开/收起
    };
  },
  computed: {
    user() { return getUser(); },
    role() { return this.user ? this.user.role : ''; },
    isReviewer() { return ['A', 'B', 'C'].includes(this.role) || this.allAccess; },
    marksList() { return Object.values(this.marks).sort((a, b) => a.page_index - b.page_index); },
    sortedQueue() {
      const dir = this.sortOrder === 'asc' ? 1 : -1;
      return (this.queue || []).slice().sort((a, b) => {
        const ta = a.created_at ? new Date(a.created_at).getTime() : 0;
        const tb = b.created_at ? new Date(b.created_at).getTime() : 0;
        return (ta - tb) * dir;
      });
    },
    timerText() {
      const m = String(Math.floor(this.seconds / 60)).padStart(2, '0');
      const s = String(this.seconds % 60).padStart(2, '0');
      return `${m}:${s}`;
    },
    selectedIds() { return Object.keys(this.selected).filter(k => this.selected[k]); },
    allSelected() { return this.sortedQueue.length > 0 && this.sortedQueue.every(t => this.selected[t.task_id]); },
    // 交付配图（AI 生成）与实景参考图分区展示
    genAssets() {
      return ((this.current && this.current.assets) || []).filter(a => a.source_type !== 'official');
    },
    refAssets() {
      return ((this.current && this.current.assets) || []).filter(a => a.source_type === 'official');
    },
  },
  methods: {
    thumbOf,    // api.js：网格缩略图
    fmtTime, roleName,
    riskTag(r) { return RISK[r] || null; },
    modeLabel(m) { return MODE[m] ? MODE[m].label : (m || '-'); },
    markKey(t, p) { return `${t}:${p}`; },
    isMarked(t, p) { return !!this.marks[this.markKey(t, p)]; },
    toggleMark(t, p) {
      const k = this.markKey(t, p);
      const m = { ...this.marks };
      if (m[k]) delete m[k];
      else m[k] = { item_type: t, page_index: p, reason: '' };
      this.marks = m;
    },
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
    async loadQueue() {
      if (!this.isReviewer || !this.activeRole) return;
      try { this.queue = (await api.get(`/api/review/queue/${this.activeRole}`)).sessions || []; this.error = ''; }
      catch (e) { this.error = e.message; }
      // 刷新后保留仍存在于队列中的选中项
      const sel = {};
      for (const t of this.queue) if (this.selected[t.task_id]) sel[t.task_id] = true;
      this.selected = sel;
    },
    switchRole(r) {
      if (r === this.activeRole) return;
      this.releaseTimers();
      this.activeRole = r;
      this.queue = []; this.current = null; this.currentId = null;
      this.claimed = false; this.lockedBy = ''; this.msg = ''; this.error = '';
      this.marks = {}; this.showReject = false; this.rejectReason = '';
      this.selected = {};
      this.showBatchMenu = false;
      this.loadQueue();
    },
    async select(t) {
      this.releaseTimers();
      this.current = null; this.currentId = t.task_id; this.detail = null; this.claimed = false; this.lockedBy = ''; this.msg = ''; this.error = '';
      this.marks = {}; this.showReject = false; this.rejectReason = '';
      try {
        const [c, d] = await Promise.all([
          api.get(`/api/review/task/${t.task_id}`),
          api.get(`/api/tasks/${t.task_id}/detail`),
        ]);
        this.current = c; this.detail = d;
      } catch (e) { this.error = e.message; }
    },
    async claim() {
      this.error = '';
      try {
        const r = await api.post('/api/review/claim', { task_id: this.currentId, role: this.activeRole, reviewer_id: this.user.name });
        if (!r.acquired) { this.lockedBy = r.locked_by || '其他人'; return; }
        this.claimed = true; this.seconds = 0;
        this.tickTimer = setInterval(() => { this.seconds++; }, 1000);
        this.hbTimer = setInterval(() => {
          api.post('/api/review/heartbeat', { task_id: this.currentId, role: this.activeRole, reviewer_id: this.user.name, client_ts: Date.now() }).catch(() => {});
        }, 10000);
      } catch (e) { this.error = e.message; }
    },
    async act(actionType) {
      if (actionType === 'reject') {
        const bad = this.marksList.find(m => !m.reason.trim());
        if (bad) {
          this.error = `请填写 ${bad.item_type === 'page' ? '文案' : '配图'} P${bad.page_index} 的问题说明`;
          return;
        }
        if (!this.marksList.length && !this.rejectReason.trim()) {
          this.error = '驳回必须填写原因，或标记具体有问题的文案/配图';
          return;
        }
      }
      this.acting = true; this.error = '';
      try {
        await api.post('/api/review/action', {
          task_id: this.currentId, role: this.activeRole, reviewer_id: this.user.name,
          action_type: actionType, reason: actionType === 'reject' ? this.rejectReason.trim() : '',
          marks: actionType === 'reject'
            ? this.marksList.map(m => ({ item_type: m.item_type, page_index: m.page_index, reason: m.reason.trim() }))
            : [],
        });
        this.msg = actionType === 'approve' ? '已通过'
          : (this.marksList.length
              ? `已驳回 ${this.marksList.length} 项标记，已自动重做对应内容（无需再点重试）`
              : '已驳回，已自动重新生成全文全图（无需再点重试）');
        this.showReject = false; this.rejectReason = ''; this.marks = {};
        this.releaseTimers();
        const id = this.currentId;
        await this.loadQueue();
        await this.select({ task_id: id });   // 刷新三方进度展示
      } catch (e) { this.error = e.message; }
      finally { this.acting = false; }
    },
    toggleSelectAll() {
      const all = !this.allSelected;
      const sel = { ...this.selected };
      for (const t of this.sortedQueue) sel[t.task_id] = all;
      this.selected = sel;
    },
    async batchApprove() {
      const ids = this.selectedIds;
      if (!ids.length) { this.error = '请先选择任务'; return; }
      this.acting = true; this.error = '';
      try {
        const r = await api.post('/api/review/batch_approve', { task_ids: ids, role: this.activeRole, reviewer_id: this.user.name });
        this.msg = `批量通过 ${r.approved} 条（跳过 ${r.skipped.length}）`;
        this.selected = {};
        this.showBatchMenu = false;
        await this.loadQueue();
      } catch (e) { this.error = e.message; }
      finally { this.acting = false; }
    },
    async batchDelete() {
      const ids = this.selectedIds;
      if (!ids.length) { this.error = '请先选择任务'; return; }
      this.acting = true; this.error = '';
      try {
        const r = await api.post('/api/tasks/batch_delete', { ids, actor: this.user.name });
        this.msg = `已删除 ${r.deleted} 条（跳过 ${r.skipped.length}）`;
        this.selected = {};
        this.showBatchMenu = false;
        await this.loadQueue();
      } catch (e) { this.error = e.message; }
      finally { this.acting = false; }
    },
    releaseTimers() {
      clearInterval(this.hbTimer); clearInterval(this.tickTimer);
      this.hbTimer = this.tickTimer = null;
    },
  },
  async mounted() {
    // 试运行期（ROLE_ALL_ACCESS=true）：全员开放 A/B/C 切换；默认进自己账号的角色
    try {
      this.allAccess = !!(await api.get('/api/meta/access')).role_all_access;
    } catch (e) { /* 取不到按收权处理 */ }
    this.activeRole = ['A', 'B', 'C'].includes(this.role) ? this.role : 'A';
    this.loadQueue();
  },
  beforeUnmount() { this.releaseTimers(); },
  template: `
  <app-layout title="任务审核">
    <div v-if="!isReviewer" class="card empty">当前账号不是审核角色（A/B/C），无待审队列。</div>
    <template v-else>
      <p v-if="error" class="form-error">{{ error }}</p>
      <p v-if="msg" class="form-ok">{{ msg }}</p>
      <div class="review-layout">
        <div class="card review-queue">
          <h2>待审队列</h2>
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
            <span style="font-size:14px;font-weight:600">待审队列</span>
            <div style="display:flex;align-items:center;gap:8px;margin-left:auto">
              <select v-model="sortOrder" style="width:auto;padding:4px 8px;font-size:13px">
                <option value="desc">最新在前</option>
                <option value="asc">最早在前</option>
              </select>
              <button class="btn btn-outline btn-sm" @click="loadQueue">刷新</button>
            </div>
          </div>
          <div style="margin:10px 0">
            <button class="btn btn-outline btn-sm" @click="showBatchMenu = !showBatchMenu">
              批量操作 {{ showBatchMenu ? '▲' : '▼' }}
            </button>
            <div v-if="showBatchMenu" style="margin-top:8px;padding:10px;border:1px solid var(--border);border-radius:8px;background:var(--card)">
              <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
                <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px;white-space:nowrap">
                  <input type="checkbox" :checked="allSelected" @change="toggleSelectAll" style="width:auto"> 全选
                </label>
                <button class="btn btn-success btn-sm" style="white-space:nowrap" :disabled="acting || !selectedIds.length" @click="batchApprove">✓ 通过选中（{{ selectedIds.length }}）</button>
                <button class="btn btn-danger btn-sm" style="white-space:nowrap" :disabled="acting || !selectedIds.length" @click="batchDelete">✗ 删除选中（{{ selectedIds.length }}）</button>
              </div>
            </div>
          </div>
          <div v-if="!sortedQueue.length" class="empty">暂无待审任务</div>
          <div v-for="t in sortedQueue" :key="t.task_id" class="queue-item" :class="{on: currentId === t.task_id}" @click="select(t)">
            <div style="display:flex;align-items:flex-start;gap:8px">
              <input type="checkbox" v-model="selected[t.task_id]" @click.stop style="margin-top:4px;flex-shrink:0;width:auto">
              <div style="flex:1;min-width:0">
                <div class="q">{{ t.query }}</div>
                <div>
                  <span class="tag tag-blue">{{ modeLabel(t.mode) }}</span>
                  <span v-if="riskTag(t.risk_level)" class="tag" :class="riskTag(t.risk_level).cls">风险：{{ riskTag(t.risk_level).label }}</span>
                  <span v-if="t.locked" class="tag tag-yellow">🔒 {{ t.locked_by }} 审核中</span>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div class="review-main">
          <div v-if="!current" class="card empty">← 从左侧队列选择一条任务开始审核</div>
          <template v-else>
            <div class="card">
              <h2>{{ current.task.query }}</h2>
              <div class="review-progress" style="margin:10px 0" v-if="detail">
                <div v-for="r in detail.review" :key="r.role" class="review-cell">
                  <span class="tag tag-blue">{{ r.role }} · {{ roleName(r.role) }}</span>
                  <span v-if="r.action === 'approve'" class="tag tag-green">已通过</span>
                  <span v-else-if="r.action === 'reject'" class="tag tag-red">已驳回</span>
                  <span v-else class="tag tag-gray">待审</span>
                  <span v-if="r.reviewer" class="muted">{{ r.reviewer }}</span>
                </div>
              </div>
              <div style="display:flex;align-items:center;gap:10px">
                <template v-if="!claimed">
                  <button class="btn btn-primary" @click="claim">🔒 领取任务</button>
                  <span v-if="lockedBy" class="muted">该任务正被 {{ lockedBy }} 审核中，稍后可抢占</span>
                </template>
                <template v-else>
                  <span class="tag tag-green">已领取 · 计时 {{ timerText }}</span>
                  <button class="btn btn-success" :disabled="acting" @click="act('approve')">✓ 通过</button>
                  <button class="btn btn-danger" :disabled="acting" @click="showReject = !showReject">✗ 驳回</button>
                </template>
              </div>
              <div v-if="showReject" style="margin-top:12px">
                <template v-if="marksList.length">
                  <label>定点问题标记（重试时仅重生成这些项，其余内容保留不重跑）</label>
                  <div v-for="m in marksList" :key="m.item_type + m.page_index" style="display:flex;align-items:center;gap:8px;margin:6px 0">
                    <span class="tag tag-yellow" style="white-space:nowrap">{{ m.item_type === 'page' ? '文案' : '配图' }} P{{ m.page_index }}</span>
                    <input v-model="m.reason" placeholder="该项的问题说明（必填）" style="flex:1">
                    <button class="btn btn-outline btn-sm" @click="toggleMark(m.item_type, m.page_index)">移除</button>
                  </div>
                </template>
                <label>整体驳回原因{{ marksList.length ? '（选填）' : '（必填，或改为上方标记具体项）' }}</label>
                <textarea v-model="rejectReason" rows="3" :placeholder="marksList.length ? '可补充整体说明' : '请说明驳回原因；也可在下方分页文案/配图上标记具体有问题的项'"></textarea>
                <button class="btn btn-danger" style="margin-top:8px" :disabled="acting" @click="act('reject')">确认驳回</button>
              </div>
            </div>

            <div class="card" v-if="current.draft">
              <h2>正文</h2>
              <p class="article-body">{{ current.draft.body }}</p>
            </div>
            <div class="card" v-if="detail && detail.page_copies && detail.page_copies.length">
              <h2>分页文案 <span class="muted" style="font-weight:normal;font-size:13px">有问题的页可点「标问题」定点驳回，重试只重做该页</span></h2>
              <div v-for="p in detail.page_copies" :key="p.page_index" class="page-copy" style="display:flex;align-items:flex-start;gap:10px">
                <b style="white-space:nowrap">P{{ p.page_index }}</b>
                <p class="muted" style="flex:1;margin:0">{{ p.body }}</p>
                <button class="btn btn-sm" :class="isMarked('page', p.page_index) ? 'btn-danger' : 'btn-outline'"
                        @click="toggleMark('page', p.page_index); showReject = true">
                  {{ isMarked('page', p.page_index) ? '✓ 已标记' : '⚑ 标问题' }}
                </button>
              </div>
            </div>
            <div class="card" v-if="genAssets.length">
              <h2>交付配图 <span class="muted" style="font-weight:normal;font-size:13px">有问题的图可点「标问题」定点驳回，重试只重做该图</span></h2>
              <div class="img-grid">
                <figure v-for="a in genAssets" :key="a.page_index">
                  <img :src="thumbOf(a)" loading="lazy" alt="" @click="openZoom(a, false)">
                  <figcaption class="muted">P{{ a.page_index }}
                    <button class="btn btn-sm" :class="isMarked('image', a.page_index) ? 'btn-danger' : 'btn-outline'"
                            style="margin-left:6px" @click.stop="toggleMark('image', a.page_index); showReject = true">
                      {{ isMarked('image', a.page_index) ? '✓ 已标记' : '⚑ 标问题' }}
                    </button>
                  </figcaption>
                </figure>
              </div>
            </div>
            <div class="card" v-if="refAssets.length">
              <h2>实景参考图 <span class="muted" style="font-weight:normal;font-size:13px">仅作生图参考，不随内容交付</span></h2>
              <div class="img-grid">
                <figure v-for="a in refAssets" :key="a.page_index">
                  <img :src="thumbOf(a)" loading="lazy" alt="" @click="openZoom(a, true)">
                  <figcaption class="muted">参考 {{ a.page_index }} · 实景抓取</figcaption>
                </figure>
              </div>
            </div>
            <div class="grid grid-2">
              <div class="card" v-if="current.claims && current.claims.length">
                <h2>事实点</h2>
                <ul class="plain-list"><li v-for="c in current.claims" :key="c.claim_text || c">{{ c.claim_text || c }} <span v-if="c.risk_level" class="tag tag-gray">{{ c.risk_level }}</span></li></ul>
              </div>
              <div class="card" v-if="current.evidences && current.evidences.length">
                <h2>证据</h2>
                <ul class="plain-list"><li v-for="e in current.evidences" :key="e.source_url || e"><a v-if="e.source_url" :href="e.source_url" target="_blank">{{ e.source_url }}</a><span v-else>{{ e }}</span></li></ul>
              </div>
            </div>
            <div class="card" v-if="current.risk">
              <h2>风险判定</h2>
              <p><span v-if="riskTag(current.risk.level)" class="tag" :class="riskTag(current.risk.level).cls">{{ riskTag(current.risk.level).label }}</span></p>
              <ul class="plain-list" v-if="current.risk.reasons"><li v-for="r in current.risk.reasons" :key="r">{{ r }}</li></ul>
            </div>
          </template>
        </div>
      </div>
    </template>
    <img-lightbox :img="zoom" @close="zoom=null" />
  </app-layout>`,
  created() { this.MODE = MODE; },
};
