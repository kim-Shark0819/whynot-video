"""작업 224 검사: ⑥ 대사별 내려받기(줄 하나 mp3 · 여러 줄 zip). 메모리 저장소 · 가짜 영상, 유료 호출 · 네트워크 0.

  (ld-a) 구간: 앞 0.15초 · 뒤 0.25초 · [0, 길이] · 이웃 가운데에서 끊김(겹침 없음) · 이름 정리 · CSV · Content-Disposition
  (ld-b) GET 줄 목록: 사운드 문서 구간 그대로(+덧붙임) · 반영본 고르기(n) · 읽기만(저장소 그대로)
  (ld-c) 217 분리된 목소리: 켬 · 낡지 않음 → 목소리를 자름 · 낡음(크기 다름) → 영상 소리
  (ld-d) zip(가짜 ffmpeg): 이름 NN_말하는사람_대사 · 대사목록.csv(BOM) · README.txt · 한 줄 → mp3 하나 · 오류 문구
  (ld-e) 캐시: 같은 요청은 다시 만들지 않음(올리기 0) · 있는 키를 덮어쓰지 않음 · 다른 키·문서·대본 그대로
  (ld-f) 임시 파일: 성공 · 실패 모두 지움
  (ld-g) 라우트: 남의 프로젝트 404 · VNEW_SOUND 끔 404 · 틀린 그림체 400 · 소유자 200
  (ld-h) 진짜 ffmpeg: mp3 48kHz · 스테레오/모노 원본 그대로 · 길이 = 구간(ffmpeg 없으면 WARN)
  (ld-i) 화면(node): 단추 · 반영 중 막힘 · 두 번 요청 없음 · 오류 문구 · 훅 줄(node 없으면 WARN)
  (ld-j) LF 줄 끝

실행: python -X utf8 -B tools/check_line_download_224.py
check_vpoc 훅 줄(직접 넣지 않는다):
    if not __import__("check_line_download_224").check_line_download_224(): fail("작업 224: ⑥ 대사별 내려받기 검사 실패")
"""

from __future__ import annotations

