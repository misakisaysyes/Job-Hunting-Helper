export async function requestJson(url, options) {
  const response = await fetch(url, { cache: "no-store", ...options });
  const contentType = response.headers.get("Content-Type") || "";
  if (!contentType.includes("application/json")) {
    throw new Error(`API 返回了非 JSON 内容（HTTP ${response.status}）。请重启 web/start.sh，确认 API 端口没有被旧服务占用。`);
  }
  let data;
  try {
    data = await response.json();
  } catch {
    throw new Error(`API 返回的 JSON 无法解析（HTTP ${response.status}）。`);
  }
  if (!response.ok) throw new Error(data.error || `请求失败（${response.status}）`);
  return data;
}

export function jsonOptions(method, body = {}) {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}
