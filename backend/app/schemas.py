from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class SourceImportRequest(BaseModel):
    url: HttpUrl


class FolderBrowseRequest(BaseModel):
    url: HttpUrl | None = None


class ProposalSelection(BaseModel):
    meeting_id: str
    proposal_ids: list[str] = Field(min_length=1, max_length=2500)


class ModelProfileRequest(BaseModel):
    id: str | None = None
    name: str
    provider: Literal["openai", "deepseek", "qwen", "custom"]
    base_url: str
    api_key: str = ""
    text_model: str = ""
    vision_model: str = ""
    embedding_model: str = ""
    available_models: list[str] = Field(default_factory=list)
    deployment: Literal["external", "local"] = "external"
    max_output_tokens: int = Field(default=8192, ge=512, le=65536)
    context_window: int = Field(default=65536, ge=4096, le=1000000)
    max_concurrency: int = Field(default=4, ge=1, le=12)
    request_timeout_seconds: int = Field(default=180, ge=30, le=900)
    enabled: bool = True
    is_default: bool = True


class ChatRequest(BaseModel):
    meeting_id: str
    proposal_ids: list[str] = Field(default_factory=list, max_length=500)
    question: str = Field(min_length=1, max_length=8000)
    thread_id: str | None = None


class ChatJobRequest(ChatRequest):
    report_options: dict | None = None
    model_option_id: str | None = None
    analysis_mode: Literal["adaptive", "exhaustive"] = "adaptive"
    chat_mode: Literal["auto", "proposal", "general"] = "auto"
    client_request_id: str | None = Field(default=None, min_length=8, max_length=100)


class ChatThreadCreate(BaseModel):
    meeting_id: str
    title: str = "新会话"
    proposal_ids: list[str] = Field(default_factory=list, max_length=500)
    default_model_option_id: str | None = None


class ChatThreadUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    archived: bool | None = None
    default_model_option_id: str | None = None


class ChatBatchDelete(BaseModel):
    thread_ids: list[str] = Field(min_length=1, max_length=500)


class SourceAliasRequest(BaseModel):
    alias: str = Field(min_length=1, max_length=200)
    canonical_name: str = Field(min_length=1, max_length=200)


class ReportOutlineRequest(BaseModel):
    meeting_id: str
    proposal_ids: list[str] = Field(min_length=1, max_length=500)
    format: Literal["docx", "pptx"]
    title: str = "3GPP Proposal Analysis"
    language: Literal["en", "zh"] = "en"
    template_id: str | None = None
    template_mode: Literal["strict", "extend"] = "strict"
    instruction: str = ""


class ReportJobRequest(BaseModel):
    report_id: str
    outline: list[dict]
    thread_id: str | None = None


class ReportRenderRequest(BaseModel):
    outline: list[dict]
