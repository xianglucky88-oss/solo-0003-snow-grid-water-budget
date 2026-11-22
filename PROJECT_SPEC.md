# 当前数据契约

坐标是米，深度是毫米。每个有效贡献等于交叠面积 × 深度 ÷ 1000。分别记录有效面积、格内缺测和格外区域；目标格独立读源格，流域不是目标格比例摊分。源格和目标格不允许内部重叠，流域可彼此重叠。

结构与字段示例以 `examples/baseline.json` 为准。`changed.json` 是可合法发布的另一个输入；`invalid.json` 是必须拒绝的反例。

独立核对值：all：实测面积 12 m²、水量 3 m³、格内缺测和格外面积各 4 m²；cut：水量 3/2 m³；dry 为零而 miss 为 null。

入口为 `snowbudget/cli.py`，归约编排在 `snowbudget/workflow.py`。有理数输出为整数或 `分子/分母` 字符串；输入的精确非整数应使用 JSON 字符串。不将 `null` 当作零。SQLite 每版保留规范化输入、成果及两者的 SHA-256；这是本地完整性核对，不是数字签名。

## 独占申报视图

输入可带可选的顶层 `declaration`（字段示例见 `examples/claims_alpha.json`）：`parent_basin` 指向一个已声明流域，`sub_basins` 列出与其重叠的子区，`certificates` 为正式唯一归属凭证，每条含 `id`、持有子区 `basin` 与矩形 `box_m`；凭证只覆盖源格的一部分、落在缺测格或格外都允许，但必须同时位于母区与持证子区内，且凭证之间不得重叠。非法申报以状态 2 拒绝。

视图直接切分母区**几何**（源格、子区、凭证的全部边形成碎片），绝不拿子区报表相减填平重叠。每块碎片按实测状态与凭证归入四部分：

- `exclusive`（逐子区）：被该子区持有的凭证覆盖的**有实测**碎片；
- `disputed`（子区争议）：无凭证且同时落入两个及以上子区的有实测碎片；
- `remainder`（母区剩余）：无凭证且至多一个子区触及的有实测碎片；
- `unobserved`（无雪水观测）：格内缺测与格外碎片，凭证落在其中也不产生独占水量。

每部分都带 `valid_m2`、`water_m3`、`valid_mean_mm`、`missing_in_grid_m2`、`outside_grid_m2`、`total_m2`、逐源格 `contributions` 与 `missing_cells`；无实测时水量为 `null`。`reconciliation` 同时给出各部分之和与独立按几何重算的母区口径（总面积、有证实测面积与水量、缺测、格外），并以 `reconciles` 布尔值确认二者相等。

`claims_alpha.json` 与 `claims_bravo.json` 几何完全相同，仅把同一批凭证的持有子区从 alpha 换给 bravo：两份成果的母区总量、争议、剩余、无观测部分及各自原有流域报表逐项相同，只有独占额在子区间移动；独占面积与水量的合计保持不变。不带 `declaration` 的输入成果形状与本视图加入前完全一致。
