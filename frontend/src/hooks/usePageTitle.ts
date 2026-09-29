import { useEffect } from "react";
import { PERSONAS, VIEW_LABEL, type View } from "../lib/personas";

/** The browser tab says which view this is and, once signed in, who it is
 *  for: "Marta · Operator — Fibrapunto". The customer's tab stays
 *  "Fibrapunto — assistant": the persona is for the room, not for Marco. */
export function usePageTitle(view: View, stage: "signin" | "in") {
  useEffect(() => {
    if (view === "customer") {
      document.title = "Fibrapunto — assistant";
      return;
    }
    const label = VIEW_LABEL[view];
    document.title =
      stage === "signin" ? `${label} sign-in — Fibrapunto` : `${PERSONAS[view].name} · ${label} — Fibrapunto`;
  }, [view, stage]);
}
