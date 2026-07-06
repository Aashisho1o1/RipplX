export function OwnedWatchTag({ owned }: { owned: boolean }) {
  return <span className={`tag${owned ? " owned" : ""}`}>{owned ? "OWNED" : "WATCH"}</span>;
}
