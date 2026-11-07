/** Project console endpoints (see peon/projects/urls.py). */
export const projectUrls = (pk: string) => {
  const base = `/projects/${pk}`;
  return {
    ops: `${base}/jobs.json`,
    events: `${base}/events/`,
    findings: `${base}/findings.json`,
    triage: (id: string) => `${base}/findings/${encodeURIComponent(id)}/triage/`,
    control: `${base}/control/`,
    memory: `${base}/memory/`,
    memoryJson: `${base}/memory.json`,
  };
};
