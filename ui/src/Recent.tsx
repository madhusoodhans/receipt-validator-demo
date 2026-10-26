/* Recent receipts and rejections (GET /api/receipts, /api/rejections), compact. */
import { useState } from "react";
import { Badge } from "./components/arc/badge/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./components/arc/tabs/tabs";
import type { ReceiptRow, RejectionRow } from "./api";
import { badReasons, money, problemLabel, receiptWhole, timeAgo } from "./format";
import styles from "./Recent.module.css";

/** The lists hold the latest 50; the tab counts are the database totals (GET /api/stats), so they can be larger. */
export function Recent({ receipts, rejections, receiptCount, rejectionCount }: {
  receipts: ReceiptRow[]; rejections: RejectionRow[]; receiptCount?: number; rejectionCount?: number;
}) {
  const [tab, setTab] = useState("receipts");
  const now = Date.now();
  return (
    <Tabs value={tab} onValueChange={setTab} className={styles.root}>
      <TabsList aria-label="Recent results">
        <TabsTrigger value="receipts">{`Receipts ${receiptCount ?? receipts.length}`}</TabsTrigger>
        <TabsTrigger value="rejections">{`Rejections ${rejectionCount ?? rejections.length}`}</TabsTrigger>
      </TabsList>
      <TabsContent value="receipts">
        {receipts.length === 0 ? <p className={styles.empty}>No receipts stored yet.</p> : (
          <ul className={styles.list}>
            {receipts.map((r) => (
              <li key={r.id} className={styles.row}>
                <img className={styles.thumb} src={r.image_url} alt="" loading="lazy" />
                <div className={styles.main}>
                  <span className={styles.title}>{r.receipt.store_name || r.file}</span>
                  <span className={styles.meta}>
                    {[r.receipt.date, r.receipt.items.length + (r.receipt.items.length === 1 ? " item" : " items")].filter(Boolean).join(", ")}
                  </span>
                </div>
                <div className={styles.side}>
                  <span className={styles.amount}>{money(r.receipt.total, r.receipt.currency, receiptWhole(r.receipt))} <span className="muted">{r.receipt.currency}</span></span>
                  <span className={styles.flags}>
                    {r.needs_review ? <Badge tone="warning" size="sm">Review</Badge> : null}
                    {!r.sumcheck_ok ? <Badge tone="danger" size="sm">Sum off</Badge> : null}
                    <span className={styles.meta}>{`#${r.id}, ${timeAgo(r.created_at, now)}`}</span>
                  </span>
                </div>
              </li>
            ))}
          </ul>
        )}
      </TabsContent>
      <TabsContent value="rejections">
        {rejections.length === 0 ? <p className={styles.empty}>No rejections yet.</p> : (
          <ul className={styles.list}>
            {rejections.map((r) => (
              <li key={r.id} className={styles.row}>
                <img className={styles.thumb} src={r.image_url} alt="" loading="lazy" />
                <div className={styles.main}>
                  <span className={styles.title}>{badReasons(r.reasons).map((x) => problemLabel(x.key)).join(", ") || "Rejected"}</span>
                  <span className={styles.message} title={r.message}>{r.message}</span>
                </div>
                <div className={styles.side}><span className={styles.meta}>{`#${r.id}, ${timeAgo(r.created_at, now)}`}</span></div>
              </li>
            ))}
          </ul>
        )}
      </TabsContent>
    </Tabs>
  );
}
