"use client";

import { useEffect } from "react";
import { useLayoutStore } from "@/lib/layoutStore";

// 挂载后手动 rehydrate 持久化的布局状态(store 用 skipHydration),
// 让首屏先用默认值渲染(与 SSR 一致),再应用 localStorage 中的用户设置。
export default function LayoutHydrator() {
  useEffect(() => {
    useLayoutStore.persist.rehydrate();
  }, []);
  return null;
}
