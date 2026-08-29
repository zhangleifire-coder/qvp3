// 任务导入：逐行文本 / CSV 文件 / 组合生成（query × 泛化问题池 × 风格垂类）
const COMBO_STYLES = ['解读·经验分享', '测评实测', '攻略教程', '避坑指南', '观点杂谈'];
const COMBO_CATEGORIES = ['家居', '汽车', '数码', '教育', '美食', '健康', '母婴', '职场'];

function newComboRow() {
  return { query: '', pool: '', style: '解读·经验分享', category: '', rounds: 3, analyzing: false };
}

const ImportView = {
  data() {
    return {
      tab: 'text', mode: 'general', text: '', file: null, result: '', error: '', errors: [], loading: false,
      // 手工内容导入（query + 手写正文 → AI 改写优化）
      mQuery: '', mBody: '', mMode: 'general', mLoading: false, mResult: '',
      // 组合生成
      comboMode: 'general', comboRows: [newComboRow()], comboResult: '', comboItems: [], comboSkipped: [], comboLoading: false,
    };
  },
  computed: {
    queries() { return this.text.split('\n').map(s => s.trim()).filter(Boolean); },
    mBodyChars() { return (this.mBody || '').replace(/\s/g, '').length; },
  },
  methods: {
    poolCount(r) {
      return r.pool.split(/[\n，,；;、]+/).map(s => s.trim()).filter(Boolean).length;
    },
    async analyze(r) {
      this.error = ''; this.result = '';
      const q = r.query.trim();
      if (q.length < 4) { this.error = '请先填写原始 query（至少 4 个字），再智能分析'; return; }
      r.analyzing = true;
      try {
        const res = await api.post('/api/tasks/analyze_query', { query: q, count: 20 });
        const existing = r.pool.split(/[\n，,；;、]+/).map(s => s.trim()).filter(Boolean);
        r.pool = Array.from(new Set([...existing, ...res.questions])).join('\n');
      } catch (e) { this.error = e.message; }
      finally { r.analyzing = false; }
    },
    async submitCombo() {
      this.error = ''; this.comboResult = ''; this.comboItems = []; this.comboSkipped = [];
      const rows = this.comboRows
        .map(r => ({ query: r.query.trim(), supplement_pool: r.pool.trim(), gen_style: r.style, gen_category: r.category.trim(), rounds: Number(r.rounds) || 1 }))
        .filter(r => r.query || r.supplement_pool);
      if (!rows.length) { this.error = '请至少填写一行（原始 query + 泛化问题池）'; return; }
      for (const r of rows) {
        if (!r.query) { this.error = '有一行的原始 query 为空'; return; }
        if (!r.supplement_pool) { this.error = '有一行的泛化问题池为空（可点「智能分析补充问题」自动生成后手工增删）'; return; }
      }
      this.comboLoading = true;
      try {
        const res = await api.post('/api/tasks/import_combo',
          { rows, mode: this.comboMode, actor: (getUser() || {}).name });
        this.comboResult = `成功创建 ${res.imported} 条组合任务，已加入生产队列` +
          (res.skipped && res.skipped.length ? `，${res.skipped.length} 个组合跳过` : '');
        this.comboItems = res.items || [];
        this.comboSkipped = (res.skipped || []).slice(0, 5);
        this.comboRows = [newComboRow()];
        window.dispatchEvent(new CustomEvent('qvp:imported'));
      } catch (e) { this.error = e.message; }
      finally { this.comboLoading = false; }
    },
    async submitManual() {
      this.error = ''; this.mResult = '';
      const q = this.mQuery.trim();
      if (q.length < 4) { this.error = '请填写 Query 标题（至少 4 个字）'; return; }
      if (this.mBodyChars < 200) { this.error = `正文目前 ${this.mBodyChars} 字，至少 200 字（建议 500-700 字）`; return; }
      this.mLoading = true;
      try {
        const r = await api.post('/api/tasks/import_manual', {
          query: q, body: this.mBody, mode: this.mMode, actor: (getUser() || {}).name });
        if (r.imported) {
          this.mResult = `已导入（${r.body_chars} 字）。AI 正在改写优化你的正文（保留事实）→ 完成后到「文字核查」确认放行`;
          this.mQuery = ''; this.mBody = '';
          window.dispatchEvent(new CustomEvent('qvp:imported'));
        } else {
          this.mResult = r.detail || '相同内容已导入过';
        }
      } catch (e) { this.error = e.message; }
      finally { this.mLoading = false; }
    },
    async submitText() {
      this.error = ''; this.result = '';
      if (!this.queries.length) { this.error = '请至少输入一条 Query'; return; }
      this.loading = true;
      try {
        const r = await api.post('/api/tasks/import_queries', { queries: this.queries, mode: this.mode, actor: (getUser() || {}).name });
        this.result = `成功导入 ${r.imported} 条，已加入生产队列`;
        this.text = '';
        window.dispatchEvent(new CustomEvent('qvp:imported'));
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
        window.dispatchEvent(new CustomEvent('qvp:imported'));
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
        <button class="tab" :class="{on: tab==='combo'}" @click="tab='combo'">组合生成</button>
        <button class="tab" :class="{on: tab==='manual'}" @click="tab='manual'">✍️ 手工内容</button>
      </div>

      <template v-if="tab==='manual'">
        <label>Query 标题</label>
        <input v-model="mQuery" placeholder="例：城市共享单车使用指南" style="width:100%">
        <label style="margin-top:14px">你的正文（AI 将在保留事实的基础上改写优化，不重写）</label>
        <textarea v-model="mBody" rows="16" placeholder="粘贴你自己写的 500-700 字正文…"
                  style="width:100%"></textarea>
        <p class="muted" style="margin:8px 0">
          {{ mBodyChars }} 字<span v-if="mBodyChars && (mBodyChars < 500 || mBodyChars > 700)"
            style="color:#c80">（建议 500-700 字）</span>
        </p>
        <label>生产模式</label>
        <div class="mode-row">
          <label v-for="(m, k) in MODE" :key="k" class="mode-card" :class="{on: mMode===k}">
            <input type="radio" v-model="mMode" :value="k">
            <b>{{ m.label }}</b>
            <span class="muted">{{ m.desc }}</span>
          </label>
        </div>
        <button class="btn btn-primary" style="margin-top:14px" :disabled="mLoading"
                @click="submitManual">{{ mLoading ? '导入中…' : '导入并启动改写优化' }}</button>
        <p v-if="mResult" class="form-ok" style="margin-top:10px">{{ mResult }}</p>
      </template>

      <template v-else-if="tab==='text'">
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

      <template v-else-if="tab==='csv'">
        <label>CSV 文件</label>
        <p class="muted" style="margin-bottom:10px">列：<code>query</code>（必填）、<code>mode</code>（可选，general / single / compare，默认 general）、<code>content_type</code> / <code>platform</code>（可选）</p>
        <input ref="fileInput" type="file" accept=".csv" @change="file = $event.target.files[0]">
        <div style="margin-top:14px">
          <button class="btn btn-primary" :disabled="loading" @click="submitCsv">{{ loading ? '上传并导入' : '上传并导入' }}</button>
        </div>
      </template>

      <template v-else>
        <p class="muted" style="margin:0 0 12px">
          每行 = 原始 query（长情境）＋泛化问题池（可智能分析自动补充，再手工增删）。导入时从池中
          <b>随机抽 N 个问题</b>，各自与原始 query 组合成一条生产任务，并按所选风格/垂类创作。
        </p>
        <label>生产模式</label>
        <div class="mode-row">
          <label v-for="(m, k) in MODE" :key="k" class="mode-card" :class="{on: comboMode===k}">
            <input type="radio" v-model="comboMode" :value="k">
            <b>{{ m.label }}</b>
            <span class="muted">{{ m.desc }}</span>
          </label>
        </div>

        <div v-for="(r, i) in comboRows" :key="i" class="combo-row" style="margin-top:16px">
          <div class="combo-row-head">
            <b>第 {{ i + 1 }} 行</b>
            <button v-if="comboRows.length > 1" class="btn btn-sm btn-danger-ghost" @click="comboRows.splice(i, 1)">✕ 删除行</button>
          </div>
          <label>原始 query（用户提问情境）</label>
          <textarea v-model="r.query" rows="3" placeholder="例：我有一台2017年的吉普指南者，现在空调不凉了，修理厂说要换散热器，一般要多少钱？"></textarea>
          <div style="margin:8px 0">
            <button class="btn" :disabled="r.analyzing" @click="analyze(r)">
              {{ r.analyzing ? '分析中…（约 5-15 秒）' : '🔍 智能分析补充问题' }}
            </button>
            <span v-if="r.pool" class="muted" style="margin-left:10px">问题池：{{ poolCount(r) }} 个问题</span>
          </div>
          <label>泛化问题池（每行一个，也可用逗号/分号分隔；点上方按钮可自动生成）</label>
          <textarea v-model="r.pool" rows="5" placeholder="汽车空调散热器更换多少钱&#10;汽车空调不凉怎么判断缺氟&#10;换散热器需要注意什么坑"></textarea>
          <div class="combo-grid">
            <div>
              <label>风格（补充条件1）</label>
              <input v-model="r.style" list="combo-styles" placeholder="解读·经验分享">
              <datalist id="combo-styles">
                <option v-for="s in COMBO_STYLES" :key="s" :value="s"></option>
              </datalist>
            </div>
            <div>
              <label>垂类（补充条件2）</label>
              <input v-model="r.category" list="combo-cats" placeholder="汽车">
              <datalist id="combo-cats">
                <option v-for="c in COMBO_CATEGORIES" :key="c" :value="c"></option>
              </datalist>
            </div>
            <div>
              <label>生成数据（条）</label>
              <input type="number" v-model.number="r.rounds" min="1" max="50">
            </div>
          </div>
        </div>

        <div style="margin-top:14px">
          <button class="btn" @click="comboRows.push(newComboRow())">＋ 添加一行</button>
          <button class="btn btn-primary" style="margin-left:8px" :disabled="comboLoading" @click="submitCombo">
            {{ comboLoading ? '导入中…' : '导入并启动生产' }}
          </button>
        </div>
      </template>

      <p v-if="error" class="form-error">{{ error }}</p>
      <p v-if="result" class="form-ok">{{ result }}　<router-link to="/tasks">前往任务中心 →</router-link></p>
      <template v-if="tab==='combo' && (comboResult || comboItems.length)">
        <p class="form-ok">{{ comboResult }}　<router-link to="/tasks">前往任务中心 →</router-link></p>
        <ul v-if="comboItems.length" class="plain-list" style="margin-top:6px">
          <li v-for="(it, i) in comboItems" :key="it.task_id">
            <b>{{ i + 1 }}.</b> {{ it.question }}
          </li>
        </ul>
        <ul v-if="comboSkipped.length" class="plain-list form-error">
          <li v-for="(s, i) in comboSkipped" :key="i">跳过：{{ s.query }}（{{ s.reason }}）</li>
        </ul>
      </template>
      <ul v-else-if="errors.length" class="plain-list form-error">
        <li v-for="(e, i) in errors" :key="i">{{ e }}</li>
      </ul>
    </div>
  </app-layout>`,
  created() { this.MODE = MODE; this.COMBO_STYLES = COMBO_STYLES; this.COMBO_CATEGORIES = COMBO_CATEGORIES; this.newComboRow = newComboRow; },
};
