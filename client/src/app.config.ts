import { tabs } from './services/navigation'
export default defineAppConfig({
  pages: [
    'pages/index/index', 'pages/jobs/index', 'pages/jobs/detail/index', 'pages/jobs/feedback/index', 'pages/me/index', 'pages/login/index',
    'pages/admin/index', 'pages/admin/org/index', 'pages/admin/region/index',
    'pages/admin/source/index', 'pages/admin/review/index', 'pages/admin/status/index',
    'pages/admin/onboarding-detail/index', 'pages/admin/incremental/index', 'pages/admin/feedback/index',
  ],
  window: {
    navigationStyle: 'default',
    navigationBarTitleText: '职有据',
    navigationBarBackgroundColor: '#f5f5f5',
    navigationBarTextStyle: 'black',
    backgroundColor: '#f5f5f5',
  },
  tabBar: {
    color: '#8a8a8a', selectedColor: '#171717', backgroundColor: '#ffffff', borderStyle: 'white',
    list: tabs.map(tab => ({
      pagePath: tab.pagePath, text: tab.text,
      iconPath: `assets/tabbar/${tab.icon}.png`, selectedIconPath: `assets/tabbar/${tab.icon}-selected.png`,
    })),
  },
})