import copy
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
import zipfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
for _path in (str(TOOLS), str(ROOT / "app")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

GROUPS = {
    "ld-a": "구간 덧붙임 · 겹침 없음 · 이름 · CSV · 헤더",
    "ld-b": "GET 줄 목록 · 반영본 고르기 · 읽기만",
    "ld-c": "분리된 목소리(켬 → 목소리 · 낡음 → 영상 소리)",
    "ld-d": "zip 이름 · CSV · README · 한 줄 mp3 · 오류 문구",
    "ld-e": "캐시 다시 쓰기 · 덮어쓰기 없음",
    "ld-f": "임시 파일 지움(성공 · 실패)",
    "ld-g": "라우트 권한 · 플래그 · 그림체",
    "ld-h": "진짜 ffmpeg mp3",
    "ld-i": "화면 단추(node)",
    "ld-j": "LF 줄 끝",
}
PUBLIC = ROOT / "frontend" / "public"
FILES = [ROOT / "app/src/vnew/sound.py", ROOT / "app/src/vnew/routes.py", PUBLIC / "vnew.js", PUBLIC / "vnew.css", Path(__file__).resolve()]
JS_START, JS_END = "/* ==== 작업 224 시작 ==== */", "/* ==== 작업 224 끝 ==== */"
JS_HOOKS = (
    'if (typeof sldZipButton === "function") sldZipButton(ctx, tools); // 작업 224',
    "sldZipButton(ctx, srcTools);",
    'if (typeof sldRowButton === "function") sldRowButton(ctx, line, listenCell); // 작업 224',
    'if (typeof sldPaint === "function") sldPaint(ctx); // 작업 224',
    "ctx.sldRows = []; // 작업 224",
    "lrdRowTools(ctx, line, row, lineCell, listenCell); // 작업 212",
)


def _ffmpeg_bin() -> str:
    try:
        from src import stitcher  # noqa: PLC0415

        return stitcher.ffmpeg_path()
    except Exception:  # noqa: BLE001
        return ""


def _media_info(binary: str, path: str) -> dict:
    out = subprocess.run([binary, "-hide_banner", "-i", path], capture_output=True).stderr.decode("utf-8", "replace")
    dur = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", out)
    audio = re.search(r"Audio: (\w+).*?(\d+) Hz, (mono|stereo)", out)
    seconds = int(dur.group(1)) * 3600 + int(dur.group(2)) * 60 + float(dur.group(3)) if dur else 0.0
    return {"duration": seconds, "codec": audio.group(1) if audio else "", "rate": int(audio.group(2)) if audio else 0,
            "layout": audio.group(3) if audio else ""}


def check_line_download_224() -> bool:  # noqa: C901, PLR0915 — 한 검사 함수(다른 작업 검사와 같은 모양)
    bad: dict[str, list[str]] = {g: [] for g in GROUPS}
    seen: set[str] = set()
    warns: list[str] = []

    def expect(group: str, got, want, what: str) -> None:
        seen.add(group)
        if got != want:
            bad[group].append(f"{what}\n      결과 {str(got)[:500]}\n      기대 {str(want)[:500]}")

    def crash(group: str, exc: BaseException) -> None:
        seen.add(group)
        bad[group].append("예외: " + repr(exc)[:300] + "\n" + traceback.format_exc()[-1200:])

    import logging  # noqa: PLC0415

    vlog = logging.getLogger("vpoc")
    saved_level = vlog.level
    vlog.setLevel(logging.CRITICAL)  # 일부러 낸 거절 · 실패 로그는 숨긴다
    try:
        return _check(expect, crash, bad, seen, warns)
    finally:
        vlog.setLevel(saved_level)


def _check(expect, crash, bad, seen, warns) -> bool:  # noqa: C901, PLR0915
    import check_line_redo_212 as fx  # noqa: PLC0415 — 212 고정물(메모리 저장소 · 가짜 영상 · 대본)

    from src import stitcher  # noqa: PLC0415
    from src.vnew import audio_separate as sep  # noqa: PLC0415
    from src.vnew import edit as editmod  # noqa: PLC0415
    from src.vnew import generate as gm  # noqa: PLC0415
    from src.vnew import line_redo as lr  # noqa: PLC0415
    from src.vnew import plan as planmod  # noqa: PLC0415
    from src.vnew import settings as vset  # noqa: PLC0415
    from src.vnew import sound as snd  # noqa: PLC0415
    from src.vpoc import settings as vpoc_set  # noqa: PLC0415
    from src.vpoc.errors import VpocError  # noqa: PLC0415

    def err_of(fn) -> list:
        try:
            fn()
            return ["통과"]
        except VpocError as exc:
            return [exc.kind, exc.status, exc.message]

    # ================= (ld-a) 순수 함수 =================
    try:
        spans = snd.padded_spans([(0.5, 1.5), (2.5, 3.4), (0.05, 0.3), (3.5, 3.9)], 4.0)
        expect("ld-a", spans, [(0.4, 1.75), (2.35, 3.45), (0.0, 0.4), (3.45, 4.0)],
               "앞 0.15 · 뒤 0.25 · 0 과 길이에서 자름 · 이웃 가운데(0.4 · 3.45)에서 끊음 · 순서 그대로")
        ordered = sorted(spans)
        expect("ld-a", all(a[1] <= b[0] + 1e-9 for a, b in zip(ordered, ordered[1:])), True, "줄끼리 겹치지 않음")
        tight = snd.padded_spans([(1.0, 2.0), (1.9, 3.0)], 3.0)
        expect("ld-a", [tight, tight[0][1] <= tight[1][0]], [[(0.85, 1.95), (1.95, 3.0)], True], "원래 겹친 줄도 가운데에서 끊음")
        expect("ld-a", [snd.line_file_name(1, "민지", "안녕"), snd.line_file_name(3, "A/B", 'he said: "안녕하세요 여러분 반갑습니다"'),
                        snd.line_file_name(12, "", "...?", 3), snd.lines_zip_name("webtoon", 2), snd.lines_zip_name("real", 0)],
               ["01_민지_안녕.mp3", "03_AB_he_said_안녕하세요_여러.mp3", "012.mp3", "대사별-anime-v2.zip", "대사별-real-v0.zip"],
               "이름 NN_말하는 사람_대사 앞 16글자(금지 글자 · 빈칸 정리, 한국어 그대로) · zip 이름")
        disp = snd.lines_disposition("대사별-anime-v2.zip", "lines-anime-v2.zip")
        expect("ld-a", disp, "attachment; filename=\"lines-anime-v2.zip\"; filename*=UTF-8''%EB%8C%80%EC%82%AC%EB%B3%84-anime-v2.zip",
               "Content-Disposition: ASCII 이름 + filename*=UTF-8''(한국어)")
        body = snd.lines_csv([{"idx": 1, "speaker": "민지", "start": 0.4, "end": 1.75, "textKo": "안녕, \"너\""}])
        expect("ld-a", [body[:3], body.decode("utf-8-sig").splitlines()],
               [b"\xef\xbb\xbf", ["번호,말하는 사람,시작(초),끝(초),대사", '1,민지,0.400,1.750,"안녕, ""너"""']], "CSV: UTF-8 BOM · 머리줄 · 따옴표")
    except Exception as exc:  # noqa: BLE001
        crash("ld-a", exc)

    # ================= 서버 고정물 =================
    class Store(fx._Store):  # noqa: SLF001
        def list_keys(self, prefix: str, *, bucket: str = "") -> list[dict]:
            return [{"key": k, "size": len(v)} for k, v in self.blobs.items() if k.startswith(prefix)]

    store = Store()
    OWNER, PID = fx.OWNER, fx.PID
    calls: dict[str, list] = {"upload": [], "ffmpeg": [], "download": []}
    saved_attrs: list = []

    def setv(obj, name, value):
        saved_attrs.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def fake_download(key, path):
        calls["download"].append(key)
        with open(path, "wb") as handle:
            handle.write(store.blobs[key])
        return len(store.blobs[key])

    real_ffmpeg = snd._lines_ffmpeg  # noqa: SLF001

    def count_upload(path, key, content_type):
        calls["upload"].append((key, content_type))
        with open(path, "rb") as handle:
            store.blobs[key] = handle.read()

    def fake_ffmpeg(args, what):
        calls["ffmpeg"].append(list(args))
        out = args[-1]
        with open(out, "wb") as handle:
            if out.endswith(".wav"):
                handle.write(b"WAV")
            else:
                ss = args[args.index("-ss") + 1]
                handle.write(f"MP3 ss={ss} t={args[args.index('-t') + 1]}".encode())

    def probe_any(path):
        with open(path, "rb") as handle:
            head = handle.read(5)
        return fx._probe_bytes(path) if head == b"FAKE:" else stitcher.probe_media(path)  # noqa: SLF001

    for mod in (editmod, planmod, gm):
        setv(mod, "STORE", store)
    setv(vpoc_set, "VIDEO_BUCKET", "ld-video")
    setv(vset, "VNEW_SOUND", True)
    setv(lr, "ENABLED", False)
    setv(snd, "_download", fake_download)
    setv(snd, "_probe", probe_any)
    setv(snd, "_detect_speech", lambda path, duration: [])
    setv(snd, "_lines_upload", count_upload)
    setv(snd, "_lines_ffmpeg", fake_ffmpeg)
    setv(sep, "enabled", lambda: True)
    final_key = fx._key("final-webtoon.mp4")  # noqa: SLF001
    v1_key = fx._key("sound/webtoon/v1.mp4")  # noqa: SLF001
    doc_key = snd.doc_key(OWNER, PID, "g1", "webtoon")
    lines_pre = snd.lines_prefix(OWNER, PID, "g1") + "/"
    tmp_root = tempfile.mkdtemp(prefix="check224-")
    saved_tempdir = tempfile.tempdir

    def key_of(res: dict) -> str:
        return str(res.get("url") or "").replace("https://signed.example/", "")

    def snapshot() -> str:
        blobs = {k: v for k, v in store.blobs.items() if not k.startswith(lines_pre)}
        return json.dumps([sorted((k, v[0]) for k, v in store.json.items()), sorted((k, v.hex()) for k, v in blobs.items())],
                          ensure_ascii=False, sort_keys=True, default=str)

    try:
        store.blobs.update({final_key: fx._fake(4.0), v1_key: fx._fake(4.0)})  # noqa: SLF001
        planmod.save_plan(OWNER, PID, fx._sound_plan())  # noqa: SLF001
        snd.sound_view(OWNER, PID, "webtoon")  # ⑥ 화면이 문서를 만든다(설정 단계)
        doc, etag = store.json[doc_key]
        doc["lines"][0].update(start=0.5, end=1.5, auto=False)
        doc["lines"][1].update(start=2.5, end=3.4, auto=False)
        doc["versions"] = [{"n": 1, "key": v1_key, "at": "2026-11-11T00:00:00Z", "thoughtIds": ["e2"], "sourceKey": final_key}]
        store.json[doc_key] = (doc, etag)
        tempfile.tempdir = tmp_root

        # ================= (ld-b) GET =================
        try:
            before = snapshot()
            view = snd.lines_view(OWNER, PID, {"variant": "webtoon"})
            want_lines = [{"idx": 1, "lineId": "e1", "speaker": "민지", "textKo": "안녕", "start": 0.35, "end": 1.75},
                          {"idx": 2, "lineId": "e2", "speaker": "준호", "textKo": "설마…", "start": 2.35, "end": 3.65}]
            expect("ld-b", [view["variant"], view["n"], view["voiceStem"], view["lines"]], ["webtoon", 1, False, want_lines],
                   "줄 구간 = 사운드 문서 start·end(212 · 202 와 같은 값) + 덧붙임 · n 없음 = 최신 반영본")
            v0 = snd.lines_view(OWNER, PID, {"variant": "webtoon", "n": "0"})
            expect("ld-b", [v0["n"], err_of(lambda: snd.lines_view(OWNER, PID, {"variant": "webtoon", "n": "9"}))[:2],
                            err_of(lambda: snd.lines_view(OWNER, PID, {"variant": "webtoon", "n": "x"}))[:2],
                            err_of(lambda: snd.lines_view(OWNER, PID, {"variant": "anime"}))[:2]],
                   [0, ["NoSoundVersion", 404], ["BadVersion", 400], ["BadVariant", 400]], "n=0 원본 · 없는 반영본 404 · 틀린 n · 그림체 400")
            saved_doc = store.json.pop(doc_key)
            fresh = snd.lines_view(OWNER, PID, {"variant": "webtoon"})
            expect("ld-b", [[(l["lineId"], l["start"], l["end"]) for l in fresh["lines"]], fresh["n"], doc_key in store.json],
                   [[("e1", 0.0, 2.0), ("e2", 2.0, 4.0)], 0, False], "문서가 없으면 같은 구간 추정(estimate_spans)을 메모리에서만 · 저장 안 함")
            store.json[doc_key] = saved_doc
            expect("ld-b", snapshot() == before, True, "GET 은 읽기만(문서 · 대본 · 원본 그대로)")
        except Exception as exc:  # noqa: BLE001
            crash("ld-b", exc)

        # ================= (ld-d) zip · 한 줄 =================
        try:
            before = snapshot()
            res = snd.download_lines(OWNER, PID, {"variant": "webtoon"})
            key = key_of(res)
            with zipfile.ZipFile(io.BytesIO(store.blobs[key])) as archive:
                names = archive.namelist()
                csv_text = archive.read("대사목록.csv")
                readme = archive.read("README.txt").decode("utf-8")
                first = archive.read(names[0]).decode()
            expect("ld-d", [res["kind"], res["filename"], res["count"], res["cached"], key.startswith(lines_pre + "webtoon-v1-"),
                            key.endswith(".zip"), calls["download"][-1], calls["upload"][-1][1]],
                   ["zip", "대사별-anime-v1.zip", 2, False, True, True, v1_key, "application/zip"],
                   "모든 줄 = zip(대사별-anime-v<n>.zip) · 새 키 sound/lines/<variant>-v<n>-<sha8>.zip · 고른 반영본을 자름")
            expect("ld-d", [names, csv_text[:3], csv_text.decode("utf-8-sig").splitlines(), "대사별" in readme, first],
                   [["01_민지_안녕.mp3", "02_준호_설마….mp3", "대사목록.csv", "README.txt"], b"\xef\xbb\xbf",
                    ["번호,말하는 사람,시작(초),끝(초),대사", "1,민지,0.350,1.750,안녕", "2,준호,2.350,3.650,설마…"], True,
                    "MP3 ss=0.350 t=1.400"], "zip 안: 줄마다 mp3 · 대사목록.csv(BOM) · README.txt · 구간대로 자름")
            wav_calls = [c for c in calls["ffmpeg"] if c[-1].endswith(".wav")]
            expect("ld-d", [len(wav_calls), wav_calls[-1][wav_calls[-1].index("-ar") + 1], "-ac" in wav_calls[-1],
                            calls["ffmpeg"][-1][calls["ffmpeg"][-1].index("-b:a") + 1]], [1, "48000", False, "192k"],
                   "원본 소리는 wav 로 한 번 · 48kHz · 채널 그대로 · 192kbps")
            one = snd.download_lines(OWNER, PID, {"variant": "webtoon", "n": 1, "lineIds": ["e2"]})
            expect("ld-d", [one["kind"], one["filename"], key_of(one).endswith(".mp3"), store.blobs[key_of(one)].decode(),
                            calls["upload"][-1][1]], ["mp3", "02_준호_설마….mp3", True, "MP3 ss=2.350 t=1.300", "audio/mpeg"],
                   "한 줄 = mp3 하나(이름 NN_말하는 사람_대사)")
            two = snd.download_lines(OWNER, PID, {"variant": "webtoon", "n": 0, "lineIds": ["e1", "e2"]})
            expect("ld-d", [two["kind"], two["filename"], calls["download"][-1]], ["zip", "대사별-anime-v0.zip", final_key],
                   "여러 줄 = zip · n=0 원본")
            expect("ld-d", [err_of(lambda: snd.download_lines(OWNER, PID, {"variant": "webtoon", "lineIds": []})),
                            err_of(lambda: snd.download_lines(OWNER, PID, {"variant": "webtoon", "lineIds": ["zz"]}))[:2],
                            err_of(lambda: snd.download_lines(OWNER, PID, {"variant": "webtoon", "lineIds": "e1"}))[:2]],
                   [["NoLines", 400, "내려받을 대사가 없습니다."], ["LinesBadLine", 400], ["LinesBadLines", 400]], "고른 줄 없음 · 모르는 줄 · 틀린 꼴")
            expect("ld-d", snapshot() == before, True, "내려받기는 새 키에만 쓴다(문서 · 대본 · 원본 · 반영본 그대로)")
            plan_none = fx._sound_plan()  # noqa: SLF001
            plan_none["gen"]["finalWebtoon"] = {"status": "failed", "key": "", "error": "x"}
            planmod.save_plan(OWNER, PID, plan_none)
            expect("ld-d", [err_of(lambda: snd.download_lines(OWNER, PID, {"variant": "webtoon"})),
                            err_of(lambda: snd.lines_view(OWNER, PID, {"variant": "webtoon"}))[:2]],
                   [["LinesNoSource", 409, "소리 원본을 찾지 못했습니다. 영상을 먼저 만들어 주세요."], ["LinesNoSource", 409]],
                   "원본 영상이 없으면 409 · 한국어 문구")
            planmod.save_plan(OWNER, PID, fx._sound_plan())  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            crash("ld-d", exc)

        # ================= (ld-e) 캐시 · 덮어쓰기 없음 =================
        try:
            ups, ffs = len(calls["upload"]), len(calls["ffmpeg"])
            again = snd.download_lines(OWNER, PID, {"variant": "webtoon"})
            expect("ld-e", [again["cached"], len(calls["upload"]) - ups, len(calls["ffmpeg"]) - ffs, again["filename"]],
                   [True, 0, 0, "대사별-anime-v1.zip"], "같은 원본 · 구간이면 있는 파일을 쓴다(만들기 · 올리기 0)")
            store.blobs[key_of(again)] = b"OLD"
            third = snd.download_lines(OWNER, PID, {"variant": "webtoon"})
            expect("ld-e", [third["cached"], store.blobs[key_of(third)], len(calls["upload"]) - ups], [True, b"OLD", 0],
                   "그 키에 무엇이 있든 덮어쓰지 않는다")
            doc2, etag2 = store.json[doc_key]
            moved = copy.deepcopy(doc2)
            moved["lines"][0]["end"] = 1.6
            store.json[doc_key] = (moved, etag2)
            fourth = snd.download_lines(OWNER, PID, {"variant": "webtoon"})
            expect("ld-e", [fourth["cached"], key_of(fourth) != key_of(again), len(calls["upload"]) - ups], [False, True, 1],
                   "줄 구간이 바뀌면 sha8 이 달라 새 키에 새로 만든다")
            store.json[doc_key] = (doc2, etag2)
        except Exception as exc:  # noqa: BLE001
            crash("ld-e", exc)

        # ================= (ld-c) 분리된 목소리 =================
        try:
            voice = sep.voice_key(v1_key)
            store.blobs[voice] = fx._fake(4.0)  # noqa: SLF001
            state = {"status": "done", "useVoice": True, "voiceKey": voice, "sourceSize": len(store.blobs[v1_key]), "sourceDuration": 4.0}
            store.write_json(sep.state_key(v1_key), state)
            res = snd.download_lines(OWNER, PID, {"variant": "webtoon", "lineIds": ["e1"]})
            view = snd.lines_view(OWNER, PID, {"variant": "webtoon"})
            expect("ld-c", [res["voiceStem"], view["voiceStem"], calls["download"][-1], res["cached"]], [True, True, voice, False],
                   "그 원본(반영본 키 그대로)에 분리된 목소리가 켜져 있으면 목소리를 자른다")
            stale = dict(state, sourceSize=len(store.blobs[v1_key]) + 7)
            store.write_json(sep.state_key(v1_key), stale)
            res2 = snd.download_lines(OWNER, PID, {"variant": "webtoon", "lineIds": ["e1"]})
            expect("ld-c", [res2["voiceStem"], calls["download"][-1], key_of(res2) != key_of(res)], [False, v1_key, True],
                   "분리 뒤 원본이 바뀌었으면(크기 다름 — 217 낡음 검사) 목소리를 쓰지 않고 영상 소리")
            store.write_json(sep.state_key(v1_key), dict(state, useVoice=False))
            expect("ld-c", snd.download_lines(OWNER, PID, {"variant": "webtoon", "lineIds": ["e1"]})["voiceStem"], False,
                   "[분리된 목소리 쓰기] 끔 → 영상 소리")
            res0 = snd.download_lines(OWNER, PID, {"variant": "webtoon", "n": 0, "lineIds": ["e1"]})
            expect("ld-c", [res0["voiceStem"], calls["download"][-1]], [False, final_key], "다른 원본(n=0)에는 그 원본의 분리 기록만 본다")
        except Exception as exc:  # noqa: BLE001
            crash("ld-c", exc)

        # ================= (ld-f) 임시 파일 =================
        try:
            expect("ld-f", os.listdir(tmp_root), [], "성공한 뒤 임시 파일 없음")
            ups = len(calls["upload"])

            def broken(args, what):
                fake_ffmpeg(args, what)
                if not args[-1].endswith(".wav"):
                    raise stitcher.StitchError("boom")

            snd._lines_ffmpeg = broken  # noqa: SLF001
            got = err_of(lambda: snd.download_lines(OWNER, PID, {"variant": "webtoon", "n": 0, "lineIds": ["e2"]}))
            snd._lines_ffmpeg = fake_ffmpeg  # noqa: SLF001
            expect("ld-f", [got[:2], os.listdir(tmp_root), len(calls["upload"]) - ups], [["LinesFailed", 500], [], 0],
                   "ffmpeg 실패: 500 LinesFailed · 임시 파일 지움 · 올리지 않음")
            hidden = store.blobs.pop(final_key)
            got2 = err_of(lambda: snd.download_lines(OWNER, PID, {"variant": "webtoon", "n": 0, "lineIds": ["e1", "e2"]}))
            store.blobs[final_key] = hidden
            expect("ld-f", [got2, os.listdir(tmp_root)],
                   [["LinesNoSource", 409, snd.LINES_NO_SOURCE], []], "원본 파일을 받지 못하면 409 한국어 문구 · 임시 파일 지움")
        except Exception as exc:  # noqa: BLE001
            crash("ld-f", exc)

        # ================= (ld-g) 라우트 =================
        try:
            from flask import Flask  # noqa: PLC0415

            from src import vnew  # noqa: PLC0415

            perm = {"write": True}
            app = Flask(__name__)
            app.register_blueprint(vnew.create_blueprint(vnew.VnewDependencies(
                require_auth=lambda handler: handler, current_owner=lambda: OWNER,
                get_project=lambda pid: {"id": pid, "owner": OWNER, "track": "video_new"},
                can_read_project=lambda proj: True, can_write_project=lambda proj: perm["write"], project_owner_prefix=lambda n: n)))
            client = app.test_client()
            base = f"/api/vnew/p/{PID}/sound/lines"

            def hit():
                a = client.get(base + "?variant=webtoon")
                b = client.post(base + "/download", json={"variant": "webtoon", "lineIds": ["e1"]})
                return [a.status_code, b.status_code]

            perm["write"] = False
            expect("ld-g", hit(), [404, 404], "남의 프로젝트(쓰기 권한 없음) → 404")
            perm["write"] = True
            vset.VNEW_SOUND = False
            expect("ld-g", hit(), [404, 404], "VNEW_SOUND 끔 → 404")
            vset.VNEW_SOUND = True
            expect("ld-g", hit(), [200, 200], "소유자 → 200")
            got = client.get(base + "?variant=webtoon").get_json() or {}
            post = client.post(base + "/download", json={"variant": "webtoon", "lineIds": ["e1"]}).get_json() or {}
            badv = client.post(base + "/download", json={"variant": "anime"})
            nol = client.post(base + "/download", json={"variant": "webtoon", "lineIds": []})
            expect("ld-g", [len(got.get("lines") or []), post.get("ok"), post.get("kind"), str(post.get("url", "")).startswith("https://"),
                            badv.status_code, nol.status_code, ((nol.get_json() or {}).get("error") or {}).get("type")],
                   [2, True, "mp3", True, 400, 400, "NoLines"], "GET 줄 목록 · POST 주소 · 틀린 그림체 400 · 고른 줄 없음 400")
        except Exception as exc:  # noqa: BLE001
            crash("ld-g", exc)

        # ================= (ld-h) 진짜 ffmpeg =================
        binary = _ffmpeg_bin()
        if not binary:
            seen.add("ld-h")
            warns.append("(ld-h) ffmpeg 없음 — 진짜 mp3 검사를 건너뜀")
        else:
            try:
                snd._lines_ffmpeg = real_ffmpeg  # noqa: SLF001
                made = {}
                for layout in ("stereo", "mono"):
                    path = os.path.join(tmp_root, f"src-{layout}.m4a")
                    subprocess.run([binary, "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                                    "sine=frequency=440:sample_rate=44100:duration=4", "-ac", "2" if layout == "stereo" else "1",
                                    "-c:a", "aac", path], check=True, capture_output=True)
                    with open(path, "rb") as handle:
                        made[layout] = handle.read()
                    os.remove(path)
                saved_final = store.blobs[final_key]
                results = []
                for layout in ("stereo", "mono"):
                    store.blobs[final_key] = made[layout]
                    res = snd.download_lines(OWNER, PID, {"variant": "webtoon", "n": 0})
                    with zipfile.ZipFile(io.BytesIO(store.blobs[key_of(res)])) as archive:
                        out_dir = os.path.join(tmp_root, "zip-" + layout)
                        archive.extractall(out_dir)
                        infos = [_media_info(binary, os.path.join(out_dir, n)) for n in archive.namelist() if n.endswith(".mp3")]
                    shutil.rmtree(out_dir)
                    results.append([[i["codec"], i["rate"], i["layout"], abs(i["duration"] - want) < 0.12]
                                    for i, want in zip(infos, (1.4, 1.3))])
                one = snd.download_lines(OWNER, PID, {"variant": "webtoon", "n": 0, "lineIds": ["e1"]})
                path = os.path.join(tmp_root, "one.mp3")
                with open(path, "wb") as handle:
                    handle.write(store.blobs[key_of(one)])
                single = _media_info(binary, path)
                os.remove(path)
                store.blobs[final_key] = saved_final
                expect("ld-h", results, [[["mp3", 48000, "stereo", True]] * 2, [["mp3", 48000, "mono", True]] * 2],
                       "진짜 mp3: 48kHz · 원본 채널 그대로 · 길이 = 덧붙인 구간")
                expect("ld-h", [single["codec"], single["rate"], single["layout"], one["kind"]], ["mp3", 48000, "mono", "mp3"],
                       "한 줄 = 진짜 mp3 하나")
            except Exception as exc:  # noqa: BLE001
                crash("ld-h", exc)
            finally:
                snd._lines_ffmpeg = fake_ffmpeg  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        crash("ld-b", exc)
    finally:
        tempfile.tempdir = saved_tempdir
        for obj, name, value in reversed(saved_attrs):
            setattr(obj, name, value)
        shutil.rmtree(tmp_root, ignore_errors=True)

    # ================= (ld-i) 화면 =================
    try:
        src = (PUBLIC / "vnew.js").read_text(encoding="utf-8")
        expect("ld-i", [src.count(JS_START), src.count(JS_END)] + [src.count(h) for h in JS_HOOKS], [1, 1] + [1] * len(JS_HOOKS),
               "224 구역 1개 · 훅 줄(반영본 옆 zip 단추 · 원본 zip 단추 · 줄 단추 · 반영 중 막기) · 212 줄 도구 그대로")
        node = shutil.which("node") or ""
        if not node:
            warns.append("(ld-i) node 없음 — 화면 하네스를 건너뜀")
        else:
            block = src[src.index(JS_START):src.index(JS_END) + len(JS_END)]
            with tempfile.TemporaryDirectory(prefix="check224-node-") as work:
                script = os.path.join(work, "h.js")
                with open(script, "w", encoding="utf-8", newline="\n") as handle:
                    handle.write(_HARNESS_HEAD + block + _HARNESS_TAIL)
                run = subprocess.run([node, script], capture_output=True, timeout=60)
            out = run.stdout.decode("utf-8", "replace").strip().splitlines()
            got = json.loads(out[-1]) if run.returncode == 0 and out else {"err": run.stderr.decode("utf-8", "replace")[-800:]}
            expect("ld-i", got, _HARNESS_WANT, "단추 글 · aria-label · 반영 중 막힘 · 만드는 중… · 두 번 요청 없음 · 숨긴 <a download> · 오류 문구")
    except Exception as exc:  # noqa: BLE001
        crash("ld-i", exc)

    # ================= (ld-j) LF =================
    try:
        expect("ld-j", [p.name for p in FILES if b"\r\n" in p.read_bytes()], [], "새·고친 파일 LF 줄 끝")
    except Exception as exc:  # noqa: BLE001
        crash("ld-j", exc)

    passed = True
    for group, title in GROUPS.items():
        if bad[group]:
            passed = False
            for item in bad[group]:
                print(f"[FAIL] 작업 224 ({group}) {item}"[:1500])
        elif group not in seen:
            passed = False
            print(f"[FAIL] 작업 224 ({group}) {title}: 검사하지 못했습니다")
        else:
            print(f"[ok  ] 작업 224 ({group}) {title}")
    for item in warns:
        print(f"[WARN] 작업 224 {item}")
    print('[hook] check_vpoc.main(): if not __import__("check_line_download_224").check_line_download_224(): '
          'fail("작업 224: ⑥ 대사별 내려받기 검사 실패")')
    return passed


_HARNESS_HEAD = r"""
"use strict";
var clicks = [], calls = [], pending = [], notes = [];
function El(tag) { this.tagName = tag; this.children = []; this.attrs = {}; this.listeners = {}; this.style = {}; this.disabled = false;
  this.textContent = ""; this.title = ""; this.className = ""; this.href = ""; }
El.prototype.appendChild = function (c) { this.children.push(c); return c; };
El.prototype.removeChild = function (c) { this.children = this.children.filter(function (x) { return x !== c; }); };
El.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
El.prototype.addEventListener = function (t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); };
El.prototype.click = function () {
  if (this.disabled) return;
  if (this.tagName === "a") clicks.push({ href: this.href, download: this.attrs.download, hidden: this.style.display === "none" });
  (this.listeners.click || []).forEach(function (f) { f(); });
};
var document = { body: new El("body") };
function el(tag, cls, text) { var e = new El(tag); e.className = cls || ""; if (text !== undefined) e.textContent = text; return e; }
function downloadLink(url, label) { var a = el("a", "outline-button vnew-download", label); a.href = url; a.setAttribute("download", ""); return a; }
function api(path, opt) { calls.push({ path: path, method: opt.method, body: JSON.parse(opt.body) });
  return new Promise(function (res, rej) { pending.push({ res: res, rej: rej }); }); }
function soundPath() { return "/api/vnew/p/P1/sound"; }
function soundAlive() { return true; }
function soundJobText(ctx, text, bad) { notes.push([text, !!bad]); }
function errMessage(e) { return (e && e.message) || "오류"; }
"""

_HARNESS_TAIL = r"""
function tick() { return new Promise(function (r) { setTimeout(r, 0); }); }
(async function () {
  var out = {};
  var ctx = { variant: "webtoon", view: { versions: [{ n: 1 }, { n: 2 }], job: { status: "none" } },
              lines: [{ id: "e1", speaker: "민지" }, { id: "e2", speaker: "준호" }], pickN: null };
  var tools = el("div"), c1 = el("span"), c2 = el("span");
  sldZipButton(ctx, tools); sldRowButton(ctx, ctx.lines[0], c1); sldRowButton(ctx, ctx.lines[1], c2);
  var zip = tools.children[0], r1 = c1.children[0], r2 = c2.children[0];
  out.render = [zip.textContent, zip.disabled, r1.textContent, r1.title, r1.attrs["aria-label"], r2.attrs["aria-label"], r1.attrs.type || r1.type];
  ctx.view.job.status = "running"; sldPaint(ctx);
  zip.disabled = false; zip.click(); // 막힌 단추를 억지로 눌러도 요청하지 않는다
  sldPaint(ctx);
  out.running = [zip.disabled, r1.disabled, r2.disabled, tools.children[1].textContent !== "", calls.length];
  ctx.view.job.status = "done"; sldPaint(ctx);
  zip.click(); zip.click(); r1.click(); sldDownload(ctx, "line:e1", ["e1"]);
  out.busy = [calls.length, zip.textContent, zip.disabled, r1.disabled, calls[0].path, calls[0].body];
  pending[0].res({ url: "https://signed.example/x.zip", filename: "대사별-anime-v2.zip" }); await tick();
  out.done = [clicks, zip.textContent, zip.disabled, r1.disabled, document.body.children.length];
  ctx.pickN = 1; r2.click();
  out.line = [calls.length, calls[1].body, r2.textContent, zip.disabled];
  pending[1].rej({ type: "NoLines", message: "x" }); await tick();
  out.error = [notes[notes.length - 1], r2.textContent, r2.disabled];
  var ctx2 = { variant: "real", view: { versions: [], job: {} }, lines: [{ id: "a", speaker: "" }], inEdit: true };
  var c3 = el("span"); sldRowButton(ctx2, ctx2.lines[0], c3);
  out.edit = [c3.children.length, sldChosenN(ctx2)];
  console.log(JSON.stringify(out));
})().catch(function (e) { console.error(String(e && e.stack || e)); process.exit(1); });
"""

_HARNESS_WANT = {
    "render": ["대사별 내려받기 (zip)", False, "↓", "이 줄 내려받기", "이 줄 내려받기: 1번 줄 민지", "이 줄 내려받기: 2번 줄 준호", "button"],
    "running": [True, True, True, True, 0],
    "busy": [1, "만드는 중…", True, True, "/api/vnew/p/P1/sound/lines/download", {"variant": "webtoon", "n": 2}],
    "done": [[{"href": "https://signed.example/x.zip", "download": "대사별-anime-v2.zip", "hidden": True}], "대사별 내려받기 (zip)", False, False, 0],
    "line": [2, {"variant": "webtoon", "n": 1, "lineIds": ["e2"]}, "만드는 중…", True],
    "error": [["대사를 내려받지 못했습니다: 내려받을 대사가 없습니다.", True], "↓", False],
    "edit": [0, 0],
}


if __name__ == "__main__":
    sys.exit(0 if check_line_download_224() else 1)
