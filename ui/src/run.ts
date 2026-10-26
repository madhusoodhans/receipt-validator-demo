/* The current check: one image streaming through the pipeline, plus the Play all queue. */
import { useCallback, useEffect, useRef, useState } from "react";
import { shrinkForUpload } from "./shrink";
import { errText, getBaseline, Refused, streamCheck, unreachable, type Comparison, type DemoItem, type EventName, type Events, type RouteName } from "./api";

export interface Run {
  key: number;
  name: string;
  /** Shown before `received` arrives: the demo thumbnail URL or an object URL for an upload. */
  preview?: string;
  startedAt: number;
  /** Milliseconds since start at which each event arrived. */
  at: Partial<Record<EventName, number>>;
  received?: Events["received"];
  d3?: Events["d3"];
  route?: Events["route"];
  message?: Events["message"];
  extract?: Events["extract"];
  sumcheck?: Events["sumcheck"];
  stored?: Events["stored"];
  done?: Events["done"];
  error?: Events["error"];
  /** True while the stream is open. */
  active: boolean;
  /** Set when the user pressed Stop. */
  stopped?: boolean;
}

/** What the Model time panel compares: the last image on its own, or every image of the current Play all. */
export interface Tally { mode: "image" | "playall"; items: (Comparison & { name: string })[]; total?: number }

/** The server sends a keep-alive every 2 s, but a proxy in between (VS Code port forwarding, an SSH tunnel) can hold
 * those back while a slow step runs. The server times out every step itself (Qwen 27B after 120 s) and reports it, so
 * only this much silence means the connection is really gone. */
const SILENCE_MS = 150000;

const sleep = (ms: number, signal: AbortSignal) => new Promise<void>((ok) => {
  const t = setTimeout(ok, ms);
  signal.addEventListener("abort", () => { clearTimeout(t); ok(); }, { once: true });
});

export function useRunner(onFinished: () => void) {
  const [run, setRun] = useState<Run | null>(null);
  const [outcomes, setOutcomes] = useState<Record<string, RouteName | "error">>({});
  const [playing, setPlaying] = useState(false);
  const [tally, setTally] = useState<Tally | null>(null);
  const playingRef = useRef(false);
  const busy = useRef(false);
  const stopOnError = useRef(false);
  const playCtl = useRef<AbortController | null>(null);
  const runCtl = useRef<AbortController | null>(null);
  const keyRef = useRef(0);
  const finished = useRef(onFinished);
  finished.current = onFinished;

  /** A new rejected image has no 27B time yet: the server measures it right after the run. Poll for up to a minute. */
  const fillBaseline = useCallback(async (sha: string) => {
    for (let i = 0; i < 30; i++) {
      await new Promise((r) => setTimeout(r, 2000));
      const b = await getBaseline(sha).catch(() => null);
      if (b?.ms != null) {
        setTally((t) => (t ? { ...t, items: t.items.map((x) => (x.image_sha256 === sha ? { ...x, all_27b_ms: b.ms, all_27b_tokens: b.tokens } : x)) } : t));
        return;
      }
    }
  }, []);

  const start = useCallback(async (input: { demo: DemoItem } | { file: File }) => {
    busy.current = true;
    const key = ++keyRef.current;
    const name = "demo" in input ? input.demo.file : input.file.name;
    const preview = "demo" in input ? input.demo.image_url : URL.createObjectURL(input.file);
    const t0 = performance.now();
    setRun((old) => {
      if (old?.preview?.startsWith("blob:")) URL.revokeObjectURL(old.preview);
      return { key, name, preview, startedAt: Date.now(), at: {}, active: true };
    });
    let route: RouteName | "error" | undefined;
    const onEvent = <E extends EventName>(event: E, data: Events[E]) => {
      if (event === "route") route = (data as Events["route"]).route;
      if (event === "done") {
        const c = (data as Events["done"]).comparison;
        if (c) {
          const item = { ...c, name };
          setTally((t) => (playingRef.current && t?.mode === "playall"
            ? { ...t, items: [...t.items.filter((x) => x.image_sha256 !== c.image_sha256), item] }
            : { mode: "image", items: [item] }));
          if (c.all_27b_ms == null) void fillBaseline(c.image_sha256);
        }
      }
      if (event === "error") route = "error";
      setRun((r) => (r && r.key === key ? { ...r, [event]: data, at: { ...r.at, [event]: performance.now() - t0 } } : r));
    };
    const ctl = new AbortController();
    runCtl.current = ctl;
    let last = performance.now();
    const watchdog = setInterval(() => {
      if (performance.now() - last > SILENCE_MS) ctl.abort("silent");
    }, 1000);
    try {
      let file: File | undefined;
      if ("file" in input) {
        try { file = await shrinkForUpload(input.file); } catch (e) { throw new Refused(errText(e)); }
      }
      await streamCheck(file ? { file } : { demo: (input as { demo: DemoItem }).demo.file }, onEvent, ctl.signal,
        () => { last = performance.now(); });
      if (!route) route = "error";  // the stream ended without a result (server stopped mid-run)
      setRun((r) => (r && r.key === key && !r.done && !r.error
        ? { ...r, error: { step: "connection", message: "The demo server closed the connection before the run finished." } } : r));
    } catch (e) {
      const why = ctl.signal.aborted ? ctl.signal.reason : null;
      if (why === "stopped") {
        route = undefined;
        setRun((r) => (r && r.key === key ? { ...r, stopped: true } : r));
      } else {
        route = "error";
        const message = why === "silent"
          ? `No reply from the demo server for ${SILENCE_MS / 1000} s. The connection was lost or the server stopped.`
          : unreachable(e) ? "Cannot reach the demo server. Is it running (start_server.sh)?" : errText(e);
        setRun((r) => (r && r.key === key ? { ...r, error: { step: e instanceof Refused ? "upload" : "connection", message } } : r));
      }
    } finally {
      clearInterval(watchdog);
      if (runCtl.current === ctl) runCtl.current = null;
      setRun((r) => (r && r.key === key ? { ...r, active: false } : r));
      if (route === "error") stopOnError.current = true;
      if ("demo" in input && route) setOutcomes((o) => ({ ...o, [input.demo.file]: route! }));
      busy.current = false;
      finished.current();
    }
  }, []);

  const playAll = useCallback(async (items: DemoItem[]) => {
    if (playCtl.current) return;
    const ctl = new AbortController();
    playCtl.current = ctl;
    stopOnError.current = false;
    playingRef.current = true;
    setTally({ mode: "playall", items: [], total: items.length });
    setPlaying(true);
    try {
      for (const item of items) {
        if (ctl.signal.aborted) break;
        await start({ demo: item });
        if (ctl.signal.aborted) break;
        if (stopOnError.current) { stopOnError.current = false; break; }  // a failed run ends Play all
        if (ctl.signal.aborted) break;
        await sleep(1800, ctl.signal);
      }
    } finally {
      playCtl.current = null;
      playingRef.current = false;
      setPlaying(false);
    }
  }, [start]);

  /** Stops everything: the run in progress (the server cancels it and stores nothing) and the Play all queue. */
  const stop = useCallback(() => {
    playCtl.current?.abort();
    runCtl.current?.abort("stopped");
  }, []);
  const clear = useCallback(() => { setRun(null); setOutcomes({}); setTally(null); }, []);

  useEffect(() => () => { playCtl.current?.abort(); runCtl.current?.abort("stopped"); }, []);

  return { run, outcomes, playing, busy, tally, start, playAll, stop, clear };
}
