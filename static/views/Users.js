// 用户管理（仅 admin）：增删改查平台账号
const UsersView = {
  data() {
    return {
      users: [], error: '', msg: '',
      create: { name: '', password: '', role: 'A' },
      edit: null,   // {id, name, role, active, password}
      roles: ['A', 'B', 'C', 'admin'],
    };
  },
  computed: {
    user() { return getUser(); },
    isAdmin() { const u = getUser(); return u && u.role === 'admin'; },
  },
  methods: {
    fmtTime, roleName,
    async load() {
      this.error = '';
      try {
        const r = await api.get('/api/admin/users?actor=' + encodeURIComponent(this.user.name));
        this.users = r.users;
      } catch (e) { this.error = e.message; }
    },
    async add() {
      this.error = ''; this.msg = '';
      try {
        await api.post('/api/admin/users', { actor: this.user.name, ...this.create });
        this.msg = '已创建用户 ' + this.create.name;
        this.create = { name: '', password: '', role: 'A' };
        await this.load();
      } catch (e) { this.error = e.message; }
    },
    startEdit(u) {
      this.edit = { id: u.id, name: u.name, role: u.role, active: u.active, password: '' };
    },
    async saveEdit() {
      this.error = ''; this.msg = '';
      const e = this.edit;
      try {
        const body = { actor: this.user.name, name: e.name, role: e.role, active: e.active };
        if (e.password) body.password = e.password;   // 留空 = 不改密码
        await api._req('PUT', '/api/admin/users/' + e.id, body);
        this.msg = '已保存'; this.edit = null; await this.load();
      } catch (err) { this.error = err.message; }
    },
    async del(u) {
      if (!confirm(`确定删除用户「${u.name}」？其名下有数据时会被拒绝，可改为停用。`)) return;
      this.error = ''; this.msg = '';
      try {
        await api._req('DELETE', '/api/admin/users/' + u.id + '?actor=' + encodeURIComponent(this.user.name));
        this.msg = '已删除'; await this.load();
      } catch (e) { this.error = e.message; }
    },
  },
  mounted() { if (this.isAdmin) this.load(); },
  template: `
  <app-layout title="用户管理">
    <div v-if="!isAdmin" class="card empty">无权限：仅管理员可访问用户管理。</div>
    <template v-else>
      <p v-if="error" class="form-error">{{ error }}</p>
      <p v-if="msg" class="form-ok">{{ msg }}</p>

      <div class="card">
        <h2>新建用户</h2>
        <div style="display:grid;grid-template-columns:1fr 1fr 160px auto;gap:10px;align-items:center">
          <input v-model="create.name" placeholder="用户名">
          <input v-model="create.password" type="password" placeholder="初始密码">
          <select v-model="create.role">
            <option v-for="r in roles" :key="r" :value="r">{{ r }} · {{ roleName(r) }}</option>
          </select>
          <button class="btn btn-primary" @click="add" :disabled="!create.name || !create.password">创建</button>
        </div>
      </div>

      <div class="card">
        <h2>用户列表（{{ users.length }}）</h2>
        <table class="table">
          <thead><tr><th>用户名</th><th>角色</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead>
          <tbody>
            <tr v-for="u in users" :key="u.id">
              <template v-if="edit && edit.id === u.id">
                <td><input v-model="edit.name"></td>
                <td><select v-model="edit.role">
                  <option v-for="r in roles" :key="r" :value="r">{{ r }} · {{ roleName(r) }}</option>
                </select></td>
                <td><label style="font-size:14px;display:flex;gap:6px;align-items:center">
                  <input type="checkbox" v-model="edit.active" style="width:auto"> 在职
                </label></td>
                <td><input v-model="edit.password" type="password" placeholder="重置密码（留空不改）"></td>
                <td>
                  <button class="btn btn-primary btn-sm" @click.stop="saveEdit">保存</button>
                  <button class="btn btn-outline btn-sm" @click.stop="edit=null">取消</button>
                </td>
              </template>
              <template v-else>
                <td>{{ u.name }}</td>
                <td><span class="tag tag-blue">{{ u.role }} · {{ roleName(u.role) }}</span></td>
                <td><span class="tag" :class="u.active ? 'tag-green' : 'tag-gray'">{{ u.active ? '在职' : '停用' }}</span></td>
                <td class="muted">{{ fmtTime(u.created_at) }}</td>
                <td>
                  <button class="btn btn-outline btn-sm" @click.stop="startEdit(u)">编辑</button>
                  <button class="btn btn-danger btn-sm" @click.stop="del(u)">删除</button>
                </td>
              </template>
            </tr>
          </tbody>
        </table>
      </div>
    </template>
  </app-layout>`,
};
