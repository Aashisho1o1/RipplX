export function SectionHeader({ index, title }: { index: string; title: string }) {
  return <header className="section-header"><div className="section-kicker">{index}</div><h2>{title}</h2><div className="hairline" /></header>;
}
