// Pages designed in a later stage: 记录 comes with stage 3a.
import { t } from "./i18n.ts";

function Later({ title, what }: { title: string; what: string }) {
  return (
    <div className="page">
      <div className="ph">
        <div className="l">
          <h1 className="title">{title}</h1>
        </div>
      </div>
      <div className="card empty" style={{ marginTop: 18 }}>
        {what}
      </div>
    </div>
  );
}

export function RecordsPage() {
  return <Later title={t("记录")} what={t("运行历史列表、筛选和失败详情在阶段 3 接入；各任务最近 3 次运行见任务详情。")} />;
}
