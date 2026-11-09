# Tasks

## 1. 板块走势图例说明

- [x] 1.1 在 `signal-hub-web/src/components/SectorTrendChart.tsx` 增加可见图例，将 MA60 系列标为“MA60（60日均线）”并保留“收盘”，调整图例与绘图区布局避免遮挡；在浏览器分别切换 60 / 120 / 250 日，验证图例含义、蓝色虚线样式和原有收盘线/状态色带一致
- [x] 1.2 验证 MA60 不足 60 个有效收盘价时仍无补值且图例说明保留，运行 `npm test --prefix signal-hub-web` 与 `npm run build --prefix signal-hub-web`，确认现有移动平均计算、悬停读数和历史长度降级测试及构建均通过
