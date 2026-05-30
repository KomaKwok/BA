# app.py —— 行业 Radar 本地网页版（编辑/研究简报风）
# 解决"在哪输关键词"的体验问题：浏览器打开、输入框、点按钮出结果。
# 架构：前端(浏览器) → 本地后端(Flask，持有 key) → 博查/DeepSeek。前端永远拿不到 key。
#
# 用法：
#   pip install flask requests python-dotenv
#   .env 里放好 BOCHA_API_KEY / DEEPSEEK_API_KEY
#   python app.py
#   浏览器打开 http://127.0.0.1:5000

import os
import json
import requests
from datetime import datetime, timezone
from urllib.parse import urlparse
from flask import Flask, request, jsonify, Response
from dotenv import load_dotenv

load_dotenv()
BOCHA_KEY = os.getenv("BOCHA_API_KEY")
DEEPSEEK_KEY = os.getenv("DEEPSEEK_API_KEY")

TRUSTED = {
    "36kr.com": "36氪", "latepost.com": "晚点LatePost", "huxiu.com": "虎嗅",
    "tmtpost.com": "钛媒体", "jiemian.com": "界面新闻", "yicai.com": "第一财经",
    "wallstreetcn.com": "华尔街见闻", "cls.cn": "财联社", "caixin.com": "财新",
    "cnr.cn": "央广网", "21jingji.com": "21世纪经济报道", "nbd.com.cn": "每日经济新闻",
    "stcn.com": "证券时报", "thepaper.cn": "澎湃新闻", "stockstar.com": "证券之星",
    "eastmoney.com": "东方财富", "sina.com.cn": "新浪财经",
}
BLACKLIST = {
    "csdn.net", "juejin.cn", "zhihu.com", "baidu.com", "jianshu.com", "cnblogs.com",
    "51cto.com", "segmentfault.com", "sohu.com", "bilibili.com", "hupu.com",
}


def host_of(url):
    try:
        return urlparse(url).netloc.lower()
    except Exception:
        return ""


def domain_hit(host, domains):
    return any(host == d or host.endswith("." + d) for d in domains)


def trusted_name(host):
    for d, name in TRUSTED.items():
        if host == d or host.endswith("." + d):
            return name
    return None


def age_in_days(date_str):
    if not date_str or date_str == "未知":
        return None
    try:
        d = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    except ValueError:
        try:
            d = datetime.strptime(date_str[:10], "%Y-%m-%d")
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - d).days


def search_bocha(query, recency_days):
    resp = requests.post(
        "https://api.bochaai.com/v1/web-search",
        headers={"Authorization": f"Bearer {BOCHA_KEY}", "Content-Type": "application/json"},
        json={"query": query, "freshness": "oneMonth", "summary": True, "count": 30},
        timeout=30,
    )
    resp.raise_for_status()
    pages = resp.json().get("data", {}).get("webPages", {}).get("value", []) or []
    kept = []
    for p in pages:
        url = p.get("url", "")
        host = host_of(url)
        date = p.get("datePublished") or p.get("dateLastCrawled", "未知")
        age = age_in_days(date)
        if age is not None and age > recency_days:
            continue
        if domain_hit(host, BLACKLIST):
            continue
        tname = trusted_name(host)
        kept.append({"title": p.get("name", ""), "url": url,
                     "source": tname or (host or "未知来源"), "trusted": bool(tname),
                     "summary": p.get("summary") or p.get("snippet", ""), "date": date})
    return kept


