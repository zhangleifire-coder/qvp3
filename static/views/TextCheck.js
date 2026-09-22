// 文字核查：query 中文自查 + 文案/生图描述人工最终核查（独立板块，2026-08-27）
// 流程：导入任务 → text_check 自动自查起草 → 此处人工核查/修正 → 放行进入生产。
const TextCheckView = {
  data() {
    return {
      items: [], total: 0, loading: false, error: '',
      cur: null, detail: null, form: null, confirming: false, timer: null,
      redrafting: false,   // 重新起草中防抖
      marks: {},           // 驳回标记 {target: {target, note}}（query/body/page:N/ip:N）
      rejecting: false,    // 按标记驳回重写中防抖
      busyNote: '',        // 后台改写/起草中的提示（自动刷新结果）
      draftTimer: null,    // 后台处理完成检测轮询
      bodyPreview: true,   // 正文默认文档格式预览（md.js 渲染），点「编辑」才出纯文本框
      sortOrder: 'desc',   // 左侧队列排序：desc=最新在前，asc=最早在前
      selected: {},        // task_id -> bool（左侧队列批量选择）
      showBatchMenu: false,// 批量操作菜单展开/收起
    };
  },
  computed: {
    awaitingCount() { return this.total; },
    pageCount() { return (this.form.pages && this.form.pages.length) || 6; },
    queryRelevance() { return this.review.query_relevance || null; },
    review() { return (this.detail && this.detail.task.text_review) || {}; },
    issues() { return (this.review.query_clean || {}).issues || []; },
    bodyIssues() { return this.review.body_issues || []; },
    autoOk() { return this.review.auto_ok; },
    draftError() { return !!this.review.draft_error; },
    isManual() { return this.review.source === 'manual'; },
    userBody() { return this.review.user_body || ''; },
    marksList() { return Object.values(this.marks).filter(m => (m.note || '').trim()); },
    lastFeedback() { return this.review.last_feedback || []; },
    bodyChars() { return (this.form && this.form.body || '').replace(/\s/g, '').length; },
    sortedItems() {
      const dir = this.sortOrder === 'asc' ? 1 : -1;
      return (this.items || []).slice().sort((a, b) => {
        const ta = a.created_at ? new Date(a.created_at).getTime() : 0;
        const tb = b.created_at ? new Date(b.created_at).getTime() : 0;
        return (ta - tb) * dir;
      });
    },
    selectedIds() { return Object.keys(this.selected).filter(k => this.selected[k]); },
    allSelected() { return this.sortedItems.length > 0 && this.sortedItems.every(t => this.selected[t.id]); },
  },
  methods: {
    renderMd,           // md.js：正文预览按文档格式渲染
    targetLabel(t) {
      if (t === 'query') return 'Query';
      if (t === 'body') return '正文';
      const parts = String(t).split(':');
      return parts[0] === 'page' ? ('第' + parts[1] + '页文案') : ('第' + parts[1] + '页生图描述');
    },
    // textarea 自适应高度：内容完整显示，不出内部滚动条
    fitField(e) {
      const el = e && e.target;
      if (!el) return;
      el.style.height = 'auto';
      el.style.height = (el.scrollHeight + 2) + 'px';
    },
    fitAll() {
      this.$nextTick(() => {
        document.querySelectorAll('.tc-field').forEach(el => {
          el.style.height = 'auto';
          el.style.height = (el.scrollHeight + 2) + 'px';
        });
      });
    },
    async load() {
      this.loading = true; this.error = '';
      try {
        const r = await api.get('/api/tasks/text/awaiting');
        this.items = r.items || []; this.total = r.total || 0;
        if (this.cur) {
          const still = this.items.find(i => i.id === this.cur.id);
          if (!still) { this.cur = null; this.detail = null; }
        }
        // 刷新后保留仍存在于队列中的选中项
        const sel = {};
        for (const t of this.items) if (this.selected[t.id]) sel[t.id] = true;
        this.selected = sel;
      } catch (e) { this.error = e.message; }
      finally { this.loading = false; }
    },
    async pick(t) {
      this.cur = t; this.detail = null; this.marks = {};
      try {
        this.detail = await api.get(`/api/tasks/${t.id}/detail`);
        const rv = this.review;
        // 表单初始值：人工修改版 > 自动草稿
        const ov = this.detail.task.text_override || {};
        this.form = {
          query: ov.query || (rv.query_clean && rv.query_clean.suggested && this.issues.length
                              ? rv.query_clean.suggested : (rv.query || t.query)),
          body: ov.body || rv.body_draft || '',
          pages: ov.pages || (rv.pages_draft || []),
          image_prompts: ov.image_prompts || (rv.image_prompt_draft || []),
        };
      } catch (e) { this.error = e.message; }
      const rvNow = this.review;
      if (rvNow.feedback || rvNow.redrafting) {
        this.busyNote = 'AI 正在改写/起草中（约 1-5 分钟）——完成后此页自动刷新';
        this.watchDraft();
      } else if (!this.draftTimer) {
        this.busyNote = '';
      }
      this.fitAll();
    },
    async removeTask(t) {
      if (!confirm(`确定删除任务「${t.query}」？

任务及全部草稿将移入回收站（72 小时内管理员可恢复）。`)) return;
      try {
        await api.delete('/api/tasks/' + t.id + '?actor=' + encodeURIComponent((getUser() || {}).name || ''));
        if (this.cur && this.cur.id === t.id) { this.cur = null; this.detail = null; this.form = null; }
        this.load();
      } catch (e) { alert('删除失败：' + e.message); }
    },
    async redraft() {
      // AI 起草失败（截断/格式异常）或草稿不满意时，重新跑 text_check 起草（后台执行）
      if (this.redrafting || !this.cur) return;
      if (!confirm('重新起草？将覆盖当前自动草稿（你未保存的人工修改会丢失），约需 1-5 分钟。')) return;
      this.redrafting = true;
      try {
        await api.post(`/api/tasks/${this.cur.id}/text/redraft?actor=`
          + encodeURIComponent((getUser() || {}).name || ''));
        this.busyNote = '已提交重新起草（约 1-5 分钟）——完成后此页自动刷新';
        this.watchDraft();
      } catch (e) { alert('重新起草提交失败：' + e.message); }
      finally { this.redrafting = false; }
    },
    // ── 驳回标记：单条内容标记 + 修改意见 → 按标记重写 ──
    toggleMark(target) {
      if (this.marks[target]) delete this.marks[target];
      else this.marks = { ...this.marks, [target]: { target, note: '' } };
    },
    // 后台改写/起草完成检测：review.feedback / review.redrafting 消失即完成
    watchDraft() {
      this.stopWatchDraft();
      this.draftTimer = setInterval(async () => {
        if (!this.cur) return this.stopWatchDraft();
        try {
          const d = await api.get(`/api/tasks/${this.cur.id}/detail`);
          const rv = (d.task && d.task.text_review) || {};
          if (!rv.feedback && !rv.redrafting) {
            this.stopWatchDraft();
            this.busyNote = '';
            await this.pick(this.cur);
            this.load();
          }
        } catch (e) { /* 网络抖动继续轮询 */ }
      }, 6000);
      setTimeout(() => this.stopWatchDraft(), 6 * 60 * 1000);  // 6 分钟兜底
    },
    stopWatchDraft() {
      clearInterval(this.draftTimer); this.draftTimer = null;
    },
    async reject() {
      if (this.rejecting || !this.cur) return;
      if (!this.marksList.length) { alert('请先点「📌标记」要驳回的条目并填写修改意见'); return; }
      if (!confirm('按 ' + this.marksList.length + ' 条标记驳回重写？\nAI 只修改被标记的条目，其余内容原样保留（约 1-5 分钟）。')) return;
      this.rejecting = true;
      try {
        await api.post(`/api/tasks/${this.cur.id}/text/reject`, {
          marks: this.marksList, actor: (getUser() || {}).name });
        this.marks = {};
        this.busyNote = '已提交按标记重写（约 1-5 分钟）——完成后此页自动刷新，期间可核查其他任务';
        this.watchDraft();
      } catch (e) { alert('驳回重写提交失败：' + e.message); }
      finally { this.rejecting = false; }
    },
    async confirm() {
      if (this.confirming || !this.detail) return;
      if (!confirm('确认放行该任务进入生产？（将使用你核定的 query / 文案 / 生图描述）')) return;
      this.confirming = true;
      try {
        await api.post(`/api/tasks/${this.cur.id}/text/confirm`, {
          query: this.form.query,
          body: this.form.body,
          pages: this.form.pages,
          image_prompts: this.form.image_prompts,
          actor: (getUser() || {}).name,
        });
        this.cur = null; this.detail = null; this.form = null;
        this.load();
      } catch (e) { alert('确认失败：' + e.message); }
      finally { this.confirming = false; }
    },
    toggleSelectAll() {
      const all = !this.allSelected;
      const sel = { ...this.selected };
      for (const t of this.sortedItems) sel[t.id] = all;
      this.selected = sel;
    },
    async batchConfirm() {
      const ids = this.selectedIds;
      if (!ids.length) { alert('请先选择任务'); return; }
      if (!confirm(`确定批量放行 ${ids.length} 条任务进入生产？`)) return;
      this.confirming = true;
      try {
        const r = await api.post('/api/tasks/text/batch_confirm', { ids, actor: (getUser() || {}).name });
        alert(`批量放行 ${r.confirmed} 条（跳过 ${r.skipped.length}）`);
        this.selected = {}; this.showBatchMenu = false;
        this.cur = null; this.detail = null; this.form = null;
        await this.load();
      } catch (e) { alert('批量放行失败：' + e.message); }
      finally { this.confirming = false; }
    },
    async batchDelete() {
      const ids = this.selectedIds;
      if (!ids.length) { alert('请先选择任务'); return; }
      if (!confirm(`确定删除选中的 ${ids.length} 条任务？\n\n任务及全部草稿将移入回收站（72 小时内管理员可恢复）。`)) return;
      this.confirming = true;
      try {
        const r = await api.post('/api/tasks/batch_delete', { ids, actor: (getUser() || {}).name });
        alert(`已删除 ${r.deleted} 条（跳过 ${r.skipped.length}）`);
        this.selected = {}; this.showBatchMenu = false;
        if (this.cur && ids.includes(this.cur.id)) { this.cur = null; this.detail = null; this.form = null; }
        await this.load();
      } catch (e) { alert('批量删除失败：' + e.message); }
      finally { this.confirming = false; }
    },
  },
  async mounted() {
    this.load();
    this.timer = setInterval(this.load, 8000);
  },
  beforeUnmount() { clearInterval(this.timer); this.stopWatchDraft(); },
  template: `
  <app-layout title="文字核查 · 人工最终审核">
    <div class="refs-layout">
      <div class="card refs-list">
        <h2>待核查任务 <span class="tag tag-yellow">{{ awaitingCount }}</span></h2>
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
          <span style="font-size:14px;font-weight:600">待核查队列</span>
          <div style="display:flex;align-items:center;gap:8px;margin-left:auto">
            <select v-model="sortOrder" style="width:auto;padding:4px 8px;font-size:13px">
              <option value="desc">最新在前</option>
              <option value="asc">最早在前</option>
            </select>
            <button class="btn btn-outline btn-sm" @click="load">刷新</button>
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
              <button class="btn btn-success btn-sm" style="white-space:nowrap" :disabled="confirming || !selectedIds.length" @click="batchConfirm">✓ 通过选中（{{ selectedIds.length }}）</button>
              <button class="btn btn-danger btn-sm" style="white-space:nowrap" :disabled="confirming || !selectedIds.length" @click="batchDelete">✗ 删除选中（{{ selectedIds.length }}）</button>
            </div>
          </div>
        </div>
        <div v-if="!sortedItems.length" class="empty" style="padding:18px 0">
          暂无待核查任务——导入的任务经「文字自查」后在这里等你最终审核
        </div>
        <div v-for="t in sortedItems" :key="t.id" class="queue-item" :class="{on: cur && cur.id === t.id}"
             @click="pick(t)">
          <div style="display:flex;align-items:flex-start;gap:8px">
            <input type="checkbox" v-model="selected[t.id]" @click.stop style="margin-top:4px;flex-shrink:0;width:auto">
            <div style="flex:1;min-width:0">
              <div class="q">{{ t.query }}</div>
              <div>
                <span v-if="t.source === 'manual'" class="tag tag-blue" title="手工内容导入">✍️ 手工</span>
                <span class="tag" :class="t.auto_ok ? 'tag-green' : 'tag-yellow'">
                  {{ t.auto_ok ? '自查通过' : (t.issues || []).length + ' 个问题' }}
                </span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <div class="card refs-main" style="flex:1" v-if="detail && form">
        <h2>{{ detail.task.query }}
          <span class="tag" :class="autoOk ? 'tag-green' : 'tag-yellow'">
            {{ autoOk ? '✓ 中文自查通过' : '⚠ 自查发现 ' + issues.length + ' 个问题' }}
          </span>
          <span v-if="isManual" class="tag tag-blue" title="正文基于你手写的内容改写优化">✍️ 手工稿 · AI 改写</span>
        </h2>

        <details v-if="isManual && userBody" style="margin:6px 0 10px">
          <summary class="muted" style="cursor:pointer;font-size:13px">查看我的手写原稿（改写底稿）</summary>
          <div class="alert-warn" style="white-space:pre-wrap;margin-top:6px;font-size:13px">{{ userBody }}</div>
        </details>

        <div v-if="busyNote" class="alert-warn" style="background:#f2f7ff;border-color:#9ec1f5">
          ⏳ {{ busyNote }}
        </div>

        <div v-if="draftError" class="alert-warn" style="border-color:#e84545">
          <b>⚠ AI 起草失败</b>——模型输出被截断或格式异常，下方草稿为空。
          请点「🔄 重新起草」重试（已提升输出上限，通常可恢复），或直接删除该任务重新导入。
        </div>

        <div v-if="issues.length || bodyIssues.length" class="alert-warn">
          <b>自查问题：</b>
          <ul class="plain-list" style="margin:4px 0 0">
            <li v-for="(i, idx) in issues" :key="'q'+idx">· {{ i }}</li>
            <li v-for="(i, idx) in bodyIssues" :key="'b'+idx">· 正文：{{ i }}</li>
          </ul>
          <p v-if="review.query_clean && review.query_clean.suggested" class="muted" style="margin-top:6px">
            建议修正：{{ review.query_clean.suggested }}
          </p>
        </div>

        <div v-if="lastFeedback.length" class="alert-warn" style="background:#f2f7ff;border-color:#9ec1f5">
          <b>上一轮驳回重写已处理（{{ lastFeedback.length }} 条标记）：</b>
          <ul class="plain-list" style="margin:4px 0 0">
            <li v-for="(f, i) in lastFeedback" :key="'lf'+i">· {{ targetLabel(f.target) }}：{{ f.note }}</li>
          </ul>
        </div>

        <h3 class="tc-h3">① Query（最终生效）
          <button class="btn btn-sm" :class="marks['query'] ? 'btn-danger' : 'btn-outline'"
                  @click="toggleMark('query')">📌{{ marks['query'] ? '已标记' : '标记' }}</button></h3>
        <div :class="{ 'tc-marked': marks['query'] }">
          <div v-if="queryRelevance && queryRelevance.level === 'warn'"
               class="tc-field" style="padding:6px 10px;margin-bottom:6px;border:1px solid #e6a23c;background:#fdf6ec;color:#b88230;font-size:12.5px">
            ⚠ 标题-Query 相关性预检：{{ queryRelevance.reason || '内容与 Query 匹配度存疑' }}（供参考，可标记驳回）
          </div>
          <textarea v-model="form.query" rows="2" class="tc-field" @input="fitField"></textarea>
          <textarea v-if="marks['query']" v-model="marks['query'].note" rows="2"
                    class="tc-note-field" placeholder="修改意见：这条 Query 要怎么改（必填，驳回重写时 AI 按此执行）"></textarea>
        </div>

        <h3 class="tc-h3">② 正文（{{ bodyChars }} 字，最终生效——生图不再重写）
          <button class="btn btn-sm btn-outline" @click="bodyPreview = !bodyPreview">{{ bodyPreview ? '✏️ 编辑' : '📖 预览' }}</button>
          <button class="btn btn-sm" :class="marks['body'] ? 'btn-danger' : 'btn-outline'"
                  @click="toggleMark('body')">📌{{ marks['body'] ? '已标记' : '标记' }}</button></h3>
        <div :class="{ 'tc-marked': marks['body'] }">
          <div v-if="bodyPreview" class="article-body md tc-field" style="min-height:280px" v-html="renderMd(form.body)"></div>
          <textarea v-else v-model="form.body" rows="12" class="tc-field" @input="fitField"></textarea>
          <textarea v-if="marks['body']" v-model="marks['body'].note" rows="2"
                    class="tc-note-field" placeholder="修改意见：正文哪里要改（必填）"></textarea>
        </div>

        <h3 class="tc-h3">③ 图上文案（最终生效）<span class="muted" style="font-weight:normal;font-size:12.5px">点每页 📌 可单独标记驳回</span></h3>
        <div class="tc-grid">
          <div v-for="(_, i) in pageCount" :key="i" :class="{ 'tc-marked': marks['page:' + (i + 1)] }">
            <label class="muted tc-h3" style="display:flex;justify-content:space-between;align-items:center">P{{ i + 1 }}{{ i === 0 ? ' 封面' : (i === 5 ? ' 结尾' : ' 要点') }}
              <button class="btn btn-sm" :class="marks['page:' + (i + 1)] ? 'btn-danger' : 'btn-outline'"
                      @click="toggleMark('page:' + (i + 1))">📌</button></label>
            <textarea v-model="form.pages[i]" rows="2" class="tc-field" @input="fitField"></textarea>
            <textarea v-if="marks['page:' + (i + 1)]" v-model="marks['page:' + (i + 1)].note" rows="2"
                      class="tc-note-field" placeholder="这页文案要怎么改（必填）"></textarea>
          </div>
        </div>

        <h3 class="tc-h3">④ 生图描述（最终生效）<span class="muted" style="font-weight:normal;font-size:12.5px">点每页 📌 可单独标记驳回</span></h3>
        <div class="tc-grid">
          <div v-for="(_, i) in pageCount" :key="'ip' + i" :class="{ 'tc-marked': marks['ip:' + (i + 1)] }">
            <label class="muted tc-h3" style="display:flex;justify-content:space-between;align-items:center">P{{ i + 1 }} 生图描述
              <button class="btn btn-sm" :class="marks['ip:' + (i + 1)] ? 'btn-danger' : 'btn-outline'"
                      @click="toggleMark('ip:' + (i + 1))">📌</button></label>
            <textarea v-model="form.image_prompts[i]" rows="2" class="tc-field" @input="fitField"></textarea>
            <textarea v-if="marks['ip:' + (i + 1)]" v-model="marks['ip:' + (i + 1)].note" rows="2"
                      class="tc-note-field" placeholder="这条生图描述要怎么改（必填）"></textarea>
          </div>
        </div>

        <div style="margin-top:14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">
          <button class="btn btn-primary" :disabled="confirming"
                  @click="confirm">{{ confirming ? '放行中…' : '✓ 最终核查通过，进入生产' }}</button>
          <button class="btn btn-danger-ghost" :disabled="rejecting || !marksList.length"
                  @click="reject">{{ rejecting ? '提交中…' : '⤺ 驳回重写（' + marksList.length + ' 条标记）' }}</button>
          <button class="btn btn-outline" :disabled="redrafting"
                  @click="redraft">{{ redrafting ? '提交中…' : '🔄 重新起草' }}</button>
          <span class="muted" style="font-size:13px">放行后进入「审图」环节（compare/single）或直接生产（general）</span>
        </div>
      </div>
      <div class="card refs-main" style="flex:1" v-else>
        <div class="empty" style="padding:60px 0">
          {{ items.length ? '← 点左侧任务开始核查' : '等待任务进入文字核查…' }}
        </div>
      </div>
    </div>
  </app-layout>`,
};
