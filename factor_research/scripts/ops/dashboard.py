"""v2.1 实时监控面板 — 传统色「察」主题

传统色体系: 深夜 · 独坐 · 以算筹观星
[本] 骐磷 #12264F  [显] 花青 #1A2847 · 月白 #D4E5EF  [显] 栀子 #FAC03D  [符] 银朱 #D12920 · 二绿 #5DA39D
气质字「察」: Noto Sans SC + IBM Plex Mono, 紧密字间距, 数字说话无修辞

用法:
  cd /Users/kiki/astcok/factor_research && python3 scripts/ops/dashboard.py
  浏览器打开 http://localhost:8888
"""
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))

from flask import Flask, jsonify, render_template_string

app = Flask(__name__)
DATA = {}

HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>v2.1 · 察</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400&family=Noto+Sans+SC:wght@300;400;500&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
/* ═══════════ 本 · 骐磷 — 世界底质 ═══════════ */
:root {
  --qilin:   #12264F;  /* [本] 骐磷 · 夜底 */
  --huaqing: #1A2847;  /* [显] 花青 · 环境面 */
  --yuebai:  #D4E5EF;  /* [显] 月白 · 环境光 */
  --zhizi:   #FAC03D;  /* [显] 栀子 · 焦点光 */
  --yinzhu:  #D12920;  /* [符] 银朱 · 涨 */
  --erlv:    #5DA39D;  /* [符] 二绿 · 跌 */

  --text-primary:    rgba(212,229,239,0.92);  /* 月白 92% */
  --text-secondary:  rgba(212,229,239,0.42);  /* 月白 42% — 标签/次级 */
  --text-tertiary:   rgba(212,229,239,0.30);  /* 月白 30% — 细注 */
  --border-ambient:  rgba(212,229,239,0.08);  /* 月白 8% — 环境线 */
  --border-focal:    rgba(250,192,61,0.18);   /* 栀子 18% — 焦点线 */
  --surface:         rgba(26,40,71,0.85);     /* 花青 85% — 卡片面 */
  --positive:        #D12920;  /* 银朱 */
  --negative:        #5DA39D;  /* 二绿 */
}
*{margin:0;padding:0;box-sizing:border-box}

body {
  font-family: 'Noto Sans SC', -apple-system, sans-serif;
  background: var(--qilin);
  color: var(--text-primary);
  padding: 36px 40px 60px;
  min-height: 100vh;
  letter-spacing: 0em;
  font-weight: 300;
}

/* ══════ [显·空] 主标题 ══════ */
h1 {
  font-size: 28px; font-weight: 300; line-height: 1.38;
  letter-spacing: 0em;
  color: var(--text-primary);
}
.sub {
  font-size: 12px; font-weight: 300; color: var(--text-secondary);
  margin-top: 4px; margin-bottom: 28px;
  letter-spacing: 0em;
}

/* ══════ 决策层卡片 ══════ */
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 12px;
  margin-bottom: 24px;
}
.card {
  background: var(--surface);
  border: 0.5px solid var(--border-ambient);
  border-radius: 8px;
  padding: 20px 18px;
  transition: border-color 0.3s;
}
.card .label {
  font-size: 11px; font-weight: 400; color: var(--text-secondary);
  letter-spacing: 0em;
  margin-bottom: 6px;
}
.card .value {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 26px; font-weight: 300; letter-spacing: 0em;
  color: var(--text-primary);
}

/* 信号卡片 — 焦点光区域 */
.signal-card {
  text-align: center; padding: 22px 18px;
  border-color: var(--border-focal);
}
.signal-card .dot {
  width: 16px; height: 16px; border-radius: 50%;
  display: inline-block; margin-bottom: 8px;
}
.signal-card .status {
  font-size: 18px; font-weight: 300; letter-spacing: 0em;
}
.signal-card .subnote {
  font-size: 11px; color: var(--text-secondary); margin-top: 4px;
}

