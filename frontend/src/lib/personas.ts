import type { Role } from "./types";

/** The four people the four views are built for (spec §2.1).
 *
 *  §2 lists actors and responsibilities. This file says who is on the other
 *  side of each page, in what situation they open it, what they must manage
 *  in thirty seconds, what the page keeps from them on purpose, and which
 *  design decision follows. The same content feeds the sign-in card, the
 *  header badge and the "who it's for" panel, so the pages and the document
 *  cannot drift apart. */

export type View = "customer" | "console" | "operator" | "analyst";

export interface Persona {
  view: View;
  /** A first name: the person, not the permission. */
  name: string;
  /** The job, in a few words. Goes next to the brand in the header. */
  role: string;
  /** The moment they open the page. */
  situation: string;
  /** What they must be able to do in thirty seconds. */
  firstJob: string;
  /** What the page deliberately does not give them. */
  never: string;
  /** The design decision that follows from the three lines above. */
  decision: string;
  /** Where that decision is written down. */
  refs: string[];
}

export const PERSONAS: Record<View, Persona> = {
  customer: {
    view: "customer",
    name: "Marco",
    role: "residential customer",
    situation:
      "Nine in the evening, on his phone, pasting the SMS that says his September direct debit was refused. He may be twenty or seventy and he types with typos.",
    firstJob:
      "Know what happened and what to do next, or know that someone will call him. One conversation, one outcome.",
    never:
      "A billing figure the system did not read. An answer about somebody else's contract. A promise the company will not keep.",
    decision:
      "The assistant asks for the contract number before discussing anything, and when billing is down it says what it cannot see instead of guessing.",
    refs: ["§1", "§4.1", "§15"],
  },
  operator: {
    view: "operator",
    name: "Marta",
    role: "retention & billing operations",
    situation:
      "Twenty to seven. A case just landed in her queue: a customer with an active lock-in wants to cancel, and expects a call back.",
    firstJob:
      "Pick up the phone without reconstructing the conversation: what the customer said in their own words, what the assistant already told them, what an offer would leave in the budget.",
    never:
      "A transcript to dig through before she can act. She does see the contact details, which the assistant is not allowed to read.",
    decision:
      "Queue on the left, one case on the right, summary and actions pinned at the top. The three fields most often omitted from a handover get their own cards.",
    refs: ["§8", "§12.2", "§12.3"],
  },
  analyst: {
    view: "analyst",
    name: "Davide",
    role: "service analyst",
    situation:
      "Monday morning, after an evaluation run and a week of campaigns. Someone will ask him whether the numbers moved, and why.",
    firstJob:
      "Read the success metrics run over run, see what each campaign did, and set the one number he owns: the budget that decides what the assistant may give away.",
    never:
      "A single conversation. The numbers, never a transcript.",
    decision:
      "One editable value, one line chart for the one series that is a real progression, everything else polled so a run finishing in a terminal shows up without a reload.",
    refs: ["§1", "§14"],
  },
  console: {
    view: "console",
    name: "Sara",
    role: "runs the platform",
    situation:
      "Eleven in the morning, something failed and nobody knows whether it was billing, the credential or the model. She owns the service identity, its grants and the queues.",
    firstJob:
      "See which step failed and with what typed outcome, break each module on command and watch the real failure path, before anyone touches a permission.",
    never:
      "An opaque error. A switch that failed silently and looks like one that worked.",
    decision:
      "Every dependency has a live switch and the trace names the step. An authorization error is never resolved by widening a grant until the missing one is identified.",
    refs: ["§7", "§8.1", "§15", "§17.10"],
  },
};

/** Order for the panel: the customer first, because the other three exist for them. */
export const PERSONA_ORDER: View[] = ["customer", "operator", "analyst", "console"];

export const VIEW_OF_ROLE: Record<Role, View> = {
  operator: "operator",
  analyst: "analyst",
  developer: "console",
};
