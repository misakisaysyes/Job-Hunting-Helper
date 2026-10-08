export async function executeMonitoringBatch(items, action, onProgress) {
  let success = 0;
  let processed = 0;
  const failures = [];
  for (const item of items) {
    try {
      await action.perform(item);
      success += 1;
    } catch (error) {
      failures.push(`${item.company || item.recruiter || item.conversation_id}：${error.message || "操作失败"}`);
    } finally {
      processed += 1;
      onProgress(processed, items.length);
    }
    if (failures.length && action.stopOnFailure) break;
  }
  return { success, processed, failures };
}
