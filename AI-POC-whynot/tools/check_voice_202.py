"""작업 202 검사 — ⑥ 사운드 인물 목소리(voices.py · voices_routes.py · sound.py 확장). 가짜 fal·가짜 저장소·로컬 ffmpeg, 유료·네트워크 0.

  (v-a) 순수 함수: splice(크로스페이드·길이 그대로·모노/스테레오)·fit_frames·match_gain·unit_usd·wav 왕복
  (v-b) 프로필 PUT·보기(GET 사운드 보기 voices): 모르는 인물·없는 목소리 이름·모델·남의 대사·원본 밖 구간 400, plan.voices 모양·revision + 1
  (v-c) 기준 소리 자르기: 사운드 폴더 voices/ 아래 wav·모노·길이 = 구간·REF_MAX_S(15초) 넘으면 잘림
  (v-d) 들어보기: dryRun 견적(fal 0회)·confirmUsd 없음 400·다름 409·맞으면 저장·주소·원장 1건, 큰 입력은 서명 주소, 단가 모름 = "가격 확인 불가"·0 USD 로 진행
  (v-e) 전체 반영 2명 × 3줄(1줄 fal 거절): 견적 = 줄별 합·409·fal 요청 6회·원장 6건(키 겹침 없음)·거절 줄은 원래 소리 그대로
  (v-f) 길이: 결과 길이 = 원본 ±1프레임·PCM 샘플 수 같음·줄 구간 그대로·바꾼 트랙 길이 같음
  (v-g) 소리 크기: 바꾼 줄(실제 말) EBU R128 차 ≤ 1.5 LU(변형: 맞추지 않은 가짜 결과는 크게 다르다)
  (v-h) 속마음: 효과는 바꾼 트랙 위(렌더 입력 = 트랙, 속마음 구간만 트랙과 다름·실제 말 구간은 트랙 그대로)
  (v-i) 새 버전 v{n}.mp4 + versions[].voices 요약·job message "5/6 줄 목소리 맞춤, 1줄 원음 유지"
  (v-j) 모델 노트: 거절 줄 stage voice·대상 voice:e4·모델
  (v-k) VNEW_SOUND_VOICES 끔 → 새 라우트 404(프로젝트 조회 0)·보기에 voices 없음
  (v-l) 옛 문서: 목소리 저장·들어보기는 사운드 문서를 바꾸지 않음·목소리 없는 반영은 옛 모양 그대로·원본 영상 그대로·
        소리 파일은 사운드 폴더 아래만·로그에 주소·키 없음·임시 폴더 남지 않음
  (v-m) 나중 영상(새 gen): 저장된 목소리를 그대로 씀("저장된 목소리 사용 중")

실행: python -X utf8 -B tools/check_voice_202.py
"""

from __future__ import annotations