/* ══════ 面板布局 ══════ */
.grid2 {
  display: grid;
  grid-template-columns: 2fr 1fr;
  gap: 12px;
  margin-bottom: 12px;
}
.panel {
  background: var(--surface);
  border: 0.5px solid var(--border-ambient);
  border-radius: 8px;
  padding: 20px 18px;
}
.panel h3 {
  font-size: 12px; font-weight: 400; letter-spacing: 0.06em;
  color: var(--text-secondary);
  margin-bottom: 14px;
}

/* ══════ 表格 ══════ */
table {width:100%;border-collapse:collapse}
th {
  font-size: 10px; font-weight: 400; letter-spacing: 0.06em;
  color: var(--text-tertiary); text-align: left;
  padding: 5px 8px; border-bottom: 0.5px solid var(--border-ambient);
}
td {
  font-family: 'IBM Plex Mono', monospace;
  font-size: 12px; font-weight: 300; letter-spacing: 0em;
  padding: 5px 8px; border-bottom: 0.5px solid rgba(212,229,239,0.04);
}
tr:hover td { background: rgba(212,229,239,0.04) }

.positive { color: var(--positive); }
.negative { color: var(--negative); }
.in  { color: var(--zhizi); }  /* 栀子 · 持仓 */
.out { color: var(--text-secondary); }

/* ══════ 图表 ══════ */
canvas { width: 100%; height: 260px; }

/* ══════ 信号条 ══════ */
#signalBar { display: flex; flex-wrap: wrap; gap: 3px; }
#signalBar .day {
  width: 12px; height: 12px; border-radius: 3px; cursor: pointer;
  transition: opacity 0.2s;
}
#signalBar .day:hover { opacity: 0.7; }

/* ══════ 空仓提示 ══════ */
.empty-state {
  text-align: center; padding: 24px;
  color: var(--text-secondary); font-size: 13px; font-weight: 300;
}
</style></head>
<body>
<h1>v2.1 小盘量价</h1>
<div class="sub">sw=30 · reb=15d · top=30 · MA16 择时 · <span id="updateTime">{{ update_time }}</span> · <span id="refreshInfo"></span></div>

<div class="cards">
  <!-- 焦点光区: 信号卡片 -->
  <div class="card signal-card">
    <div class="dot" style="background:{{ '#FAC03D' if in_market else 'rgba(212,229,239,0.25)' }}"></div>
    <div class="status {{ 'in' if in_market else 'out' }}">{{ '持仓' if in_market else '空仓' }}</div>
    <div class="subnote">MA16 {{ ma16_dist }}</div>
  </div>
  <div class="card"><div class="label">累计净值</div><div class="value {{ 'positive' if total_ret_val > 1 else 'negative' }}">{{ total_ret }}</div></div>
  <div class="card"><div class="label">年化</div><div class="value {{ 'positive' if annual_val > 0 else 'negative' }}">{{ annual }}</div></div>
  <div class="card"><div class="label">{{ current_month }}</div><div class="value {{ 'positive' if monthly_val > 0 else 'negative' }}">{{ monthly }}</div></div>
  <div class="card"><div class="label">最大回撤</div><div class="value negative">{{ maxdd }}</div></div>
</div>

<div class="grid2">
  <div class="panel">
    <h3>净值曲线</h3>
    <canvas id="navChart"></canvas>
  </div>
  <div class="panel">
    <h3>信号 · 60天</h3>
    <div id="signalBar"></div>
    <div style="font-size:10px;color:var(--text-tertiary);margin-top:8px;letter-spacing:0em">
      <span style="color:var(--zhizi)">●</span> 持仓
      <span style="color:rgba(212,229,239,0.3);margin-left:10px">●</span> 空仓
    </div>
  </div>
</div>

