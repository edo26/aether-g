import { useEffect, useRef, useState } from "react";
import { parseJsonl, validateFrame } from "./contract.js";

export const SCENARIOS = ["nominal", "dust", "smoldering", "airgapped"];
export const INTERVAL_MS = 500;
export const STALE_AFTER_MS = 2000;
const MAX_FRAMES = 400;

/**
 * Feeds contract frames to the HUD from either captured fixtures ("replay") or a
 * local backend ("live", polling GET {apiBase}/status). Switching source/scenario
 * restarts the session and clears history, as the contract requires.
 * Returns { frames, state, error, lastRxAt, restart }.
 */
export function useSource({ source, scenario, apiBase }) {
  const [frames, setFrames] = useState([]);
  const [state, setState] = useState("connecting"); // connecting | playing | finished | live | disconnected | error
  const [error, setError] = useState(null);
  const [lastRxAt, setLastRxAt] = useState(null);
  const [epoch, setEpoch] = useState(0);
  const lastTs = useRef(-1);

  useEffect(() => {
    setFrames([]); setError(null); setLastRxAt(null); setState("connecting"); lastTs.current = -1;
    const ac = new AbortController();
    let timer;

    if (source === "replay") {
      (async () => {
        try {
          const res = await fetch(`${import.meta.env.BASE_URL}fixtures/${scenario}.jsonl`, { signal: ac.signal });
          if (!res.ok) throw new Error(`fixture ${scenario}: HTTP ${res.status}`);
          const all = parseJsonl(await res.text());
          let i = 0;
          setState("playing");
          timer = setInterval(() => {
            if (i >= all.length) { clearInterval(timer); setState("finished"); return; }
            const f = all[i++];
            setFrames((p) => [...p, f]);
            setLastRxAt(Date.now());
          }, INTERVAL_MS);
        } catch (e) {
          if (e.name !== "AbortError") { setError(e.message); setState("error"); }
        }
      })();
    } else {
      const poll = async () => {
        try {
          const res = await fetch(`${apiBase}/status`, { signal: ac.signal });
          if (!res.ok) throw new Error(`HTTP ${res.status}`);
          const body = await res.json();
          const f = body && body.latest ? body.latest : body; // shape not yet agreed with backend
          const problems = validateFrame(f);
          if (problems.length) { setError(`rejected frame: ${problems[0]}`); setState("error"); return; }
          setError(null); setState("live"); setLastRxAt(Date.now());
          if (f.timestamp_ms === lastTs.current) return;
          const restarted = f.timestamp_ms < lastTs.current;
          lastTs.current = f.timestamp_ms;
          setFrames((p) => {
            const prev = p[p.length - 1];
            const reset = restarted || (prev && prev.mode !== f.mode);
            return [...(reset ? [] : p), f].slice(-MAX_FRAMES);
          });
        } catch (e) {
          if (e.name !== "AbortError") { setError(e.message); setState("disconnected"); }
        }
      };
      poll();
      timer = setInterval(poll, INTERVAL_MS);
    }
    return () => { ac.abort(); clearInterval(timer); };
  }, [source, scenario, apiBase, epoch]);

  return { frames, state, error, lastRxAt, restart: () => setEpoch((n) => n + 1) };
}
