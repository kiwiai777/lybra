/**
 * Lybra Board i18n system (AIPOS-288: unified single-channel i18n)
 * 
 * RED LINE: All product UI text MUST flow through this channel.
 * - Frontend: i18n.t('key') in JS, data-i18n="key" in HTML
 * - Backend: _generate_text('template', locale, ...) in Python
 * - CJK literals outside i18n dictionaries => test failure (test_aipos288_cjk_source_guard.py)
 * 
 * Adding new UI text:
 * 1. Add key to BOTH zh and en dictionaries below (missing key in en => test failure)
 * 2. Use i18n.t('your.new.key') in JS or data-i18n="your.new.key" in HTML
 * 3. For backend-generated text (advisor prompts, etc.), add to _I18N_TEMPLATES in app.py
 * 4. Run: python3 tests/test_aipos288_cjk_source_guard.py to verify
 * 
 * Exemptions:
 * - Data content (project names, task titles, descriptions) stays original
 * - Code comments and docstrings (engineering docs, not product UI)
 * - Mark exemptions with: // i18n-exempt: <reason>
 * 
 * Centralized translation management (AIPOS-252 baseline, AIPOS-288 enforcement)
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
    'overview.add_project': '新增项目',
    'overview.new_project_modal.title': '新增项目',
    'overview.new_project_modal.intro': '启动一个新的并行项目工作区。选择你的设置方式:',
    'overview.new_project_modal.project_name': '项目名称',
    'overview.new_project_modal.project_name_hint': '仅限小写字母、数字、短横线或下划线',
    'overview.new_project_modal.project_name_en': '英文名称（可选）',
    'overview.new_project_modal.project_name_en_hint': '用于英文界面显示，留空则使用项目名称',
    'overview.new_project_modal.option_a': '复制命令（推荐）',
    'overview.new_project_modal.option_a_desc': 'AIPOS-F24: 项目顾问自助全流程。Owner 先发 planner 码,项目顾问持码完成项目初始化与后续角色注册,无需 lybra 顾问参与:',
    'overview.new_project_modal.option_b': '服务端初始化（兼容旧流程）',
    'overview.new_project_modal.option_b_desc': '让服务器创建并注册工作区（需要文件写入权限）。新项目推荐走选项 A 的自助流程:',
    // AIPOS-293 S4: Option C — Import existing project
    'overview.new_project_modal.option_c': '导入已有项目',
    'overview.new_project_modal.option_c_desc': '将现有项目接入 Lybra。先预览结构，确认后再导入——不会删除任何源文件。',
    'overview.new_project_modal.import_workspace_path': '已有工作区路径',
    'overview.new_project_modal.import_workspace_hint': '填写现有项目的绝对路径，如 ~/projects/my-app',
    'overview.new_project_modal.preview_import': '预览结构',
    'overview.new_project_modal.confirm_import': '确认导入',
    'overview.new_project_modal.previewing': '扫描工作区中...',
    'overview.new_project_modal.importing': '导入工作区中...',
    'overview.new_project_modal.import_success': '工作区导入成功！刷新中...',
    'overview.new_project_modal.import_path_required': '请输入路径',
    'overview.new_project_modal.need_project_name': '请在上方输入项目名称',
    'overview.new_project_modal.invalid_name': '项目名称无效。仅限小写字母、数字、短横线或下划线',
    // AIPOS-293 FIX-1: Dual mode labels
    'overview.new_project_modal.import_mode_directory': '项目目录',
    'overview.new_project_modal.import_mode_file': '结构文件',
    'overview.new_project_modal.import_structure_file_path': '结构文件路径',
    'overview.new_project_modal.import_structure_file_hint': '填写 .yaml 结构文件的绝对路径，如 /tmp/lybra-structure.yaml',
    'overview.new_project_modal.import_structure_file_upload': '或上传结构文件:',
    'overview.new_project_modal.import_yaml_hint': '检测到 .yaml 路径 → 建议切换到上方「结构文件」模式',
    // AIPOS-293 FIX-1: Preview display strings
    'overview.new_project_modal.preview_file_loaded': '✅ 结构文件已加载:',
    'overview.new_project_modal.preview_workspace_found': '✅ 找到工作区:',
    'overview.new_project_modal.preview_field_project': '项目',
    'overview.new_project_modal.preview_field_description': '描述',
    'overview.new_project_modal.preview_field_documents': '文档',
    'overview.new_project_modal.preview_field_code_repos': '代码仓库',
    'overview.new_project_modal.preview_field_governance': '治理文件',
    'overview.new_project_modal.preview_instruction': '在上方输入项目名称，然后点击「确认导入」创建工作区。',
    'overview.new_project_modal.preview_error_prefix': '错误',
    'overview.new_project_modal.suggest_file_mode_hint': ' — 看起来是结构文件。试试切换到上方的「结构文件」模式。',
    // AIPOS-293 FIX-1: Humanized error messages
    'error.import.path_required': '路径不能为空。请填写路径后重试',
    'error.import.path_not_exists': '路径不存在: {detail}。请检查路径拼写和权限',
    'error.import.path_not_directory': '路径不是目录: {detail}。请指定一个目录路径',
    'error.import.path_not_file': '路径不是文件: {detail}。请指定一个文件路径',
    'error.import.path_not_yaml': '文件不是 YAML 格式: {detail}。请使用 .yaml 或 .yml 文件',
    'error.import.file_read_failed': '无法读取文件: {detail}。请检查文件权限和编码',
    'error.import.schema_validation_failed': '结构文件校验失败: {detail}。请检查文件格式是否符合 schema',
    'error.import.project_id_required': '项目名称不能为空。请在上方输入项目名称',
    'error.import.project_id_invalid': '项目名称格式无效。仅限小写字母、数字、短横线或下划线',
    'error.import.workspace_not_empty': '目标工作区已存在且非空: {detail}。请选择其他名称或清空目标目录',
    'error.import.export_failed': '导出失败: {detail}。请检查源工作区完整性',
    'error.import.import_failed': '导入失败: {detail}。请检查结构文件内容',
    'error.import.unexpected_error': '发生意外错误: {detail}。请检查输入后重试，若持续出现请报告给顾问',
    'overview.copy': '复制',
    'overview.copied': '已复制!',
    'overview.init_now': '立即初始化',
    'overview.initializing': '初始化工作区中...',
    'overview.init_success': '工作区创建成功！刷新中...',
    
    // Workspace status
    'status.ok': '正常',
    'status.error': '错误',
    'status.unknown': '未知',
    
    // Queue states (人话翻译)
    'queue.pending': '待认领',
    'queue.claimed': '进行中',
    'queue.blocked': '受阻',
    'queue.completed': '已完成',
    'queue.closed': '已收编',
    
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
    'detail.subtitle': 'Owner 控制台',
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
    'map.updated_prefix': '地图更新于 ',
    'map.popup.updated_prefix': '地图更新于 ',
    
    // Workspace card (overview)
    'card.needs_owner': '等我处理',
    'card.needs_owner_empty': '暂无待处理事项',
    'card.recent_activity': '最近活动',
    'card.more_items': '更多',
    'card.advisor_pending': '待顾问收编',
    'card.advisor_pending_empty': '暂无待收编项',
    'card.advisor_pending_approvals': '已通过待收编',
    'card.advisor_pending_rejects': '已打回待处理',
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
    'vb.audit.none_badge': '免审计',
    'vb.audit.none_title': '本任务卡 audit:none，无需审计，直接从 return 记录起站',
    'vb.audit.none_note': '免审计（audit:none）——以 RETURN 记录为证据。',
    'vb.action.pass': '通过',
    'vb.action.reject': '打回',
    'vb.action.processing': '处理中...',
    'vb.action.verified': '已核验',
    'vb.action.rejected': '已打回',
    'vb.reject.confirm': '确认打回',
    'vb.reject.cancel': '取消',
    'vb.reject.reason_required': '请输入打回理由',
    'vb.reject.reason_placeholder': '请输入打回理由...',
    'vb.success.approved': '已通过核验，记录已写入',
    'vb.success.rejected': '已打回，记录已写入',
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

    // Onboarding guide (AIPOS-286)
    'onboarding.welcome_title': '🎉 欢迎使用 Lybra',
    'onboarding.welcome_intro': '这是你的第一个工作区。让我们开始三步走，从零到第一张任务卡闭环。',
    'onboarding.loading': '加载中...',
    'onboarding.step1_title': '连接你的顾问 Agent',
    'onboarding.step1_body': '复制下方定制接入提示词，粘贴给 Claude / Codex / 任意 AI agent，它就能成为你的顾问（Advisor）——帮你起草任务卡、建议发布、查看真相。',
    'onboarding.copy_prompt': '📋 一键复制接入提示词',
    'onboarding.ssh_reminder': '⚠️ 跨机接入提醒：',
    'onboarding.ssh_reminder_text': '如果你的顾问 agent 与 Lybra 服务端不在同一台机器，请先配置好 SSH 连通性（或网络路由），确保 agent 能访问服务端后再继续。提示词中包含第 0 步检查指引。',
    'onboarding.step2_title': '发布第一张任务卡',
    'onboarding.step2_body': '工作区已为你生成示例任务卡（在 <code>5_tasks/drafts/example-task.md</code>）。复制下方命令发布它，任务就会进入队列等待认领。',
    'onboarding.step2_copy_btn': '📋 一键复制发布命令',
    'onboarding.step3_title': '看它流经 Lybra 的门',
    'onboarding.step3_body': '任务卡发布后，会经历完整的闭环链路：',
    'onboarding.step3_flow_publish': '发布',
    'onboarding.step3_flow_claim': '认领',
    'onboarding.step3_flow_deliver': '交付',
    'onboarding.step3_flow_audit': '审计',
    'onboarding.step3_flow_close': '收编',
    'onboarding.step3_note': '每个环节都有记录（records），Owner 真相视图始终向你呈现真实阶段。发布第一张卡后，刷新页面，向导会自动让位给任务中心。',
    'onboarding.copied': '✅ 已复制',
    'onboarding.copy_failed': '复制失败，请手动复制',

    // Verification bench inline labels (AIPOS-288)
    'vb.inline.what_to_verify': '你要验证的是',
    'vb.inline.preview_label': '内嵌预览',
    'vb.inline.preview_title': '任务预览',
    'vb.inline.tech_details': '技术细节 (验收断言 + 证据 + 操作)',

    // Misc UI alerts/messages (AIPOS-288)
    'alert.operation_failed': '操作失败',
    'alert.network_error': '网络错误',
    'map.stale_badge': '地图已 {days} 天未更新',
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
    'overview.add_project': 'New Project',
    'overview.new_project_modal.title': 'New Project',
    'overview.new_project_modal.intro': 'Start a new parallel project workspace. Choose your setup method:',
    'overview.new_project_modal.project_name': 'Project Name',
    'overview.new_project_modal.project_name_hint': 'Lowercase letters, numbers, dash, or underscore only',
    'overview.new_project_modal.project_name_en': 'English Name (optional)',
    'overview.new_project_modal.project_name_en_hint': 'Displayed in English UI; leave blank to use project name',
    'overview.new_project_modal.option_a': 'Copy Command (Recommended)',
    'overview.new_project_modal.option_a_desc': 'Run this command in your terminal to initialize the workspace:',
    'overview.new_project_modal.option_b': 'Server-Side Init',
    'overview.new_project_modal.option_b_desc': 'Let the server create and register the workspace (requires file write permissions):',
    // AIPOS-293 S4: Option C — Import existing project
    'overview.new_project_modal.option_c': 'Import Existing Project',
    'overview.new_project_modal.option_c_desc': 'Onboard an existing project into Lybra. Preview the structure first, then import — no source files are deleted.',
    'overview.new_project_modal.import_workspace_path': 'Existing Workspace Path',
    'overview.new_project_modal.import_workspace_hint': 'Enter the absolute path to your existing project, e.g. ~/projects/my-app',
    'overview.new_project_modal.preview_import': 'Preview Structure',
    'overview.new_project_modal.confirm_import': 'Confirm Import',
    'overview.new_project_modal.previewing': 'Scanning workspace...',
    'overview.new_project_modal.importing': 'Importing workspace...',
    'overview.new_project_modal.import_success': 'Workspace imported successfully! Refreshing...',
    'overview.new_project_modal.import_path_required': 'Please enter a path',
    'overview.new_project_modal.need_project_name': 'Please enter a project name above',
    'overview.new_project_modal.invalid_name': 'Invalid project name. Use lowercase letters, numbers, dash, or underscore only',
    // AIPOS-293 FIX-1: Dual mode labels
    'overview.new_project_modal.import_mode_directory': 'Project Directory',
    'overview.new_project_modal.import_mode_file': 'Structure File',
    'overview.new_project_modal.import_structure_file_path': 'Structure File Path',
    'overview.new_project_modal.import_structure_file_hint': 'Enter the absolute path to a .yaml structure file, e.g. /tmp/lybra-structure.yaml',
    'overview.new_project_modal.import_structure_file_upload': 'Or upload a structure file:',
    'overview.new_project_modal.import_yaml_hint': 'Detected .yaml path → consider switching to "Structure File" mode above',
    // AIPOS-293 FIX-1: Preview display strings
    'overview.new_project_modal.preview_file_loaded': '✅ Structure file loaded:',
    'overview.new_project_modal.preview_workspace_found': '✅ Workspace found:',
    'overview.new_project_modal.preview_field_project': 'Project',
    'overview.new_project_modal.preview_field_description': 'Description',
    'overview.new_project_modal.preview_field_documents': 'Documents',
    'overview.new_project_modal.preview_field_code_repos': 'Code repos',
    'overview.new_project_modal.preview_field_governance': 'Governance files',
    'overview.new_project_modal.preview_instruction': 'Enter a project name above, then click "Confirm Import" to create the workspace.',
    'overview.new_project_modal.preview_error_prefix': 'Error',
    'overview.new_project_modal.suggest_file_mode_hint': ' — It looks like a structure file. Try switching to "Structure File" mode above.',
    // AIPOS-293 FIX-1: Humanized error messages
    'error.import.path_required': 'Path cannot be empty. Please enter a path and try again',
    'error.import.path_not_exists': 'Path does not exist: {detail}. Please check the path spelling and permissions',
    'error.import.path_not_directory': 'Path is not a directory: {detail}. Please specify a directory path',
    'error.import.path_not_file': 'Path is not a file: {detail}. Please specify a file path',
    'error.import.path_not_yaml': 'File is not YAML format: {detail}. Please use a .yaml or .yml file',
    'error.import.file_read_failed': 'Cannot read file: {detail}. Please check file permissions and encoding',
    'error.import.schema_validation_failed': 'Structure file validation failed: {detail}. Please check the file format matches the schema',
    'error.import.project_id_required': 'Project name cannot be empty. Please enter a project name above',
    'error.import.project_id_invalid': 'Invalid project name format. Use lowercase letters, numbers, dash, or underscore only',
    'error.import.workspace_not_empty': 'Target workspace already exists and is not empty: {detail}. Choose a different name or clear the target directory',
    'error.import.export_failed': 'Export failed: {detail}. Please check the source workspace integrity',
    'error.import.import_failed': 'Import failed: {detail}. Please check the structure file content',
    'error.import.unexpected_error': 'Unexpected error: {detail}. Please check your input and try again; if it persists, report to advisor',
    'overview.copy': 'Copy',
    'overview.copied': 'Copied!',
    'overview.init_now': 'Initialize Now',
    'overview.initializing': 'Initializing workspace...',
    'overview.init_success': 'Workspace created successfully! Refreshing...',
    
    // Workspace status
    'status.ok': 'OK',
    'status.error': 'Error',
    'status.unknown': 'Unknown',
    
    // Queue states
    'queue.pending': 'Pending',
    'queue.claimed': 'In Progress',
    'queue.blocked': 'Blocked',
    'queue.completed': 'Completed',
    'queue.closed': 'Closed',
    
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
    'detail.subtitle': 'Owner Console',
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
    'map.updated_prefix': 'Map updated ',
    'map.popup.updated_prefix': 'Map updated ',
    
    // Workspace card (overview)
    'card.advisor_pending': 'Awaiting Advisor Review',
    'card.advisor_pending_empty': 'No pending items',
    'card.advisor_pending_approvals': 'Approved, awaiting finalization',
    'card.advisor_pending_rejects': 'Rejected, awaiting followup',
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
    'vb.audit.none_badge': 'Audit-exempt',
    'vb.audit.none_title': 'This task has audit:none, no audit required, directly elevated from return record',
    'vb.audit.none_note': 'Audit-exempt (audit:none) — RETURN record serves as evidence.',
    'vb.action.pass': 'Pass',
    'vb.action.reject': 'Reject',
    'vb.action.processing': 'Processing...',
    'vb.action.verified': 'Verified',
    'vb.action.rejected': 'Rejected',
    'vb.reject.confirm': 'Confirm reject',
    'vb.reject.cancel': 'Cancel',
    'vb.reject.reason_required': 'Please enter a rejection reason',
    'vb.reject.reason_placeholder': 'Enter rejection reason...',
    'vb.success.approved': 'Approved, record written',
    'vb.success.rejected': 'Rejected, record written',
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

    // Onboarding guide (AIPOS-286)
    'onboarding.welcome_title': '🎉 Welcome to Lybra',
    'onboarding.welcome_intro': 'This is your first workspace. Let\'s go through three steps to close your first task loop.',
    'onboarding.loading': 'Loading...',
    'onboarding.step1_title': 'Connect Your Advisor Agent',
    'onboarding.step1_body': 'Copy the customized onboarding prompt below and paste it to Claude / Codex / any AI agent, and it will become your advisor—helping you draft task cards, suggest publishing, and view the truth.',
    'onboarding.copy_prompt': '📋 Copy Onboarding Prompt',
    'onboarding.ssh_reminder': '⚠️ Cross-Machine Setup:',
    'onboarding.ssh_reminder_text': 'If your advisor agent and Lybra server are on different machines, please configure SSH connectivity (or network routing) first to ensure the agent can reach the server before proceeding. The prompt includes step-0 connectivity checks.',
    'onboarding.step2_title': 'Publish Your First Task Card',
    'onboarding.step2_body': 'The workspace has generated an example task card for you (in <code>5_tasks/drafts/example-task.md</code>). Copy the command below to publish it, and the task will enter the queue awaiting claim.',
    'onboarding.step2_copy_btn': '📋 Copy Publish Command',
    'onboarding.step3_title': 'Watch It Flow Through Lybra\'s Gate',
    'onboarding.step3_body': 'After publishing, the task will go through the complete closure loop:',
    'onboarding.step3_flow_publish': 'Publish',
    'onboarding.step3_flow_claim': 'Claim',
    'onboarding.step3_flow_deliver': 'Deliver',
    'onboarding.step3_flow_audit': 'Audit',
    'onboarding.step3_flow_close': 'Close',
    'onboarding.step3_note': 'Each step is recorded (in records). The Owner truth view always presents the real stage. After publishing your first card, refresh the page and the guide will give way to the task center.',
    'onboarding.copied': '✅ Copied',
    'onboarding.copy_failed': 'Copy failed, please copy manually',

    // Verification bench inline labels (AIPOS-288)
    'vb.inline.what_to_verify': 'What to verify',
    'vb.inline.preview_label': 'Embedded preview',
    'vb.inline.preview_title': 'Task preview',
    'vb.inline.tech_details': 'Technical details (assertions + evidence + actions)',

    // Misc UI alerts/messages (AIPOS-288)
    'alert.operation_failed': 'Operation failed',
    'alert.network_error': 'Network error',
    'map.stale_badge': 'Map not updated for {days} days',
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
 * Apply translations to DOM elements with data-i18n attributes
 * @param {Element} root - Root element to scan (default: document)
 */
