export type RequestMessage = {
  role: string;
  content: string;
};

export type RequestHistoryDirection = "older" | "newer";

export type RequestHistoryState = {
  index: number | null;
  savedDraft: string;
};

export type RequestHistoryKeyContext = {
  key: string;
  value: string;
  selectionStart: number | null;
  selectionEnd: number | null;
  historyActive: boolean;
  isComposing: boolean;
  altKey: boolean;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
};

export function extractRequestHistory(messages: readonly RequestMessage[]): string[] {
  return messages
    .filter((message) => message.role === "user" && message.content.trim())
    .map((message) => message.content);
}

export function shouldNavigateRequestHistory(context: RequestHistoryKeyContext): boolean {
  if (
    !["ArrowUp", "ArrowDown"].includes(context.key) ||
    context.isComposing ||
    context.altKey ||
    context.ctrlKey ||
    context.metaKey ||
    context.shiftKey ||
    context.selectionStart === null ||
    context.selectionEnd === null ||
    context.selectionStart !== context.selectionEnd
  ) {
    return false;
  }
  if (context.historyActive) return true;
  if (context.key === "ArrowUp") {
    return !context.value.slice(0, context.selectionStart).includes("\n");
  }
  return !context.value.slice(context.selectionEnd).includes("\n");
}

export function navigateRequestHistory(
  history: readonly string[],
  state: RequestHistoryState,
  currentDraft: string,
  direction: RequestHistoryDirection,
): { handled: boolean; draft: string; state: RequestHistoryState } {
  if (!history.length) {
    return { handled: false, draft: currentDraft, state };
  }

  if (direction === "older") {
    const nextIndex = state.index === null
      ? history.length - 1
      : Math.max(0, state.index - 1);
    return {
      handled: true,
      draft: history[nextIndex],
      state: {
        index: nextIndex,
        savedDraft: state.index === null ? currentDraft : state.savedDraft,
      },
    };
  }

  if (state.index === null) {
    return { handled: false, draft: currentDraft, state };
  }
  if (state.index < history.length - 1) {
    const nextIndex = state.index + 1;
    return {
      handled: true,
      draft: history[nextIndex],
      state: { ...state, index: nextIndex },
    };
  }
  return {
    handled: true,
    draft: state.savedDraft,
    state: { index: null, savedDraft: "" },
  };
}
