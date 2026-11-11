"""작업 219: 코드 리뷰 결함 고침 검사(가짜 저장소 · 가짜 fal · 가짜 ffmpeg — 네트워크 · 돈 0).

  (rf-1) ⑦ 내보내기 순서: 215c 계획 뒤 분리 결과 — 빈틈 뒤 배경 자리 · 길이, 사용 안 함 배경 없음, 역재생 정방향 배경 없음(로그),
         화면만 숨김(정방향 · 속도 1)은 원래 원본으로 되짚음, 빈틈 둘 IndexError 없음, run_export 끝까지(빈틈 → A2 자리)
  (rf-2) A1 끔 + A2 들림 → 대사.mp3 무음(A2 소리 없음) · 전체 소리에는 A2
  (rf-3) 클립 모두 소리 끔 · 들리는 트랙 없음 → hasAudio False · README 무음 안내
  (rf-4) 스템 wav 는 쓰자마자 지움 — 올리기 때 · 성공 · 실패 모두 workdir 에 남지 않음
  (rf-5) 옛 문서 color 글자 → labelColor(라벨 이름) · 버림(그 밖) · 색 보정 dict 는 그대로 검사 · edit_tools 설명
  (rf-6) 대사 고침 판 키: 같은 r 로 동시에 이어도 키가 다르고 남은 판은 제 파일 · rebuild_revision 도 고유 키
  (rf-7) 대사 고침 시작: 제출 뒤 상태 쓰기 실패(다시 씀) · n 없음 → running · 끝내 못 쓰면 503 · 다음 견적 · 시작이 이어받음(다시 내지 않음)
  (rf-8) 분리: COMPLETED 뒤 결과 받기 HTTP500 이 계속되면 RUN_MAX_S 뒤 failed(같은 안내)
  (rf-9) 분리: requestId 와 running 한 번에 쓰기 · 그 쓰기 실패해도 running · 실패 뒤 다시 누르면 이어받기(제출 · 원장 그대로)
  (rf-10) 분리: 임시 입력 지움(fail · 제출 실패)
  (rf-11) 분리: 가격 모름 → 마지막 단가 × 초 추정 + priceUnknown(조용히 0 아님)
  (rf-12) 분리: 원본을 덮어쓰면(크기 · 길이) separated_audio None · 219 전 기록은 그대로
  (rf-13) LF 줄 끝

실행: python -X utf8 -B tools/check_review_fixes_219.py
check_vpoc 훅 줄(직접 넣지 않는다):
    if not __import__("check_review_fixes_219").check_review_fixes_219(): fail("작업 219 리뷰 결함 고침 검사 실패")
"""

from __future__ import annotations

