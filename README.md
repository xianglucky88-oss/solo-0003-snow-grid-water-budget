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

高程分带示例（两套不对齐高程格，同一源格跨越高程边界与流域边界；两版各选一套主覆盖，冲突证据不按带混用）：

```sh
python3 -m snowbudget preview examples/elevation-a.json
python3 -m snowbudget preview examples/elevation-b.json
python3 -m snowbudget preview examples/elevation-gap.json
python3 -m snowbudget publish examples/elevation-a.json ledger.sqlite cover-a
python3 -m snowbudget publish examples/elevation-b.json ledger.sqlite cover-b
python3 -m snowbudget replay ledger.sqlite cover-a
python3 -I acceptance/test_elevation_banding.py
```

样本字段及成果口径见 [PROJECT_SPEC.md](PROJECT_SPEC.md)。这里的规则仅用于合成数据，并非真实业务规范。