def analyze_deepseek(query, results):
    lines = []
    for r in results:
        tag = "【可信主流媒体】" if r["trusted"] else "【来源未核实】"
        lines.append(f"{tag}来源：{r['source']}\n标题：{r['title']}\n日期：{r['date']}\nURL：{r['url']}\n摘要：{r['summary']}")
    sources_text = "\n\n".join(lines)
    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    system = (
        f"你是面向互联网/科技行业商业分析师的情报助手，兼任严格质检。今天是 {today}。严格执行："
        "1) 严禁编造。2) 【直接剔除】往年数据/预测的旧内容(即使日期看着新)、纯股价行情、广告招商招聘、"
        "技术教程、用户吐槽、与行业商业动态无关的，一律不进简报。3) 【去重】同一事件只留一条。"
        "4) 标【可信主流媒体】可直接用；【来源未核实】须内容本身是清晰可信的当期行业事实才用。"
        "5) 【宁短勿掺水】只输出真正当期有实质的信号，没有就给空数组。"
        "6) 字段：title、source、date、fact、materiality(高/中/低)、implication(商业含义一句话，不复述事实)。"
        "按 materiality 高到低排。overall：2-3 句有观点的判断；无有效信号则写'本期无符合标准的有效信号'。"
        "只输出一个 JSON 对象，禁止前言/解释/代码块标记。格式："
        '{"keyword":"","overall":"","signals":[{"title":"","source":"","date":"","fact":"","materiality":"","implication":""}]}'
    )
    resp = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {DEEPSEEK_KEY}", "Content-Type": "application/json"},
        json={"model": "deepseek-chat",
              "messages": [{"role": "system", "content": system},
                           {"role": "user", "content": f"关键词：{query}\n\n搜索结果：\n{sources_text}"}],
              "stream": False},
        timeout=60,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    s, e = content.find("{"), content.rfind("}")
    brief = json.loads(content[s:e + 1])
    # 兜底：砍掉所有[低]，只留高/中
    brief["signals"] = [x for x in brief.get("signals", []) if x.get("materiality") in ("高", "中")]
    return brief


app = Flask(__name__)


@app.route("/api/search", methods=["POST"])
def api_search():
    if not BOCHA_KEY or not DEEPSEEK_KEY:
        return jsonify({"error": "服务端没读到 key，请检查 .env"}), 500
    data = request.get_json(force=True)
    keyword = (data.get("keyword") or "").strip()
    recency = int(data.get("recency", 45))
    if not keyword:
        return jsonify({"error": "请输入关键词"}), 400
    try:
        results = search_bocha(keyword, recency)
        if not results:
            return jsonify({"keyword": keyword, "overall": "两道硬闸后无可用结果，换个关键词试试。", "signals": [], "raw": 0})
        brief = analyze_deepseek(keyword, results)
        brief["raw"] = len(results)
        return jsonify(brief)
    except requests.HTTPError as ex:
        return jsonify({"error": f"请求失败：{ex}"}), 502
    except Exception as ex:
        return jsonify({"error": f"出错：{ex}"}), 500


@app.route("/")
def index():
    return Response(PAGE, mimetype="text/html")


PAGE = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>行业 Radar · 情报简报</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,500;0,9..144,900;1,9..144,500&family=IBM+Plex+Mono:wght@400;500&family=Noto+Sans+SC:wght@300;400;500;700&family=Noto+Serif+SC:wght@600;700;900&display=swap" rel="stylesheet">
<style>
:root{
  --paper:#f3eee2; --paper2:#fbf8f1; --ink:#241f17; --ink2:#5d5547; --muted:#938a77;
  --rule:#ddd3bf; --rule2:#cabfa6; --pine:#1f4d3e; --pine2:#2f6a55;
  --clay:#a8492a; --bronze:#8a6d2f;
  --serif:'Noto Serif SC',serif; --sans:'Noto Sans SC',sans-serif;
  --disp:'Fraunces',serif; --mono:'IBM Plex Mono',monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
html{-webkit-font-smoothing:antialiased}
body{
  background:var(--paper); color:var(--ink); font-family:var(--sans); font-weight:300;
  min-height:100vh; line-height:1.65;
  background-image:
    radial-gradient(900px 480px at 88% -8%, rgba(31,77,62,.05), transparent 60%),
    url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='120'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.025'/%3E%3C/svg%3E");
}
.wrap{max-width:760px;margin:0 auto;padding:54px 24px 90px}

/* 报头 */
.kicker{font-family:var(--mono);font-size:11px;letter-spacing:.32em;text-transform:uppercase;color:var(--pine);margin-bottom:14px}
.masthead{display:flex;align-items:baseline;gap:16px;flex-wrap:wrap}
.masthead h1{font-family:var(--serif);font-weight:900;font-size:46px;letter-spacing:1px;line-height:1}
.masthead h1 .r{font-family:var(--disp);font-weight:900;font-style:italic;color:var(--pine);letter-spacing:0}
.nameplate{margin:18px 0 6px;border-top:2px solid var(--ink);border-bottom:1px solid var(--rule2);height:5px}
.dateline{font-family:var(--mono);font-size:11px;color:var(--muted);letter-spacing:.06em;display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}

/* 检索条 */
.search{margin-top:30px;display:flex;gap:14px;align-items:flex-end;flex-wrap:wrap}
.field{flex:1;min-width:220px}
.field label{display:block;font-family:var(--mono);font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:var(--muted);margin-bottom:6px}
#kw{width:100%;background:transparent;border:none;border-bottom:1.5px solid var(--ink);color:var(--ink);
  font-family:var(--serif);font-weight:600;font-size:22px;padding:4px 2px 8px;outline:none}
