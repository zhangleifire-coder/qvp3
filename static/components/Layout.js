// 主框架：侧边栏 + 顶栏 + 内容插槽
const AppLayout = {
  props: { title: { type: String, default: '' } },
  computed: {
    user() { return getUser(); },
    menu() {
      const items = [
        { path: '/', icon: '📈', label: '工作台' },
        { path: '/import', icon: '📥', label: '任务导入' },
        { path: '/tasks', icon: '🗂️', label: '任务中心' },
        { path: '/monitor', icon: '📡', label: '实时监控' },
        { path: '/review', icon: '📋', label: '任务审核' },
        { path: '/sample', icon: '🎲', label: '随机抽查' },
        { path: '/settings', icon: '🔑', label: '我的设置' },
        { path: '/users', icon: '👥', label: '用户管理', admin: true },
        { path: '/admin', icon: '⚙️', label: '系统管理', admin: true },
      ];
      return items.filter(i => !i.admin || (this.user && this.user.role === 'admin'));
    },
  },
  methods: {
    roleName,
    doLogout() { logout(); },
  },
  template: `
  <div>
    <aside class="sidebar">
      <div class="brand"><img src="/static/logo.png" alt="logo" class="brand-logo">图文生产平台</div>
      <div class="menu-label">功能菜单</div>
      <router-link v-for="i in menu" :key="i.path" :to="i.path" class="menu-item" exact-active-class="active">
        <span class="icon">{{ i.icon }}</span>{{ i.label }}
      </router-link>
      <div class="sidebar-footer">
        <div class="user-box" v-if="user">
          <span class="role-badge">{{ user.role }}</span>
          <span class="name">{{ user.name }} · {{ roleName(user.role) }}</span>
          <button class="logout" @click="doLogout">退出</button>
        </div>
      </div>
    </aside>
    <div class="topbar"><div class="page-title">{{ title }}</div></div>
    <div class="content"><slot /></div>
  </div>`,
};
