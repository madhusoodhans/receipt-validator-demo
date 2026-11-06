/* <img> source for an API image URL: demo images as is, the visitor's own uploads via imageSrc (sent with the visitor id). */
import { useEffect, useState } from "react";
import { imageSrc } from "./api";

export function useImageSrc(url: string | undefined): string | undefined {
  const direct = !url || !url.includes("api/uploads/");
  const [src, setSrc] = useState<string | undefined>(direct ? url : undefined);
  useEffect(() => {
    if (direct) { setSrc(url); return; }
    let live = true;
    setSrc(undefined);
    imageSrc(url!).then((s) => { if (live) setSrc(s); }, () => { if (live) setSrc(undefined); });
    return () => { live = false; };
  }, [url, direct]);
  return src;
}