#kw::placeholder{color:#b6ac96;font-weight:400}
#kw:focus{border-color:var(--pine)}
#rec{background:var(--paper2);border:1px solid var(--rule2);color:var(--ink2);font-family:var(--sans);
  font-size:13px;padding:9px 10px;border-radius:2px;outline:none}
#go{font-family:var(--mono);font-size:13px;letter-spacing:.12em;text-transform:uppercase;
  background:var(--pine);color:#f3eee2;border:none;padding:12px 22px;border-radius:2px;cursor:pointer;
  transition:background .2s}
#go:hover{background:var(--pine2)}
#go:disabled{opacity:.45;cursor:not-allowed}

/* 加载：一条游走的细线 */
.loader{display:none;margin-top:34px}
.loader.on{display:block}
.bar{height:2px;background:var(--rule2);overflow:hidden;position:relative}
.bar::after{content:"";position:absolute;left:-40%;top:0;height:100%;width:40%;background:var(--pine);
  animation:slide 1.1s ease-in-out infinite}
@keyframes slide{0%{left:-40%}100%{left:100%}}
.loader p{font-family:var(--mono);font-size:11px;color:var(--muted);letter-spacing:.14em;margin-top:12px;text-transform:uppercase}

#out{margin-top:38px}

/* 整体判断：导语 */
.lede{position:relative;padding-left:20px;margin-bottom:8px}
.lede::before{content:"";position:absolute;left:0;top:6px;bottom:6px;width:3px;background:var(--pine)}
.lede .l{font-family:var(--mono);font-size:10px;letter-spacing:.24em;text-transform:uppercase;color:var(--pine);margin-bottom:9px}
.lede p{font-family:var(--serif);font-weight:600;font-size:19px;line-height:1.75;color:var(--ink)}

.count{font-family:var(--mono);font-size:11px;color:var(--muted);letter-spacing:.1em;
  margin:34px 0 6px;padding-bottom:8px;border-bottom:1px solid var(--rule);text-transform:uppercase}

/* 信号条目：编辑式，发丝线分隔 */
.sig{padding:22px 0 22px 22px;border-bottom:1px solid var(--rule);position:relative;
  opacity:0;animation:rise .5s ease forwards}
@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.sig::before{content:"";position:absolute;left:0;top:24px;bottom:24px;width:2px;background:var(--mark)}
.sig .hd{display:flex;align-items:baseline;gap:12px;margin-bottom:7px}
.sig .no{font-family:var(--disp);font-weight:900;font-size:20px;color:var(--rule2);line-height:1}
.sig h3{font-family:var(--serif);font-weight:700;font-size:18px;line-height:1.45;color:var(--ink);flex:1}
.tag{font-family:var(--mono);font-size:10px;font-weight:500;letter-spacing:.1em;color:var(--mark);
  border:1px solid var(--mark);padding:2px 7px;border-radius:2px;white-space:nowrap;align-self:center}
.sig .meta{font-family:var(--mono);font-size:11px;color:var(--muted);letter-spacing:.04em;margin-bottom:11px}
.sig .fact{font-size:14.5px;color:var(--ink2);margin-bottom:12px}
.sig .impl{font-size:13.5px;color:var(--ink2);background:rgba(31,77,62,.045);
  border-left:2px solid var(--pine);padding:9px 14px;border-radius:0 3px 3px 0}
.sig .impl b{font-family:var(--mono);font-size:9px;letter-spacing:.18em;text-transform:uppercase;
  color:var(--pine);display:block;margin-bottom:4px;font-weight:500}

