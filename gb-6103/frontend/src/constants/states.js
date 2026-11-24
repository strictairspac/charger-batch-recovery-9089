export const States = Object.freeze({
  queued: "等待执行", running: "验收中", done: "已完成", failed: "执行失败",
});

export const Actions = Object.freeze({
  create_case: "登记资料", enqueue: "提交执行", resume: "中断续跑",
  done: "执行完成", failed: "执行失败", review: "复核签署",
});

export const Active = Object.freeze(["queued", "running"]);
