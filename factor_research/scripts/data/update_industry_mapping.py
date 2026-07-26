"""Tushare 申万行业成分分类数据编译脚本

从 Tushare 自动抓取申万一级与二级行业的成份股映射，
并编译生成 data_lake/meta/industry.parquet 供因子与预测引擎使用。
"""

import sys
import time
from pathlib import Path

import pandas as pd

# 设定工作目录
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from lake.sources import tushare

LAKE = ROOT / "data_lake"
INDUSTRY_PARQUET = LAKE / "meta" / "industry.parquet"
SW_CLASSIFY_PARQUET = LAKE / "index" / "sw_classify.parquet"


def build_industry_mapping():
    print("[*] 开始构建行业映射表...")
    
    # 1. 检查 sw_classify.parquet 存在性
    if not SW_CLASSIFY_PARQUET.exists():
        raise FileNotFoundError(f"找不到申万行业分类数据: {SW_CLASSIFY_PARQUET}")
        
    classify_df = pd.read_parquet(SW_CLASSIFY_PARQUET)
    
    # 2. 筛选一级与二级行业
    l1_industries = classify_df[classify_df["level"] == "L1"]
    l2_industries = classify_df[classify_df["level"] == "L2"]
    
    print(f"[*] 发现一级行业 {len(l1_industries)} 个，二级行业 {len(l2_industries)} 个。")
    
    # 3. 循环调用 index_member 获取成员列表
    # 为防止限速，每次调用增加适当延迟
    all_members = []
    
    # 获取 L1 成员
    for idx, row in enumerate(l1_industries.itertuples()):
        index_code = row.index_code
        ind_name = row.industry_name
        print(f"[+] 正在抓取 L1 行业 ({idx+1}/{len(l1_industries)}): {ind_name} ({index_code})...")
        try:
            df = tushare.call("index_member", params={"index_code": index_code},
                              fields="index_code,index_name,con_code,con_name,in_date,out_date")
            if not df.empty:
                df["level"] = "L1"
                all_members.append(df)
            time.sleep(0.2)
        except Exception as e:
            print(f"  [!] 抓取 {index_code} 成员失败: {e}")
            
    # 获取 L2 成员
    for idx, row in enumerate(l2_industries.itertuples()):
        index_code = row.index_code
        ind_name = row.industry_name
        print(f"[+] 正在抓取 L2 行业 ({idx+1}/{len(l2_industries)}): {ind_name} ({index_code})...")
        try:
            df = tushare.call("index_member", params={"index_code": index_code},
                              fields="index_code,index_name,con_code,con_name,in_date,out_date")
            if not df.empty:
                df["level"] = "L2"
                all_members.append(df)
            time.sleep(0.2)
        except Exception as e:
            print(f"  [!] 抓取 {index_code} 成员失败: {e}")
            
    if not all_members:
        print("[!] 抓取到的行业成员数据为空，操作中止。")
        return
        
    combined = pd.concat(all_members, ignore_index=True)
    
    # 4. 过滤出当前活跃的成份股 (out_date 为空或未退出的)
    combined["out_date"] = combined["out_date"].fillna("").astype(str).str.strip()
    active = combined[combined["out_date"] == ""].copy()
    
    print(f"[*] 过滤出 {len(active)} 条活跃的成份股映射关系")
    
    # 5. 转换结构并拆分为 L1 & L2 字典以映射
    # 每个 con_code 在 L1 下映射一个 industry_code 和 industry_name
    # 每个 con_code 在 L2 下映射一个 industry_code 和 industry_name
    l1_map = active[active["level"] == "L1"].drop_duplicates(subset=["con_code"], keep="last")
    l2_map = active[active["level"] == "L2"].drop_duplicates(subset=["con_code"], keep="last")
    
    # 6. 生成基础股票列表 (从 active 取得全部股票代码去重)
    unique_stocks = active[["con_code", "con_name"]].drop_duplicates(subset=["con_code"], keep="last").copy()
    unique_stocks.columns = ["ts_code", "name"]
    unique_stocks["code"] = unique_stocks["ts_code"].str.split(".").str[0]
    
    # 7. 合并分类信息
    # 映射 L1
    l1_info = l1_map.set_index("con_code")[["index_code", "index_name"]]
    l1_info.columns = ["industry_l1_code", "industry_l1_name"]
    unique_stocks = unique_stocks.join(l1_info, on="ts_code")
    
    # 映射 L2
    l2_info = l2_map.set_index("con_code")[["index_code", "index_name"]]
    l2_info.columns = ["industry_l2_code", "industry_l2_name"]
    unique_stocks = unique_stocks.join(l2_info, on="ts_code")
    
    # 填充缺失值
    for col in ["industry_l1_code", "industry_l1_name", "industry_l2_code", "industry_l2_name"]:
        unique_stocks[col] = unique_stocks[col].fillna("未知")
        
    # 8. 写入文件
    INDUSTRY_PARQUET.parent.mkdir(parents=True, exist_ok=True)
    unique_stocks.to_parquet(INDUSTRY_PARQUET, index=False)
    print(f"[+] 行业映射构建成功！写入至: {INDUSTRY_PARQUET.relative_to(ROOT)}")
    print(unique_stocks.head(10))
    print(f"[*] 映射表总行数: {len(unique_stocks)}")


if __name__ == "__main__":
    build_industry_mapping()
