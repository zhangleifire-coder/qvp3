// 应用入口：路由 + 全局组件注册
const { createApp } = Vue;
const { createRouter, createWebHashHistory } = VueRouter;

const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/login', component: LoginView, meta: { public: true } },
    { path: '/', component: DashboardView },
    { path: '/import', component: ImportView },
    { path: '/tasks', component: TasksView },
    { path: '/monitor', component: MonitorView },
    { path: '/textcheck', component: TextCheckView },
    { path: '/refs', component: RefsBoardView },
    { path: '/review', component: ReviewView },
    { path: '/settings', component: SettingsView },
    { path: '/prompts', redirect: '/settings' },
    { path: '/superadmin', component: SuperAdminView },
    { path: '/users', component: UsersView },
    { path: '/admin', component: AdminView },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
});

router.beforeEach((to) => {
  if (!to.meta.public && !getUser()) return '/login';
});

const app = createApp({});
app.component('app-layout', AppLayout);
app.component('steps-bar', StepsBar);
app.component('img-lightbox', ImgLightbox);
app.use(router);
app.mount('#app');
