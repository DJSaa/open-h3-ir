# SPDX-FileCopyrightText: Copyright (c) 2026 Dingo contributors
# SPDX-License-Identifier: Apache-2.0
"""Generate an escaped HTML report from saved regression results."""
import argparse
from collections import Counter
from html import escape
import json
from pathlib import Path
from common import ROOT, load, save


def report(output):
    output = Path(output)
    baseline = {c["id"]: c for c in load(ROOT / "baselines/compiler-20261112.json")["cases"]}
    issues = load(ROOT / "baselines/known-issues.json")
    rows = [load(path) for path in sorted((output / "cases").glob("*/result.json"))]
    summary = {"recorded": len(rows), "outcomes": dict(Counter(r["outcome"] for r in rows)),
               "semantic_quality": "NOT_REVIEWED", "video_generation": False,
               "new_normal_technical_failures": [r["id"] for r in rows if r["id"] not in issues and baseline.get(r["id"], {}).get("written_ready") and r["outcome"] not in ("PASS_TECHNICAL", "BLOCKED_ASSET")],
               "blocked_assets": [r["id"] for r in rows if r["outcome"] == "BLOCKED_ASSET"]}
    save(output / "summary.json", summary)
    page = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>ContextIR 编译回归</title>',
            '<style>body{font:16px sans-serif;margin:24px;line-height:1.6}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:8px}pre{white-space:pre-wrap;overflow-wrap:anywhere}details{margin:12px 0}</style>',
            '<h1>ContextIR 编译回归</h1><p>技术通过不等于语义或视频质量通过。降级、缺素材及已知负向输入问题单列，未隐藏为成功。历史基线仅为直接调用；Gateway 的差异不能自动归因于编译器。</p>',
            '<pre>'+escape(json.dumps(summary, ensure_ascii=False, indent=2))+'</pre>',
            '<table><tr><th>用例</th><th>本次结果</th><th>历史直接调用</th><th>耗时/秒</th><th>已知问题</th></tr>']
    for row in rows:
        cid = row["id"]
        old = baseline.get(cid, {})
        page.append('<tr><td>'+escape(cid)+'</td><td>'+escape(row['outcome'])+'</td><td>'+escape(str(old.get('upstream_status')))+' / '+escape(str(old.get('source')))+'</td><td>'+str(row.get('elapsed_s', ''))+'</td><td>'+escape(issues.get(cid, ''))+'</td></tr>')
    page.append('</table>')
    for row in rows:
        cid = row["id"]
        page.append('<h2>'+escape(cid)+'</h2>')
        for filename, title in [('request.json', '输入'), ('body.json', '本次输出'), ('expectations.json', '验收要求'), ('result.json', '判定与耗时')]:
            path = output / "cases" / cid / filename
            if path.is_file():
                page.append('<details><summary>'+title+'</summary><pre>'+escape(path.read_text())+'</pre></details>')
    (output / 'index.html').write_text(''.join(page)+'</html>', encoding='utf-8')
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output', type=Path)
    print(json.dumps(report(parser.parse_args().output), indent=2))
