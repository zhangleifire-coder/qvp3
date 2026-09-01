// 审图：实景参考图人工筛选工作台（独立板块，2026-08-27）
// 流程：任务到 awaiting_refs → 此处按任务看候选（默认 OCR 命中优先）→
// 勾选保留 / 全不合适「驳回重搜」（可补关键词）→ 确认后自动入队继续生产。
const RefsBoardView = {
  data() {
    return {
      items: [], total: 0, loading: false, error: '',
      cur: null, detail: null, keep: {}, confirming: false,
      researchQ: '', researching: false, timer: null,
      uploading: false,   // 手工上传中
      zoom: null,         // 全屏查看器（放大细察候选图）
    };
  },
  computed: {
    candidates() {
      return ((this.detail && this.detail.assets) || [])
        .filter(a => a.source_type === 'official' && a.selection_status === 'candidate');
    },
    keepCount() { return Object.keys(this.keep).filter(k => this.keep[k]).length; },
    awaitingCount() { return this.total; },
  },
  methods: {
    // 全屏查看候选图：放大细察细节（缩放锚定光标位置），←→/按钮切换上一张下一张
    openZoom(i) {
      const list = this.candidates.map(a => ({
        src: a.display_url || a.image_url,
        title: `候选 ${a.page_index || '·'}`
               + (a.model_version === 'manual' ? ' · 手工上传' : ' · 搜索'),
        text: a.ocr_hit ? `OCR命中：${a.ocr_hit}` : '',
      }));
      if (!list.length) return;
      this.zoom = { list, index: Math.min(Math.max(0, i), list.length - 1) };
    },
    async load() {
      this.loading = true; this.error = '';
      try {
        const r = await api.get('/api/tasks/refs/awaiting');
        this.items = r.items || []; this.total = r.total || 0;
        if (this.cur) {
          const still = this.items.find(i => i.id === this.cur.id);
          if (!still) { this.cur = null; this.detail = null; }
        }
      } catch (e) { this.error = e.message; }
      finally { this.loading = false; }
    },
    async pick(t) {
      this.cur = t; this.detail = null; this.researchQ = '';
      try {
        this.detail = await api.get(`/api/tasks/${t.id}/detail`);
        this.keep = {};
        this.candidates.forEach(a => { this.keep[a.id] = true; });  // 默认全选
      } catch (e) { this.error = e.message; }
    },
    async removeTask(t) {
      if (!confirm(`确定删除任务「${t.query}」？

任务及已搜集的候选图将移入回收站（72 小时内管理员可恢复）。`)) return;
      try {
        await api.delete('/api/tasks/' + t.id + '?actor=' + encodeURIComponent((getUser() || {}).name || ''));
        if (this.cur && this.cur.id === t.id) { this.cur = null; this.detail = null; }
        this.load();
      } catch (e) { alert('删除失败：' + e.message); }
    },
    async confirm() {
      if (this.confirming || !this.detail) return;
      const keep = Object.keys(this.keep).filter(k => this.keep[k]);
      if (!keep.length) { alert('至少保留一张参考图（全不要请用「驳回重搜」）'); return; }
      if (!confirm(`确认保留 ${keep.length} 张并继续生产？未勾选的候选将被剔除。`)) return;
      this.confirming = true;
      try {
        await api.post(`/api/tasks/${this.cur.id}/refs/confirm`,
          { keep_ids: keep, actor: (getUser() || {}).name });
        this.cur = null; this.detail = null;
        this.load();
      } catch (e) { alert('确认失败：' + e.message); }
      finally { this.confirming = false; }
    },
    async research() {
      if (this.researching || !this.cur) return;
      if (!confirm(`重新搜集实景图？现有候选将被清空，按${this.researchQ.trim() ? '补充关键词' : '原 query 换序'}重搜。`)) return;
      this.researching = true;
      try {
        const r = await api.post(`/api/tasks/${this.cur.id}/refs/research`,
          { extra_query: this.researchQ.trim(), actor: (getUser() || {}).name });
        this.detail = await api.get(`/api/tasks/${this.cur.id}/detail`);
        this.keep = {};
        this.candidates.forEach(a => { this.keep[a.id] = true; });
      } catch (e) { alert('重搜失败：' + e.message); }
      finally { this.researching = false; }
    },
    async upload(e) {
      // 手工上传自定义实景图：与搜索候选并列（默认勾选）
      const files = e && e.target ? e.target.files : null;
      if (!files || !files.length || this.uploading || !this.cur) return;
      this.uploading = true;
      try {
        const fd = new FormData();
        for (const f of files) fd.append('files', f);
        fd.append('actor', (getUser() || {}).name || '');
        const r = await api.postForm(`/api/tasks/${this.cur.id}/refs/upload`, fd);
        this.detail = await api.get(`/api/tasks/${this.cur.id}/detail`);
        this.keep = {};
        this.candidates.forEach(a => { this.keep[a.id] = true; });  // 默认全选（含新上传）
        this.load();
        alert(`已上传 ${r.uploaded} 张（当前候选共 ${r.candidates} 张）`);
      } catch (err) { alert('上传失败：' + err.message); }
      finally {
        this.uploading = false;
        if (e && e.target) e.target.value = '';   // 允许重复选择同一文件
      }
    },
    fmtSize(b) { return b >= 1048576 ? (b / 1048576).toFixed(1) + 'MB' : Math.round(b / 1024) + 'KB'; },
  },
  async mounted() {
    this.load();
    this.timer = setInterval(this.load, 8000);
  },
  beforeUnmount() { clearInterval(this.timer); },
  template: `
  <app-layout title="审图 · 实景参考图筛选">
    <div class="refs-layout">
      <div class="card refs-list">
        <h2>待确认任务 <span class="tag tag-yellow">{{ awaitingCount }}</span></h2>
        <div v-if="!items.length" class="empty" style="padding:18px 0">
          暂无待确认任务——compare/single 任务搜集完参考图后会在这里等你筛选
        </div>
        <div v-for="t in items" :key="t.id" class="refs-item" :class="{on: cur && cur.id === t.id}"
             @click="pick(t)">
          <b>{{ t.query }}</b>
          <span style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">
            <span class="tag tag-blue">{{ t.mode === 'compare' ? '对比' : '单品' }}</span>
            <span class="muted" style="font-size:12px">候选 {{ t.candidates }} 张</span>
            <button class="btn btn-sm btn-danger-ghost" title="删除该任务（入回收站，72h 可恢复）"
                    @click.stop="removeTask(t)">🗑</button>
          </span>
        </div>
      </div>

      <div class="card" style="flex:1">
        <template v-if="detail">
          <h2>{{ detail.task.query }}
            <span class="tag tag-yellow">待确认</span>
          </h2>
          <p class="muted" style="font-size:13px;margin:4px 0 10px">
            共 {{ candidates.length }} 张候选（系统按 OCR 命中排序；勾选保留，全不合适可驳回重搜）
          </p>
          <div class="research-bar">
            <input v-model="researchQ" placeholder="补充搜索关键词（可选，如：戴森 V12 实拍）" style="flex:1">
            <button class="btn btn-outline btn-sm" :disabled="researching"
                    @click="research">{{ researching ? '重搜中…' : '↻ 驳回重搜（再搜一批）' }}</button>
            <label class="btn btn-outline btn-sm" style="cursor:pointer"
                   :title="'上传自定义实景图（与搜索候选并列，默认勾选）'">
              {{ uploading ? '上传中…' : '⬆ 上传图片' }}
              <input type="file" accept="image/*" multiple style="display:none"
                     :disabled="uploading" @change="upload">
            </label>
          </div>
          <div class="img-grid ref-grid">
            <figure v-for="(a, ai) in candidates" :key="a.id" :class="{unchecked: !keep[a.id]}">
              <img :src="a.display_url || a.image_url" loading="lazy" alt=""
                   style="cursor:zoom-in" title="点击放大查看细节"
                   @click="openZoom(ai)">
              <label class="ref-check">
                <input type="checkbox" v-model="keep[a.id]" style="width:auto">
                <span v-if="a.model_version === 'manual'" class="tag tag-blue" style="font-size:11px">手工上传</span>
                <span v-else-if="a.model_version === 'library'" class="tag tag-gray" style="font-size:11px">素材库复用</span>
                <span v-else-if="a.ocr_hit" class="tag tag-green" style="font-size:11px">OCR命中: {{ a.ocr_hit.slice(0, 12) }}</span>
                <span v-else class="tag tag-gray" style="font-size:11px">无命中</span>
              </label>
            </figure>
          </div>
          <div style="margin-top:12px;display:flex;gap:10px;align-items:center">
            <button class="btn btn-primary" :disabled="confirming"
                    @click="confirm">{{ confirming ? '确认中…' : '✓ 确认保留 ' + keepCount + ' 张并继续生产' }}</button>
            <span class="muted" style="font-size:13px">确认后任务自动入队，Agent 用这批图生图</span>
          </div>
        </template>
        <div v-else class="empty" style="padding:60px 0">
          {{ items.length ? '← 点左侧任务开始审图' : '等待任务进入审图环节…' }}
        </div>
      </div>
    </div>
    <img-lightbox :img="zoom" @close="zoom=null" />
  </app-layout>`,
};
