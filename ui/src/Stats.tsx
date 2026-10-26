/* Routed with D3 against everything on Qwen 27B, for the last image on its own or the images of the current Play all:
 * inference time, LLM tokens, and from the inference time, dollars and inference hours per million requests like these.
 * Then the totals from GET /api/stats. */
import { motion, useReducedMotion } from "motion/react";
import { AnimatedCounter } from "./components/arc/animated-counter/animated-counter";
import { motionTokens } from "./components/arc/lib/motion-tokens";
import type { Stats as StatsT } from "./api";
import type { Tally } from "./run";
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

const sec = (ms: number) => `${(ms / 1000).toFixed(ms < 1000 ? 2 : 1)} s`;

/** Whole numbers from 100 up, two decimals below (so $0.43 per million still reads). */
const dec = (v: number) => (Math.abs(v) >= 100 ? 0 : 2);
const plain = (v: number, decimals = dec(v)) =>
  v.toLocaleString("en-US", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });

type Num = { value: number; prefix?: string; suffix?: string; decimals?: number };
const Counter = ({ n }: { n: Num }) => (
  <AnimatedCounter value={Math.abs(n.value)} prefix={n.prefix} suffix={n.suffix} decimals={n.decimals ?? dec(n.value)} />
);

/** One comparison box: the saving as the headline, then routed with D3 against everything on Qwen 27B as two bars.
 * Every box uses it, so they read the same way. */
function Compare({ id, title, headline, caption, same, routed, all, note }: {
  /** `same` replaces the caption when the difference rounds to zero. */
  id: string; title: string; headline: Num | null; caption: string; same: string;
  routed: Num | null; all: Num | null; note: string;
}) {
  const max = Math.max(routed?.value ?? 0, all?.value ?? 0);
  const zero = headline != null && Math.abs(headline.value) < 0.5 / 10 ** (headline.decimals ?? dec(headline.value));
  return (
    <section className={styles.gpu} aria-labelledby={id}>
      <h3 id={id} className={styles.h3}>{title}</h3>
      <div className={styles.saved}>
        <span className={styles.savedNum}>{!headline ? "\u2013" : zero ? "No difference" : <Counter n={headline} />}</span>
        {headline && <span className={styles.savedText}>{zero ? same : caption}</span>}
      </div>
      <dl className={styles.compare}>
        <div className={styles.row}>
          <dt>Routed with D3</dt>
          <dd><span className={styles.sec}>{routed ? <Counter n={routed} /> : "\u2013"}</span></dd>
          <Bar value={routed?.value ?? 0} max={max} tone="accent" />
        </div>
        <div className={styles.row}>
          <dt>Everything on Qwen 27B</dt>
          <dd><span className={styles.sec}>{all ? <Counter n={all} /> : "\u2013"}</span></dd>
          <Bar value={all?.value ?? 0} max={max} tone="muted" />
        </div>
      </dl>
      <p className={styles.note}>{note}</p>
    </section>
  );
}

const TIMED = " Each model call is timed end to end through the Router.";

/** Panel heading and note for the comparison, so the panel always says which images it covers. */
function describe(t: Tally | null, routed: number, all: number, pending: number, badShare: number | null) {
  const n = t?.items.length ?? 0;
  if (!t || (n === 0 && t.mode === "image")) {
    return { heading: "Inference stats for this request", note: "Run an image to compare: routed with D3 against sending it straight to Qwen 27B." };
  }
  if (t.mode === "playall") {
    const note = (pending ? `${pending} rejected ${pending === 1 ? "image is" : "images are"} still being timed on Qwen 27B and not counted yet. ` : "")
      + "Rejected images count the 27B's measured time on that image; images the 27B read count the same read on both sides." + TIMED
      + (badShare == null ? "" : ` The saving depends on how many uploads are bad; this demo set is about ${Math.round(badShare * 100)}% bad.`);
    return { heading: `Inference stats for ${n} of ${t.total ?? n} requests in this Play all`, note };
  }
  const c = t.items[0];
  const d3 = routed - (c.route === "reject" ? 0 : all);
  const note = c.all_27b_ms == null ? "Measuring how long Qwen 27B takes on this image..."
    : c.route === "reject" ? `D3 and the Qwen 4B message took ${sec(routed)}; Qwen 27B was skipped. It would have taken ${sec(all)} on this image (measured).${TIMED}`
    : `Qwen 27B read this image either way; D3 added ${sec(Math.max(0, d3))} to check it first.${TIMED}`;
  return { heading: "Inference stats for this request", note };
}

