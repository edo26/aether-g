// Crew advisory from the local Gemma model. The transport is not wired yet, so nothing is faked here.
export default function GemmaPanel({ advisory, stage1Alarm }) {
  return (
    <section className={`panel advisory${stage1Alarm ? " is-alarm" : ""}`} aria-labelledby="adv-h">
      <div className="panel-head">
        <h2 id="adv-h">Crew advisory</h2>
        <span className="chip info">Gemma · local</span>
      </div>
      {advisory ? (
        <>
          <p className="adv-warning">{advisory.warning}</p>
          <ol className="steps">{advisory.steps.map((s, i) => <li key={i}><span className="step-n mono">{i + 1}</span><span>{s}</span></li>)}</ol>
          <p className="footnote">Sources: {advisory.sources.join("; ")}</p>
        </>
      ) : (
        <div className="adv-empty">
          <div className="adv-icon" aria-hidden="true">
            <svg viewBox="0 0 24 24"><path d="M4 5h16v10H8l-4 4z" /></svg>
          </div>
          <p className="adv-title">{stage1Alarm ? "Stage-1 alarm is active" : "No advisory yet"}</p>
          <p className="adv-body">
            {stage1Alarm
              ? "Follow the vehicle's approved checklist. The advisory feed is not connected yet, so the Stage-1 result stands on its own."
              : "Advice appears here once the advisory endpoint is wired. The Stage-1 status above never waits for it."}
          </p>
        </div>
      )}
      <div className="adv-rules">
        <span className="eyebrow">How this panel works</span>
        <p>Gemma explains the Stage-1 result with local NASA references. It cannot change the status, and it never needs the internet.</p>
      </div>
    </section>
  );
}
