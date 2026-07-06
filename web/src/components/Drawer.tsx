import { useEffect, useRef, type ReactNode } from "react";

export function Drawer({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    dialog.showModal();
    const close = () => onClose();
    dialog.addEventListener("close", close);
    return () => { dialog.removeEventListener("close", close); if (dialog.open) dialog.close(); };
  }, []);
  return <dialog ref={ref} className="drawer" aria-labelledby="drawer-title"><div className="drawer-head"><h2 id="drawer-title">{title}</h2><button className="drawer-close" aria-label="Close panel" onClick={onClose}>×</button></div><div className="drawer-body">{children}</div></dialog>;
}
