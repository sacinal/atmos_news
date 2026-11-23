"""抓取候选论文（Crossref + OpenAlex + arXiv），做关键词预筛，输出候选 JSON 供人工/Claude 分类。

用法：
  python3 tools/fetch_candidates.py --from 2026-11-20 [--until 2026-11-23] --out /tmp/cand.json

输出：{"window":[from,until],"log":[每条检索的状态], "candidates":[{doi,url,title,venue,date,authors,abstract,xco2,score,hits,sources}]}
  - 已在 data.json 里的条目（按 DOI / 标题）会被排除。
  - xco2=true 的条目（标题或摘要提到 XCO2 / OCO-2 / OCO-3 等）按用户要求必须全部收录。
只依赖标准库。需要能直连 api.crossref.org / api.openalex.org / export.arxiv.org。
"""
import argparse, json, os, re, sys, time, html, urllib.parse, urllib.request, datetime
import xml.etree.ElementTree as ET

MAILTO = "sacinal@users.noreply.github.com"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

# ---- 来源 ----
# 1) 整个前缀全量抓（地学专业出版社 / 顶刊出版社，量可控）
FULL_PREFIXES = {
    "10.5194": "Copernicus/EGU（ACP AMT GMD ESSD BG ESD EGUsphere…）",
    "10.1029": "AGU（GRL JGR GBC AGU Advances JAMES Earth's Future…）",
    "10.1126": "AAAS（Science, Science Advances）",
    "10.1073": "PNAS",
    "10.1175": "AMS（MWR JAS JCLI JAMC BAMS…）",
    "10.22541": "ESS Open Archive",
}
# 2) 按 ISSN 全量抓的期刊
ISSNS = {
    # Elsevier
    "1352-2310": "Atmospheric Environment", "0034-4257": "Remote Sensing of Environment",
    "0169-8095": "Atmospheric Research", "0048-9697": "Science of the Total Environment",
    "0168-1923": "Agricultural and Forest Meteorology", "0924-2716": "ISPRS J Photogramm",
    "1569-8432": "Int J Appl Earth Obs Geoinf", "2095-9273": "Science Bulletin",
    "0269-7491": "Environmental Pollution", "1309-1042": "Atmospheric Pollution Research",
    "1001-0742": "J Environ Sci", "2590-3322": "One Earth", "0273-1177": "Advances in Space Research",
    "0022-4073": "JQSRT", "1470-160X": "Ecological Indicators", "2212-0955": "Urban Climate",
    "2666-0172": "Science of Remote Sensing", "2590-1621": "Atmospheric Environment: X",
    # Wiley（非 AGU）
    "0035-9009": "QJRMS", "1354-1013": "Global Change Biology", "1530-261X": "Atmospheric Science Letters",
    # Nature 系
    "0028-0836": "Nature", "1752-0894": "Nature Geoscience", "1758-678X": "Nature Climate Change",
    "2041-1723": "Nature Communications", "2662-4435": "Communications Earth & Environment",
    "2397-3722": "npj Climate and Atmospheric Science", "2052-4463": "Scientific Data",
    "2045-2322": "Scientific Reports", "2398-9629": "Nature Sustainability",
    # IOP
    "1748-9326": "Environmental Research Letters", "2515-7620": "Environmental Research Communications",
    "2752-5295": "Environmental Research: Climate", "2752-664X": "Environmental Research: Ecology",
    # ACS
    "0013-936X": "ES&T", "2328-8930": "ES&T Letters", "2837-1402": "ACS ES&T Air", "2472-3452": "ACS Earth Space Chem",
    # MDPI
    "2072-4292": "Remote Sensing", "2073-4433": "Atmosphere",
    # Springer / BMC
    "0256-1530": "Advances in Atmospheric Sciences", "1674-7313": "Science China Earth Sciences",
    "2095-6037": "J Meteorol Res", "1750-0680": "Carbon Balance and Management",
    # IEEE
    "0196-2892": "IEEE TGRS", "1939-1404": "IEEE JSTARS", "1545-598X": "IEEE GRSL",
    # Taylor & Francis
    "0143-1161": "Int J Remote Sens", "2096-4471": "Big Earth Data", "2150-704X": "Remote Sensing Letters",
    "1674-2834": "Atmos Oceanic Sci Lett",
    # Frontiers
    "2673-6187": "Front Remote Sens", "2296-6463": "Front Earth Sci", "2296-665X": "Front Environ Sci",
    "2624-893X": "Front For Glob Change",
    # 其他
    "2694-1589": "Journal of Remote Sensing", "1600-0889": "Tellus B", "2095-5138": "National Science Review", "1094-4087": "Optics Express",
}
# 3) 关键词安全网（按相关度取前 N 条），覆盖上面没列到的期刊
NET_PREFIXES = ["10.1016", "10.1002", "10.1111", "10.1007", "10.1080", "10.3390", "10.1109", "10.1021",
                "10.1088", "10.3389", "10.1038", "10.1093", "10.1186", "10.34133", "10.1364", "10.1117"]
