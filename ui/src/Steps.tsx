/* The pipeline for the current image, one step per streamed event, each with its time. */
import { useEffect, useRef, useState, type ReactNode } from "react";
import { motion, useReducedMotion } from "motion/react";
import { ArrowRight, Check, CircleAlert, CircleStop, Database, Eye, MessageSquareText, ScanSearch, Square, X } from "lucide-react";
import { Button } from "./components/arc/button/button";
import { Badge } from "./components/arc/badge/badge";
import { TextShimmer } from "./components/arc/text-shimmer/text-shimmer";
import { motionTokens } from "./components/arc/lib/motion-tokens";
import type { Check as D3Check, D3, Events, Receipt, RouteName, Status } from "./api";
import { kindLabel, money, ms, pct, pctNear, receiptWhole } from "./format";
import type { Run } from "./run";
import styles from "./Steps.module.css";

const TONE: Record<Status, "success" | "warning" | "danger"> = { ok: "success", borderline: "warning", bad: "danger" };
const STATUS_TEXT: Record<Status, string> = { ok: "Ok", borderline: "Borderline", bad: "Bad" };

function Step({ icon, title, time, pending, children, label }: { icon: ReactNode; title: ReactNode; time?: ReactNode; pending?: boolean; children?: ReactNode; label: string }) {
  const reduce = useReducedMotion();
  return (
    <motion.li
      className={styles.step}
      aria-label={label}
      data-pending={pending || undefined}
      initial={reduce ? { opacity: 0 } : { opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={reduce ? { duration: motionTokens.duration.fast } : motionTokens.spring.smooth}
    >
      <div className={styles.head}>
        <span className={styles.icon} aria-hidden="true">{icon}</span>
        <h3 className={styles.title}>{title}</h3>
        {time != null && <span className={styles.time}>{time}</span>}
      </div>
      {children && <div className={styles.body}>{children}</div>}
    </motion.li>
  );
}

/** Elapsed time since `from` (ms since the run started), ticking while the step is pending. */
function useElapsed(run: Run, from: number | undefined) {
  const [, tick] = useState(0);
  useEffect(() => {
    if (!run.active) return;
    const t = setInterval(() => tick((n) => n + 1), 100);
    return () => clearInterval(t);
  }, [run.active]);
  const base = run.startedAt + (from ?? 0);
  return Math.max(0, Date.now() - base);
}

function Pending({ run, text, from, model }: { run: Run; text: string; from?: number; model?: string }) {
  const elapsed = useElapsed(run, from);
  return (
    <Step icon={<ScanSearch size={18} strokeWidth={1.75} />} pending label={text}
      title={<TextShimmer>{text}</TextShimmer>} time={<span className="num">{ms(elapsed)}</span>}>
      {model && <p className="muted">{model}</p>}
    </Step>
  );
}

function CheckBar({ c }: { c: D3Check }) {
  const reduce = useReducedMotion();
  const thr = Math.min(1, Math.max(0, c.threshold));
  const zone = c.bad_when === "high" ? { left: `${thr * 100}%`, right: 0 } : { left: 0, width: `${thr * 100}%` };
  const rule = `${c.bad_when === "high" ? "bad above" : "bad below"} ${pct(c.threshold)}`;
  return (
    <li className={styles.check} data-status={c.status}>
      <div className={styles.checkLabel}>
        <span>{c.label}</span>
        <span className={styles.rule}>{rule}</span>
      </div>
      <div className={styles.track} role="meter" aria-label={c.label} aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={Math.round(c.prob * 100)} aria-valuetext={`${pctNear(c.prob, c.threshold)}, ${rule}, ${STATUS_TEXT[c.status]}`}>
        <span className={styles.zone} style={zone} />
        <motion.span className={styles.fill}
          initial={{ scaleX: 0 }} animate={{ scaleX: Math.min(1, Math.max(0, c.prob)) }}
          transition={reduce ? { duration: 0 } : motionTokens.spring.smooth} />
        <span className={styles.marker} style={{ left: `${thr * 100}%` }} />
      </div>
      <span className={styles.prob}>{pctNear(c.prob, c.threshold)}</span>
      <Badge tone={TONE[c.status]} size="sm">{STATUS_TEXT[c.status]}</Badge>
    </li>
  );
}

function D3Body({ d3 }: { d3: D3 }) {
  const kinds = Object.entries(d3.kind.probabilities).sort((a, b) => b[1] - a[1]);
  const isReceipt = d3.kind.choice === "receipt";
  const others = kinds.filter(([k]) => k !== d3.kind.choice).filter(([, p]) => p >= 0.005).slice(0, 3);
  return (
    <>
      <div className={styles.kind}>
        <span className={styles.kindName} data-bad={!isReceipt || undefined}>{kindLabel(d3.kind.choice)}</span>
        <span className={styles.kindProb}>{pct(d3.kind.probabilities[d3.kind.choice] ?? 0)}</span>
        {others.length > 0 && <span className="muted">{others.map(([k, p]) => `${kindLabel(k).toLowerCase()} ${pct(p)}`).join(", ")}</span>}
      </div>
      <ul className={styles.checks} aria-label="Image checks">{d3.checks.map((c) => <CheckBar key={c.key} c={c} />)}</ul>
    </>
  );
}

const LANES: { route: RouteName; title: string; detail: string }[] = [
  { route: "extract", title: "Read", detail: "Qwen 27B" },
  { route: "review", title: "Read, flag for review", detail: "Qwen 27B" },
  { route: "reject", title: "Explain the problem", detail: "Qwen 4B" },
];

/* Connector from the D3 node to each lane's centre, in a 300 x 56 box stretched to the lane row's width. */
const FORK_X = [50, 150, 250];
const forkPath = (x: number) => `M150 0 C150 30 ${x} 26 ${x} 56`;

function RouteBody({ route }: { route: Events["route"] }) {
  return (
    <>
      <div className={styles.fork} aria-hidden="true">
        <span className={styles.forkNode} data-route={route.route}>D3 decides</span>
        <svg className={styles.forkLines} viewBox="0 0 300 56" preserveAspectRatio="none">
          {LANES.map((l, i) => (
            <path key={l.route} d={forkPath(FORK_X[i])} className={styles.forkLine} data-route={l.route}
              data-active={l.route === route.route || undefined} vectorEffect="non-scaling-stroke" />
          ))}
        </svg>
      </div>
      <ol className={styles.lanes} aria-label="Possible routes">
        {LANES.map((l) => (
          <li key={l.route} className={styles.lane} data-route={l.route} data-active={l.route === route.route || undefined}
            aria-current={l.route === route.route ? "step" : undefined}>
            <span className={styles.laneTitle}>{l.title}</span>
            <span className={styles.laneDetail}>{l.detail}</span>
          </li>
        ))}
      </ol>
      <div className={styles.reasons}>
        <span className={styles.summary}>{route.summary}</span>
        {route.reasons.map((r) => (
          <Badge key={r.key} tone={TONE[r.status] ?? "neutral"}>{`${r.label} ${pct(r.prob)}`}</Badge>
        ))}
      </div>
    </>
  );
}

function MessageBody({ text }: { text: string }) {
  return (
    <figure className={styles.notice}>
      <figcaption className={styles.noticeHead}><span>Receipt scanner</span><span>now</span></figcaption>
      <p className={styles.noticeTitle}>Please retake this photo</p>
      <blockquote className={styles.noticeText}>{text}</blockquote>
    </figure>
  );
}

function ReceiptBody({ r }: { r: Receipt }) {
  const cur = r.currency;
  const whole = receiptWhole(r);
  const m = (v: number | null) => money(v, cur, whole);
  const totals: [string, number | null, boolean?][] = [
    ["Subtotal", r.subtotal], ["Tax", r.tax], ["Service charge", r.service_charge], ["Discount", r.discount != null ? -Math.abs(r.discount) : null], ["Total", r.total, true],
  ];
  return (
    <div className={styles.receipt}>
      <div className={styles.store}>
        <span className={styles.storeName}>{r.store_name || "Unknown store"}</span>
        <span className="muted">{[r.date, cur].filter(Boolean).join(", ")}</span>
      </div>
      <div className={styles.tableWrap}>
        <table className={styles.items}>
          <thead><tr><th scope="col">Item</th><th scope="col">Qty</th><th scope="col">Price</th><th scope="col">Amount</th></tr></thead>
          <tbody>
            {r.items.map((it, i) => (
              <tr key={i}><td>{it.name}</td><td>{it.quantity ?? ""}</td><td>{m(it.unit_price)}</td><td>{m(it.amount)}</td></tr>
            ))}
          </tbody>
          <tfoot>
            {totals.filter(([, v]) => v != null).map(([label, v, strong]) => (
              <tr key={label} data-total={strong || undefined}><th scope="row" colSpan={3}>{label}</th><td>{m(v)}{strong && cur ? ` ${cur}` : ""}</td></tr>
            ))}
          </tfoot>
        </table>
      </div>
    </div>
  );
}

function SumBody({ s }: { s: Events["sumcheck"] }) {
  return (
    <ul className={styles.sums}>
      {s.checks.map((c) => (
        <li key={c.name + c.label} data-ok={c.ok || undefined}>
          <span className={styles.sumIcon} aria-hidden="true">{c.ok ? <Check size={16} strokeWidth={2} /> : <X size={16} strokeWidth={2} />}</span>
          <span className="srOnly">{c.ok ? "Passed:" : "Failed:"}</span>
          <span>{c.label}</span>
          {c.detail && <span className={styles.sumDetail}>{c.detail}</span>}
        </li>
      ))}
    </ul>
  );
}

/** The error step's title: plain words for a refused upload or a lost connection, else the step that failed. */
const errorTitle = (step: string) =>
  step === "upload" ? "Upload not accepted" : step === "connection" ? "Connection lost" : `Failed at ${step}`;

export function Steps({ run, onStop }: { run: Run; onStop: () => void }) {
  const { d3, route, message, extract, sumcheck, stored, done, error, at } = run;
  const live = run.active && !error && !run.stopped;  // pending steps only while the run is going
  const steps = done?.steps || {};
  const reading = route && route.route !== "reject";
  const answerMs = done?.total_ms;
  const cols = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion();
  const count = Object.keys(at).length + (error ? 1 : 0);
  // Keep the newest step in view: on a projector the result usually lands below the fold.
  useEffect(() => {
    const items = cols.current?.querySelectorAll("li[aria-label]");
    const last = items?.[items.length - 1];
    if (last && count > 1) last.scrollIntoView({ block: "nearest", behavior: reduce ? "auto" : "smooth" });
  }, [count, reduce]);
  return (
    <section className={styles.root} aria-label="Pipeline steps">
      <div className={styles.answer} aria-live="polite">
        {done ? (
          <>
            <span className={styles.answerNum}>{ms(answerMs)}</span>
            <span className={styles.answerText}>
              {route?.route === "reject" ? "to tell the user what is wrong" : route?.route === "review" ? "to read it and flag it for review" : "to read, check and store it"}
            </span>
          </>
        ) : error ? (
          <span className={styles.answerText}>{errorTitle(error.step)}</span>
        ) : run.stopped ? (
          <span className={styles.answerText}>Stopped. Nothing was stored.</span>
        ) : (
          <span className={styles.answerText}><TextShimmer>{`Checking ${run.name}`}</TextShimmer></span>
        )}
        {run.active && !run.stopped && (
          <Button variant="secondary" size="sm" className={styles.stop} onClick={onStop}>
            <Square size={14} strokeWidth={2} aria-hidden="true" />
            Stop
          </Button>
        )}
      </div>

      <div className={styles.cols} ref={cols}>
      <ol className={styles.list} aria-label="Check and route">
        {!d3 && live && <Pending run={run} text="D3 checks the image" model="vllm-sr/d3-mini" />}
        {d3 && (
          <Step icon={<ScanSearch size={18} strokeWidth={1.75} />} label="D3 check"
            title={<>D3 check <span className={styles.model}>{d3.model}</span></>} time={ms(d3.ms)}>
            <D3Body d3={d3} />
          </Step>
        )}
        {route && (
          <Step icon={<ArrowRight size={18} strokeWidth={1.75} />} label="Route" title="Route">
            <RouteBody route={route} />
          </Step>
        )}
      </ol>
      <ol className={styles.list} aria-label="Result">
        {route?.route === "reject" && !message && live && <Pending run={run} text="Qwen 4B writes a message" from={at.route} model="qwen4b" />}
        {message && (
          <Step icon={<MessageSquareText size={18} strokeWidth={1.75} />} label="Message to the user"
            title={<>Message to the user <span className={styles.model}>{message.model}</span></>} time={ms(message.ms)}>
            <MessageBody text={message.text} />
          </Step>
        )}
        {reading && !extract && live && <Pending run={run} text="Qwen 27B reads the receipt" from={at.route} model="qwen27b" />}
        {extract && (
          <Step icon={<Eye size={18} strokeWidth={1.75} />} label="Extracted receipt"
            title={<>Extracted receipt <span className={styles.model}>{extract.model}</span></>}
            time={<>{ms(extract.ms)}{extract.completion_tokens ? <span className={styles.tokens}>{extract.completion_tokens} tokens</span> : null}</>}>
            <ReceiptBody r={extract.receipt} />
          </Step>
        )}
        {sumcheck && (
          <Step icon={sumcheck.ok ? <Check size={18} strokeWidth={1.75} /> : <CircleAlert size={18} strokeWidth={1.75} />} label="Sum check"
            title={<>Sum check {sumcheck.ok ? <Badge tone="success" size="sm">Adds up</Badge> : <Badge tone="warning" size="sm">Does not add up</Badge>}</>}
            time={steps.sumcheck != null ? ms(steps.sumcheck) : undefined}>
            <SumBody s={sumcheck} />
          </Step>
        )}
        {stored && (
          <Step icon={<Database size={18} strokeWidth={1.75} />} label="Stored"
            title={<>
              {stored.table === "receipts" ? `Saved as receipt ${stored.id}` : `Logged as rejection ${stored.id}`}
              {stored.needs_review ? <Badge tone="warning" size="sm">Needs review</Badge> : null}
            </>}
            time={steps.db != null ? ms(steps.db) : undefined} />
        )}
        {run.stopped && !error && (
          <Step icon={<CircleStop size={18} strokeWidth={1.75} />} label="Stopped" title="Stopped by you">
            <p className={styles.stoppedText}>The server cancels the run after its current step; nothing is stored or counted.</p>
          </Step>
        )}
        {error && (
          <Step icon={<CircleAlert size={18} strokeWidth={1.75} />} label="Error" title={errorTitle(error.step)}>
            <p className={styles.error}>{error.message}</p>
          </Step>
        )}
      </ol>
      </div>
    </section>
  );
}
