"use strict";
(function () {
  const shellApi = window.okbd2Shell;
  const byId = (id) => document.getElementById(id);

  function render(status) {
    if (!status) {
      return;
    }
    byId("title").textContent = status.title;
    byId("message").textContent = status.message;
    const details = byId("details");
    details.textContent = (status.details || []).join("\n");
    details.hidden = !status.details || status.details.length === 0;
    byId("spinner").hidden = !(status.kind === "starting" || status.kind === "exiting");
    byId("restart").hidden = !status.canRestart;
    byId("quit").hidden = status.kind === "exiting";
    document.body.dataset.kind = status.kind;
  }

  if (!shellApi) {
    byId("message").textContent = "页面没有通过 ok-bd2 外壳打开。";
    return;
  }
  shellApi.getStatus().then(render);
  shellApi.onStatus(render);
  byId("restart").addEventListener("click", () => shellApi.restartCore());
  byId("logs").addEventListener("click", () => shellApi.openLogsFolder());
  byId("quit").addEventListener("click", () => shellApi.quit());
})();