<div class="grid2">
  <div class="panel">
    <h3>持仓 · {{ holdings|length }} 只</h3>
    {% if holdings %}
    <table><tr><th>#</th><th>代码</th><th>最新价</th></tr>
    {% for h in holdings %}<tr><td style="color:var(--text-tertiary)">{{ loop.index }}</td><td>{{ h.code }}</td><td style="font-family:'IBM Plex Mono',monospace">¥{{ h.price }}</td></tr>{% endfor %}</table>
    {% else %}<div class="empty-state">当前空仓 · 等待 MA16 信号</div>{% endif %}
  </div>
  <div class="panel">
    <h3>年度收益</h3>
    <table><tr><th>年份</th><th>收益</th></tr>
    {% for y in yearly %}<tr><td>{{ y.year }}</td><td class="{{ 'positive' if y.ret>0 else 'negative' }}">{{ y.display }}</td></tr>{% endfor %}</table>
  </div>
</div>

<script>
// 净值曲线
fetch('/api/nav').then(r=>r.json()).then(data=>{
  new Chart(document.getElementById('navChart'),{type:'line',data:{labels:data.dates,datasets:[{
    label:'v2.1',data:data.values,
    borderColor:'#FAC03D',borderWidth:1.2,pointRadius:0,fill:false,tension:0.1
  },{
    label:'v2.0',data:data.baseline,
    borderColor:'rgba(212,229,239,0.25)',borderWidth:0.8,pointRadius:0,fill:false,tension:0.1
  }]},
  options:{responsive:true,
    scales:{x:{display:false,grid:{display:false}},
            y:{type:'log',grid:{color:'rgba(212,229,239,0.06)'},
               ticks:{color:'rgba(212,229,239,0.30)',font:{family:'IBM Plex Mono',size:10}}}},
    plugins:{legend:{labels:{color:'rgba(212,229,239,0.60)',font:{size:11,family:'Noto Sans SC'}},boxWidth:10}}}
  })
});
// 信号条
fetch('/api/signals').then(r=>r.json()).then(data=>{
  const div=document.getElementById('signalBar');
  data.forEach(s=>{const el=document.createElement('div');el.className='day';
    el.style.background=s.in?'#FAC03D':'rgba(212,229,239,0.15)';
    el.title=s.date;div.appendChild(el)})
});

