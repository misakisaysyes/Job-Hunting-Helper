export async function executeMonitoringBatch(items, action, onProgress) {
  let success = 0;
  let processed = 0;
  const failures = [];
  const perform = async (item) => {
    try {
      await action.perform(item);
      success += 1;
    } catch (error) {
      failures.push(`${item.company || item.recruiter || item.conversation_id}：${error.message || "操作失败"}`);
    } finally {
      processed += 1;
      onProgress(processed, items.length);
    }
  };
  if (action.parallel && !action.stopOnFailure) {
    await Promise.all(items.map(perform));
  } else {
    for (const item of items) {
      await perform(item);
      if (failures.length && action.stopOnFailure) break;
    }
  }
  return { success, processed, failures };
}
export async function executeRecordedMonitoringBatch(items, action, onState, api) {
  let state = await api.start(action.batchAction, items.map(action.toBatchItem));
  const runId = state.run_id;
  onState(state);
  while (state.status === "running") {
    await api.wait();
    state = await api.read(runId);
    if (state.run_id !== runId) throw new Error("批量任务编号已变化，请刷新查看任务记录。");
    onState(state);
  }
  return state;
}