NET_QUERIES = [
    "atmospheric inversion emissions fluxes CO2 methane top-down",
    "carbon sink land ocean budget atmospheric CO2 oxygen",
    "XCO2 OCO-2 OCO-3 GOSAT TROPOMI TanSat satellite greenhouse gas retrieval",
    "fire emissions biomass burning wildfire carbon",
    "ensemble Kalman variational data assimilation atmospheric",
]
# 4) XCO2 / OCO 全收：不限前缀
XCO2_QUERIES = ["XCO2 OCO-2 OCO-3 Orbiting Carbon Observatory",
                "column-averaged CO2 satellite XCO2 retrieval",
                "OCO-2 solar-induced fluorescence SIF"]
OPENALEX_SEARCH = ["XCO2", "OCO-2", "OCO-3", "Orbiting Carbon Observatory"]

# ---- 预筛 ----
R = lambda p: re.compile(p, re.I)
# XCO2/OCO 大小写敏感（避免把化学里的 xCO2 摩尔分数当成 XCO2）
XCO2_RE = re.compile(r"XCO\s?2|XCO₂|X_?\{?CO2|\bOCO[- ‐‑]?[23]\b|Orbiting Carbon Observatory|[Cc]olumn[- ‐]averaged (dry[- ‐]air )?(mole fraction|CO\s?2|carbon dioxide)")
GHG = R(r"\bCO\s?2\b|CO₂|carbon dioxide|\bCH\s?4\b|CH₄|methane|\bN\s?2\s?O\b|N₂O|nitrous oxide|carbon monoxide|greenhouse gas|\bGHGs?\b|"
        r"carbon (sink|source|budget|cycle|flux|uptake|balance|storage|loss|neutrality)|net ecosystem|\bNEE\b|\bNBP\b|\bGPP\b|gross primary|"
        r"solar[- ]induced|\bSIF\b|O2/N2|\bAPO\b|radiocarbon|14CO2|Δ14C|fossil[- ]fuel CO|\bXCH4|\bXCO\b|terrestrial carbon|ocean carbon|land carbon|"
        r"\bCOS\b|carbonyl sulfide|\bOCS\b")
# 标题里必须出现的“大气/通量/观测”类词
METHOD = R(r"invers|assimilat|\bflux|budget|\bsinks?\b|satellite|retriev|remote sens|spaceborne|TROPOMI|Sentinel|GOSAT|TanSat|IASI|MOPITT|EMIT|"
           r"GHGSat|Carbon ?Mapper|MethaneSAT|PRISMA|EnMAP|TCCON|COCCON|ICOS|aircraft|airborne|drone|UAV|tower|eddy|atmospher|transport model|"
           r"GEOS|WRF|CMAQ|STILT|FLEXPART|TM5|CarbonTracker|CAMS|GCAS|plume|top-down|isotop|concentration|mole fraction|mixing ratio|"
           r"observ|measur|monitor|enhancement|hotspot|super-?emitter|leak|point source|wetland|permafrost|peatland|ocean|sea|terrestrial|"
           r"ecosystem|forest|land|global|regional|urban|city|cities|anthropogenic|emission inventor|sources and sinks|trend|growth rate|"
           r"interannual|drought|El Ni|model|simulat|uptake|exchange|lidar|spectromet|sensor|in situ|network|station")