.empty{font-family:var(--serif);font-size:16px;color:var(--ink2);padding:24px 0;font-style:italic}
.err{font-family:var(--sans);color:#7a2e1a;background:rgba(168,73,42,.08);border:1px solid var(--clay);
  border-radius:3px;padding:14px 16px;font-size:13px}
footer{margin-top:46px;font-family:var(--mono);font-size:10px;color:var(--muted);letter-spacing:.08em;
  border-top:1px solid var(--rule);padding-top:16px;line-height:1.9}
</style></head><body><div class="wrap">

<div class="kicker">实时行业情报 · Intelligence Brief</div>
<div class="masthead"><h1>行业 <span class="r">Radar</span></h1></div>
<div class="nameplate"></div>
<div class="dateline"><span id="today"></span><span>引擎 · 博查检索 × DeepSeek 分析</span></div>

<div class="search">
  <div class="field">
    <label>检索关键词</label>
    <input id="kw" placeholder="腾讯广告、储能、泡泡玛特…">
  </div>
  <select id="rec"><option value="14">近 2 周</option><option value="45" selected>近 1 月</option><option value="90">近 3 月</option></select>
  <button id="go">扫描</button>
</div>

<div class="loader" id="loader"><div class="bar"></div><p>检索可信信源 · 提炼信号中</p></div>
<div id="out"></div>

<footer>原则：只取可信信源 · 硬卡时效 · 同事件去重 · 宁短勿掺水<br>本地运行 · 密钥仅存于后端</footer>
</div>
<script>
const kw=document.getElementById('kw'),rec=document.getElementById('rec'),go=document.getElementById('go'),
  loader=document.getElementById('loader'),out=document.getElementById('out');
document.getElementById('today').textContent=new Date().toLocaleDateString('zh-CN',{year:'numeric',month:'long',day:'numeric'});
const esc=s=>String(s==null?'':s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const mark=m=>m==='高'?'var(--clay)':'var(--bronze)';
go.onclick=run; kw.addEventListener('keydown',e=>{if(e.key==='Enter')run()});
async function run(){
  const keyword=kw.value.trim(); if(!keyword){kw.focus();return;}
  out.innerHTML=''; loader.classList.add('on'); go.disabled=true;
  try{
    const r=await fetch('/api/search',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({keyword,recency:Number(rec.value)})});
    const d=await r.json();
    if(d.error){out.innerHTML='<div class="err">'+esc(d.error)+'</div>';return;}
    let h='';
    if(d.overall) h+='<div class="lede"><div class="l">整体判断 · Overview</div><p>'+esc(d.overall)+'</p></div>';
    const sigs=d.signals||[];
    h+='<div class="count">'+(sigs.length?('信号 '+sigs.length+' 条 · 按重要性排序'):'本期信号')+'</div>';
    if(!sigs.length){h+='<div class="empty">本期无符合标准的有效信号——这是诚实结果，换个关键词或放宽时间范围试试。</div>';}
    sigs.forEach((s,i)=>{const c=mark(s.materiality);
      h+='<div class="sig" style="--mark:'+c+';animation-delay:'+(i*70)+'ms">'
        +'<div class="hd"><span class="no">'+String(i+1).padStart(2,'0')+'</span>'
        +'<h3>'+esc(s.title)+'</h3><span class="tag">'+esc(s.materiality)+'</span></div>'
        +'<div class="meta">'+esc(s.source)+' &nbsp;·&nbsp; '+esc(s.date)+'</div>'
        +'<div class="fact">'+esc(s.fact)+'</div>'
        +'<div class="impl"><b>商业含义</b>'+esc(s.implication)+'</div></div>';});
    out.innerHTML=h;
  }catch(e){out.innerHTML='<div class="err">网络出错：'+esc(e.message||e)+'</div>';}
  finally{loader.classList.remove('on');go.disabled=false;}
}
</script></body></html>"""


if __name__ == "__main__":
    if not BOCHA_KEY or not DEEPSEEK_KEY:
        print("⚠️ 没读到 key，请先在 .env 配好 BOCHA_API_KEY / DEEPSEEK_API_KEY")
    print("启动中… 浏览器打开 http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
