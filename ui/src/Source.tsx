/* Where images come from: an upload (Upload), or the demo set one at a time or with Play all (SamplePane). */
import { Play, Square } from "lucide-react";
import { Button } from "./components/arc/button/button";
import { FileDropzone } from "./components/arc/file-dropzone/file-dropzone";
import type { DemoItem, RouteName } from "./api";
import styles from "./Source.module.css";

/** Large phone photos are accepted and shrunk in the browser before upload (shrink.ts); the server takes up to 15 MB. */
const MAX = 60 * 1024 * 1024;
const OUTCOME_TEXT: Record<RouteName | "error", string> = { extract: "extracted", review: "needs review", reject: "rejected", error: "error" };

/** locked: no API key yet, so nothing can run (the drop zone is inert and dimmed). */
export function Upload({ busy, locked, onFile }: { busy: boolean; locked?: boolean; onFile: (f: File) => void }) {
  return (
    <div className={styles.drop} inert={locked || undefined} data-locked={locked || undefined}
      title={locked ? "Enter your vllm-sr API key to run the demo" : undefined}>
      <FileDropzone
        label="Upload a photo"
        description="Drop a receipt photo here or choose one"
        note="JPEG, PNG, WebP or HEIC, up to 60 MB (HEIC up to 15 MB). Large photos are shrunk before upload."
        accept="image/jpeg,image/png,image/webp,image/heic,image/heif,.heic,.heif"
        multiple={false}
        maxFiles={1}
        maxSize={MAX}
        listPlacement="inside"
        compactAt={1}
        onFilesChange={(files) => { const f = files[0]; if (f && f.size <= MAX && !busy && !locked) onFile(f); }}
      />
    </div>
  );
}

const EXPECTED_TEXT: Record<RouteName, string> = { extract: "Should be extracted", review: "Should need review", reject: "Should be rejected" };
const GROUPS: [DemoItem["group"], string][] = [["good", "Good receipts"], ["borderline", "Borderline"], ["bad", "Bad uploads"]];

/** Left pane: the demo set as a vertical list of thumbnails in their groups (good, borderline, bad), each with its label
 * and expected outcome, and a dot for the last outcome; Play all (when on) at the bottom. It scrolls on its own. */
export function SamplePane(props: {
  demo: DemoItem[]; demoError: string | null; current: string | null; busy: boolean; playing: boolean;
  /** Off on the hosted demo (server env PLAY_ALL=0): single demo images still run. */
  playAll: boolean;
  /** No API key yet: demo images and Play all are disabled. */
  locked?: boolean;
  outcomes: Record<string, RouteName | "error">;
  onDemo: (d: DemoItem) => void; onPlay: () => void; onStop: () => void;
}) {
  const { demo, current, playing, outcomes } = props;
  const busy = props.busy || !!props.locked;
  const groups = GROUPS.map(([g, title]) => [title, demo.filter((d) => d.group === g)] as const).filter(([, items]) => items.length);
  const showFoot = props.locked || playing || props.playAll;
  return (
    <nav className={styles.pane} aria-label="Sample images you can try">
      <div className={styles.paneHead}>
        <h2 className={styles.h2}>Sample images you can try</h2>
        <span className={styles.count}>{demo.length || ""}</span>
      </div>
      <div className={styles.paneScroll}>
        {props.demoError && <p className={styles.error}>Could not load the demo set: {props.demoError}</p>}
        {groups.map(([title, items]) => (
          <section key={title} className={styles.group} aria-label={title}>
            <h3 className={styles.groupTitle}><span>{title}</span><span className={styles.groupCount}>{items.length}</span></h3>
            <ul className={styles.list}>
              {items.map((d) => {
                const o = outcomes[d.file];
                return (
                  <li key={d.file}>
                    <button type="button" className={styles.item} onClick={() => props.onDemo(d)} disabled={busy}
                      aria-current={current === d.file || undefined} data-outcome={o}
                      aria-label={`${d.label}${o ? `, ${OUTCOME_TEXT[o]}` : ""}`} title={d.label}>
                      <span className={styles.thumb}>
                        <img src={d.image_url} alt="" loading="lazy" />
                        {o && <span className={styles.dot} aria-hidden="true" />}
                      </span>
                      <span className={styles.itemText}>
                        <span className={styles.itemLabel}>{d.label}</span>
                        <span className={styles.itemSub}>{o ? `Last run: ${OUTCOME_TEXT[o]}` : EXPECTED_TEXT[d.expected_route]}</span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </section>
        ))}
      </div>
      {showFoot && (
        <div className={styles.paneFoot}>
          {props.locked && <span className={styles.keyHint}>Enter your vllm-sr API key to try these.</span>}
          {playing ? (
            <Button variant="secondary" onClick={props.onStop}><Square size={16} strokeWidth={1.75} aria-hidden="true" />Stop</Button>
          ) : props.playAll && (
            <Button variant="primary" onClick={props.onPlay} disabled={busy || demo.length === 0}><Play size={16} strokeWidth={1.75} aria-hidden="true" />Play all</Button>
          )}
        </div>
      )}
    </nav>
  );
}
