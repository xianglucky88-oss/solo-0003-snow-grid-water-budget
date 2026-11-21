# 当前数据契约

坐标是米，深度是毫米。每个有效贡献等于交叠面积 × 深度 ÷ 1000。分别记录有效面积、格内缺测和格外区域；目标格独立读源格，流域不是目标格比例摊分。源格和目标格不允许内部重叠，流域可彼此重叠。

结构与字段示例以 `examples/baseline.json` 为准。`changed.json` 是可合法发布的另一个输入；`invalid.json` 是必须拒绝的反例。

独立核对值：all：实测面积 12 m²、水量 3 m³、格内缺测和格外面积各 4 m²；cut：水量 3/2 m³；dry 为零而 miss 为 null。

入口为 `snowbudget/cli.py`，归约编排在 `snowbudget/workflow.py`。有理数输出为整数或 `分子/分母` 字符串；输入的精确非整数应使用 JSON 字符串。不将 `null` 当作零。SQLite 每版保留规范化输入、成果及两者的 SHA-256；这是本地完整性核对，不是数字签名。

## 高程分带（可选段，示例见 `examples/elevation-a.json`）

输入新增三个成对出现的字段：`elevation_bands`（半开区间 `[lo, hi)`，彼此不重叠）、`elevation_schemes`（两套或更多互不对齐的矩形高程格，格内单点高程以 `elevation_m` 给出）、`coverage_choice`（本版唯一选定的主覆盖方案）。三者要么全给，要么全不给；高程不允许 `null`。

每个发布版本只承认一套全流域一致的覆盖次序：主方案的格先铺，其余方案按方案名排序依次尝试，每个补接格先对已覆盖区域做矩形差集裁剪，只在主方案无证据的非重叠部分落地。因此同一位置即便两套高程给出冲突证据，也不存在按海拔带各挑有利版本的可能；片段级 `elevation_scheme`/`elevation_cell` 记录每块证据的出处。

分带片段由源格 × 流域 × 有效覆盖三者相交得到，输出在 `elevation_banding`，四类台账彼此分开并与原流域总表对账：

- 已分带（非零）：带内 `measured_m2` / `water_m3`；
- 已分带零值：带内 `zero_m2` / `zero_water_m3`（深度为 0 的观测，计入有效面积但不产生水量）；
- 有雪水观测但无可用高程：`no_elevation_m2` / `no_elevation_water_m3`（含高程落在所有带之外的情形）；
- 无雪水观测：`unobserved_m2`（源格 `swe_mm` 为 `null`，永不计零、永不计水量）。

`reconciliation` 块给出对账等式：各带（实测+零值）面积加无高程面积等于原流域 `valid_m2`；各带水量加无高程水量等于原流域 `water_m3`；有效面积加无观测面积等于流域面积。两套方案下带间去向可以改变，但原流域总量不变。`elevation-invalid.json` 是必须拒绝的反例（选择了不存在的方案）；`elevation-gap.json` 展示两套方案都没有证据的永久缺口。
