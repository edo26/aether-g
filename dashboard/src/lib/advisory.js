// Crew advisory state from GET /advisories (history) and the /stream advisory_* events.
// Pure reducer so the event handling is testable without a browser.
//
// Per the backend contract (edge-ai/API.md): advisory_start carries a rule-based `preliminary` to show at
// once; advisory_token appends LLM text to a live area; advisory_done replaces everything for that run_id
// with the final advisory (which may be the deterministic fallback). `cancelled: true` drops the run.
// Crew questions (`trigger: "ask"`) are answers, not advisories, so this panel ignores them.

export const initialAdvisory = { compartmentId: null, current: null, run: null, disclaimer: null };

const forUs = (state, cid) => !state.compartmentId || !cid || cid === state.compartmentId;

export function advisoryReducer(state, action) {
  switch (action.type) {
    case "reset":
      return { ...initialAdvisory, compartmentId: action.compartmentId ?? null };
    case "history": {
      const latest = (action.advisories || []).find((a) => a.trigger !== "ask" && forUs(state, a.compartment_id));
      // Never let an older history fetch replace a newer advisory that already arrived on the stream.
      const current = state.current && latest && state.current.id >= latest.id ? state.current : latest ?? state.current;
      return { ...state, current, disclaimer: action.disclaimer ?? state.disclaimer };
    }
    case "advisory_start": {
      const d = action.data;
      if (d.trigger === "ask" || !forUs(state, d.compartment_id)) return state;
      return { ...state, run: { ...d, text: "" } };
    }
    case "advisory_token": {
      const d = action.data;
      if (!state.run || d.run_id !== state.run.run_id) return state;
      return { ...state, run: { ...state.run, text: state.run.text + d.token } };
    }
    case "advisory_done": {
      const d = action.data;
      const run = state.run && state.run.run_id === d.run_id ? null : state.run;
      if (d.cancelled || !d.advisory) return { ...state, run };
      const a = d.advisory;
      if (a.trigger === "ask" || !forUs(state, a.compartment_id)) return { ...state, run };
      return { ...state, run, current: a, disclaimer: a.disclaimer ?? state.disclaimer };
    }
    default:
      return state;
  }
}

/** Models sometimes escape Markdown (AVIONICS\_BAY). Show plain text. */
export const plain = (s) => (s ?? "").replace(/\\([_*`#[\]()])/g, "$1");

export const TRIGGER_LABEL = {
  status_change: "Alarm onset",
  periodic: "Periodic update",
  dust_rejected: "Dust rejected",
  ask: "Crew question",
};

export const isLlm = (a) => a && !a.fallback && a.model && a.model !== "deterministic";
