# 格网雪水量守恒归约

在合成等面积矩形网格上，从相同源格独立构建目标格网和流域成果。 使用合成资料。无需联网、账户或第三方 Python 依赖；已验证 Python 3.14。

```sh
python3 -m snowbudget preview examples/baseline.json
python3 -m snowbudget publish examples/baseline.json ledger.sqlite original
python3 -m snowbudget publish examples/changed.json ledger.sqlite revised
python3 -m snowbudget publish examples/occlusion.json ledger.sqlite occlusion
python3 -m snowbudget history ledger.sqlite
python3 -m snowbudget show ledger.sqlite original
python3 -m snowbudget source ledger.sqlite original
python3 -m snowbudget replay ledger.sqlite revised
python3 -m unittest discover -s tests
```

`preview` 不写库；`show` 读已发布成果，`source` 读随版封存的输入，`replay` 重算并核对。`publish` 不覆盖已用版名。命令在输入错误时以状态 2 退出，不发布结果。重复演示请换版名或用新库。

除了整格源数据，源格还可给出位置确切的遮挡矩形，另可带独立来源的局部复测补片；补片只填原值不可用的地面，重叠冲突留作争议区，只知比例的遮挡不会被摊到流域。规则见 [PROJECT_SPEC.md](PROJECT_SPEC.md)。`examples/occlusion.json` 用两个遮挡面积相同、补片与流域交界位置不同的源格演示这些口径。

样本字段及成果口径见 [PROJECT_SPEC.md](PROJECT_SPEC.md)。这里的规则仅用于合成数据，并非真实业务规范。
