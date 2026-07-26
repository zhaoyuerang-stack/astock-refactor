import { redirect } from "next/navigation";

// 首屏 = 今日简报·决策收件箱(「系统找人」):打开产品先看要不要介入,而非巡视看板。
export default function Home() {
  redirect("/inbox");
}
