"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { FilterableMultiSelect } from "@carbon/react";
import { Add, ArrowDown, ArrowUp, Close, Document, Download, Folder, Send, TrashCan, Upload, View } from "@carbon/icons-react";
import "@carbon/styles/css/styles.css";
import { extractRequestHistory, navigateRequestHistory, shouldNavigateRequestHistory } from "./chat-history";

type Proposal = {
  id: string;
  tdoc: string;
  title: string;
  source: string;
  primary_source: string;
  canonical_source: string;
  agenda_item: string;
  agenda_description: string;
  status: string;
  download_state: string;
  analysis_state: string;
  preparation_state: string;
  preparation_error?: string;
  file_url?: string;
  file_name?: string;
  file_kind?: string;
  file_size?: number;
  file_modified?: string;
  metadata_source?: "index" | "directory";
  analyzable?: number;
};

type Meeting = { id: string; name: string; proposal_count: number; source_url: string };
type Job = {
  id: string;
  kind: string;
  status: string;
  progress: number;
  message: string;
  error?: string;
  updated_at?: string;
  partial_result?: { answer?: string; thread_id?: string; message_id?: string };
  result?: {
    file_name?: string;
    completed_count?: number;
    failed_count?: number;
    thread_id?: string;
    answer?: string;
    citations?: Citation[];
    artifacts?: ChatArtifact[];
    artifact?: ReportFileArtifact;
    message_id?: string;
    successful_proposals?: string[];
    failed_proposals?: { tdoc: string; error: string }[];
    vision_complete?: boolean;
    analysis_coverage?: { analyzed: number; selected: number; mode?: string };
  };
};
type Citation = { tdoc: string; title: string; artifact_name?: string; page_or_slide?: number };
type OutlineItem = { title: string; content?: string; purpose?: string; role?: string };
type ReportOutlineArtifact = { kind: "report_outline"; report_id: string; format: "docx" | "pptx"; title: string; outline: OutlineItem[]; proposal_count: number; message_count?: number; template_id?: string | null; warning?: string };
type ReportFileArtifact = { kind: "report_file"; report_id: string; format: "docx" | "pptx"; title: string; file_name: string; proposal_count?: number; template_name?: string; preview_available: boolean; preview_url?: string | null; download_url: string; quality?: { passed: boolean; warnings: string[] } };
type ChatArtifact = ReportOutlineArtifact | ReportFileArtifact;
type Message = { id?: string; role: "user" | "assistant"; content: string; citations?: Citation[]; artifacts?: ChatArtifact[]; status?: string; model_option_id?: string };
type TemplateInfo = { id: string; name: string; kind: "docx" | "pptx"; file_name?: string; warnings?: string[]; strict_compatible?: boolean };
type SourceAlias = { id: string; alias: string; canonical_name: string; built_in: boolean };
type FilterOption = { id: string; label: string; isSelectAll?: boolean };
type FtpFolder = { kind: "folder"; name: string; url: string; modified?: string };
type FtpFile = { kind: "file"; name: string; url: string; modified?: string; size?: number; extension?: string; analyzable: boolean; proposal?: Proposal | null };
type Breadcrumb = { label: string; url: string };
type BrowseResult = { workspace: Meeting; breadcrumbs: Breadcrumb[]; folders: FtpFolder[]; files: FtpFile[]; proposals: Proposal[]; index_status: "ready" | "failed" | "absent"; index_error?: string; facets: { agendas: string[]; sources: string[] } };
type ChatThread = { id: string; meeting_id: string; meeting_name?: string; title: string; default_model_option_id?: string; archived: number; active_job_id?: string; message_count?: number; report_count?: number; updated_at: string };
type ModelOption = { id: string; profile_id: string; model: string; label: string; provider: string; deployment: "external" | "local"; is_default: boolean };
type Section = "workspace" | "settings";
type ChatMode = "auto" | "proposal" | "general";
type SessionRuntime = {
  messages: Message[];
  draft: string;
  job: Job | null;
  busy: boolean;
  error?: string;
  chatMode: ChatMode;
  nextMode?: ChatMode;
  historyIndex: number | null;
  historyDraft: string;
};

const API =
  (typeof window !== "undefined" && window.proposalDesktop?.apiBase) ||
  process.env.NEXT_PUBLIC_API_BASE ||
  "http://127.0.0.1:8765";
const DEFAULT_FTP_URL = "https://www.3gpp.org/ftp/tsg_sa/wg2_arch/";

class ApiRequestError extends Error {
  constructor(message: string, public status: number, public detail: unknown) { super(message); }
}

function userFacingError(value: unknown) {
  const message = value instanceof Error ? value.message : String(value || "");
  if (/unable to open database|sqlite|database is locked|请求失败（5\d\d）/i.test(message)) return "本地数据服务暂时繁忙，请稍后重试";
  return message || "请求暂时无法完成，请重试";
}

const DEMO_PROPOSALS: Proposal[] = [
  {
    id: "demo-1",
    tdoc: "S2-2600841",
    title: "Key issue on AI-assisted policy optimization in the 5GC",
    source: "Huawei, HiSilicon",
    primary_source: "Huawei",
    canonical_source: "Huawei",
    agenda_item: "20.3.1",
    agenda_description: "Core Network Enhanced Support for AI/ML",
    status: "available",
    download_state: "idle",
    analysis_state: "idle",
    preparation_state: "idle",
  },
  {
    id: "demo-2",
    tdoc: "S2-2600927",
    title: "Architecture assumptions for enhanced sensing exposure",
    source: "Ericsson",
    primary_source: "Ericsson",
    canonical_source: "Ericsson",
    agenda_item: "20.6.0",
    agenda_description: "Study on Architecture for 6G System",
    status: "available",
    download_state: "downloaded",
    analysis_state: "analyzed",
    preparation_state: "ready",
  },
  {
    id: "demo-3",
    tdoc: "S2-2601044",
    title: "Discussion on trusted non-3GPP access discovery",
    source: "Nokia",
    primary_source: "Nokia",
    canonical_source: "Nokia",
    agenda_item: "19.4.2",
    agenda_description: "Access traffic steering, switching and splitting",
    status: "revised",
    download_state: "idle",
    analysis_state: "idle",
    preparation_state: "idle",
  },
];

const INITIAL_MESSAGES: Message[] = [
  {
    role: "assistant",
    content: "勾选左侧提案后，可以询问技术变化、提案差异、风险与建议，也可以直接要求生成 Word 或 PowerPoint 报告。",
  },
];

async function api<T>(path: string, init?: RequestInit, retries = 0): Promise<T> {
  let lastError: unknown;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), 10_000);
    const abort = () => controller.abort();
    init?.signal?.addEventListener("abort", abort, { once: true });
    try {
      const response = await fetch(`${API}${path}`, {
        ...init,
        signal: controller.signal,
        headers: init?.body instanceof FormData ? init.headers : { "Content-Type": "application/json", ...(init?.headers || {}) },
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        const detail = body.detail;
        const detailMessage = typeof detail === "object" && detail && "message" in detail ? String(detail.message) : typeof detail === "string" ? detail : `请求失败（${response.status}）`;
        const message = response.status >= 500 ? "本地服务暂时繁忙，请稍后重试" : detailMessage;
        throw new ApiRequestError(message, response.status, detail);
      }
      return await response.json();
    } catch (error) {
      lastError = error;
      if (init?.signal?.aborted || attempt === retries) break;
      await new Promise((resolve) => window.setTimeout(resolve, 300 * (attempt + 1)));
    } finally {
      window.clearTimeout(timer);
      init?.signal?.removeEventListener("abort", abort);
    }
  }
  if (lastError instanceof DOMException && lastError.name === "AbortError") throw new Error("会话加载超时，请重试");
  if (lastError instanceof Error && !/Load failed|Failed to fetch/i.test(lastError.message)) throw lastError;
  throw new Error("本地服务暂时无法响应，请稍后重试");
}

function streamJob(id: string, onUpdate: (job: Job) => void, onDelta?: (delta: string, messageId?: string) => void): Promise<Job | null> {
  if (typeof EventSource === "undefined") return Promise.resolve(null);
  return new Promise((resolve) => {
    const stream = new EventSource(`${API}/api/jobs/${id}/events`);
    const handleProgress = (event: MessageEvent) => {
      const current = JSON.parse(event.data) as Job;
      onUpdate(current);
    };
    stream.addEventListener("progress", handleProgress as EventListener);
    stream.addEventListener("answer_delta", ((event: MessageEvent) => {
      const data = JSON.parse(event.data) as { delta: string; message_id?: string };
      onDelta?.(data.delta, data.message_id);
    }) as EventListener);
    stream.addEventListener("recoverable_error", (() => { stream.close(); resolve(null); }) as EventListener);
    ["completed", "failed", "cancelled"].forEach((type) => stream.addEventListener(type, ((event: MessageEvent) => {
      const current = JSON.parse(event.data) as Job;
      onUpdate(current);
      if (["completed", "failed", "cancelled"].includes(current.status || type)) {
        stream.close();
        resolve({ ...current, status: current.status || type });
      }
    }) as EventListener));
    stream.onerror = () => {
      stream.close();
      resolve(null);
    };
  });
}

