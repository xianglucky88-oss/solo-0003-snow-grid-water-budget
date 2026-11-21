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

## 三处蓄水区离线推演

`examples/reservoir.json` 在同一流程上追加三处蓄水区（互不重叠责任区、起始可用库容、无环单出口拓扑），入流只取有证雪水，实测零与未观测分开。缺测补量上限按源带共享发放；多库同时溢流主张由精确有理数线性规划判定能否由同一份补量支持，并输出可前向核对守恒的见证。

```sh
python3 -m snowbudget preview examples/reservoir.json
python3 -m snowbudget publish examples/reservoir.json ledger.sqlite reservoirs
PYTHONPATH=. python3 scripts/check_shared_budget.py
PYTHONPATH=. python3 -m unittest discover -s tests -v
python3 -m snowbudget preview examples/reservoir_invalid.json   # 状态 2：责任区重叠
```

样本字段及成果口径见 [PROJECT_SPEC.md](PROJECT_SPEC.md)。这里的规则仅用于合成数据，并非真实业务规范。
