# Tasks

## 1. 图表排序纯函数

- [x] 1.1 在 `signal-hub-web/src/lib/` 新增图表布局模块：定义 `DASHBOARD_INDEX_CHART_ORDER`（按 code：sh000001、sh000688、sz399006、sz399001、sh000300、sh000905、sh000852）与纯函数 `orderIndicesForCharts(indices)`，返回排序后的新数组，未知 code 按原相对顺序排末尾，且不修改入参数组
- [x] 1.2 在 `signal-hub-web/tests/` 新增对应单测（node --test 模式），覆盖：目标顺序正确、快照缺项时只渲染现有项、未知 code 兜底排末尾、入参不被就地修改；`npm test` 通过

## 2. 指标总览接入四行布局

- [x] 2.1 修改 `signal-hub-web/src/pages/Dashboard.tsx`：图表主区遍历 `orderIndicesForCharts(snapshot.indices)` 的结果渲染，跨列条件从 `index === 6` 改为排序后首张（`index === 0`，上证指数）加 `md:col-span-2`，其余卡片不跨列；容器保持 `grid grid-cols-1 md:grid-cols-2 gap-6`
- [x] 2.2 运行 `npm run build`（在 `signal-hub-web/` 下）确认 TypeScript 编译与打包通过

## 3. 集成验证

- [x] 3.1 启动本地服务在桌面宽度（≥md 断点）核对【指标总览】四行分组：第一行上证指数横跨整行，第二行科创50+创业板指，第三行深证成指+沪深300，第四行中证500+中证1000；每张卡的名称、收盘价、涨跌幅与自身数据对应
- [x] 3.2 缩窄窗口验证移动端单列按上证指数 → 科创50 → 创业板指 → 深证成指 → 沪深300 → 中证500 → 中证1000 顺序堆叠，无横向挤压或裁切；确认时间范围下拉框仍同步作用于全部 7 张图
