// Crew advisory: the latest advisory from the backend, streamed live from the local Gemma model.
// The Stage-1 status above never waits for this panel, and nothing here is invented client-side.
import { TRIGGER_LABEL, isLlm, plain } from "../lib/advisory.js";

const STATUS_WORD = { SMOLDERING_ANOMALY: "smoldering anomaly", NOMINAL: "nominal" };

function ago(iso, now) {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const s = Math.max(0, Math.round((now - t) / 1000));
  return s < 60 ? `${s} s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`;
}

function Steps({ steps }) {
  if (!steps?.length) return null;
  return (
    <ol className="steps">
      {steps.map((s, i) => <li key={i}><span className="step-n mono">{i + 1}</span><span>{plain(s)}</span></li>)}
    </ol>
  );
}

export default function GemmaPanel({ advisory, live, status, stage1Alarm, now }) {
  const { current, run, disclaimer, connected } = advisory;
  const llm = isLlm(current);

  let chip;
  if (!live) chip = <span className="chip muted">Replay</span>;
  else if (run) chip = <span className="chip dot info">Generating</span>;
  else if (current) chip = llm ? <span className="chip info">Gemma · local</span> : <span className="chip caution">Rule-based fallback</span>;
  else chip = <span className={`chip dot ${connected ? "ok" : "muted"}`}>{connected ? "Listening" : "Connecting"}</span>;

  return (
    <section className={`panel advisory${stage1Alarm ? " is-alarm" : ""}`} aria-labelledby="adv-h" aria-busy={Boolean(run)}>
      <div className="panel-head">
        <h2 id="adv-h">Crew advisory</h2>
        {chip}
      </div>

      {live && run ? (
        <div className="adv-run" aria-live="polite">
          {run.preliminary && (
            <>
              <span className="eyebrow">Preliminary · rule-based, shown at once</span>
              <p className="adv-warning">{plain(run.preliminary.warning)}</p>
              <Steps steps={run.preliminary.steps} />
            </>
          )}
          {run.llm && (
            <div className="adv-stream">
              <span className="eyebrow">Gemma is writing</span>
              <p className="mono">{plain(run.text)}<span className="caret" aria-hidden="true" /></p>
            </div>
          )}
        </div>
      ) : live && current ? (
        <div className="adv-final">
          <p className="adv-warning">{plain(current.warning)}</p>
          <Steps steps={current.steps} />
          {status && current.status !== status && (
            <p className="adv-note">Written for a {STATUS_WORD[current.status] ?? current.status} status. The status is now {STATUS_WORD[status] ?? status}.</p>
          )}
          {current.citations?.length > 0 && (
            <div className="adv-cites">
              <span className="eyebrow">Sources</span>
              {current.citations.map((c) => (
                c.url ? <a key={c.id} href={c.url} target="_blank" rel="noreferrer">{c.title}</a> : <span key={c.id}>{c.title}</span>
              ))}
            </div>
          )}
          <dl className="adv-meta">
            <div><dt>Trigger</dt><dd>{TRIGGER_LABEL[current.trigger] ?? current.trigger}</dd></div>
            <div><dt>Written</dt><dd className="mono">{ago(current.created_at, now) ?? "—"}</dd></div>
            {llm ? (
              <>
                <div><dt>First token</dt><dd className="mono">{current.first_token_ms != null ? `${(current.first_token_ms / 1000).toFixed(1)} s` : "—"}</dd></div>
                <div><dt>Complete</dt><dd className="mono">{current.latency_ms != null ? `${(current.latency_ms / 1000).toFixed(1)} s` : "—"}</dd></div>
              </>
            ) : (
              <div><dt>Model</dt><dd>Rule-based text (LLM unavailable or too slow)</dd></div>
            )}
          </dl>
        </div>
      ) : (
        <div className="adv-empty">
          <div className="adv-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24"><path d="M4 5h16v10H8l-4 4z" /></svg>
          </div>
          <p className="adv-title">{!live ? "Advisories come from the live backend" : stage1Alarm ? "Stage-1 alarm is active" : "No advisory yet"}</p>
          <p className="adv-body">
            {!live
              ? "Switch Source to Live backend to see Gemma's advisories. Captured replays carry telemetry only."
              : stage1Alarm
                ? "Follow the vehicle's approved checklist. An advisory appears here within a few seconds of the alarm."
                : "One appears when the status changes, when a dust spike is rejected, and every 30 s during an alarm."}
          </p>
        </div>
      )}

      <div className="adv-rules">
        <span className="eyebrow">How this panel works</span>
        <p>{disclaimer ?? "Gemma explains the Stage-1 result with local NASA references. It cannot change the status, and it never needs the internet."}</p>
      </div>
    </section>
  );
}
