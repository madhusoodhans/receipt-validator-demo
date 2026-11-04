/* Page shell: top bar; image source row; the current image beside its pipeline steps; running totals and recent results. */
import { useCallback, useEffect, useState } from "react";
import { ArrowLeft, ImageIcon, RotateCcw } from "lucide-react";
import { Alert } from "./components/arc/alert/alert";
import { Badge } from "./components/arc/badge/badge";
import { ConfirmMorph } from "./components/arc/confirm-morph/confirm-morph";
import { EmptyState } from "./components/arc/empty-state/empty-state";
import { ThemeSwitch, type Theme } from "./components/arc/theme-switch/theme-switch";
import { Tooltip } from "./components/arc/tooltip/tooltip";
import {
  errText, forgetImages, getConfig, getDemo, getHealth, getReceipts, getRejections, getStats, postReset,
  type Config, type DemoItem, type Health, type ReceiptRow, type Service, type RejectionRow, type Stats as StatsT,
} from "./api";
import { Recent } from "./Recent";
import { useRunner } from "./run";
import { DemoStrip, Upload } from "./Source";
import { Stats } from "./Stats";
import { Steps } from "./Steps";
import { useImageSrc } from "./useImage";
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

const SERVICES: [Service, string][] = [["d3", "D3"], ["qwen27b", "Qwen 27B"], ["qwen4b", "Qwen 4B"]];
const TONE = { ok: "success", degraded: "warning", down: "danger" } as const;

/** The server's availability state in plain words (GET /health `message`); the tooltip lists each model. */
function HealthBadge({ health, error }: { health: Health | null; error: boolean }) {
  const status = health?.status ?? (health ? (health.ok ? "ok" : "down") : undefined);
  const tone = error ? "danger" : status ? TONE[status] : "neutral";
  const text = error ? "Demo server offline" : !health ? "Checking" : health.message ?? (health.ok ? "All systems working" : "Receipt checking unavailable");
  const detail = error ? "The demo server does not answer"
    : !health ? "Waiting for the first check"
    : SERVICES.map(([k, n]) => `${n}: ${health[k] === false ? health.detail?.[k] ?? "unavailable" : "working"}`).join(", ")
      + (health.checked_at ? `. Checked ${new Date(health.checked_at).toLocaleTimeString()}` : "");
  return (
    <Tooltip content={detail}>
      <span className={styles.health} tabIndex={0} aria-label={`Health: ${text}. ${detail}`}>
        <Badge tone={tone}>{text}</Badge>
      </span>
    </Tooltip>
  );
}

/** The landing page's address with ?theme= set, so it opens in the theme chosen here; "" when there is none (or it is bad). */
function landingHref(url: string | undefined, theme: Theme) {
  if (!url) return "";
  try {
    const u = new URL(url, location.href);
    u.searchParams.set("theme", theme);
    return u.href;
  } catch { return ""; }
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
  const [config, setConfig] = useState<Config | null>(null);

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
    getConfig().then(setConfig);
    refresh();
    const ping = () => getHealth().then((h) => { setHealth(h); setHealthError(false); }, () => setHealthError(true));
    ping();
    const t = setInterval(ping, 10000);
    return () => clearInterval(t);
  }, [refresh]);

  const busy = !!run?.active;
  // A refused upload (not an image) has nothing to show.
  // An upload's own image is fetched with the visitor id; the local preview shows until it arrives.
  const received = useImageSrc(run?.received?.image_url);
  const image = run?.error?.step === "upload" ? undefined : received || run?.preview;
  const playAll = config?.play_all ?? false;
  const landing = landingHref(config?.landing_url, theme);

  return (
    <div className={styles.shell}>
      <header className={styles.bar}>
        <div className={styles.brand}>
          {landing && (
            <a className={styles.back} href={landing} aria-label="Back to all demos">
              <ArrowLeft size={16} strokeWidth={1.75} aria-hidden="true" />
              <span className={styles.backText}>All demos</span>
            </a>
          )}
        </div>
        <div className={styles.title}>
          <h1>Receipt Intake Demo using vllm-sr &amp; Decision 3.0</h1>
        </div>
        <div className={styles.actions}>
          <HealthBadge health={health} error={healthError} />
          <ConfirmMorph
            label="Reset"
            icon={<RotateCcw size={16} strokeWidth={1.75} />}
            prompt="Clear your results?"
            confirmLabel="Clear"
            pendingLabel="Clearing"
            doneLabel="Cleared"
            disabled={busy || runner.playing}
            onConfirm={async () => { await postReset(); runner.clear(); forgetImages(); await refresh(); }}
          />
          <ThemeSwitch theme={theme} iconOnly variant="rise" onThemeChange={(next) => setTheme(next)} />
        </div>
      </header>

      <div className={styles.body}>
        <main className={styles.main}>
          <DemoStrip
            demo={demo} demoError={demoError} current={run?.name ?? null} busy={busy || runner.playing} playing={runner.playing}
            playAll={playAll}
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
                    description={playAll ? "Upload a receipt photo, click a demo image, or press Play all to run the whole demo set."
                      : "Upload a receipt photo or click a demo image."}
                  />
                </div>
              )}
            </div>
          </div>
        </main>
        <aside className={styles.side} aria-label="Running totals">
          <Stats stats={stats} run={run} />
          <Recent receipts={receipts} rejections={rejections} receiptCount={stats?.extracted} rejectionCount={stats?.rejected} />
        </aside>
      </div>
    </div>
  );
}