function applyTranslations(root = document) {
  // Handle data-i18n="key" for textContent
  const textElements = root.querySelectorAll('[data-i18n]');
  textElements.forEach(el => {
    const key = el.getAttribute('data-i18n');
    if (key) {
      el.textContent = t(key);
    }
  });
  
  // Handle data-i18n-attr="attrName:key" for attributes (e.g., placeholder:key)
  const attrElements = root.querySelectorAll('[data-i18n-attr]');
  attrElements.forEach(el => {
    const attrSpec = el.getAttribute('data-i18n-attr');
    if (attrSpec) {
      // Support multiple attributes: "placeholder:key1;title:key2"
      attrSpec.split(';').forEach(spec => {
        const [attrName, key] = spec.split(':').map(s => s.trim());
        if (attrName && key) {
          el.setAttribute(attrName, t(key));
        }
      });
    }
  });
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
  
  // Apply translations after language switch
  applyTranslations();
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

// Apply translations on DOMContentLoaded
if (typeof document !== 'undefined') {
  document.addEventListener('DOMContentLoaded', () => {
    applyTranslations();
  });
}

// Export for browser
if (typeof window !== 'undefined') {
  window.i18n = {
    t,
    applyTranslations,
    switchLanguage,
    getCurrentLang,
    createLanguageSwitcher
  };
}
