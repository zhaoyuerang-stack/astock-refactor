"""Simulate Hot Research Reports

This script creates simulated PDF files under data_lake/research_pdf/2026-06-18/
to represent the industry directions identified as most valuable (Semiconductors, Cyclical Metals, Consumption).
These files will trigger the NLP inbox pipeline in demo mode to generate structural signals.
"""

from pathlib import Path


def main():
    # Define root path relative to this script
    ROOT = Path(__file__).resolve().parents[2]
    
    # Target directory for simulated PDFs
    target_dir = ROOT / "data_lake" / "research_pdf" / "2026-06-18"
    target_dir.mkdir(parents=True, exist_ok=True)
    
    # Reports to create
    reports = {
        "semiconductor_guosen.pdf": "半导体 算力芯片 研报内容: AI基础设施大模型驱动算力变革，GPU需求爆发，先进代工利用率满载至95%，国产设备材料周期景气上行",
        "copper_zijin.pdf": "有色金属 铜 研报内容: 低库存下伦铜价格上涨，自备矿比例高的铜企将实现显著利润增量",
        "maotai_chinastock.pdf": "贵州茅台 600519 研报内容: 渠道改革红利持续释放，直营占比提升拉动毛利率"
    }
    
    print(f"[*] Creating simulated research reports under: {target_dir}")
    for filename, content in reports.items():
        filepath = target_dir / filename
        # Overwrite if exists, write simple content
        filepath.write_text(content, encoding="utf-8")
        print(f"  [+] Created {filename}")
        
    print("[*] Simulation setup complete. You can now run the NLP pipeline in demo mode.")

if __name__ == "__main__":
    main()
