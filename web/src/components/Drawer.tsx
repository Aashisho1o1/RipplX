import { useEffect, useRef, type ReactNode } from "react";

export function Drawer({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (!dialog.open) dialog.showModal();
  }, []);
  return <dialog ref={ref} className="drawer" aria-labelledby="drawer-title" onClose={onClose}><div className="drawer-head"><h2 id="drawer-title">{title}</h2><button className="drawer-close" aria-label="Close panel" onClick={onClose}>×</button></div><div className="drawer-body">{children}</div></dialog>;
}
