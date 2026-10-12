export const monitoringKey = (item) => `${item.platform}:${item.conversation_id}`;

export function toggleSelectedConversation(selected, item) {
  const next = new Map(selected);
  const key = monitoringKey(item);
  if (next.has(key)) next.delete(key);
  else next.set(key, item);
  return next;
}

export function selectPageConversations(selected, items, checked) {
  const next = new Map(selected);
  for (const item of items) {
    if (checked) next.set(monitoringKey(item), item);
    else next.delete(monitoringKey(item));
  }
  return next;
}
