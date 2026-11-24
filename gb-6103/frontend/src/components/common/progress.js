import { el } from "../../utils/dom.js";

/** Show how far a batch has been adjudicated, station by station. */
export function progressMeter(progress, {label = "待验进度"} = {}) {
  const {total = 0, decided = 0, passed = 0, held = 0, pending = 0} = progress || {};
  const ratio = total ? Math.round((decided / total) * 100) : 0;
  return el("div", {class: "progress", "data-testid": "progress", "data-pending": pending},
    el("div", {class: "progress-head"},
      el("strong", {}, `${label} ${decided} / ${total}`),
      el("span", {class: "progress-pending"}, pending ? `待验 ${pending} 站` : "全部裁决完成")),
    el("div", {class: "progress-track", role: "progressbar", "aria-valuemin": 0,
      "aria-valuemax": total, "aria-valuenow": decided, "aria-label": label},
      el("span", {class: "progress-fill", style: `width:${ratio}%`})),
    el("dl", {class: "progress-facts"},
      el("div", {}, el("dt", {}, "通过"), el("dd", {}, passed)),
      el("div", {}, el("dt", {}, "搁置"), el("dd", {}, held)),
      el("div", {}, el("dt", {}, "待验"), el("dd", {}, pending))));
}
