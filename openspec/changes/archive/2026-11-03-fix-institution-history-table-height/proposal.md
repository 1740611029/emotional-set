# Proposal

## Why

「机构多空单」页面历史明细拆分为三个独立列表后，三张表格高度随各自行数变化，长短不一，页面纵向被拉得很长，三表之间难以对齐比较。用户要求给三个历史明细表格统一固定高度 250px。

## What Changes

- 三个历史明细表格（国泰君安 / 中信期货 / 其他机构）的表格区域统一固定高度 250px，超出部分在表格区域内纵向滚动，表头在滚动时保持可见
- 行数不足填满 250px 时表格区域仍保持 250px 高度；空状态区域同样保持 250px，保证三个列表视觉对齐
- 分页逻辑不变（每页 50 行），固定高度只影响单页可视区域
- 不影响数据接口、筛选、分页数量与其他页面区域

假设（用户未逐项确认，按常规做法推定）：

- 固定高度作用于表体滚动容器（含表头粘性定位），而非裁切行数
- 空状态提示在 250px 区域内居中展示

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `institution-position-view`: 修改「历史明细列表」需求——三个独立列表的表格区域 MUST 固定高度 250px，超出内容区域内部滚动且表头保持可见

## Impact

- 前端：`signal-hub-web/src/pages/InstitutionPositions.tsx`（历史明细表格容器样式）
- 数据接口 / 后端 / 信号计算：无影响
- 规范：`openspec/specs/institution-position-view/spec.md` 的「历史明细列表」需求更新
