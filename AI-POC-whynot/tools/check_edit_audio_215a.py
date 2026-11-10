"""작업 215a 검사 — ⑦ 편집 오디오 트랙(내 소리 · 이득 · 페이드 · 볼륨 키 · 자동 덕킹 · 섞어 내보내기 · 스템). 유료 호출 0.

  (ea-a) 문서 정리: audioTracks · a1 검사 · 한도(트랙 4 · 이득 −60~12 · 페이드 · 키 · 없는 소리), 옛 문서(트랙 없음)는 문서 · 해시 그대로
  (ea-b) 내 소리 올리기: 형식(이름 + 앞 바이트) · 크기(413) · 길이 · 소유자만(403) · 목록 · 쓰는 소리 지우기 409 · 안 쓰면 지움
  (ea-c) 키프레임 포락선 수학: 꺾은선 · 페이드 · ffmpeg 식(clip 합)이 꺾은선과 같은 값
  (ea-d) 대사 구간 → 덕킹 포락선(공격 · 놓기 0.15초, 가까운 구간 잇기)
  (ea-e) 2트랙 섞기 필터 그래프 글 + 로컬 ffmpeg 실측(사인 · 잡음): 덕킹 ≈ 8 dB · 페이드 끝 −inf · 참 피크 ≤ −1 dBTP · 잘림 없음
  (ea-f) 스템 목록 · 내보내기(run_export) 끝까지: mp4 소리 = 섞은 소리 · zip(대사만 · 배경음 · 효과음 · mix.wav) · 상태 stems
  (ea-g) 실행 취소: 오디오 몸짓마다 한 단계 · 클립 몸짓과 같은 스택 · 빈 mix 는 215a 전 스냅샷 그대로(node)
  (ea-h) 화면(node): node --check · 아이템 넣기 · 끝 다듬기 · 키 더하기/옮기기/지우기 · mute/solo · 자르기 · 붙기 · PUT 바디 · JS=파이썬 수학
  (ea-i) AI 0: edit_audio 는 모델 · fal 을 부르지 않는다(가져오기 · 글), 검사 중 urlopen 0
  (ea-j) LF 줄 끝(바꾼 파일)

실행: python -X utf8 -B tools/check_edit_audio_215a.py   (node 를 숨기고 보려면 --no-node)
"""

from __future__ import annotations

import io
import json
import math
import os
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
    "ea-a": "문서 정리 · 한도 · 옛 문서 그대로",
    "ea-b": "내 소리 올리기 검사(형식 · 크기 · 소유자) · 쓰는 소리 지우기 막음",
    "ea-c": "키프레임 포락선 수학 · ffmpeg 식",
    "ea-d": "대사 구간 → 덕킹 포락선",
    "ea-e": "2트랙 섞기 그래프 · ffmpeg 실측(덕킹 · 페이드 · −1 dBTP)",
    "ea-f": "스템 목록 · 내보내기 끝까지",
    "ea-g": "실행 취소(몸짓마다 한 단계)",
    "ea-h": "화면 몸짓(node)",
    "ea-i": "AI 호출 0",
    "ea-j": "LF 줄 끝",
}
NODE_GROUPS = ("ea-g", "ea-h")
CHANGED = ["app/src/vnew/edit.py", "app/src/vnew/edit_audio.py", "app/src/vnew/routes.py", "frontend/public/vnew.js",
           "frontend/public/vnew.css", "tools/check_edit_audio_215a.py"]
HOOK = ('    if not __import__("check_edit_audio_215a").check_edit_audio_215a(): '
        'fail("작업 215a: ⑦ 오디오 트랙(내 소리 · 키 · 덕킹 · 섞기 · 스템) 검사 실패")')
JS_START = "  /* ================================================================ 작업 215a: ⑦ 오디오 트랙(A2~A5 내 소리) 시작"
JS_END = "  /* ================================================================ 작업 215a: ⑦ 오디오 트랙 끝 */"
A1 = "a" + "1" * 16
A2 = "a" + "2" * 16


def _wav(seconds: float = 2.0, rate: int = 8000) -> bytes:
    """작은 무음 WAV(검사용 업로드 바이트)."""
    import wave  # noqa: PLC0415

    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\0\0" * int(seconds * rate))
    return buf.getvalue()


def _eval_expr(expr: str, t: float) -> float:
    """piecewise_expr 글을 파이썬으로 계산(clip · gte 만 쓴다)."""
    env = {"clip": lambda v, lo, hi: max(lo, min(hi, v)), "gte": lambda a, b: 1.0 if a >= b else 0.0, "t": t}
    return float(eval(expr, {"__builtins__": {}}, env))  # noqa: S307 — 우리가 만든 식(숫자 · clip · gte)만


def _item(**kw) -> dict:
    base = {"id": "i1", "assetId": A1, "start": 0.0, "in": 0.0, "out": 4.0, "gainDb": 0.0, "fadeIn": 0.0, "fadeOut": 0.0, "keys": []}
    base.update(kw)
    return base


def _track(tid: str, items: list, **kw) -> dict:
    base = {"id": tid, "name": "", "mute": False, "solo": False, "gainDb": 0.0, "items": items}
    base.update(kw)
    return base


def _node_bin(argv) -> str:
    if "--no-node" in argv or os.environ.get("CHECK_215A_NO_NODE") == "1":
        return ""
    return shutil.which("node") or ""


