# Proposal

## Why

板块趋势页的二级板块详情弹窗（`SubsectorDialog`）当前显示在页面左上角，而不是居中于视口。原因是项目使用 Tailwind v4（`@import 'tailwindcss'` 含 preflight），preflight 会重置浏览器对 `dialog` 元素的默认居中样式（`margin: auto` 等），而弹窗现有 className 只声明了宽高与外观、未声明定位，导致模态弹窗落在视口左上角，影响阅读与视觉一致性。

## What Changes

- 修正 `SubsectorDialog` 的定位样式，使二级板块详情弹窗以模态方式打开时居中于视口（水平与垂直居中），并与现有的 `w-[min(960px,calc(100vw-2rem))]` / `max-h-[90vh]` 尺寸约束兼容。
- 弹窗其余行为不变：遮罩（`backdrop`）、点击遮罩关闭、Escape 关闭、内容结构与数据口径均保持原样。
- 不改动任何信号计算、数据获取或后端逻辑。

## Capabilities

### New Capabilities

（无新增能力）

### Modified Capabilities

- `sector-trend-explorer`：修改"二级板块详情弹窗"需求，补充弹窗打开时的定位行为要求——弹窗 MUST 居中于视口显示（水平与垂直方向），且在内容高度变化与不同窗口尺寸下保持不超出视口。

## Impact

- 受影响代码：`signal-hub-web/src/pages/Sectors.tsx` 中 `SubsectorDialog` 组件的 `<dialog>` className（纯样式修改）。
- 无 API、数据口径、信号算法变更；不涉及 `data/` 与 `pipeline/`、`server/`。
- 验证方式：dev 服务器打开板块页点击二级板块，目测弹窗居中；小窗口与内容滚动场景下不溢出视口。
