export interface NavItem {
  href: string;
  label: string;
  desc: string;
  ready: boolean;
  icon: string;
  modes: ("ops" | "rd")[];
}

export interface NavGroup {
  title: string;
  items: NavItem[];
}

// 按「决策重心」排序(DECISION_COCKPITS §5):顶层是三个决策驾驶舱,沿 alpha 生命周期
// ①Alpha 工厂 → ②验证闸门 → ③PM 交易台。0 在册阶段研究/验证两线在前,PM 交易台诚实居末
// (一旦有在册可部署标的,③ 应上移)。其余为支撑/明细页。
export const NAV_GROUPS: NavGroup[] = [
  {
    title: "决策驾驶舱",
    items: [
      { href: "/inbox", label: "⓪ 今日简报·收件箱", desc: "系统找人:信任裁决 + 今天需要你裁决的事项(证据已装配,空箱=健康)", ready: true, icon: "dashboard", modes: ["ops", "rd"] },
      { href: "/alpha-factory", label: "① Alpha 工厂", desc: "晋级就绪:候选按距入册排序 + 唯一卡点门 + 信息簇拥挤度(下一步研究投向哪)", ready: true, icon: "candidates", modes: ["ops", "rd"] },
      { href: "/system-governance", label: "② 验证闸门", desc: "9-Gate 逐门裁决 + 部署真相 + CI 守卫/审计(候选能否独立验证通过→入册)", ready: true, icon: "settings", modes: ["ops", "rd"] },
      { href: "/dashboard", label: "③ PM 交易台", desc: "今日操作台:今天是否可交易 + 绑定原因 + 板凳/解锁路径", ready: true, icon: "dashboard", modes: ["ops", "rd"] },
    ],
  },
  {
    title: "研究与证据",
    items: [
      { href: "/strategy-registry", label: "策略台帳", desc: "母策略台账管理、生命周期与九门禁状态", ready: true, icon: "candidates", modes: ["ops", "rd"] },
      { href: "/factor-research", label: "因子研究", desc: "因子库规模、IC 时序、分组单调性与相关性", ready: true, icon: "factors", modes: ["ops", "rd"] },
      { href: "/backtest-lab", label: "回測實驗", desc: "历史回测表现、样本外塌陷与真实口径偏差", ready: true, icon: "backtest", modes: ["ops", "rd"] },
    ],
  },
  {
    title: "风险与执行",
    items: [
      { href: "/portfolio-risk", label: "組合風控", desc: "小盘、流动性与集中度等主要风险暴露", ready: true, icon: "portfolio", modes: ["ops", "rd"] },
      { href: "/signal-audit", label: "信號審計", desc: "信号追溯与 Top-25 选股流水线审计", ready: true, icon: "signals", modes: ["ops", "rd"] },
    ],
  },
  {
    title: "数据与配置",
    items: [
      { href: "/data-health", label: "數據健康", desc: "最新交易日对齐、PIT 通过率与数据管道状态", ready: true, icon: "data", modes: ["ops", "rd"] },
      { href: "/settings", label: "系統設置", desc: "成本、风控阈值与大模型接口密钥配置", ready: true, icon: "settings", modes: ["ops", "rd"] },
    ],
  },
];

export const NAV = NAV_GROUPS.flatMap((group) => group.items);
