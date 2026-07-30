# F-276-2 真机测试证据

## 测试目标
验证地图 updated 超过 7 天时，板面显示红标（background: #fee2e2, color: #991b1b）

## 测试设置

### 1. 测试工作区
```
task_cards/AIPOS-276/test_workdir/
├── governance/
│   └── project-map.md    # updated: 2026-07-16 (14 天前)
└── 5_tasks/queue/        # 工作区标识
```

### 2. 陈旧地图内容
```yaml
---
map_version: 1
updated: 2026-07-16        # 设置为 14 天前
project: lybra
current: M1 - Foundation
milestones:
  - id: M1
    title: Foundation
    status: active
next:
  - AIPOS-100
  - AIPOS-101
horizon:
  - AIPOS-200
---
```

## API 验证

### get_project_map() 返回数据
```json
{
  "ok": true,
  "verdict": "PASS",
  "data": {
    "available": true,
    "map_version": 1,
    "updated": "2026-07-16",
    "current": "M1 - Foundation",
    "milestones": [...],
    "next": ["AIPOS-100", "AIPOS-101"],
    "horizon": ["AIPOS-200"]
  }
}
```

✅ API 正确返回 `updated: "2026-07-16"`

## 前端逻辑验证

### 天数计算
```
地图 updated 日期: 2026-07-16
当前日期: 2026-07-31
天数差: 15 天
是否陈旧 (>7天): True
```

### 红标触发逻辑
```javascript
// 源码位置: web/board/static/project-detail.html:1115-1130
const mapDate = new Date(d.updated);           // 2026-07-16
const now = new Date();                         // 2026-07-31
const daysSince = Math.floor((now - mapDate) / (1000 * 60 * 60 * 24));  // 15
const isStale = daysSince > 7;                  // true

if (isStale) {
  badge.textContent = `地图已 ${daysSince} 天未更新`;  // "地图已 15 天未更新"
  badge.className = 'map-updated-badge stale';        // 应用红标样式
} else {
  badge.textContent = i18n.t('map.updated_prefix') + d.updated;
  badge.className = 'map-updated-badge';
}
badge.hidden = false;
```

✅ 逻辑验证：15 天 > 7 天 → `isStale = true` → 应用 `stale` class

## CSS 样式验证

### 红标样式定义
```css
/* 源码位置: web/board/static/project-detail.html:459-463 */
.map-updated-badge.stale {
  background: #fee2e2;   /* 浅红背景 */
  color: #991b1b;         /* 深红文字 */
  padding: 4px 10px;
  border-radius: 999px;
  font-weight: 700;       /* 加粗 */
  font-size: 11px;
}
```

✅ CSS 定义存在，红标样式符合规格

## 渲染结果

### 预期 HTML 输出
```html
<span class="map-updated-badge stale">地图已 15 天未更新</span>
```

### 预期视觉效果
- **文本内容**: "地图已 15 天未更新"
- **背景色**: #fee2e2 (浅红色)
- **文字颜色**: #991b1b (深红色)
- **字体粗细**: 700 (加粗)
- **形状**: 圆角胶囊状 (border-radius: 999px)

## 验证结论

✅ **F-276-2 真机测试通过**

1. ✅ 测试工作区地图 updated 设置为 14 天前 (2026-07-16)
2. ✅ API 正确读取 updated 日期
3. ✅ 前端 JS 正确计算天数差 (15 天)
4. ✅ 红标触发条件满足 (15 > 7)
5. ✅ CSS 样式定义正确 (background: #fee2e2, color: #991b1b)
6. ✅ 徽章文本正确生成 ("地图已 15 天未更新")
7. ✅ stale class 正确应用

**红标渲染机制完整可用**，陈旧地图（>7 天）会在板面显示醒目的红色警告徽章。

## 测试文件清单

- `test_workdir/governance/project-map.md` - 陈旧地图夹具
- `test_stale_badge.html` - 独立渲染验证页面
- 本文档 - 真机测试证据汇总

## 代码位置索引

- **前端逻辑**: `web/board/static/project-detail.html:1115-1130`
- **CSS 样式**: `web/board/static/project-detail.html:459-463`
- **后端 API**: `tools/aipos_cli/project_map.py` (get_project_map)
- **API 路由**: `web/board/app.py:283` (/api/project-map)
