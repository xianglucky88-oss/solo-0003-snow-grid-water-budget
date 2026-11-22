# 当前数据契约

坐标是米，深度是毫米。每个有效贡献等于交叠面积 × 深度 ÷ 1000。分别记录有效面积、格内缺测和格外区域；目标格独立读源格，流域不是目标格比例摊分。源格和目标格不允许内部重叠，流域可彼此重叠。

结构与字段示例以 `examples/baseline.json` 为准。`changed.json` 是可合法发布的另一个输入；`invalid.json` 是必须拒绝的反例。

独立核对值：all：实测面积 12 m²、水量 3 m³、格内缺测和格外面积各 4 m²；cut：水量 3/2 m³；dry 为零而 miss 为 null。

入口为 `snowbudget/cli.py`，归约编排在 `snowbudget/workflow.py`。有理数输出为整数或 `分子/分母` 字符串；输入的精确非整数应使用 JSON 字符串。不将 `null` 当作零。SQLite 每版保留规范化输入、成果及两者的 SHA-256；这是本地完整性核对，不是数字签名。

## 联立补给可行性（可选 `supply_plan`）

输入含 `supply_plan` 时新增 `supply_feasibility` 成果；不含时成果与旧版完全一致，原目标格与流域报表不变。

- `threshold_mm`：雪深门槛。达标地面 = 有观测（`swe_mm` 非 `null`）且深度不低于门槛的地面；门槛为零时零深仍达标，缺测地面永不参与。
- `requirements`：每个参与联立的流域（责任区，允许彼此重叠）给出 `min_qualifying_m2`（最小达标面积）与 `min_complete_m3`（最小完整水量，按全深 `面积 × 深度 ÷ 1000` 计）。
- 同一片达标地面至多安排给一个责任区；以所有责任区矩形边沿做轴对齐切分得到原子片，片内允许任意比例再切。联立可行性是精确有理数线性规划（`Fraction` 两阶段单纯形，Bland 规则），面积与完整水量下限同时满足才可行。
- 完整水量与超阈值层水量（`面积 × (深度 − 门槛) ÷ 1000`）分账记账，互不混用；见证同时给两列。
- 可行成果逐区列出 `assigned_m2`、`assigned_complete_m3`、`assigned_excess_m3` 与 `witness`：每条见证给出原子片 `piece_m`、互不重叠的左到右条带 `strip_m`、源格、深度、面积、完整水量与超阈值水量，条带几何面积等于申报面积。
- 不可行时不出见证，逐区给 `standalone_qualifying_m2`、`standalone_complete_m3`、`standalone_excess_m3` 与 `meets_minimums`，用于表达“逐区独立达标但联立不可交付”；`totals` 给达标并集面积、并集完整/超阈值水量及需求合计。
- 样例：`examples/supply.json` 可交付；`examples/unsupply.json` 两区独立均达标、联立不可交付。输入错误以状态 2 退出，不发布结果。
