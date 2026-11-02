"""vpoc1 오류 봉투 — PRD 6.7.

AWS 오류는 Error.Code · Error.Message · RequestId · HTTP 상태를 **원문 그대로** 싣는다.
운영(VPOC_RAW_ERRORS=false)에서는 message 만 갈아치우고 원문은 로그에 남긴다.
"""

from __future__ import annotations

import logging
import re
import traceback
from datetime import datetime, timezone

from botocore.exceptions import ClientError

from . import settings

log = logging.getLogger("vpoc")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class VpocError(Exception):
    """화면에 그대로 보여 줄 오류. status 와 kind 를 직접 정한다."""

    def __init__(self, where: str, kind: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.where = where
        self.kind = kind
        self.message = message
        self.status = status


# 우리가 직접 만든 안내 문구라 운영에서도 그대로 보여 준다(원문 감추기 대상이 아니다).
# 감추면 사용자가 "처리 중 오류(코드: BadDuration)"만 보고 무엇을 고칠지 모른다(2026-10-13).
# fal.py 가 직접 쓴 한국어 안내 kind 도 같은 취지로 더한다. 원문이 외부 응답 그대로인
# HTTP<code> · NoRequestId 는 제외한다(감출 이유가 있다)(2026-10-19).
USER_FACING_KINDS = {
    "BadDuration",
    "FalBalanceExhausted",
    "FalKeyMissing",
    "FalUnreachable",
    "AlreadyRegistered",
    "SchemaUnreadable",
    "ModelUnavailable",
    "CharacterRealMissing",
    "RealStartImageUnsupported",
    # 073 R10.5: fal 모델 해제 거절(E3)은 운영(RAW_ERRORS=false)에서도 문구를 그대로 보인다.
    "AdminOnly",
    # vnew-live-fx A(2026-10-30 검토): ③ [기호·분위기 채우기] 거절 문구. 모두 고정 한국어 문구라(외부 원문 없음) 운영에서도 보인다.
    # ContiNotReady 는 generate.check_preconditions 의 고정 문구("먼저 콘티를 완성해 주세요.")에도 같이 적용된다.
    "FxDisabled",
    "ContiNotReady",
    "NothingToFill",
    # vnew-live-fx B3(2026-10-30, 091 남은 이슈 2 PM 결정): 실사 캐스팅 거절 문구. 던지는 곳(vnew/casting.py · vnew/routes.py)이
    # 모두 고정 한국어 문구다(사용자 입력·URL·키 없음). NoConfirmUsd 는 ⑤ 생성 시작(routes.py)의 같은 고정 문구에도 적용된다.
    # CostMismatch 는 넣지 않는다: 문구에 화면이 보낸 금액이 들어가고, vpoc(regen·routes)는 그 입력 원문을 그대로 싣는다.
    "CastingDisabled",
    "CastingRunning",
    "NoLook",
    "CandidateNotFound",
    "BadPreset",
    "BadGender",
    "BadLook",
    "NoConfirmUsd",
    "NoCandidateId",
    # vnew-live-fx C2(2026-10-30): 통 테이크 실사 버전 거절 문구. 던지는 곳(vnew/generate.py · vnew/routes.py)이 모두 고정
    # 한국어 문구다. CharacterRealMissing·FalKeyMissing·NoConfirmUsd 는 이미 있다. CostMismatch 는 B와 같은 이유로 넣지 않는다.
    "TakeRealDisabled",
    "RealNotReady",
    "RealRunning",
    "RealPromptWebtoon",
    # 지시서 103 피드백(2026-10-31): 캐스팅 BL 역할·유형 거절. 던지는 곳(vnew/casting.py)은 고정 문구 둘뿐이다
    # ("알 수 없는 BL 역할·유형입니다." · 역할과 유형이 맞지 않는다는 문구, 사용자 입력을 되풀이하지 않음).
    "BadBlType",
    # 지시서 111(2026-11-02): ⑦ 편집 거절 문구. 던지는 곳(vnew/edit.py · vnew/routes.py _edit_body)이 모두 고정 한국어 문구이고,
    # 문구에 들어가는 값은 서버 값뿐이다(몇 번째 클립·잰 원본 길이·한도 상수·그림체 이름, 사용자 글·키·URL 없음).
    # BadVariant·NoGen 은 characters.py·generate.py·routes.py 의 같은 kind 고정 문구("알 수 없는 그림체입니다." 등)에도 적용된다.
    "EditDisabled",
    "NoGen",
    "BadGen",
    "BadVariant",
    "NoMaterials",
    "NoEtag",
    "BadAudio",
    "AudioTrackUnsupported",
    "BadClips",
    "TooManyClips",
    "BadClip",
    "BadClipId",
    "DuplicateClipId",
    "SourceNotAllowed",
    "SourceUnreadable",
    "BadClipTime",
    "BadClipRange",
    "BadClipMuted",
    "TooLong",
    "EditChanged",
    "EditNotSaved",
    "EmptyTimeline",
    "ExportRunning",
    "MediaNotFound",
    "BodyTooLarge",
    # 지시서 116(2026-11-02): ⑦ AI로 고치기 거절 문구. 던지는 곳(vnew/retake.py · vnew/generate.py resubmit_cell)이 모두 고정 한국어 문구이고,
    # 문구에 들어가는 값은 서버 값뿐이다(글자 수 상한·바이트 수·정규식으로 뽑은 참고 표시·항목 이름, 사용자 글·키·URL 없음).
    # NoConfirmUsd·FalKeyMissing·MediaNotFound 는 이미 있다. CostMismatch 는 넣지 않는다(화면 금액이 문구에 들어간다 — 기존 규칙).
    "RetakeDisabled",
    "RetakeNotTarget",
    "RetakeNoPrompt",
    "RetakeBadRequest",
    "RetakeDrafting",
    "RetakeRunning",
    "RetakeNoDraft",
    "RetakePromptInvalid",
    "RetakeLimit",
    "RetakeGenChanged",
    # 지시서 116 보완 1(2026-11-02): 모델 고르기·클립 구간. 고정 한국어 문구(retake.BAD_MODEL_TEXT·BAD_RANGE_TEXT, generate.RETAKE_MODEL_GONE_TEXT).
    "RetakeBadModel",
    "RetakeBadRange",
}


_DATA_URI_RE = re.compile(r"data:[\w.+/-]+;base64,[A-Za-z0-9+/=]*")
_URL_QUERY_RE = re.compile(r"(https?://[^\s?#\"'<>]+)[?#][^\s\"'<>]*")
_SPACES_RE = re.compile(r"\s+")


def error_gist(text: object, limit: int = 200) -> str:
    """화면·로그용 오류 요지(073 R15, design.md §13.2).

    외부 응답 원문에서 비밀이 될 수 있는 것을 지운다: URL 쿼리(서명·토큰), data URI(이미지 본문),
    fal 키 값. 공백을 한 칸으로 모으고 limit 자로 자른다(넘으면 끝에 "…").
    """
    value = str(text or "")
    if not value:
        return ""
    value = _DATA_URI_RE.sub("data:…", value)
    value = _URL_QUERY_RE.sub(r"\1", value)
    key = str(getattr(settings, "FAL_KEY", "") or "")
    if key:
        value = value.replace(key, "***")
    value = _SPACES_RE.sub(" ", value).strip()
    limit = max(1, int(limit))
    if len(value) > limit:
        value = value[: limit - 1].rstrip() + "…"
    return value


def envelope(where: str, exc: BaseException) -> dict:
    """예외를 오류 봉투로 바꾼다."""
    if isinstance(exc, VpocError):
        body = {
            "where": exc.where or where,
            "type": exc.kind,
            "message": exc.message,
            "requestId": "",
            "httpStatus": exc.status,
            "at": now_iso(),
        }
    elif isinstance(exc, ClientError):
        err = exc.response.get("Error", {}) or {}
        meta = exc.response.get("ResponseMetadata", {}) or {}
        body = {
            "where": where,
            "type": str(err.get("Code") or type(exc).__name__),
            "message": str(err.get("Message") or exc),
            "requestId": str(meta.get("RequestId") or ""),
            "httpStatus": int(meta.get("HTTPStatusCode") or 502),
            "at": now_iso(),
        }
    else:
        body = {
            "where": where,
            "type": type(exc).__name__,
            "message": str(exc),
            "requestId": "",
            "httpStatus": 500,
            "at": now_iso(),
        }

    log.error(
        "vpoc.error where=%s type=%s msg=%s",
        body["where"],
        body["type"],
        body["message"][:800],
    )
    if not settings.RAW_ERRORS and not (
        isinstance(exc, VpocError) and exc.kind in USER_FACING_KINDS
    ):
        log.error("vpoc.error.raw %s", traceback.format_exc()[:4000])
        body["message"] = f"처리 중 오류가 발생했습니다 (코드: {body['type']})"
        body["requestId"] = ""
    return body


def status_for(body: dict) -> int:
    """봉투의 httpStatus 를 라우트 응답 코드로 옮긴다."""
    status = int(body.get("httpStatus") or 500)
    if status in (400, 401, 403, 404, 409, 413):
        return status
    if 200 <= status < 300:
        return 502
    if status >= 500:
        return 502
    return 502
