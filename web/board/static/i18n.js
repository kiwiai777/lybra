/**
 * Lybra Board i18n system
 * Centralized translation management (AIPOS-252)
 */

const translations = {
  zh: {
    // Common
    'lang.name': '中文',
    'lang.switch': '切换语言',
    
    // Overview page
    'overview.title': 'Lybra 总览',
    'overview.subtitle': '多工作区控制台',
    'overview.workspaces': '工作区',
    'overview.all_projects': '所有项目',
    'overview.loading': '加载中...',
    'overview.no_workspaces': '未配置工作区。请在 .board_config.json 添加工作区',
    'overview.error': '加载失败',
    
    // Workspace status
    'status.ok': '正常',
    'status.error': '错误',
    'status.unknown': '未知',
    
    // Queue states (人话翻译)
    'queue.pending': '待认领',
    'queue.claimed': '进行中',
    'queue.blocked': '受阻',
    'queue.completed': '已完成',
    
    // Project detail page
    'detail.title': '项目详情',
    'detail.back_to_overview': '← 返回总览',
    'detail.loading': '加载项目详情...',
    'detail.error_loading': '加载失败',
    
    // Section: Progress
    'detail.section.progress': '进展概况',
    'detail.progress.in_progress': '件进行中',
    'detail.progress.waiting_review': '件等我拍板',
    'detail.progress.completed_today': '件今天完成',
    'detail.progress.pending': '件待认领',
    'detail.progress.blocked': '件受阻',
    
    // Section: Needs Owner
    'detail.section.needs_owner': '等我处理',
    'detail.needs_owner.empty': '暂无需要 Owner 处理的事项',
    'detail.needs_owner.what_to_do': '要做什么',
    'detail.needs_owner.task': '任务',
    'detail.needs_owner.reason': '原因',
    'detail.needs_owner.command': '下一步命令',
    'detail.needs_owner.copy': '复制',
    'detail.needs_owner.copied': '已复制',
    
    // Section: Timeline
    'detail.section.timeline': '最近动态',
    'detail.timeline.empty': '暂无记录',
    'detail.timeline.type.claim': '认领了',
    'detail.timeline.type.return': '完成了',
    'detail.timeline.type.publish': '发布了',
    'detail.timeline.type.audit_dispatch': '发起了审计',
    'detail.timeline.type.audit_verdict': '审计裁决',
    'detail.timeline.type.owner_decision': 'Owner 决策',
    'detail.timeline.type.session': '会话',
    'detail.timeline.type.unknown': '操作',
    
    // Advanced/Debug
    'detail.advanced': '高级选项',
    'detail.advanced.debug_view': '工程调试视图',
    'detail.advanced.debug_desc': '查看完整的工程字段与原始 JSON(仅供排障)',
    
    // Workspace card (overview)
    'card.needs_owner': '等我处理',
    'card.needs_owner_empty': '暂无待处理事项',
    'card.recent_activity': '最近活动',
    'card.more_items': '更多',
  },
  
  en: {
    // Common
    'lang.name': 'English',
    'lang.switch': 'Switch Language',
    
    // Overview page
    'overview.title': 'Lybra Overview',
    'overview.subtitle': 'Multi-workspace Dashboard',
    'overview.workspaces': 'Workspaces',
    'overview.all_projects': 'All Projects',
    'overview.loading': 'Loading...',
    'overview.no_workspaces': 'No workspaces configured. Add workspaces to .board_config.json',
    'overview.error': 'Error loading',
    
    // Workspace status
    'status.ok': 'OK',
    'status.error': 'Error',
    'status.unknown': 'Unknown',
    
    // Queue states
    'queue.pending': 'Pending',
    'queue.claimed': 'In Progress',
    'queue.blocked': 'Blocked',
    'queue.completed': 'Completed',
    
    // Project detail page
    'detail.title': 'Project Detail',
    'detail.back_to_overview': '← Back to Overview',
    'detail.loading': 'Loading project detail...',
    'detail.error_loading': 'Failed to load',
    
    // Section: Progress
    'detail.section.progress': 'Progress Overview',
    'detail.progress.in_progress': 'in progress',
    'detail.progress.waiting_review': 'awaiting decision',
    'detail.progress.completed_today': 'completed today',
    'detail.progress.pending': 'pending',
    'detail.progress.blocked': 'blocked',
    
    // Section: Needs Owner
    'detail.section.needs_owner': 'Needs Your Attention',
    'detail.needs_owner.empty': 'No items requiring Owner action',
    'detail.needs_owner.what_to_do': 'What to do',
    'detail.needs_owner.task': 'Task',
    'detail.needs_owner.reason': 'Reason',
    'detail.needs_owner.command': 'Next Command',
    'detail.needs_owner.copy': 'Copy',
    'detail.needs_owner.copied': 'Copied',
    
    // Section: Timeline
    'detail.section.timeline': 'Recent Activity',
    'detail.timeline.empty': 'No records',
    'detail.timeline.type.claim': 'claimed',
    'detail.timeline.type.return': 'completed',
    'detail.timeline.type.publish': 'published',
    'detail.timeline.type.audit_dispatch': 'dispatched audit for',
    'detail.timeline.type.audit_verdict': 'audit verdict for',
    'detail.timeline.type.owner_decision': 'Owner decision on',
    'detail.timeline.type.session': 'session on',
    'detail.timeline.type.unknown': 'action on',
    
    // Advanced/Debug
    'detail.advanced': 'Advanced',
    'detail.advanced.debug_view': 'Engineering Debug View',
    'detail.advanced.debug_desc': 'View full engineering fields and raw JSON (for troubleshooting only)',
    
    // Workspace card (overview)
    'card.needs_owner': 'Needs Owner',
    'card.needs_owner_empty': 'No items waiting for Owner',
    'card.recent_activity': 'Recent',
    'card.more_items': 'more',
  }
};

// Current language (default: zh, persisted in localStorage)
let currentLang = localStorage.getItem('lybra_lang') || 'zh';

/**
 * Get translation for a key
 */
function t(key) {
  return translations[currentLang]?.[key] || translations['zh']?.[key] || key;
}

/**
 * Switch language
 */
function switchLanguage(lang) {
  if (!translations[lang]) {
    console.warn(`Language ${lang} not supported`);
    return;
  }
  currentLang = lang;
  localStorage.setItem('lybra_lang', lang);
  
  // Re-render page
  if (typeof renderPage === 'function') {
    renderPage();
  } else {
    // Fallback: reload page
    location.reload();
  }
}

/**
 * Get current language
 */
function getCurrentLang() {
  return currentLang;
}

/**
 * Create language switcher widget
 */
function createLanguageSwitcher() {
  const switcher = document.createElement('div');
  switcher.className = 'lang-switcher';
  
  const zhBtn = document.createElement('button');
  zhBtn.className = currentLang === 'zh' ? 'active' : '';
  zhBtn.textContent = '中文';
  zhBtn.onclick = () => switchLanguage('zh');
  
  const enBtn = document.createElement('button');
  enBtn.className = currentLang === 'en' ? 'active' : '';
  enBtn.textContent = 'EN';
  enBtn.onclick = () => switchLanguage('en');
  
  switcher.appendChild(zhBtn);
  switcher.appendChild(enBtn);
  
  return switcher;
}

// Export for browser
if (typeof window !== 'undefined') {
  window.i18n = {
    t,
    switchLanguage,
    getCurrentLang,
    createLanguageSwitcher
  };
}
