import { useEffect } from "react";
import { useStore } from "../store";

export function Toast() {
  const toast = useStore((s) => s.toast);
  const setToast = useStore((s) => s.setToast);
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 2800);
    return () => clearTimeout(t);
  }, [toast, setToast]);
  if (!toast) return null;
  return <div className="toast show">{toast}</div>;
}
