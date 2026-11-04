/* The current request's numbers in four boxes (decision latency, LLM time, total model time, LLM tokens generated,
 * from the streamed events), then this visitor's totals from GET /api/stats. */
import { motion, useReducedMotion } from "motion/react";
import { motionTokens } from "./components/arc/lib/motion-tokens";
import type { Stats as StatsT } from "./api";
import type { Run } from "./run";
import { problemLabel } from "./format";
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

/** A duration split into a number and its unit, so the unit can sit smaller beside it. */
function dur(v: number): [string, string] {
  return v < 1000 ? [String(Math.round(v)), "ms"] : [(v / 1000).toFixed(v < 10000 ? 2 : 1), "s"];
}

interface Tile { key: string; label: string; value: [string, string] | null; note: string }

/** Four numbers for the current request: D3's time, the LLM's time and generated tokens, and all model time together. */
function tiles(run: Run): Tile[] {
  const { d3, message, extract, active, stopped, error } = run;
  const done = !active || !!error || stopped;
  const llm: { name: string; ms: number; out: number | null | undefined }[] = [];
  if (extract) llm.push({ name: "Qwen 27B", ms: extract.ms, out: extract.completion_tokens });
  if (message && !message.fallback) llm.push({ name: "Qwen 4B", ms: message.ms, out: message.completion_tokens });
  const llmMs = llm.reduce((a, r) => a + r.ms, 0);
  const outs = llm.map((r) => r.out).filter((v): v is number => v != null);
  const llmNote = llm.length ? llm.map((r) => r.name).join(" and ") : done ? "No LLM ran" : "Waiting";
  const total = (d3?.ms ?? 0) + llmMs;
  return [
    { key: "d3", label: "Decision latency", value: d3 ? dur(d3.ms) : null, note: d3 ? "D3, one call for every check" : done ? "D3 did not answer" : "Waiting" },
    { key: "llm", label: "LLM execution time", value: llm.length ? dur(llmMs) : done && d3 ? ["0", "ms"] : null, note: llmNote },
    { key: "total", label: "Total inference time", value: d3 ? dur(total) : null, note: "All model calls, end to end" },
    { key: "tokens", label: "LLM tokens generated", value: outs.length ? [n(outs.reduce((a, v) => a + v, 0)), "tokens"] : done && d3 && !llm.length ? ["0", "tokens"] : null,
      note: "Written by the LLM; D3 writes none" },
  ];
}

function ModelStats({ run }: { run: Run | null }) {
  return (
    <section className={styles.gpu} aria-labelledby="models-title">
      <h2 id="models-title" className={styles.h3}>This request</h2>
      {!run ? <p className={styles.note}>Run an image to see its decision and LLM times and the tokens the LLM wrote.</p> : (
        <dl className={styles.tiles}>
          {tiles(run).map((t) => (
            <div key={t.key} className={styles.tile}>
              <dt>{t.label}</dt>
              <dd className={styles.value}>{t.value ? <>{t.value[0]}<span className={styles.unit}>{t.value[1]}</span></> : "–"}</dd>
              <dd className={styles.note}>{t.note}</dd>
            </div>
          ))}
        </dl>
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
