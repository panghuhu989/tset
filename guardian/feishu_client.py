import json
import time
from typing import Any, Dict, Optional

import requests


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str) -> None:
        self.app_id = app_id.strip()
        self.app_secret = app_secret.strip()
        self._token_cache: Dict[str, Any] = {
            "tenant_access_token": None,
            "expire_at": 0,
        }

    def get_tenant_access_token(self) -> str:
        now = time.time()
        cached = self._token_cache.get("tenant_access_token")

        if cached and now < float(self._token_cache.get("expire_at", 0)):
            return str(cached)

        if not self.app_id or not self.app_secret:
            raise RuntimeError("FEISHU_APP_ID or FEISHU_APP_SECRET is empty")

        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={
                "app_id": self.app_id,
                "app_secret": self.app_secret,
            },
            timeout=15,
        )

        try:
            data = resp.json()
        except Exception:
            raise RuntimeError(
                f"get tenant_access_token failed: status={resp.status_code}, body={resp.text}"
            )

        if resp.status_code != 200 or data.get("code") != 0:
            raise RuntimeError(f"get tenant_access_token failed: {data}")

        token = data["tenant_access_token"]
        expire = int(data.get("expire", 7200))

        self._token_cache["tenant_access_token"] = token
        self._token_cache["expire_at"] = now + max(expire - 600, 60)

        return token

    @staticmethod
    def infer_receive_id_type(receive_id: str) -> str:
        if receive_id.startswith("ou_"):
            return "open_id"
        if receive_id.startswith("oc_"):
            return "chat_id"
        return "chat_id"

    def send_text(
        self,
        receive_id: str,
        text: str,
        receive_id_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        token = self.get_tenant_access_token()
        actual_receive_id_type = receive_id_type or self.infer_receive_id_type(receive_id)

        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages",
            params={"receive_id_type": actual_receive_id_type},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={
                "receive_id": receive_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
            timeout=15,
        )

        try:
            data = resp.json()
        except Exception:
            data = {"raw": resp.text}

        if resp.status_code != 200 or data.get("code") != 0:
            raise RuntimeError(
                f"send feishu message failed: status={resp.status_code}, body={data}"
            )

        return data