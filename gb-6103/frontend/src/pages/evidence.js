import { api } from "../api/workbench.js";
import { errorText, field } from "../components/common/fields.js";
import { empty, pageHeading, section } from "../components/common/panel.js";
import { progressMeter } from "../components/common/progress.js";
import { status } from "../components/common/status.js";
import { resultTable } from "../components/common/table.js";
import { submit } from "../hooks/submit.js";
import { el } from "../utils/dom.js";
import { cell, download, time } from "../utils/format.js";

function reviewForm(id, saved) {
  const form = el("form", {id: "review-form"},
    field("复核意见", el("textarea", {name: "note", required: true, minlength: 3, maxlength: 500, rows: 3})),
    errorText(), el("button", {type: "submit"}, "签署复核"));
  submit(form, async values => saved(await api.submitReview(id, {note: values.get("note")})));
  return form;
}

function signature(review) {
  return el("div", {"data-testid": "reviewed", class: "signature"},
    el("strong", {}, `已签署 · ${review.actor}`), el("p", {}, review.note), el("time", {}, time(review.created_at)));
}

/** Signing only opens once every station in the batch has been adjudicated. */
function signingPanel(run, user, saved, onSigned) {
  if (saved) return signature(saved);
  if (run.status !== "done" || run.progress.pending) {
    const wait = run.progress.total
      ? `待验 ${run.progress.pending} 站，整批裁决完成后开放签核。`
      : "整批裁决完成后开放签核。";
    return el("div", {class: "stack", "data-testid": "signing-locked"},
      status(run.status), el("p", {class: "empty"}, wait));
  }
  return user.role === "reviewer" ? reviewForm(run.id, onSigned) : empty("待复核员签署。");
}

export async function evidencePage(config, user, route) {
  const all = await api.runs();
  const run = route.id ? await api.run(route.id) : all.find(item => item.status === "done") ?? all[0];
  if (!run) return el("div", {class: "page"},
    pageHeading(config.modules[3], null), empty("成功执行一个批次后可以查看、签核和下载证据。"),
    el("a", {href: "#runs"}, config.modules[2]));

  const review = await api.review(run.id);
  const issue = errorText();
  const button = el("button", {type: "button", disabled: run.status !== "done", onclick: async () => {
    button.disabled = true;
    issue.textContent = "";
    try {
      await download(await api.export(run.id), `evidence-${run.id}.json`);
    } catch (error) {
      issue.textContent = error.message;
    } finally {
      button.disabled = run.status !== "done";
    }
  }}, "导出证据");
  const signing = el("div", {});
  const onSigned = data => signing.replaceChildren(signature(data));
  signing.append(signingPanel(run, user, review, onSigned));
  const options = el("select", {"aria-label": "选择批次", onchange: event => {
    location.hash = `evidence/${event.target.value}`;
  }}, all.map(item => el("option", {value: item.id, selected: item.id === run.id},
    `#${item.id} ${item.case_title}${item.status === "done" ? "" : " · 未完成"}`)));
  const verdict = run.result ? el("section", {class: "feature evidence-summary"},
    el("div", {}, el("h2", {}, "批次判定"), el("p", {}, run.result.summary)),
    el("details", {}, el("summary", {}, "证据摘要"), el("code", {}, run.digest))) :
    el("p", {class: "empty"}, run.failure || "该批次尚未完成，整批裁决后可在此签核。");
  return el("div", {class: "page evidence"},
    pageHeading(config.modules[3], run.case_title, button),
    issue, options,
    progressMeter(run.progress, {label: "待验进度"}),
    verdict,
    run.result ? el("dl", {class: "metrics"}, Object.entries(run.result.metrics).map(([label, value]) =>
      el("div", {}, el("dt", {}, label), el("dd", {}, cell(value))))) : null,
    run.result ? section("业务结果明细", run.result.rows.length ? resultTable(run.result.rows) : empty("此批次无明细。")) : null,
    section("复核签署", signing));
}
