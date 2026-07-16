import assert from "node:assert/strict";
import test from "node:test";

import {
  extractRequestHistory,
  navigateRequestHistory,
  shouldNavigateRequestHistory,
} from "../app/chat-history.ts";

const baseKeyContext = {
  value: "",
  selectionStart: 0,
  selectionEnd: 0,
  historyActive: false,
  isComposing: false,
  altKey: false,
  ctrlKey: false,
  metaKey: false,
  shiftKey: false,
};

test("extracts only current conversation user requests and preserves duplicates", () => {
  assert.deepEqual(extractRequestHistory([
    { role: "assistant", content: "欢迎" },
    { role: "user", content: "问题一" },
    { role: "user", content: "问题一" },
    { role: "assistant", content: "回答" },
    { role: "user", content: "问题二" },
  ]), ["问题一", "问题一", "问题二"]);
});

test("walks older and newer requests and restores the unsent draft", () => {
  const history = ["问题一", "问题二", "问题三"];
  let state = { index: null, savedDraft: "" };

  let result = navigateRequestHistory(history, state, "尚未发送的草稿", "older");
  assert.equal(result.draft, "问题三");
  assert.deepEqual(result.state, { index: 2, savedDraft: "尚未发送的草稿" });

  result = navigateRequestHistory(history, result.state, result.draft, "older");
  assert.equal(result.draft, "问题二");
  result = navigateRequestHistory(history, result.state, result.draft, "older");
  assert.equal(result.draft, "问题一");

  const oldest = navigateRequestHistory(history, result.state, result.draft, "older");
  assert.equal(oldest.draft, "问题一");
  assert.equal(oldest.state.index, 0);

  result = navigateRequestHistory(history, oldest.state, oldest.draft, "newer");
  assert.equal(result.draft, "问题二");
  result = navigateRequestHistory(history, result.state, result.draft, "newer");
  assert.equal(result.draft, "问题三");
  result = navigateRequestHistory(history, result.state, result.draft, "newer");
  assert.equal(result.draft, "尚未发送的草稿");
  assert.deepEqual(result.state, { index: null, savedDraft: "" });
});

test("does not consume keys when history navigation cannot move", () => {
  assert.equal(
    navigateRequestHistory([], { index: null, savedDraft: "" }, "草稿", "older").handled,
    false,
  );
  assert.equal(
    navigateRequestHistory(["问题"], { index: null, savedDraft: "" }, "草稿", "newer").handled,
    false,
  );
});

test("starts history navigation only at multiline boundaries", () => {
  const value = "第一行\n第二行\n第三行";
  assert.equal(shouldNavigateRequestHistory({
    ...baseKeyContext, key: "ArrowUp", value, selectionStart: 2, selectionEnd: 2,
  }), true);
  assert.equal(shouldNavigateRequestHistory({
    ...baseKeyContext, key: "ArrowUp", value, selectionStart: 6, selectionEnd: 6,
  }), false);
  assert.equal(shouldNavigateRequestHistory({
    ...baseKeyContext, key: "ArrowDown", value, selectionStart: value.length, selectionEnd: value.length,
  }), true);
  assert.equal(shouldNavigateRequestHistory({
    ...baseKeyContext, key: "ArrowDown", value, selectionStart: 2, selectionEnd: 2,
  }), false);
});

test("keeps composition, selections and modified arrow keys unchanged", () => {
  assert.equal(shouldNavigateRequestHistory({
    ...baseKeyContext, key: "ArrowUp", isComposing: true,
  }), false);
  assert.equal(shouldNavigateRequestHistory({
    ...baseKeyContext, key: "ArrowUp", shiftKey: true,
  }), false);
  assert.equal(shouldNavigateRequestHistory({
    ...baseKeyContext, key: "ArrowUp", selectionEnd: 2,
  }), false);
  assert.equal(shouldNavigateRequestHistory({
    ...baseKeyContext, key: "ArrowDown", historyActive: true, value: "多行\n内容", selectionStart: 0, selectionEnd: 0,
  }), true);
});