DA = R(r"data assimilation|assimilating|assimilation (system|scheme|experiment|method|framework|cycl|window|of (satellite|observ|radar|radiance|in[- ]situ|GNSS|ocean|SMAP|soil|snow|aerosol|AOD|chemical|emission|TROPOMI|lidar|wind|Doppler|precip|cloud|all-sky|IASI|AMSU|surface|XCO2|CO2|methane))|ensemble Kalman|\bEnKF|\bLETKF|\bEnVar|4D-?Var|3D-?Var|particle filter|observation impact|\bOSSEs?\b|"
       r"inverse model|Bayesian inversion|source inversion|emission inversion|top-down estimat|flux inversion|atmospheric inversion|reanalysis")
DA_CTX = R(r"WRF|GEOS|CMAQ|TROPOMI|IASI|\bCO\b|CO2|methane|NO2|-Chem|\bmodel|atmospher|weather|forecast|meteorolog|ocean|climate|chemical|emission|aerosol|ozone|carbon|land surface|soil moisture|hydrolog|precipitation|radar|satellite|NWP|reanalysis")
FIRE = R(r"\bfires?\b|wildfires?|biomass burning|burned area|burnt area|\bsmoke|peat ?fires?|pyrocumul|bushfire")
FIRE2 = R(r"emission|carbon|\bCO2|\bCO\b|PM2\.5|aerosol|FRP|radiative power|inventor|GFED|GFAS|QFED|FINN|plume|black carbon|brown carbon|"
          r"satellite|air quality|atmospher|transport|injection|pollut|ozone|trace gas|budget")
TRACE = R(r"\bNOx\b|\bNO2\b|\bSO2\b|ammonia|\bNH3\b|HCHO|formaldehyde|halocarbon|\bHFCs?\b|\bSF6\b|\bCFCs?\b|hydrogen \(H2\)|atmospheric hydrogen|isoprene|\bVOCs?\b")
TRACE2 = R(r"invers|top-down|satellite|TROPOMI|flux|emission inventor|emissions? estimat|constrain|source attribution|atmospheric observ")
BAD_TITLE = R(r"supplement|^editorial board|front matter|^index$|^BM$|^issue information|table of contents|^erratum|^correction|"
              r"^corrigendum|retraction|^reply to|^comment on|^author response|^review of|cover image|^front cover|^back cover")
CHEM = R(r"electro(chemical )?reduction|electrocatal|photocatal|catalys|hydrogenation|methanation|reforming|\bMOFs?\b|metal[- ]organic|"
         r"membrane|adsorb|sorbent|battery|electrolys|direct air capture|mineraliz|concrete|cement|mortar|aquifer|enhanced oil|"
         r"oil recovery|coal seam|coalbed|shale|hydrate|anaerobic digest|biogas|wastewater|sewage|sludge|bioreactor|biochar|compost|"
         r"combustion engine|diesel engine|gas turbine|burner|flame|supercritical|solar cell|perovskite|nanosheet|electrode|fuel cell|zeolite|"
         r"cattle|dairy|rumen|enteric|livestock diet|feed additive|manure|"
         r"econom|GDP|financ|\bpolic(y|ies)\b|decoupl|household|consumption-based|tourism|trade|life[- ]cycle|\bLCA\b|footprint|"
         r"scheduling|manufactur|machining|tunnel|building|construction|supply chain|logistics|firm|enterprise|industrial structure|"
         r"low-carbon city|carbon market|carbon price|carbon tax|emission trading|investment|happiness|health risk|hospital|patient|"
         r"sleep|apnoea|apnea|anesthe|clinical|fire safety|fire performance|fire resist|flame retard|steel|GFRP|pyrolysis|gasification|"
         r"crustal|tectonic|Proterozoic|Cretaceous|Jurassic|Eocene|Paleocene|Holocene|Pleistocene|seep|geolog")
GOOD_TYPES = {"journal-article", "posted-content", "proceedings-article", "book-chapter", "report", "dataset", None}


