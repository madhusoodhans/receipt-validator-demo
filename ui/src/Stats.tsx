/* Model stats for the current request (which models ran, each one's time and tokens, from the streamed events), then
 * this visitor's totals from GET /api/stats. */
import { motion, useReducedMotion } from "motion/react";
import { motionTokens } from "./components/arc/lib/motion-tokens";
import type { Stats as StatsT } from "./api";
import type { Run } from "./run";
import { ms, problemLabel } from "./format";
import styles from "./Stats.module.css";

function Bar({ value, max, tone }: { value: number; max: number; tone: "accent" | "muted" }) {
  const reduce = useReducedMotion();
  return (
    <span className={styles.bar} data-tone={tone}>
      <motion.span className={styles.barFill} initial={false}
        animate={{ scaleX: max > 0 ? Math.min(1, value / max) : 0 }}
        transition={reduce ? { duration: 0 } : motionTokens.spring.smooth} />
    </span>
  );
}

const n = (v: number) => v.toLocaleString("en-US");

/** One model's row: its time and tokens when it ran, else why not (not needed, skipped, failed, still running). */
interface Row { key: string; name: string; model?: string; ms?: number; tokens?: string; note?: string }

function rows(run: Run): Row[] {
  const { d3, route, message, extract, error, active } = run;
  const failed = (step: string) => (error?.step === step ? error.detail || "Failed" : undefined);
  const waiting = active && !error && !run.stopped ? "Running" : run.stopped ? "Stopped" : "Skipped";
  const d3Row: Row = d3
    ? { key: "d3", name: "D3", model: d3.model, ms: d3.ms, tokens: d3.tokens != null ? `${n(d3.tokens)} image tokens read, none written` : undefined }
    : { key: "d3", name: "D3", note: failed("d3") ?? waiting };
  const reject = route?.route === "reject";
  let msgRow: Row;
  if (message && message.fallback) msgRow = { key: "qwen4b", name: "Qwen 4B", note: `Not used: ${message.fallback}` };
  else if (message) msgRow = { key: "qwen4b", name: "Qwen 4B", model: message.model, ms: message.ms, tokens: message.tokens != null ? `${n(message.tokens)} tokens` : undefined };
  else msgRow = { key: "qwen4b", name: "Qwen 4B", note: route && !reject ? "Not needed: the image passed the checks" : failed("message") ?? waiting };
  let readRow: Row;
  if (extract) {
    const t = extract.prompt_tokens != null && extract.completion_tokens != null
      ? `${n(extract.prompt_tokens)} in, ${n(extract.completion_tokens)} out tokens` : undefined;
    readRow = { key: "qwen27b", name: "Qwen 27B", model: extract.model, ms: extract.ms, tokens: t };
  } else readRow = { key: "qwen27b", name: "Qwen 27B", note: reject ? "Not needed: the image was rejected" : failed("extract") ?? waiting };
  return [d3Row, readRow, msgRow];
}

function ModelStats({ run }: { run: Run | null }) {
  const list = run ? rows(run) : [];
  const ran = list.filter((r) => r.ms != null);
  const max = Math.max(1, ...ran.map((r) => r.ms!));
  const total = ran.reduce((a, r) => a + r.ms!, 0);
  return (
    <section className={styles.gpu} aria-labelledby="models-title">
      <h2 id="models-title" className={styles.h3}>Model stats for this request</h2>
      {!run ? <p className={styles.note}>Run an image to see which models it used, with their time and tokens.</p> : (
        <>
          <dl className={styles.compare}>
            {list.map((r) => (
              <div key={r.key} className={styles.row} data-off={r.ms == null || undefined}>
                <dt>{r.name}{r.model && <span className={styles.modelId}>{r.model}</span>}</dt>
                <dd><span className={styles.sec}>{r.ms != null ? ms(r.ms) : "–"}</span></dd>
                {r.ms != null ? (
                  <div className={styles.detail}>
                    <Bar value={r.ms} max={max} tone="accent" />
                    {r.tokens && <span className={styles.note}>{r.tokens}</span>}
                  </div>
                ) : <span className={styles.note}>{r.note}</span>}
              </div>
            ))}
          </dl>
          {ran.length > 0 && <p className={styles.note}>{`Model time in all: ${ms(total)}. Each call is timed end to end through the Router.`}</p>}
        </>
      )}
    </section>
  );
}

export function Stats({ stats, run }: { stats: StatsT | null; run: Run | null }) {
  const s = stats;
  const problems = Object.entries(s?.by_problem ?? {}).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  const maxProblem = Math.max(1, ...problems.map(([, v]) => v));
  const multi = problems.reduce((a, [, v]) => a + v, 0) > (s?.rejected ?? 0);
  return (
    <div className={styles.root}>
      <ModelStats run={run} />

      <section aria-labelledby="problems-title" className={styles.problems}>
        <h2 id="problems-title" className={styles.h3}>Your uploads this session</h2>
        <dl className={styles.counts} aria-label="Totals">
          {([["Uploads", s?.uploads], ["Extracted", s?.extracted], ["Review", s?.needs_review], ["Rejected", s?.rejected]] as const).map(([label, v], i) => (
            <div key={label} className={styles.count} data-kind={i}>
              <dd className="num">{v ?? 0}</dd>
              <dt>{label}</dt>
            </div>
          ))}
        </dl>
        <h3 className={styles.h3}>Rejections by problem</h3>
        {multi && <p className={styles.note}>One rejection can count under more than one problem.</p>}
        {problems.length === 0 ? <p className="muted">None yet</p> : (
          <ul className={styles.problemList}>
            {problems.map(([k, v]) => (
              <li key={k}>
                <span>{problemLabel(k)}</span>
                <Bar value={v} max={maxProblem} tone="muted" />
                <span className="num">{v}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
