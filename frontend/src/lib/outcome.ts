import type { Outcome } from "./types";

/** Status role for an outcome, plus the glyph that carries it alongside the
 *  name. Colour is reinforcement here, never the signal — the reference
 *  palette's status steps are deliberately paired with an icon and a label so
 *  a reader who cannot separate the hues loses nothing. */
export function statusOf(outcome: string): { role: string; glyph: string } {
  switch (outcome as Outcome) {
    case "found":
    case "created":
    case "transferred":
      return { role: "good", glyph: "●" };
    case "multipleMatches":
      return { role: "warning", glyph: "◆" };
    case "createdWithoutContractLink":
      return { role: "warning", glyph: "◆" };
    case "notFound":
    case "rejected":
    case "noAgentAvailable":
      return { role: "serious", glyph: "▲" };
    case "serviceUnavailable":
    case "failed":
      return { role: "critical", glyph: "■" };
    default:
      return { role: "neutral", glyph: "·" };
  }
}
