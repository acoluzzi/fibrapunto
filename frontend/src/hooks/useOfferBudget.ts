import { useCallback, useEffect, useState } from "react";
import { api } from "../lib/api";
import type { Campaign, OfferBudget } from "../lib/types";

/** The recommendation system's budget and catalogue, read from the system
 *  itself every few seconds — it is the operator's budget across every
 *  conversation, not any one session's. `refresh` is for the moment after a
 *  decision, so the bar moves with the click rather than on the next poll.
 *  null when the system cannot be reached; the views say so. */
export function useOfferBudget(intervalMs = 5000) {
  const [budget, setBudget] = useState<OfferBudget | null>(null);
  const [campaigns, setCampaigns] = useState<Campaign[] | null>(null);

  const refresh = useCallback(() => {
    api.offerBudget().then(setBudget).catch(() => setBudget(null));
    api.campaigns().then(setCampaigns).catch(() => setCampaigns(null));
  }, []);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, intervalMs);
    return () => window.clearInterval(timer);
  }, [refresh, intervalMs]);

  return { budget, campaigns, refresh, setBudget };
}
