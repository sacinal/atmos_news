"""从 data.json 生成 feed.xml（RSS 2.0），按收录日期取最新 60 条。用法：python3 build_feed.py"""
import json, html, datetime, os
here = os.path.dirname(os.path.abspath(__file__))
d = json.load(open(os.path.join(here, "data.json"), encoding="utf-8"))
CATS = {"carbon":"CO2 / 碳汇反演","methane":"甲烷","fire":"火排放","other":"其他排放反演","obs":"卫星与观测","da":"同化方法"}
SITE = "https://sacinal.github.io/atmos_news/"
items = sorted(d["items"], key=lambda i: (i.get("added", i["date"]), i["date"], i.get("core", False)), reverse=True)[:60]
def rfc822(day):
    dt = datetime.datetime.strptime(day, "%Y-%m-%d").replace(hour=12, tzinfo=datetime.timezone.utc)
    return dt.strftime("%a, %d %b %Y %H:%M:%S +0000")
e = html.escape
out = ['<?xml version="1.0" encoding="UTF-8"?>', '<rss version="2.0"><channel>',
       '<title>大气反演·同化 论文速递</title>', f'<link>{SITE}</link>',
       '<description>大气反演、资料同化、温室气体通量与碳汇方向的每日论文速递</description>', '<language>zh-cn</language>',
       f'<lastBuildDate>{datetime.datetime.now(datetime.timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")}</lastBuildDate>']
for it in items:
    star = "★ " if it.get("core") else ""
    desc = f'{it.get("authors","—")} · {it["venue"]} · {CATS.get(it["cat"], it["cat"])} · {"、".join(it.get("tags", []))}'
    out.append(f'<item><title>{e(star + it["title"])}</title><link>{e(it["url"])}</link><guid isPermaLink="true">{e(it["url"])}</guid>'
               f'<pubDate>{rfc822(it.get("added", it["date"]))}</pubDate><category>{e(CATS.get(it["cat"], it["cat"]))}</category><description>{e(desc)}</description></item>')
out.append('</channel></rss>')
open(os.path.join(here, "feed.xml"), "w", encoding="utf-8").write("\n".join(out))
print("feed.xml:", len(items), "items")
