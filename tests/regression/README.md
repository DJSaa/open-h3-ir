<!--
SPDX-FileCopyrightText: Copyright (c) 2026 Dingo contributors
SPDX-License-Identifier: Apache-2.0
-->

# ContextIR 编译回归

这里保存冻结输入、参考素材、验收要求和显式运行的 HTTP 回归工具。
默认 `pytest` 不调用远程模型；本目录的 `run.py` 必须主动执行。
只编译提示词，不生成视频，不修改部署配置。

## 用例范围

| 分组 | 请求数 | 覆盖内容 |
|---|---:|---|
| `comprehensive` | 48 | 原 F/R 系列普通及专业输入、纯文本/首帧、多语言、对白、音频用途、素材绑定、结构化输入和负向输入 |
| `prompt_quality` | 78 | 39 个场景各含普通描述及 H3 精修输入，覆盖日常物理过程、特殊世界规则及首尾帧 |
| `fixtures` | 6，单列 | 历史输出错误和合法对照，仅供内部校验或人工复核，不发送为用户提示词 |

总计 **126 条请求：121 条正常输入、5 条负向输入**。完整说明、逐项要求位于
`cases/index.json` 及各用例的 `expectations.json`。F04、R06 缺少合格素材，记录为未覆盖。
已有用例 ID 保留；`quality-*-C` 是普通输入经编译，`quality-*-D` 是精修输入经编译。
专业版新增描述不是普通版的隐藏验收要求。

`prompt.txt` 保留原文；`request.json` 分别冻结直接服务请求、Gateway 字段和附件顺序。
测试时不把验收答案、内部检查项或历史输出发给模型，也不人工润色输入。
两条路径具有各自的输入转换，不能仅因用户原话相同就声称上游请求完全相同。

## 离线预检与素材

在仓库根目录执行，使用安装了项目依赖的 Python：

```bash
python tests/regression/validate.py
python -m pytest -q
```

25 份去重素材中有 22 份随仓库保存；3 张鼠标图片的单独素材许可未明确，不重新发布原件。
它们的公开来源、SHA256 和字节数仍保存在 `assets/manifest.json`。
已有合法取得的素材包时，可在本地导入，导入目录已加入 Git 忽略规则：

```bash
python tests/regression/import_assets.py /path/to/reference-assets
python tests/regression/validate.py --require-all-assets
```

导入按完整 SHA256 匹配，不以相似图片替代。缺素材的用例记录 `BLOCKED_ASSET`，不记通过，
也不静默跳过。来源、署名、许可声明和必要的制作记录见 `THIRD_PARTY_NOTICES.md`。
任何路径都不自动下载素材或访问其它环境。

## 显式运行

地址和鉴权只在运行时提供；地址应为服务 origin，不能附加 `/v1`、用户名、密码或查询参数。
可选 `REGRESSION_TOKEN` 从环境读取并放入 Bearer 头，不写入测试输入。
以下域名均为占位值，应替换为有权限访问的服务地址。

```bash
# 直接 ContextIR：5 条冒烟输入，默认串行
python tests/regression/run.py \
  --transport direct --base-url https://compiler.example.com \
  --profile smoke --output tests/regression/outputs/smoke

# 通过 Gateway：只编译，不调用视频生成；公开模型绑定可按部署调整
python tests/regression/run.py \
  --transport gateway --base-url https://gateway.example.com \
  --profile dialogue --model minimax-h3 --ref-model minimax-h3-ref2va

# 全量 126 条，编译 seed 7；可改 seed 单独作稳定性对照
python tests/regression/run.py \
  --transport direct --base-url https://compiler.example.com --seed 7

# 选择单项或分组
python tests/regression/run.py \
  --transport direct --base-url https://compiler.example.com --cases B-R04-A D05 D06
python tests/regression/run.py \
  --transport direct --base-url https://compiler.example.com --suite prompt_quality
```

可用 profile：`smoke`、`dialogue`、`negative`、`ref2va`、`professional`。
不指定 profile、cases 或 suite 时运行全量。默认每次创建独立输出目录；固定输出目录已存在时，
必须用 `--resume`，防止覆盖历史结果。恢复要求地址指纹、路径、用例、seed、模型绑定和输入相同。
Gateway 使用稳定幂等键，已取得任务 ID 时继续查询原任务。
直接服务同步调用失联时标记 `EXECUTION_UNKNOWN`，不盲目重新编译；需核查后使用新的输出目录。
运行中的单次 HTTP 超时由 `--timeout` 配置，默认 900 秒；Gateway 轮询间隔默认 2 秒。

## 结果和判定

每次运行保存逐项输入、验收要求、完整返回、耗时及返回的 `usage`；不估算未返回的 token 数。
运行后自动生成 `index.html` 和 `summary.json`，也可离线重建：

```bash
python tests/regression/report.py tests/regression/outputs/smoke
```

- `PASS_TECHNICAL`：正常请求返回非空 `ready/written`，或 Gateway `completed` 且没有降级警告。
- `DEGRADED`：返回了回退草稿，单独统计；Gateway 正常返回草稿不等于模型改写通过。
- `PASS_REJECTION` / `PASS_CLARIFICATION`：负向输入收到明确输入拒绝或澄清；后端崩溃不算正确拒绝。
- `BLOCKED_ASSET`、超时、协议和传输失败均单列；任何非通过项都会使运行命令非零退出。

技术通过不等于语义通过。人物、动作、场景、台词、禁止项和音色等仍按逐项要求复核，
语义状态默认 `NOT_REVIEWED`。需要视频质量结论时另行生成、听看；此工具不自动触发。
模型生成文本存在变化，不以最终提示词逐字相同作为全量质量通过条件。

`baselines/compiler-20261112.json` 是历史直接调用的精简观察记录，不包含请求 ID、地址、
模型调用日志或完整输出。121 条正常请求当时均为 `ready/written`；N01—N04 仍被错误接受，
记录在 `baselines/known-issues.json`，并未把正确验收标准改为“允许接受”。
N05 空输入当时返回 422。该基线不代表当前 Gateway 路径或视频质量已通过。

## 信息边界

源码及输入不包含真实服务地址、集群/节点/namespace/大区、凭据、部署 YAML 或运行机器路径。
`outputs/` 和 `assets/local/` 不入库。实际运行输出可能包含服务任务 ID、内部错误或模型返回信息，
不要直接强制加入 Git；需要分享时另做脱敏。不要把密钥写入命令行、用例或报告。
