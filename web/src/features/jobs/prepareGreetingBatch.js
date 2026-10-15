export async function prepareGreetingJob(job, {
  allowBelowThreshold = false, startGreeting, generateGreeting, onJobChange,
}) {
  let transitioned = false;
  try {
    let current = job;
    if (job.job_status === "scored") {
      const result = await startGreeting(job, allowBelowThreshold
        ? { allow_below_threshold: true } : {});
      if (!result?.job) throw new Error("流转接口未返回岗位信息");
      current = result.job;
      transitioned = true;
      onJobChange(current);
    }
    const result = await generateGreeting(current);
    if (result?.job) onJobChange(result.job);
    if (result?.error) throw new Error(result.error);
    if (!result?.job) throw new Error("生成接口未返回岗位信息");
    return { transitioned, generated: true };
  } catch (error) {
    return { transitioned, generated: false, error };
  }
}
