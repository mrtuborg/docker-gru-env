"""Pipeline Pydantic models for API request/response validation."""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class ModelConfig(BaseModel):
    model: str
    priority: int = 1


class FindingsBoard(BaseModel):
    project_owner: str
    project_number: int
    initial_status: str = "Analysis"


class PipelineStage(BaseModel):
    column: str
    actor: str = "ai"
    agent_id: Optional[str] = ""
    task_prompt: Optional[str] = ""
    prompt: str = ""
    on_success: str = ""
    on_failure: str = ""
    on_timeout: str = ""
    env: Dict[str, str] = Field(default_factory=dict)


class PipelineCreate(BaseModel):
    id: str
    name: str
    enabled: bool = True
    plugin_id: str
    board_type: str = "github"
    project_owner: Optional[str] = None
    project_number: Optional[int] = None
    board_path: Optional[str] = None
    stages: List[PipelineStage] = Field(default_factory=list)
    poll_interval: int = 300
    max_issues: int = 50
    max_retries: int = 3
    session_timeout_hours: float = 4.0
    models: List[ModelConfig] = Field(default_factory=list)
    allowed_repos: List[str] = Field(default_factory=list)
    findings: Optional[FindingsBoard] = None
    working_dir: Optional[str] = None


class PipelineUpdate(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    plugin_id: Optional[str] = None
    board_type: Optional[str] = None
    project_owner: Optional[str] = None
    project_number: Optional[int] = None
    board_path: Optional[str] = None
    stages: Optional[List[PipelineStage]] = None
    poll_interval: Optional[int] = None
    max_issues: Optional[int] = None
    max_retries: Optional[int] = None
    session_timeout_hours: Optional[float] = None
    models: Optional[List[ModelConfig]] = None
    allowed_repos: Optional[List[str]] = None
    findings: Optional[FindingsBoard] = None
    working_dir: Optional[str] = None
