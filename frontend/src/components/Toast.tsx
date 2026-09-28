import { useEffect, useState } from "react";

/** A short confirmation for an action that would otherwise succeed in
 *  silence: a switch applied, a budget set, an offer confirmed. One host per
 *  page; `toast()` can be called from anywhere. */

interface Item {
  id: number;
  text: string;
  kind: "info" | "error";
}

type Listener = (item: Item) => void;
const listeners = new Set<Listener>();
let counter = 0;

export function toast(text: string, kind: "info" | "error" = "info") {
  const item = { id: ++counter, text, kind };
  listeners.forEach((l) => l(item));
}

export function Toasts() {
  const [items, setItems] = useState<Item[]>([]);
  useEffect(() => {
    const add: Listener = (item) => {
      setItems((current) => [...current, item]);
      window.setTimeout(() => setItems((current) => current.filter((i) => i.id !== item.id)), 3200);
    };
    listeners.add(add);
    return () => {
      listeners.delete(add);
    };
  }, []);
  return (
    <div className="toasts" role="status" aria-live="polite">
      {items.map((item) => (
        <div key={item.id} className={`toast ${item.kind}`}>
          <span aria-hidden="true">{item.kind === "error" ? "■" : "✓"}</span>
          {item.text}
        </div>
      ))}
    </div>
  );
}
