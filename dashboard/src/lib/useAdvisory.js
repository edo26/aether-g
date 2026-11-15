import { useEffect, useReducer, useState } from "react";
import { advisoryReducer, initialAdvisory } from "./advisory.js";

const EVENTS = ["advisory_start", "advisory_token", "advisory_done"];

/**
 * Live mode only: loads the latest advisory from GET /advisories, then follows /stream.
 * EventSource reconnects by itself; every (re)connect refetches the history to catch up.
 */
export function useAdvisory({ enabled, apiBase, compartmentId }) {
  const [state, dispatch] = useReducer(advisoryReducer, initialAdvisory);
  const [connected, setConnected] = useState(false);

  useEffect(() => {
    dispatch({ type: "reset", compartmentId });
    setConnected(false);
    if (!enabled) return undefined;
    const ac = new AbortController();
    const loadHistory = async () => {
      try {
        const q = compartmentId ? `&compartment_id=${encodeURIComponent(compartmentId)}` : "";
        const res = await fetch(`${apiBase}/advisories?limit=10${q}`, { signal: ac.signal });
        if (!res.ok) return;
        const body = await res.json();
        dispatch({ type: "history", advisories: body.advisories, disclaimer: body.disclaimer });
      } catch { /* offline or aborted: the stream will retry */ }
    };
    loadHistory();

    const es = new EventSource(`${apiBase}/stream`);
    es.onopen = () => { setConnected(true); loadHistory(); };
    es.onerror = () => setConnected(false);
    EVENTS.forEach((name) => es.addEventListener(name, (e) => {
      try { dispatch({ type: name, data: JSON.parse(e.data) }); } catch { /* malformed event: skip */ }
    }));
    return () => { ac.abort(); es.close(); };
  }, [enabled, apiBase, compartmentId]);

  return { ...state, connected };
}
