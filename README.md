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

## 两套错位格网的差额成果

带 `forecasts` 的输入对两套错位格网做差额归约，输出 `difference` 成果：双方都有有效观测（真零算、`null` 不算）的真实交叠区各自积分水量并求有符号差额；单方有证区单独保留面积、水量与源格引用；县级责任区按区内真实交叠直接重判门槛，整流域差额不按面积摊派。

```sh
python3 -m snowbudget preview examples/paired-left.json
python3 -m snowbudget preview examples/paired-right.json
python3 -m snowbudget preview examples/paired-invalid.json   # 责任区越界，退出码 2
python3 -m snowbudget publish examples/paired-left.json ledger.sqlite pair-left
python3 tools/verify_overlap.py
```

两份验收输入的流域共同面积（24 m²）与双方共同水量（各 22/5 m³）完全相同、流域差额均为 0，但共同差额落在不同责任区，门槛判定整体改判。

样本字段及成果口径见 [PROJECT_SPEC.md](PROJECT_SPEC.md)。这里的规则仅用于合成数据，并非真实业务规范。
