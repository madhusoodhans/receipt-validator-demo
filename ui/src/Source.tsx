/* Where images come from: an upload (Upload), or the demo set one at a time or with Play all (DemoStrip). */
import { Play, Square } from "lucide-react";
import { Button } from "./components/arc/button/button";
import { FileDropzone } from "./components/arc/file-dropzone/file-dropzone";
import type { DemoItem, RouteName } from "./api";
import styles from "./Source.module.css";

/** Large phone photos are accepted and shrunk in the browser before upload (shrink.ts); the server takes up to 15 MB. */
const MAX = 60 * 1024 * 1024;
const OUTCOME_TEXT: Record<RouteName | "error", string> = { extract: "extracted", review: "needs review", reject: "rejected", error: "error" };

export function Upload({ busy, onFile }: { busy: boolean; onFile: (f: File) => void }) {
  return (
    <div className={styles.drop}>
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
        onFilesChange={(files) => { const f = files[0]; if (f && f.size <= MAX && !busy) onFile(f); }}
      />
    </div>
  );
}

export function DemoStrip(props: {
  demo: DemoItem[]; demoError: string | null; current: string | null; busy: boolean; playing: boolean;
  outcomes: Record<string, RouteName | "error">;
  onDemo: (d: DemoItem) => void; onPlay: () => void; onStop: () => void;
}) {
  const { demo, current, busy, playing, outcomes } = props;
  const groups: [string, DemoItem[]][] = [["Good receipts", demo.filter((d) => d.group === "good")], ["Borderline", demo.filter((d) => d.group === "borderline")], ["Bad uploads", demo.filter((d) => d.group === "bad")]];
  return (
    <section className={styles.demo} aria-label="Demo images">
      <div className={styles.demoHead}>
        <h2 className={styles.h2}>Demo images</h2>
        {playing ? (
          <Button variant="secondary" onClick={props.onStop}><Square size={16} strokeWidth={1.75} aria-hidden="true" />Stop</Button>
        ) : (
          <Button variant="primary" onClick={props.onPlay} disabled={busy || demo.length === 0}><Play size={16} strokeWidth={1.75} aria-hidden="true" />Play all</Button>
        )}
      </div>
      {props.demoError && <p className={styles.error}>Could not load the demo set: {props.demoError}</p>}
      <div className={styles.groups}>
        {groups.filter(([, items]) => items.length).map(([title, items]) => (
          <div key={title} className={styles.group}>
            <h3 className={styles.groupTitle}>{title}</h3>
            <ul className={styles.strip}>
              {items.map((d) => {
                const o = outcomes[d.file];
                return (
                  <li key={d.file}>
                    <button type="button" className={styles.thumb} onClick={() => props.onDemo(d)} disabled={busy}
                      aria-current={current === d.file || undefined} data-outcome={o}
                      aria-label={`${d.label}${o ? `, ${OUTCOME_TEXT[o]}` : ""}`} title={d.label}>
                      <img src={d.image_url} alt="" loading="lazy" />
                      {o && <span className={styles.dot} aria-hidden="true" />}
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}
