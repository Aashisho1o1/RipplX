export function SectionHeader({ index, title }: { index: string; title: string }) {
  return <header className="section-header"><div className="section-index">{index}</div><div><div className="section-kicker">Intelligence layer</div><h2>{title}</h2></div><div className="hairline" /></header>;
}
