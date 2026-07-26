import type { Metadata } from "next";
// import { Noto_Sans_SC, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";
import Sidebar from "@/components/shell/Sidebar";
import TopBar from "@/components/shell/TopBar";
import AgentPanel from "@/components/shell/AgentPanel";
import ResizeHandle from "@/components/shell/ResizeHandle";
import LayoutHydrator from "@/components/shell/LayoutHydrator";

const notoCc = { variable: "font-sans" };
const ibmPlexMono = { variable: "font-mono" };

export const metadata: Metadata = {
  title: "Quant Research OS",
  description: "Research-first 量化研究平台",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" className={`${notoCc.variable} ${ibmPlexMono.variable}`}>
      <body>
        {/* 三栏:左导航 | 中研究区 | 右 Agent(WEB_DESIGN §1.1)*/}
        <LayoutHydrator />
        <div className="flex min-h-screen bg-bg">
          <Sidebar />
          <ResizeHandle target="sidebar" />
          <div className="flex-1 min-w-0 flex flex-col">
            <TopBar />
            <main className="flex-1 p-6 overflow-x-hidden">{children}</main>
          </div>
          <ResizeHandle target="agent" />
          <AgentPanel />
        </div>
      </body>
    </html>
  );
}
