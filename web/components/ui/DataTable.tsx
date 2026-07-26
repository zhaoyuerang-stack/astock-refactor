import type { ReactNode } from "react";

export type Column<T> = {
  key: string;
  header: ReactNode;
  align?: "left" | "right";
  // 缺省时直接取 row[key];自定义渲染(徽章/染色/font-mono)返回 ReactNode
  render?: (row: T) => ReactNode;
  className?: string;
};

// 列配置式通用表格,复刻现有表样式,覆盖徽章/盈亏染色等自定义渲染。
export default function DataTable<T>({
  columns,
  rows,
  getRowKey,
  empty = "暂无数据",
}: {
  columns: Column<T>[];
  rows: T[];
  getRowKey: (row: T, index: number) => string | number;
  empty?: ReactNode;
}) {
  return (
    <table className="w-full text-[13px]">
      <thead>
        <tr className="text-subink text-left border-b border-cardline">
          {columns.map((col) => (
            <th
              key={col.key}
              className={`py-2 font-medium ${col.align === "right" ? "text-right" : ""}`}
            >
              {col.header}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 ? (
          <tr>
            <td colSpan={columns.length} className="py-6 text-center text-subink">
              {empty}
            </td>
          </tr>
        ) : (
          rows.map((row, i) => (
            <tr key={getRowKey(row, i)} className="border-b border-cardline/60">
              {columns.map((col) => (
                <td
                  key={col.key}
                  className={`py-2 ${col.align === "right" ? "text-right" : ""} ${col.className ?? ""}`}
                >
                  {col.render ? col.render(row) : ((row as Record<string, ReactNode>)[col.key] ?? "—")}
                </td>
              ))}
            </tr>
          ))
        )}
      </tbody>
    </table>
  );
}
