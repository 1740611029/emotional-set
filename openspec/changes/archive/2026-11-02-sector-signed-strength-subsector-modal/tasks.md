# Tasks

## 1. 有向强度产物与校验

- [x] 1.1 备份当前 `data/sector-trends.json` 为有向强度迁移前基线并记录 `as_of`，验证基线文件可读取且不会被后续重建覆盖
- [x] 1.2 修改 `pipeline/confirmed_sector_trends.py`：全部四态统一使用 `(range_position + ma_score) / 2 - 50`，范围 -50–+50，零通道为 0，`strength_basis` 不再输出 `inverted`；更新 `pipeline/test_confirmed_sector_trends.py` 覆盖基础分 80 → 30、20 → -30、零通道 → 0 及四态/持续天数不变，并运行该测试文件通过
- [x] 1.3 修改 `pipeline/schemas.py` 的强度范围、`directional` 复算与 `neutral` 校验，更新 `pipeline/test_io_utils.py` 验证合法负值可通过、旧取反公式和旧中性值 50 会被拒绝，并运行相关测试通过
- [x] 1.4 使用与基线相同的原始日线重建板块趋势产物并输出前后对照，验证全部一级/二级板块的 `state`、`days`、分组、历史序列和评估结果不变，所有强度差异均可由既定口径转换解释且新产物通过 Schema 校验

## 2. 前端有向强度展示

- [x] 2.1 更新 `signal-hub-web/src/lib/sectorStates.ts`、`StrengthExplainer.tsx` 与 `Sectors.tsx` 的强度算式、摘要和说明，删除取反及“跨状态不可比”旧文案；更新 `tests/sectorStates.test.ts` 验证正负值、0 中性与构成分复算，并运行 `npm test --prefix signal-hub-web` 通过
- [x] 2.2 将一级板块强度排序改为有符号数值降序，空值和 `data_anomaly` 项置尾，补充排序测试验证不取绝对值、不按状态取反且异常项不会排在有效项之前
- [x] 2.3 更新根 `README.md` 的板块强度范围、统一公式、零通道中性值与重建说明，验证文档示例 80 → 30、20 → -30 与代码公式一致，且不再描述空头/退潮取反

## 3. 二级板块详情弹窗

- [x] 3.1 将 `Sectors.tsx` 的一级 `code` 与二级弹窗 `sub` 选中状态拆开，点击二级行只设置 `sub`，切换一级、筛选或检索时清除 `sub`；抽出可测试的导航状态逻辑并新增测试，验证打开弹窗不改变一级 `code`、关闭只移除 `sub`
- [x] 3.2 新增原生 `<dialog>` 二级详情组件，复用状态标签、父级差异提示、强度解释和 `SectorTrendChart`，按现有浅色 UI 展示二级自身摘要与走势；在浏览器验证背景一级详情和二级列表保持不变，关闭按钮与 Escape 均可恢复原上下文
- [x] 3.3 为一级主详情和二级弹窗使用独立历史请求及 60 / 120 / 250 日范围状态，补充加载、失败、历史不足与数据滞后展示；在浏览器验证弹窗始终使用被点击二级板块的数据、切换范围不改变主图表且不刷新整页
- [x] 3.4 补充弹窗导航与数据选择测试并运行 `npm test --prefix signal-hub-web`，验证二级代码只匹配其父级、未知或失效 `sub` 不展示伪造数据，主详情不会因弹窗打开而改用二级历史

## 4. 联调验收

- [x] 4.1 运行板块流水线相关 Python 回归、`npm test --prefix signal-hub-web` 与 `npm run build --prefix signal-hub-web`，并以新产物启动服务检查快照和历史接口：强度均在 -50–+50、无 `inverted`，四态和历史评估与基线一致
- [x] 4.2 在浏览器完成一级详情、二级弹窗、排序、解释面板和走势图联调，确认收盘实线与 MA60 蓝色虚线保持原含义，历史评估文案未改，且旧 0–100 产物不会与新前端混合交付
