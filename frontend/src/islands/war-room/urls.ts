/** Project console endpoints (see peon/projects/urls.py). */
export const projectUrls = (pk: string) => {
  const base = `/projects/${pk}`;
  const job = (id: string) => `${base}/jobs/${encodeURIComponent(id)}`;
  return {
    detail: `${base}/`,
    ops: `${base}/jobs.json`,
    events: `${base}/events/`,
    findings: `${base}/findings.json`,
    triage: (id: string) => `${base}/findings/${encodeURIComponent(id)}/triage/`,
    control: `${base}/control/`,
    chat: `${base}/chat/`,
    inputs: `${base}/inputs/`,
    inputDelete: `${base}/inputs/delete/`,
    reportView: (path: string) => `${base}/reports/${path}/view/`,
    reportFile: (path: string) => `${base}/reports/${path}`,
    terminal: `${base}/terminal/`,
    jobLive: (id: string) => `${job(id)}/`,
    jobStart: (id: string) => `${job(id)}/start/`,
    jobSteer: (id: string) => `${job(id)}/steer/`,
    jobAddChild: (id: string) => `${job(id)}/add-child/`,
    jobRoles: (id: string) => `${job(id)}/suggest-roles.json`,
  };
};
export type ProjectUrls = ReturnType<typeof projectUrls>;
