export function latestDateFromRange(dateRange?: string | null): string {
  if (!dateRange) return "—";
  const end = dateRange.split("~").pop()?.trim();
  const match = end?.match(/\d{4}-\d{2}-\d{2}/);
  return match?.[0] ?? "—";
}