const word = (d: number, less: string, more: string) => (d < 0 ? more : less);
const pct = (d: number, base: number) => (base > 0 ? ` (${Math.round(Math.abs(d / base) * 100)}%)` : "");
const sum = <T,>(xs: T[], f: (x: T) => number) => xs.reduce((a, x) => a + f(x), 0);

/** LLM tokens for the images in the Inference time box: the Qwen models' prompt plus completion, routed against all on
 * Qwen 27B. */
function Tokens({ tally }: { tally: Tally | null }) {
  const items = (tally?.items ?? []).filter((x) => x.all_27b_ms != null && x.routed_tokens != null && x.all_27b_tokens != null);
  const these = tally?.mode === "playall" ? "these images" : "this image";
  // D3 is not an LLM: it reads the image (input tokens) and writes none, and its cost is in the inference time.
  const tok = items.length ? sum(items, (x) => (x.routed_tokens ?? 0) - (x.d3_tokens ?? 0)) : null;
  const tok27 = items.length ? sum(items, (x) => x.all_27b_tokens ?? 0) : null;
  const tokD3 = sum(items, (x) => x.d3_tokens ?? 0);
  const d = tok != null && tok27 != null ? tok27 - tok : null;
  return (
    <Compare id="tok-title" title="LLM tokens"
      headline={d == null ? null : { value: d, decimals: 0 }}
      caption={d == null ? "" : `${word(d, "fewer", "more")} Qwen tokens for ${these}${pct(d, tok27 ?? 0)}`}
      same={tally?.mode === "image" ? "Same Qwen tokens on both paths: Qwen 27B read this image either way" : "Same Qwen tokens on both paths"}
      routed={tok == null ? null : { value: tok, decimals: 0 }} all={tok27 == null ? null : { value: tok27, decimals: 0 }}
      note={tok == null ? "Run an image to compare." : `Qwen 4B or 27B, prompt plus completion. D3 also read ${plain(tokD3, 0)} image tokens and wrote none; its cost is in the inference time.`} />
  );
}

/** Inference hours and dollars per million requests like the ones in the Inference time box: their average model time
 * per request x 1e6 / 3600, priced at the GPU hour price. Measured one request at a time, so an upper bound: a busy
 * server batches many requests on the GPU at once. */
function Costs({ tally, price }: { tally: Tally | null; price: StatsT["gpu_price"] }) {
  const items = (tally?.items ?? []).filter((x) => x.all_27b_ms != null);
  const n = items.length;
  const these = tally?.mode === "playall" ? "these images" : "this image";
  const h = n ? sum(items, (x) => x.routed_ms) / n * 1000 / 3600 : null;
  const h27 = n ? sum(items, (x) => x.all_27b_ms ?? 0) / n * 1000 / 3600 : null;
  const rate = price?.usd_per_hour;
  const usd = h != null && rate != null ? h * rate : null;
  const usd27 = h27 != null && rate != null ? h27 * rate : null;
  const dH = h != null && h27 != null ? h27 - h : null;
  const dUsd = usd != null && usd27 != null ? usd27 - usd : null;
  const hours = (v: number): Num => ({ value: v, decimals: 1, suffix: "\u00a0h" });
  const dollars = (v: number): Num => ({ value: v, prefix: "$" });
  const bound = "Measured one request at a time; a busy server runs many requests together on the GPU, so the real cost per request is lower (this is an upper bound).";
  return (
    <>
      <Compare id="usd-title" title="Dollars per million requests like this one"
        headline={dUsd == null ? null : dollars(dUsd)}
        caption={dUsd == null ? "" : `${word(dUsd, "saved", "extra")} per million requests like ${these}${pct(dUsd, usd27 ?? 0)}`}
        same="About the same cost on both paths"
        routed={usd == null ? null : dollars(usd)} all={usd27 == null ? null : dollars(usd27)}
        note={rate == null ? "No GPU price set." : h == null ? "Run an image to compare." : `Inference hours at $${plain(rate, 2)} per GPU hour (${price!.source}, checked ${price!.checked}). ${bound}`} />
      <Compare id="hours-title" title="Inference hours per million requests like this one"
        headline={dH == null ? null : hours(dH)}
        caption={dH == null ? "" : `inference hours ${word(dH, "saved", "extra")} per million requests like ${these}${pct(dH, h27 ?? 0)}`}
        same="About the same inference hours on both paths"
        routed={h == null ? null : hours(h)} all={h27 == null ? null : hours(h27)}
        note={h == null ? "Run an image to compare." : `Inference time per request times 1 million, on the one MI300X serving D3 and both Qwen models. ${bound}`} />
    </>
  );
}

