export const jobKey = (job) => `${job.source_platform}:${job.source_job_id}`;

export function toggleSelectedJob(selected, job) {
  const next = new Map(selected);
  const key = jobKey(job);
  if (next.has(key)) next.delete(key);
  else next.set(key, job);
  return next;
}

export function selectPageJobs(selected, jobs, checked) {
  const next = new Map(selected);
  for (const job of jobs) {
    if (checked) next.set(jobKey(job), job);
    else next.delete(jobKey(job));
  }
  return next;
}