function formatBytes(value: number | undefined) {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let size = value;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(index ? 1 : 0)} ${units[index]}`;
}

function proposalStatus(proposal: Proposal) {
  if (proposal.analysis_state === "analyzed") return "已分析";
  if (proposal.analysis_state === "analyzing") return "模型分析中";
  if (proposal.analysis_state === "failed" || proposal.preparation_state === "failed") return "处理失败";
  if (proposal.preparation_state === "ready") return "本地准备就绪";
  if (proposal.preparation_state === "extracting") return "文稿提取中";
  if (proposal.preparation_state === "downloading" || proposal.download_state === "downloading") return "下载中";
  if (proposal.preparation_state === "queued") return "等待下载";
  if (proposal.download_state === "downloaded") return "已下载";
  return "待处理";
}

export function ProposalApp() {
  const [section, setSection] = useState<Section>("workspace");
  const [connected, setConnected] = useState(false);
  const [, setMeetings] = useState<Meeting[]>([]);
  const [meeting, setMeeting] = useState<Meeting | null>(null);
  const [sourceUrl, setSourceUrl] = useState("");
  const [proposals, setProposals] = useState<Proposal[]>(DEMO_PROPOSALS);
  const [agendas, setAgendas] = useState(["all", "20.3.1", "20.6.0", "19.4.2"]);
  const [sources, setSources] = useState(["all", "Ericsson", "Huawei", "Nokia"]);
  const [folders, setFolders] = useState<FtpFolder[]>([]);
  const [directoryFiles, setDirectoryFiles] = useState<FtpFile[]>([]);
  const [breadcrumbs, setBreadcrumbs] = useState<Breadcrumb[]>([]);
  const [indexStatus, setIndexStatus] = useState<"ready" | "failed" | "absent">("absent");
  const [fileType, setFileType] = useState("all");
  const [selectedAgendas, setSelectedAgendas] = useState<string[]>([]);
  const [selectedSources, setSelectedSources] = useState<string[]>([]);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("未连接本地服务，当前显示示例数据");
  const [job, setJob] = useState<Job | null>(null);
  const [threadId, setThreadId] = useState<string | null>(null);
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const [sessionsOpen, setSessionsOpen] = useState(false);
  const [threadLoadError, setThreadLoadError] = useState("");
  const [modelOptions, setModelOptions] = useState<ModelOption[]>([]);
  const [modelOptionId, setModelOptionId] = useState("");
  const [reportFormat, setReportFormat] = useState<"docx" | "pptx">("docx");
  const [reportLanguage, setReportLanguage] = useState<"en" | "zh">("en");
  const [templateMode, setTemplateMode] = useState<"strict" | "extend">("strict");
  const [templates, setTemplates] = useState<TemplateInfo[]>([]);
  const [templateId, setTemplateId] = useState<string>("");
  const [reportSettingsOpen, setReportSettingsOpen] = useState(false);
  const [mobileScopeOpen, setMobileScopeOpen] = useState(false);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const requestedPreparation = useRef(new Set<string>());
  const [sessionStates, setSessionStates] = useState<Record<string, SessionRuntime>>({
    __new__: { messages: INITIAL_MESSAGES, draft: "", job: null, busy: false, chatMode: "auto", historyIndex: null, historyDraft: "" },
  });
  const messageRequestRef = useRef<{ sequence: number; controller?: AbortController }>({ sequence: 0 });
  const activeThreadRef = useRef<string | null>(null);
  const filterRequestRef = useRef<{ sequence: number; controller?: AbortController }>({ sequence: 0 });
  const filterStateRef = useRef<{ meetingId?: string; agendas: string[]; sources: string[]; search: string }>({ agendas: [], sources: [], search: "" });
  const messageListRef = useRef<HTMLDivElement | null>(null);
  const chatInputRef = useRef<HTMLTextAreaElement | null>(null);
  const followLatestRef = useRef(false);
  const [showLatestButton, setShowLatestButton] = useState(false);
  const healthFailures = useRef(0);
  const lastProposalRefreshRef = useRef(0);
  const desktopRestartAttempted = useRef(false);
  const [runtime, setRuntime] = useState<{ disk_free?: number; data_dir?: string; components?: { name: string; installed: boolean }[] }>({});
  const [onboardingOpen, setOnboardingOpen] = useState(false);
  const activeSessionKey = threadId || "__new__";
  const activeSession = sessionStates[activeSessionKey] || {
    messages: INITIAL_MESSAGES, draft: "", job: null, busy: false, chatMode: "auto" as ChatMode, historyIndex: null, historyDraft: "",
  };
  const messages = activeSession.messages;
  const question = activeSession.draft;
  const chatJob = activeSession.job;
  const chatBusy = activeSession.busy;
  const chatMode = activeSession.chatMode;

  function updateSession(key: string, update: Partial<SessionRuntime> | ((current: SessionRuntime) => SessionRuntime)) {
    setSessionStates((all) => {
      const current = all[key] || {
        messages: INITIAL_MESSAGES, draft: "", job: null, busy: false, chatMode: "auto" as ChatMode, historyIndex: null, historyDraft: "",
      };
      return { ...all, [key]: typeof update === "function" ? update(current) : { ...current, ...update } };
    });
  }

  function setQuestion(value: string) {
    updateSession(activeSessionKey, { draft: value, historyIndex: null, historyDraft: "" });
  }

  useEffect(() => { activeThreadRef.current = threadId; }, [threadId]);
  useEffect(() => { filterStateRef.current = { meetingId: meeting?.id, agendas: selectedAgendas, sources: selectedSources, search }; }, [meeting, selectedAgendas, selectedSources, search]);

  function scrollToLatest(force = false) {
    const element = messageListRef.current;
    if (!element || (!force && !followLatestRef.current)) return;
    element.scrollTop = element.scrollHeight;
    followLatestRef.current = true;
    setShowLatestButton(false);
  }

  useEffect(() => {
    void bootstrap();
    setOnboardingOpen(localStorage.getItem("proposal-onboarding-complete") !== "1");
    // The one-time bootstrap intentionally owns its initial filter snapshot.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const check = async () => {
      try {
        const health = await api<{ ok: boolean }>("/api/health");
        if (health.ok) { healthFailures.current = 0; desktopRestartAttempted.current = false; setConnected(true); }
      } catch {
        healthFailures.current += 1;
        if (healthFailures.current >= 3) {
          setConnected(false);
          if (window.proposalDesktop && !desktopRestartAttempted.current) {
            desktopRestartAttempted.current = true;
            await window.proposalDesktop.repairRuntime().catch(() => undefined);
          }
        }
      }
    };
    const timer = window.setInterval(() => void check(), 5000);
    return () => window.clearInterval(timer);
  }, []);
  useEffect(() => {
    if (!sessionsOpen) return;
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") setSessionsOpen(false); };
    window.addEventListener("keydown", close);
    return () => window.removeEventListener("keydown", close);
  }, [sessionsOpen]);

  useEffect(() => {
    if (!meeting || selected.size === 0 || !connected) return;
    const pending = Array.from(selected).filter((id) => !requestedPreparation.current.has(id));
    if (!pending.length) return;
    const timer = window.setTimeout(() => {
      pending.forEach((id) => requestedPreparation.current.add(id));
      void startPreparation(meeting.id, pending);
    }, 800);
    return () => window.clearTimeout(timer);
    // Only selection and connection changes should restart the debounce window.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, meeting, connected]);

  async function bootstrap() {
    const [healthResult, meetingsResult, runtimeResult, templatesResult, modelsResult] = await Promise.allSettled([
      api<{ ok: boolean }>("/api/health", undefined, 2),
      api<Meeting[]>("/api/meetings", undefined, 2),
      api<typeof runtime>("/api/runtime/status"),
      api<TemplateInfo[]>("/api/templates"),
      api<ModelOption[]>("/api/model-options"),
    ]);
    setConnected(healthResult.status === "fulfilled" && healthResult.value.ok);
    if (runtimeResult.status === "fulfilled") setRuntime(runtimeResult.value);
    if (templatesResult.status === "fulfilled") setTemplates(templatesResult.value);
    const availableModels = modelsResult.status === "fulfilled" ? modelsResult.value : [];
    setModelOptions(availableModels);
    setModelOptionId(availableModels.find((item) => item.is_default)?.id || availableModels[0]?.id || "");
    if (meetingsResult.status === "fulfilled") setMeetings(meetingsResult.value);
    if (healthResult.status === "fulfilled" && healthResult.value.ok) {
      await browseDirectory(DEFAULT_FTP_URL, availableModels).catch((error) => {
        setProposals([]);
        setNotice(error instanceof Error ? error.message : "默认目录加载失败");
      });
    } else setNotice("本地服务暂时无法连接");
  }

  async function loadThreads(meetingId: string) {
    try {
      const rows = await api<ChatThread[]>(`/api/chats?meeting_id=${encodeURIComponent(meetingId)}&state=active`, undefined, 2);
      setThreads(rows);
      setThreadLoadError("");
      return rows;
    } catch (error) {
      setThreadLoadError(error instanceof Error ? error.message : "会话加载暂时失败");
      throw error;
    }
  }

  async function openThread(thread: ChatThread, available = modelOptions) {
    messageRequestRef.current.controller?.abort();
    const controller = new AbortController();
    const sequence = messageRequestRef.current.sequence + 1;
    messageRequestRef.current = { sequence, controller };
    setThreadId(thread.id);
    setSessionsOpen(false);
    setThreadLoadError("");
    try {
      const history = await api<Message[]>(`/api/chats/${thread.id}/messages`, { signal: controller.signal }, 2);
      if (messageRequestRef.current.sequence !== sequence) return;
      updateSession(thread.id, (current) => ({
        ...current, messages: history.length ? history : INITIAL_MESSAGES, error: undefined, historyIndex: null, historyDraft: "",
      }));
      requestAnimationFrame(() => requestAnimationFrame(() => scrollToLatest(true)));
    } catch (error) {
      if (controller.signal.aborted) return;
      setThreadLoadError(error instanceof Error ? error.message : "会话加载暂时失败");
      updateSession(thread.id, (current) => ({ ...current, error: "会话加载暂时失败" }));
      return;
    }
    if (thread.default_model_option_id && available.some((item) => item.id === thread.default_model_option_id)) {
      setModelOptionId(thread.default_model_option_id);
    }
    localStorage.setItem(`proposal-thread-${thread.meeting_id}`, thread.id);
    if (thread.active_job_id) {
      try {
        const activeResult = await api<{ job: Job | null }>(`/api/chats/${thread.id}/active-job`, undefined, 2);
        const active = activeResult.job;
        if (active && !["completed", "failed", "cancelled"].includes(active.status)) {
          updateSession(thread.id, { busy: true, job: active });
          void pollChatJob(active.id, thread.id, true);
        }
      } catch { /* A stale active-job marker is harmless and is cleared by the backend. */ }
    }
  }

  async function newThread() {
    if (!meeting) return;
    const created = await api<ChatThread>("/api/chats", {
      method: "POST", body: JSON.stringify({ meeting_id: meeting.id, title: "新会话", proposal_ids: Array.from(selected), default_model_option_id: modelOptionId || null }),
    });
    setThreads((items) => [created, ...items]);
    await openThread(created);
  }

  async function renameThread(thread: ChatThread) {
    const title = window.prompt("输入新的会话名称", thread.title)?.trim();
    if (!title) return;
    const updated = await api<ChatThread>(`/api/chats/${thread.id}`, { method: "PATCH", body: JSON.stringify({ title }) });
    setThreads((items) => items.map((item) => item.id === updated.id ? { ...item, ...updated } : item));
  }

  async function archiveThread(thread: ChatThread, archived: boolean) {
    await api(`/api/chats/${thread.id}`, { method: "PATCH", body: JSON.stringify({ archived }) });
    if (thread.id === threadId) {
      setThreadId(null);
    }
    if (meeting) await loadThreads(meeting.id);
  }

  async function loadProposals(meetingId: string, nextAgendas = selectedAgendas, nextSources = selectedSources, nextSearch = search, pruneInvalid = true) {
    filterRequestRef.current.controller?.abort();
    const controller = new AbortController();
    const sequence = filterRequestRef.current.sequence + 1;
    filterRequestRef.current = { sequence, controller };
    const params = new URLSearchParams({ search: nextSearch, limit: "2500" });
    (nextAgendas.length ? nextAgendas : ["all"]).forEach((item) => params.append("agenda", item));
    (nextSources.length ? nextSources : ["all"]).forEach((item) => params.append("source", item));
    const data = await api<{ items: Proposal[]; facets: { agendas: string[]; sources: string[] } }>(
      `/api/meetings/${meetingId}/proposals?${params}`, { signal: controller.signal }, 1,
    );
    if (filterRequestRef.current.sequence !== sequence) return;
    if (pruneInvalid) {
      const validAgendas = nextAgendas.filter((item) => data.facets.agendas.includes(item));
      const validSources = nextSources.filter((item) => data.facets.sources.includes(item));
      if (validAgendas.length !== nextAgendas.length || validSources.length !== nextSources.length) {
        setSelectedAgendas(validAgendas);
        setSelectedSources(validSources);
        setNotice("已移除与当前筛选条件不匹配的选项");
        await loadProposals(meetingId, validAgendas, validSources, nextSearch, false);
        return;
      }
    }
    setProposals(data.items);
    setAgendas(data.facets.agendas);
    setSources(data.facets.sources);
    setSelected(new Set());
  }

  async function refreshProposalStates() {
    const snapshot = filterStateRef.current;
    if (!snapshot.meetingId) return;
    const requestSequence = filterRequestRef.current.sequence;
    const params = new URLSearchParams({ search: snapshot.search, limit: "2500" });
    (snapshot.agendas.length ? snapshot.agendas : ["all"]).forEach((item) => params.append("agenda", item));
    (snapshot.sources.length ? snapshot.sources : ["all"]).forEach((item) => params.append("source", item));
    const data = await api<{ items: Proposal[] }>(`/api/meetings/${snapshot.meetingId}/proposals?${params}`);
    if (filterRequestRef.current.sequence === requestSequence) setProposals(data.items);
  }

  async function startPreparation(meetingId: string, proposalIds: string[]) {
    try {
      const created = await api<{ id: string }>("/api/preparation-jobs", {
        method: "POST",
        body: JSON.stringify({ meeting_id: meetingId, proposal_ids: proposalIds }),
      });
      await pollBackgroundPreparation(created.id);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "后台预处理启动失败");
    }
  }

  async function retryPreparation(proposal: Proposal) {
    if (!meeting) return;
    requestedPreparation.current.delete(proposal.id);
    requestedPreparation.current.add(proposal.id);
    await startPreparation(meeting.id, [proposal.id]);
  }

  async function pollBackgroundPreparation(id: string) {
    const streamed = await streamJob(id, () => { void refreshProposalStates(); });
    if (streamed) {
      await refreshProposalStates();
      return;
    }
    while (true) {
      const current = await api<Job>(`/api/jobs/${id}`);
      await refreshProposalStates();
      if (["completed", "failed", "cancelled"].includes(current.status)) break;
      await new Promise((resolve) => setTimeout(resolve, 900));
    }
  }

  async function browseDirectory(url: string, availableModels = modelOptions) {
    const target = url.trim() || DEFAULT_FTP_URL;
    const result = await api<BrowseResult>("/api/ftp/browse", {
      method: "POST", body: JSON.stringify({ url: target }),
    });
    setMeeting(result.workspace);
    setMeetings((current) => [result.workspace, ...current.filter((item) => item.id !== result.workspace.id)]);
    setBreadcrumbs(result.breadcrumbs);
    setFolders(result.folders);
    setDirectoryFiles(result.files);
    setIndexStatus(result.index_status);
    setProposals(result.proposals);
    setAgendas(result.facets.agendas);
    setSources(result.facets.sources);
    setSelected(new Set());
    setSelectedAgendas([]);
    setSelectedSources([]);
    setSearch("");
    setFileType("all");
    requestedPreparation.current.clear();
    filterStateRef.current = { meetingId: result.workspace.id, agendas: [], sources: [], search: "" };
    setThreadId(null);
    setThreads([]);
    const rows = await loadThreads(result.workspace.id).catch(() => [] as ChatThread[]);
    const saved = localStorage.getItem(`proposal-thread-${result.workspace.id}`);
    const initial = rows.find((item) => item.id === saved) || rows[0];
    if (initial) await openThread(initial, availableModels);
    setConnected(true);
    const suffix = result.index_status === "ready" ? "，已读取会议索引" : result.index_status === "failed" ? "，索引解析失败，已使用基础文件信息" : "";
    setNotice(`已打开目录：${result.workspace.name}${suffix}`);
  }

  async function importSource(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setNotice("正在读取 3GPP FTP 目录…");
    try {
      await browseDirectory(sourceUrl);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "目录读取失败");
    } finally {
      setBusy(false);
    }
  }

  async function navigateDirectory(url: string) {
    setBusy(true);
    setNotice("正在读取目录…");
    try { await browseDirectory(url); }
    catch (error) { setNotice(error instanceof Error ? error.message : "目录读取失败"); }
    finally { setBusy(false); }
  }

  async function changeFilter(nextAgendas: string[], nextSources: string[], nextSearch = search) {
    setSelectedAgendas(nextAgendas);
    setSelectedSources(nextSources);
    if (meeting) {
      try {
        await loadProposals(meeting.id, nextAgendas, nextSources, nextSearch);
      } catch (error) {
        setNotice(error instanceof Error ? error.message : "筛选失败");
      }
    }
  }

  function toggleProposal(id: string) {
    if (proposals.find((item) => item.id === id)?.analyzable === 0) return;
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleAll() {
    setSelected(selected.size === selectableProposals.length ? new Set() : new Set(selectableProposals.map((item) => item.id)));
  }

  async function runDownloadJob() {
    if (!meeting || selected.size === 0) return;
    setBusy(true);
    setNotice("正在准备原始提案下载…");
    try {
      const created = await api<{ id: string }>("/api/download-jobs", {
        method: "POST",
        body: JSON.stringify({ meeting_id: meeting.id, proposal_ids: Array.from(selected) }),
      });
      await pollJob(created.id);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "任务创建失败");
      setBusy(false);
    }
  }

  async function pollJob(id: string) {
    while (true) {
      const current = await api<Job>(`/api/jobs/${id}`);
      setJob(current);
      setNotice(current.message || current.status);
      if (["completed", "failed", "cancelled"].includes(current.status)) {
        setBusy(false);
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 700));
    }
  }

  async function askQuestion(event: FormEvent) {
    event.preventDefault();
    if (!question.trim() || !meeting) return;
    const asked = question.trim();
    const requestKey = activeSessionKey;
    let targetKey = requestKey;
    const clientRequestId = crypto.randomUUID();
    const optimisticUserId = `pending-user-${clientRequestId}`;
    const optimisticAssistantId = `pending-assistant-${clientRequestId}`;
    const sendMode = activeSession.nextMode || chatMode;
    updateSession(requestKey, (current) => ({
      ...current, draft: "", busy: true, error: undefined, nextMode: undefined, historyIndex: null, historyDraft: "",
      messages: [...current.messages, { id: optimisticUserId, role: "user", content: asked }, { id: optimisticAssistantId, role: "assistant", content: "", status: "streaming" }],
      job: { id: "pending", kind: "chat", status: "queued", progress: 0, message: sendMode === "general" ? "正在组织回答…" : "正在判断问题类型…" },
    }));
    requestAnimationFrame(() => requestAnimationFrame(() => scrollToLatest(true)));
    try {
      const created = await api<{ id: string; thread_id: string; user_message_id: string; assistant_message_id: string }>("/api/chat-jobs", {
        method: "POST",
        body: JSON.stringify({
          meeting_id: meeting.id,
          proposal_ids: Array.from(selected),
          question: asked,
          thread_id: threadId,
          model_option_id: modelOptionId || null,
          analysis_mode: "adaptive",
          chat_mode: sendMode,
          client_request_id: clientRequestId,
          report_options: { format: reportFormat, language: reportLanguage, template_id: templateId || null, template_mode: templateMode },
        }),
      }, 1);
      setSessionStates((all) => {
        const source = all[requestKey] || all.__new__;
        const messages = source.messages.map((item) => item.id === optimisticUserId ? { ...item, id: created.user_message_id } : item.id === optimisticAssistantId ? { ...item, id: created.assistant_message_id } : item);
        return { ...all, [created.thread_id]: { ...source, messages } };
      });
      targetKey = created.thread_id;
      setThreadId(created.thread_id);
      localStorage.setItem(`proposal-thread-${meeting.id}`, created.thread_id);
      await pollChatJob(created.id, created.thread_id, false, created.assistant_message_id);
    } catch (error) {
      if (error instanceof ApiRequestError && error.status === 409 && typeof error.detail === "object" && error.detail && "active_job" in error.detail) {
        const active = (error.detail as { active_job: Job }).active_job;
        updateSession(requestKey, (current) => ({ ...current, busy: true, job: active,
          messages: current.messages.filter((item) => item.id !== optimisticUserId && item.id !== optimisticAssistantId) }));
        if (threadId) void pollChatJob(active.id, threadId, true);
        return;
      }
      const message = userFacingError(error);
      updateSession(targetKey, (current) => ({
        ...current, busy: false, error: message,
        job: current.job ? { ...current.job, status: "failed", message: "任务失败", error: message } : null,
        messages: current.messages.map((item) => item.id === optimisticAssistantId ? { ...item, status: "failed" } : item),
      }));
    }
  }

  async function pollChatJob(id: string, targetThreadId: string, resume = false, targetMessageId?: string) {
    const streamed = resume ? null : await streamJob(id, (current) => {
      updateSession(targetThreadId, { job: current, busy: !["completed", "failed", "cancelled"].includes(current.status) });
      if (Date.now() - lastProposalRefreshRef.current > 1500) {
        lastProposalRefreshRef.current = Date.now();
        void refreshProposalStates();
      }
    }, (delta, messageId) => {
      const selectedMessageId = messageId || targetMessageId;
      updateSession(targetThreadId, (current) => ({ ...current, messages: current.messages.map((item) => item.id === selectedMessageId ? { ...item, content: item.content + delta } : item) }));
      if (targetThreadId === activeThreadRef.current) requestAnimationFrame(() => scrollToLatest());
    });
    if (streamed) {
      await finishChatJob(streamed, targetThreadId);
      return;
    }
    while (true) {
      const current = await api<Job>(`/api/jobs/${id}`, undefined, 2);
      updateSession(targetThreadId, { job: current, busy: !["completed", "failed", "cancelled"].includes(current.status) });
      if (current.partial_result?.answer) {
        const messageId = current.partial_result.message_id || targetMessageId;
        updateSession(targetThreadId, (state) => ({ ...state, messages: state.messages.map((item) => item.id === messageId ? { ...item, content: current.partial_result!.answer! } : item) }));
      }
      await refreshProposalStates();
      if (["completed", "failed", "cancelled"].includes(current.status)) {
        await finishChatJob(current, targetThreadId);
        break;
      }
      await new Promise((resolve) => setTimeout(resolve, 700));
    }
  }

  async function finishChatJob(current: Job, targetThreadId: string) {
    await refreshProposalStates();
    if (current.status === "completed" && current.result?.answer) {
      const completedThreadId = current.result.thread_id || targetThreadId;
      if (meeting && completedThreadId) {
        localStorage.setItem(`proposal-thread-${meeting.id}`, completedThreadId);
      }
      updateSession(targetThreadId, (state) => ({ ...state, busy: false, job: null, messages: state.messages.map((item) => item.id === (current.result?.message_id || current.partial_result?.message_id) || (!current.result?.message_id && item.role === "assistant" && item.status === "streaming") ? {
        ...item, status: "completed", content: current.result!.answer!, citations: current.result!.citations, artifacts: current.result!.artifacts,
      } : item) }));
      if (meeting) void loadThreads(meeting.id).catch(() => undefined);
    } else if (current.status === "cancelled") {
      updateSession(targetThreadId, (state) => ({ ...state, busy: false, job: null, messages: state.messages.map((item) => item.role === "assistant" && item.status === "streaming" ? { ...item, status: "cancelled" } : item) }));
    } else if (current.status === "failed") {
      const error = userFacingError(current.error || "请求处理失败，请重试");
      updateSession(targetThreadId, (state) => ({ ...state, busy: false, error, job: { ...current, error }, messages: state.messages.map((item) => item.role === "assistant" && item.status === "streaming" ? { ...item, status: "failed" } : item) }));
    }
  }

  async function uploadTemplate(file: File) {
    const body = new FormData();
    body.append("file", file);
    try {
      const created = await api<TemplateInfo>("/api/templates/inspect", { method: "POST", body });
      setTemplates((items) => [created, ...items.filter((item) => item.id !== created.id)]);
      setTemplateId(created.id);
      setReportFormat(created.kind);
      setNotice(`模板已载入：${created.name || created.file_name}`);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "模板上传失败");
    }
  }

  async function renderReportArtifact(artifact: ReportOutlineArtifact, outline: OutlineItem[]) {
    const targetKey = activeSessionKey;
    updateSession(targetKey, { busy: true, job: { id: "report-pending", kind: "report", status: "queued", progress: 0, message: "正在创建报告任务…" } });
    try {
      const created = await api<{ id: string }>("/api/report-jobs", {
        method: "POST", body: JSON.stringify({ report_id: artifact.report_id, outline, thread_id: threadId }),
      });
      const streamed = await streamJob(created.id, (current) => updateSession(targetKey, { job: current }));
      let terminal = streamed;
      while (!terminal) {
        const current = await api<Job>(`/api/jobs/${created.id}`);
        updateSession(targetKey, { job: current });
        if (["completed", "failed", "cancelled"].includes(current.status)) terminal = current;
        else await new Promise((resolve) => setTimeout(resolve, 700));
      }
      if (terminal.status === "completed" && terminal.result?.artifact) {
        updateSession(targetKey, (state) => ({ ...state, busy: false, job: null, messages: [...state.messages, { role: "assistant", content: "报告文件已生成。", artifacts: [terminal!.result!.artifact!] }] }));
      } else {
        updateSession(targetKey, { busy: false, job: terminal });
      }
    } catch (error) {
      updateSession(targetKey, (state) => ({ ...state, busy: false, job: state.job ? { ...state.job, status: "failed", error: error instanceof Error ? error.message : "报告生成失败" } : null }));
    }
  }

  function handleChatKeyDown(event: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "ArrowUp" || event.key === "ArrowDown") {
      const shouldNavigate = shouldNavigateRequestHistory({
        key: event.key,
        value: event.currentTarget.value,
        selectionStart: event.currentTarget.selectionStart,
        selectionEnd: event.currentTarget.selectionEnd,
        historyActive: activeSession.historyIndex !== null,
        isComposing: event.nativeEvent.isComposing,
        altKey: event.altKey,
        ctrlKey: event.ctrlKey,
        metaKey: event.metaKey,
        shiftKey: event.shiftKey,
      });
      if (!shouldNavigate) return;
      const result = navigateRequestHistory(
        extractRequestHistory(messages),
        { index: activeSession.historyIndex, savedDraft: activeSession.historyDraft },
        question,
        event.key === "ArrowUp" ? "older" : "newer",
      );
      if (!result.handled) return;
      event.preventDefault();
      updateSession(activeSessionKey, {
        draft: result.draft,
        historyIndex: result.state.index,
        historyDraft: result.state.savedDraft,
      });
      requestAnimationFrame(() => {
        const input = chatInputRef.current;
        if (input) input.setSelectionRange(input.value.length, input.value.length);
      });
      return;
    }
    if (event.key !== "Enter" || event.altKey || event.nativeEvent.isComposing) return;
    event.preventDefault();
    if (!chatBusy && question.trim()) event.currentTarget.form?.requestSubmit();
  }

  async function retryChat() {
    if (!chatJob?.error) return;
    updateSession(activeSessionKey, {
      job: null,
      draft: messages.filter((item) => item.role === "user").at(-1)?.content || "",
      historyIndex: null,
      historyDraft: "",
    });
  }

  async function cancelActiveAnalysis() {
    if (!chatJob || chatJob.id.endsWith("pending")) return;
    const targetKey = activeSessionKey;
    const targetJob = chatJob.id;
    updateSession(targetKey, (current) => ({
      ...current, busy: false, job: null,
      messages: current.messages.map((item, index) => index === current.messages.length - 1 && item.status === "streaming" ? { ...item, status: "cancelled", content: item.content || "分析已终止。" } : item),
    }));
    try {
      await api(`/api/jobs/${targetJob}/cancel`, { method: "POST" });
    } catch (error) {
      updateSession(targetKey, { error: error instanceof Error ? error.message : "终止请求未确认" });
    }
  }

  async function changeModel(next: string) {
    if (next === "__configure__") {
      setSection("settings");
      return;
    }
    setModelOptionId(next);
    if (threadId) {
      await api(`/api/chats/${threadId}`, { method: "PATCH", body: JSON.stringify({ default_model_option_id: next }) });
      setThreads((items) => items.map((item) => item.id === threadId ? { ...item, default_model_option_id: next } : item));
    }
  }

  const visibleProposals = useMemo(() => proposals.filter((proposal) =>
    (fileType === "all" || (proposal.file_kind || "zip") === fileType) &&
    (indexStatus === "ready" || !search || `${proposal.tdoc} ${proposal.title}`.toLocaleLowerCase().includes(search.toLocaleLowerCase()))
  ), [proposals, fileType, indexStatus, search]);
  const selectableProposals = useMemo(() => visibleProposals.filter((item) => item.analyzable !== 0), [visibleProposals]);
  const visibleFolders = useMemo(() => folders.filter((item) => !search || item.name.toLocaleLowerCase().includes(search.toLocaleLowerCase())), [folders, search]);
  const visibleFiles = useMemo(() => directoryFiles.filter((item) => !item.proposal && (!search || item.name.toLocaleLowerCase().includes(search.toLocaleLowerCase()))), [directoryFiles, search]);
  const fileTypes = useMemo(() => Array.from(new Set(proposals.map((item) => item.file_kind || "zip"))).sort(), [proposals]);
  const selectedRows = useMemo(() => proposals.filter((proposal) => selected.has(proposal.id)), [proposals, selected]);
  const selectedAnalyzed = selectedRows.filter((item) => item.analysis_state === "analyzed").length;
  const agendaOptions = useMemo<FilterOption[]>(() => [
    { id: "all", label: "All agenda items", isSelectAll: true },
    ...agendas.filter((item) => item !== "all").map((item) => ({ id: item, label: item })),
  ], [agendas]);
  const sourceOptions = useMemo<FilterOption[]>(() => [
    { id: "all", label: "All sources", isSelectAll: true },
    ...sources.filter((item) => item !== "all").map((item) => ({ id: item, label: item })),
  ], [sources]);

  return (
    <div className="app-shell">
      {onboardingOpen && (
        <Onboarding
          runtime={runtime}
          onFinish={() => {
            localStorage.setItem("proposal-onboarding-complete", "1");
            setOnboardingOpen(false);
          }}
        />
      )}
      <header className="app-header">
        <button className="brand" onClick={() => setSection("workspace")} aria-label="打开提案工作区">
          <span className="brand-mark">3GPP</span>
          <span><strong>提案洞察</strong><small>Proposal Analyzer</small></span>
        </button>
        <nav className="main-nav" aria-label="主要导航">
          {(["workspace", "settings"] as Section[]).map((item) => (
            <button key={item} className={section === item ? "active" : ""} onClick={() => setSection(item)}>
              {item === "workspace" ? "提案工作区" : "设置"}
            </button>
          ))}
        </nav>
        <button className={`connection ${connected ? "online" : "offline"}`} onClick={() => !connected && void bootstrap()}>
          <span />{connected ? "本地服务正常" : "重新连接"}
        </button>
      </header>

      {section === "workspace" && (
        <main className="workspace-page">
          <section className="source-panel">
            <div className="meeting-title"><strong>{meeting?.name || "FTP 文稿"}</strong><span>{meeting ? `${meeting.proposal_count.toLocaleString()} 份可分析文稿` : "默认打开 WG2 Arch"}</span></div>
            <form onSubmit={importSource} className="source-form">
              <label htmlFor="source-url">3GPP 目录链接（选填）</label>
              <div className="source-input-row">
                <input id="source-url" value={sourceUrl} placeholder={DEFAULT_FTP_URL} onChange={(event) => setSourceUrl(event.target.value)} />
                <button className="primary" type="submit" disabled={busy}>读取会议</button>
              </div>
            </form>
            <p className="source-notice">{notice}</p>
          </section>

          <section className="selection-summary">
            <div><span>会议</span><strong>{meeting?.name || "示例工作区"}</strong></div>
            <div><span>当前结果</span><strong>{proposals.length.toLocaleString()}</strong></div>
            <div><span>已选择</span><strong>{selected.size.toLocaleString()}</strong></div>
            <div><span>已完成分析</span><strong>{selectedAnalyzed}</strong></div>
            <div className="summary-actions">
              <button className="secondary" disabled={!selected.size || busy} onClick={() => void runDownloadJob()}>下载所选提案</button>
            </div>
          </section>

          {job && (
            <section className={`job-strip ${job.status}`}>
              <div><strong>{job.kind === "download" ? "提案打包" : "文稿分析"}</strong><span>{job.message}</span></div>
              <div className="progress-track"><span style={{ width: `${Math.max(3, job.progress * 100)}%` }} /></div>
              <b>{Math.round(job.progress * 100)}%</b>
              {job.status === "completed" && job.kind === "download" && (
                <a className="download-link" href={`${API}/api/download-jobs/${job.id}/file`}>保存总压缩包</a>
              )}
            </section>
          )}

          <section className="workbench">
            <div className={`proposal-pane ${mobileScopeOpen ? "mobile-open" : ""}`}>
              <div className="mobile-scope-header"><strong>提案范围</strong><button onClick={() => setMobileScopeOpen(false)} aria-label="关闭提案范围"><Close size={18} /></button></div>
              <nav className="ftp-breadcrumbs" aria-label="3GPP FTP 目录">
                {breadcrumbs.map((crumb, index) => <span key={crumb.url}>{index > 0 && <i>/</i>}{index === breadcrumbs.length - 1 ? <b>{crumb.label}</b> : <button disabled={busy} onClick={() => void navigateDirectory(crumb.url)}>{crumb.label}</button>}</span>)}
              </nav>
              <div className="filter-bar">
                {indexStatus === "ready" ? <>
                  <div><FilterableMultiSelect id="agenda-filter" titleText="Agenda item" placeholder="All agenda items" items={agendaOptions} selectedItems={agendaOptions.filter((item) => selectedAgendas.includes(item.id))} itemToString={(item) => item?.label || ""} onChange={({ selectedItems }) => { void changeFilter((selectedItems || []).filter((item) => !item.isSelectAll).map((item) => item.id), selectedSources); }} selectionFeedback="top-after-reopen" clearSelectionText="清空 Agenda 选择" /></div>
                  <div><FilterableMultiSelect id="source-filter" titleText="提案来源" placeholder="All sources" items={sourceOptions} selectedItems={sourceOptions.filter((item) => selectedSources.includes(item.id))} itemToString={(item) => item?.label || ""} onChange={({ selectedItems }) => { void changeFilter(selectedAgendas, (selectedItems || []).filter((item) => !item.isSelectAll).map((item) => item.id)); }} selectionFeedback="top-after-reopen" clearSelectionText="清空来源选择" /></div>
                </> : <div className="file-type-filter"><label>文件类型</label><select value={fileType} onChange={(event) => setFileType(event.target.value)}><option value="all">全部支持格式</option>{fileTypes.map((type) => <option key={type} value={type}>{type.toUpperCase()}</option>)}</select></div>}
                <div className="search-box"><label>搜索</label><input value={search} placeholder="TDoc 或标题" onChange={(event) => setSearch(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter") void changeFilter(selectedAgendas, selectedSources, search); }} /></div>
              </div>
              <div className="proposal-list-header">
                <button className="checkbox" aria-label="全选当前结果" onClick={toggleAll}>{selected.size === selectableProposals.length && selectableProposals.length ? "✓" : ""}</button>
                <span>目录内容</span><span>状态</span>
              </div>
              <div className="proposal-list">
                {visibleFolders.map((folder) => <article key={folder.url} className="folder-row"><Folder size={20}/><button disabled={busy} onClick={() => void navigateDirectory(folder.url)}><strong>{folder.name}</strong><small>{folder.modified || "修改时间未知"}</small></button><span>文件夹</span></article>)}
                {visibleProposals.map((proposal) => (
                  <article key={proposal.id} className={`proposal-row ${selected.has(proposal.id) ? "selected" : ""}`}>
                    <button className="checkbox" disabled={proposal.analyzable === 0} aria-label={`选择 ${proposal.tdoc}`} onClick={() => toggleProposal(proposal.id)}>{selected.has(proposal.id) ? "✓" : ""}</button>
                    <button className="proposal-main" disabled={proposal.analyzable === 0} onClick={() => toggleProposal(proposal.id)}>
                      <div className="proposal-meta"><b>{proposal.tdoc}</b><span>{proposal.metadata_source === "index" ? proposal.agenda_item : (proposal.file_kind || "file").toUpperCase()}</span><span title={proposal.source}>{proposal.metadata_source === "index" ? (proposal.canonical_source || proposal.primary_source) : formatBytes(proposal.file_size)}</span></div>
                      <h3>{proposal.title}</h3>
                      <p>{proposal.metadata_source === "index" ? (proposal.agenda_description || "暂无 agenda 描述") : (proposal.file_modified || "修改时间未知")}</p>
                      {proposal.metadata_source === "index" && <p className="proposal-source" title={proposal.source}>完整来源：{proposal.source}</p>}
                    </button>
                    <div className="status-cell">
                      <span className={`status ${["analyzed", "ready"].includes(proposal.analysis_state === "analyzed" ? "analyzed" : proposal.preparation_state) ? "done" : proposal.preparation_state === "failed" || proposal.analysis_state === "failed" ? "failed" : "waiting"}`}>
                        {proposalStatus(proposal)}
                      </span>
                      {proposal.preparation_error ? (
                        <button className="status-retry" title={proposal.preparation_error} onClick={() => void retryPreparation(proposal)}>重试</button>
                      ) : <small>{proposal.status || "available"}</small>}
                    </div>
                  </article>
                ))}
                {visibleFiles.map((file) => <article key={file.url} className="plain-file-row"><Document size={18}/><div><strong>{file.name}</strong><small>{[file.extension?.toUpperCase(), formatBytes(file.size), file.modified].filter(Boolean).join(" · ")}</small></div><a href={file.url} target="_blank" rel="noreferrer">下载</a></article>)}
                {!visibleFolders.length && !visibleProposals.length && !visibleFiles.length && <div className="empty-state"><strong>当前目录为空或没有匹配项</strong><span>可通过上方面包屑返回上级目录。</span></div>}
              </div>
            </div>
            {mobileScopeOpen && <button className="scope-backdrop" aria-label="关闭提案范围" onClick={() => setMobileScopeOpen(false)} />}

            <aside className="chat-pane">
              {sessionsOpen && <button className="session-backdrop" aria-label="关闭会话列表" onClick={() => setSessionsOpen(false)} />}
              {sessionsOpen && <div className="session-drawer" role="dialog" aria-label="活动会话">
                <header><strong>会话</strong><button className="primary" onClick={() => void newThread()}><Add size={15}/>新会话</button></header>
                {threadLoadError && <div className="session-load-error"><span>会话加载暂时失败</span><button onClick={() => meeting && void loadThreads(meeting.id)}>重试</button></div>}
                <div>{threads.map((thread) => <article key={thread.id} className={thread.id === threadId ? "active" : ""}>
                  <button className="session-main" onClick={() => void openThread(thread)}><strong>{thread.title}</strong><small>{thread.message_count || 0} 条消息</small></button>
                  <div><button onClick={() => void renameThread(thread)}>重命名</button><button onClick={() => void archiveThread(thread, true)}>归档</button></div>
                </article>)}</div>
              </div>}
              <div className="chat-header">
                <div><button className="mobile-scope-toggle" onClick={() => setMobileScopeOpen(true)}>选择提案</button><button className="session-toggle" onClick={() => setSessionsOpen((value) => !value)}>会话</button><span className="ai-badge">AI</span><div><strong>提案分析助手</strong><small>基于当前选择范围</small></div></div>
                <span className="scope-pill">{selected.size} 份提案</span>
              </div>
              <div className="message-list" ref={messageListRef} onScroll={(event) => { const element = event.currentTarget; const nearBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 120; followLatestRef.current = nearBottom; setShowLatestButton(!nearBottom); }}>
                {threadLoadError && <div className="chat-load-error"><span>会话加载暂时失败，已保留当前内容。</span><button onClick={() => { const current = threads.find((item) => item.id === threadId); if (current) void openThread(current); }}>重试</button></div>}
                {messages.filter((message) => !(message.role === "assistant" && message.status === "streaming" && !message.content && !message.artifacts?.length)).map((message, index) => (
                  <div key={message.id || `${message.role}-${index}`} className={`message ${message.role}`}>
                    <span className="message-role">{message.role === "assistant" ? "AI" : "我"}</span>
                    <div>
                      <p>{message.content}</p>
                      {message.artifacts?.map((artifact) => <ReportArtifactCard key={`${artifact.kind}-${artifact.report_id}`} artifact={artifact} onRender={renderReportArtifact} onPreview={(url) => setPreviewUrl(`${API}${url}`)} />)}
                    </div>
                  </div>
                ))}
                {showLatestButton && <button className="jump-latest" onClick={() => scrollToLatest(true)}>回到最新消息</button>}
              </div>
              {chatJob && <div className={`chat-progress-dock ${chatJob.status}`}><div><strong>{chatJob.status === "failed" ? "请求处理失败" : (chatJob.message || "正在处理问题")}</strong><small>{chatJob.error ? userFacingError(chatJob.error) : `${Math.round(chatJob.progress * 100)}%${chatJob.updated_at ? ` · 最近活动 ${new Date(chatJob.updated_at).toLocaleTimeString()}` : ""}`}</small></div><div className="progress-track"><span style={{ width: `${Math.max(3, chatJob.progress * 100)}%` }} /></div>{!(["failed", "cancelled", "completed"].includes(chatJob.status)) && <button className="cancel-analysis" onClick={() => void cancelActiveAnalysis()}>终止分析</button>}{chatJob.status === "failed" && <><button className="secondary" onClick={() => void retryChat()}>重试</button><button className="dock-close" onClick={() => updateSession(activeSessionKey, { job: null })}>关闭</button></>}</div>}
              <div className="suggestions">
                {["按公司总结主要观点", "按核心问题归纳各公司观点", "基于分析结果生成 Word / PowerPoint 报告"].map((item) => <button key={item} onClick={() => updateSession(activeSessionKey, { draft: item, nextMode: "proposal", historyIndex: null, historyDraft: "" })}>{item}</button>)}
              </div>
              <form className="chat-form" onSubmit={askQuestion}>
                <textarea ref={chatInputRef} value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={handleChatKeyDown} placeholder={selected.size ? "询问提案，或直接提出其他问题…" : "可以直接提问；涉及提案时请先选择范围"} />
                <button className="primary send-button" aria-label="发送" disabled={!question.trim() || chatBusy || !meeting}><Send size={18} /></button>
                <small className="chat-shortcut">Enter 发送，Option+Enter 换行；↑/↓ 浏览当前会话历史</small>
              </form>
              <div className="model-selector"><label>模式</label><select value={chatMode} onChange={(event) => updateSession(activeSessionKey, { chatMode: event.target.value as ChatMode })}><option value="auto">自动</option><option value="proposal">提案问答</option><option value="general">通用问答</option></select><label>模型</label><select value={modelOptionId} onChange={(event) => void changeModel(event.target.value)}><option value="" disabled>请选择已配置模型</option>{modelOptions.map((item) => <option key={item.id} value={item.id}>{item.deployment === "local" ? "本地 / " : "外部 / "}{item.label}</option>)}<option value="__configure__">配置新的大模型…</option></select></div>
              <div className="report-settings">
                <button className="report-settings-toggle" onClick={() => setReportSettingsOpen((value) => !value)}><Document size={16} /> 报告设置 <span>{reportFormat.toUpperCase()}</span></button>
                {reportSettingsOpen && <div className="report-settings-panel">
                  <label>格式<select value={reportFormat} onChange={(event) => { setReportFormat(event.target.value as "docx" | "pptx"); setTemplateId(""); }}><option value="docx">Word</option><option value="pptx">PowerPoint</option></select></label>
                  <label>语言<select value={reportLanguage} onChange={(event) => setReportLanguage(event.target.value as "en" | "zh")}><option value="en">English</option><option value="zh">中文</option></select></label>
                  <label>模板<select value={templateId} onChange={(event) => setTemplateId(event.target.value)}><option value="">默认模板</option>{templates.filter((item) => item.kind === reportFormat).map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></label>
                  <label>版式<select value={templateMode} onChange={(event) => setTemplateMode(event.target.value as "strict" | "extend")}><option value="strict">保真优先</option><option value="extend">风格扩展</option></select></label>
                  <label className="template-upload"><Upload size={16} /> 上传模板<input type="file" accept=".docx,.pptx" onChange={(event) => event.target.files?.[0] && void uploadTemplate(event.target.files[0])} /></label>
                  <p className="security-warning">信息安全提醒：如果使用外部大模型，请不要上传包含敏感、保密或受限信息的文件；提取后的文本或图像可能发送到模型服务。</p>
                </div>}
              </div>
            </aside>
          </section>
        </main>
      )}

      {section === "settings" && <SettingsView runtime={runtime} onRuntime={setRuntime} onModelSaved={async () => { const options = await api<ModelOption[]>("/api/model-options"); setModelOptions(options); setModelOptionId(options.find((item) => item.is_default)?.id || options[0]?.id || ""); setSection("workspace"); }} />}
      {previewUrl && <div className="preview-modal" role="dialog" aria-modal="true" aria-label="报告预览"><div className="preview-header"><strong>报告预览</strong><button onClick={() => setPreviewUrl(null)} aria-label="关闭预览"><Close size={20} /></button></div><iframe src={previewUrl} title="报告预览" /></div>}
    </div>
  );
}

function Onboarding({ runtime, onFinish }: { runtime: { disk_free?: number; data_dir?: string; components?: { name: string; installed: boolean }[] }; onFinish: () => void }) {
  const [step, setStep] = useState(0);
  const [provider, setProvider] = useState("openai");
  const [baseUrl, setBaseUrl] = useState("https://api.openai.com/v1");
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [message, setMessage] = useState("");
  async function saveAndFinish() {
    if (apiKey && model) {
      try {
        await api("/api/model-profiles", { method: "POST", body: JSON.stringify({ name: `${provider} profile`, provider, base_url: baseUrl, api_key: apiKey, text_model: model, vision_model: "", embedding_model: "", is_default: true }) });
      } catch (error) {
        setMessage(error instanceof Error ? error.message : "配置保存失败");
        return;
      }
    }
    onFinish();
  }
  return <div className="onboarding-backdrop" role="dialog" aria-modal="true" aria-label="首次使用向导">
    <div className="onboarding-card">
      <div className="onboarding-rail"><span className="brand-mark">3GPP</span><div><b>欢迎使用提案洞察</b><p>三步完成设置，之后只需从桌面图标启动。</p></div><ol><li className={step >= 0 ? "done" : ""}>开始</li><li className={step >= 1 ? "done" : ""}>本机环境</li><li className={step >= 2 ? "done" : ""}>模型服务</li></ol></div>
      <div className="onboarding-content">
        {step === 0 && <><p className="eyebrow">首次使用</p><h2>不需要 Docker，也不需要命令行</h2><p>应用会在本机保存会议、提案、分析结果和报告；勾选提案后会自动进行本地预处理。</p><div className="choice-grid"><button onClick={() => setStep(1)}><strong>建立新工作区</strong><span>检查运行组件并配置模型</span></button></div></>}
        {step === 1 && <><p className="eyebrow">本机环境</p><h2>确认存储空间与运行组件</h2><div className="setup-status"><div><span>数据位置</span><strong>{runtime.data_dir || "桌面应用数据目录"}</strong></div><div><span>可用空间</span><strong>{formatBytes(runtime.disk_free)}</strong></div>{runtime.components?.map((component) => <div key={component.name}><span>{component.name}</span><strong className={component.installed ? "ready" : "optional"}>{component.installed ? "已就绪" : "首次使用时自动配置"}</strong></div>)}</div><button className="primary" onClick={() => setStep(2)}>继续</button></>}
        {step === 2 && <><p className="eyebrow">模型服务</p><h2>配置文本模型，也可以稍后再做</h2><div className="onboarding-form"><label>供应商</label><select value={provider} onChange={(event) => { const value = event.target.value; setProvider(value); setBaseUrl(value === "deepseek" ? "https://api.deepseek.com/v1" : value === "qwen" ? "https://dashscope.aliyuncs.com/compatible-mode/v1" : "https://api.openai.com/v1"); }}><option value="openai">OpenAI</option><option value="deepseek">DeepSeek</option><option value="qwen">通义千问</option></select><label>API Key</label><input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} /><label>文本模型</label><input value={model} onChange={(event) => setModel(event.target.value)} placeholder="输入模型名称" /><small>密钥只会加密保存在本机，不会写入日志或诊断信息。</small></div>{message && <p className="form-error">{message}</p>}<div className="onboarding-actions"><button className="secondary" onClick={onFinish}>暂时跳过</button><button className="primary" onClick={() => void saveAndFinish()}>保存并开始使用</button></div></>}
      </div>
    </div>
  </div>;
}

function ReportArtifactCard({ artifact, onRender, onPreview }: { artifact: ChatArtifact; onRender: (artifact: ReportOutlineArtifact, outline: OutlineItem[]) => Promise<void>; onPreview: (url: string) => void }) {
  const [outline, setOutline] = useState(artifact.kind === "report_outline" ? artifact.outline : []);
  if (artifact.kind === "report_file") {
    return <section className="artifact-card file-artifact">
      <div className="artifact-icon"><Document size={22} /></div>
      <div><strong>{artifact.file_name}</strong><span>{artifact.format.toUpperCase()} / {artifact.proposal_count || 0} 份提案 / {artifact.template_name || "默认模板"} / {artifact.quality?.passed ? "检查通过" : "需要检查"}</span>{artifact.quality && !artifact.quality.passed && <small>{artifact.quality.warnings.join("；")}</small>}</div>
      <div className="artifact-actions">{artifact.preview_available && artifact.preview_url && <button onClick={() => onPreview(artifact.preview_url!)}><View size={16} />预览</button>}<a href={`${API}${artifact.download_url}`}><Download size={16} />下载</a></div>
    </section>;
  }
  function update(index: number, patch: Partial<OutlineItem>) { setOutline((items) => items.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item)); }
  function move(index: number, direction: -1 | 1) { setOutline((items) => { const target = index + direction; if (target < 0 || target >= items.length) return items; const next = [...items]; [next[index], next[target]] = [next[target], next[index]]; return next; }); }
  return <section className="artifact-card outline-artifact">
    <header><div><Document size={20} /><strong>{artifact.title}</strong><span>{outline.length} {artifact.format === "pptx" ? "页" : "节"}</span></div><small>基于 {artifact.message_count || 0} 条会话消息和 {artifact.proposal_count} 篇提案</small>{artifact.warning && <small>{artifact.warning}</small>}</header>
    <div className="artifact-outline-list">{outline.map((item, index) => <article key={index}><div className="outline-order"><button onClick={() => move(index, -1)} disabled={!index} aria-label="上移"><ArrowUp size={14} /></button><button onClick={() => move(index, 1)} disabled={index === outline.length - 1} aria-label="下移"><ArrowDown size={14} /></button></div><div><input value={item.title} onChange={(event) => update(index, { title: event.target.value })} /><textarea value={item.content || item.purpose || ""} onChange={(event) => update(index, { content: event.target.value })} /></div><button className="outline-delete" onClick={() => setOutline((items) => items.filter((_, itemIndex) => itemIndex !== index))} aria-label="删除"><TrashCan size={15} /></button></article>)}</div>
    <footer><button className="secondary" onClick={() => setOutline((items) => [...items, { title: "New section", content: "" }])}><Add size={16} />增加</button><button className="primary" onClick={() => void onRender(artifact, outline)}>生成报告</button></footer>
  </section>;
}

function SettingsView({ runtime, onRuntime, onModelSaved }: { runtime: { disk_free?: number; data_dir?: string; components?: { name: string; installed: boolean }[] }; onRuntime: (value: typeof runtime) => void; onModelSaved: () => Promise<void> }) {
  const [provider, setProvider] = useState("openai");
  const [baseUrl, setBaseUrl] = useState("https://api.openai.com/v1");
  const [apiKey, setApiKey] = useState("");
  const [textModel, setTextModel] = useState("");
  const [visionModel, setVisionModel] = useState("");
  const [embeddingModel, setEmbeddingModel] = useState("");
  const [availableModels, setAvailableModels] = useState("");
  const [deployment, setDeployment] = useState<"external" | "local">("external");
  const [maxOutputTokens, setMaxOutputTokens] = useState(8192);
  const [maxConcurrency, setMaxConcurrency] = useState(4);
  const [message, setMessage] = useState("模型配置仅保存在本机并加密存储。");
  const [aliases, setAliases] = useState<SourceAlias[]>([]);
  const [aliasSearch, setAliasSearch] = useState("");
  const [aliasName, setAliasName] = useState("");
  const [canonicalName, setCanonicalName] = useState("");
  const [archivedThreads, setArchivedThreads] = useState<ChatThread[]>([]);
  const [archiveSearch, setArchiveSearch] = useState("");
  const [archiveSelected, setArchiveSelected] = useState<Set<string>>(new Set());
  const [archiveMessage, setArchiveMessage] = useState("");
  async function loadArchived() { try { setArchivedThreads(await api<ChatThread[]>("/api/chats?state=archived", undefined, 2)); setArchiveMessage(""); } catch (error) { setArchiveMessage(error instanceof Error ? error.message : "归档会话加载失败"); } }
  useEffect(() => {
    let active = true;
    void Promise.allSettled([
      api<SourceAlias[]>("/api/source-aliases"),
      api<ChatThread[]>("/api/chats?state=archived", undefined, 2),
    ]).then(([aliasResult, archiveResult]) => {
      if (!active) return;
      setAliases(aliasResult.status === "fulfilled" ? aliasResult.value : []);
      if (archiveResult.status === "fulfilled") setArchivedThreads(archiveResult.value);
      else setArchiveMessage("归档会话加载失败");
    });
    return () => { active = false; };
  }, []);
  const payload = { name: `${provider} profile`, provider, base_url: baseUrl, api_key: apiKey, text_model: textModel, vision_model: visionModel, embedding_model: embeddingModel, available_models: availableModels.split(/[,\n]/).map((item) => item.trim()).filter(Boolean), deployment, max_output_tokens: maxOutputTokens, context_window: 65536, max_concurrency: maxConcurrency, request_timeout_seconds: 180, enabled: true, is_default: true };
  async function test() { try { const result = await api<{ ok: boolean; message: string }>("/api/model-profiles/test", { method: "POST", body: JSON.stringify(payload) }); setMessage(result.message); } catch (error) { setMessage(error instanceof Error ? error.message : "测试失败"); } }
  async function save() { try { const created = await api<{ id: string }>("/api/model-profiles", { method: "POST", body: JSON.stringify(payload) }); try { await api(`/api/model-profiles/${created.id}/discover`, { method: "POST" }); } catch { /* Manual model names remain available when discovery is unsupported. */ } setMessage("配置已加密保存"); setApiKey(""); await onModelSaved(); } catch (error) { setMessage(error instanceof Error ? error.message : "保存失败"); } }
  async function repair() { try { if (window.proposalDesktop) await window.proposalDesktop.repairRuntime(); const status = await api<typeof runtime>("/api/runtime/status"); onRuntime(status); setMessage("运行环境检查和修复完成"); } catch (error) { setMessage(error instanceof Error ? error.message : "本地服务未启动"); } }
  async function saveAlias() { if (!aliasName.trim() || !canonicalName.trim()) return; try { await api("/api/source-aliases", { method: "PUT", body: JSON.stringify({ alias: aliasName, canonical_name: canonicalName }) }); setAliases(await api<SourceAlias[]>("/api/source-aliases")); setAliasName(""); setCanonicalName(""); } catch (error) { setMessage(error instanceof Error ? error.message : "别名保存失败"); } }
  async function deleteAlias(id: string) { try { await api(`/api/source-aliases/${id}`, { method: "DELETE" }); setAliases(await api<SourceAlias[]>("/api/source-aliases")); } catch (error) { setMessage(error instanceof Error ? error.message : "别名删除失败"); } }
  async function restoreArchived(thread: ChatThread) { try { await api(`/api/chats/${thread.id}`, { method: "PATCH", body: JSON.stringify({ archived: false }) }); setArchiveSelected((current) => { const next = new Set(current); next.delete(thread.id); return next; }); await loadArchived(); } catch (error) { setArchiveMessage(error instanceof Error ? error.message : "恢复失败"); } }
  async function deleteArchived(ids: string[]) {
    if (!ids.length) return;
    const reports = archivedThreads.filter((item) => ids.includes(item.id)).reduce((sum, item) => sum + (item.report_count || 0), 0);
    if (!window.confirm(`将永久删除 ${ids.length} 个会话和 ${reports} 个专属报告。此操作无法撤销，确定继续？`)) return;
    try { await api("/api/chats/batch-delete", { method: "POST", body: JSON.stringify({ thread_ids: ids }) }); setArchiveSelected(new Set()); await loadArchived(); } catch (error) { setArchiveMessage(error instanceof Error ? error.message : "删除失败"); }
  }
  const filteredAliases = aliases.filter((item) => `${item.alias} ${item.canonical_name}`.toLowerCase().includes(aliasSearch.toLowerCase())).slice(0, 80);
  const filteredArchived = archivedThreads.filter((item) => `${item.title} ${item.meeting_name || ""}`.toLowerCase().includes(archiveSearch.toLowerCase()));
  const filteredArchivedIds = filteredArchived.map((item) => item.id);
  const allFilteredArchivedSelected = filteredArchivedIds.length > 0 && filteredArchivedIds.every((id) => archiveSelected.has(id));
  function toggleAllFilteredArchived() {
    setArchiveSelected((current) => {
      const next = new Set(current);
      if (allFilteredArchivedSelected) filteredArchivedIds.forEach((id) => next.delete(id));
      else filteredArchivedIds.forEach((id) => next.add(id));
      return next;
    });
  }
  return <main className="secondary-page settings-page"><div className="page-intro"><h1>设置</h1><p>模型、运行组件和公司归并规则。</p></div><div className="settings-grid">
    <section className="card control-card"><h2>模型服务</h2><label>供应商</label><select value={provider} onChange={(event) => { const value = event.target.value; setProvider(value); setBaseUrl(value === "deepseek" ? "https://api.deepseek.com/v1" : value === "qwen" ? "https://dashscope.aliyuncs.com/compatible-mode/v1" : value === "openai" ? "https://api.openai.com/v1" : ""); }}><option value="openai">OpenAI</option><option value="deepseek">DeepSeek</option><option value="qwen">通义千问</option><option value="custom">自定义兼容接口</option></select><label>部署位置</label><select value={deployment} onChange={(event) => setDeployment(event.target.value as "external" | "local")}><option value="external">外部模型服务</option><option value="local">本地部署模型</option></select><label>Base URL</label><input value={baseUrl} onChange={(event) => setBaseUrl(event.target.value)} /><label>API Key</label><input type="password" value={apiKey} onChange={(event) => setApiKey(event.target.value)} placeholder="只会加密保存在本机" /><label>默认文本模型</label><input value={textModel} onChange={(event) => setTextModel(event.target.value)} /><label>其他可选模型（逗号或换行分隔）</label><input value={availableModels} onChange={(event) => setAvailableModels(event.target.value)} placeholder="deepseek-v4-flash, deepseek-v4-pro"/><label>视觉模型</label><input value={visionModel} onChange={(event) => setVisionModel(event.target.value)} /><label>Embedding 模型（可选）</label><input value={embeddingModel} onChange={(event) => setEmbeddingModel(event.target.value)} /><label>最大输出 Tokens</label><input type="number" min={512} max={65536} value={maxOutputTokens} onChange={(event) => setMaxOutputTokens(Number(event.target.value))}/><label>并发请求数</label><input type="number" min={1} max={12} value={maxConcurrency} onChange={(event) => setMaxConcurrency(Number(event.target.value))}/><div className="button-row"><button className="secondary" onClick={() => void test()}>测试连接</button><button className="primary" onClick={() => void save()}>保存配置</button></div><p className="helper">{message}</p></section>
    <section className="card runtime-card"><div className="card-title"><div><span>本机环境</span><h2>运行组件</h2></div><button className="secondary" onClick={() => void repair()}>检查并修复</button></div><p className="data-path">数据位置<br/><strong>{runtime.data_dir || "等待本地服务"}</strong></p><p>可用空间 <b>{formatBytes(runtime.disk_free)}</b></p><div className="component-list">{runtime.components?.map((component) => <div key={component.name}><span className={component.installed ? "ok" : "missing"}>{component.installed ? "✓" : "!"}</span><strong>{component.name}</strong><small>{component.installed ? "已就绪" : "需要安装或修复"}</small></div>) || <div><span className="missing">!</span><strong>尚未连接</strong><small>启动桌面应用后自动检查</small></div>}</div><a className="diagnostic-link" href={`${API}/api/diagnostics/export`}>导出脱敏诊断信息</a></section>
    <section className="card alias-card"><div className="card-title"><div><span>来源规范化</span><h2>公司别名</h2></div><strong>{aliases.length}</strong></div><div className="alias-editor"><input value={aliasName} onChange={(event) => setAliasName(event.target.value)} placeholder="名称变体，如 Ericsson Canada Inc."/><input value={canonicalName} onChange={(event) => setCanonicalName(event.target.value)} placeholder="母公司，如 Ericsson"/><button className="primary" onClick={() => void saveAlias()}>保存规则</button></div><input className="alias-search" value={aliasSearch} onChange={(event) => setAliasSearch(event.target.value)} placeholder="搜索公司别名"/><div className="alias-list">{filteredAliases.map((item) => <div key={item.id}><button className="alias-main" onClick={() => { setAliasName(item.alias); setCanonicalName(item.canonical_name); }}><span>{item.alias}</span><strong>{item.canonical_name}</strong></button>{item.built_in ? <small>内置</small> : <button className="alias-delete" onClick={() => void deleteAlias(item.id)} aria-label={`删除 ${item.alias}`}><TrashCan size={16}/></button>}</div>)}</div></section>
    <section className="card archived-card"><div className="card-title"><div><span>会话管理</span><h2>已归档会话</h2></div><strong>{archivedThreads.length}</strong></div><div className="archived-toolbar"><input value={archiveSearch} onChange={(event) => setArchiveSearch(event.target.value)} placeholder="搜索会议或会话名称"/></div><div className="archived-batch-bar"><label><input type="checkbox" checked={allFilteredArchivedSelected} disabled={!filteredArchived.length} onChange={toggleAllFilteredArchived}/>全选当前结果（{filteredArchived.length}）</label><span>已选择 {archiveSelected.size} 个会话</span>{archiveSelected.size > 0 && <button className="clear-selection" onClick={() => setArchiveSelected(new Set())}>清空选择</button>}<button className="batch-delete" disabled={!archiveSelected.size} onClick={() => void deleteArchived(Array.from(archiveSelected))}><TrashCan size={16}/>批量永久删除（{archiveSelected.size}）</button></div>{archiveMessage && <p className="form-error">{archiveMessage} <button onClick={() => void loadArchived()}>重试</button></p>}<div className="archived-list">{filteredArchived.length ? filteredArchived.map((thread) => <article key={thread.id}><input type="checkbox" aria-label={`选择 ${thread.title}`} checked={archiveSelected.has(thread.id)} onChange={() => setArchiveSelected((current) => { const next = new Set(current); if (next.has(thread.id)) next.delete(thread.id); else next.add(thread.id); return next; })}/><div><strong>{thread.title}</strong><small>{thread.meeting_name || "未知会议"} · {thread.message_count || 0} 条消息 · {thread.report_count || 0} 个报告</small></div><button onClick={() => void restoreArchived(thread)}>恢复</button><button className="danger" onClick={() => void deleteArchived([thread.id])}>永久删除</button></article>) : <p className="empty-state">暂无已归档会话</p>}</div></section>
  </div></main>;
}
