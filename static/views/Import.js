// 任务导入：逐行文本 / CSV 文件
const ImportView = {
  data() {
    return { tab: 'text', mode: 'general', text: '', file: null, result: '', error: '', errors: [], loading: false };
  },
  computed: {
    queries() { return this.text.split('\n').map(s => s.trim()).filter(Boolean); },
  },
  methods: {
    async submitText() {
      this.error = ''; this.result = '';
      if (!this.queries.length) { this.error = '请至少输入一条 Query'; return; }
      this.loading = true;
      try {
        const r = await api.post('/api/tasks/import_queries', { queries: this.queries, mode: this.mode, actor: (getUser() || {}).name });
        this.result = `成功导入 ${r.imported} 条，已加入生产队列`;
        this.text = '';
      } catch (e) { this.error = e.message; }
      finally { this.loading = false; }
    },
    async submitCsv() {
      this.error = ''; this.result = '';
      if (!this.file) { this.error = '请选择 CSV 文件'; return; }
      this.loading = true;
      try {
        const fd = new FormData();
        fd.append('file', this.file);
        fd.append('actor', (getUser() || {}).name || 'anonymous');
        const r = await api.postForm('/api/tasks/import', fd);
        this.result = `成功导入 ${r.imported} 条` + (r.errors && r.errors.length ? `，${r.errors.length} 行失败` : '');
        this.errors = (r.errors || []).slice(0, 5).map(e => `${(e.row && e.row.query) || '(空行)'}：${e.error}`);
        this.file = null; this.$refs.fileInput.value = '';
      } catch (e) { this.error = e.message; }
      finally { this.loading = false; }
    },
  },
  template: `
  <app-layout title="任务导入">
    <div class="card">
      <div class="tabs">
        <button class="tab" :class="{on: tab==='text'}" @click="tab='text'">逐行文本</button>
        <button class="tab" :class="{on: tab==='csv'}" @click="tab='csv'">CSV 文件</button>
      </div>

      <template v-if="tab==='text'">
        <label>生产模式</label>
        <div class="mode-row">
          <label v-for="(m, k) in MODE" :key="k" class="mode-card" :class="{on: mode===k}">
            <input type="radio" v-model="mode" :value="k">
            <b>{{ m.label }}</b>
            <span class="muted">{{ m.desc }}</span>
          </label>
        </div>
        <label style="margin-top:14px">Query（每行一条）</label>
        <textarea v-model="text" rows="8" placeholder="小米17 Pro 和 荣耀600 Pro 怎么选？&#10;曲靖高三补习怎么选"></textarea>
        <p class="muted" style="margin:8px 0">共 {{ queries.length }} 条</p>
        <button class="btn btn-primary" :disabled="loading" @click="submitText">{{ loading ? '导入中…' : '导入并启动生产' }}</button>
      </template>

      <template v-else>
        <label>CSV 文件</label>
        <p class="muted" style="margin-bottom:10px">列：<code>query</code>（必填）、<code>mode</code>（可选，general / single / compare，默认 general）、<code>content_type</code> / <code>platform</code>（可选）</p>
        <input ref="fileInput" type="file" accept=".csv" @change="file = $event.target.files[0]">
        <div style="margin-top:14px">
          <button class="btn btn-primary" :disabled="loading" @click="submitCsv">{{ loading ? '导入中…' : '上传并导入' }}</button>
        </div>
      </template>

      <p v-if="error" class="form-error">{{ error }}</p>
      <p v-if="result" class="form-ok">{{ result }}　<router-link to="/tasks">前往任务中心 →</router-link></p>
      <ul v-if="errors.length" class="plain-list form-error">
        <li v-for="(e, i) in errors" :key="i">{{ e }}</li>
      </ul>
    </div>
  </app-layout>`,
  created() { this.MODE = MODE; },
};
