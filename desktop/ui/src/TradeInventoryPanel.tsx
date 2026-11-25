import { useEffect, useState } from "react";
import { api, errorText } from "./api.ts";
import { coreText, currentLanguage, t } from "./i18n.ts";
import type { TradeInventoryReport } from "./types.ts";

export function TradeInventoryPanel({ historyVersion }: { historyVersion: number }) {
  const [day, setDay] = useState("");
  const [report, setReport] = useState<TradeInventoryReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    api.tradeInventory(day, controller.signal).then((value) => {
      if (controller.signal.aborted) return;
      setReport(value);
      setLoading(false);
    }, (reason: unknown) => {
      if (controller.signal.aborted) return;
      setError(errorText(reason));
      setLoading(false);
    });
    return () => controller.abort();
  }, [day, historyVersion]);

  const locale = currentLanguage().replace("_", "-");
  const number = new Intl.NumberFormat(locale);
  const change = new Intl.NumberFormat(locale, { signDisplay: "exceptZero" });
  const time = new Intl.DateTimeFormat(locale, {
    timeZone: "Asia/Shanghai", hour12: false, hour: "2-digit", minute: "2-digit",
  });
  return (
    <div className="trade-inventory" aria-busy={loading}>
      {report && report.days.length > 0 && (
        <label className="trade-inventory-day">
          {t("盘点日期")}
          <select className="select" value={day} onChange={(event) => setDay(event.target.value)}>
            <option value="">{t("最新盘点")}</option>
            {report.days.map((value) => <option key={value}>{value}</option>)}
          </select>
        </label>
      )}
      {loading ? <div className="dempty" role="status">{t("读取中…")}</div>
        : error ? <div className="dempty" role="alert">{error}</div>
          : !report?.items.length ? <div className="dempty">{t("该日没有库存盘点记录")}</div>
            : (
              <table className="trade-inventory-table" aria-label={t("库存日报")}>
                <caption>{report.day}</caption>
                <thead>
                  <tr>
                    <th scope="col">{t("商品")}</th>
                    <th scope="col">{t("库存数量")}</th>
                    {report.comparison_day && (
                      <th scope="col">{t("较 {date} 变化", { date: report.comparison_day })}</th>
                    )}
                    <th scope="col">{t("记录时间（北京时间）")}</th>
                  </tr>
                </thead>
                <tbody>
                  {report.items.map((item) => (
                    <tr key={item.item}>
                      <th scope="row">{coreText(item.name)}</th>
                      <td>{number.format(item.quantity)}</td>
                      {report.comparison_day && (
                        <td>{item.quantity_change == null ? "—" : change.format(item.quantity_change)}</td>
                      )}
                      <td>{time.format(item.observed_at * 1000)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
    </div>
  );
}
