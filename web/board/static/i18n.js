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
    
    // AIPOS-260: record-derived true stages (Owner truth summary)
    'stage.published': '已发布',
    'stage.executing': '执行中',
    'stage.delivered': '已交付待审',
    'stage.auditing': '审计中',
    'stage.verdict_pass': '判决 PASS',
    'stage.verdict_fail': '判决 FAIL',
    'stage.closed': '已闭环',
    'stage.pending': '待认领',
    'stage.blocked': '受阻',
    'stage.unknown': '未知',
    
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

    // ===== AIPOS-266: 四区界面标签 (门户头/里程碑地图/验证台/任务中心) + 共享弹层 =====
    // 红线:记录与声明内容 (卡标题/摘要/findings/里程碑文字/portal 描述) 原文直显,不译。

    // 共享弹层
    'popup.close': '关闭',

    // 门户头 (AIPOS-264)
    'portal.name_fallback': '项目',
    'portal.updated_prefix': '更新 ',
    'portal.label.mode': '模式',
    'portal.label.topology': '拓扑',
    'portal.label.workers': '牛马',
    'portal.label.advisor': '顾问',
    'portal.role.worker_fallback': '牛马',
    'portal.role.advisor_fallback': '顾问',
    'portal.worker_chip_title': '查看 agent 档案',

    // 里程碑地图 (AIPOS-262B)
    'map.title': '项目里程碑',
    'map.hint': '横向里程碑图表:已完成(实心)→ 当前(高亮)→ 近期规划 → 远期走向(虚线)。点节点看详情与决策引用。',
    'map.legend.done': '已完成',
    'map.legend.current': '当前',
    'map.legend.near': '近期规划',
    'map.legend.horizon': '远期走向',
    'map.popup.kind.done': '已完成里程碑',
    'map.popup.kind.current': '当前位置',
    'map.popup.kind.near': '近期规划',
    'map.popup.kind.horizon': '远期走向',
    'map.popup.current_title': '当前进展',
    'map.popup.section.refs': '相关决策引用',
    'map.popup.section.direction_log': 'direction_log 最近方向',
    
    // Workspace card (overview)
    'card.needs_owner': '等我处理',
    'card.needs_owner_empty': '暂无待处理事项',
    'card.recent_activity': '最近活动',
    'card.more_items': '更多',
    'card.real_progress': '真实进展（按记录推导）',

    // 验证台 (AIPOS-262B FIX-1)
    'vb.title': '验证台 · Owner 核验',
    'vb.hint': '审计已 PASS 待 Owner 真机过目的核验站 + 进行中预览;需要 Owner 裁定的待办("等我处理")并入此处。',
    'vb.stations.heading': '待验站({n})——审计已 PASS,待 Owner 真机过目',
    'vb.preview.heading': '进行中预览({n})——它将被怎么验',
    'vb.needs.heading': '待 Owner 裁定({n})——"等我处理"并入此处',
    'vb.resolution_note_fallback': '只读核验面;通过/打回按键留候选⑬(board 鉴权后)。',
    'vb.station.await': '待 Owner 核验',
    'vb.station.head_title': '点击展开/收起断言与证据',
    'vb.preview.head_title': '点击展开/收起验收标准',
    'vb.toggle.expand': '展开 ▸',
    'vb.toggle.collapse': '收起 ▾',
    'vb.section.assertions': '验收断言',
    'vb.ring.machine': '机判记录',
    'vb.ring.audit': '审计判决',
    'vb.ring.fix': '往轮修复',
    'vb.ring.status_prefix': '状态:',
    'vb.empty.machine': '尚未提交返回记录。',
    'vb.empty.audit': '尚未记录审计判决。',
    'vb.empty.fix': '无往轮修复(首轮即过)。',
    'vb.action.pass': '通过',
    'vb.action.reject': '打回',
    'vb.preview.note_fallback': '进行中——验收标准预览。',
    'card.real_progress_empty': '暂无已记录进展',

    // 任务中心 (AIPOS-260 FIX-1)
    'tc.title': '任务中心 · Owner 真相摘要',
    'tc.hint': '每任务一卡：标题 + 一句话目的 + 真实阶段（从已记录的 records 推导，不改队列状态机）。点开看每轮摘要时间线；下方为按记录时间倒序的动态流。只读已记录真相。',
    'tc.subheading.cards': '任务卡片（点开看每轮摘要）',
    'tc.subheading.feed': '动态流（按记录时间倒序）',
    'tc.loading': '加载中...',
    'tc.purpose_fallback': '(无目的摘要)',
    'tc.view_card': '查看原卡',
    'tc.view_card_title': '右侧抽屉渲染本卡 md 原文',
    'tc.view_record': '原记录',
    'tc.view_record_title': '查看本记录 md 原文',
    'tc.member.main': '主',
    'tc.member.audit': '审',
    'tc.member.fix': '修',
    'tc.member.finalize': '编',
    'tc.dur.sec': ' 秒',
    'tc.dur.min': ' 分钟',
    'tc.dur.hr': ' 小时',
    'detail.needs_owner.reason.approval': '需要 Owner 审批',
    'detail.needs_owner.reason.review': '待审核',

    // 共享:agent 档案弹层 (AIPOS-265;门户头牛马 chip + 任务中心 agent 链接共用)
    'agent.profile.default': 'agent 档案',
    'agent.profile.suffix': '档案',
    'agent.profile.latest': '最近已知档案',
    'agent.profile.this_round': '本轮',
    'agent.profile.model': '自报模型',
    'agent.profile.tokens': '自报 tokens(in/out)',
    'agent.profile.duration': '本次耗时',
    'agent.profile.source_prefix': '来自 ',
    'agent.profile.self_reported_tag': '自报',
    'agent.profile.not_recorded': '未记录',
    'agent.profile.no_profile': '暂无已知档案',
    'agent.profile.no_round': '本轮未记录',

    // 共享:md 原文抽屉 (AIPOS-263;任务中心原卡/原记录共用)
    'md.drawer.aria': 'md 原文',
    'md.drawer.title_fallback': 'md 原文',
    'md.drawer.loading': '加载中…',
    'md.drawer.loading_path': '加载中…',
    'md.drawer.load_failed': '加载失败',
    'md.drawer.network_error': '网络错误',
    'md.drawer.frontmatter': 'frontmatter(折叠)',
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
    
    // AIPOS-260: record-derived true stages (Owner truth summary)
    'stage.published': 'Published',
    'stage.executing': 'Executing',
    'stage.delivered': 'Delivered (awaiting audit)',
    'stage.auditing': 'Under audit',
    'stage.verdict_pass': 'Verdict PASS',
    'stage.verdict_fail': 'Verdict FAIL',
    'stage.closed': 'Closed loop',
    'stage.pending': 'Pending',
    'stage.blocked': 'Blocked',
    'stage.unknown': 'Unknown',
    
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

    // ===== AIPOS-266: four-area UI labels (portal header / milestone map / verify bench /
    // task center) + shared popups. Red line: record & statement content (card titles /
    // summaries / findings / milestone text / portal description) stays original; chrome only. =====

    // Shared popups
    'popup.close': 'Close',

    // Portal header (AIPOS-264)
    'portal.name_fallback': 'Project',
    'portal.updated_prefix': 'Updated ',
    'portal.label.mode': 'Mode',
    'portal.label.topology': 'Topology',
    'portal.label.workers': 'Workers',
    'portal.label.advisor': 'Advisor',
    'portal.role.worker_fallback': 'Worker',
    'portal.role.advisor_fallback': 'Advisor',
    'portal.worker_chip_title': 'View agent profile',

    // Milestone map (AIPOS-262B)
    'map.title': 'Project Milestones',
    'map.hint': 'Horizontal milestone chart: completed (solid) → current (highlighted) → near-term plan → horizon (dashed). Click a node for details and decision refs.',
    'map.legend.done': 'Completed',
    'map.legend.current': 'Current',
    'map.legend.near': 'Near-term',
    'map.legend.horizon': 'Horizon',
    'map.popup.kind.done': 'Completed milestone',
    'map.popup.kind.current': 'Current position',
    'map.popup.kind.near': 'Near-term plan',
    'map.popup.kind.horizon': 'Horizon',
    'map.popup.current_title': 'Current progress',
    'map.popup.section.refs': 'Related decision refs',
    'map.popup.section.direction_log': 'direction_log (recent directions)',
    
    // Workspace card (overview)
    'card.needs_owner': 'Needs Owner',
    'card.needs_owner_empty': 'No items waiting for Owner',
    'card.recent_activity': 'Recent',
    'card.more_items': 'more',
    'card.real_progress': 'Real progress (record-derived)',

    // Verify bench (AIPOS-262B FIX-1)
    'vb.title': 'Verify Bench · Owner Review',
    'vb.hint': 'Verification stations (audit PASSed, awaiting Owner real-machine review) + in-progress previews; Owner-decision to-dos ("needs owner") are grouped here.',
    'vb.stations.heading': 'Verification stations ({n}) — audit PASSed, awaiting Owner real-machine review',
    'vb.preview.heading': 'In-progress previews ({n}) — how it will be verified',
    'vb.needs.heading': 'Awaiting Owner decision ({n}) — "needs owner" grouped here',
    'vb.resolution_note_fallback': 'Read-only verification face; pass/reject buttons are pending (after board auth).',
    'vb.station.await': 'Awaiting Owner review',
    'vb.station.head_title': 'Click to expand/collapse assertions and evidence',
    'vb.preview.head_title': 'Click to expand/collapse acceptance criteria',
    'vb.toggle.expand': 'Expand ▸',
    'vb.toggle.collapse': 'Collapse ▾',
    'vb.section.assertions': 'Acceptance assertions',
    'vb.ring.machine': 'Machine judgment',
    'vb.ring.audit': 'Audit verdict',
    'vb.ring.fix': 'Prior-round fixes',
    'vb.ring.status_prefix': 'Status: ',
    'vb.empty.machine': 'No return record submitted yet.',
    'vb.empty.audit': 'No audit verdict recorded yet.',
    'vb.empty.fix': 'No prior-round fixes (passed on first round).',
    'vb.action.pass': 'Pass',
    'vb.action.reject': 'Reject',
    'vb.preview.note_fallback': 'In progress — acceptance criteria preview.',
    'card.real_progress_empty': 'No recorded progress yet',

    // Task center (AIPOS-260 FIX-1)
    'tc.title': 'Task Center · Owner Truth Summary',
    'tc.hint': 'One card per task: title + one-line purpose + true stage (derived from recorded records; the queue state machine is untouched). Expand to see the per-round summary timeline; below is the activity feed in reverse record-time order. Read-only recorded truth.',
    'tc.subheading.cards': 'Task cards (expand for per-round summaries)',
    'tc.subheading.feed': 'Activity feed (reverse record-time order)',
    'tc.loading': 'Loading...',
    'tc.purpose_fallback': '(no purpose summary)',
    'tc.view_card': 'View source card',
    'tc.view_card_title': "Render this card's md source in the side drawer",
    'tc.view_record': 'Source record',
    'tc.view_record_title': "View this record's md source",
    'tc.member.main': 'Main',
    'tc.member.audit': 'Audit',
    'tc.member.fix': 'Fix',
    'tc.member.finalize': 'Finalize',
    'tc.dur.sec': ' sec',
    'tc.dur.min': ' min',
    'tc.dur.hr': ' hr',
    'detail.needs_owner.reason.approval': 'Requires Owner approval',
    'detail.needs_owner.reason.review': 'Pending review',

    // Shared: agent profile popup (AIPOS-265; portal worker chips + task-center agent links)
    'agent.profile.default': 'Agent Profile',
    'agent.profile.suffix': 'Profile',
    'agent.profile.latest': 'Latest known profile',
    'agent.profile.this_round': 'This round',
    'agent.profile.model': 'Self-reported model',
    'agent.profile.tokens': 'Self-reported tokens (in/out)',
    'agent.profile.duration': 'Duration',
    'agent.profile.source_prefix': 'From ',
    'agent.profile.self_reported_tag': 'self-reported',
    'agent.profile.not_recorded': 'not recorded',
    'agent.profile.no_profile': 'No known profile',
    'agent.profile.no_round': 'Not recorded this round',

    // Shared: md source drawer (AIPOS-263; task-center source card / record)
    'md.drawer.aria': 'md source',
    'md.drawer.title_fallback': 'md source',
    'md.drawer.loading': 'Loading…',
    'md.drawer.loading_path': 'Loading…',
    'md.drawer.load_failed': 'Failed to load',
    'md.drawer.network_error': 'Network error',
    'md.drawer.frontmatter': 'frontmatter (collapsed)',
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
