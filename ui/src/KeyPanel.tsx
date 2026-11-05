/* The visitor's vllm-sr API key: entered here (or handed over by the demos landing page), checked by the demo server
 * against the hosted API (one tiny call per system), then used for every model call this tab causes. The key stays in
 * this tab's sessionStorage and the server's memory; it is never shown back, logged or put in a URL. */
import { useEffect, useId, useRef, useState, type FormEvent } from "react";
import { Eye, EyeOff, ExternalLink, KeyRound } from "lucide-react";
import { Button } from "./components/arc/button/button";
import { KEY_URL, postKey, storeKey, type KeyResult } from "./api";
import styles from "./KeyPanel.module.css";

export function KeyPanel(props: {
  /** A key is already in use: the panel is the "Change key" form (Cancel and Forget key shown). */
  hasKey: boolean;
  /** Why a key is needed again (the last check failed, or the key failed during a run). */
  error?: { code?: string; message: string } | null;
  /** Shown while a key handed over by the landing page is being checked. */
  checking?: boolean;
  onSaved: () => void;
  onCancel?: () => void;
  onForget?: () => void;
  /** Bumped to move focus into the key field (the "Change key" action of an error banner). */
  focusSignal?: number;
}) {
  const [value, setValue] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<KeyResult | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const id = useId();
  const error = result && !result.ok ? result : props.error ? { ok: false, ...props.error } : null;

  useEffect(() => { if (props.focusSignal) input.current?.focus(); }, [props.focusSignal]);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const key = value.trim();
    if (!key || busy) return;
    setBusy(true); setResult(null);
    const r = await postKey(key);
    setBusy(false);
    if (r.ok) {
      storeKey(key);
      setValue(""); setShow(false);
      props.onSaved();
    } else {
      setResult(r);
      input.current?.focus();
    }
  };

  const checking = busy || props.checking;
  return (
    <section className={styles.panel} aria-labelledby={`${id}-title`}>
      <div className={styles.head}>
        <span className={styles.icon} aria-hidden="true"><KeyRound size={18} strokeWidth={1.75} /></span>
        <div className={styles.headText}>
          <h2 id={`${id}-title`} className={styles.title}>{props.hasKey ? "Change your API key" : "Enter your vllm-sr API key to run the demo"}</h2>
          <p className={styles.sub}>
            Every check runs on the hosted vllm-sr API with your key. It stays in this tab only and is never saved by the
            demo.
          </p>
        </div>
      </div>
      <form className={styles.form} onSubmit={submit} noValidate>
        <label className={styles.label} htmlFor={`${id}-key`}>API key</label>
        <div className={styles.row}>
          <div className={styles.field} data-invalid={error ? true : undefined}>
            <input
              ref={input} id={`${id}-key`} className={styles.input}
              type={show ? "text" : "password"} value={value} placeholder="sk-vsr-..."
              autoComplete="off" autoCapitalize="off" autoCorrect="off" spellCheck={false}
              aria-invalid={error ? true : undefined} aria-describedby={error ? `${id}-err` : undefined}
              onChange={(e) => { setValue(e.target.value); if (result) setResult(null); }}
            />
            <button type="button" className={styles.reveal} onClick={() => setShow((s) => !s)}
              aria-label={show ? "Hide key" : "Show key"} aria-pressed={show}>
              {show ? <EyeOff size={16} strokeWidth={1.75} aria-hidden="true" /> : <Eye size={16} strokeWidth={1.75} aria-hidden="true" />}
            </button>
          </div>
          <Button type="submit" variant="primary" loading={checking} disabled={!value.trim() && !checking}>
            {checking ? "Checking" : "Check key"}
          </Button>
        </div>
        <p className={styles.status} id={`${id}-err`} role={error ? "alert" : "status"} aria-live="polite">
          {error ? <span className={styles.error}>{error.message}</span>
            : props.checking ? <span className="muted">Checking the key from the demos page</span> : null}
        </p>
        <div className={styles.links}>
          <a href={KEY_URL} target="_blank" rel="noopener noreferrer" className={styles.link}>
            Get an API key <ExternalLink size={13} strokeWidth={1.75} aria-hidden="true" />
          </a>
          {props.hasKey && (
            <span className={styles.secondary}>
              {props.onForget && <Button type="button" variant="ghost" size="sm" onClick={props.onForget}>Forget key</Button>}
              {props.onCancel && <Button type="button" variant="secondary" size="sm" onClick={props.onCancel}>Cancel</Button>}
            </span>
          )}
        </div>
      </form>
    </section>
  );
}
