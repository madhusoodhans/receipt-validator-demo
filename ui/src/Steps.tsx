/* The pipeline for the current image, one step per streamed event, each with its time. */
import { useEffect, useRef, type ReactNode } from "react";
import { motion, useReducedMotion } from "motion/react";
import { ArrowRight, Check, CircleAlert, CircleStop, Database, Eye, MessageSquareText, Ruler, ScanSearch, Square, X } from "lucide-react";
import { Button } from "./components/arc/button/button";
import { Badge } from "./components/arc/badge/badge";
import { TextShimmer } from "./components/arc/text-shimmer/text-shimmer";
import { motionTokens } from "./components/arc/lib/motion-tokens";
import type { Check as D3Check, Config, D3, Events, Receipt, Routed, RouteName, SizeGate, Status } from "./api";
import { kindLabel, money, ms, pct, pctNear, reasonBadge, receiptWhole } from "./format";
import type { Run } from "./run";
import { elapsedSince, llmStatus, seconds, Stepper, useTick } from "./Progress";
import styles from "./Steps.module.css";

const TONE: Record<Status, "success" | "warning" | "danger"> = { ok: "success", borderline: "warning", bad: "danger" };
const STATUS_TEXT: Record<Status, string> = { ok: "Ok", borderline: "Borderline", bad: "Bad" };

