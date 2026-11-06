/* Where the run is: a five-step stepper (size check, photo check, LLM, sum check, store) with each step's state and
 * time, a live counter on the active step, and the helpers the LLM placeholder card shares with it. */
import { useEffect, useState, type ReactNode } from "react";
import { Check, Minus, Square, X } from "lucide-react";
import type { Config, SizeGate } from "./api";
import { ms } from "./format";
import type { Run } from "./run";
import styles from "./Progress.module.css";

export type PhaseKey = "size" | "d3" | "llm" | "sum" | "stored";
export type PhaseState = "waiting" | "active" | "done" | "skipped" | "failed" | "stopped";
export interface Phase {
  key: PhaseKey;
  label: string;
  /** What does the work: code or a model. */
  tag: string;
  state: PhaseState;
  /** The step's own time, once done. */
  ms?: number;
  /** A short word instead of a time: "standard message", "Timed out", ... */
  note?: string;
  /** Milliseconds since the run started at which the step became active. */
  from?: number;
}

const REASON: Record<string, string> = { unavailable: "Unavailable", timeout: "Timed out", busy: "Busy", too_long: "Too long", key: "Key problem", error: "Failed" };
export const failWord = (reason?: string) => REASON[reason ?? "error"] ?? "Failed";

/** The run as five steps. Steps not reached when the run ended show as skipped (or stopped). */
export function phases(run: Run): Phase[] {
  const { received, d3, route, message, extract, sumcheck, stored, done, error, at } = run;
  const live = run.active && !error && !run.stopped;
  const reject = route?.route === "reject";
  const size: (SizeGate & { ms?: number }) | undefined = received?.size ?? d3?.size;
  const later: PhaseState = live ? "waiting" : run.stopped ? "stopped" : "skipped";
  const failed = (...steps: string[]) => !!error && steps.includes(error.step);
  const fail = { state: "failed" as const, note: failWord(error?.reason) };

  const sizeP: Phase = { key: "size", label: "Size check", tag: "code", state: later };
  if (size) Object.assign(sizeP, { state: "done", ms: size.ms });
  else if (live) Object.assign(sizeP, { state: "active", from: 0 });

  const d3P: Phase = { key: "d3", label: "Photo check", tag: "D3", state: later };
  if (d3) Object.assign(d3P, { state: "done", ms: d3.ms });
  else if (failed("d3")) Object.assign(d3P, fail);
  else if (live && received) Object.assign(d3P, { state: "active", from: at.received });

  const llmP: Phase = { key: "llm", label: reject ? "Explain the problem" : "Read receipt", tag: "LLM", state: later };
  if (message) Object.assign(llmP, message.fallback ? { state: "done", note: "standard message" } : { state: "done", ms: message.ms });
  else if (extract) Object.assign(llmP, { state: "done", ms: extract.ms });
  else if (failed("extract", "message")) Object.assign(llmP, fail);
  else if (live && route) Object.assign(llmP, { state: "active", from: at.route });

  const sumP: Phase = { key: "sum", label: "Sum check", tag: "code", state: reject ? "skipped" : later };
  if (sumcheck) Object.assign(sumP, { state: "done", ms: done?.steps.sumcheck });
  else if (failed("sumcheck")) Object.assign(sumP, fail);
  else if (live && extract) Object.assign(sumP, { state: "active", from: at.extract });

  const storedP: Phase = { key: "stored", label: "Stored", tag: "database", state: later };
  if (stored) Object.assign(storedP, { state: "done", ms: done?.steps.db });
  else if (failed("db")) Object.assign(storedP, fail);
  else if (live && (sumcheck || message)) Object.assign(storedP, { state: "active", from: at.sumcheck ?? at.message });

  return [sizeP, d3P, llmP, sumP, storedP];
}

/** Re-renders every 100 ms while `on`. */
export function useTick(on: boolean) {
  const [, tick] = useState(0);
  useEffect(() => {
    if (!on) return;
    const t = setInterval(() => tick((n) => n + 1), 100);
    return () => clearInterval(t);
  }, [on]);
}

