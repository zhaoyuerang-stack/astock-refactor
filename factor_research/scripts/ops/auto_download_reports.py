"""高价值产业研报自动下载与清洗脚本 (Auto Download and Clean Reports)

此脚本从东方财富公开 API 自动获取近期的个股与行业研报列表，
通过关键词过滤出最有价值的产业方向（半导体/算力、周期金属/铜、消费/白酒），
自动下载 PDF 报告并落盘，然后自动调用 NLP 提取管线进行解析，并在解析成功后立即自动删除 PDF 临时文件。
"""

import datetime
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# 设定工作目录
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PDF_DIR = ROOT / "data_lake" / "research_pdf"
INBOX_STATE_FILE = PDF_DIR / "_inbox_state.json"

# 行业筛选关键词
KEYWORDS = {
    "semiconductor": ["半导体", "芯片", "算力", "gpu", "先进代工", "光刻", "集成电路", "光模块"],
    "metals": ["有色金属", "铜", "黄金", "金属", "锂", "钴", "镍", "紫金"],
    "consumption": ["白酒", "食品饮料", "消费", "茅台", "五粮液", "啤酒"]
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "http://data.eastmoney.com/"
}

def load_processed_hashes() -> set:
    """从 _inbox_state.json 加载已经成功处理过的文件名或哈希。"""
    if INBOX_STATE_FILE.exists():
        try:
            state = json.loads(INBOX_STATE_FILE.read_text(encoding="utf-8"))
            processed = state.get("processed_files", {})
            # 提取所有成功处理过的文件名
            return {info.get("file_name") for info in processed.values() if info.get("status") == "success"}
        except Exception as e:
            print(f"[!] 读取 _inbox_state.json 失败: {e}")
    return set()

def fetch_report_list(q_type: int, begin_time: str, end_time: str, page_size: int = 100) -> list:
    """从东财 API 获取研报列表。q_type: 0 个股研报, 2 行业研报"""
    ts = int(time.time() * 1000)
    cb_name = "datatable"
    url = (
        f"https://reportapi.eastmoney.com/report/list?"
        f"cb={cb_name}&industryCode=*&pageSize={page_size}&industry=*&rating=*&ratingChange=*&"
        f"beginTime={begin_time}&endTime={end_time}&pageNo=1&fields=&qType={q_type}&"
        f"orgCode=&code=*&rcode=&_={ts}"
    )
    
    print(f"[*] 正在获取研报列表: qType={q_type}, 时间范围={begin_time} 至 {end_time}...")
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            text = response.read().decode("utf-8")
            if text.startswith(cb_name + "("):
                text = text[len(cb_name) + 1 : text.rfind(")")]
            data = json.loads(text)
            return data.get("data", [])
    except Exception as e:
        print(f"[!] 获取研报列表失败: {e}")
        return []

def filter_reports(reports: list) -> list:
    """根据关键词筛选符合高价值方向的研报。"""
    filtered = []
    for r in reports:
        title = r.get("title", "").lower()
        matched = False
        matched_category = ""
        for cat, keywords in KEYWORDS.items():
            if any(kw in title for kw in keywords):
                matched = True
                matched_category = cat
                break
        
        if matched:
            r["category"] = matched_category
            filtered.append(r)
    return filtered

def download_pdf(info_code: str, target_path: Path) -> bool:
    """根据 infoCode 下载研报 PDF 文件。"""
    pdf_url = f"https://pdf.dfcfw.com/pdf/H3_{info_code}_1.pdf"
    req = urllib.request.Request(pdf_url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_bytes(response.read())
        return True
    except urllib.error.HTTPError as he:
        print(f"  [!] HTTP错误下载 PDF {info_code}: {he.code} {he.reason}")
    except Exception as e:
        print(f"  [!] 下载 PDF {info_code} 异常: {e}")
    return False

def main():
    import argparse
    parser = argparse.ArgumentParser(description="高价值研报自动下载与管线分析")
    parser.add_argument("--days", type=int, default=15, help="获取最近多少天的研报")
    parser.add_argument("--demo", action="store_true", help="使用 Demo 模式运行 NLP 管线 (不扣费/使用 Mock 数据)")
    args = parser.parse_args()
    
    # 计算时间窗口
    today = datetime.date.today()
    start_date = today - datetime.timedelta(days=args.days)
    begin_str = start_date.strftime("%Y-%m-%d")
    end_str = today.strftime("%Y-%m-%d")
    
    # 1. 加载已经处理过的列表，避免重复下载/分析
    processed_files = load_processed_hashes()
    
    # 2. 查询研报列表 (个股研报 qType=0 + 行业研报 qType=2)
    raw_reports = []
    raw_reports.extend(fetch_report_list(0, begin_str, end_str))
    raw_reports.extend(fetch_report_list(2, begin_str, end_str))
    
    # 3. 过滤高价值研报
    target_reports = filter_reports(raw_reports)
    print(f"[*] 过滤完成: 在 {len(raw_reports)} 篇研报中，发现 {len(target_reports)} 篇高价值研报（半导体/周期金属/消费）。")
    
    # 4. 下载未处理过的研报
    downloaded_paths = []
    for idx, r in enumerate(target_reports):
        info_code = r.get("infoCode")
        title = r.get("title")
        publish_date = r.get("publishDate", "")[:10]  # yyyy-mm-dd
        
        filename = f"{info_code}.pdf"
        
        # 校验是否已经成功处理过该文件
        if filename in processed_files:
            continue
            
        target_path = PDF_DIR / publish_date / filename
        
        # 如果文件已存在，无需重复下载
        if target_path.exists():
            downloaded_paths.append(target_path)
            continue
            
        print(f"[+] 下载 [{r['category'].upper()}] 研报 ({idx+1}/{len(target_reports)}): {title}")
        success = download_pdf(info_code, target_path)
        if success:
            downloaded_paths.append(target_path)
            # 礼貌性延迟，防止频繁请求被封锁
            time.sleep(1.2)
            
    print(f"[*] 下载阶段结束. 共下载/准备了 {len(downloaded_paths)} 个 PDF 文件。")
    
    # 5. 如果有下载好的文件，拉起 report_nlp_pipeline.py 执行结构化提取，并在成功后自动删除文件以节省存储空间
    if downloaded_paths:
        print("\n" + "="*60)
        print("[*] 正在启动 NLP 提取管线分析研报并在成功后自动删除文件...")
        print("="*60)
        
        cmd = ["python3", "scripts/research/report_nlp_pipeline.py", "--delete"]
        if args.demo:
            cmd.append("--demo")
            
        proc = subprocess.run(cmd, cwd=ROOT)
        if proc.returncode == 0:
            print("[+] 研报下载、深度分析及存储清洗流程全部顺利完成！")
        else:
            print(f"[!] 研报 NLP 管线执行异常退出，退出码: {proc.returncode}")
    else:
        print("[*] 没有发现新研报需要处理，流程结束。")

if __name__ == "__main__":
    main()
