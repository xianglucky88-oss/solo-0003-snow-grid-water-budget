# 格网雪水量守恒归约

在合成等面积矩形网格上，从相同源格独立构建目标格网和流域成果。 使用合成资料。无需联网、账户或第三方 Python 依赖；已验证 Python 3.14。

```sh
python3 -m snowbudget preview examples/baseline.json
python3 -m snowbudget publish examples/baseline.json ledger.sqlite original
python3 -m snowbudget publish examples/changed.json ledger.sqlite revised
python3 -m snowbudget history ledger.sqlite
python3 -m snowbudget show ledger.sqlite original
python3 -m snowbudget source ledger.sqlite original
python3 -m snowbudget replay ledger.sqlite revised
```

`preview` 不写库；`show` 读已发布成果，`source` 读随版封存的输入，`replay` 重算并核对。`publish` 不覆盖已用版名。命令在输入错误时以状态 2 退出，不发布结果。重复演示请换版名或用新库。

样本字段及成果口径见 [PROJECT_SPEC.md](PROJECT_SPEC.md)。这里的规则仅用于合成数据，并非真实业务规范。

## 守恒验收

`tests/fixtures/handgrid.json` 是小型手算格网：源格 `a`=100 mm、`b`=实测 0 mm、`f1`/`f2`=同一 4 m²/300 mm 有效格拆成的两块同值碎片、`m`=缺测；目标格既有压在格内/缺测上的，也有整个落在格外；流域 `shed-left` 与 `shed-right` 彼此重叠，且两者名义面积之和恰好等于 `domain` 的名义面积。

`tests/acceptance.py` 只用归档输入、成果自带几何和各自面积口径对账，不调用归约器：

- 流域以 `valid + missing + outside = area_m2` 为口径，目标格以自身 `box_m` 面积为 `coverage` 口径；
- 逐格台账（`contributions` 的 cell/m²/m³）对归档源深重算，单格少算被其他格多算抵消时总量虽不变仍会报出；
- 面积归一化（均值、coverage 的除数）与重叠流域总量当区域总量分开归类。

```sh
python3 -m unittest discover -s tests -v
```

测试同时跑预览与封存后的 `show`/`source`，用同一手算表核对两份产物，并对每个变异体（抵消型单格错、归一化错、重叠流域总量、碎片重复计数、零/缺测互串、格外当零）断言其被对应类别捕获。
