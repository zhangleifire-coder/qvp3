// 文字核查：query 中文自查 + 文案/生图描述人工最终核查（独立板块，2026-08-27）
// 流程：导入任务 → text_check 自动自查起草 → 此处人工核查/修正 → 放行进入生产。
const TextCheckView = {
  data() {
    return {
      items: [], total: 0, loading: false, error: '',
      cur: null, detail: null, form: null, confirming: false, timer: null,
      redrafting: false,   // 重新起草中防抖
    };
  },
  computed: {
    awaitingCount() { return this.total; },
    review() { return (this.detail && this.detail.task.text_review) || {}; },
    issues() { return (this.review.query_clean || {}).issues || []; },
    bodyIssues() { return this.review.body_issues || []; },
    autoOk() { return this.review.auto_ok; },
    draftError() { return !!this.review.draft_error; },
    isManual() { return this.review.source === 'manual'; },
    userBody() { return this.review.user_body || ''; },
    bodyChars() { return (this.form && this.form.body || '').replace(/\s/g, '').length; },
  },
  methods: {
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
      } catch (e) { this.error = e.message; }
      finally { this.loading = false; }
    },
    async pick(t) {
      this.cur = t; this.detail = null;
      try {
        this.detail = await api.get(`/api/tasks/${t.id}/detail`);
        const rv = this.review;
        // 表单初始值：人工修改版 > 自动草稿
        const ov = this.detail.task.text_override || {};
        this.form = {
          query: ov.query || (rv.query_clean && rv.query_clean.suggested && this.issues.length
                              ? rv.query_clean.suggested : (rv.query || t.query)),
          body: ov.body || rv.body_draft || '',
          pages: ov.pages || (rv.pages_draft || []).slice(0, 6),
          image_prompts: ov.image_prompts || (rv.image_prompt_draft || []).slice(0, 6),
        };
      } catch (e) { this.error = e.message; }
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
      // AI 起草失败（截断/格式异常）或草稿不满意时，重新跑 text_check 起草
      if (this.redrafting || !this.cur) return;
      if (!confirm('重新起草？将覆盖当前自动草稿（你未保存的人工修改会丢失），约需 30-90 秒。')) return;
      this.redrafting = true;
      try {
        await api.post(`/api/tasks/${this.cur.id}/text/redraft?actor=`
          + encodeURIComponent((getUser() || {}).name || ''));
        await this.pick(this.cur);   // 重载详情与表单
        this.load();
      } catch (e) { alert('重新起草失败：' + e.message); }
      finally { this.redrafting = false; }
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
  },
  async mounted() {
    this.load();
    this.timer = setInterval(this.load, 8000);
  },
  beforeUnmount() { clearInterval(this.timer); },
  template: `
  <app-layout title="文字核查 · 人工最终审核">
    <div class="refs-layout">
      <div class="card refs-list">
        <h2>待核查任务 <span class="tag tag-yellow">{{ awaitingCount }}</span></h2>
        <div v-if="!items.length" class="empty" style="padding:18px 0">
          暂无待核查任务——导入的任务经「文字自查」后在这里等你最终审核
        </div>
        <div v-for="t in items" :key="t.id" class="refs-item" :class="{on: cur && cur.id === t.id}"
             @click="pick(t)">
          <b>{{ t.query }}</b>
          <span style="display:flex;align-items:center;gap:6px">
            <span v-if="t.source === 'manual'" class="tag tag-blue" title="手工内容导入">✍️ 手工</span>
            <span class="tag" :class="t.auto_ok ? 'tag-green' : 'tag-yellow'">
              {{ t.auto_ok ? '自查通过' : (t.issues || []).length + ' 个问题' }}
            </span>
            <button class="btn btn-sm btn-danger-ghost" title="删除该任务（入回收站，72h 可恢复）"
                    @click.stop="removeTask(t)">🗑</button>
          </span>
        </div>
      </div>

      <div class="card" style="flex:1" v-if="detail && form">
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

        <h3>① Query（最终生效）</h3>
        <textarea v-model="form.query" rows="2" class="tc-field" @input="fitField"></textarea>

        <h3>② 正文（{{ bodyChars }} 字，最终生效——生图不再重写）</h3>
        <textarea v-model="form.body" rows="12" class="tc-field" @input="fitField"></textarea>

        <h3>③ 图上文案（6 页，最终生效）</h3>
        <div class="tc-grid">
          <div v-for="(_, i) in 6" :key="i">
            <label class="muted">P{{ i + 1 }}{{ i === 0 ? ' 封面' : (i === 5 ? ' 结尾' : ' 要点') }}</label>
            <textarea v-model="form.pages[i]" rows="2" class="tc-field" @input="fitField"></textarea>
          </div>
        </div>

        <h3>④ 生图描述（6 页，最终生效）</h3>
        <div class="tc-grid">
          <div v-for="(_, i) in 6" :key="'ip' + i">
            <label class="muted">P{{ i + 1 }} 生图描述</label>
            <textarea v-model="form.image_prompts[i]" rows="2" class="tc-field" @input="fitField"></textarea>
          </div>
        </div>

        <div style="margin-top:14px;display:flex;gap:10px;align-items:center">
          <button class="btn btn-primary" :disabled="confirming"
                  @click="confirm">{{ confirming ? '放行中…' : '✓ 最终核查通过，进入生产' }}</button>
          <button class="btn btn-outline" :disabled="redrafting"
                  @click="redraft">{{ redrafting ? '起草中…（约 30-90 秒）' : '🔄 重新起草' }}</button>
          <span class="muted" style="font-size:13px">放行后进入「审图」环节（compare/single）或直接生产（general）</span>
        </div>
      </div>
      <div class="card" style="flex:1" v-else>
        <div class="empty" style="padding:60px 0">
          {{ items.length ? '← 点左侧任务开始核查' : '等待任务进入文字核查…' }}
        </div>
      </div>
    </div>
  </app-layout>`,
};