// ══════ 实时轮询: 每60秒刷新信号状态 ══════
let lastLoad = Date.now();
function refreshSignal(){
  fetch('/api/live').then(r=>r.json()).then(d=>{
    // 更新时间
    document.getElementById('updateTime').textContent = d.date;
    const mins = Math.floor((Date.now()-lastLoad)/60000);
    document.getElementById('refreshInfo').textContent = '· 实时';
    document.getElementById('refreshInfo').style.color = 'var(--zhizi)';
    setTimeout(()=>{document.getElementById('refreshInfo').style.color='var(--text-secondary)'},2000);

    // 信号点
    const dot = document.querySelector('.signal-card .dot');
    dot.style.background = d.in_market ? '#FAC03D' : 'rgba(212,229,239,0.25)';

    // 状态文字
    const status = document.querySelector('.signal-card .status');
    status.textContent = d.in_market ? '持仓' : '空仓';
    status.className = 'status ' + (d.in_market ? 'in' : 'out');

    // MA16偏离
    document.querySelector('.signal-card .subnote').textContent = 'MA16 ' + d.ma16_dist;
  });
}
setInterval(refreshSignal, 60000);
refreshSignal();  // 首次立即更新
</script>
</body></html>"""


def load_data():
    global DATA
    if DATA and time.time() - DATA.get("ts", 0) < 300:
        return DATA

    from factors.small_cap import small_cap_factor, small_cap_timing
    from strategies.small_cap import (
        StrategyConfig,
        backtest_weights,
        build_rebalance_weights,
        load_price_panels,
    )

    close, _, amount = load_price_panels("2010-01-01")
    factor = small_cap_factor(amount, 30)
    sched = build_rebalance_weights(factor, close, 30, 15)
    timing, _, dist = small_cap_timing(close, amount, 16)
    cfg = StrategyConfig(start="2010-01-01")
    ret, _ = backtest_weights(close, sched, timing.astype(float), cfg)

    last = close.index[-1]
    in_market = bool(timing.loc[last])
    ma16_dist = f"{float(dist.loc[last]):+.1%}"

    holdings = []
    if in_market:
        f = factor.loc[last].dropna()
        active = close.loc[last].dropna().index
        codes = f.reindex(active).dropna().nlargest(30).index.tolist()
        holdings = [{"code": c, "price": f"{close.loc[last, c]:.2f}"} for c in codes]

    r18 = ret[ret.index.year >= 2018].fillna(0)
    n_yr = max(len(r18) / 252, 1)
    nav18 = (1 + r18).cumprod()
    total_ret_val = float(nav18.iloc[-1])
    annual_val = total_ret_val ** (1 / n_yr) - 1
    maxdd_val = float((nav18 / nav18.cummax() - 1).min())
    month_ret = (1 + ret[(ret.index.year==last.year)&(ret.index.month==last.month)].fillna(0)).prod()-1

    yearly_ret = ret.groupby(ret.index.year).apply(lambda x: (1 + x.fillna(0)).prod() - 1)
    yearly = [{"year": str(y), "ret": v, "display": f"{v:+.1%}"} for y, v in yearly_ret.items() if y >= 2018]

    # NAV chart (quarterly samples)
    nav_q = nav18.resample("QE").last()
    f20 = small_cap_factor(amount, 60)
    s20 = build_rebalance_weights(f20, close, 25, 20)
    ret20, _ = backtest_weights(close, s20, timing.astype(float), cfg)
    nav20 = (1 + ret20[ret20.index.year >= 2018].fillna(0)).cumprod().resample("QE").last()
    nav_dates = [str(d.date()) for d in nav_q.index]
    nav_vals = [float(x) for x in nav_q.values]
    nav_base = [float(x) for x in nav20.reindex(nav_q.index).ffill().values]

    # Signal history
    sig60 = [{"date": str(dt.date()), "in": bool(timing.loc[dt])} for dt in close.index[-60:]]

    months_cn = {1:'Jan',2:'Feb',3:'Mar',4:'Apr',5:'May',6:'Jun',
                 7:'Jul',8:'Aug',9:'Sep',10:'Oct',11:'Nov',12:'Dec'}

    DATA = {
        "ts": time.time(),
        "in_market": in_market, "ma16_dist": ma16_dist,
        "holdings": holdings,
        "total_ret": f"{total_ret_val:.1f}×",
        "total_ret_val": total_ret_val,
        "annual": f"{annual_val:+.1%}", "annual_val": annual_val,
        "current_month": months_cn.get(last.month, str(last.month)),
        "monthly": f"{month_ret:+.1%}", "monthly_val": month_ret,
        "maxdd": f"{maxdd_val:+.1%}",
        "yearly": yearly, "update_time": str(last.date()),
        "_nav": {"dates": nav_dates, "values": nav_vals, "baseline": nav_base},
        "_signals": sig60,
    }
    return DATA


@app.route("/")
def index():
    return render_template_string(HTML, **load_data())

@app.route("/api/nav")
def api_nav():
    return jsonify(load_data().get("_nav", {}))

@app.route("/api/signals")
def api_signals():
    return jsonify(load_data().get("_signals", []))

@app.route("/api/live")
def api_live():
    """实时信号 — 60s 缓存"""
    global DATA
    t = time.time()
    if DATA and "_live_ts" in DATA and t - DATA["_live_ts"] < 60:
        return jsonify(DATA["_live"])

    from factors.small_cap import small_cap_timing
    from strategies.small_cap import load_price_panels

    close, _, amount = load_price_panels("2010-01-01")
    timing, _, dist = small_cap_timing(close, amount, 16)
    last = close.index[-1]
    result = {
        "date": str(last.date()),
        "in_market": bool(timing.loc[last]),
        "ma16_dist": f"{float(dist.loc[last]):+.1%}",
    }
    DATA["_live"] = result
    DATA["_live_ts"] = t
    return jsonify(result)

if __name__ == "__main__":
    print("🌙 v2.1 · 察  http://localhost:8888")
    app.run(host="127.0.0.1", port=8888, debug=False)