function Step({ icon, title, time, pending, skipped, children, label, follow, compact }: { icon: ReactNode; title: ReactNode; time?: ReactNode; pending?: boolean; skipped?: boolean; children?: ReactNode; label: string;
  /** The card to keep in view while it is the newest thing happening. */
  follow?: boolean; compact?: boolean }) {
  const reduce = useReducedMotion();
  return (
    <motion.li
      className={styles.step}
      aria-label={label}
      data-pending={pending || undefined}
      data-skipped={skipped || undefined}
      data-follow={follow || undefined}
      data-compact={compact || undefined}
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

function Pending({ run, text, from, model }: { run: Run; text: string; from?: number; model?: string }) {
  useTick(run.active);
  return (
    <Step icon={<ScanSearch size={18} strokeWidth={1.75} />} pending label={text}
      title={<TextShimmer>{text}</TextShimmer>} time={<span className="num">{seconds(elapsedSince(run, from))}</span>}>
      {model && <p className="muted">{model}</p>}
    </Step>
  );
}

/** While the LLM works: what it is doing, a live counter, and a still outline of what it will fill in (the receipt's
 * store, date, items and total, or the retake message). Honest about a retry or a slow answer. */
function LlmWaiting({ run, model, timeouts }: { run: Run; model?: string; timeouts?: Config["timeouts_s"] }) {
  useTick(run.active);
  const reject = run.route?.route === "reject";
  const elapsed = elapsedSince(run, run.at.route);
  const status = llmStatus(run, elapsed, timeouts);
  const text = reject ? "The LLM is writing the message" : "The LLM is reading the receipt";
  return (
    <Step icon={reject ? <MessageSquareText size={18} strokeWidth={1.75} /> : <Eye size={18} strokeWidth={1.75} />} pending follow
      label={text}
      title={<><TextShimmer>{text}</TextShimmer>{model && <span className={styles.model}>{model}</span>}</>}
      time={<span className="num">{seconds(elapsed)}</span>}>
      <p className={styles.waitLine} data-tone={status.tone}>
        {status.line ?? (reject ? "It turns the photo check's reasons into a short note for the user. Usually a few seconds."
          : "It reads every line from the photo and fills in the fields below. Usually 5 to 15 s.")}
      </p>
      {reject ? (
        <div className={styles.skeleton} aria-hidden="true">
          <span className={styles.bone} style={{ width: "40%" }} />
          <span className={styles.bone} style={{ width: "92%" }} />
          <span className={styles.bone} style={{ width: "70%" }} />
        </div>
      ) : (
        <div className={styles.skeleton} aria-hidden="true">
          <div className={styles.boneRow}><span className={styles.boneLabel}>Store</span><span className={styles.bone} style={{ width: "55%" }} /></div>
          <div className={styles.boneRow}><span className={styles.boneLabel}>Date</span><span className={styles.bone} style={{ width: "30%" }} /></div>
          <div className={styles.boneItems}>
            <span className={styles.boneLabel}>Items</span>
            {[62, 48, 56].map((w, i) => (
              <div key={i} className={styles.boneItem}><span className={styles.bone} style={{ width: `${w}%` }} /><span className={styles.bone} style={{ width: "18%" }} /></div>
            ))}
          </div>
          <div className={styles.boneTotal}><span className={styles.boneLabel}>Total</span><span className={styles.bone} style={{ width: "24%" }} /></div>
        </div>
      )}
    </Step>
  );
}

/** Every check label names the problem ("Total cut off or hidden", "Blurry text"). D3 is asked some of them the other way
 * round ("the total line is fully visible", bad when low); those show 1 - p, so every bar reads as the chance of the
 * problem with its reject zone on the right. */
const problemProb = (c: { prob: number; bad_when: "low" | "high" }) => (c.bad_when === "low" ? 1 - c.prob : c.prob);

function CheckBar({ c }: { c: D3Check }) {
  const reduce = useReducedMotion();
  const prob = problemProb(c);
  const threshold = c.bad_when === "low" ? 1 - c.threshold : c.threshold;
  const thr = Math.min(1, Math.max(0, threshold));
  const zone = { left: `${thr * 100}%`, right: 0 };
  const rule = `bad above ${pct(threshold)}`;
  return (
    <li className={styles.check} data-status={c.status}>
      <div className={styles.checkLabel}>
        <span>{c.label}</span>
        <span className={styles.rule}>{rule}</span>
      </div>
      <div className={styles.track} role="meter" aria-label={c.label} aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={Math.round(prob * 100)} aria-valuetext={`${pctNear(prob, threshold)}, ${rule}, ${STATUS_TEXT[c.status]}`}>
        <span className={styles.zone} style={zone} />
        <motion.span className={styles.fill}
          initial={{ scaleX: 0 }} animate={{ scaleX: Math.min(1, Math.max(0, prob)) }}
          transition={reduce ? { duration: 0 } : motionTokens.spring.smooth} />
        <span className={styles.marker} style={{ left: `${thr * 100}%` }} />
      </div>
      <span className={styles.prob}>{pctNear(prob, threshold)}</span>
      <Badge tone={TONE[c.status]} size="sm">{STATUS_TEXT[c.status]}</Badge>
    </li>
  );
}

/** The size check: code, no model, run before D3. A small step of its own; a bad or borderline size is still a route
 * reason. */
function SizeStep({ s }: { s: SizeGate & { ms?: number } }) {
  return (
    <Step icon={<Ruler size={18} strokeWidth={1.75} />} compact label={`Size check: ${s.px} px, ${STATUS_TEXT[s.status]}`}
      title="Size check"
      time={<><span className={styles.sizeValue}>{`${s.px} px`}</span><Badge tone={TONE[s.status]} size="sm">{STATUS_TEXT[s.status]}</Badge></>}>
      <p className={styles.sizeRule}>
        {s.status === "ok" ? "Code, no model, before D3." : `Code, no model, before D3. Bad below ${s.threshold} px; read but flagged for review below ${s.borderline_from} px.`}
      </p>
    </Step>
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
      <ul className={styles.checks} aria-label="Image checks">
        {d3.checks.map((c) => <CheckBar key={c.key} c={c} />)}
      </ul>
    </>
  );
}

const LANES: { route: RouteName; title: string; detail: string }[] = [
  { route: "extract", title: "Read", detail: "LLM reads the image" },
  { route: "review", title: "Read, flag for review", detail: "LLM reads the image" },
  { route: "reject", title: "Explain the problem", detail: "LLM writes, text only" },
];

/* Connector from the D3 node to each lane's centre, in a 300 x 56 box stretched to the lane row's width. */
const FORK_X = [50, 150, 250];
const forkPath = (x: number) => `M150 0 C150 30 ${x} 26 ${x} 56`;

function RouteBody({ route, d3 }: { route: Events["route"]; d3?: D3 }) {
  const lowBad = new Set((d3?.checks ?? []).filter((c) => c.bad_when === "low").map((c) => c.key));
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
          <Badge key={r.key} tone={TONE[r.status] ?? "neutral"}>{reasonBadge(lowBad.has(r.key) ? { ...r, prob: 1 - r.prob } : r)}</Badge>
        ))}
      </div>
    </>
  );
}

