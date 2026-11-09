# Design

## Context

`SubsectorDialog`（`signal-hub-web/src/pages/Sectors.tsx`）使用原生 `<dialog>` + `showModal()`，是项目中唯一的弹窗实现。项目样式来自 Tailwind v4（`src/index.css` 首行 `@import 'tailwindcss'`），其 preflight 会重置浏览器对 `dialog` 的默认样式，破坏 UA 样式表依赖 `margin: auto` 的居中效果，因此模态弹窗渲染在视口左上角。现有 className 已声明 `w-[min(960px,calc(100vw-2rem))]`、`max-h-[90vh]` 与 `backdrop:bg-slate-900/35`，未声明任何定位。动机见 proposal.md，行为要求见 specs/sector-trend-explorer/spec.md。

## Goals / Non-Goals

**Goals:**
- 弹窗打开后在视口水平、垂直居中，窗口缩放与内容高度变化时不溢出视口。
- 保持现有交互不变：遮罩点击关闭、Escape 关闭、内部滚动区、点击事件依赖 `event.target === currentTarget` 判定遮罩点击。

**Non-Goals:**
- 不重构为组件库弹窗或 portal 方案；不新增全局 dialog 样式类；不改其他页面与任何数据/信号逻辑。

## Decisions

- **决策 1：用 Tailwind 工具类显式恢复居中，而不是回退 UA 样式。**
  在 `<dialog>` 的 className 上追加 `fixed inset-0 m-auto`：
  - `fixed inset-0` 显式给出模态定位上下文（不依赖 UA 表里 position 的优先级，避免类似重置再次破坏）；
  - `m-auto` 配合已有限的 `width` / `max-height` 实现双向居中；
  - 保持纯工具类写法，与项目现有 UI 风格一致性规范（AGENTS.md §13）相符。
  - 备选 A：只加 `m-auto`——依赖 UA 的 `position: fixed` 未被重置，通常可用，但居中仍建立在"UA 样式恰好没被其他 reset 破坏"之上，显式定位更稳。
  - 备选 B：`left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2`——同样居中，但 transform 与弹窗内 `max-h`/滚动组合可读性较差，且宽/高是"内容自适应 + 上限"场景，`m-auto` 语义更直接。
- **决策 2：不改动点击遮罩关闭逻辑。** `fixed inset-0` 后 dialog 盒子仍与视口同尺寸、内容通过 `m-auto` 收缩居中，空白区域点击依然命中 `dialog` 本身，`event.target === event.currentTarget` 的判定继续有效，无需改成 backdrop 监听。
- **决策 3：内层滚动结构维持现状。** 外层 `max-h-[90vh]` + 内层 `max-h-[90vh] overflow-y-auto` 已保证高内容可滚动且不超视口，本次不调整。

## Risks / Trade-offs

- [Tailwind `inset-0` 与 `m-auto` 的层叠结果依赖宽度上限生效] → 验证时覆盖宽窗口、窄窗口（<1000px）、250 日高内容三种情形，确认居中且不溢出。
- [`fixed inset-0` 使 dialog 盒子铺满视口，理论上边缘点击行为范围变大] → 与现状（同样铺满的 UA 模态盒子）一致，回归验证遮罩点击关闭与 Escape 关闭即可。

## Migration Plan

单文件样式改动，随前端正常构建部署；回滚即移除追加的三个工具类。
