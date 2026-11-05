/* Page shell: top bar; sample images on the left; upload and the current image beside its pipeline steps; running totals and recent results on the right. */
import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, ImageIcon, RotateCcw } from "lucide-react";
import { Button } from "./components/arc/button/button";
import { Alert } from "./components/arc/alert/alert";
import { Badge } from "./components/arc/badge/badge";
import { ConfirmMorph } from "./components/arc/confirm-morph/confirm-morph";
import { EmptyState } from "./components/arc/empty-state/empty-state";
import { ThemeSwitch, type Theme } from "./components/arc/theme-switch/theme-switch";
import { Tooltip } from "./components/arc/tooltip/tooltip";
import {
  deleteKey, dropStoredKey, errText, forgetImages, getConfig, getDemo, getHealth, getKey, getReceipts, getRejections,
  getStats, postKey, postReset, storedKey, takeHandoff,
  type Config, type DemoItem, type Health, type ReceiptRow, type Service, type RejectionRow, type Stats as StatsT,
} from "./api";
import { KeyPanel } from "./KeyPanel";
import { KeyMenu, type KeyMenuStatus } from "./KeyMenu";
import { Recent } from "./Recent";
import { useRunner } from "./run";
import { SamplePane, Upload } from "./Source";
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

const SERVICES: [Service, string][] = [["d3", "D3"], ["extract", "LLM (reading)"], ["retake", "LLM (retake messages)"]];
const TONE = { ok: "success", degraded: "warning", down: "danger" } as const;