/** A model step that failed: one plain line on what failed, then what the user should do. */
const REASON: Record<string, string> = { unavailable: "Unavailable", timeout: "Timed out", busy: "Busy", too_long: "Too long", key: "Key", error: "Failed" };
const SKIPPED: Record<string, string> = { route: "Route", model: "Read or explain (LLM)", sumcheck: "Sum check", stored: "Store" };

/** What the vllm-sr Router chose for an LLM call: the model asked for, the model it picked, its decision and reasoning. */
function RoutedLine({ asked, routed }: { asked: string; routed?: Routed }) {
  if (!routed?.model) return null;
  const parts = [routed.decision && `decision ${routed.decision}`, routed.reasoning && `reasoning ${routed.reasoning}`].filter(Boolean);
  return (
    <p className={styles.routed}>
      <span className={styles.model}>{asked}</span> routed to <span className={styles.model}>{routed.model}</span>
      {parts.length > 0 && <span className="muted">{` (${parts.join(", ")})`}</span>}
    </p>
  );
}

function Failed({ title, error }: { title: ReactNode; error: Events["error"] }) {
  return (
    <Step icon={<CircleAlert size={18} strokeWidth={1.75} />} label="Failed step" follow
      title={<>{title} <Badge tone="danger" size="sm">{REASON[error.reason ?? "error"] ?? "Failed"}</Badge></>}>
      {error.detail && <p className={styles.error}>{error.detail}</p>}
      <p>{error.message}</p>
    </Step>
  );
}

function Skipped({ step }: { step: string }) {
  return <Step icon={<CircleStop size={18} strokeWidth={1.75} />} label={`${SKIPPED[step] ?? step}: skipped`} skipped
    title={<>{SKIPPED[step] ?? step} <Badge tone="neutral" size="sm">Skipped</Badge></>} />;
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
    ["Subtotal", r.subtotal], ["Tax", r.tax], ["Service charge", r.service_charge], ["Discount", r.discount != null ? -Math.abs(r.discount) : null],
  ];
  const printed = r.total_label?.trim();
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
            <tr data-total>
              <th scope="row" colSpan={3}>
                Total
                {printed && printed.toLowerCase() !== "total" && <span className={styles.totalLabel}>{`printed as "${printed}"`}</span>}
              </th>
              <td>{r.total == null ? <span className={styles.noTotal}>Not visible</span> : `${m(r.total)}${cur ? ` ${cur}` : ""}`}</td>
            </tr>
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

/** The error's headline: plain words for a refused upload, checking unavailable, a lost connection or a failed step. */
const ERROR_TITLE: Record<string, string> = {
  upload: "Upload not accepted", unavailable: "Receipt checking unavailable", connection: "Connection lost",
  d3: "Could not check the image", extract: "Checked, but could not read it", message: "Checked, but could not write the message",
  key: "API key needed", server: "Something went wrong",
};
const errorTitle = (e: Events["error"]) => (e.reason === "key" ? "API key problem" : ERROR_TITLE[e.step] ?? "Something went wrong");
/** Failures shown on their own step, with the steps after them marked skipped. */
const MODEL_STEP = new Set(["d3", "extract", "message"]);

/** The nearest scrolling ancestor (the main column on wide screens), or null for the page itself. */
function scroller(el: HTMLElement | null): HTMLElement | null {
  for (let p = el?.parentElement; p; p = p.parentElement) {
    const o = getComputedStyle(p).overflowY;
    if ((o === "auto" || o === "scroll") && p.scrollHeight > p.clientHeight) return p;
  }
  return null;
}