import base64
import json
import logging
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from array import array
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
for _path in (str(TOOLS), str(ROOT / "app")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

GROUPS = {
    "v-a": "순수 함수(크로스페이드·길이·크기·단가)",
    "v-b": "프로필 PUT·보기·검사(400)",
    "v-c": "기준 소리 자르기(모노·길이·15초)",
    "v-d": "들어보기 견적·확인·원장",
    "v-e": "전체 반영 2명×3줄·1줄 실패 원음",
    "v-f": "길이 그대로(±1프레임)",
    "v-g": "소리 크기 ±1.5 LU",
    "v-h": "속마음 효과는 바꾼 뒤",
    "v-i": "새 버전·voices 요약",
    "v-j": "모델 노트",
    "v-k": "끔 → 404",
    "v-l": "옛 문서·원본·키·로그",
    "v-m": "나중 영상도 같은 목소리",
}
LINES = [(0.5, 1.3), (1.8, 2.6), (3.1, 3.9), (4.4, 5.2), (5.7, 6.5), (7.0, 7.8)]  # e1..e6
SPK_A, SPK_B = "민지", "준호"  # e1·e3·e5(속마음) / e2·e4·e6
RATE = 48000


def _fixture(ffmpeg: str, path: str) -> None:
    """8초 160x120 24fps 영상 + 48kHz 모노 소리: 민지 줄은 440Hz, 준호 줄은 330Hz(0.3), 그 밖은 무음."""
    a = "+".join(f"between(t,{s},{e})" for i, (s, e) in enumerate(LINES) if i % 2 == 0)
    b = "+".join(f"between(t,{s},{e})" for i, (s, e) in enumerate(LINES) if i % 2 == 1)
    expr = f"0.3*sin(2*PI*440*t)*({a})+0.3*sin(2*PI*330*t)*({b})"
    command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=160x120:r=24:d=8",
               "-f", "lavfi", "-i", f"aevalsrc='{expr}':s={RATE}:d=8", "-c:a", "aac", "-b:a", "128k", "-shortest",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", path]
    result = subprocess.run(command, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError("검사용 mp4 를 만들지 못했습니다: " + result.stderr.decode("utf-8", errors="replace")[-300:])


def _lufs(ffmpeg: str, voices, rate: int, samples: array, work: str) -> float:
    path = os.path.join(work, "lufs.wav")
    with open(path, "wb") as handle:
        handle.write(voices.wav_bytes(rate, 1, samples))
    result = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-i", path, "-af", "ebur128", "-f", "null", "-"], capture_output=True)
    found = re.findall(r"I:\s+(-?\d+(?:\.\d+)?) LUFS", result.stderr.decode("utf-8", errors="replace"))
    return float(found[-1]) if found else float("nan")


class _LogTap(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record) -> None:
        try:
            self.lines.append(record.getMessage())
        except Exception:  # noqa: BLE001
            self.lines.append(str(record.msg))


def check_voice_202() -> bool:
    bad: dict[str, list[str]] = {g: [] for g in GROUPS}
    seen: set[str] = set()

    def expect(group: str, got, want, what: str) -> None:
        seen.add(group)
        if got != want:
            bad[group].append(f"{what}\n      결과 {str(got)[:400]}\n      기대 {str(want)[:400]}")

    def crash(group: str, exc: BaseException) -> None:
        seen.add(group)
        bad[group].append("예외: " + repr(exc)[:300] + "\n" + traceback.format_exc()[-1500:])

    # ================= (v-a) 순수 함수 =================
    try:
        from src.vnew import voices  # noqa: PLC0415

        base = array("h", [1000] * 1000)
        voices.splice(base, array("h", [-1000] * 200), 100, 1, 20)
        expect("v-a", [len(base), base[99], base[100], base[110], base[119], list(set(base[120:280])), base[299], base[300]],
               [1000, 1000, 950, -50, -950, [-1000], 950, 1000],
               "splice 모노: 길이 그대로·구간 밖 그대로·앞뒤 20샘플 직선 크로스페이드·가운데는 새 소리")
        stereo = array("h", [500, -500] * 100)
        voices.splice(stereo, array("h", [0, 0] * 40), 30, 2, 10)
        expect("v-a", [len(stereo), stereo[58:60].tolist(), stereo[60:62].tolist(), stereo[80:82].tolist(), stereo[140:142].tolist()],
               [200, [500, -500], [475, -475], [0, 0], [500, -500]], "splice 스테레오: 채널마다 같은 섞음")
        expect("v-a", [len(voices.fit_frames(array("h", [1] * 10), 1, 6)), voices.fit_frames(array("h", [1] * 4), 2, 3).tolist()],
               [6, [1, 1, 1, 1, 0, 0]], "fit_frames: 길면 자르고 짧으면 무음으로 채움")
        expect("v-a", [round(voices.match_gain(array("h", [100, -100] * 50), array("h", [400, -400] * 50)), 3),
                       voices.match_gain(array("h", [0] * 10), array("h", [400] * 10)),
                       voices.match_gain(array("h", [1, -1] * 50), array("h", [30000, -30000] * 50))],
               [4.0, 1.0, 8.0], "match_gain: RMS 비율·무음이면 1·상한 GAIN_MAX(8)")
        expect("v-a", [voices.unit_usd({"unitPrice": 0.01, "unit": "seconds"}, 2.0), voices.unit_usd({"unitPrice": 0.6, "unit": "minutes"}, 30),
                       voices.unit_usd({"unitPrice": 0.05, "unit": "requests"}, 9), voices.unit_usd({"unitPrice": 1, "unit": "1000 characters"}, 3),
                       voices.unit_usd(None, 3)],
               [0.02, 0.3, 0.05, None, None], "unit_usd: 초·분·요청·모르는 단위·단가 없음")
        rate, ch, back = voices.read_wav(voices.wav_bytes(24000, 2, array("h", [1, 2, 3, 4])))
        expect("v-a", [rate, ch, back.tolist()], [24000, 2, [1, 2, 3, 4]], "wav 왕복")
    except Exception as exc:  # noqa: BLE001
        crash("v-a", exc)

    # ================= 라우트·실제 ffmpeg =================
    import check_vpoc as cv  # noqa: PLC0415 — 하네스만 읽어 쓴다(_EdtEnv·_pin_vnew·_snd_pcm)

    ffmpeg = ""
    try:
        from src import stitcher as stitchmod  # noqa: PLC0415

        ffmpeg = stitchmod.ffmpeg_path()
    except Exception:  # noqa: BLE001
        ffmpeg = ""
    if not ffmpeg:
        print("[FAIL] 작업 202: ffmpeg 가 없어 목소리 검사를 할 수 없습니다")
        return False
    tmp_before = {n for n in os.listdir(tempfile.gettempdir()) if n.startswith(("vnew-voice-", "vnew-sound-"))}
    work = tempfile.mkdtemp(prefix="voice202-")
    tap = _LogTap()
    vlog = logging.getLogger("vpoc")
    vlog.addHandler(tap)
    old_level = vlog.level
    vlog.setLevel(logging.INFO)
    saved_env = os.environ.get("VNEW_SOUND_VOICES")
    os.environ["VNEW_SOUND_VOICES"] = "true"
    try:
        src_path = os.path.join(work, "src.mp4")
        _fixture(ffmpeg, src_path)
        with open(src_path, "rb") as handle:
            src_bytes = handle.read()
        with cv._EdtEnv() as env, cv._pin_vnew(VNEW_EDIT=True, VNEW_SOUND=True, VNEW_MODEL_NOTES=True):  # noqa: SLF001
            from flask import Blueprint, Flask, jsonify  # noqa: PLC0415

            from src.vnew import model_notes as notesmod  # noqa: PLC0415
            from src.vnew import sound as sndmod  # noqa: PLC0415
            from src.vnew import voices  # noqa: PLC0415
            from src.vnew import voices_routes  # noqa: PLC0415
            from src.vpoc import settings as vpoc_set  # noqa: PLC0415
            from src.vpoc.errors import VpocError, envelope, status_for  # noqa: PLC0415

            owner, pid = cv._EDT_OWNER, "prj_voice"  # noqa: SLF001
            root = f"users/{owner}/video/{pid}/vnew/gen/g1"
            queue = vpoc_set.FAL_QUEUE_URL

            # ---- 가짜 fal · 단가 · 원장 ----
            fal = {"posts": 0, "reject": set(), "submitFail": set(), "requests": {}}
            charges: list[dict] = []
            prices = {voices.MODEL_HD: {"unitPrice": 0.01, "unit": "seconds"},
                      voices.MODEL_ELEVEN: {"unitPrice": 0.3, "unit": "minutes"}, voices.MODEL_LITE: None}

            def fake_fal(method, url, payload, *, where):
                if method == "POST":
                    fal["posts"] += 1
                    rid = f"req-{fal['posts']}"
                    fal["requests"][rid] = {"endpoint": url[len(queue) + 1:], "payload": payload, "where": where}
                    if fal["posts"] in fal["submitFail"]:
                        raise VpocError(where, "HTTP422", "bad input", 502)
                    return {"request_id": rid}
                parts = url.split("?")[0].rstrip("/").split("/")
                if parts[-1] == "status":
                    return {"status": "COMPLETED"}
                rid = parts[-1]
                if int(rid.split("-")[1]) in fal["reject"]:
                    raise VpocError(where, "HTTP422", "Unprocessable Entity: voice conversion rejected for this audio", 502)
                return {"audio": {"url": f"https://fal.example/out/{rid}.wav"}}

            fake_out: dict[str, bytes] = {}

            def fake_fetch(url):
                rid = url.rsplit("/", 1)[-1][:-4]
                payload = fal["requests"][rid]["payload"]
                src = payload.get("source_audio_url") or payload.get("audio_url") or ""
                if src.startswith("data:"):
                    data = base64.b64decode(src.partition(",")[2])
                else:
                    data = env.store.blobs[src.split("https://signed.example/", 1)[1].split("?", 1)[0]]
                rate, _ch, samples = voices.read_wav(data)
                block = max(1, rate // 100)
                out = array("h")
                out_rate = 24000
                n_out = int(len(samples) * out_rate / rate) + int(0.05 * out_rate)  # 50ms 더 길게(자르기 검사)
                loud = [voices.rms(samples[i:i + block]) > 200 for i in range(0, len(samples), block)]
                for j in range(n_out):
                    t = j / out_rate
                    on = loud[min(len(loud) - 1, int(t * rate) // block)] if int(t * rate) < len(samples) else False
                    out.append(int(2000 * math.sin(2 * math.pi * 660 * t)) if on else 0)
                body = voices.wav_bytes(out_rate, 1, out)
                fake_out[rid] = body
                return body

            def fake_charge(key, *, model, seconds, usd, owner, pid, gen_id):
                charges.append({"key": key, "model": model, "seconds": seconds, "usd": usd, "gen": gen_id})

            env.set(voices, "_fal_call", fake_fal)
            env.set(voices, "_fetch_audio", fake_fetch)
            env.set(voices, "_price", lambda endpoint: prices.get(endpoint))
            env.set(voices, "_charge", fake_charge)
            env.set(voices, "_sleep", lambda s: None)
            env.set(voices, "_fal_ready", lambda: True)
            renders: list[dict] = []
            orig_render = sndmod._render  # noqa: SLF001

            def capture_render(source_path, output_path, spans, audio_path=None):
                item = {"spans": [list(s) for s in spans], "audio": bool(audio_path), "track": None}
                if audio_path:
                    with open(audio_path, "rb") as handle:
                        item["track"] = handle.read()
                renders.append(item)
                return orig_render(source_path, output_path, spans, audio_path=audio_path)

            env.set(sndmod, "_render", capture_render)
            env.set(sndmod, "_spawn_apply", lambda o, p, g, v, c: sndmod.run_apply(o, p, g, v, c))

            # ---- 라우트: 빈 블루프린트 + register ----
            project = {"deny": False, "calls": 0}

            def project_for(pid_):
                project["calls"] += 1
                if project["deny"]:
                    raise VpocError("vnew.sound", "NotFound", "프로젝트를 찾을 수 없습니다.", 404)
                return owner

            def fail(exc):
                body = envelope("vnew", exc)
                return jsonify({"error": body}), status_for(body)

            class _Deps:
                require_auth = staticmethod(lambda handler: handler)

            bp = Blueprint("vnew", __name__, url_prefix="/api/vnew")
            voices_routes.register(bp, _Deps(), project_for, fail)
            app = Flask("check_voice_202")
            app.register_blueprint(bp)
            client = app.test_client()
            base_url = f"/api/vnew/p/{pid}/sound"

            def err_of(resp) -> list:
                body = (resp.get_json(silent=True) or {}).get("error") or {}
                return [resp.status_code, str(body.get("type") or "")]

            # ---- 계획 ----
            plan = env.planmod.new_plan()
            plan["revision"] = 3
            plan["script"] = {"status": "ready", "setting": {}, "entries": [
                {"id": f"e{i}", "panels": [i], "speaker": SPK_A if i % 2 else SPK_B, "lineKo": f"대사 {i}",
                 "voice": "thought" if i == 5 else "spoken"} for i in range(1, 7)]}
            plan["conti"] = {"status": "ready", "segments": [], "cuts": []}
            final_web = f"{root}/final-webtoon.mp4"
            final_real = f"{root}/final-real.mp4"
            plan["gen"] = {"genId": "g1", "mode": "previz", "status": "done", "createdAt": "2026-11-09T00:00:00Z", "segments": [],
                           "units": [], "finalWebtoon": {"status": "done", "key": final_web, "error": ""},
                           "finalReal": {"status": "done", "key": final_real, "error": ""}, "selected": "webtoon", "costUsd": 0.0}
            env.store.blobs.update({final_web: src_bytes, final_real: src_bytes})
            env.save_plan(pid, plan)
            exact = [{"id": f"e{i + 1}", "start": s, "end": e} for i, (s, e) in enumerate(LINES)]
            for variant in ("webtoon", "real"):
                sndmod.sound_view(owner, pid, variant)
                sndmod.save_lines(owner, pid, {"variant": variant, "lines": exact})
            doc_key = sndmod.doc_key(owner, pid, "g1", "webtoon")
            doc_before = json.dumps(env.store.read_json(doc_key)[0], ensure_ascii=False, sort_keys=True)
            lines_before = [(l["id"], l["start"], l["end"]) for l in json.loads(doc_before)["lines"]]

            # ================= (v-b) 프로필 PUT · 보기 =================
            put = lambda body: client.put(base_url + "/voices", json=body)  # noqa: E731
            try:
                rev0 = env.plan(pid)["revision"]
                expect("v-b", [err_of(put({"speaker": "없는사람", "mode": "preset", "preset": "Roger"})),
                               err_of(put({"speaker": SPK_B, "mode": "preset", "preset": "Nobody"})),
                               err_of(put({"speaker": SPK_A, "mode": "clip", "ref": {"lineId": "e1", "start": 7.5, "end": 9.0}})),
                               err_of(put({"speaker": SPK_A, "mode": "clip", "ref": {"lineId": "e1", "start": -0.2, "end": 1.0}})),
                               err_of(put({"speaker": SPK_A, "mode": "clip", "model": voices.MODEL_ELEVEN, "ref": {"lineId": "e1"}})),
                               err_of(put({"speaker": SPK_A, "mode": "clip", "ref": {"lineId": "e2"}})),
                               err_of(put({"speaker": SPK_A, "mode": "clip"})),
                               err_of(put({"speaker": SPK_A, "mode": "loud"})),
                               env.plan(pid)["revision"] == rev0, "voices" in env.plan(pid)],
                       [[400, "UnknownSpeaker"], [400, "BadVoicePreset"], [400, "BadVoiceRefRange"], [400, "BadVoiceRefRange"],
                        [400, "BadVoiceModel"], [400, "BadVoiceRef"], [400, "BadVoiceRef"], [400, "BadVoiceMode"], True, False],
                       "모르는 인물·없는 목소리 이름·원본 밖 구간(끝 > 8초·음수)·모드에 안 맞는 모델·남의 대사·ref 없음·모드 → 400, plan 그대로")
                resp_a = put({"speaker": SPK_A, "mode": "clip", "ref": {"lineId": "e1"}})
                resp_b = put({"speaker": SPK_B, "mode": "preset", "preset": "Roger"})
                stored = env.plan(pid).get("voices") or {}
                prof_a, prof_b = stored.get(SPK_A) or {}, stored.get(SPK_B) or {}
                ref_a = prof_a.get("ref") or {}
                expect("v-b", [resp_a.status_code, resp_b.status_code, env.plan(pid)["revision"] - rev0, sorted(stored),
                               [prof_a.get("mode"), prof_a.get("model"), prof_a.get("preset")],
                               sorted(ref_a), [ref_a.get("genId"), ref_a.get("variant"), ref_a.get("lineId"), ref_a.get("start"), ref_a.get("end")],
                               [prof_b.get("mode"), prof_b.get("model"), prof_b.get("preset"), prof_b.get("ref")], "perVariant" in prof_a],
                       [200, 200, 2, sorted([SPK_A, SPK_B]), ["clip", voices.MODEL_HD, ""],
                        ["end", "genId", "key", "lineId", "seconds", "start", "variant"], ["g1", "webtoon", "e1", 0.5, 1.3],
                        ["preset", voices.MODEL_ELEVEN, "Roger", None], False],
                       "PUT: plan.voices[이름] = {mode, model, preset, ref{genId, variant, lineId, start, end, key}}·revision + 1씩")
                got = sndmod.sound_view(owner, pid, "webtoon").get("voices") or {}
                sp = {s["speaker"]: s for s in got.get("speakers") or []}
                models = {m["id"]: m for m in got.get("models") or []}
                expect("v-b", [sorted(sp), [sp.get(SPK_A, {}).get("status"), sp.get(SPK_B, {}).get("status")],
                               [sp.get(SPK_A, {}).get("lines"), sp.get(SPK_A, {}).get("scope")],
                               bool(((sp.get(SPK_A, {}).get("profile") or {}).get("ref") or {}).get("url")),
                               "key" in json.dumps(got.get("profiles")), sorted(models),
                               models.get(voices.MODEL_HD, {}).get("presets"), "Roger" in (models.get(voices.MODEL_ELEVEN, {}).get("presets") or []),
                               models.get(voices.MODEL_LITE, {}).get("modes")],
                       [sorted([SPK_A, SPK_B]), [voices.USING_SAVED_LABEL] * 2, [3, "shared"], True, False, sorted(voices.MODELS),
                        list(voices.CHATTERBOX_HD_VOICES), True, ["clip"]],
                       "GET 사운드 보기 voices: 인물별 '저장된 목소리 사용 중'·줄 수·공유·기준 소리 주소(키는 안 보임)·모델별 목소리 목록")
            except Exception as exc:  # noqa: BLE001
                crash("v-b", exc)

            # ================= (v-c) 기준 소리 =================
            try:
                ref_key = str(((env.plan(pid).get("voices") or {}).get(SPK_A) or {}).get("ref", {}).get("key") or "")
                rate, ch, samples = voices.read_wav(env.store.blobs.get(ref_key) or b"")
                expect("v-c", [ref_key.startswith(f"{root}/sound/webtoon/voices/ref-"), ref_key.endswith(".wav"), ch, rate,
                               abs(len(samples) / rate - 0.8) <= 0.02, voices.REF_MAX_S, voices.rms(samples) > 1000],
                       [True, True, 1, voices.EXCERPT_RATE, True, 15.0, True], "기준 소리: voices/ref-*.wav·모노·길이 0.8초(그 줄 구간)·상한 15초·소리 있음")
                saved_max = voices.REF_MAX_S
                voices.REF_MAX_S = 0.6
                try:
                    resp = put({"speaker": SPK_A, "mode": "clip", "ref": {"lineId": "e3"}})
                finally:
                    voices.REF_MAX_S = saved_max
                ref3 = ((env.plan(pid).get("voices") or {}).get(SPK_A) or {}).get("ref") or {}
                r3, _c3, s3 = voices.read_wav(env.store.blobs.get(str(ref3.get("key") or "")) or b"")
                expect("v-c", [resp.status_code, ref3.get("start"), ref3.get("end"), abs(len(s3) / r3 - 0.6) <= 0.02],
                       [200, 3.1, 3.7, True], "상한을 넘는 구간은 상한 길이로 자른다")
                put({"speaker": SPK_A, "mode": "clip", "ref": {"lineId": "e1"}})  # 다시 e1 기준
            except Exception as exc:  # noqa: BLE001
                crash("v-c", exc)

            # ================= (v-d) 들어보기 =================
            prev = lambda body: client.post(base_url + "/voice-preview", json={"variant": "webtoon", **body})  # noqa: E731
            try:
                posts0, charges0 = fal["posts"], len(charges)
                dry = prev({"lineId": "e3", "dryRun": True})
                est = (dry.get_json() or {}).get("estimate") or {}
                want_usd = round(0.01 * (3.9 + 0.15 - (3.1 - 0.15)), 2)
                expect("v-d", [dry.status_code, est.get("usd"), est.get("priceKnown"), est.get("requests"), fal["posts"] - posts0,
                               len(charges) - charges0],
                       [200, want_usd, True, 1, 0, 0], "dryRun: 견적(단가 × 앞뒤 0.15초 포함 길이)만·fal 0회·원장 0건")
                expect("v-d", [err_of(prev({"lineId": "e3"})), err_of(prev({"lineId": "e3", "confirmUsd": 9.99})),
                               err_of(prev({"lineId": "e9", "confirmUsd": want_usd})), fal["posts"] - posts0, len(charges) - charges0],
                       [[400, "NoConfirmUsd"], [409, "CostMismatch"], [400, "BadLine"], 0, 0], "confirmUsd 없음 400·다름 409·없는 줄 400(fal 0회)")
                ok_resp = prev({"lineId": "e3", "confirmUsd": want_usd})
                body = ok_resp.get_json() or {}
                pv = body.get("preview") or {}
                payload = (fal["requests"].get(f"req-{fal['posts']}") or {}).get("payload") or {}
                pkey = (pv.get("url") or "").split("https://signed.example/", 1)[-1].split("?", 1)[0]
                prate, pch, psamples = voices.read_wav(env.store.blobs.get(pkey) or b"RIFF")  if pkey in env.store.blobs else (0, 0, array("h"))
                expect("v-d", [ok_resp.status_code, fal["posts"] - posts0, len(charges) - charges0,
                               [round(c["usd"], 4) for c in charges[charges0:]], (body.get("charge") or {}).get("requests"),
                               pkey.startswith(f"{root}/sound/webtoon/voices/preview-"), [pv.get("lineId"), pv.get("speaker"), pv.get("mode")],
                               sorted(payload), str(payload.get("source_audio_url") or "")[:22], str(payload.get("target_voice_audio_url") or "")[:22],
                               [pch, prate, abs(len(psamples) / max(prate, 1) - 1.1) <= 0.02]],
                       [200, 1, 1, [0.011], 1, True, ["e3", SPK_A, "clip"],
                        ["high_quality_audio", "source_audio_url", "target_voice_audio_url"], "data:audio/wav;base64,", "data:audio/wav;base64,",
                        [1, voices.EXCERPT_RATE, True]],
                       "확인 금액이 맞으면 fal 1회·원장 1건(받은 뒤)·voices/preview-*.wav(모노·1.1초)·data URI 입력")
                env.set(voices, "DATA_URI_MAX_BYTES", 1000)
                big = prev({"lineId": "e3", "confirmUsd": want_usd})
                payload = (fal["requests"].get(f"req-{fal['posts']}") or {}).get("payload") or {}
                src_url = str(payload.get("source_audio_url") or "")
                voices.DATA_URI_MAX_BYTES = 1_500_000
                expect("v-d", [big.status_code, src_url.startswith(f"https://signed.example/{root}/sound/webtoon/voices/tmp/in-"),
                               f"e={voices.PRESIGN_S}" in src_url, str(payload.get("target_voice_audio_url") or "").startswith("https://signed.example/")],
                       [200, True, True, True], "큰 입력은 사운드 폴더 voices/tmp/ 서명 주소(짧은 만료)")
                over = put({"speaker": SPK_B, "mode": "clip", "model": voices.MODEL_LITE, "variant": "real",
                            "ref": {"lineId": "e2", "variant": "real"}})
                dry_real = client.post(base_url + "/voice-preview", json={"variant": "real", "lineId": "e2", "dryRun": True})
                est_real = (dry_real.get_json() or {}).get("estimate") or {}
                c1 = len(charges)
                ok_real = client.post(base_url + "/voice-preview", json={"variant": "real", "lineId": "e2", "confirmUsd": 0})
                payload = (fal["requests"].get(f"req-{fal['posts']}") or {}).get("payload") or {}
                expect("v-d", [over.status_code, [est_real.get("usd"), est_real.get("priceKnown"), est_real.get("label")], ok_real.status_code,
                               [c["usd"] for c in charges[c1:]], (fal["requests"].get(f"req-{fal['posts']}") or {}).get("endpoint"), sorted(payload)],
                       [200, [None, False, "가격 확인 불가"], 200, [0.0], voices.MODEL_LITE, ["source_audio_url", "target_voice_audio_url"]],
                       "단가를 모르면 '가격 확인 불가'·confirmUsd 0 으로 진행·원장 0 USD 1건(실사만 덮어쓴 Chatterbox)")
            except Exception as exc:  # noqa: BLE001
                crash("v-d", exc)

            doc_after_preview = json.dumps(env.store.read_json(doc_key)[0], ensure_ascii=False, sort_keys=True)

            # ================= (v-e~j) 전체 반영 =================
            apply_url = base_url + "/voice-apply"
            out_key = ""
            try:
                dry = client.post(apply_url, json={"variant": "webtoon", "speakers": [SPK_A, SPK_B], "dryRun": True})
                est = (dry.get_json() or {}).get("estimate") or {}
                want = round(sum((min(8.0, e + 0.15) - max(0.0, s - 0.15)) * (0.01 if i % 2 == 0 else 0.3 / 60)
                                 for i, (s, e) in enumerate(LINES)), 2)
                posts0, charges0 = fal["posts"], len(charges)
                expect("v-e", [dry.status_code, est.get("usd"), est.get("priceKnown"), [l["lineId"] for l in est.get("lines") or []],
                               err_of(client.post(apply_url, json={"variant": "webtoon", "speakers": [SPK_A, SPK_B], "confirmUsd": 1.23})),
                               err_of(client.post(apply_url, json={"variant": "webtoon", "speakers": ["없는사람"], "confirmUsd": want})),
                               err_of(client.post(apply_url, json={"variant": "webtoon", "speakers": [], "confirmUsd": want})),
                               err_of(client.post(apply_url, json={"variant": "webtoon", "speakers": [SPK_A]})), fal["posts"] - posts0],
                       [200, want, True, ["e1", "e2", "e3", "e4", "e5", "e6"], [409, "CostMismatch"], [400, "UnknownSpeaker"],
                        [400, "BadSpeakers"], [400, "NoConfirmUsd"], 0],
                       "견적 = 줄별 합(초·분 단가)·금액 다름 409·모르는 인물·빈 목록·확인 금액 없음 400·fal 0회")
                fal["reject"] = {fal["posts"] + 4}  # 4번째 줄(e4, 준호) 결과를 fal 이 거절
                renders.clear()
                resp = client.post(apply_url, json={"variant": "webtoon", "speakers": [SPK_A, SPK_B], "confirmUsd": want})
                doc = env.store.read_json(doc_key)[0] or {}
                job = doc.get("job") or {}
                versions = doc.get("versions") or []
                summary = (versions[-1] if versions else {}).get("voices") or {}
                out_key = (versions[-1] if versions else {}).get("key") or ""
                new_charges = charges[charges0:]
                endpoints = [fal["requests"][f"req-{n}"]["endpoint"] for n in range(posts0 + 1, fal["posts"] + 1)]
                b_payload = fal["requests"][f"req-{posts0 + 2}"]["payload"]
                expect("v-e", [resp.status_code, job.get("status"), job.get("error"), fal["posts"] - posts0, len(new_charges),
                               len({c["key"] for c in new_charges}), all(c["key"].startswith("vnew-voice-req-") for c in new_charges),
                               round(sum(c["usd"] for c in new_charges), 4), endpoints,
                               sorted(b_payload), [b_payload.get("voice"), isinstance(b_payload.get("seed"), int)]],
                       [202, "done", "", 6, 6, 6, True, round(sum((min(8.0, e + 0.15) - max(0.0, s - 0.15)) * (0.01 if i % 2 == 0 else 0.3 / 60)
                                                                  for i, (s, e) in enumerate(LINES)), 4),
                        [voices.MODEL_HD, voices.MODEL_ELEVEN] * 3,
                        ["audio_url", "output_format", "remove_background_noise", "seed", "voice"], ["Roger", True]],
                       "반영: 202·done·fal 6회(줄마다)·받은 요청마다 원장 1건(거절 줄 포함, 키 겹침 없음)·민지 HD·준호 ElevenLabs(Roger·seed)")
                out_path = os.path.join(work, "out.mp4")
                with open(out_path, "wb") as handle:
                    handle.write(env.store.blobs.get(out_key, b""))
                pcm_src, pcm_out = cv._snd_pcm(ffmpeg, src_path), cv._snd_pcm(ffmpeg, out_path)  # noqa: SLF001
                track = renders[-1]["track"] if renders and renders[-1]["track"] else b""
                t_rate, t_ch, t_samples = voices.read_wav(track) if track else (0, 0, array("h"))
                pcm_track = t_samples.tobytes()

                def region(pcm: bytes, line_no: int, inner: float = 0.0) -> bytes:
                    s, e = LINES[line_no - 1]
                    return pcm[int(round((s + inner) * RATE)) * 2:int(round((e - inner) * RATE)) * 2]

                expect("v-e", [region(pcm_out, 4) == region(pcm_src, 4), len(region(pcm_src, 4)) > 0,
                               [region(pcm_track, n, 0.03) != region(pcm_src, n, 0.03) for n in (1, 2, 3, 5, 6)]],
                       [True, True, [True] * 5], "거절된 줄(e4)은 원래 소리 그대로(PCM 같음)·나머지 5줄은 바뀜")

                # ---- (v-f) 길이 ----
                d_src, d_out = stitchmod.probe_media(src_path)["duration"], stitchmod.probe_media(out_path)["duration"]
                lines_after = [(l["id"], l["start"], l["end"]) for l in doc.get("lines") or []]
                expect("v-f", [abs(d_src - d_out) <= 1 / 24 + 1e-6, len(pcm_out) == len(pcm_src), len(pcm_track) == len(pcm_src),
                               [t_rate, t_ch], lines_after == lines_before, summary.get("fadeSeconds"), summary.get("padSeconds")],
                       [True, True, True, [RATE, 1], True, 0.02, 0.15],
                       f"결과 길이 = 원본 ±1프레임({d_src:.3f} / {d_out:.3f})·샘플 수 같음·바꾼 트랙 길이 같음·줄 구간 그대로·20ms 페이드")
                s0, s1 = int(round(LINES[0][0] * RATE)), int(round(LINES[0][1] * RATE))
                fade = int(round(0.02 * RATE))
                src_arr = array("h")
                src_arr.frombytes(pcm_src)
                expect("v-f", [t_samples[s0 - 5:s0].tolist() == src_arr[s0 - 5:s0].tolist(), t_samples[s1:s1 + 5].tolist() == src_arr[s1:s1 + 5].tolist(),
                               t_samples[s0:s0 + fade].tolist() != src_arr[s0:s0 + fade].tolist()],
                       [True, True, True], "e1 경계: 구간 밖 샘플 그대로·페이드 안은 바뀐 값(섞음 비율은 v-a)")

                # ---- (v-g) 소리 크기 ----
                diffs = {}
                for n in (1, 2, 3, 6):
                    a, b = array("h"), array("h")
                    a.frombytes(region(pcm_src, n))
                    b.frombytes(region(pcm_out, n))
                    diffs[n] = round(_lufs(ffmpeg, voices, RATE, b, work) - _lufs(ffmpeg, voices, RATE, a, work), 2)
                first_out = fake_out.get(f"req-{posts0 + 1}") or b""
                fr, _fc, fs = voices.read_wav(first_out) if first_out else (24000, 1, array("h"))
                ref_a = array("h")
                ref_a.frombytes(region(pcm_src, 1))
                raw_diff = _lufs(ffmpeg, voices, fr, fs, work) - _lufs(ffmpeg, voices, RATE, ref_a, work)
                expect("v-g", [all(abs(d) <= 1.5 for d in diffs.values()), abs(raw_diff) > 6],
                       [True, True], f"바꾼 줄 EBU R128 차 {diffs} ≤ 1.5 LU·변형(맞추지 않은 가짜 결과 {raw_diff:.1f} LU)은 잡힌다")

                # ---- (v-h) 속마음 ----
                e5 = LINES[4]
                expect("v-h", [renders[-1]["audio"] if renders else None, renders[-1]["spans"] if renders else None,
                               region(pcm_out, 5, 0.05) != region(pcm_track, 5, 0.05), region(pcm_out, 1) == region(pcm_track, 1),
                               region(pcm_out, 6) == region(pcm_track, 6), region(pcm_track, 5, 0.05) != region(pcm_src, 5, 0.05)],
                       [True, [[e5[0], e5[1]]], True, True, True, True],
                       "렌더 입력 = 바꾼 트랙·속마음 구간(e5)만 효과(트랙과 다름)·실제 말 구간은 트랙 그대로·e5 도 목소리 바뀜")

                # ---- (v-i) 새 버전 · 요약 ----
                view = sndmod.sound_view(owner, pid, "webtoon")
                vv = (view.get("versions") or [{}])[-1]
                msg = "5/6 줄 목소리 맞춤, 1줄 원음 유지"
                expect("v-i", [out_key == sndmod.version_key(owner, pid, "g1", "webtoon", len(versions)), out_key.endswith("/sound/webtoon/v1.mp4"),
                               [summary.get("converted"), summary.get("total"), summary.get("kept"), summary.get("keptIds"), summary.get("message")],
                               sorted(summary.get("speakers") or {}), (summary.get("speakers") or {}).get(SPK_B, {}).get("kept"),
                               job.get("message"), (view.get("job") or {}).get("message"), (vv.get("voices") or {}).get("message"),
                               versions[-1].get("thoughtIds"), cv._snd_video_md5(ffmpeg, src_path) == cv._snd_video_md5(ffmpeg, out_path)],  # noqa: SLF001
                       [True, True, [5, 6, 1, ["e4"], msg], sorted([SPK_A, SPK_B]), ["e4"], msg, msg, msg, ["e5"], True],
                       "새 v1.mp4·versions[].voices 요약(인물별 바꾼·원음 줄)·job·보기 message·영상 스트림 그대로(md5)")

                # ---- (v-j) 모델 노트 ----
                notes = [n for n in notesmod.read_notes(owner, pid, "g1") if n.get("stage") == "voice"]
                e4_notes = [n for n in notes if (n.get("target") or {}).get("retakeCell") == "voice:e4"]
                expect("v-j", [len(e4_notes) >= 1, (e4_notes[-1] if e4_notes else {}).get("model"), (e4_notes[-1] if e4_notes else {}).get("action"),
                               "rejected" in str((e4_notes[-1] if e4_notes else {}).get("providerMessage") or ""),
                               "https://" in json.dumps(notes, ensure_ascii=False)],
                       [True, voices.MODEL_ELEVEN, "gaveUp", True, False], "거절 줄 노트: stage voice·대상 voice:e4·모델·gaveUp·원문(주소 없음)")
            except Exception as exc:  # noqa: BLE001
                crash("v-e", exc)

            # ================= (v-k) 끔 =================
            try:
                os.environ["VNEW_SOUND_VOICES"] = "false"
                calls = project["calls"]
                off = [err_of(put({"speaker": SPK_A, "mode": "preset", "preset": "Aria"})),
                       err_of(client.post(base_url + "/voice-preview", json={"lineId": "e1", "confirmUsd": 0})),
                       err_of(client.post(apply_url, json={"speakers": [SPK_A], "confirmUsd": 0}))]
                off_view = sndmod.sound_view(owner, pid, "webtoon")
                os.environ["VNEW_SOUND_VOICES"] = "true"
                with cv._pin_vnew(VNEW_SOUND=False):  # noqa: SLF001
                    sound_off = err_of(put({"speaker": SPK_A, "mode": "preset", "preset": "Aria"}))
                expect("v-k", [off, project["calls"] == calls, "voices" in off_view, sound_off, "voices" in sndmod.sound_view(owner, pid, "webtoon")],
                       [[[404, "VoicesDisabled"]] * 3, True, False, [404, "SoundDisabled"], True],
                       "VNEW_SOUND_VOICES 끔 → 새 라우트 404(프로젝트 조회 0)·보기에 voices 없음, VNEW_SOUND 끔도 404")
            except Exception as exc:  # noqa: BLE001
                crash("v-k", exc)
            finally:
                os.environ["VNEW_SOUND_VOICES"] = "true"

            # ================= (v-l) 옛 문서 · 원본 · 키 · 로그 =================
            try:
                renders.clear()
                plain = sndmod.start_apply(owner, pid, {"variant": "webtoon"})
                doc = env.store.read_json(doc_key)[0] or {}
                last = (doc.get("versions") or [{}])[-1]
                view = sndmod.sound_view(owner, pid, "webtoon")
                expect("v-l", [doc_after_preview == doc_before, plain.get("job"), sorted(doc.get("job") or {}), sorted(last),
                               sorted((view.get("versions") or [{}])[-1]), sorted(view.get("job") or {}), renders[-1]["audio"] if renders else None],
                       [True, {"status": "running", "error": "", "at": plain.get("job", {}).get("at")}, ["at", "error", "n", "status"],
                        ["at", "key", "n", "sourceKey", "thoughtIds"], ["at", "currentSource", "n", "thoughtIds", "videoUrl"], ["at", "error", "status"], False],
                       "목소리 저장·들어보기는 사운드 문서 그대로·목소리 없는 반영은 옛 모양 그대로(job·versions·보기에 새 필드 없음)")
                audio_puts = [k for k, _b, t in env.store.puts if t == "audio/wav"]
                expect("v-l", [env.store.blobs.get(final_web) == src_bytes, env.store.blobs.get(final_real) == src_bytes,
                               [k for k, _b, _t in env.store.puts if k in (final_web, final_real)], len(audio_puts) > 0,
                               [k for k in audio_puts if not (k.startswith(f"{root}/sound/") and "/voices/" in k)]],
                       [True, True, [], True, []], "원본 영상 그대로(덮어쓰기 0)·소리 파일은 gen 사운드 폴더 voices/ 아래만")
                leaks = [l[:120] for l in tap.lines if re.search(r"https?://|data:audio|users/|/sound/|signed\.example", l)]
                expect("v-l", leaks, [], "로그에 주소·키·data URI 없음")
            except Exception as exc:  # noqa: BLE001
                crash("v-l", exc)

            # ================= (v-m) 나중 영상 =================
            try:
                data = env.plan(pid)
                g2_key = f"users/{owner}/video/{pid}/vnew/gen/g2/final-webtoon.mp4"
                env.store.blobs[g2_key] = src_bytes
                data["gen"] = {**data["gen"], "genId": "g2", "finalWebtoon": {"status": "done", "key": g2_key, "error": ""}}
                env.save_plan(pid, data)
                got = sndmod.sound_view(owner, pid, "webtoon").get("voices") or {}
                sp = {s["speaker"]: s for s in got.get("speakers") or []}
                real = {s["speaker"]: s for s in (sndmod.sound_view(owner, pid, "real").get("voices") or {}).get("speakers") or []}
                expect("v-m", [got.get("genId"), [sp.get(n, {}).get("status") for n in (SPK_A, SPK_B)],
                               ((sp.get(SPK_A, {}).get("profile") or {}).get("ref") or {}).get("genId"),
                               [real.get(SPK_A, {}).get("scope"), real.get(SPK_B, {}).get("scope"), (real.get(SPK_B, {}).get("profile") or {}).get("model")],
                               [real.get(n, {}).get("status") for n in (SPK_A, SPK_B)]],
                       ["g2", [voices.USING_SAVED_LABEL] * 2, "g1", ["shared", "variant", voices.MODEL_LITE], [voices.USING_SAVED_LABEL] * 2],
                       "새 gen(g2)·실사도 저장된 목소리 그대로(g1 기준 소리)·실사만 덮어쓴 준호는 variant")
            except Exception as exc:  # noqa: BLE001
                crash("v-m", exc)
    except Exception as exc:  # noqa: BLE001
        crash("v-e", exc)
    finally:
        vlog.removeHandler(tap)
        vlog.setLevel(old_level)
        if saved_env is None:
            os.environ.pop("VNEW_SOUND_VOICES", None)
        else:
            os.environ["VNEW_SOUND_VOICES"] = saved_env
        shutil.rmtree(work, ignore_errors=True)
    tmp_after = {n for n in os.listdir(tempfile.gettempdir()) if n.startswith(("vnew-voice-", "vnew-sound-"))}
    if tmp_after - tmp_before:
        bad["v-l"].append(f"임시 폴더가 남았습니다: {sorted(tmp_after - tmp_before)[:5]}")

    passed = True
    for group, title in GROUPS.items():
        if bad[group]:
            passed = False
            for item in bad[group]:
                print(f"[FAIL] 작업 202 ({group}) {item}"[:2000])
        elif group not in seen:
            passed = False
            print(f"[FAIL] 작업 202 ({group}) {title}: 검사하지 못했습니다")
        else:
            print(f"[ok  ] 작업 202 ({group}) {title}")
    return passed


if __name__ == "__main__":
    sys.exit(0 if check_voice_202() else 1)