export function Stats({ stats, tally, badShare }: { stats: StatsT | null; tally: Tally | null; badShare: number | null }) {
  const s = stats;
  const counted = (tally?.items ?? []).filter((x) => x.all_27b_ms != null);
  const pending = (tally?.items.length ?? 0) - counted.length;
  const routed = counted.reduce((a, x) => a + x.routed_ms, 0) / 1000;
  const all = counted.reduce((a, x) => a + (x.all_27b_ms ?? 0), 0) / 1000;
  // Signed: an image the 27B reads anyway costs a little more routed (D3 runs first), and the page says so.
  const diffPct = all > 0 ? Math.round(((all - routed) / all) * 100) : 0;
  const { heading, note } = describe(tally, routed * 1000, all * 1000, pending, badShare);
  const empty = counted.length === 0;
  const problems = Object.entries(s?.by_problem ?? {}).filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]);
  const maxProblem = Math.max(1, ...problems.map(([, n]) => n));
  const multi = problems.reduce((a, [, n]) => a + n, 0) > (s?.rejected ?? 0);
  return (
    <div className={styles.root}>
      {s?.measurement_warning && <p className={styles.warning} role="status">{s.measurement_warning}</p>}
      <h2 className={styles.heading}>{heading}</h2>
      <Compare id="time-title" title="Inference time"
        headline={empty ? null : { value: diffPct, suffix: "%", decimals: 0 }}
        caption={`${diffPct < 0 ? "more" : "less"} inference time than sending ${tally?.mode === "image" ? "this image" : "the same images"} straight to Qwen 27B`}
        same={`About the same inference time as sending ${tally?.mode === "image" ? "this image" : "the same images"} straight to Qwen 27B`}
        routed={empty ? null : { value: routed, decimals: 1, suffix: "\u00a0s" }} all={empty ? null : { value: all, decimals: 1, suffix: "\u00a0s" }}
        note={note} />
      <Tokens tally={tally} />

      <Costs tally={tally} price={s?.gpu_price} />

      <section aria-labelledby="problems-title" className={styles.problems}>
        <h2 id="problems-title" className={styles.h3}>Rejections by problem</h2>
        <dl className={styles.counts} aria-label="Totals">
          {([["Uploads", s?.uploads], ["Extracted", s?.extracted], ["Review", s?.needs_review], ["Rejected", s?.rejected]] as const).map(([label, v], i) => (
            <div key={label} className={styles.count} data-kind={i}>
              <dd className="num">{v ?? 0}</dd>
              <dt>{label}</dt>
            </div>
          ))}
        </dl>
        {multi && <p className={styles.note}>One rejection can count under more than one problem.</p>}
        {problems.length === 0 ? <p className="muted">None yet</p> : (
          <ul className={styles.problemList}>
            {problems.map(([k, n]) => (
              <li key={k}>
                <span>{problemLabel(k)}</span>
                <Bar value={n} max={maxProblem} tone="muted" />
                <span className="num">{n}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