/** The headline: the time to answer once done; while the LLM works, how long the photo check took and what is happening. */
function Headline({ run }: { run: Run }) {
  const { d3, route, extract, done, error } = run;
  const reject = route?.route === "reject";
  if (done) {
    return (
      <>
        <span className={styles.answerNum}>{ms(done.total_ms)}</span>
        <span className={styles.answerText}>
          {reject ? "to tell the user what is wrong" : route?.route === "review" ? "to read it and flag it for review" : "to read, check and store it"}
        </span>
      </>
    );
  }
  if (error) return <span className={styles.answerText}>{errorTitle(error)}</span>;
  if (run.stopped) return <span className={styles.answerText}>Stopped. Nothing was stored.</span>;
  if (d3) {
    const doing = extract ? `receipt read in ${ms(extract.ms)}, saving…` : reject ? "the LLM is writing the message…" : route ? "the LLM is reading the receipt…" : "routing…";
    return (
      <span className={styles.progressLine}>
        <span className={styles.progressDone}>{`Photo checked in ${ms(d3.ms)}`}</span>
        <span className="srOnly">, </span>
        <TextShimmer>{doing}</TextShimmer>
      </span>
    );
  }
  return <span className={styles.answerText}><TextShimmer>{run.received ? "Checking the photo…" : `Uploading ${run.name}…`}</TextShimmer></span>;
}

