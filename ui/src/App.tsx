/* Page shell: top bar; image source row; the current image beside its pipeline steps; running totals and recent results. */
import { useCallback, useEffect, useState } from "react";
import { ImageIcon, RotateCcw } from "lucide-react";
import { Alert } from "./components/arc/alert/alert";
import { Badge } from "./components/arc/badge/badge";
import { ConfirmMorph } from "./components/arc/confirm-morph/confirm-morph";
import { EmptyState } from "./components/arc/empty-state/empty-state";
import { ThemeSwitch, type Theme } from "./components/arc/theme-switch/theme-switch";
import { Tooltip } from "./components/arc/tooltip/tooltip";
import {
  errText, getDemo, getHealth, getReceipts, getRejections, getStats, postReset,
  type DemoItem, type Health, type ReceiptRow, type Service, type RejectionRow, type Stats as StatsT,
} from "./api";
import { Recent } from "./Recent";
import { useRunner } from "./run";
import { DemoStrip, Upload } from "./Source";
import { Stats } from "./Stats";
import { Steps } from "./Steps";
import styles from "./App.module.css";

function useTheme(): [Theme, (t: Theme) => void] {
  const [theme, setThemeState] = useState<Theme>(() => (document.documentElement.dataset.theme === "dark" ? "dark" : "light"));
  const setTheme = (t: Theme) => {
    const apply = () => { document.documentElement.dataset.theme = t; setThemeState(t); };
    const reduce = matchMedia("(prefers-reduced-motion: reduce)").matches;
    const doc = document as Document & { startViewTransition?: (cb: () => void) => void };
    if (doc.startViewTransition && !reduce) doc.startViewTransition(apply); else apply();
    try { localStorage.setItem("theme", t); } catch { /* private window */ }
  };
  return [theme, setTheme];
}

const SERVICES: [Service, string][] = [["router", "Router"], ["d3", "D3"], ["qwen27b", "Qwen 27B"], ["qwen4b", "Qwen 4B"]];

/** One failure in plain words: vision off is its own problem (the 27B is up but cannot read images). */
function failure(key: Service, name: string, why?: string) {
  if (why === "image input off") return `Vision off on ${name}`;
  if (why === "not served by the Router") return `${name} not on the Router`;
  return `${name} down`;
}

function HealthBadge({ health, error }: { health: Health | null; error: boolean }) {
  const failed = SERVICES.filter(([k]) => health && health[k] === false);
  // With the Router down every probe fails; naming the Router alone says it.
  const shown = failed.some(([k]) => k === "router") ? failed.filter(([k]) => k === "router") : failed;
  const tone = error ? "danger" : !health ? "neutral" : health.ok ? "success" : "warning";
  const text = error ? "Backend offline" : !health ? "Checking"
    : shown.length ? shown.map(([k, n]) => failure(k, n, health.detail?.[k])).join(", ") : "All models up";
  const detail = error ? "GET /health failed"
    : !health ? "Waiting for /health"
    : SERVICES.map(([k, n]) => `${n}: ${health[k] ? "up" : health.detail?.[k] ?? "down"}`).join(", ")
      + (health.checked_at ? `. Checked ${new Date(health.checked_at).toLocaleTimeString()}` : "");
  return (
    <Tooltip content={detail}>
      <span className={styles.health} tabIndex={0} aria-label={`Health: ${text}. ${detail}`}>
        <Badge tone={tone}>{text}</Badge>
      </span>
    </Tooltip>
  );
}

