// 设置：修改自己的密码 + 提示词库（系统默认仅 admin 可改；自定义每人管自己的，admin 看全部）
const SettingsView = {
  data() {
    return {
      tab: 'prompts',
      // 密码
      old_password: '', new_password: '', confirm: '', pwError: '', pwMsg: '', savingPw: false,
      // 提示词库
      catalog: [], customs: [],
      curStage: 'draft_gen', curMode: 'general',
      form: null, showSystem: false, sysForm: null,
      error: '', msg: '',
      // 工作日志
      logs: [], logTotal: 0, logActions: [], logUsers: [],
      logUser: '', logAction: '', logPage: 0, logLimit: 20, logsAdmin: false,
    };
  },
  computed: {
    user() { return getUser(); },
    isAdmin() { const u = getUser(); return u && u.role === 'admin'; },
    curStageObj() { return this.catalog.find(s => s.stage === this.curStage); },
    modes() { return this.curStageObj ? this.curStageObj.items : []; },
    curItem() { return this.modes.find(m => m.mode === this.curMode) || this.modes[0]; },
    // 当前 (环节, 模式) 下的自定义提示词；admin 看全部用户的
    filtered() {
      const m = this.curItem ? this.curItem.mode : null;
      return this.customs.filter(p => p.stage === this.curStage && p.mode === m);
    },
  },
  methods: {
    fmtTime,
    // ---------- 工作日志 ----------
    actLabel(a) {
      return {
        login: '登录', login_failed: '登录失败', register: '注册',
        change_password: '修改密码', import_tasks: '导入任务', retry_task: '重试任务',
        review_approve: '审核通过', review_reject: '审核驳回',
        prompt_create: '新建提示词', prompt_update: '修改提示词', prompt_delete: '删除提示词',
        system_prompt_update: '改系统提示词', system_prompt_restore: '恢复系统提示词',
        user_create: '新建用户', user_update: '修改用户', user_delete: '删除用户',
        admin_clear: '清空工作内容', admin_export: '导出工作记录',
        export_approved: '导出内容包',
        maintenance_start: '进入检修', maintenance_end: '结束检修',
      }[a] || a;
    },
    async loadLogs() {
      try {
        let url = '/api/activity?actor=' + encodeURIComponent(this.user.name)
          + '&limit=' + this.logLimit + '&offset=' + (this.logPage * this.logLimit);
        if (this.logUser) url += '&user=' + encodeURIComponent(this.logUser);
        if (this.logAction) url += '&action=' + encodeURIComponent(this.logAction);
        const r = await api.get(url);
        this.logs = r.logs; this.logTotal = r.total;
        this.logActions = r.actions; this.logsAdmin = r.is_admin;
        if (r.is_admin && !this.logUsers.length) {
          const u = await api.get('/api/admin/users?actor=' + encodeURIComponent(this.user.name));
          this.logUsers = u.users.map(x => x.name);
        }
      } catch (e) { this.error = e.message; }
    },
    logPages() { return Math.max(1, Math.ceil(this.logTotal / this.logLimit)); },
    // ---------- 密码 ----------
    async submitPw() {
      this.pwError = ''; this.pwMsg = '';
      if (this.new_password !== this.confirm) { this.pwError = '两次输入的新密码不一致'; return; }
      this.savingPw = true;
      try {
        const r = await api.post('/api/auth/change_password', {
          username: this.user.name,
          old_password: this.old_password,
          new_password: this.new_password,
        });
        if (!r.ok) throw new Error(r.error || '修改失败');
        this.pwMsg = '密码已修改，下次登录请使用新密码';
        this.old_password = this.new_password = this.confirm = '';
      } catch (e) { this.pwError = e.message; }
      finally { this.savingPw = false; }
    },
    // ---------- 提示词库 ----------
    async load() {
      this.error = '';
      try {
        const [c, l] = await Promise.all([
          api.get('/api/prompts/catalog'),
          api.get('/api/prompts?actor=' + encodeURIComponent(this.user.name)),
        ]);
        this.catalog = c.stages; this.customs = l.prompts;
        if (this.curStageObj && !this.modes.some(m => m.mode === this.curMode)) {
          this.curMode = this.modes[0].mode;
        }
      } catch (e) { this.error = e.message; }
    },
    selectStage(s) {
      this.curStage = s;
      const st = this.catalog.find(x => x.stage === s);
      this.curMode = st && st.items.length ? st.items[0].mode : null;
      this.form = null; this.sysForm = null;
    },
    // 系统默认（仅 admin 可编辑/恢复）
    startSysEdit() { this.sysForm = { content: this.curItem ? this.curItem.system : '' }; },
    async saveSys() {
      this.error = ''; this.msg = '';
      try {
        await api._req('PUT', '/api/prompts/system', {
          actor: this.user.name, stage: this.curStage,
          mode: this.curItem ? this.curItem.mode : null,
          content: this.sysForm.content });
        this.msg = '系统默认提示词已更新，对所有用户生效';
        this.sysForm = null; await this.load();
      } catch (e) { this.error = e.message; }
    },
    async restoreSys() {
      if (!confirm('确定恢复为代码内置的系统默认提示词？当前的 admin 修改将被移除。')) return;
      this.error = ''; this.msg = '';
      try {
        const mode = this.curItem ? this.curItem.mode : null;
        await api._req('DELETE', '/api/prompts/system?actor=' + encodeURIComponent(this.user.name)
          + '&stage=' + this.curStage + (mode ? '&mode=' + mode : ''));
        this.msg = '已恢复内置默认'; await this.load();
      } catch (e) { this.error = e.message; }
    },
    // 自定义
    newPrompt() {
      this.form = {
        id: null, name: '', content: this.curItem ? this.curItem.system : '',
        is_active: false, stage: this.curStage,
        mode: this.curItem ? this.curItem.mode : null,
      };
    },
    editPrompt(p) {
      this.form = { id: p.id, name: p.name, content: p.content,
                    is_active: p.is_active, stage: p.stage, mode: p.mode };
    },
    canEdit(p) { return this.isAdmin || p.owner_name === this.user.name; },
    async save() {
      this.error = ''; this.msg = '';
      const f = this.form;
      try {
        if (f.id) {
          await api._req('PUT', '/api/prompts/' + f.id, {
            actor: this.user.name, name: f.name, content: f.content, is_active: f.is_active });
        } else {
          await api.post('/api/prompts', {
            actor: this.user.name, stage: f.stage, mode: f.mode,
            name: f.name, content: f.content, is_active: f.is_active });
        }
        this.msg = '已保存'; this.form = null; await this.load();
      } catch (e) { this.error = e.message; }
    },
    async toggle(p) {
      this.error = ''; this.msg = '';
      try {
        await api._req('PUT', '/api/prompts/' + p.id, {
          actor: this.user.name, is_active: !p.is_active });
        this.msg = p.is_active ? '已停用，回退系统默认' : '已启用，新任务将使用该提示词';
        await this.load();
      } catch (e) { this.error = e.message; }
    },
    async del(p) {
      if (!confirm(`确定删除自定义提示词「${p.name}」？`)) return;
      this.error = ''; this.msg = '';
      try {
        await api._req('DELETE', '/api/prompts/' + p.id + '?actor=' + encodeURIComponent(this.user.name));
        this.msg = '已删除'; await this.load();
      } catch (e) { this.error = e.message; }
    },
  },
  mounted() { this.load(); },
  template: `
  <app-layout title="我的设置">
    <div class="card" style="padding:0 20px">
      <div class="tabs" style="margin-bottom:0">
        <button class="tab" :class="{on: tab==='prompts'}" @click="tab='prompts'">提示词库</button>
        <button class="tab" :class="{on: tab==='logs'}" @click="tab='logs'; loadLogs()">工作日志</button>
        <button class="tab" :class="{on: tab==='password'}" @click="tab='password'">修改密码</button>
      </div>
    </div>

    <div class="card" v-if="tab==='logs'">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <h2 style="margin:0">{{ logsAdmin ? '工作日志（全部用户）' : '我的工作日志' }}</h2>
        <span style="margin-left:auto;display:flex;gap:8px;align-items:center">
          <select v-if="logsAdmin" v-model="logUser" @change="logPage=0; loadLogs()" style="width:auto">
            <option value="">全部用户</option>
            <option v-for="u in logUsers" :key="u" :value="u">{{ u }}</option>
          </select>
          <select v-model="logAction" @change="logPage=0; loadLogs()" style="width:auto">
            <option value="">全部动作</option>
            <option v-for="a in logActions" :key="a" :value="a">{{ actLabel(a) }}</option>
          </select>
          <button class="btn btn-outline btn-sm" @click="loadLogs">刷新</button>
        </span>
      </div>
      <table class="table" style="margin-top:12px" v-if="logs.length">
        <thead><tr><th>时间</th><th v-if="logsAdmin">用户</th><th>动作</th><th>内容</th></tr></thead>
        <tbody>
          <tr v-for="l in logs" :key="l.id" style="cursor:default">
            <td class="muted" style="white-space:nowrap">{{ fmtTime(l.created_at) }}</td>
            <td v-if="logsAdmin">{{ l.actor_name }}</td>
            <td><span class="tag tag-blue">{{ actLabel(l.action) }}</span></td>
            <td style="white-space:pre-wrap">{{ l.detail }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="empty" style="padding:20px 0">暂无日志</p>
      <div style="display:flex;align-items:center;gap:10px;margin-top:10px" v-if="logTotal > logLimit">
        <button class="btn btn-outline btn-sm" :disabled="logPage===0" @click="logPage--; loadLogs()">上一页</button>
        <span class="muted">{{ logPage + 1 }} / {{ logPages() }} 页 · 共 {{ logTotal }} 条</span>
        <button class="btn btn-outline btn-sm" :disabled="logPage + 1 >= logPages()" @click="logPage++; loadLogs()">下一页</button>
      </div>
    </div>

    <div class="card" style="max-width:520px" v-if="tab==='password'">
      <h2>修改我的密码</h2>
      <p class="muted">当前账号：{{ user.name }}（{{ user.role }}）</p>
      <form @submit.prevent="submitPw">
        <p><input v-model="old_password" type="password" required placeholder="原密码"></p>
        <p><input v-model="new_password" type="password" required placeholder="新密码（至少 8 位）"></p>
        <p><input v-model="confirm" type="password" required placeholder="再输入一次新密码"></p>
        <p v-if="pwError" class="form-error">{{ pwError }}</p>
        <p v-if="pwMsg" class="form-ok">{{ pwMsg }}</p>
        <button class="btn btn-primary" :disabled="savingPw">{{ savingPw ? '提交中…' : '保存新密码' }}</button>
      </form>
    </div>

    <template v-if="tab==='prompts'">
    <p v-if="error" class="form-error">{{ error }}</p>
    <p v-if="msg" class="form-ok">{{ msg }}</p>
    <div class="card">
      <div class="tabs">
        <button v-for="s in catalog" :key="s.stage" class="tab"
                :class="{on: curStage===s.stage}" @click="selectStage(s.stage)">{{ s.label }}</button>
      </div>
      <div v-if="modes.length > 1" class="tabs">
        <button v-for="m in modes" :key="m.mode" class="tab"
                :class="{on: curMode===m.mode}" @click="curMode=m.mode; form=null; sysForm=null">{{ m.mode_label }}模式</button>
      </div>
      <p class="muted" v-if="curStageObj">{{ curStageObj.hint }}</p>

      <div style="display:flex;align-items:center;gap:10px;margin-top:16px">
        <h2 style="margin:0">系统默认提示词</h2>
        <span v-if="curItem && curItem.customized" class="tag tag-yellow">已被管理员修改</span>
      </div>
      <p class="muted">{{ isAdmin ? '管理员可修改，修改后对所有用户生效。' : '仅管理员可修改；未启用自定义提示词时，你的任务使用此默认。' }}</p>
      <button class="btn btn-outline btn-sm" @click="showSystem=!showSystem">{{ showSystem ? '收起' : '查看' }}</button>
      <template v-if="isAdmin">
        <button class="btn btn-outline btn-sm" @click="startSysEdit" v-if="!sysForm">编辑</button>
        <button class="btn btn-outline btn-sm" @click="restoreSys" v-if="curItem && curItem.customized">恢复内置默认</button>
      </template>
      <pre v-if="showSystem && !sysForm" class="log-box" style="background:#f8f9fb;color:var(--text)">{{ curItem && curItem.system }}</pre>
      <div v-if="sysForm" style="margin-top:10px">
        <textarea v-model="sysForm.content" rows="12"></textarea>
        <div style="display:flex;gap:8px;margin-top:10px">
          <button class="btn btn-primary btn-sm" @click="saveSys">保存为系统默认</button>
          <button class="btn btn-outline btn-sm" @click="sysForm=null">取消</button>
        </div>
      </div>

      <div style="display:flex;justify-content:space-between;align-items:center;margin-top:20px">
        <h2 style="margin:0">自定义提示词{{ isAdmin ? '（全部用户）' : '（我的）' }}</h2>
        <button class="btn btn-primary btn-sm" @click="newPrompt" v-if="!form">新建（以系统默认起稿）</button>
      </div>
      <table class="table" v-if="filtered.length" style="margin-top:10px">
        <thead><tr>
          <th>名称</th><th v-if="isAdmin">归属</th><th>状态</th><th>更新时间</th><th>操作</th>
        </tr></thead>
        <tbody>
          <tr v-for="p in filtered" :key="p.id">
            <td>{{ p.name }}</td>
            <td v-if="isAdmin">{{ p.owner_name }}</td>
            <td><span class="tag" :class="p.is_active ? 'tag-green' : 'tag-gray'">{{ p.is_active ? '已启用' : '未启用' }}</span></td>
            <td class="muted">{{ fmtTime(p.updated_at) }}</td>
            <td v-if="canEdit(p)">
              <button class="btn btn-outline btn-sm" @click.stop="editPrompt(p)">编辑</button>
              <button class="btn btn-outline btn-sm" @click.stop="toggle(p)">{{ p.is_active ? '停用' : '启用' }}</button>
              <button class="btn btn-danger btn-sm" @click.stop="del(p)">删除</button>
            </td>
            <td v-else class="muted">-</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="empty" style="padding:20px 0">暂无自定义提示词</p>
    </div>

    <div class="card" v-if="form">
      <h2>{{ form.id ? '编辑' : '新建' }}自定义提示词 · {{ curStageObj.label }}<template v-if="curItem && curItem.mode"> · {{ curItem.mode_label }}</template></h2>
      <p><input v-model="form.name" placeholder="提示词名称，如：活泼口吻版"></p>
      <p style="margin-top:10px"><textarea v-model="form.content" rows="12" placeholder="提示词内容"></textarea></p>
      <label style="display:flex;align-items:center;gap:6px;margin-top:10px;font-size:14px">
        <input type="checkbox" v-model="form.is_active" style="width:auto"> 保存后立即启用（同环节同模式的其它自定义提示词会自动停用）
      </label>
      <div style="display:flex;gap:8px;margin-top:14px">
        <button class="btn btn-primary" @click="save">保存</button>
        <button class="btn btn-outline" @click="form=null">取消</button>
      </div>
    </div>
    </template>
  </app-layout>`,
};
