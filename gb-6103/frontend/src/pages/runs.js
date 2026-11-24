import { api } from "../api/workbench.js";
import { empty, pageHeading, section } from "../components/common/panel.js";
import { progressMeter } from "../components/common/progress.js";
import { status } from "../components/common/status.js";
import { table } from "../components/common/table.js";
import { poll } from "../hooks/poll.js";
import { active } from "../types/workbench.js";
import { el } from "../utils/dom.js";
import { time } from "../utils/format.js";

function reportState(run) {
  const note = run.status === "failed" ? run.failure
    : run.status === "done" ? run.result?.summary
    : "后台逐站验收，页面会自动更新；中断后从首个未裁决站点续跑。";
  return el("div", {class: "stack", id: "run-state"}, status(run.status),
    progressMeter(run.progress, {label: "待验进度"}),
    el("p", {}, note),
    run.status === "done" ? el("a", {class: "button", href: `#evidence/${run.id}`}, "查看业务证据") : null);
}

export async function runsPage(config, route, signal) {
  const items = await api.runs();
  const latest = route.id ? await api.run(route.id, signal) : items[0];
  const detail = latest ? reportState(latest) : empty("进入资料页，选用一个业务样本并提交执行。");
  async function refresh() {
    try {
      const next = await api.run(latest.id, signal);
      if (!signal.aborted) {
        detail.replaceChildren(...reportState(next).childNodes);
        const badge = document.querySelector(`[data-run="${next.id}"] .status`);
        if (badge) badge.replaceWith(status(next.status));
      }
      return active(next);
    } catch (error) {
      if (!signal.aborted) detail.append(el("p", {role: "alert"}, error.message));
      return false;
    }
  }
  if (latest && active(latest)) poll(refresh, signal);
  return el("div", {class: "page"},
    pageHeading(config.modules[2], config.parameter_label),
    latest ? section(latest.case_title, detail) : detail,
    section("执行批次", items.length ? table(["资料", "编号", "参数", "进度", "状态", "提交时间"],
      items.map(run => [el("a", {"data-run": run.id, href: `#runs/${run.id}`}, run.case_title), run.id,
        run.parameter, `${run.progress.decided}/${run.progress.total}`,
        el("span", {"data-run": run.id}, status(run.status)), time(run.created_at)]), "执行批次") :
      empty("暂无执行记录。")));
}