def strip(s):
    s = re.sub(r"</?(sub|sup|inf|mml:[a-z]+)[^>]*>", "", s or "")
    s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    return re.sub(r"\s+", " ", s).strip()


def score(title, abstract):
    t, a = title, abstract or ""
    hits = []
    chem = bool(CHEM.search(t))
    if XCO2_RE.search(t) or (XCO2_RE.search(a) and not chem): hits.append("xco2")
    if chem: return hits
    if GHG.search(t) and METHOD.search(t): hits.append("ghg")
    if DA.search(t) and (DA_CTX.search(t) or DA_CTX.search(a[:600])): hits.append("da")
    if FIRE.search(t) and FIRE2.search(t): hits.append("fire")
    if TRACE.search(t) and TRACE2.search(t): hits.append("trace")
    return hits


class Net:
    def __init__(s): s.last = 0; s.n = 0
    def get(s, url, kind="json", tries=6):
        for k in range(tries):
            wait = 1.05 - (time.time() - s.last)
            if wait > 0: time.sleep(wait)
            s.last = time.time(); s.n += 1
            try:
                req = urllib.request.Request(url, headers={"User-Agent": f"atmos_news/1.0 (mailto:{MAILTO})"})
                with urllib.request.urlopen(req, timeout=120) as r:
                    b = r.read()
                return json.loads(b) if kind == "json" else b.decode("utf-8", "replace")
            except Exception as e:
                code = getattr(e, "code", None)
                if code in (400, 404): raise
                time.sleep(min(60, 4 * (k + 1) * (3 if code == 429 else 1)))
        raise RuntimeError("failed after retries: " + url[:160])


NET = Net()
SEL = "DOI,title,container-title,created,author,abstract,type,ISSN"


def crossref_all(filt, query=None, cap=None, sort=None):
    """cursor 分页拉取；cap=最多条数"""
    out, cursor = [], "*"
    while True:
        p = {"filter": filt, "rows": 1000, "cursor": cursor, "select": SEL, "mailto": MAILTO}
        if query: p["query.bibliographic"] = query
        if sort: p["sort"], p["order"] = sort, "desc"
        m = NET.get("https://api.crossref.org/works?" + urllib.parse.urlencode(p))["message"]
        items = m.get("items", [])
        out += items
        if not items or len(items) < 1000 or (cap and len(out) >= cap): break
        cursor = m.get("next-cursor")
        if not cursor: break
    return (out[:cap] if cap else out), m.get("total-results", len(out))


def venue_of(doi, ct, typ):
    if ct: return html.unescape(ct)
    m = re.match(r"10\.5194/([a-z]+)-20\d\d-\d+$", doi)
    if m:
        j = m.group(1)
        return "EGUsphere (preprint)" if j == "egusphere" else {"essd": "ESSD (Discussions)", "acp": "ACP (Discussions)",
                "amt": "AMT (Discussions)", "gmd": "GMD (Discussions)", "bg": "BG (Discussions)"}.get(j, j.upper() + " (Discussions)")
    if doi.startswith("10.22541/"): return "ESS Open Archive (preprint)"
    return "—"


def authors_of(au):
    au = [a for a in (au or []) if a.get("family") or a.get("name")]
    if not au: return "—"
    f = au[0].get("family") or au[0].get("name")
    return f + (" et al." if len(au) > 1 else "")