export function Steps({ run, onStop, models, timeouts }: { run: Run; onStop: () => void; models?: Config["models"]; timeouts?: Config["timeouts_s"] }) {
  const { received, d3, route, message, extract, sumcheck, stored, error } = run;
  const live = run.active && !error && !run.stopped;  // pending steps only while the run is going
  const steps = run.done?.steps || {};
  const size = received?.size ?? d3?.size;
  const root = useRef<HTMLElement>(null);
  const bar = useRef<HTMLDivElement>(null);
  const reduce = useReducedMotion();
  const failedAt = error && MODEL_STEP.has(error.step) ? error.step : null;
  const skipped = (keys: string[]) => (error?.skipped ?? []).filter((k) => keys.includes(k)).map((k) => <Skipped key={k} step={k} />);
  const waitingLlm = live && !!route && !message && !extract;
  // The card to keep in view: the LLM placeholder while it works, then its result (or the failure, or Stopped).
  const follow = waitingLlm ? "wait" : extract ? "extract" : message ? "message" : error ? "error" : run.stopped ? "stopped" : null;

  // Follow the run down the column, gently, until the visitor scrolls on their own; each new run follows again.
  const userScrolled = useRef(false);
  useEffect(() => {
    userScrolled.current = false;
    const el = scroller(root.current?.parentElement ?? null) ?? document.documentElement;
    const target: EventTarget = el === document.documentElement ? window : el;
    const mark = () => { userScrolled.current = true; };
    const keys = (e: Event) => { if (["PageDown", "PageUp", "ArrowDown", "ArrowUp", "Home", "End", " "].includes((e as KeyboardEvent).key)) mark(); };
    const grab = (e: Event) => { if (e.target === el) mark(); };  // dragging the scrollbar
    target.addEventListener("wheel", mark, { passive: true });
    target.addEventListener("touchmove", mark, { passive: true });
    target.addEventListener("pointerdown", grab);
    window.addEventListener("keydown", keys);
    return () => {
      target.removeEventListener("wheel", mark); target.removeEventListener("touchmove", mark);
      target.removeEventListener("pointerdown", grab); window.removeEventListener("keydown", keys);
    };
  }, [run.key]);
  useEffect(() => {
    if (!follow || userScrolled.current) return;
    const card = root.current?.querySelector<HTMLElement>("li[data-follow]");
    if (!card) return;
    // after the card's entry animation has laid it out
    const t = setTimeout(() => card.scrollIntoView({ block: "nearest", behavior: reduce ? "auto" : "smooth" }), 120);
    return () => clearTimeout(t);
  }, [follow, reduce]);
  // Cards scrolled into view stop below the sticky stepper, not under it.
  useEffect(() => {
    const b = bar.current, r = root.current;
    if (!b || !r) return;
    const ro = new ResizeObserver(() => r.style.setProperty("--bar-h", `${b.offsetHeight}px`));
    ro.observe(b);
    return () => ro.disconnect();
  }, []);

  return (
    <section className={styles.root} aria-label="Pipeline steps" ref={root}>
      <div className={styles.answer} aria-live="polite">
        <Headline run={run} />
        {run.active && !run.stopped && (
          <Button variant="secondary" size="sm" className={styles.stop} onClick={onStop}>
            <Square size={14} strokeWidth={2} aria-hidden="true" />
            Stop
          </Button>
        )}
      </div>

      <div className={styles.bar} ref={bar} data-live={live || undefined}>
        <Stepper run={run} timeouts={timeouts} />
      </div>

      <div className={styles.cols}>
      <ol className={styles.list} data-area="check" aria-label="Checks">
        {size && <SizeStep s={size} />}
        {!d3 && live && received && <Pending run={run} text="D3 checks the photo" from={run.at.received} model={models?.d3} />}
        {d3 && (
          <Step icon={<ScanSearch size={18} strokeWidth={1.75} />} label="D3 check"
            title={<>D3 check <span className={styles.model}>{d3.model}</span></>} time={ms(d3.ms)}>
            <D3Body d3={d3} />
          </Step>
        )}
        {failedAt === "d3" && <Failed title={<>D3 check {models?.d3 && <span className={styles.model}>{models.d3}</span>}</>} error={error!} />}
      </ol>
      <ol className={styles.list} data-area="result" aria-label="Result">
        {waitingLlm && <LlmWaiting run={run} model={route!.route === "reject" ? models?.message : models?.extract} timeouts={timeouts} />}
        {message && (
          <Step icon={<MessageSquareText size={18} strokeWidth={1.75} />} label="Message to the user" follow={follow === "message"}
            title={<>Message to the user <span className={styles.model}>{message.fallback ? "standard message" : message.routed?.model ?? message.model}</span></>}
            time={message.fallback ? undefined : ms(message.ms)}>
            {message.fallback && <p><Badge tone="warning" size="sm">{`fallback: ${message.fallback}`}</Badge></p>}
            {!message.fallback && <RoutedLine asked={message.model} routed={message.routed} />}
            <MessageBody text={message.text} />
          </Step>
        )}
        {failedAt === "message" && <Failed title={<>Message to the user {models?.message && <span className={styles.model}>{models.message}</span>}</>} error={error!} />}
        {failedAt === "extract" && <Failed title={<>Read the receipt {models?.extract && <span className={styles.model}>{models.extract}</span>}</>} error={error!} />}
        {extract && (
          <Step icon={<Eye size={18} strokeWidth={1.75} />} label="Extracted receipt" follow={follow === "extract"}
            title={<>Extracted receipt <span className={styles.model}>{extract.routed?.model ?? extract.model}</span></>}
            time={<>{ms(extract.ms)}{extract.completion_tokens ? <span className={styles.tokens}>{extract.completion_tokens} tokens</span> : null}</>}>
            <RoutedLine asked={extract.model} routed={extract.routed} />
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
          <Step icon={<CircleStop size={18} strokeWidth={1.75} />} label="Stopped" title="Stopped by you" follow>
            <p className={styles.stoppedText}>The server cancels the run after its current step; nothing is stored or counted.</p>
          </Step>
        )}
        {skipped(["model", "sumcheck", "stored"])}
        {error && !failedAt && (
          <Step icon={<CircleAlert size={18} strokeWidth={1.75} />} label="Error" title={errorTitle(error)} follow>
            <p className={styles.error}>{error.message}</p>
          </Step>
        )}
      </ol>
      <ol className={styles.list} data-area="route" aria-label="Route">
        {route && (
          <Step icon={<ArrowRight size={18} strokeWidth={1.75} />} label="Route" title={<>Route <span className={styles.model}>rule on D3&apos;s answers</span></>}>
            <RouteBody route={route} d3={d3} />
          </Step>
        )}
        {skipped(["route"])}
      </ol>
      </div>
    </section>
  );
}
