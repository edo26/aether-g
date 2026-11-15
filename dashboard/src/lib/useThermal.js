import { useEffect, useState } from "react";
import { validateThermal } from "./thermal.js";

const POLL_MS = 500;

/** Polls the latest thermal camera frame while `enabled` (live mode). 404 simply means no camera yet. */
export function useThermal({ enabled, apiBase, compartmentId }) {
  const [state, setState] = useState({ frame: null, fetchedAt: null });

  useEffect(() => {
    setState({ frame: null, fetchedAt: null });
    if (!enabled) return undefined;
    const ac = new AbortController();
    const poll = async () => {
      try {
        const q = compartmentId ? `?compartment_id=${encodeURIComponent(compartmentId)}` : "";
        const res = await fetch(`${apiBase}/thermal${q}`, { signal: ac.signal });
        if (res.status === 404) { setState({ frame: null, fetchedAt: null }); return; }
        if (!res.ok) return;
        const f = await res.json();
        if (validateThermal(f).length) return;
        setState({ frame: f, fetchedAt: Date.now() });
      } catch (e) {
        if (e.name !== "AbortError") { /* keep the last frame; it ages out on its own */ }
      }
    };
    poll();
    const timer = setInterval(poll, POLL_MS);
    return () => { ac.abort(); clearInterval(timer); };
  }, [enabled, apiBase, compartmentId]);

  return state;
}
