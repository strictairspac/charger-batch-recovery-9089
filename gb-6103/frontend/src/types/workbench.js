/**
 * @typedef {{total:number, decided:number, passed:number, held:number, ports:number, pending:number}} Progress
 * @typedef {{id:number, code:string, title:string, description:string, payload:Object, created_by:string}} Case
 * @typedef {{id:number, case_id:number, case_title:string, status:string, parameter:number, result:Object|null, progress:Progress}} Run
 * @typedef {{run_id:number, actor:string, note:string, created_at:number}} Review
 */

export function active(run) {
  return run?.status === "queued" || run?.status === "running";
}

/** Reject a malformed configuration before rendering its navigation. */
export function validateConfig(config) {
  if (!config || !Array.isArray(config.modules) || config.modules.length !== 4 ||
      !Number.isInteger(config.default_parameter) || !config.title) {
    throw new Error("工作台配置不完整");
  }
  return config;
}
