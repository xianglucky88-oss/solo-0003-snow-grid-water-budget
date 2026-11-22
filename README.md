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

含 `supply_plan` 的输入额外产出 `supply_feasibility`：在重叠流域责任区之间联立分配达标地面（同一片地至多给一个区，允许轴对齐切分），完整水量与超阈值层水量分账。`examples/supply.json` 可交付并给出互不重叠的条带见证；`examples/unsupply.json` 逐区独立达标但联立不可交付。

```sh
python3 -m snowbudget preview examples/supply.json
python3 -m snowbudget preview examples/unsupply.json
```

样本字段及成果口径见 [PROJECT_SPEC.md](PROJECT_SPEC.md)。这里的规则仅用于合成数据，并非真实业务规范。
