# 当前数据契约

坐标是米，深度是毫米。每个有效贡献等于交叠面积 × 深度 ÷ 1000。分别记录有效面积、格内缺测和格外区域；目标格独立读源格，流域不是目标格比例摊分。源格和目标格不允许内部重叠，流域可彼此重叠。

## 采信表

同一时次分块瓦片允许在接缝重叠，但重叠双方都必须给出整数 `evidence` 等级（数值越大越强）。只按足迹判决：先把区域沿所有像元边界和撤回边界切成互不重叠的原子足迹，再在每个足迹内按 `evidence` 选最强等级。同级而值不同即冲突，冲突足迹不计有效面积、不计水量，也不求平均。缺 `evidence` 的重叠仍按旧规则拒绝（`overlapping grid cell`），因此无重叠历史保持原读法。

## 局部撤回

可选 `retractions` 列出 `{id, box_m}`；省略 `box_m` 表示撤回整块。撤回只移除该像元被覆盖的那部分面积。被撤回的高等级像元若其下还有未撤回的较低等级证据，就由低等级补位；同级冲突中撤回一方后，另一方直接接管。撤回不得越出该像元自身的范围。

## 流域分类

有接缝或撤回时，流域按足迹分为互斥且可加总的类别，并各自列出 `footprints`（足迹）：`unique`（无替代证据）、`replaceable`（可用但另有同级或更低等级证据可替换）、`backfilled`（撤回后由更低等级补位）、`resolved`（撤回后同级剩余一方接管）、`contested`（同级不同值，不可采信）、`no_evidence`（格内缺测或被撤回后无证据）、`unsurveyed`（格外区域）。旧字段 `valid_m2`、`water_m3` 不变，且 `valid_m2 + missing_in_grid_m2 + retracted_m2 + conflict_m2 + outside_grid_m2 = area_m2`；`contributions` 仍为逐像元汇总，`retracted_cells` 列出被撤回后无补位的像元。

结构与字段示例以 `examples/baseline.json` 为准。`changed.json` 是可合法发布的另一个输入；`invalid.json` 是必须拒绝的反例。接缝样例见 `examples/seam.json`，仅撤回同级冲突一方、其余接缝与独有区域不变的样例见 `examples/withdrawn.json`。

独立核对值：all：实测面积 12 m²、水量 3 m³、格内缺测和格外面积各 4 m²；cut：水量 3/2 m³；dry 为零而 miss 为 null。

接缝样例（`seam.json`）：all 面积 52 m²、有效 36 m²、水量 10 m³、冲突 8 m²、缺测 8 m²。
撤回样例（`withdrawn.json`）：`seam` 目标格冲突由 4 m² 降为 0、水量由 1 m³ 变为 5/2 m³；all 有效 40 m²、水量 23/2 m³，仅该接缝改变。

入口为 `snowbudget/cli.py`，归约编排在 `snowbudget/workflow.py`，采信表在 `snowbudget/acceptance.py`。有理数输出为整数或 `分子/分母` 字符串；输入的精确非整数应使用 JSON 字符串。不将 `null` 当作零。SQLite 每版保留规范化输入、成果及两者的 SHA-256；这是本地完整性核对，不是数字签名。
