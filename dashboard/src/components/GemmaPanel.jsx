// Advisory transport (Gemma/RAG) is not agreed yet (issues #6/#7). Nothing is faked here.
export default function GemmaPanel({ advisory, stage1Alarm }) {
  return (
    <section className="panel">
      <h2>Gemma crew advisory</h2>
      {advisory ? (
        <>
          <p className="warn-line">{advisory.warning}</p>
          <ol>{advisory.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
          <p className="muted small">Sources: {advisory.sources.join("; ")}</p>
        </>
      ) : (
        <p className="muted">
          {stage1Alarm
            ? "Stage-1 alarm is active. No advisory connected yet — the Stage-1 result above stands on its own."
            : "No advisory transport connected. Advice appears here once Fachri's advisory endpoint (#6/#7) is wired."}
        </p>
      )}
    </section>
  );
}