def check_edit_audio_215a(argv=None) -> bool:  # noqa: C901, PLR0915 — 묶음마다 한 곳에서 본다
    argv = list(sys.argv[1:] if argv is None else argv)
    bad: dict[str, list[str]] = {g: [] for g in GROUPS}
    seen: set[str] = set()
    warns: list[str] = []
    notes: list[str] = []

    def expect(group: str, got, want, what: str) -> None:
        seen.add(group)
        if got != want:
            bad[group].append(f"{what}\n      결과 {str(got)[:400]}\n      기대 {str(want)[:400]}")

    def crash(group: str, exc: BaseException) -> None:
        seen.add(group)
        bad[group].append("예외: " + repr(exc)[:300] + "\n" + traceback.format_exc()[-1200:])

    import check_vpoc as cv  # noqa: PLC0415 — 기존 ⑦ 하네스(_EdtEnv · 가짜 DOM)만 빌려 쓴다(고치지 않는다)
    from src import stitcher  # noqa: PLC0415
    from src.vnew import edit as edit_mod  # noqa: PLC0415
    from src.vnew import edit_audio as ea  # noqa: PLC0415
    from src.vpoc.errors import VpocError  # noqa: PLC0415

    def kind_of(fn) -> str:
        try:
            fn()
        except VpocError as exc:
            return exc.kind
        return ""

    assets = {A1: {"duration": 10.0}, A2: {"duration": 3.0}}

    # ================= (ea-a) 문서 정리 =================
    try:
        clean = ea.clean_tracks([_track("a2", [_item(keys=[{"t": 3, "db": -6}, {"t": 1, "db": 2}], fadeIn=0.5, fadeOut=0.5,
                                                     out=12.0)])], assets, 300.0, "w")
        expect("ea-a", [clean[0]["name"], clean[0]["items"][0]["out"], [k["t"] for k in clean[0]["items"][0]["keys"]]],
               ["A2 배경음", 10.0, [1.0, 3.0]], "이름 기본값 · out 은 소리 길이로 · 키는 시각 순")
        cases = {
            "트랙 5개": lambda: ea.clean_tracks([_track(f"t{n}", []) for n in range(5)], assets, 300, "w"),
            "이득 13dB": lambda: ea.clean_tracks([_track("a2", [], gainDb=13)], assets, 300, "w"),
            "아이템 이득 −61": lambda: ea.clean_tracks([_track("a2", [_item(gainDb=-61)])], assets, 300, "w"),
            "페이드 합 > 길이": lambda: ea.clean_tracks([_track("a2", [_item(fadeIn=3, fadeOut=2)])], assets, 300, "w"),
            "없는 소리": lambda: ea.clean_tracks([_track("a2", [_item(assetId="a" + "9" * 16)])], assets, 300, "w"),
            "키 밖": lambda: ea.clean_tracks([_track("a2", [_item(keys=[{"t": 9, "db": 0}])])], assets, 300, "w"),
            "타임라인 넘음": lambda: ea.clean_tracks([_track("a2", [_item(start=298)])], assets, 300, "w"),
            "id 겹침": lambda: ea.clean_tracks([_track("a2", [_item(), _item()])], assets, 300, "w"),
            "덕킹 50": lambda: ea.clean_a1({"duck": True, "duckDb": 50}, "w"),
            "mute 글자": lambda: ea.clean_a1({"mute": "yes"}, "w"),
        }
        expect("ea-a", {k: kind_of(f) for k, f in cases.items()},
               {"트랙 5개": "TooManyAudioTracks", "이득 13dB": "BadAudioGain", "아이템 이득 −61": "BadAudioGain",
                "페이드 합 > 길이": "BadAudioFade", "없는 소리": "AudioAssetMissing", "키 밖": "BadAudioKeys",
                "타임라인 넘음": "BadAudioItem", "id 겹침": "BadAudioItem", "덕킹 50": "BadAudioDuck", "mute 글자": "BadAudioTrack"},
               "한도 밖 값은 400 kind")
        clips = [{"sourceKey": "k1", "in": 0.0, "out": 3.0, "muted": False}]
        expect("ea-a", [edit_mod._doc_hash("webtoon", clips) == edit_mod._doc_hash("webtoon", clips, {"tracks": [], "a1": ea.default_a1()}),  # noqa: SLF001
                        edit_mod._doc_hash("webtoon", clips) == edit_mod._doc_hash("webtoon", clips, {"tracks": clean, "a1": ea.default_a1()}),  # noqa: SLF001
                        edit_mod._doc_mix({"clips": []}), ea.mix_active([_track("a2", [])], ea.default_a1())],  # noqa: SLF001
               [True, False, {"tracks": [], "a1": ea.default_a1()}, False],
               "트랙 없는 문서 해시는 215a 전과 같다 · 아이템이 있으면 바뀐다 · 빈 트랙만은 결과를 바꾸지 않는다")
    except Exception as exc:  # noqa: BLE001
        crash("ea-a", exc)

    # ================= (ea-c) 키프레임 포락선 =================
    try:
        item = _item(gainDb=-3, keys=[{"t": 1.0, "db": 0.0}, {"t": 2.0, "db": -20.0}], fadeIn=0.5, fadeOut=1.0)
        pts = ea.key_points(item)
        expect("ea-c", [pts, round(ea.points_value(pts, 0.2), 3), round(ea.points_value(pts, 1.5), 3), round(ea.points_value(pts, 3.5), 3)],
               [[(1.0, -3.0), (2.0, -23.0)], -3.0, -13.0, -23.0], "꺾은선 = 이득 + 키(앞 · 뒤는 끝 값, 사이는 직선)")
        expect("ea-c", [ea.fade_gain(item, 0.0), ea.fade_gain(item, 0.25), ea.fade_gain(item, 2.0), ea.fade_gain(item, 4.0),
                        round(ea.item_gain(item, 1.5), 4)],
               [0.0, 0.5, 1.0, 0.0, round(10 ** (-13 / 20), 4)], "페이드는 직선(끝점 0 = −inf dB) · 이득 = 10^(dB/20) × 페이드")
        expr = ea.piecewise_expr(pts)
        diffs = [abs(_eval_expr(expr, t) - ea.points_value(pts, t)) for t in (0, 0.5, 1, 1.25, 1.5, 2, 3)]
        step = ea.piecewise_expr([(0.0, 0.0), (1.0, 0.0), (1.0, -6.0)])
        expect("ea-c", [max(diffs) < 1e-3, "if(" in expr, _eval_expr(step, 0.99), _eval_expr(step, 1.0),
                        ea.db_volume([(0.0, -6.0)]), ea.db_volume(pts).endswith(":eval=frame")],
               [True, False, 0.0, -6.0, "volume=-6.000dB", True], "ffmpeg 식(clip 합 · 중첩 if 없음)이 꺾은선과 같다 · 상수는 dB 한 줄")
    except Exception as exc:  # noqa: BLE001
        crash("ea-c", exc)

    # ================= (ea-d) 덕킹 =================
    try:
        expect("ea-d", [ea.duck_points([[2.0, 4.0]], 8), ea.duck_points([[2.0, 4.0], [4.2, 5.0]], 8), ea.duck_points([[0.05, 1.0]], 6),
                        ea.duck_points([], 8), ea.duck_points([[1, 2]], 0)],
               [[(1.85, 0.0), (2.0, -8.0), (4.0, -8.0), (4.15, 0.0)], [(1.85, 0.0), (2.0, -8.0), (5.0, -8.0), (5.15, 0.0)],
                [(0.0, 0.0), (0.05, -6.0), (1.0, -6.0), (1.15, 0.0)], [], []],
               "대사 0.15초 전부터 −N dB · 끝나고 0.15초에 0 dB · 0.3초 안 쉼은 잇는다 · 0초 앞은 자른다")
        plan = ea.mix_plan([_track("a2", [_item()])], {"duck": True, "duckDb": 8, "gainDb": 0, "mute": False, "solo": False},
                           total_samples=48000 * 4, spans=[[1, 2]])
        muted = ea.mix_plan([_track("a2", [_item()])], {"duck": True, "duckDb": 8, "gainDb": 0, "mute": True, "solo": False},
                            total_samples=48000 * 4, spans=[[1, 2]])
        expect("ea-d", [len(plan["duck"]), muted["duck"], muted["a1On"]], [4, [], False], "A1 이 꺼져 있으면 덕킹도 없다")
    except Exception as exc:  # noqa: BLE001
        crash("ea-d", exc)

    # ================= (ea-e) 2트랙 섞기 그래프 + ffmpeg 실측 =================
    try:
        tracks = [_track("a2", [_item(id="b", out=6.0, fadeIn=0.5, fadeOut=0.5)], gainDb=-6),
                  _track("a3", [_item(id="s", assetId=A2, start=1.0, out=2.0, gainDb=3, keys=[{"t": 0, "db": 0}, {"t": 1, "db": -20}])])]
        a1 = {"gainDb": 0.0, "mute": False, "solo": False, "duck": True, "duckDb": 8.0}
        plan = ea.mix_plan(tracks, a1, total_samples=6 * 48000, spans=[[2.0, 4.0]], stems=True)
        graph, outs = ea.mix_graph(plan)
        want_bits = ["afade=t=in:st=0:d=0.5000:curve=tri", "afade=t=out:st=5.5000:d=0.5000:curve=tri", "adelay=delays=48000S:all=1",
                     "volume='pow(10,(3.000-20.000*clip((t-0.0000)/1.0000,0,1))/20)':eval=frame", "volume=-6.000dB",
                     "amix=inputs=3:normalize=0", "alimiter=limit=0.79:", "level=0", "aresample=192000", "atrim=end_sample=288000",
                     "-8.000*clip((t-1.8500)/0.1500,0,1)"]
        expect("ea-e", [[b for b in want_bits if b not in graph], [k for _l, k in outs]],
               [[], ["raw", "mix", "대사만.wav", "배경음.wav", "효과음.wav"]], "필터 그래프 글(페이드 · 키 · 덕킹 · 리미터 · 길이 고정)")
        try:
            ffmpeg = stitcher.ffmpeg_path()
        except Exception:  # noqa: BLE001
            ffmpeg = ""
        if not ffmpeg:
            warns.append("ea-e: ffmpeg 가 없어 섞기 실측을 건너뜁니다")
        else:
            work = tempfile.mkdtemp(prefix="ea215a-")
            try:
                a1_raw = os.path.join(work, "a1.raw")
                bgm = os.path.join(work, "bgm.wav")
                sfx = os.path.join(work, "sfx.mp3")
                run = lambda cmd: subprocess.run([ffmpeg, "-y", "-hide_banner", "-loglevel", "error"] + cmd, check=True,  # noqa: E731
                                                 capture_output=True)
                run(["-f", "lavfi", "-i", "aevalsrc=0.5*sin(2*PI*300*t)*between(t\\,2\\,4):s=48000:d=6", "-ac", "2", "-f", "s16le",
                     "-c:a", "pcm_s16le", a1_raw])
                run(["-f", "lavfi", "-i", "anoisesrc=d=8:c=pink:a=0.3:r=48000:seed=7", "-ac", "2", bgm])
                run(["-f", "lavfi", "-i", "sine=f=1000:d=3:sample_rate=44100", "-ac", "1", sfx])
                spans = edit_mod.sound_ranges(ea.pcm_peaks(a1_raw))
                plan = ea.mix_plan([_track("a2", [_item(id="b", out=6.0, fadeIn=0.5, fadeOut=0.5)]),
                                    _track("a3", [_item(id="s", assetId=A2, start=1.0, out=2.0, gainDb=3)])],
                                   a1, total_samples=6 * 48000, spans=spans, stems=True)
                res = ea.render_mix(plan, a1_raw, {A1: bgm, A2: sfx}, work)
                stems = dict(res["stems"])

                def level(path: str, a: float, b: float, raw: bool = False) -> float:
                    out = os.path.join(work, "lv.raw")
                    pre = ["-f", "s16le", "-ar", "48000", "-ac", "2"] if raw else []
                    run(pre + ["-i", path, "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-c:a", "pcm_f32le", "-f", "f32le", out])
                    import array  # noqa: PLC0415

                    data = array.array("f")
                    with open(out, "rb") as handle:
                        data.frombytes(handle.read())
                    rms = math.sqrt(sum(v * v for v in data) / max(1, len(data)))
                    return 20 * math.log10(max(rms, 1e-12))

                bg = stems["배경음.wav"]
                duck_db = level(bg, 0.8, 1.6) - level(bg, 2.5, 3.5)
                edge_in, edge_out, body = level(bg, 0.0, 0.004), level(bg, 5.996, 6.0), level(bg, 0.8, 1.6)
                probe = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-f", "s16le", "-ar", "48000", "-ac", "2", "-i", res["raw"],
                                        "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True)
                text = probe.stderr.decode("utf-8", "replace")
                tp = text.rsplit("True peak:", 1)[-1].split("Peak:", 1)[-1].split("dBFS", 1)[0].strip()
                true_peak = float(tp) if tp.replace(".", "", 1).replace("-", "", 1).isdigit() else 99.0
                import array  # noqa: PLC0415

                samples = array.array("h")
                with open(res["raw"], "rb") as handle:
                    samples.frombytes(handle.read())
                peak16 = max(max(samples), -min(samples))
                expect("ea-e", [spans, 6.5 <= duck_db <= 9.5, edge_in < body - 30, edge_out < body - 30, true_peak <= -1.0, peak16 < 32767,
                                os.path.getsize(res["raw"]), sorted(stems)],
                       [[[2.0, 4.0]], True, True, True, True, True, 6 * 48000 * 4, sorted(["대사만.wav", "배경음.wav", "효과음.wav", "mix.wav"])],
                       "실측: 대사 구간 = 2~4초 · 덕킹 6.5~9.5 dB · 페이드 끝 −inf(본문보다 30 dB 아래) · 참 피크 ≤ −1 dBTP · 잘림 없음 · 길이 고정")
                notes.append(f"ea-e 실측: 덕킹 {duck_db:.2f} dB, 페이드 끝 {edge_in:.1f}/{edge_out:.1f} dB(본문 {body:.1f}), "
                             f"참 피크 {true_peak:.1f} dBTP, s16 최대 {peak16}")
                # 리미터가 실제로 일하는 경우: 잡음 +12 dB · 트랙 +12 dB(앞 단계에서 0 dBFS 를 훨씬 넘는다)
                hot = ea.mix_plan([_track("a2", [_item(id="b", out=6.0, gainDb=12)], gainDb=12)], a1, total_samples=6 * 48000,
                                  spans=spans, stems=False)
                hot_dir = os.path.join(work, "hot")
                os.makedirs(hot_dir)
                hot_res = ea.render_mix(hot, a1_raw, {A1: bgm}, hot_dir)
                probe = subprocess.run([ffmpeg, "-hide_banner", "-nostats", "-f", "s16le", "-ar", "48000", "-ac", "2", "-i", hot_res["raw"],
                                        "-af", "ebur128=peak=true", "-f", "null", "-"], capture_output=True)
                tp = probe.stderr.decode("utf-8", "replace").rsplit("True peak:", 1)[-1].split("Peak:", 1)[-1].split("dBFS", 1)[0].strip()
                hot_tp = float(tp) if tp.replace(".", "", 1).replace("-", "", 1).isdigit() else 99.0
                hot_samples = array.array("h")
                with open(hot_res["raw"], "rb") as handle:
                    hot_samples.frombytes(handle.read())
                hot_peak = max(max(hot_samples), -min(hot_samples))
                expect("ea-e", [hot_tp <= -1.0, hot_peak < 32767, hot_peak > 20000, hot_res["stems"]], [True, True, True, []],
                       "+24 dB 로 민 트랙도 리미터 뒤 참 피크 ≤ −1 dBTP · 잘림 없음(그리고 리미터가 소리를 지우지 않는다)")
                notes.append(f"ea-e 리미터(+24 dB): 참 피크 {hot_tp:.1f} dBTP, s16 최대 {hot_peak}")
            finally:
                shutil.rmtree(work, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001
        crash("ea-e", exc)

    # ================= (ea-b · ea-f · ea-i) 라우트 · 내보내기 =================
    try:
        with cv._LogCapture(), cv._EdtEnv() as env, cv._pin_vnew(VNEW_EDIT=True):  # noqa: SLF001
            client = env.client()
            pid = "prj_ea"
            cv._edt_cut_plan(env, pid)  # noqa: SLF001
            base = f"/api/vnew/p/{pid}/edit"
            view = client.get(base).get_json() or {}
            env.set(edit_mod, "_probe_audio", lambda path: {"duration": 2.0, "hasAudio": True})
            env.set(edit_mod, "_audio_peaks_file", lambda path, workdir: [0.5] * 40)

            def up(name: str, data: bytes):
                return client.post(base + "/audio-assets", data={"file": (io.BytesIO(data), name)}, content_type="multipart/form-data")

            r_ok = up("배경음.wav", _wav())
            body = r_ok.get_json() or {}
            aid = (body.get("asset") or {}).get("assetId")
            r_type = up("a.exe", _wav())
            r_fake = up("a.mp3", b"not audio at all, just text")
            env.set(ea, "MAX_ASSET_BYTES", 1000)
            r_big = up("big.wav", _wav())
            env.set(ea, "MAX_ASSET_BYTES", 50 * 1024 * 1024)
            env.set(edit_mod, "_probe_audio", lambda path: {"duration": 700.0, "hasAudio": True})
            r_long = up("long.wav", _wav())
            env.can_write = False
            r_owner = up("x.wav", _wav())
            r_list_other = client.get(base + "/audio-assets")
            env.can_write = True
            listing = client.get(base + "/audio-assets").get_json() or {}
            one = client.get(base + f"/audio-assets/{aid}").get_json() or {}
            file_r = client.get(base + f"/audio-assets/{aid}/file")
            stored = [p for p in env.store.puts if "/edit-audio/" in p[0]]
            expect("ea-b", [r_ok.status_code, bool(edit_mod.edit_audio.valid_asset_id(aid)), r_type.status_code,
                            ((r_type.get_json() or {}).get("error") or {}).get("type"), r_fake.status_code, r_big.status_code,
                            r_long.status_code, ((r_long.get_json() or {}).get("error") or {}).get("type"), r_owner.status_code,
                            r_list_other.status_code, [a["name"] for a in listing.get("assets") or []], len(one["peaks"]["values"]),
                            file_r.status_code, file_r.data == _wav(), [(p[1], p[2]) for p in stored]],
                   [201, True, 400, "BadAudioType", 400, 413, 400, "AudioTooLong", 403, 403, ["배경음"], 40, 200, True,
                    [("edt-video-bucket", "audio/wav")]],
                   "올리기: wav 201 · 확장자 · 내용 400 · 크기 413 · 10분 넘음 400 · 소유자만 · 목록 · 피크 · 같은 출처 파일 · 영상 버킷")
            # 문서에 넣고 저장 → 지우기 409 → 빼고 저장 → 지우기 200
            clips = view["doc"]["clips"]
            item = {"id": "s1", "assetId": aid, "start": 0.5, "in": 0.0, "out": 2.0, "gainDb": -3, "fadeIn": 0.2, "fadeOut": 0.3,
                    "keys": [{"t": 1.0, "db": -6}]}
            put = client.put(base, json={"etag": "", "variant": view["variant"], "clips": clips,
                                         "audioTracks": [{"id": "a2", "name": "A2 배경음", "items": [item]}],
                                         "a1": {"gainDb": -2, "duck": True, "duckDb": 8}})
            saved = put.get_json() or {}
            del_used = client.delete(base + f"/audio-assets/{aid}")
            bad_put = client.put(base, json={"etag": saved.get("etag"), "variant": view["variant"], "clips": clips,
                                             "audioTracks": [{"id": "a2", "items": [dict(item, assetId="a" + "f" * 16)]}]})
            expect("ea-b", [put.status_code, sorted(saved.get("doc", {}).keys()), saved.get("doc", {}).get("a1"),
                            del_used.status_code, ((del_used.get_json() or {}).get("error") or {}).get("type"), bad_put.status_code,
                            ((bad_put.get_json() or {}).get("error") or {}).get("type"),
                            (client.get(base + "/audio-assets").get_json() or {}).get("assets", [{}])[0].get("used")],
                   [200, ["a1", "audio", "audioTracks", "clips", "updatedAt", "variant", "version"],
                    {"gainDb": -2.0, "mute": False, "solo": False, "duck": True, "duckDb": 8.0}, 409, "AudioAssetInUse", 400,
                    "AudioAssetMissing", True], "저장: audioTracks · a1 · 쓰는 소리 지우기 409 · 목록에 없는 소리 400 · used 표시")
            # (ea-f) 스템과 함께 내보내기(끝까지 — 섞기는 로컬 ffmpeg, 없으면 가짜)
            have_ff = True
            try:
                stitcher.ffmpeg_path()
            except Exception:  # noqa: BLE001
                have_ff = False
            if not have_ff:
                warns.append("ea-f: ffmpeg 가 없어 내보내기 섞기를 가짜로 바꿉니다")

                def fake_tracks(plan, a1_raw, paths, workdir):
                    raw = os.path.join(workdir, "mix.raw")
                    shutil.copyfile(a1_raw, raw)
                    outs = []
                    for name in ea.stem_list(plan):
                        path = os.path.join(workdir, "stem-" + str(len(outs)) + ".wav")
                        with open(path, "wb") as handle:
                            handle.write(_wav(0.1))
                        outs.append((name, path))
                    return {"raw": raw, "stems": outs}

                env.set(edit_mod, "_export_tracks", fake_tracks)
            env.inline = True
            exp = client.post(base + "/export", json={"etag": saved.get("etag"), "variant": view["variant"], "stems": True})
            state = env.export_state(pid)
            cur = state.get("current") or {}
            zkey = cur.get("stemsKey") or ""
            names = []
            if zkey:
                with zipfile.ZipFile(io.BytesIO(env.store.get_bytes(zkey, bucket="edt-video-bucket"))) as archive:
                    names = archive.namelist()
            again = client.get(base).get_json() or {}
            stems_r = client.get(base + "/stems?variant=" + view["variant"]).get_json() or {}
            expect("ea-f", [exp.status_code, cur.get("status"), cur.get("error"), cur.get("hasAudio"), names, cur.get("stems"),
                            bool((again.get("export") or {}).get("stemsUrl")), (again.get("export") or {}).get("matchesDoc"),
                            stems_r.get("stems"), env.joins[-1]["audioBytes"] if env.joins else None],
                   [202, "done", "", True, ["대사만.wav", "배경음.wav", "mix.wav"], ["대사만.wav", "배경음.wav", "mix.wav"], True, True,
                    ["대사만.wav", "배경음.wav", "mix.wav"], (env.joins[-1]["frames"] * 2000 * 4) if env.joins else None],
                   "내보내기: 섞은 소리로 잇기 · 스템 zip 이름 · 상태 · 보기 stemsUrl · GET /stems")
            # 216-fix1b: 내려받기 zip 의 mp3 = 잇기에 넣은 바로 그 섞은 소리(같은 PCM 길이) · zip 세 항목 · 내려받기 URL 은 zip
            pkg = env.packages[-1] if env.packages else {}
            expect("ea-f", [pkg.get("audioBytes"), pkg.get("samples"), cur.get("files"),
                            str((again.get("export") or {}).get("downloadUrl") or "").split("?")[0].endswith(".zip")],
                   [env.joins[-1]["audioBytes"] if env.joins else None, (env.joins[-1]["frames"] * 2000) if env.joins else None,
                    [f"edit-{cur.get('n')}-영상(소리없음).mp4", f"edit-{cur.get('n')}-대사.mp3", f"edit-{cur.get('n')}-전체 소리.mp3", "README.txt"], True],
                   "216-fix2: zip(소리 없는 mp4 · 대사 mp3 · 전체 소리 mp3 · README; 가짜 스템이라 배경음 없음) — 전체 mp3 원천 = 섞은 PCM")
            plan_f = ea.mix_plan([_track("a2", [_item()]), _track("a3", [_item(id="x")], mute=True)], ea.default_a1(),
                                 total_samples=48000, stems=True)
            plan_s = ea.mix_plan([_track("a2", [_item()]), _track("a3", [_item(id="x")], solo=True)], ea.default_a1(),
                                 total_samples=48000, stems=True)
            expect("ea-f", [ea.stem_list(plan_f), ea.stem_list(plan_s),
                            ea.stem_list(ea.mix_plan([], ea.default_a1(), total_samples=48000, stems=False))],
                   [["대사만.wav", "배경음.wav", "mix.wav"], ["효과음.wav", "mix.wav"], []], "스템 목록은 들리는 트랙만(mute · solo) · 안 고르면 없음")
            # 옛 문서(트랙 없음) 저장 · 내보내기 상태는 215a 전 모양
            put_old = client.put(base, json={"etag": saved.get("etag"), "variant": view["variant"], "clips": clips, "audio": []})
            doc_old = (put_old.get_json() or {}).get("doc") or {}
            del_free = client.delete(base + f"/audio-assets/{aid}")
            expect("ea-a", [put_old.status_code, sorted(doc_old.keys()), del_free.status_code],
                   [200, ["audio", "clips", "updatedAt", "variant", "version"], 200], "옛 바디 저장 = 215a 전 문서 모양 · 빼면 지울 수 있다")
            expect("ea-i", env.urlopen_calls, [], "검사 중 urlopen(외부 호출) 0")
    except Exception as exc:  # noqa: BLE001
        crash("ea-b", exc)

    try:
        src = (ROOT / "app/src/vnew/edit_audio.py").read_text(encoding="utf-8")
        imports = [line.strip() for line in src.splitlines() if line.strip().startswith(("import ", "from "))]
        banned = [w for w in ("fal", "gateway", "bedrock", "boto3", "urllib", "requests", "retake", "generate") if
                  any(w in line for line in imports)]
        expect("ea-i", [banned, "invoke_model" in src], [[], False], "edit_audio 는 모델 · fal · 네트워크를 가져오지 않는다")
    except Exception as exc:  # noqa: BLE001
        crash("ea-i", exc)

    # ================= (ea-g · ea-h) 화면(node) =================
    node = _node_bin(argv)
    if not node:
        for group in NODE_GROUPS:
            seen.add(group)
        warns.append("node 가 없어 ea-g · ea-h 화면 실행을 건너뜁니다")
    else:
        try:
            vjs = (ROOT / "frontend/public/vnew.js").read_text(encoding="utf-8")
            checked = subprocess.run([node, "--check", str(ROOT / "frontend/public/vnew.js")], capture_output=True, text=True,
                                     encoding="utf-8")
            expect("ea-h", [checked.returncode, vjs.count(JS_START), vjs.count(JS_END)], [0, 1, 1], "node --check · 215a 구역 표시")
            code, problems = cv._edu_code(vjs, cv._EDU_SLICES)  # noqa: SLF001
            expect("ea-h", problems, [], "⑦ 구역 자르기")
            if not problems:
                got, err = cv._lr_run_node(node, cv._EDU_NODE_HARNESS + code + _NODE_TEST, "ea215a_")  # noqa: SLF001
                if err:
                    bad["ea-h"].append("node 실행 실패: " + err)
                    seen.add("ea-h")
                else:
                    py_item = _item(gainDb=-3, keys=[{"t": 1.0, "db": 0.0}, {"t": 2.0, "db": -20.0}], fadeIn=0.5, fadeOut=1.0)
                    want_gain = [round(ea.item_gain(py_item, t), 4) for t in (0.0, 0.25, 1.5, 3.5)]
                    want_duck = [list(p) for p in ea.duck_points([[2.0, 4.0], [4.2, 5.0]], 8)]
                    for name, want in _NODE_WANT.items():
                        expect("ea-g" if name.startswith("undo") else "ea-h", got.get(name), want, f"node {name}")
                    expect("ea-h", [got.get("gainParity"), got.get("duckParity")], [want_gain, want_duck], "JS 이득 · 덕킹 = 파이썬")
        except Exception as exc:  # noqa: BLE001
            crash("ea-h", exc)

    # ================= (ea-j) LF =================
    try:
        crlf = [p for p in CHANGED if (ROOT / p).exists() and b"\r\n" in (ROOT / p).read_bytes()]
        expect("ea-j", crlf, [], "바꾼 파일은 LF 줄 끝")
    except Exception as exc:  # noqa: BLE001
        crash("ea-j", exc)

    ok = True
    for group, title in GROUPS.items():
        if group not in seen:
            print(f"[WARN] 작업 215a ({group}) {title} — 돌지 않음")
            continue
        if bad[group]:
            ok = False
            print(f"[FAIL] 작업 215a ({group}) {title}")
            for line in bad[group][:3]:
                print("    " + line[:600])
        else:
            print(f"[ok  ] 작업 215a ({group}) {title}")
    for line in warns:
        print(f"[WARN] 작업 215a {line}")
    for line in notes:
        print(f"       {line}")
    return ok


_NODE_TEST = r"""
var OUT = {};
var changedN = 0;
function editChanged(ctx) { changedN += 1; }
function mkItem(o) { return Object.assign({ id: "i1", assetId: "a1111111111111111", start: 0, in: 0, out: 4, gainDb: 0, fadeIn: 0, fadeOut: 0, keys: [] }, o || {}); }
var asset = { assetId: "a1111111111111111", duration: 5, name: "bgm" };
var mix0 = editMixNorm({});
OUT.oldDoc = [mix0.tracks.length, editMixEmpty(mix0), JSON.stringify(editMixSaveFields(mix0))];
var m1 = editMixAddTrack(mix0);
var m2 = editMixAddItem(m1, 0, asset, 1.5, 300);
OUT.addItem = [m1.tracks[0].name, m2.tracks[0].items.length, m2.tracks[0].items[0].start, m2.tracks[0].items[0].out, m2.tracks[0].items[0].id];
var full = m2; for (var n = 0; n < 5; n++) full = editMixAddTrack(full) || full;
OUT.maxTracks = [full.tracks.length, editMixAddTrack(full) === null];
var it = m2.tracks[0].items[0];
var tIn = editMixTrimItem(it, "in", 2.0, 5);
var tOut = editMixTrimItem(it, "out", 10, 5);
var tMin = editMixTrimItem(it, "out", 1.0, 5);
OUT.trim = [[tIn.start, tIn.in, tIn.out], [tOut.out], [tMin.out]];
var k = editMixKeyAdd(mkItem({ gainDb: -3 }), 1.0);
var k2 = editMixKeyAdd(k[0], 3.0, -12);
var moved = editMixKeyMove(k2[0], 1, 10, 99);
OUT.keys = [k[0].keys, k2[1], moved.keys[1], editMixKeyRemove(moved, 0).keys.length];
var ms = editMixToggle(editMixToggle(m2, 0, "mute"), -1, "solo");
OUT.muteSolo = [editMixAudible(m2), editMixAudible(editMixToggle(m2, 0, "mute")), editMixAudible(ms)];
var sp = editMixSplitAll(editMixNorm({ audioTracks: [{ id: "a2", items: [mkItem({ fadeIn: 1, fadeOut: 1, keys: [{ t: 1, db: -6 }, { t: 3, db: 0 }] })] }] }), 2);
OUT.split = sp ? sp.tracks[0].items.map(function (i) { return [i.id, i.start, i.in, i.out, i.fadeIn, i.fadeOut, i.keys]; }) : null;
OUT.splitEdge = editMixSplitAll(m2, 1.51);
OUT.snap = [editMixSnap(2.03, [0, 2, 5], 0.05), editMixSnap(2.2, [0, 2, 5], 0.05), editMixSnap(2.03, [2], 0)];
OUT.snapPts = editMixSnapPoints([{ id: "c", sourceKey: "k", in: 0, out: 3 }], m2, 4, "").sort(function (a, b) { return a - b; });
OUT.save = editMixSaveFields(editMixToggle(m2, -1, "duck"));
var py = mkItem({ gainDb: -3, keys: [{ t: 1, db: 0 }, { t: 2, db: -20 }], fadeIn: 0.5, fadeOut: 1 });
OUT.gainParity = [0, 0.25, 1.5, 3.5].map(function (t) { return Math.round(editMixGainAt(py, t) * 10000) / 10000; });
OUT.duckParity = editMixDuckPoints([[2, 4], [4.2, 5]], 8).map(function (p) { return [Math.round(p[0] * 10000) / 10000, p[1]]; });
OUT.dialogue = editMixDialogueSpans([{ id: "a", sourceKey: "k", in: 1, out: 3 }, { id: "b", sourceKey: "k", in: 0, out: 2, speed: 2 }],
  function () { return [[0.5, 1.5], [2.5, 4]]; });
// 실행 취소: 오디오 몸짓마다 한 단계, 클립 몸짓과 같은 스택, 빈 mix 는 215a 전 스냅샷
var ctx = { variant: "webtoon", clips: [{ id: "c1", sourceKey: "k", in: 0, out: 3, muted: false }], hist: { undo: [], redo: [] }, mix: editMixNorm({}),
  mixSel: null, mixKey: null, dom: {}, sounds: { byId: {}, peaks: {}, peakFail: {}, buffers: {} } };
editMixCommit(ctx, editMixAddTrack(ctx.mix));
editMixCommit(ctx, editMixAddItem(ctx.mix, 0, asset, 0, 300), { t: 0, i: 0 });
editMixCommit(ctx, editMixReplaceItem(ctx, 0, 0, editMixTrimItem(ctx.mix.tracks[0].items[0], "out", 2, 5)), { t: 0, i: 0 });
editCommit(ctx, [{ id: "c1", sourceKey: "k", in: 0, out: 2, muted: false }], {});
OUT.undoStack = [ctx.hist.undo.length, ctx.hist.undo[0].mix === undefined, ctx.hist.undo[3].mix.tracks[0].items[0].out, changedN];
editUndoRedo(ctx, false);
OUT.undoClip = [ctx.clips[0].out, ctx.mix.tracks[0].items[0].out];
editUndoRedo(ctx, false);
OUT.undoTrim = ctx.mix.tracks[0].items[0].out;
editUndoRedo(ctx, false); editUndoRedo(ctx, false);
OUT.undoAll = [ctx.mix.tracks.length, ctx.hist.redo.length];
editUndoRedo(ctx, true);
OUT.undoRedo = ctx.mix.tracks.length;
ctx.mixSel = null; ctx.mix = editMixAddItem(editMixAddTrack(editMixNorm({})), 0, asset, 0, 300); ctx.hist = { undo: [], redo: [] };
ctx.mixSel = { t: 0, i: 0 };
OUT.undoDelete = [editMixDelete(ctx), ctx.mix.tracks[0].items.length, ctx.hist.undo.length, editMixDelete(ctx)];
OUT.keyAction = [editKeyAction("n", "KeyN", false, false), editKeyAction("ㅜ", "KeyN", false, false), editKeyAction("s", "KeyS", false, false)];
OUT.exportBody = editMixExportBody({ etag: "e1", variant: "real" });
console.log(JSON.stringify(OUT));
"""

_NODE_WANT = {
    "oldDoc": [0, True, "{}"],
    "addItem": ["A2 배경음", 1, 1.5, 5, "s1"],
    "maxTracks": [4, True],
    "trim": [[2, 0.5, 5], [5], [0.04]],
    "keys": [[{"t": 1, "db": 0}], 1, {"t": 4, "db": 12}, 1],
    "muteSolo": [{"a1": True, "tracks": [True]}, {"a1": True, "tracks": [False]}, {"a1": True, "tracks": [False]}],
    "split": [["i1", 0, 0, 2, 1, 0, [{"t": 1, "db": -6}, {"t": 2, "db": -3}]],
              ["s1", 2, 2, 4, 0, 1, [{"t": 0, "db": -3}, {"t": 1, "db": 0}]]],
    "splitEdge": None,
    "snap": [2, 2.2, 2.03],
    "snapPts": [0, 0, 1.5, 3, 4, 6.5],
    "save": {"audioTracks": [{"id": "a2", "name": "A2 배경음", "mute": False, "solo": False, "gainDb": 0,
                              "items": [{"id": "s1", "assetId": "a1111111111111111", "start": 1.5, "in": 0, "out": 5, "gainDb": 0,
                                         "fadeIn": 0, "fadeOut": 0, "keys": []}]}],
             "a1": {"gainDb": 0, "mute": False, "solo": False, "duck": True, "duckDb": 8}},
    "dialogue": [[0, 0.5], [1.5, 2], [2.25, 2.75]],
    "undoStack": [4, True, 2, 4],
    "undoClip": [3, 2],
    "undoTrim": 5,
    "undoAll": [0, 4],
    "undoRedo": 1,
    "undoDelete": [True, 0, 1, False],
    "keyAction": ["snap", "snap", "split"],
    "exportBody": {"etag": "e1", "variant": "real", "stems": True},
}


if __name__ == "__main__":
    passed = check_edit_audio_215a()
    print(HOOK)
    sys.exit(0 if passed else 1)