/** The server's availability state in plain words (GET /health `message`); the tooltip lists each model. */
function HealthBadge({ health, error }: { health: Health | null; error: boolean }) {
  const status = health?.status ?? (health ? (health.ok ? "ok" : "down") : undefined);
  const tone = error ? "danger" : status ? TONE[status] : "neutral";
  const text = error ? "Demo server offline" : !health ? "Checking" : health.message ?? (health.ok ? "All systems working" : "Receipt checking unavailable");
  const keyText = { set: "your key is set", missing: "no key entered yet", server: "the server's key is in use" } as const;
  const detail = error ? "The demo server does not answer"
    : !health ? "Waiting for the first check"
    : SERVICES.map(([k, n]) => `${n}: ${health[k] === false ? health.detail?.[k] ?? "unavailable" : "working"}`).join(", ")
      + (health.checked_at ? `. Checked ${new Date(health.checked_at).toLocaleTimeString()}` : "")
      + (health.key ? `. API key: ${keyText[health.key]}` : "");
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
  // API key: "loading" until the first GET /api/key, "checking" while a key handed over by the landing page is checked,
  // "ready" when this visitor has a working key (or the server has its own), else "needed".
  const [keyStatus, setKeyStatus] = useState<"loading" | "checking" | "ready" | "needed">("loading");
  const [keySet, setKeySet] = useState(false);  // this visitor gave a key (not the server's own)
  const [keyRequired, setKeyRequired] = useState(true);
  const [keyError, setKeyError] = useState<{ code?: string; message: string } | null>(null);
  const [runKeyError, setRunKeyError] = useState<{ code?: string; message: string } | null>(null);
  const [keyOk, setKeyOk] = useState(false);  // "Key works" notice after a check from this page
  const [changing, setChanging] = useState(false);
  const [focusKey, setFocusKey] = useState(0);

  const needKey = useCallback((why: { code?: string; message: string } | null) => {
    dropStoredKey();
    setKeySet(false); setKeyOk(false); setKeyError(why);
    setKeyStatus((s) => (keyRequiredRef.current ? "needed" : s === "loading" ? "ready" : s));
  }, []);
  const keyRequiredRef = useRef(true);
  const keyStarted = useRef(false);  // the key check on load runs once (StrictMode runs effects twice in development)
  keyRequiredRef.current = keyRequired;

  const refresh = useCallback(async () => {
    try {
      const [s, a, b] = await Promise.all([getStats(), getReceipts(), getRejections()]);
      setStats(s); setReceipts(a); setRejections(b); setLoadError(null);
    } catch (e) { setLoadError(errText(e)); }
  }, []);
  const runner = useRunner(refresh, useCallback((e: { code?: string; message: string }) => {
    // The server dropped the key: ask for one again, and say why at the top of the page.
    setRunKeyError(e); needKey(e); setChanging(false);
  }, [needKey]));
  const { run } = runner;

  useEffect(() => {
    getDemo().then(setDemo, (e) => setDemoError(errText(e)));
    getConfig().then(setConfig);
    refresh();
    // Key on load: a key handed over by the landing page (index.html moved it from #key= to sessionStorage) is checked
    // and the result shown; else the server's state; after a server restart the tab's stored key is sent again quietly.
    const loadKey = async () => {
      const handoff = takeHandoff();
      const stored = storedKey();
      let st: Awaited<ReturnType<typeof getKey>> | null = null;
      try { st = await getKey(); } catch { /* old or offline server: treat as needing a key */ }
      const required = st?.required ?? true;
      setKeyRequired(required); keyRequiredRef.current = required;
      if (stored && (handoff || !st?.set)) {
        setKeyStatus("checking");
        const r = await postKey(stored);
        if (r.ok) { setKeySet(true); setKeyError(null); setKeyOk(handoff); setKeyStatus("ready"); }
        else needKey({ code: r.code, message: r.message ?? "The key check failed. Try again." });
        return;
      }
      if (st?.set) { setKeySet(true); setKeyStatus("ready"); return; }
      if (st?.failed) { needKey(st.failed); return; }
      setKeyStatus(required ? "needed" : "ready");
    };
    if (!keyStarted.current) { keyStarted.current = true; loadKey(); }
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
  const locked = keyStatus !== "ready";
  const showPanel = keyStatus === "needed" || keyStatus === "checking" || changing;
  const openKey = () => { setChanging(true); setKeyOk(false); setFocusKey((n) => n + 1); };
  const forgetKey = async () => { await deleteKey(); setChanging(false); needKey(null); };
  // Top bar key control (KeyMenu): hidden only when the server has its own key and this visitor gave none.
  const keyMenu: KeyMenuStatus | null = keyStatus === "loading" || keyStatus === "checking" ? "checking"
    : keyStatus === "needed" ? "none" : keySet ? "ready" : null;

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
          {keyMenu && (
            <KeyMenu status={keyMenu} busy={busy || runner.playing} onEnter={() => setFocusKey((n) => n + 1)}
              onChange={openKey} onForget={forgetKey} />
          )}
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
        <SamplePane
          demo={demo} demoError={demoError} current={run?.name ?? null} busy={busy || runner.playing} playing={runner.playing}
          playAll={playAll}
          locked={locked}
          outcomes={runner.outcomes}
          onDemo={(d) => runner.start({ demo: d })}
          onPlay={() => runner.playAll(demo)}
          onStop={runner.stop}
        />
        <main className={styles.main}>
          {runKeyError && (
            <Alert tone="danger" title={runKeyError.code === "no_key" ? "API key needed" : "Your API key stopped working"}
              onDismiss={() => setRunKeyError(null)}>
              <span className={styles.keyAlert}>
                <span>{runKeyError.message}</span>
                <Button variant="secondary" size="sm" onClick={() => setFocusKey((n) => n + 1)}>Change key</Button>
              </span>
            </Alert>
          )}
          {keyOk && !showPanel && (
            <Alert tone="success" title="Key works for decisions and chat" onDismiss={() => setKeyOk(false)}>
              Every check in this tab now runs on the hosted vllm-sr API with your key.
            </Alert>
          )}
          {showPanel && (
            <KeyPanel
              hasKey={keySet}
              checking={keyStatus === "checking"}
              error={keyError}
              focusSignal={focusKey}
              onSaved={() => { setKeySet(true); setKeyError(null); setRunKeyError(null); setKeyOk(true); setChanging(false); setKeyStatus("ready"); }}
              onCancel={keySet ? () => setChanging(false) : undefined}
              onForget={keySet ? forgetKey : undefined}
            />
          )}
          {loadError && <Alert tone="warning" title="Could not load totals">{loadError}</Alert>}
          <div className={styles.stage}>
            <div className={styles.left}>
              <Upload busy={busy || runner.playing} locked={locked} onFile={(f) => runner.start({ file: f })} />
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
              {run ? <Steps run={run} onStop={runner.stop} models={config?.models} /> : (
                <div className={styles.emptyStage}>
                  <EmptyState
                    icon={<ImageIcon size={24} />}
                    title="Pick an image to check"
                    description={locked ? "Enter your vllm-sr API key above to run the demo."
                      : playAll ? "Upload a receipt photo, pick a sample image on the left, or press Play all to run the whole demo set."
                      : "Upload a receipt photo or pick a sample image on the left."}
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
      <footer className={styles.footer}>
        Uploads are processed by the hosted vllm-sr API (research and evaluation use). Please do not upload receipts with
        personal data.
      </footer>
    </div>
  );
}
