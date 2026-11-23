# 大气反演·同化 论文速递

网页：https://sacinal.github.io/atmos_news/

`data.json` 每天由定时任务自动更新，`feed.xml` 为 RSS 订阅。

## 抓取流程

`tools/fetch_candidates.py` 负责检索候选论文（定时任务每天调用）：

- Crossref 前缀全量：Copernicus/EGU、AGU、AAAS、PNAS、AMS、ESS Open Archive
- Crossref 按 ISSN 全量：约 60 种期刊（Elsevier、Nature 系、IOP、ACS、MDPI、Springer、IEEE、T&F、Frontiers、Wiley 等）
- Crossref 关键词安全网：16 个出版社前缀 × 5 组关键词
- OCO-2 / OCO-3 / XCO2 全收：Crossref 全库检索 + OpenAlex 标题/摘要检索
- arXiv API

脚本先用关键词规则预筛，再由 Claude 逐条判断类别、★核心和标签。

```
python3 tools/fetch_candidates.py --from 2026-11-20 --out /tmp/cand.json
```
