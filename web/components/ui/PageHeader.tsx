export default function PageHeader({ title, desc }: { title: string; desc?: string }) {
  return (
    <div className="mb-5">
      <h1 className="text-xl font-semibold text-ink">{title}</h1>
      {desc && <p className="text-sm text-subink mt-1">{desc}</p>}
    </div>
  );
}
