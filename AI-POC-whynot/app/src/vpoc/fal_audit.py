"""fal 모델 추가(활성화)·해제 기록 — 날짜별 영구 로그.

운영에서 "fal 모델 활성화가 거부됐다"는 보고가 왔지만 파드 재시작으로 stdout 로그가
사라져 원인을 확인할 수 없었다(2026-10-20). ② 영상 모델 고르기의 fal 카탈로그에서
모델을 **추가(register)** 하거나 **해제(unregister)** 하는 모든 시도를 성공·실패 구분 없이
영구 저장소(S3)에 날짜별 파일로 남긴다.

키는 `fal.REGISTRY_KEY` 와 같은 `users/_system/vpoc/` 아래에 둔다(IRSA 가 `users/*` 만 허용).
파일은 날짜별 `users/_system/vpoc/logs/fal/<YYYY-MM-DD>-error.log`, 날짜·시각은 KST.

이 모듈은 기록 실패가 추가·해제를 막지 않도록 **절대 예외를 밖으로 던지지 않는다**(record).
STORE 에 텍스트 추가 쓰기 함수가 없어 `store.s3_client` 와 `STORE.main_bucket` 을 직접 쓴다
(store.py 는 건드리지 않는다). 동시 쓰기는 store.write_json 과 같은 ETag 조건부 쓰기로 막는다.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from botocore.exceptions import ClientError, ParamValidationError

from . import store
from .store import STORE

log = logging.getLogger("vpoc")

# KST 는 표준 라이브러리 zoneinfo 로 얻는다. tzdata 가 없는 런타임을 대비해 UTC+9 고정으로 대체한다.
try:  # pragma: no cover - 분기 자체는 환경에 따라 다르다
    from zoneinfo import ZoneInfo

    _KST = ZoneInfo("Asia/Seoul")
except Exception:  # noqa: BLE001  (zoneinfo 없음·tzdata 없음 둘 다 포함)
    _KST = timezone(timedelta(hours=9), name="KST")

# store.write_json 과 같은 처리: boto3 가 조건부 쓰기를 모르면 한 번 확인하고 이후로는 붙이지 않는다.
_CONDITIONAL_WRITES = {"supported": True}

# 메시지 한 건의 최대 길이. 줄바꿈·구분자는 공백으로 바꾼 뒤 자른다.
_MESSAGE_MAX = 500
# 조건부 쓰기 충돌(동시 쓰기) 재시도 횟수.
_RETRY = 5

_PREFIX = "users/_system/vpoc/logs/fal/"


def _now_kst() -> datetime:
    return datetime.now(_KST)


def _date_str(dt: datetime | None = None) -> str:
    return (dt or _now_kst()).strftime("%Y-%m-%d")


def log_key(date_str: str) -> str:
    """날짜별 로그 파일 키. 날짜는 KST 기준 YYYY-MM-DD."""
    return f"{_PREFIX}{date_str}-error.log"


def today() -> str:
    """오늘(KST) 날짜 문자열 YYYY-MM-DD."""
    return _date_str()


def _clean(value: str) -> str:
    """메시지 한 건을 한 줄로 만든다. 줄바꿈과 구분자 '|' 는 공백으로 바꾸고 500자로 자른다."""
    text = str(value or "")
    for bad in ("\r\n", "\r", "\n", "\t", "|"):
        text = text.replace(bad, " ")
    text = " ".join(text.split())  # 연속 공백 정리
    if len(text) > _MESSAGE_MAX:
        text = text[:_MESSAGE_MAX]
    return text


def _format_line(
    action: str, endpoint_id: str, *, user: str, ok: bool, code: str, message: str, status
) -> str:
    """사람이 읽는 한 줄. 예:
    `2026-10-20 18:31:05 KST | register | FAIL | admin | fal-ai/... | NotVideoModel | 400 | 설명`
    """
    stamp = _now_kst().strftime("%Y-%m-%d %H:%M:%S KST")
    verdict = "OK" if ok else "FAIL"
    status_text = "" if status is None else str(status)
    fields = [
        stamp,
        _clean(action),
        verdict,
        _clean(user),
        _clean(endpoint_id),
        _clean(code),
        status_text,
        _clean(message),
    ]
    return " | ".join(fields)


def _read_raw(key: str) -> tuple[bytes, str]:
    """기존 내용과 ETag 를 읽는다. 없으면 (b"", "")."""
    try:
        response = store.s3_client(STORE.main_region).get_object(
            Bucket=STORE.main_bucket, Key=key
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound"):
            return b"", ""
        raise
    etag = (response.get("ETag") or "").strip('"')
    return response["Body"].read(), etag


def _put(key: str, body: bytes, *, etag: str, create_only: bool) -> None:
    """텍스트 로그를 쓴다(ServerSideEncryption · text/plain). 조건부 쓰기는 store.write_json 과 같은 처리."""
    kwargs: dict = {
        "Bucket": STORE.main_bucket,
        "Key": key,
        "Body": body,
        "ContentType": "text/plain; charset=utf-8",
        "ServerSideEncryption": "AES256",
    }
    if _CONDITIONAL_WRITES["supported"]:
        if create_only:
            kwargs["IfNoneMatch"] = "*"
        elif etag:
            kwargs["IfMatch"] = f'"{etag}"'
    try:
        store.s3_client(STORE.main_region).put_object(**kwargs)
    except ParamValidationError:
        # boto3 가 조건부 쓰기를 모르는 버전이다. 한 번만 확인하고 이후로는 붙이지 않는다.
        _CONDITIONAL_WRITES["supported"] = False
        kwargs.pop("IfNoneMatch", None)
        kwargs.pop("IfMatch", None)
        store.s3_client(STORE.main_region).put_object(**kwargs)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "NotImplemented":
            _CONDITIONAL_WRITES["supported"] = False
            kwargs.pop("IfNoneMatch", None)
            kwargs.pop("IfMatch", None)
            store.s3_client(STORE.main_region).put_object(**kwargs)
        else:
            raise


def _append(key: str, line: str) -> None:
    """기존 내용을 읽고 끝에 한 줄 붙여 쓴다. 동시 쓰기는 ETag 조건부 쓰기로 막고 충돌 시 재시도."""
    chunk = (line + "\n").encode("utf-8")
    last: BaseException | None = None
    for _ in range(_RETRY):
        existing, etag = _read_raw(key)
        create_only = not existing and not etag
        body = existing + chunk
        try:
            _put(key, body, etag=etag, create_only=create_only)
            return
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code in ("PreconditionFailed", "ConditionalRequestConflict"):
                last = exc
                continue  # 다른 파드가 먼저 썼다 — 다시 읽어서 끝에 붙인다.
            raise
    if last is not None:
        raise last


def record(
    action: str,
    endpoint_id: str,
    *,
    user: str,
    ok: bool,
    code: str = "",
    message: str = "",
    status=None,
) -> None:
    """추가(activate)·해제 시도를 날짜별 로그에 한 줄 남긴다.

    action 은 "register"(화면 문구: 활성화/추가) 또는 "unregister"(해제).
    **절대 예외를 밖으로 던지지 않는다** — 기록 실패가 추가·해제를 막으면 안 된다. 실패 시 warning 만.
    같은 줄을 log.info(성공) / log.warning(실패) 로도 남긴다(비밀값은 쓰지 않는다 — 호출부 책임).
    """
    line = _format_line(
        action, endpoint_id, user=user, ok=ok, code=code, message=message, status=status
    )
    if ok:
        log.info("vpoc.fal_audit %s", line)
    else:
        log.warning("vpoc.fal_audit %s", line)
    try:
        _append(log_key(_date_str()), line)
    except Exception as exc:  # noqa: BLE001  (기록 실패가 본 작업을 막으면 안 된다)
        log.warning("vpoc.fal_audit.write_failed err=%s", exc)


def read(date_str: str) -> str:
    """해당 날짜(YYYY-MM-DD) 파일 내용. 없으면 빈 문자열. 형식이 틀리면 ValueError."""
    # YYYY-MM-DD 형식만 허용한다. 그 밖은 ValueError(라우트에서 400 으로 바꾼다).
    datetime.strptime(date_str, "%Y-%m-%d")
    body, _ = _read_raw(log_key(date_str))
    return body.decode("utf-8", errors="replace")
