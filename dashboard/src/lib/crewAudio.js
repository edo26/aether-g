// Crew audio: a master-alarm siren on SMOLDERING_ANOMALY and spoken Gemma advisories.
// Everything is generated in the browser (Web Audio + speechSynthesis), so it works fully offline.
// Browsers only allow sound after a user gesture; the Sound button (or any click/key) unlocks it.
import { useCallback, useEffect, useRef, useState } from "react";
import { plain } from "./advisory.js";

const PREF_KEY = "aether-g.sound";
const BURST_S = 3.2; // two-tone siren burst
const REPEAT_MS = 6000; // the burst repeats until acknowledged or the status clears
const ALARM_GAIN = 0.22;
const DUCKED_GAIN = 0.04; // while an advisory is being read out

/** Text the crew hears for one advisory: the warning, then the numbered steps. */
export function advisorySpeech(a) {
  if (!a) return "";
  const parts = [plain(a.warning).trim()];
  (a.steps ?? []).forEach((s, i) => parts.push(`Step ${i + 1}. ${plain(s).trim()}`));
  return parts.filter(Boolean).join(" ")
    .replace(/_/g, " ")
    .replace(/\bppm\b/gi, "p p m")
    .replace(/\s+/g, " ");
}

function pickVoice() {
  const voices = window.speechSynthesis?.getVoices?.() ?? [];
  const en = voices.filter((v) => /^en[-_]/i.test(v.lang));
  const prefer = [/natural/i, /neural/i, /google us english/i, /premium|enhanced/i, /samantha|ava|allison|aria|jenny/i];
  for (const re of prefer) {
    const v = en.find((x) => re.test(x.name));
    if (v) return v;
  }
  return en[0] ?? voices[0] ?? null;
}

// One siren burst: alternating 960/770 Hz, like a spacecraft master alarm, with a short attack/release.
function playBurst(ctx, out) {
  const t0 = ctx.currentTime + 0.02;
  const osc = ctx.createOscillator();
  const env = ctx.createGain();
  osc.type = "square";
  for (let i = 0, t = t0; t < t0 + BURST_S; i++, t += 0.2) osc.frequency.setValueAtTime(i % 2 ? 770 : 960, t);
  env.gain.setValueAtTime(0, t0);
  env.gain.linearRampToValueAtTime(1, t0 + 0.03);
  env.gain.setValueAtTime(1, t0 + BURST_S - 0.08);
  env.gain.linearRampToValueAtTime(0, t0 + BURST_S);
  const lp = ctx.createBiquadFilter(); // soften the square wave a little
  lp.type = "lowpass";
  lp.frequency.value = 3200;
  osc.connect(lp).connect(env).connect(out);
  osc.start(t0);
  osc.stop(t0 + BURST_S + 0.05);
}

export function useCrewAudio({ alarm, advisory, live }) {
  const [enabled, setEnabled] = useState(() => localStorage.getItem(PREF_KEY) !== "off");
  const [acked, setAcked] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const ctxRef = useRef(null);
  const outRef = useRef(null);
  const mountedAt = useRef(Date.now());
  const spokenId = useRef(null);

  const ensureCtx = useCallback(() => {
    if (!ctxRef.current) {
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return null;
      const ctx = new Ctx();
      const out = ctx.createGain();
      out.gain.value = ALARM_GAIN;
      out.connect(ctx.destination);
      ctxRef.current = ctx;
      outRef.current = out;
    }
    if (ctxRef.current.state === "suspended") ctxRef.current.resume().catch(() => {});
    return ctxRef.current;
  }, []);

  // Any user gesture unlocks audio for the rest of the session.
  useEffect(() => {
    const unlock = () => { if (enabled) ensureCtx(); };
    window.addEventListener("pointerdown", unlock);
    window.addEventListener("keydown", unlock);
    return () => { window.removeEventListener("pointerdown", unlock); window.removeEventListener("keydown", unlock); };
  }, [enabled, ensureCtx]);

  useEffect(() => { localStorage.setItem(PREF_KEY, enabled ? "on" : "off"); }, [enabled]);

  // A new alarm re-arms the siren; returning to nominal clears the acknowledgement.
  useEffect(() => { if (!alarm) setAcked(false); }, [alarm]);

  // Siren: first burst on alarm onset, then every REPEAT_MS until acknowledged.
  const sounding = enabled && alarm && !acked;
  useEffect(() => {
    if (!sounding) return undefined;
    const ctx = ensureCtx();
    if (!ctx) return undefined;
    playBurst(ctx, outRef.current);
    const id = setInterval(() => playBurst(ctx, outRef.current), REPEAT_MS);
    return () => clearInterval(id);
  }, [sounding, ensureCtx]);

  // Duck the siren while an advisory is read out so the crew can hear it.
  useEffect(() => {
    const ctx = ctxRef.current, out = outRef.current;
    if (!ctx || !out) return;
    out.gain.setTargetAtTime(speaking ? DUCKED_GAIN : ALARM_GAIN, ctx.currentTime, 0.08);
  }, [speaking]);

  const stopSpeaking = useCallback(() => {
    window.speechSynthesis?.cancel();
    setSpeaking(false);
  }, []);

  const speak = useCallback((a) => {
    const synth = window.speechSynthesis;
    const text = advisorySpeech(a);
    if (!synth || !text) return;
    synth.cancel();
    const u = new SpeechSynthesisUtterance(text);
    const voice = pickVoice();
    if (voice) { u.voice = voice; u.lang = voice.lang; } else u.lang = "en-US";
    u.rate = 1.0;
    u.pitch = 1.0;
    u.onstart = () => setSpeaking(true);
    u.onend = u.onerror = () => setSpeaking(false);
    synth.speak(u);
  }, []);

  // Read out each new advisory once: only ones written after this page opened, never old history.
  const current = advisory?.current;
  useEffect(() => {
    if (!live || !enabled || !current || current.id === spokenId.current) return;
    const created = Date.parse(current.created_at);
    if (Number.isNaN(created) || created < mountedAt.current) { spokenId.current = current.id; return; }
    spokenId.current = current.id;
    speak(current);
  }, [live, enabled, current, speak]);

  useEffect(() => { if (!enabled) stopSpeaking(); }, [enabled, stopSpeaking]);
  useEffect(() => () => { window.speechSynthesis?.cancel(); ctxRef.current?.close(); }, []);

  return {
    enabled,
    toggle: () => setEnabled((e) => { if (!e) ensureCtx(); return !e; }),
    sounding,
    acknowledge: () => setAcked(true),
    speaking,
    speak: (a) => { ensureCtx(); speak(a); },
    stopSpeaking,
  };
}
