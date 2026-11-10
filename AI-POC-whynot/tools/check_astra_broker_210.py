"""작업 210 검사 — GPT-6 Astra 는 와이랩이 아니라 키이스트 계정 역할로 부른다. 유료·네트워크·AWS 0(boto3·sts 가짜).

  (a-a) Astra → 키이스트 역할 · ExternalId whynot-astra-broker · 리전 · 세션 이름 whynot-vpoc-astra
  (a-b) Fable 5.1 · Opus 5.5 → 와이랩 역할 그대로(ExternalId whynot-vpoc-broker · 세션 whynot-vpoc) · 둘이 같은 클라이언트
  (a-c) Opus 4.6 → 와이낫 계정 runtime(assume_role 없음)
  (a-d) Astra 역할 빈 값 → Astra 는 와이낫 계정 runtime · 와이랩으로 넘어가지 않음
  (a-e) 캐시: 역할마다 클라이언트 1개 · assume_role 역할마다 1번 · 계정 runtime 1개
  (a-f) 설정: BROKER_MODEL_MARKERS 에 gpt-6 없음 · ASTRA_BROKER_MODEL_MARKERS = ("gpt-6",) · 기본값 · 스트림 권한 거절은 그 경로만
  (a-g) LF 줄 끝(바꾼 파일)

실행: python -X utf8 -B tools/check_astra_broker_210.py
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
for _path in (str(TOOLS), str(ROOT / "app")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

GROUPS = {
    "a-a": "Astra → 키이스트 역할 · ExternalId · 리전 · 세션 이름",
    "a-b": "Fable 5.1 · Opus 5.5 → 와이랩 역할 그대로",
    "a-c": "Opus 4.6 → 와이낫 계정 runtime",
    "a-d": "Astra 역할 빈 값 → 와이낫 계정(와이랩 아님)",
    "a-e": "역할마다 클라이언트 1개(캐시)",
    "a-f": "마커 · 기본값 · 스트림 권한 거절은 그 경로만",
    "a-g": "LF 줄 끝",
}
ASTRA = "global.openai.gpt-6-astra"
FABLE = "global.anthropic.claude-fable-5-1"
OPUS55 = "global.anthropic.claude-opus-5-5"
OPUS46 = "global.anthropic.claude-opus-4-6-v1"
KEYEAST_ROLE = "arn:aws:iam::722500516860:role/imsi-astra-used-role"  # account-check: ignore
YLAB_ROLE = "arn:aws:iam::892278727066:role/whynot-bedrock-broker"  # account-check: ignore
CHANGED = ["app/src/vpoc/settings.py", "app/src/vpoc/llm.py", "tools/check_astra_broker_210.py"]
HOOK = ('    if not __import__("check_astra_broker_210").check_astra_broker_210(): '
        'fail("작업 210: GPT-6 Astra 키이스트 역할 · Claude 5.x 와이랩 · 나머지 와이낫 계정 검사 실패")')


class _Runtime:
    """가짜 bedrock-runtime. 누가 만들었는지(account · role) 기억한다."""

    def __init__(self, account: str, region: str, role: str = "") -> None:
        self.account, self.region, self.role = account, region, role


class _Sts:
    def __init__(self, log: list, region: str) -> None:
        self.log, self.region = log, region

    def assume_role(self, **kwargs):
        self.log.append({"sts_region": self.region, **kwargs})
        return {"Credentials": {"AccessKeyId": "AK|" + kwargs["RoleArn"], "SecretAccessKey": "x", "SessionToken": "t",
                                "Expiration": datetime.now(timezone.utc) + timedelta(hours=1)}}


class _Session:
    def __init__(self, fake: "_Boto3", botocore_session) -> None:
        self.fake, self.botocore_session = fake, botocore_session

    def client(self, service, region_name=None, config=None):
        role = self.botocore_session._credentials.access_key.split("|", 1)[1]  # noqa: SLF001
        self.fake.made.append(("broker", service, region_name, role))
        return _Runtime("broker", region_name, role)


class _Boto3:
    """llm.boto3 대신. client("sts") · client("bedrock-runtime") · Session(...).client(...) 만 흉내 낸다."""

    def __init__(self) -> None:
        self.assumed: list[dict] = []
        self.made: list[tuple] = []

    def client(self, service, region_name=None, config=None):
        if service == "sts":
            return _Sts(self.assumed, region_name)
        self.made.append(("account", service, region_name, ""))
        return _Runtime("account", region_name)

    def Session(self, botocore_session=None):  # noqa: N802
        return _Session(self, botocore_session)


def check_astra_broker_210() -> bool:
    bad: dict[str, list[str]] = {g: [] for g in GROUPS}
    seen: set[str] = set()

    def expect(group: str, got, want, what: str) -> None:
        seen.add(group)
        if got != want:
            bad[group].append(f"{what}\n      결과 {str(got)[:300]}\n      기대 {str(want)[:300]}")

    def crash(group: str, exc: BaseException) -> None:
        seen.add(group)
        bad[group].append("예외: " + repr(exc)[:300] + "\n" + traceback.format_exc()[-1200:])

    from src.vpoc import llm, settings  # noqa: PLC0415

    names = ("BROKER_ROLE_ARN", "BROKER_EXTERNAL_ID", "BROKER_REGION", "BROKER_MODEL_MARKERS", "ASTRA_BROKER_ROLE_ARN",
             "ASTRA_BROKER_EXTERNAL_ID", "ASTRA_BROKER_REGION", "ASTRA_BROKER_MODEL_MARKERS")
    saved_settings = {n: getattr(settings, n, None) for n in names}
    saved = {"boto3": llm.boto3, "runtime": dict(llm._RUNTIME), "stream": dict(llm._STREAM)}
    fake = _Boto3()

    def reset() -> None:
        fake.assumed.clear()
        fake.made.clear()
        for key in list(llm._RUNTIME):
            llm._RUNTIME[key] = None
        settings.BROKER_ROLE_ARN, settings.BROKER_EXTERNAL_ID, settings.BROKER_REGION = YLAB_ROLE, "whynot-vpoc-broker", "ap-northeast-2"
        settings.BROKER_MODEL_MARKERS = ("claude-fable-", "claude-opus-5", "claude-sonnet-5")
        settings.ASTRA_BROKER_ROLE_ARN, settings.ASTRA_BROKER_EXTERNAL_ID = KEYEAST_ROLE, "whynot-astra-broker"
        settings.ASTRA_BROKER_REGION, settings.ASTRA_BROKER_MODEL_MARKERS = "ap-northeast-2", ("gpt-6",)

    def assumed() -> list:
        return [(a["RoleArn"], a["ExternalId"], a["RoleSessionName"]) for a in fake.assumed]

    llm.boto3 = fake
    try:
        # ================= (a-a) Astra → 키이스트 =================
        try:
            reset()
            c = llm.client_for(ASTRA)
            expect("a-a", [c.account, c.role, c.region, assumed(), [a["DurationSeconds"] for a in fake.assumed]],
                   ["broker", KEYEAST_ROLE, "ap-northeast-2", [(KEYEAST_ROLE, "whynot-astra-broker", "whynot-vpoc-astra")], [3600]],
                   "Astra 클라이언트: 키이스트 역할 · ExternalId · 리전 · 세션 이름")
            expect("a-a", [llm._route(ASTRA), llm._RUNTIME["astra_broker"] is c, llm._RUNTIME["broker"]],
                   ["astra_broker", True, None], "_RUNTIME['astra_broker'] 에 둔다 · 와이랩 클라이언트는 안 만든다")
        except Exception as exc:  # noqa: BLE001
            crash("a-a", exc)

        # ================= (a-b) Claude 5.x → 와이랩 =================
        try:
            reset()
            f, o = llm.client_for(FABLE), llm.client_for(OPUS55)
            expect("a-b", [f.role, f.region, f is o, assumed(), llm._RUNTIME["astra_broker"]],
                   [YLAB_ROLE, "ap-northeast-2", True, [(YLAB_ROLE, "whynot-vpoc-broker", "whynot-vpoc")], None],
                   "Fable 5.1 · Opus 5.5: 와이랩 역할 1번 · 같은 클라이언트 · 키이스트 안 만듦")
        except Exception as exc:  # noqa: BLE001
            crash("a-b", exc)

        # ================= (a-c) Opus 4.6 → 와이낫 계정 =================
        try:
            reset()
            c = llm.client_for(OPUS46)
            expect("a-c", [c.account, c.region, assumed(), fake.made],
                   ["account", settings.TEXT_REGION, [], [("account", "bedrock-runtime", settings.TEXT_REGION, "")]],
                   "Opus 4.6: 와이낫 계정 runtime · assume_role 없음")
        except Exception as exc:  # noqa: BLE001
            crash("a-c", exc)

        # ================= (a-d) Astra 역할 빈 값 → 와이낫 계정 =================
        try:
            reset()
            settings.ASTRA_BROKER_ROLE_ARN = ""
            c = llm.client_for(ASTRA)
            expect("a-d", [c.account, llm._route(ASTRA), assumed(), llm._RUNTIME["broker"], llm._RUNTIME["astra_broker"]],
                   ["account", "client", [], None, None], "빈 Astra 역할: 와이낫 계정 · 와이랩 assume_role 없음")
            expect("a-d", llm.client_for(FABLE).role, YLAB_ROLE, "그래도 Fable 은 와이랩 그대로")
        except Exception as exc:  # noqa: BLE001
            crash("a-d", exc)

        # ================= (a-e) 캐시 =================
        try:
            reset()
            first = [llm.client_for(m) for m in (ASTRA, FABLE, OPUS46)]
            again = [llm.client_for(m) for _ in range(3) for m in (ASTRA, OPUS55, FABLE, OPUS46)]
            expect("a-e", [sorted(a[0] for a in assumed()), len(fake.made), len({id(x) for x in first}),
                           all(x is first[0] for x in again[0::4]), all(x is first[1] for x in again[1::4]),
                           all(x is first[1] for x in again[2::4]), all(x is first[2] for x in again[3::4])],
                   [sorted([KEYEAST_ROLE, YLAB_ROLE]), 3, 3, True, True, True, True],
                   "역할마다 assume_role 1번 · 클라이언트 3개만 · 다시 불러도 같은 것")
        except Exception as exc:  # noqa: BLE001
            crash("a-e", exc)

        # ================= (a-f) 설정 · 스트림 권한 =================
        try:
            src = (ROOT / "app/src/vpoc/settings.py").read_text(encoding="utf-8")
            expect("a-f", [saved_settings["BROKER_MODEL_MARKERS"], saved_settings["ASTRA_BROKER_MODEL_MARKERS"]],
                   [("claude-fable-", "claude-opus-5", "claude-sonnet-5"), ("gpt-6",)], "마커: gpt-6 은 Astra 쪽에만")
            expect("a-f", ['"VPOC_BEDROCK_ASTRA_ROLE_ARN", "' + KEYEAST_ROLE + '"' in src,
                           '"VPOC_BEDROCK_ASTRA_EXTERNAL_ID", "whynot-astra-broker"' in src,
                           '"VPOC_BEDROCK_ASTRA_REGION", "ap-northeast-2"' in src],
                   [True, True, True], "settings.py 기본값(환경 변수 이름 · 역할 · ExternalId · 리전)")
            if not os.getenv("VPOC_BEDROCK_ASTRA_ROLE_ARN") and not os.getenv("VPOC_BEDROCK_ASTRA_EXTERNAL_ID"):
                expect("a-f", [saved_settings["ASTRA_BROKER_ROLE_ARN"], saved_settings["ASTRA_BROKER_EXTERNAL_ID"]],
                       [KEYEAST_ROLE, "whynot-astra-broker"], "환경 변수 없을 때 실제 값")
            # Astra 경로 스트림 권한 거절 → 그 경로만 보통 호출로 바꾼다(와이랩 · 계정 경로는 그대로).
            reset()
            from botocore.exceptions import ClientError  # noqa: PLC0415

            class _NoStream:
                def converse_stream(self, **_k):
                    raise ClientError({"Error": {"Code": "AccessDeniedException",
                                                 "Message": "no bedrock:InvokeModelWithResponseStream"}}, "ConverseStream")

                def converse(self, **_k):
                    return {"output": {"message": {"content": [{"toolUse": {"input": {"k": 1}}}]}},
                            "usage": {}, "stopReason": "tool_use"}

            llm._STREAM.clear()
            llm._STREAM.update({"ok": True, "broker": True, "astra_broker": True})
            llm._RUNTIME["astra_broker"] = _NoStream()
            tool = {"name": "t", "description": "t", "inputSchema": {"json": {"type": "object"}}}
            payload = llm._stream_once(ASTRA, "s", [{"text": "u"}], tool, 10)[0]
            expect("a-f", [payload, llm._STREAM], [{"k": 1}, {"ok": True, "broker": True, "astra_broker": False}],
                   "Astra 스트림 권한 거절 → astra_broker 만 끔")
        except Exception as exc:  # noqa: BLE001
            crash("a-f", exc)

        # ================= (a-g) LF =================
        try:
            crlf = [p for p in CHANGED if b"\r\n" in (ROOT / p).read_bytes()]
            expect("a-g", crlf, [], "CRLF 가 있는 파일")
        except Exception as exc:  # noqa: BLE001
            crash("a-g", exc)
    finally:
        llm.boto3 = saved["boto3"]
        llm._RUNTIME.clear()
        llm._RUNTIME.update(saved["runtime"])
        llm._STREAM.clear()
        llm._STREAM.update(saved["stream"])
        for n, v in saved_settings.items():
            setattr(settings, n, v)

    ok = True
    for group, title in GROUPS.items():
        if bad[group]:
            ok = False
            print(f"[FAIL] 작업 210 ({group}) {title}")
            for line in bad[group][:3]:
                print("    " + line[:600])
        elif group not in seen:
            ok = False
            print(f"[FAIL] 작업 210 ({group}) {title}: 검사하지 못했습니다")
        else:
            print(f"[ok  ] 작업 210 ({group}) {title}")
    return ok


if __name__ == "__main__":
    result = check_astra_broker_210()
    print("check_vpoc.py 에 붙일 줄(main, check_pose_controls_209a 다음):")
    print(HOOK)
    sys.exit(0 if result else 1)
