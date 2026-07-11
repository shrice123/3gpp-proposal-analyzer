import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);
  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the 3GPP proposal workbench", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  const html = await response.text();
  assert.match(html, /<title>3GPP 提案洞察<\/title>/);
  assert.match(html, /class="brand-mark">3GPP</);
  assert.match(html, /提案工作区/);
  assert.match(html, /All agenda items/);
  assert.match(html, /All sources/);
  assert.match(html, /Enter 发送，Option\+Enter 换行/);
  assert.match(html, /报告设置/);
  assert.match(html, /配置新的大模型/);
  assert.match(html, /按公司总结主要观点/);
  assert.match(html, /按核心问题归纳各公司观点/);
  assert.match(html, /基于分析结果生成 Word \/ PowerPoint 报告/);
  assert.match(html, />自动<\/option>/);
  assert.match(html, />通用问答<\/option>/);
  assert.match(html, /终止分析|会话/);
  assert.doesNotMatch(html, /分析文档|把一场会议的提案/);
  assert.doesNotMatch(html, /Load failed/);
});

test("keeps the enterprise visual and accessibility preflight rules", async () => {
  const [css, app] = await Promise.all([
    readFile(new URL("../app/globals.css", import.meta.url), "utf8"),
    readFile(new URL("../app/proposal-app.tsx", import.meta.url), "utf8"),
  ]);
  assert.match(css, /IBM Plex Sans/);
  assert.match(css, /focus-visible/);
  assert.match(css, /prefers-reduced-motion:\s*reduce/);
  assert.match(css, /\.proposal-pane\.mobile-open/);
  assert.match(app, /nativeEvent\.isComposing/);
  assert.match(app, /isSelectAll:\s*true/);
  assert.match(app, /proposal-thread-/);
  assert.match(app, /answer_delta/);
  assert.match(app, /信息安全提醒/);
  assert.match(app, /state=archived/);
  assert.match(app, /chats\/batch-delete/);
  assert.match(app, /全选当前结果/);
  assert.match(app, /批量永久删除/);
  assert.match(app, /toggleAllFilteredArchived/);
  assert.match(app, /session-backdrop/);
  assert.match(app, /Promise\.allSettled/);
  assert.match(app, /chat_mode: sendMode/);
  assert.match(app, /chat-progress-dock/);
  assert.match(app, /回到最新消息/);
  assert.match(app, /client_request_id/);
  assert.doesNotMatch(app, /processing-message/);
  assert.doesNotMatch(app, /显示已归档/);
  assert.doesNotMatch(app, /未配置可用的视觉模型/);
});