def from_crossref(it, src):
    doi = it.get("DOI", "").lower()
    title = strip((it.get("title") or [""])[0])
    if not title or not doi: return None
    if re.search(r"\.s\d{3}$|supplement|/v\d+/review|\.sa\d+$|-rc\d+$|-cc\d+$|-ac\d+$", doi): return None
    if it.get("type") not in GOOD_TYPES: return None
    dp = (it.get("created") or {}).get("date-parts", [[None]])[0]
    date = "%04d-%02d-%02d" % tuple(dp) if dp and len(dp) == 3 and dp[0] else ""
    return dict(doi=doi, url="https://doi.org/" + doi, title=title,
                venue=venue_of(doi, (it.get("container-title") or [""])[0], it.get("type")),
                date=date, authors=authors_of(it.get("author")), abstract=strip(it.get("abstract"))[:1500], sources=[src])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", required=True)
    ap.add_argument("--until", dest="until", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--net-cap", type=int, default=0, help="关键词安全网每条检索取前 N 条（默认按窗口长度自动）")
    ap.add_argument("--part", default="0/1", help="i/n：只跑第 i 份（并行补抓用）")
    ap.add_argument("--skip", default="", help="逗号分隔：full,issn,net,xco2,openalex,arxiv")
    a = ap.parse_args()
    until = a.until or datetime.date.today().isoformat()
    days = (datetime.date.fromisoformat(until) - datetime.date.fromisoformat(a.frm)).days + 1
    netcap = a.net_cap or (200 if days <= 10 else 1000)
    skip = set(filter(None, a.skip.split(",")))
    win = f"from-created-date:{a.frm},until-created-date:{until}"
    log, pool = [], {}

    def add(c, hits):
        if not c: return
        k = c["doi"]
        if k in pool:
            pool[k]["sources"] = sorted(set(pool[k]["sources"] + c["sources"]))
            if not pool[k]["abstract"] and c["abstract"]: pool[k]["abstract"] = c["abstract"]
            return
        c["hits"] = hits
        pool[k] = c

    jobs = []
    def run(name, fn): jobs.append((name, fn))

    def execute(name, fn):
        t = time.time()
        try:
            n, tot, kept = fn()
            log.append(dict(name=name, ok=True, fetched=n, total=tot, kept=kept))
        except Exception as e:
            log.append(dict(name=name, ok=False, error=str(e)[:200]))
        print(json.dumps(log[-1], ensure_ascii=False), f"{time.time()-t:.0f}s", file=sys.stderr, flush=True)

    def crossref_job(filt, src, query=None, cap=None, sort=None, force_xco2=False):
        def f():
            items, tot = crossref_all(filt, query, cap, sort)
            kept = 0
            for it in items:
                c = from_crossref(it, src)
                if not c or BAD_TITLE.search(c["title"]): continue
                hits = score(c["title"], c["abstract"])
                if force_xco2 and "xco2" not in hits: continue
                if hits: add(c, hits); kept += 1
            return len(items), tot, kept
        return f

    if "full" not in skip:
        for p, nm in FULL_PREFIXES.items():
            run(f"prefix {p} 全量 ({nm})", crossref_job(f"{win},prefix:{p}", f"prefix:{p}"))
    if "issn" not in skip:
        for i, nm in ISSNS.items():
            run(f"ISSN {i} 全量 ({nm})", crossref_job(f"{win},issn:{i}", f"issn:{i}"))
    if "net" not in skip:
        for p in NET_PREFIXES:
            for q in NET_QUERIES:
                run(f"关键词 {p}「{q[:30]}…」前{netcap}", crossref_job(f"{win},prefix:{p}", f"net:{p}", q, netcap, "score"))
    if "xco2" not in skip:
        for q in XCO2_QUERIES:
            run(f"XCO2 全库「{q}」", crossref_job(win, "xco2", q, 3000, "score", force_xco2=True))
    if "openalex" not in skip:
        for q in OPENALEX_SEARCH:
            def f(q=q):
                n = kept = 0; page = 1; tot = 0
                while page <= 25:
                    p = {"search": f'"{q}"', "filter": f"from_publication_date:{a.frm},to_publication_date:{until}",
                         "per-page": 200, "page": page, "mailto": MAILTO,
                         "select": "doi,title,primary_location,publication_date,authorships,abstract_inverted_index,type"}
                    j = NET.get("https://api.openalex.org/works?" + urllib.parse.urlencode(p))
                    tot = j["meta"]["count"]; res = j.get("results", [])
                    for w in res:
                        n += 1
                        doi = (w.get("doi") or "").replace("https://doi.org/", "").lower()
                        if not doi: continue
                        inv = w.get("abstract_inverted_index") or {}
                        words = [];
                        for wd, pos in inv.items():
                            for q2 in pos: words.append((q2, wd))
                        abs_ = " ".join(x for _, x in sorted(words))[:1500]
                        title = strip(w.get("title"))
                        if not title or BAD_TITLE.search(title) or not XCO2_RE.search(title + " " + abs_): continue
                        src = ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or "—"
                        au = w.get("authorships") or []
                        auth = "—" if not au else ((au[0].get("author") or {}).get("display_name", "—").split()[-1] + (" et al." if len(au) > 1 else ""))
                        add(dict(doi=doi, url="https://doi.org/" + doi, title=title, venue=src, date=w.get("publication_date") or "",
                                 authors=auth, abstract=abs_, sources=["openalex"]), ["xco2"]); kept += 1
                    page += 1
                    if len(res) < 200: break
                return n, tot, kept
            run(f"OpenAlex 检索「{q}」(发表日期窗口)", f)
    if "arxiv" not in skip:
        def f():
            d0, d1 = a.frm.replace("-", "") + "0000", until.replace("-", "") + "2359"
            q = (f'(cat:physics.ao-ph AND (abs:assimilation OR abs:inversion OR abs:CO2 OR abs:methane OR abs:emissions '
                 f'OR abs:Kalman OR abs:greenhouse OR abs:wildfire)) OR abs:XCO2 OR abs:"OCO-2" OR abs:"OCO-3" '
                 f'OR (abs:"data assimilation" AND (cat:physics.ao-ph OR cat:stat.ML OR cat:cs.LG))')
            n = kept = 0; start = 0
            while True:
                url = "https://export.arxiv.org/api/query?" + urllib.parse.urlencode(
                    {"search_query": f"({q}) AND submittedDate:[{d0} TO {d1}]", "start": start, "max_results": 200,
                     "sortBy": "submittedDate", "sortOrder": "descending"})
                x = ET.fromstring(NET.get(url, "text"))
                ns = {"a": "http://www.w3.org/2005/Atom"}
                es = x.findall("a:entry", ns)
                for e in es:
                    n += 1
                    aid = re.sub(r"v\d+$", "", e.find("a:id", ns).text.rsplit("/abs/", 1)[-1])
                    title = strip(e.find("a:title", ns).text); abs_ = strip(e.find("a:summary", ns).text)[:1500]
                    hits = score(title, abs_)
                    if not hits: continue
                    au = e.findall("a:author/a:name", ns)
                    auth = "—" if not au else au[0].text.split()[-1] + (" et al." if len(au) > 1 else "")
                    add(dict(doi="arxiv:" + aid.lower(), url="https://arxiv.org/abs/" + aid, title=title, venue="arXiv preprint",
                             date=e.find("a:published", ns).text[:10], authors=auth, abstract=abs_, sources=["arxiv"]), hits); kept += 1
                if len(es) < 200: break
                start += 200; time.sleep(3)
            return n, n, kept
        run("arXiv API", f)

    pi, pn = map(int, a.part.split("/"))
    for k, (name, fn) in enumerate(jobs):
        if k % pn == pi: execute(name, fn)

    # 去重：排除已在 data.json 的
    try:
        d = json.load(open(os.path.join(ROOT, "data.json"), encoding="utf-8"))
        have_u = {i["url"].lower().replace("https://doi.org/", "") for i in d["items"]}
        have_t = {re.sub(r"\W+", "", i["title"].lower()) for i in d["items"]}
    except Exception:
        have_u, have_t = set(), set()
    cands = []
    seen_t = set()
    for c in pool.values():
        k = c["url"].lower().replace("https://doi.org/", "")
        tk = re.sub(r"\W+", "", c["title"].lower())
        if k in have_u or tk in have_t or tk in seen_t: continue
        seen_t.add(tk)
        c["xco2"] = "xco2" in c["hits"]
        cands.append(c)
    cands.sort(key=lambda c: (not c["xco2"], c["date"]), reverse=False)
    json.dump(dict(window=[a.frm, until], requests=NET.n, log=log, candidates=cands),
              open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    bad = [l["name"] for l in log if not l["ok"]]
    print(f"候选 {len(cands)} 篇（其中 XCO2/OCO {sum(c['xco2'] for c in cands)} 篇）；请求 {NET.n} 次；失败 {len(bad)} 条：{bad}", file=sys.stderr)


if __name__ == "__main__":
    main()
