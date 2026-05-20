from typing import Any, Dict, Optional

from pydantic import BaseModel


class NotifyInfo(BaseModel):
    channel: str
    chat_id: str


class AlertInfo(BaseModel):
    id: str
    metric: str
    value: float
    threshold: float
    created_at: str
    status: str
    host: str
    platform: str
    workspace_dir: str
    pending_file: str
    details: Optional[Dict[str, Any]] = None


class GuardianAlertEvent(BaseModel):
    type: str
    session_id: str
    notify: NotifyInfo
    alert: AlertInfo
    strategy: Dict[str, Any]
    reply_commands: list[str]


class GuardianCommandEvent(BaseModel):
    text: str
    session_id: Optional[str] = None
    chat_id: Optional[str] = None
    send_reply: bool = False


def model_to_dict(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()