import copy
import io
import os
import re
import shutil
import sys
import tempfile
import threading
import traceback
import types
import zipfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
for _path in (str(TOOLS), str(ROOT / "app")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

GROUPS = {
    "rf-1": "⑦ 내보내기 순서(215c 계획 → 분리)", "rf-2": "A1 끔 → 대사.mp3 무음", "rf-3": "소리 없음 → hasAudio False",
    "rf-4": "스템 wav 지움", "rf-5": "옛 color 글자 → labelColor", "rf-6": "대사 고침 판 키 고유", "rf-7": "대사 고침 시작 상태",
    "rf-8": "분리 결과 받기 시간 상한", "rf-9": "분리 requestId 지킴 · 이어받기", "rf-10": "분리 임시 입력 지움",
    "rf-11": "분리 가격 모름 원장", "rf-12": "분리 뒤 원본 바뀜", "rf-13": "LF",
}
FILES = [ROOT / "app/src/vnew" / n for n in ("edit.py", "edit_audio.py", "edit_text.py", "edit_tools.py", "audio_separate.py",
                                             "line_redo.py")] + [TOOLS / "check_line_redo_212.py", Path(__file__).resolve()]


def check_review_fixes_219() -> bool:  # noqa: C901, PLR0915 — 묶음마다 한 곳에서 본다
    bad: dict[str, list[str]] = {g: [] for g in GROUPS}
    seen: set[str] = set()
    warns: list[str] = []

    def expect(group: str, got, want, what: str) -> None:
        seen.add(group)
        if got != want:
            bad[group].append(f"{what}\n      결과 {str(got)[:500]}\n      기대 {str(want)[:500]}")

    def crash(group: str, exc: BaseException) -> None:
        seen.add(group)
        bad[group].append("예외: " + repr(exc)[:300] + "\n" + traceback.format_exc()[-1500:])

    import check_vpoc as cv  # noqa: PLC0415
    from src import stitcher as stitchmod  # noqa: PLC0415
    from src.vnew import audio_separate as sep  # noqa: PLC0415
    from src.vnew import edit as E  # noqa: PLC0415
    from src.vnew import edit_audio as ea  # noqa: PLC0415
    from src.vnew import edit_text as ET  # noqa: PLC0415
    from src.vnew import edit_tools as T  # noqa: PLC0415
    from src.vpoc.errors import VpocError  # noqa: PLC0415

    def err_of(fn):
        try:
            fn()
        except VpocError as exc:
            return [exc.kind, exc.status]
        return None

    # ================= (rf-1) 순서: 단위 =================
    try:
        K, K2 = "users/u/video/p/a.mp4", "users/u/video/p/b.mp4"
        V, B = sep.voice_key(K), sep.bg_key(K)
        meta = {k: {"duration": 4.0, "hasAudio": True, "fps": 24, "width": 160, "height": 120} for k in (K, K2)}
        saved_sa = E._separated_audio  # noqa: SLF001
        E._separated_audio = lambda owner, pid, key, duration=None: {"voiceKey": V, "bgKey": B} if key == K else None  # noqa: SLF001
        try:
            def run(clips):
                cl, _mt, _plan, af, bg = E._export_plan_sources("u", "p", copy.deepcopy(clips), dict(meta), {}, 24, 160, 120)  # noqa: SLF001
                frames = E._clip_frames(cl, 24)  # noqa: SLF001
                doc = sep.export_mix_doc(None, bg, cl, frames, None, 24)
                items = [(i["start"], i["in"], i["out"]) for i in (((doc or {}).get("tracks") or [{}])[0].get("items") or [])]
                alt = [E._clip_audio(c, af.get(c["sourceKey"])) for c in cl]  # noqa: SLF001
                return cl, bg, items, alt, af

            def C(cid, key, a, b, **kw):  # noqa: N802
                return {"id": cid, "sourceKey": key, "in": a, "out": b, "muted": False, **kw}

            cl, bg, items, alt, _af = run([C("x", K2, 0.0, 2.0), C("y", K, 1.0, 3.0, gapBefore=1.5)])
            expect("rf-1", [[c["sourceKey"] for c in cl], [b["index"] for b in bg], items, [a is not None for a in alt]],
                   [[K2, T.BLACK_KEY, K], [2], [(3.5, 1.0, 3.0)], [False, False, True]],
                   "빈틈 뒤 분리 클립: 배경 = 계획 뒤 자리(3.5초) · 길이 2초 · 대사 = 분리 목소리")
            cl, bg, items, alt, af = run([C("x", K, 0.0, 2.0, disabled=True), C("y", K2, 0.0, 2.0)])
            expect("rf-1", [cl[0]["sourceKey"], bg, items, alt, T.BLACK_KEY in af],
                   [T.BLACK_KEY, [], [], [None, None], False], "사용 안 함 클립: 배경 · 분리 목소리 없음")
            with cv._LogCapture() as logs:  # noqa: SLF001
                cl, bg, items, alt, af = run([C("x", K, 0.0, 2.0, reverse=True)])
            expect("rf-1", [cl[0]["sourceKey"].startswith("215c:d"), bg, items, alt,
                            cl[0]["sourceKey"] in af, any("why=reverse" in m for m in logs.messages("export_skip"))],
                   [True, [], [], [None], False, True], "역재생 클립: 정방향 배경 · 분리 목소리 없음(파생 원본 소리 그대로) · 로그 한 줄")
            cl, bg, items, alt, af = run([C("x", K, 0.0, 2.0, gain=3.0)])
            expect("rf-1", [bg, alt, cl[0]["sourceKey"] in af], [[], [None], False], "게인 클립: 분리 결과를 넣지 않음(게인이 빠지지 않게)")
            cl, bg, items, alt, af = run([C("x", K, 0.5, 2.5, hideVideo=True)])
            expect("rf-1", [cl[0]["sourceKey"].startswith("215c:d"), [b["index"] for b in bg], items, alt[0] is not None,
                            (af.get(cl[0]["sourceKey"]) or {}).get("sourceKey")],
                   [True, [0], [(0.0, 0.5, 2.5)], True, V], "화면만 숨긴 클립(정방향 · 속도 1): 원래 원본의 분리 결과로 되짚음")
            cl, bg, items, alt, af = run([C("x", K2, 0.0, 1.0, gapBefore=0.5), C("y", K, 0.0, 2.0, gapBefore=1.0), C("z", K, 2.0, 3.0)])
            expect("rf-1", [len(cl), [b["index"] for b in bg], items], [5, [3, 4], [(2.5, 0.0, 2.0), (4.5, 2.0, 3.0)]],
                   "빈틈 둘: IndexError 없음 · 두 배경 다 제자리")
            expect("rf-1", sep.export_mix_doc(None, [{"index": 7, "key": B, "offset": 0.0, "end": 4.0}], cl, E._clip_frames(cl, 24),  # noqa: SLF001
                                              None, 24)["tracks"][0]["items"], [], "목록 밖 자리는 넣지 않음(IndexError 대신)")
        finally:
            E._separated_audio = saved_sa  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        crash("rf-1", exc)

    # ================= (rf-5) 옛 color 글자 =================
    try:
        expect("rf-5", [ET.clean_clip_fx({"color": "teal"}, 1, "t"), ET.clean_clip_fx({"color": "bogus"}, 1, "t"),
                        ET.clean_clip_fx({"color": {"contrast": 10}}, 1, "t"), err_of(lambda: ET.clean_clip_fx({"color": 5}, 1, "t")),
                        err_of(lambda: ET.clean_clip_fx({"color": {"contrast": 140}}, 1, "t"))],
               [{"labelColor": "teal"}, {}, {"color": {"contrast": 10}}, ["BadClipColor", 400], ["BadClipColor", 400]],
               "color 글자: 라벨 이름 → labelColor · 그 밖 버림(400 아님) · 색 보정 dict 는 그대로 검사")
        key = "users/u/video/p/a.mp4"
        clean = lambda extra: E._clean_clips([{"id": "a", "sourceKey": key, "in": 0.0, "out": 1.0, "muted": False, **extra}],  # noqa: E731,SLF001
                                             {key}, {key: 4.0}, "t")[0]
        a, b, c = clean({"color": "teal"}), clean({"color": "nope"}), clean({"color": "teal", "labelColor": "red"})
        expect("rf-5", [a.get("labelColor"), "color" in a, b.get("labelColor"), "color" in b, c.get("labelColor")],
                       ["teal", False, None, False, "red"], "저장 경로(_clean_clips): 옛 color → labelColor · labelColor 가 있으면 그것")
        doc = T.__doc__ or ""
        expect("rf-5", ["color(라벨 색)" in doc, "labelColor(라벨 색" in doc], [False, True], "edit_tools 설명: labelColor(라벨 색)")
    except Exception as exc:  # noqa: BLE001
        crash("rf-5", exc)

    # ================= (rf-1 끝까지 · rf-2 · rf-3 · rf-4 · rf-8 ~ rf-12) 하네스 =================
    try:
        with cv._LogCapture() as logs, cv._EdtEnv() as env, cv._pin_vnew(VNEW_EDIT=True, VNEW_SOUND=True):  # noqa: SLF001
            client = env.client()
            env.inline = True
            env.store.delete_key = lambda k, bucket="": env.store.blobs.pop(k, None)
            owner = env.owner

            # ---- 내보내기 가짜: 섞기 · 내려받기 묶음(진짜 export_package + 가짜 ffmpeg) · 검은 클립 · 임시 폴더 기록 ----
            mixes, listings, uploads = [], [], []
            pkg = {"fail": False}

            def fake_tracks(plan, a1_raw, paths, workdir):
                mixes.append({"items": [(i["assetId"][:1], i["start"], i["in"], i["out"]) for b in plan["buses"] for i in b["items"]],
                              "a1On": plan["a1On"]})
                raw = os.path.join(workdir, "mix.raw")
                with open(raw, "wb") as handle:
                    handle.write(b"\x11" * (int(plan["totalSamples"]) * 4))  # 섞인 소리(A2 소리가 들어 있다)
                outs = []
                for name in ea.stem_list(plan):
                    path = os.path.join(workdir, f"stem-{len(outs):02d}.wav")
                    with open(path, "wb") as handle:
                        handle.write(b"RIFF-" + name.encode("utf-8"))
                    outs.append((name, path))
                return {"raw": raw, "stems": outs}

            def fake_run(command):
                inputs = [command[i + 1] for i, a in enumerate(command) if a == "-i"]
                with open(command[-1], "wb") as out:
                    for path in inputs:
                        with open(path, "rb") as handle:
                            out.write(handle.read())
                return types.SimpleNamespace(returncode=0, stderr=b"")

            def package(video_path, audio_raw, zip_path, **kw):
                if pkg["fail"]:
                    raise stitchmod.StitchError("가짜 묶음 실패")
                return ea.export_package(video_path, audio_raw, zip_path, **kw)

            def black(command):
                with open(command[-1], "wb") as handle:
                    handle.write(b"BLACK")

            real_upload = E._upload  # noqa: SLF001

            def upload(path, key, **kw):
                uploads.append((os.path.basename(path), sorted(os.listdir(os.path.dirname(path)))))
                return real_upload(path, key, **kw)

            class _Dir:
                def __init__(self, *args, **kwargs):
                    self.kwargs = kwargs

                def __enter__(self):
                    self.path = tempfile.mkdtemp(**self.kwargs)
                    return self.path

                def __exit__(self, *_exc):
                    listings.append((str(self.kwargs.get("prefix") or ""), sorted(os.listdir(self.path))))
                    shutil.rmtree(self.path, ignore_errors=True)
                    return False

            class _Temp:
                TemporaryDirectory = _Dir

                def __getattr__(self, name):
                    return getattr(tempfile, name)

            env.set(E, "_export_tracks", fake_tracks)
            env.set(E, "_export_package", package)
            env.set(E, "_upload", upload)
            env.set(E, "tempfile", _Temp())
            env.set(stitchmod, "_edit_run", fake_run)
            env.set(stitchmod, "ffmpeg_path", lambda: "ffmpeg")
            env.set(T, "_run_ffmpeg", black)
            env.set(E, "_probe_audio", lambda path: {"duration": 2.0, "hasAudio": True})
            env.set(E, "_audio_peaks_file", lambda path, workdir: [0.5] * 40)

            def export(pid, clips, stems=False, **doc):
                base = f"/api/vnew/p/{pid}/edit"
                view = client.get(base).get_json() or {}
                saved = client.put(base, json={"etag": view.get("etag") or "", "variant": "webtoon", "clips": clips, **doc}).get_json() or {}
                r = client.post(base + "/export", json={"etag": saved.get("etag"), "variant": "webtoon", "stems": stems})
                return r.status_code, env.export_state(pid).get("current") or {}, saved

            def zip_files(cur) -> dict:
                with zipfile.ZipFile(io.BytesIO(env.store.blobs[cur["zipKey"]])) as archive:
                    return {name: archive.read(name) for name in archive.namelist()}

            def export_dirs():
                return [names for prefix, names in listings if prefix == "vnew-edit-export-"]

            def doc_clips(pid):
                view = client.get(f"/api/vnew/p/{pid}/edit").get_json() or {}
                return {c["sourceKey"].split("/")[-2]: c for c in view["doc"]["clips"]}

            # ---- (rf-1) run_export 끝까지: 빈틈 뒤 분리 클립 → A2 배경 자리 ----
            try:
                pid = "prj_rf1"
                cv._edt_cut_plan(env, pid)  # noqa: SLF001
                c1 = cv._edt_key(pid, "c1-webtoon-1")  # noqa: SLF001
                env.store.blobs[sep.voice_key(c1)] = b"VOICE"
                env.store.blobs[sep.bg_key(c1)] = b"BG"
                real_sa = E._separated_audio  # noqa: SLF001
                env.set(E, "_separated_audio", lambda o, p, k, duration=None, mine=c1: {"voiceKey": sep.voice_key(mine),
                                                                                         "bgKey": sep.bg_key(mine)}
                        if k == mine else real_sa(o, p, k, duration))
                by = doc_clips(pid)
                first, second = dict(by["c2-webtoon-1"]), dict(by["c1-webtoon-1"], gapBefore=1.0)
                seg_before, mix_before = len(env.segments), len(mixes)
                code, cur, _ = export(pid, [first, second])
                f1 = E._clip_frames([first], 24)[0]  # noqa: SLF001
                f2 = E._clip_frames([second], 24)[0]  # noqa: SLF001
                want = [("s", round((f1 + 24) / 24.0, 4), round(float(second["in"]), 4), round(min(3.0, float(second["in"]) + f2 / 24.0), 4))]
                segs = env.segments[seg_before:]
                expect("rf-1", [code, cur.get("status"), cur.get("error"), (mixes[mix_before:] or [{}])[0].get("items"),
                                [bool(s.get("audio_source")) for s in segs]],
                       [202, "done", "", want, [False, False, True]],
                       "run_export: 빈틈 뒤 분리 클립 — A2 배경이 계획 뒤 자리 · 길이 · 대사(분리 목소리)는 그 클립에만")
            except Exception as exc:  # noqa: BLE001
                crash("rf-1", exc)

            # ---- (rf-2) A1 끔 + A2 들림 → 대사.mp3 무음 ----
            try:
                pid = "prj_rf2"
                cv._edt_cut_plan(env, pid)  # noqa: SLF001
                import check_edit_audio_215a as a215  # noqa: PLC0415

                base = f"/api/vnew/p/{pid}/edit"
                client.get(base)
                up = client.post(base + "/audio-assets", data={"file": (io.BytesIO(a215._wav()), "bg.wav")},  # noqa: SLF001
                                 content_type="multipart/form-data").get_json() or {}
                aid = (up.get("asset") or {}).get("assetId")
                item = {"id": "s1", "assetId": aid, "start": 0.0, "in": 0.0, "out": 2.0, "gainDb": 0, "fadeIn": 0, "fadeOut": 0, "keys": []}
                clips = list(doc_clips(pid).values())
                mix_before = len(mixes)
                code, cur, _ = export(pid, clips, audioTracks=[{"id": "a2", "name": "A2 배경음", "items": [item]}],
                                      a1={"gainDb": 0, "mute": True, "duck": False, "duckDb": 8})
                files = zip_files(cur) if cur.get("zipKey") else {}
                dlg = next((v for k, v in files.items() if k.endswith(ea.PACKAGE_DIALOGUE_SUFFIX)), b"?")
                full = next((v for k, v in files.items() if k.endswith(ea.PACKAGE_AUDIO_SUFFIX)), b"")
                expect("rf-2", [code, cur.get("status"), cur.get("error"), (mixes[mix_before:] or [{}])[0].get("a1On"), len(dlg) > 0,
                                set(dlg), b"\x11" in full, len(dlg) == len(full), cur.get("hasAudio")],
                       [202, "done", "", False, True, {0}, True, True, True],
                       "A1 끔 + A2 들림: 대사.mp3 = 같은 길이 무음(A2 없음) · 전체 소리에는 A2 · hasAudio True")
            except Exception as exc:  # noqa: BLE001
                crash("rf-2", exc)

            # ---- (rf-3) 모두 소리 끔 · 트랙 없음 → hasAudio False ----
            try:
                pid = "prj_rf3"
                cv._edt_cut_plan(env, pid)  # noqa: SLF001
                clips = [dict(c, muted=True) for c in doc_clips(pid).values()]
                up_before, dirs_before = len(uploads), len(export_dirs())
                code, cur, _ = export(pid, clips, stems=True)
                files = zip_files(cur) if cur.get("zipKey") else {}
                readme = files.get(ea.PACKAGE_README, b"").decode("utf-8")
                expect("rf-3", [code, cur.get("status"), cur.get("hasAudio"), "들리는 소리가 없어" in readme, bool(cur.get("stemsKey"))],
                       [202, "done", False, True, True], "클립 모두 소리 끔 · 섞기 문서(스템만) · 트랙 없음: hasAudio False · README 무음 안내")
                # (rf-4) 올리기 때 스템 wav 는 이미 묶여 지워졌다 · 끝난 폴더에도 없다
                ups = uploads[up_before:]
                stem_left = lambda names: [n for n in names if n.startswith("stem-") or n == "dialogue-silence.raw"]  # noqa: E731
                expect("rf-4", [[u[0] for u in ups], [stem_left(u[1]) for u in ups], "stems.zip" in (ups[0][1] if ups else []),
                                [stem_left(d) for d in export_dirs()[dirs_before:]]],
                       [["edit.mp4", "package.zip", "stems.zip"], [[], [], []], True, [[]]],
                       "스템 zip 을 고르면: 영상을 올리기 전에 묶고 wav 는 지움 · 끝난 workdir 에 스템 없음")
            except Exception as exc:  # noqa: BLE001
                crash("rf-3", exc)

            # ---- (rf-4) 성공(스템 zip 안 고름) · 실패 때도 남지 않음 ----
            try:
                stem_left = lambda names: [n for n in names if n.startswith("stem-") or n == "dialogue-silence.raw"]  # noqa: E731
                expect("rf-4", [stem_left(d) for d in export_dirs()], [[] for _ in export_dirs()], "지금까지 모든 내보내기 workdir 에 스템 없음")
                pid = "prj_rf4"
                cv._edt_cut_plan(env, pid)  # noqa: SLF001
                dirs_before = len(export_dirs())
                pkg["fail"] = True
                try:
                    code, cur, _ = export(pid, list(doc_clips(pid).values()), stems=True, a1={"gainDb": 0, "mute": True, "duck": False})
                finally:
                    pkg["fail"] = False
                expect("rf-4", [cur.get("status"), [stem_left(d) for d in export_dirs()[dirs_before:]]],
                       ["failed", [[]]], "묶음 단계에서 실패해도 스템 wav · 무음 대사가 workdir 에 남지 않음")
            except Exception as exc:  # noqa: BLE001
                crash("rf-4", exc)

            # ---- 분리 가짜 ----
            fal = {"posts": 0, "status": "COMPLETED", "result": "500", "post_fail": None}
            ff_calls, charges = [], []

            def fake_fal(method, url, payload, *, where):
                if method == "POST":
                    if fal["post_fail"] is not None:
                        raise fal["post_fail"]
                    fal["posts"] += 1
                    return {"request_id": f"req-219-{fal['posts']}"}
                if url.endswith("/status"):
                    return {"status": fal["status"]}
                if fal["result"] == "500":
                    raise VpocError(where, "HTTP500", "fal 500", 502)
                return {"vocals": {"url": "https://fal.example/v.wav"}, "accompaniment": {"url": "https://fal.example/b.wav"}}

            def fake_ff(args, what):
                ff_calls.append(list(args))
                with open(args[-1], "wb") as handle:
                    handle.write(b"FF")

            fetched = {"https://fal.example/v.wav": b"VOICE", "https://fal.example/b.wav": b"BG"}
            env.set(sep, "_fal_call", fake_fal)
            env.set(sep, "_ffmpeg", fake_ff)
            env.set(sep, "_fetch", lambda url: fetched[url])
            env.set(sep, "_price", lambda m: {"unitPrice": 0.02, "unit": "seconds"})
            env.set(sep, "_charge", lambda k, **kw: charges.append((k, kw)))
            env.set(sep, "_fal_ready", lambda: True)
            writes: list = []
            fail_plan: dict = {}
            real_ws = sep._write_state  # noqa: SLF001

            def ws(key, fn):
                plan = fail_plan.get(key)
                if plan is not None:
                    plan["n"] += 1
                    if plan["n"] == plan["at"]:
                        raise VpocError("store.write_json", "WriteFailed", "가짜 쓰기 실패", 500)
                out = real_ws(key, fn)
                writes.append((key, copy.deepcopy(out)))
                return out

            env.set(sep, "_write_state", ws)
            body = lambda key, **kw: {"scope": "edit", "variant": "webtoon", "sourceKey": key, **kw}  # noqa: E731
            inputs = lambda key: [k for k in env.store.blobs if k.startswith(sep.sep_prefix(key) + "/input-")]  # noqa: E731
            old = lambda key: sep._write_state(key, lambda s: {**s, "startedAt": "2000-01-01T00:00:00+00:00"})  # noqa: E731,SLF001
            view = lambda pid: sep.view(owner, pid, {"scope": "edit", "variant": "webtoon"})  # noqa: E731

            # ---- (rf-8 · rf-9 · rf-10) 결과 받기 HTTP500 → 시간 상한 → 다시 누르면 이어받기 ----
            try:
                pid = "prj_rf8"
                cv._edt_cut_plan(env, pid)  # noqa: SLF001
                c1 = cv._edt_key(pid, "c1-webtoon-1")  # noqa: SLF001
                started = sep.start(owner, pid, body(c1, confirmUsd=0.06))
                mine = [w[1] for w in writes if w[0] == c1]
                first_run = next((s for s in mine if (s or {}).get("status") == "running"), {})
                expect("rf-9", [started["state"]["status"], [s.get("status") for s in mine], first_run.get("requestId"),
                                bool(first_run.get("inputKey")), first_run.get("sourceSize"), first_run.get("sourceDuration"), len(charges)],
                       ["running", ["starting", "running"], "req-219-1", True, len(env.store.blobs[c1]), 3.0, 1],
                       "제출: starting → running 한 번에(requestId · inputKey · 원본 크기 · 길이) · 원장 1건")
                young = view(pid)
                had_input = inputs(c1)
                old(c1)
                failed = view(pid)
                st = sep._read_state(c1)  # noqa: SLF001
                expect("rf-8", [[i["state"]["status"] for i in young["items"] if i["sourceKey"] == c1],
                                [i["state"]["status"] for i in failed["items"] if i["sourceKey"] == c1], st.get("error"), st.get("requestId"),
                                st.get("resumable")],
                       [["running"], ["failed"], sep.TIMEOUT_TEXT, "req-219-1", True],
                       "COMPLETED 뒤 결과 받기 HTTP500: 젊으면 running · RUN_MAX_S 지나면 failed(같은 안내) · requestId 남김")
                expect("rf-10", [len(had_input), inputs(c1)], [1, []], "fail(): 임시 입력 지움")
                posts, n_charges = fal["posts"], len(charges)
                again = sep.start(owner, pid, body(c1, confirmUsd=0.06))
                fal["result"] = "ok"
                done = view(pid)
                st = sep._read_state(c1)  # noqa: SLF001
                expect("rf-9", [again.get("resumed"), again["state"]["status"], fal["posts"] - posts, len(charges) - n_charges,
                                [i["state"]["status"] for i in done["items"] if i["sourceKey"] == c1], st.get("requestId"),
                                env.store.blobs.get(sep.voice_key(c1))],
                       [True, "running", 0, 0, ["done"], "req-219-1", b"FF"],
                       "실패 뒤 다시 누르면 같은 requestId 로 결과를 다시 받음(제출 0 · 원장 0) → done")
                fal["result"] = "500"
            except Exception as exc:  # noqa: BLE001
                crash("rf-9", exc)

            # ---- (rf-9) running 쓰기가 실패해도 requestId 를 지킨다 ----
            try:
                pid = "prj_rf9"
                cv._edt_cut_plan(env, pid)  # noqa: SLF001
                c1 = cv._edt_key(pid, "c1-webtoon-1")  # noqa: SLF001
                fail_plan[c1] = {"n": 0, "at": 2}  # 1 = starting, 2 = running(실패)
                posts, n_charges = fal["posts"], len(charges)
                started = sep.start(owner, pid, body(c1, confirmUsd=0.06))
                st = sep._read_state(c1)  # noqa: SLF001
                fail_plan.pop(c1, None)
                expect("rf-9", [started["state"]["status"], st.get("status"), st.get("requestId"), fal["posts"] - posts,
                                len(charges) - n_charges, bool(inputs(c1))],
                       ["running", "running", f"req-219-{posts + 1}", 1, 1, True],
                       "제출 뒤 running 쓰기 실패: 다시 써 running · requestId 지킴(고아 요청 없음) · 원장 1건")
            except Exception as exc:  # noqa: BLE001
                crash("rf-9", exc)

            # ---- (rf-10) 제출 실패 → 임시 입력 지움 ----
            try:
                pid = "prj_rf10"
                cv._edt_cut_plan(env, pid)  # noqa: SLF001
                c1 = cv._edt_key(pid, "c1-webtoon-1")  # noqa: SLF001
                fal["post_fail"] = VpocError("vpoc.fal", "HTTP400", "bad request", 400)
                try:
                    got = err_of(lambda: sep.start(owner, pid, body(c1, confirmUsd=0.06)))
                finally:
                    fal["post_fail"] = None
                st = sep._read_state(c1)  # noqa: SLF001
                put_inputs = [p for p in env.store.puts if p[0].startswith(sep.sep_prefix(c1) + "/input-")]
                expect("rf-10", [got, st.get("status"), len(put_inputs), inputs(c1)], [["HTTP400", 400], "failed", 1, []],
                       "제출 실패: 올린 임시 입력을 지움 · failed")
            except Exception as exc:  # noqa: BLE001
                crash("rf-10", exc)

            # ---- (rf-11) 가격 모름 → 추정 · priceUnknown ----
            try:
                pid = "prj_rf11"
                cv._edt_cut_plan(env, pid)  # noqa: SLF001
                c1, c2 = cv._edt_key(pid, "c1-webtoon-1"), cv._edt_key(pid, "c2-webtoon-1")  # noqa: SLF001
                saved_last = dict(sep._LAST_PRICE)  # noqa: SLF001
                try:
                    sep.estimate(1.0)  # 단가를 안다 → 기억
                    remembered = dict(sep._LAST_PRICE)  # noqa: SLF001
                    env.set(sep, "_price", lambda m: None)
                    est = sep.estimate(3.0)
                    logs.clear()
                    sep.start(owner, pid, body(c1, confirmUsd=0))
                    k1, kw1 = charges[-1]
                    st1 = sep._read_state(c1)  # noqa: SLF001
                    sep._LAST_PRICE.clear()  # noqa: SLF001
                    sep.start(owner, pid, body(c2, confirmUsd=0))
                    _k2, kw2 = charges[-1]
                    st2 = sep._read_state(c2)  # noqa: SLF001
                finally:
                    sep._LAST_PRICE.clear()  # noqa: SLF001
                    sep._LAST_PRICE.update(saved_last)  # noqa: SLF001
                    env.set(sep, "_price", lambda m: {"unitPrice": 0.02, "unit": "seconds"})
                expect("rf-11", [remembered, est["priceKnown"], k1.startswith("vnew-sep-"), round(kw1["usd"], 6), kw1.get("price_unknown"),
                                 st1.get("priceUnknown"), st1.get("usdEstimate"), kw2["usd"], kw2.get("price_unknown"), st2.get("priceUnknown"),
                                 len(logs.messages("price_unknown")) >= 2],
                       [{"unitPrice": 0.02, "unit": "seconds"}, False, True, 0.06, True, True, 0.06,
                        round(sep.FALLBACK_USD_PER_S * 3.0, 6), True, True, True],
                       "가격 모름: 마지막 단가 × 3초 = 0.06 추정 · priceUnknown(상태 · 원장 호출 · 경고 로그) · 단가도 모르면 공개 단가 추정(작업 220a) + 표시")
            except Exception as exc:  # noqa: BLE001
                crash("rf-11", exc)

            # ---- (rf-12) 원본 덮어쓰기 → separated_audio None ----
            try:
                pid = "prj_rf12"
                cv._edt_cut_plan(env, pid)  # noqa: SLF001
                c1 = cv._edt_key(pid, "c1-webtoon-1")  # noqa: SLF001
                fal["result"] = "ok"
                try:
                    sep.start(owner, pid, body(c1, confirmUsd=0.06))
                    view(pid)
                finally:
                    fal["result"] = "500"
                sep.set_use(owner, pid, body(c1, useVoice=True))
                env.set(sep, "_source_size", lambda k: len(env.store.blobs[k]) if k in env.store.blobs else None)
                same = sep.separated_audio(owner, pid, c1)
                same_dur = sep.separated_audio(owner, pid, c1, 3.0)
                other_dur = sep.separated_audio(owner, pid, c1, 5.0)
                original = env.store.blobs[c1]
                env.store.blobs[c1] = original + b";overwritten"
                resized = sep.separated_audio(owner, pid, c1)
                via_edit = E._separated_audio(owner, pid, c1)  # noqa: SLF001
                sep._write_state(c1, lambda s: {k: v for k, v in s.items() if k not in ("sourceSize", "sourceDuration")})  # noqa: SLF001
                legacy = sep.separated_audio(owner, pid, c1)
                env.store.blobs[c1] = original
                expect("rf-12", [sep._read_state(c1).get("status"), bool(same), bool(same_dur), other_dur, resized, via_edit, bool(legacy)],  # noqa: SLF001
                       ["done", True, True, None, None, None, True],
                       "원본 그대로면 분리 결과 · 길이 다르면 None · 크기 다르면 None(⑦ 경로도) · 크기 · 길이 기록이 없는 옛 기록은 그대로")
            except Exception as exc:  # noqa: BLE001
                crash("rf-12", exc)
    except Exception as exc:  # noqa: BLE001
        crash("rf-2", exc)

    # ================= (rf-6 · rf-7) 대사 고침 =================
    with cv._LogCapture():  # noqa: SLF001 — 경고 로그(다시 쓰기 · 경합)는 일부러 나는 것이라 화면에 내지 않는다
        _line_redo_checks(expect, crash, err_of)

    # ================= (rf-13) LF =================
    try:
        expect("rf-13", [p.name for p in FILES if b"\r" in p.read_bytes()], [], "LF 줄 끝")
    except Exception as exc:  # noqa: BLE001
        crash("rf-13", exc)

    ok_all = True
    for group, title in GROUPS.items():
        if bad[group]:
            ok_all = False
            print(f"[FAIL] 작업 219 {group} {title}")
            for line in bad[group][:3]:
                print("    " + line)
        elif group in seen:
            print(f"[ok  ] 작업 219 {group} {title}")
        else:
            ok_all = False
            print(f"[FAIL] 작업 219 {group} {title}: 돌지 않았습니다")
    for line in warns:
        print(f"[WARN] {line}")
    print('check_vpoc 훅 줄: if not __import__("check_review_fixes_219").check_review_fixes_219(): '
          'fail("작업 219 리뷰 결함 고침 검사 실패")')
    return ok_all


def _line_redo_checks(expect, crash, err_of) -> None:  # noqa: C901, PLR0915
    """(rf-6) 판 키 고유 · (rf-7) 시작 상태. check_line_redo_212 의 가짜 저장소 · 계획을 쓴다."""
    import check_line_redo_212 as L  # noqa: PLC0415
    from src.vnew import edit as editmod  # noqa: PLC0415
    from src.vnew import generate as gm  # noqa: PLC0415
    from src.vnew import line_redo as lr  # noqa: PLC0415
    from src.vnew import plan as planmod  # noqa: PLC0415
    from src.vnew import retake as rt  # noqa: PLC0415
    from src.vnew import settings as vset  # noqa: PLC0415
    from src.vnew import sound as snd  # noqa: PLC0415
    from src.vnew import voices as voices_mod  # noqa: PLC0415
    from src.vpoc import ledger as ledger_mod  # noqa: PLC0415
    from src.vpoc import settings as vpoc_set  # noqa: PLC0415
    from src.vpoc.errors import VpocError  # noqa: PLC0415

    owner, pid = L.OWNER, L.PID
    lock = threading.Lock()

    class Store(L._Store):  # noqa: SLF001
        def __init__(self) -> None:
            super().__init__()
            self.fail: dict[str, int] = {}

        def write_json(self, key, data, *, etag="", create_only=False):
            with lock:  # 두 스레드(두 파드 흉내)가 같은 문서를 조건부로 쓴다
                if self.fail.get(key, 0) > 0:
                    self.fail[key] -= 1
                    raise VpocError("store.write_json", "WriteFailed", "가짜 쓰기 실패", 500)
                return super().write_json(key, data, etag=etag, create_only=create_only)

    store = Store()
    calls: dict[str, list] = {"compose": [], "submit": []}
    retake_state: dict = {"v": 1, "cells": {}}
    sub = {"n": 2, "add": False}
    sync = {"on": False, "barrier": None}
    uploads: dict[str, bytes] = {}
    spawned: list = []

    def fake_compose(owner_, pid_, mid, payload, edit_lines=None):
        lines = edit_lines('Change the line to "Bye." and match lip-sync.', 3000) if edit_lines else ["EDIT: Edit @Video1."]
        prompt = "\n".join(lines + ["@Video1 is the current result video of this shot (not a previz): keep it as the base.",
                                    rt.COMPOSE_CONTEXT_LINE, "CONTEXT: 민지 says \"안녕\". 준호 thinks \"설마…\"", gm.AUDIO_DIALOGUE_ONLY])
        calls["compose"].append(mid)
        cell = "webtoon:c1" if mid == editmod.media_id(L._key("c1-webtoon-1.mp4")) else "webtoon:c2"  # noqa: SLF001
        return {"cellKey": cell, "draft": {"id": f"d{len(calls['compose'])}", "model": "fal:edit", "modelLabel": "Kling Edit",
                                           "maxBytes": 3000, "plan": {"usd": 1.23, "sentSeconds": 3.0}, "prompt": prompt}}

    def fake_submit(owner_, pid_, mid, payload):
        calls["submit"].append(payload.get("draftId"))
        cell = "webtoon:c1" if mid == editmod.media_id(L._key("c1-webtoon-1.mp4")) else "webtoon:c2"  # noqa: SLF001
        if sub["add"]:
            retake_state["cells"][cell] = {"versions": [{"n": 3, "status": "queued", "draftId": payload.get("draftId")}], "draft": None}
        return {"cellKey": cell, "live": {"n": sub["n"], "status": "queued", "attempt": 1}}

    def fake_splice(base_path, new_path, out_path, **kw):
        with open(new_path, "rb") as handle:
            tag = b";tag=1" if b"tag=1" in handle.read() else b";tag=2"
        with open(out_path, "wb") as handle:
            handle.write(L._fake(kw["duration"], w=kw["width"], h=kw["height"], fps=kw["fps"]) + tag)  # noqa: SLF001

    def fake_download(key, path):
        with open(path, "wb") as handle:
            handle.write(store.blobs[key])
        return len(store.blobs[key])

    def fake_upload(path, key):
        with open(path, "rb") as handle:
            data = handle.read()
        if sync["on"]:
            try:
                sync["barrier"].wait()  # 두 잇기가 모두 같은 r 로 올리기 직전까지 온다
            except threading.BrokenBarrierError:
                pass
        uploads[key] = data
        store.blobs[key] = data

    saved_attrs: list = []

    def setv(obj, name, value):
        saved_attrs.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    for mod in (editmod, planmod, gm):
        setv(mod, "STORE", store)
    setv(vpoc_set, "VIDEO_BUCKET", "lr-video")
    setv(vset, "VNEW_SOUND", True)
    setv(voices_mod, "enabled", lambda: False)
    setv(vset, "VNEW_EDIT_RETAKE", True)
    setv(vpoc_set, "FAL_KEY", "")
    setv(snd, "_download", fake_download)
    setv(snd, "_probe", L._probe_bytes)  # noqa: SLF001
    setv(snd, "_detect_speech", lambda path, duration: [])
    setv(lr, "ENABLED", True)
    setv(lr, "START_WRITE_WAIT_S", 0.0)
    setv(lr, "_SPLICE_SEM", threading.Semaphore(2))  # 파드 둘(파드마다 제 세마포어)
    setv(lr, "_download", fake_download)
    setv(lr, "_upload", fake_upload)
    setv(lr, "_probe", L._probe_bytes)  # noqa: SLF001
    setv(lr, "_splice_file", fake_splice)
    setv(lr, "_compose", fake_compose)
    setv(lr, "_submit", fake_submit)
    setv(lr, "_models", lambda *a, **k: [])
    setv(lr, "_retake_state", lambda o, p, g: copy.deepcopy(retake_state))
    setv(lr, "_spawn", lambda target, args, name: spawned.append((target, args)))
    setv(lr, "_scene_changes", lambda key, start, end: [])
    setv(lr, "_scenes", lambda path, start, end: [])
    setv(ledger_mod, "charge_text", lambda key, **kw: None)
    redo_k = lr.redo_key(owner, pid, "g1", "webtoon")
    final_key = L._key("final-webtoon.mp4")  # noqa: SLF001
    try:
        store.blobs.update({final_key: L._fake(4.0), L._key("c1-webtoon-1.mp4"): L._fake(2.0),  # noqa: SLF001
                            L._key("c2-webtoon-1.mp4"): L._fake(2.0)})  # noqa: SLF001
        planmod.save_plan(owner, pid, L._sound_plan())  # noqa: SLF001
        snd.sound_view(owner, pid, "webtoon")
        lr.save_override(owner, pid, {"variant": "webtoon", "lineId": "e1", "textKo": "잘 가", "speaker": "민지", "thought": False})
        lr.save_override(owner, pid, {"variant": "webtoon", "lineId": "e2", "textKo": "설마 그럴 리가", "speaker": "준호", "thought": True})
        job = lambda line="e1": lr.read_redo(owner, pid, "g1", "webtoon")["jobs"].get(line) or {}  # noqa: E731
        start = lambda line="e1": lr.start(owner, pid, {"variant": "webtoon", "lineId": line, "confirmUsd": 1.23})  # noqa: E731
        quote = lambda line="e1": lr.quote(owner, pid, {"variant": "webtoon", "lineId": line})  # noqa: E731

        def fresh(line="e1") -> dict:
            store.json.pop(redo_k, None)
            retake_state["cells"].clear()
            sub.update(n=2, add=False)
            quote(line)
            return job(line)

        # ---- (rf-7) ----
        try:
            q = fresh()
            n_sub = len(calls["submit"])
            store.fail[redo_k] = 2
            s1 = start()
            j1 = job()
            expect("rf-7", [q["status"], s1["job"]["status"], j1["status"], j1.get("n"), j1.get("cellKey"), len(calls["submit"]) - n_sub],
                   ["quoted", "running", "running", 2, "webtoon:c1", 1], "제출 뒤 상태 쓰기 두 번 실패 → 다시 써 running(n · 칸 키)")
            q = fresh()
            sub.update(n=None, add=True)
            start()
            j2a = job()
            q = fresh()
            sub.update(n=None, add=False)
            start()
            j2b = job()
            retake_state["cells"]["webtoon:c1"] = {"versions": [{"n": 3, "status": "submitted", "draftId": q["draftId"]}], "draft": None}
            snd.sound_view(owner, pid, "webtoon")
            j2c = job()
            expect("rf-7", [[j2a["status"], j2a.get("n")], [j2b["status"], j2b.get("n")], [j2c["status"], j2c.get("n"), j2c.get("stage")]],
                   [["running", 3], ["running", None], ["running", 3, "submitted"]],
                   "제출 응답에 n 이 없음: retake 상태에서 이 초안의 번호 · 못 찾으면 n 없이 running → 다음 조회가 초안으로 번호를 찾음")
            q = fresh()
            n_sub, n_compose = len(calls["submit"]), len(calls["compose"])
            store.fail[redo_k] = 99
            lost = err_of(start)
            store.fail[redo_k] = 0
            after_lost = job()["status"]
            retake_state["cells"]["webtoon:c1"] = {"versions": [{"n": 2, "status": "queued", "draftId": q["draftId"]}], "draft": None}
            requote = err_of(quote)
            j3 = job()
            expect("rf-7", [lost, after_lost, requote, j3["status"], j3.get("n"), len(calls["submit"]) - n_sub, len(calls["compose"]) - n_compose],
                   [["LineRedoStateFailed", 503], "quoted", ["LineRedoRunning", 409], "running", 2, 1, 0],
                   "상태를 끝내 못 쓰면 503 · 다음 견적은 이미 나간 retake 를 이어받음(새 견적 · 제출 없음)")
            q = fresh()
            n_sub = len(calls["submit"])
            retake_state["cells"]["webtoon:c1"] = {"versions": [{"n": 2, "status": "submitted", "draftId": q["draftId"]}], "draft": None}
            s4 = start()
            expect("rf-7", [s4["job"]["status"], job().get("n"), len(calls["submit"]) - n_sub], ["running", 2, 0],
                   "다음 시작도 같은 초안의 retake 를 이어받음(두 번 내지 않음 · 돈 두 번 아님)")
        except Exception as exc:  # noqa: BLE001
            crash("rf-7", exc)

        # ---- (rf-6) ----
        try:
            store.json.pop(redo_k, None)
            retake_state["cells"].clear()
            sub.update(n=2, add=False)
            for line in ("e1", "e2"):
                quote(line)
                start(line)
            out1, out2 = L._key("retake/c1-v2.mp4"), L._key("retake/c2-v2.mp4")  # noqa: SLF001
            store.blobs[out1] = L._fake(2.0) + b";tag=1"  # noqa: SLF001
            store.blobs[out2] = L._fake(2.0) + b";tag=2"  # noqa: SLF001
            retake_state["cells"]["webtoon:c1"] = {"versions": [{"n": 2, "status": "completed", "outputKey": out1}], "draft": None}
            retake_state["cells"]["webtoon:c2"] = {"versions": [{"n": 2, "status": "completed", "outputKey": out2}], "draft": None}
            spawned.clear()
            snd.sound_view(owner, pid, "webtoon")
            sync.update(on=True, barrier=threading.Barrier(2, timeout=10))
            threads = [threading.Thread(target=t, args=a, daemon=True) for t, a in list(spawned)]
            for th in threads:
                th.start()
            for th in threads:
                th.join(timeout=30)
            sync["on"] = False
            redo = lr.read_redo(owner, pid, "g1", "webtoon")
            keys = sorted(uploads)
            rev = redo["revisions"][0] if redo["revisions"] else {}
            jobs = {k: (v.get("status"), v.get("error")) for k, v in redo["jobs"].items()}
            winner = rev.get("lineId")
            loser = "e2" if winner == "e1" else "e1"
            expect("rf-6", [len(spawned), len(keys), len(set(keys)), all(re.search(r"-lines-r1-[0-9a-f]{6}\.mp4$", k) for k in keys),
                            len(redo["revisions"]), rev.get("key") in uploads, store.blobs.get(rev.get("key")) == uploads.get(rev.get("key")),
                            (b";tag=1" if winner == "e1" else b";tag=2") in store.blobs.get(rev.get("key"), b""),
                            len({uploads[k] for k in keys}), jobs.get(winner, (None,))[0], jobs.get(loser)],
                   [2, 2, 2, True, 1, True, True, True, 2, "done", ("failed", "다른 작업이 먼저 새 판을 만들었습니다.")],
                   "같은 r 로 동시에 잇기: 키 둘이 다름(-r1-토큰) · 진 쪽은 LineRedoRace · 남은 판 = 제 파일(덮이지 않음)")
            built = lr.rebuild_revision(owner, pid, "webtoon", 1)
            redo = lr.read_redo(owner, pid, "g1", "webtoon")
            rev2 = next((r for r in redo["revisions"] if r.get("r") == 2), {})
            expect("rf-6", [bool(re.search(r"-lines-r2-[0-9a-f]{6}\.mp4$", built["key"])), rev2.get("key") == built["key"],
                            built["key"] in store.blobs, built["key"] != rev.get("key")],
                   [True, True, True, True], "rebuild_revision 도 고유 키 · 판 key 가 그 파일")
        except Exception as exc:  # noqa: BLE001
            crash("rf-6", exc)
    finally:
        for obj, name, value in reversed(saved_attrs):
            setattr(obj, name, value)


if __name__ == "__main__":
    sys.exit(0 if check_review_fixes_219() else 1)
