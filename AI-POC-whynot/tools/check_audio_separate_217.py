"""작업 217 검사: 섞인 소리 분리하기(⑥ 사운드 · ⑦ 재료). 가짜 fal · 가짜 ffmpeg · 메모리 저장소, 유료 · 네트워크 0.

  (as-a) 키(원본 옆 -sep/) · 견적 단추 글(금액 · 가격 확인 불가 · $0.01 미만)
  (as-b) 시작: dryRun 견적 · confirmUsd 없음 400 · 금액 다름 409 CostMismatch(보내지 않음) · 제출 바디 = 소리 주소 하나 · -vn · 원장 1건 · 진행 중 409
  (as-c) 진행: GET 이 fal 상태를 봄(진행 중 그대로 → 완료) · 줄기 합치기(amix) · voice.wav · bg.wav 영상 버킷 · 임시 입력 지움
  (as-d) 켜고 끄기: 끝나기 전 409 · useVoice · bgOnA2 · edit._separated_audio
  (as-e) ⑦ 내보내기: 끔이면 그대로 · 켬이면 A1 = voice.wav · A2(배경음) 그 클립 자리에 bg.wav · 스템 배경음.wav
  (as-f) ⑥: sound_track(켬 → voice.wav, 끔 → None) · run_apply 훅 · ⑥ 원본 견적
  (as-g) 라우트: 남의 프로젝트 404 · 플래그 끔 404
  (as-h) 화면: node --check · 217 구역 1개 · 훅 줄 · 단추 글(node 없으면 WARN)
  (as-i) LF 줄 끝

실행: python -X utf8 -B tools/check_audio_separate_217.py   (node 를 숨기고 보려면 --no-node)
check_vpoc 훅 줄(직접 넣지 않는다):
    if not __import__("check_audio_separate_217").check_audio_separate_217(): fail("작업 217 섞인 소리 분리 검사 실패")
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
for _path in (str(TOOLS), str(ROOT / "app")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

GROUPS = {
    "as-a": "키 · 견적 단추 글", "as-b": "시작 · 금액 확인 · 소리만", "as-c": "진행 → 완료 · 저장",
    "as-d": "켜고 끄기", "as-e": "⑦ 내보내기 A1 · A2", "as-f": "⑥ 사운드", "as-g": "라우트", "as-h": "화면", "as-i": "LF",
}
JS_START, JS_END = "/* ==== 작업 217 시작 ==== */", "/* ==== 작업 217 끝 ==== */"
FILES = [ROOT / "app/src/vnew" / n for n in ("audio_separate.py", "routes.py", "sound.py", "edit.py")] + \
    [ROOT / "frontend/public/vnew.js", ROOT / "frontend/public/vnew.css", Path(__file__).resolve()]


def _node_bin(argv) -> str:
    if "--no-node" in argv or os.environ.get("CHECK_217_NO_NODE") == "1":
        return ""
    return shutil.which("node") or ""


def check_audio_separate_217(argv=None) -> bool:  # noqa: C901, PLR0915 — 묶음마다 한 곳에서 본다
    argv = list(sys.argv[1:] if argv is None else argv)
    bad: dict[str, list[str]] = {g: [] for g in GROUPS}
    seen: set[str] = set()
    warns: list[str] = []

    def expect(group: str, got, want, what: str) -> None:
        seen.add(group)
        if got != want:
            bad[group].append(f"{what}\n      결과 {str(got)[:400]}\n      기대 {str(want)[:400]}")

    def crash(group: str, exc: BaseException) -> None:
        seen.add(group)
        bad[group].append("예외: " + repr(exc)[:300] + "\n" + traceback.format_exc()[-1200:])

    import check_vpoc as cv  # noqa: PLC0415
    from src.vnew import audio_separate as sep  # noqa: PLC0415
    from src.vnew import edit as edit_mod  # noqa: PLC0415
    from src.vnew import edit_audio as ea  # noqa: PLC0415

    # ================= (as-a) 키 · 견적 =================
    try:
        key = "users/u/video/p/a/b/output.mp4"
        expect("as-a", [sep.sep_prefix(key), sep.voice_key(key), sep.bg_key(key), sep.state_key(key)],
               ["users/u/video/p/a/b/output-sep", "users/u/video/p/a/b/output-sep/voice.wav", "users/u/video/p/a/b/output-sep/bg.wav",
                "users/u/video/p/a/b/output-sep/separate.json"], "결과는 원본 옆 -sep/ 폴더")
        saved = sep._price  # noqa: SLF001
        try:
            sep._price = lambda m: {"unitPrice": 0.02, "unit": "seconds"}  # noqa: SLF001
            known = sep.estimate(3.0)
            sep._price = lambda m: None  # noqa: SLF001
            unknown = sep.estimate(3.0)
            sep._price = lambda m: {"unitPrice": 0.001, "unit": "seconds"}  # noqa: SLF001
            tiny = sep.estimate(3.0)
        finally:
            sep._price = saved  # noqa: SLF001
        expect("as-a", [known["usd"], known["button"], unknown["usd"], unknown["button"], tiny["usd"], tiny["label"]],
               [0.06, "섞인 소리 분리하기 (AI, $0.06)", None, "섞인 소리 분리하기 (AI, 가격 확인 불가)", 0.0, "$0.01 미만"],
               "단추 글 = 섞인 소리 분리하기 (AI, $x) · 모르면 가격 확인 불가")
    except Exception as exc:  # noqa: BLE001
        crash("as-a", exc)

    # ================= (as-b ~ as-g) 서버 =================
    try:
        with cv._LogCapture(), cv._EdtEnv() as env, cv._pin_vnew(VNEW_EDIT=True, VNEW_SOUND=True):  # noqa: SLF001
            client = env.client()
            pid = "prj_as"
            cv._edt_cut_plan(env, pid)  # noqa: SLF001
            env.store.delete_key = lambda k, bucket="": env.store.blobs.pop(k, None)
            api = f"/api/vnew/p/{pid}/audio-separate"
            c1 = cv._edt_key(pid, "c1-webtoon-1")  # noqa: SLF001
            fal_calls, ff_calls, charges = [], [], []
            phase = {"status": "IN_PROGRESS"}

            def fake_fal(method, url, payload, *, where):
                fal_calls.append((method, url, payload))
                if method == "POST":
                    return {"request_id": "req-217"}
                if url.endswith("/status"):
                    return dict(phase)
                return {"vocals": {"url": "https://fal.example/v.wav"}, "drums": {"url": "https://fal.example/d.wav"},
                        "bass": "https://fal.example/b.wav", "other": {"url": "https://fal.example/o.wav"}}

            def fake_ff(args, what):
                ff_calls.append(list(args))
                inputs = [args[i + 1] for i, a in enumerate(args) if a == "-i"]
                parts = []
                for path in inputs:
                    with open(path, "rb") as handle:
                        parts.append(handle.read()[:20])
                with open(args[-1], "wb") as handle:
                    handle.write(b"FF[" + b"+".join(parts) + b"]")

            fetched = {"https://fal.example/v.wav": b"VOICE", "https://fal.example/d.wav": b"DRUMS",
                       "https://fal.example/b.wav": b"BASS", "https://fal.example/o.wav": b"OTHER"}
            env.set(sep, "_fal_call", fake_fal)
            env.set(sep, "_ffmpeg", fake_ff)
            env.set(sep, "_fetch", lambda url: fetched[url])
            env.set(sep, "_price", lambda m: {"unitPrice": 0.02, "unit": "seconds"})
            env.set(sep, "_charge", lambda k, **kw: charges.append((k, kw)))
            env.set(sep, "_fal_ready", lambda: True)
            env.client().get(f"/api/vnew/p/{pid}/edit")  # 재료 길이 캐시

            # (as-b) 시작
            body = {"scope": "edit", "variant": "webtoon", "sourceKey": c1}
            dry = client.post(api, json={**body, "dryRun": True})
            no_confirm = client.post(api, json=body)
            mismatch = client.post(api, json={**body, "confirmUsd": 0.05})
            bad_src = client.post(api, json={**body, "sourceKey": "users/x/other.mp4", "dryRun": True})
            silent = client.post(api, json={**body, "sourceKey": cv._edt_key(pid, "c3-webtoon-1"), "dryRun": True})  # noqa: SLF001
            calls_before = len(fal_calls)
            started = client.post(api, json={**body, "confirmUsd": 0.06})
            again = client.post(api, json={**body, "confirmUsd": 0.06})
            post = [c for c in fal_calls if c[0] == "POST"]
            extract = ff_calls[0] if ff_calls else []
            err = lambda r: ((r.get_json() or {}).get("error") or {}).get("type")  # noqa: E731
            expect("as-b", [dry.status_code, (dry.get_json() or {}).get("estimate", {}).get("button"), no_confirm.status_code,
                            err(no_confirm), mismatch.status_code, err(mismatch), calls_before, bad_src.status_code, silent.status_code],
                   [200, "섞인 소리 분리하기 (AI, $0.06)", 400, "NoConfirmUsd", 409, "CostMismatch", 0, 400, 400],
                   "견적 · 확인 금액 없음 400 · 금액 다름 409(fal 호출 0) · 목록 밖 재료 400 · 소리 없는 재료 400")
            expect("as-b", [started.status_code, (started.get_json() or {}).get("state", {}).get("status"), again.status_code, err(again),
                            len(post), sorted((post[0][2] or {}).keys()) if post else None, sep.MODEL in (post[0][1] if post else ""),
                            "-vn" in extract, "0:a:0" in extract, "0:v" in " ".join(extract), len(charges),
                            charges[0][0] if charges else None, round(charges[0][1]["usd"], 4) if charges else None],
                   [202, "running", 409, "SeparateRunning", 1, ["audio_url"], True, True, True, False, 1, "vnew-sep-req-217", 0.06],
                   "제출 1번 · 바디 = audio_url 하나(그림 없음) · ffmpeg -vn 소리만 · 원장 1건 · 진행 중 다시 누르면 409")

            # (as-c) 진행 → 완료
            view_run = client.get(api + "?scope=edit&variant=webtoon").get_json() or {}
            st_run = next((i["state"]["status"] for i in view_run.get("items") or [] if i["sourceKey"] == c1), None)
            phase["status"] = "COMPLETED"
            view_done = client.get(api + "?scope=edit&variant=webtoon").get_json() or {}
            item = next((i for i in view_done.get("items") or [] if i["sourceKey"] == c1), {})
            vbucket = "edt-video-bucket"
            sep_puts = [(p[0].rsplit("/", 1)[1][:6], p[1], p[2]) for p in env.store.puts if p[0].startswith(sep.sep_prefix(c1))]
            amix = [a for a in ff_calls if "-filter_complex" in a]
            inputs_left = [k for k in env.store.blobs if k.startswith(sep.sep_prefix(c1) + "/input-")]
            expect("as-c", [st_run, item.get("state", {}).get("status"), env.store.blobs.get(sep.voice_key(c1)),
                            env.store.blobs.get(sep.bg_key(c1)), sep_puts, len(amix), "amix=inputs=3" in " ".join(amix[0]) if amix else None,
                            inputs_left, bool(item.get("state", {}).get("voiceUrl")), item.get("state", {}).get("useVoice")],
                   ["running", "done", b"FF[VOICE]", b"FF[DRUMS+BASS+OTHER]",
                    [("input-", vbucket, "audio/wav"), ("voice.", vbucket, "audio/wav"), ("bg.wav", vbucket, "audio/wav")], 1, True,
                    [], True, False],
                   "진행 중이면 그대로 · 완료면 voice.wav · 줄기 3개 amix → bg.wav(영상 버킷) · 임시 입력 지움 · 기본은 원래 소리")

            # (as-d) 켜고 끄기
            other = cv._edt_key(pid, "c2-webtoon-1")  # noqa: SLF001
            put_early = client.put(api, json={"scope": "edit", "variant": "webtoon", "sourceKey": other, "useVoice": True})
            owner = env.owner
            before = edit_mod._separated_audio(owner, pid, c1)  # noqa: SLF001
            put_on = client.put(api, json={**body, "useVoice": True})
            on = edit_mod._separated_audio(owner, pid, c1)  # noqa: SLF001
            client.put(api, json={**body, "bgOnA2": False})
            no_bg = edit_mod._separated_audio(owner, pid, c1)  # noqa: SLF001
            client.put(api, json={**body, "bgOnA2": True})
            expect("as-d", [put_early.status_code, err(put_early), before, put_on.status_code,
                            (put_on.get_json() or {}).get("state", {}).get("useVoice"), on, no_bg],
                   [409, "NotSeparated", None, 200, True, {"voiceKey": sep.voice_key(c1), "bgKey": sep.bg_key(c1)},
                    {"voiceKey": sep.voice_key(c1), "bgKey": ""}], "끝나기 전 409 · 켬이면 voice · bg 키 · 배경 끔이면 bgKey 빈 값")

            # (as-e) ⑦ 내보내기
            heard, plans = [], []

            def seg(source_path, video_out, **kw):
                if kw.get("audio_source"):
                    with open(kw["audio_source"], "rb") as handle:
                        heard.append((os.path.basename(source_path), handle.read()[:40], kw.get("audio_start_sec")))
                return env._segment(source_path, video_out, **kw)  # noqa: SLF001

            def fake_tracks(plan, a1_raw, paths, workdir):
                contents = {}
                for aid, path in paths.items():
                    with open(path, "rb") as handle:
                        contents[aid] = handle.read()[:40]
                plans.append({"buses": [[(i["assetId"][:1], i["start"], i["in"], i["out"]) for i in b["items"]] for b in plan["buses"]],
                              "stems": ea.stem_list(plan), "contents": sorted(contents.values())})
                raw = os.path.join(workdir, "mix.raw")
                shutil.copyfile(a1_raw, raw)
                outs = []
                for name in ea.stem_list(plan):
                    path = os.path.join(workdir, f"stem-{len(outs)}.wav")
                    with open(path, "wb") as handle:
                        handle.write(b"RIFF")
                    outs.append((name, path))
                return {"raw": raw, "stems": outs}

            env.set(edit_mod, "_export_segment", seg)
            env.set(edit_mod, "_export_tracks", fake_tracks)
            env.inline = True
            base = f"/api/vnew/p/{pid}/edit"
            view = client.get(base).get_json() or {}
            saved = client.put(base, json={"etag": "", "variant": "webtoon", "clips": view["doc"]["clips"]}).get_json() or {}
            client.put(api, json={**body, "useVoice": False})
            exp_off = client.post(base + "/export", json={"etag": saved.get("etag"), "variant": "webtoon"})
            off = [list(heard), list(plans), (env.export_state(pid).get("current") or {}).get("status")]
            client.put(api, json={**body, "useVoice": True})
            exp_on = client.post(base + "/export", json={"etag": saved.get("etag"), "variant": "webtoon", "stems": True})
            cur = env.export_state(pid).get("current") or {}
            expect("as-e", [exp_off.status_code, off], [202, [[], [], "done"]], "끔이면 217 전 그대로(대체 소리 · 섞기 없음)")
            expect("as-e", [exp_on.status_code, cur.get("status"), cur.get("error"), [h[1] for h in heard], [h[2] for h in heard],
                            plans[0]["buses"] if plans else None, plans[0]["contents"] if plans else None, cur.get("stems")],
                   [202, "done", "", [b"FF[VOICE]"], [0.0],
                    [[("s", 0.0, 0.0, 3.0)]], [b"FF[DRUMS+BASS+OTHER]"], ["대사만.wav", "배경음.wav", "mix.wav"]],
                   "켬: c1 클립 A1 = voice.wav · A2 첫 트랙 그 자리(0초, 원본 0~3초)에 bg.wav · 스템 배경음.wav")

            # (as-f) ⑥ 사운드
            final_web = f"users/{cv._EDT_OWNER}/video/{pid}/vnew/gen/g1/final-webtoon.mp4"  # noqa: SLF001
            exports = env.export_state(pid)
            snd_key = (exports.get("current") or {}).get("key") or final_web
            snd_dry = client.post(api, json={"scope": "sound", "variant": "webtoon", "dryRun": True}).get_json() or {}
            tmp = tempfile.mkdtemp(prefix="as217-")
            try:
                none_path = sep.sound_track(owner, pid, snd_key, tmp)
                env.store.blobs[sep.voice_key(snd_key)] = b"SNDVOICE"
                env.store.write_json(sep.state_key(snd_key), {"status": "done", "useVoice": True, "voiceKey": sep.voice_key(snd_key),
                                                              "bgKey": sep.bg_key(snd_key)})
                path = sep.sound_track(owner, pid, snd_key, tmp)
                with open(path or os.devnull, "rb") as handle:
                    got = handle.read()
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
            src = (ROOT / "app/src/vnew/sound.py").read_text(encoding="utf-8")
            expect("as-f", [none_path, got, ((snd_dry.get("estimate") or {}).get("seconds") or 0) > 0,
                            "audio_separate.sound_track(owner, pid, source_key, workdir)" in src, "audio_path or source_path, workdir" in src],
                   [None, b"SNDVOICE", True, True, True],
                   "⑥: 끔이면 원래 소리 · 켬이면 voice.wav 를 반영 소리로(목소리 맞춤도 그 위에) · ⑥ 원본 견적")

            # (as-g) 라우트
            env.can_write = False
            other_r = client.get(api + "?scope=edit&variant=webtoon")
            env.can_write = True
            os.environ[sep.FLAG_ENV] = "0"
            try:
                off_r = client.get(api + "?scope=edit&variant=webtoon")
            finally:
                os.environ.pop(sep.FLAG_ENV, None)
            expect("as-g", [other_r.status_code, off_r.status_code, err(off_r), sep.separated_audio(owner, pid, "users/x/video/y/z.mp4")],
                   [404, 404, "SeparateDisabled", None], "남의 프로젝트 404 · 플래그 끔 404 · 다른 프로젝트 키는 None")
    except Exception as exc:  # noqa: BLE001
        crash("as-b", exc)

    # ================= (as-h) 화면 =================
    try:
        vjs = (ROOT / "frontend/public/vnew.js").read_text(encoding="utf-8")
        css = (ROOT / "frontend/public/vnew.css").read_text(encoding="utf-8")
        expect("as-h", [vjs.count(JS_START), vjs.count(JS_END), css.count(JS_START), css.count(JS_END),
                        vjs.count("sepMountSound(ctx, sourceBox); // 작업 217"), vjs.count('if (typeof sepMountBin === "function") sepMountBin(ctx, bin); // 작업 217')],
                       [1, 1, 1, 1, 1, 1], "구역 1개씩 · 훅 줄 2개")
        node = _node_bin(argv)
        if not node:
            warns.append("node 가 없어 화면 실행(as-h)을 건너뜁니다")
        else:
            checked = subprocess.run([node, "--check", str(ROOT / "frontend/public/vnew.js")], capture_output=True, text=True,
                                     encoding="utf-8", timeout=120, check=False)
            block = vjs[vjs.index(JS_START):vjs.index(JS_END)]
            script = ("var state={projectId:'p'};\n" + block + "\nvar out={a:sepButtonText({estimate:{label:'$0.06'},state:{status:'none'}}),"
                      "b:sepButtonText({estimate:null,state:{}}),c:sepButtonText({estimate:{label:'$0.06'},state:{status:'done'}}),"
                      "d:sepConfirmText({label:'컷 1'},{seconds:3,priceKnown:true,label:'$0.06',modelLabel:'Demucs'}).split('\\n')[1]};"
                      "console.log(JSON.stringify(out));")
            ran = subprocess.run([node, "-e", script], capture_output=True, text=True, encoding="utf-8", timeout=60, check=False)
            got = json.loads(ran.stdout.strip().splitlines()[-1]) if ran.returncode == 0 and ran.stdout.strip() else {"err": ran.stderr[-300:]}
            expect("as-h", [checked.returncode, got],
                   [0, {"a": "섞인 소리 분리하기 (AI, $0.06)", "b": "섞인 소리 분리하기 (AI, 가격 확인 불가)", "c": "다시 분리하기 (AI, $0.06)",
                        "d": "비용: $0.06"}], "node --check · 단추 글 · 확인창 금액")
    except Exception as exc:  # noqa: BLE001
        crash("as-h", exc)

    # ================= (as-i) LF =================
    try:
        expect("as-i", [p.name for p in FILES if b"\r" in p.read_bytes()], [], "LF 줄 끝")
    except Exception as exc:  # noqa: BLE001
        crash("as-i", exc)

    ok_all = True
    for group, title in GROUPS.items():
        if bad[group]:
            ok_all = False
            print(f"[FAIL] 작업 217 {group} {title}")
            for line in bad[group][:3]:
                print("    " + line)
        elif group in seen:
            print(f"[ok  ] 작업 217 {group} {title}")
        else:
            ok_all = False
            print(f"[FAIL] 작업 217 {group} {title}: 돌지 않았습니다")
    for line in warns:
        print(f"[WARN] {line}")
    return ok_all


if __name__ == "__main__":
    sys.exit(0 if check_audio_separate_217() else 1)