/** Milliseconds since `from` (ms since the run started); call useTick to keep it moving. */
export const elapsedSince = (run: Run, from: number | undefined) => Math.max(0, Date.now() - (run.startedAt + (from ?? 0)));
/** A live counter: always seconds with one decimal, so it does not jump between units. */
export const seconds = (v: number) => `${(v / 1000).toFixed(1)} s`;

/** What the LLM step is doing right now, in plain words: busy and retrying, slower than usual, or the usual time. */
export function llmStatus(run: Run, elapsed: number, timeouts?: Config["timeouts_s"]) {
  const reject = run.route?.route === "reject";
  const model = reject ? "retake" : "extract";
  const retrying = run.retry && run.retry.model === model && (run.at.retry ?? 0) >= (run.at.route ?? 0);
  const limit = (reject ? timeouts?.message : timeouts?.extract) ?? (reject ? 20 : 30);
  const slowAfter = reject ? 6000 : 15000;
  if (retrying) return { tone: "warning" as const, short: "busy, retrying", line: `The model was busy (HTTP ${run.retry!.status}), so the app is trying once more.` };
  if (elapsed > slowAfter) {
    return { tone: "warning" as const, short: "slower than usual",
      line: reject ? `Slower than usual. If there is no answer within ${limit} s, a standard message is used instead.`
        : `Slower than usual. The app waits up to ${limit} s for an answer, then tells you here.` };
  }
  return { tone: "neutral" as const, short: reject ? "usually 1 to 5 s" : "usually 5 to 15 s", line: null };
}

const ACTIVE_TEXT: Record<PhaseKey, string> = {
  size: "Uploading and measuring", d3: "Checking the photo", llm: "Reading the receipt", sum: "Adding up", stored: "Saving",
};

function Mark({ state }: { state: PhaseState }) {
  const icon = state === "done" ? <Check size={13} strokeWidth={2.25} />
    : state === "failed" ? <X size={13} strokeWidth={2.25} />
    : state === "skipped" ? <Minus size={13} strokeWidth={2} />
    : state === "stopped" ? <Square size={10} strokeWidth={2.25} />
    : null;
  return <span className={styles.mark} data-state={state} aria-hidden="true">{icon}</span>;
}

const STATE_TEXT: Record<PhaseState, string> = { waiting: "waiting", active: "in progress", done: "done", skipped: "skipped", failed: "failed", stopped: "stopped" };

export function Stepper({ run, timeouts }: { run: Run; timeouts?: Config["timeouts_s"] }) {
  const list = phases(run);
  const live = run.active && !run.error && !run.stopped;
  useTick(live);
  const reject = run.route?.route === "reject";
  let footer: ReactNode = null;
  return (
    <div className={styles.wrap}>
    <ol className={styles.stepper} aria-label="Progress">
      {list.map((p) => {
        const elapsed = p.state === "active" ? elapsedSince(run, p.from) : 0;
        const status = p.key === "llm" && p.state === "active" ? llmStatus(run, elapsed, timeouts) : null;
        const doing = p.key === "llm" && reject ? "Writing the message" : ACTIVE_TEXT[p.key];
        const value = p.state === "done" ? (p.note ?? ms(p.ms)) : p.state === "active" ? seconds(elapsed)
          : p.state === "failed" ? p.note : p.state === "skipped" ? "skipped" : p.state === "stopped" ? "stopped" : "";
        return (
          <li key={p.key} className={styles.item} data-state={p.state} aria-current={p.state === "active" ? "step" : undefined}
            aria-label={`${p.label} (${p.tag}): ${STATE_TEXT[p.state]}${p.state === "done" && value ? `, ${value}` : ""}`}>
            <Mark state={p.state} />
            <span className={styles.name}>
              <span className={styles.label}>{p.label}</span>
              <span className={styles.tag}>{p.tag}</span>
            </span>
            <span className={styles.value} data-tone={status?.tone}>{value}</span>
            {p.state === "active" && (footer = (
              <span className={styles.hint} data-tone={status?.tone}>
                {`${doing}…`}{status && <span className={styles.hintNote}>{status.short}</span>}
              </span>
            ))}
          </li>
        );
      })}
    </ol>
    {footer && <p className={styles.footer}>{footer}</p>}
    </div>
  );
}