export function App() {
  const [theme, setTheme] = useTheme();
  const [demo, setDemo] = useState<DemoItem[]>([]);
  const [demoError, setDemoError] = useState<string | null>(null);
  const [stats, setStats] = useState<StatsT | null>(null);
  const [receipts, setReceipts] = useState<ReceiptRow[]>([]);
  const [rejections, setRejections] = useState<RejectionRow[]>([]);
  const [health, setHealth] = useState<Health | null>(null);
  const [healthError, setHealthError] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const [s, a, b] = await Promise.all([getStats(), getReceipts(), getRejections()]);
      setStats(s); setReceipts(a); setRejections(b); setLoadError(null);
    } catch (e) { setLoadError(errText(e)); }
  }, []);
  const runner = useRunner(refresh);
  const { run } = runner;

  useEffect(() => {
    getDemo().then(setDemo, (e) => setDemoError(errText(e)));
    refresh();
    const ping = () => getHealth().then((h) => { setHealth(h); setHealthError(false); }, () => setHealthError(true));
    ping();
    const t = setInterval(ping, 10000);
    return () => clearInterval(t);
  }, [refresh]);

  // A new rejected upload is timed on Qwen 27B in the background; refresh the totals until that lands.
  const pending = stats?.comparison?.pending_baseline ?? 0;
  useEffect(() => {
    if (!pending) return;
    const t = setTimeout(refresh, 3000);
    return () => clearTimeout(t);
  }, [pending, stats, refresh]);

  const busy = !!run?.active;
  // Share of bad uploads in the demo set (labels.json), for the note on how the saving depends on it.
  const badShare = demo.length ? demo.filter((d) => d.group === "bad").length / demo.length : null;
  // A refused upload (not an image) has nothing to show.
  const image = run?.error?.step === "upload" ? undefined : run?.received?.image_url || run?.preview;

  return (
    <div className={styles.shell}>
      <header className={styles.bar}>
        <div className={styles.brand}>
          <span className={styles.chip}>MI300X, gfx942</span>
        </div>
        <div className={styles.title}>
          <h1>Receipt Intake Demo using vllm-sr &amp; Decision 3.0</h1>
        </div>
        <div className={styles.actions}>
          <HealthBadge health={health} error={healthError} />
          <ConfirmMorph
            label="Reset"
            icon={<RotateCcw size={16} strokeWidth={1.75} />}
            prompt="Clear all results?"
            confirmLabel="Clear"
            pendingLabel="Clearing"
            doneLabel="Cleared"
            disabled={busy || runner.playing}
            onConfirm={async () => { await postReset(); runner.clear(); await refresh(); }}
          />
          <ThemeSwitch theme={theme} iconOnly variant="rise" onThemeChange={(next) => setTheme(next)} />
        </div>
      </header>

      <div className={styles.body}>
        <main className={styles.main}>
          <DemoStrip
            demo={demo} demoError={demoError} current={run?.name ?? null} busy={busy || runner.playing} playing={runner.playing}
            outcomes={runner.outcomes}
            onDemo={(d) => runner.start({ demo: d })}
            onPlay={() => runner.playAll(demo)}
            onStop={runner.stop}
          />
          {loadError && <Alert tone="warning" title="Could not load totals">{loadError}</Alert>}
          <div className={styles.stage}>
            <div className={styles.left}>
              <Upload busy={busy || runner.playing} onFile={(f) => runner.start({ file: f })} />
              {run && image && (
                <figure className={styles.figure}>
                  <img key={image} className={styles.image} src={image} alt={`Current image: ${run.name}`} />
                  <figcaption className={styles.caption}>
                    <span className={styles.fileName}>{run.received?.file || run.name}</span>
                    {run.received && run.received.width > 0 && <span className="muted num">{`${run.received.width} x ${run.received.height}`}</span>}
                  </figcaption>
                </figure>
              )}
            </div>
            <div className={styles.steps}>
              {run ? <Steps run={run} onStop={runner.stop} /> : (
                <div className={styles.emptyStage}>
                  <EmptyState
                    icon={<ImageIcon size={24} />}
                    title="Pick an image to check"
                    description="Upload a receipt photo, click a demo image, or press Play all to run the whole demo set."
                  />
                </div>
              )}
            </div>
          </div>
        </main>
        <aside className={styles.side} aria-label="Running totals">
          <Stats stats={stats} tally={runner.tally} badShare={badShare} />
          <Recent receipts={receipts} rejections={rejections} receiptCount={stats?.extracted} rejectionCount={stats?.rejected} />
        </aside>
      </div>
    </div>
  );
}
