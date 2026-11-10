"""작업 215d 검사: ⑦ 편집 → Premiere Pro 프로젝트(FCP7 XML · CMX3600 EDL · media zip). 가짜 저장소, 유료·네트워크 0.

  (px-a) xmeml: 3 클립(디졸브 0.5초 · 속도 0.5 · L 컷) → XML 파싱 · 24fps 프레임(start/end/in/out/duration) · Time Remap 50 ·
         교차 디졸브 · file id 재사용(내용은 한 번) · 상대 경로 media/ · A1/A2 + 겹친 소리 A3/A4 · 소리 크로스 페이드
  (px-b) EDL: 타임코드 · D 012 두 줄 · M2 012.0 · AA 이벤트(L 컷 길이)
  (px-c) audioTracks: 없음·틀린 모양은 그대로 · 있으면 트랙 2개 더함 · Audio Levels(−6 dB) · Cross Fade (+3dB) 페이드
  (px-d) package(가짜 S3): zip 항목 · media 바이트 · URL · 백그라운드 시작 → done · 같은 문서 다시 = 재사용 · 저장 안 됨 409
  (px-e) 라우트: 남의 프로젝트 404 · 소유자 202/200 · 쓰기 권한 확인 · VNEW_EDIT 끔 404
  (px-f) LF 줄 끝 · src.vnew.routes import

실행: python -X utf8 -B tools/check_edit_xml_215d.py
"""

from __future__ import annotations

import copy
import io
import json
import sys
import traceback
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
for _path in (str(TOOLS), str(ROOT / "app")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

GROUPS = {
    "px-a": "xmeml: 프레임 · 속도 · 디졸브 · file id 재사용 · A1/A2",
    "px-b": "EDL: 타임코드 · D · M2 · AA",
    "px-c": "audioTracks 없음/있음",
    "px-d": "package zip · 백그라운드 · 재사용",
    "px-e": "라우트 소유자 검사",
    "px-f": "LF · routes import",
}
HOOK = "from . import edit_xml; edit_xml.register_routes(bp, deps, _project_for, _fail)  # 작업 215d: ⑦ Premiere 내보내기"
OWNER, PID = "tester", "prj_px"
BASE = f"users/{OWNER}/video/{PID}/"
K1, K2, K3 = BASE + "vnew/gen/g1/a.mp4", BASE + "vnew/gen/g1/b.mp4", BASE + "sound/bgm.mp3"
META = {K1: {"sourceKey": K1, "duration": 6.0, "width": 1280, "height": 720, "fps": 24, "hasAudio": True},
        K2: {"sourceKey": K2, "duration": 6.0, "width": 1280, "height": 720, "fps": 24, "hasAudio": True}}
MEDIA = {K1: {**META[K1], "name": "clip-01.mp4"}, K2: {**META[K2], "name": "clip-02.mp4"}}
CLIPS = [
    {"id": "c1", "sourceKey": K1, "in": 0.0, "out": 2.0, "muted": False, "transition": {"type": "dissolve", "seconds": 0.5}},
    {"id": "c2", "sourceKey": K2, "in": 1.0, "out": 3.0, "muted": False, "speed": 0.5, "audioTail": 0.5},
    {"id": "c3", "sourceKey": K1, "in": 3.0, "out": 5.0, "muted": False},
]
BGM = [{"name": "BGM", "items": [{"sourceKey": K3, "start": 1.0, "in": 0.0, "out": 4.0, "gainDb": -6, "fadeIn": 0.5,
                                  "fadeOut": 1.0}]}]


class _Store:
    """메모리 저장소(조건부 쓰기 흉내)."""

    def __init__(self) -> None:
        self.json: dict = {}
        self.blobs: dict[str, bytes] = {}
        self.n = 0

    def read_json(self, key: str):
        item = self.json.get(key)
        return (copy.deepcopy(item[0]), item[1]) if item else (None, "")

    def write_json(self, key: str, data: dict, *, etag: str = "", create_only: bool = False) -> str:
        from src.vpoc.errors import VpocError  # noqa: PLC0415

        current = self.json.get(key)
        if (create_only and current) or (etag and (not current or current[1] != etag)):
            raise VpocError("store", "PreconditionFailed", "conflict", 409)
        self.n += 1
        self.json[key] = (json.loads(json.dumps(data)), f"e{self.n}")
        return f"e{self.n}"

    def put_bytes(self, key: str, body: bytes, content_type: str, *, bucket: str = "") -> None:
        self.blobs[key] = bytes(body)

    def get_bytes(self, key: str, *, bucket: str = "") -> bytes:
        return self.blobs[key]

    def presign(self, key: str, **kwargs) -> str:
        return "https://signed.example/" + key


def _parse(text: str):
    return ET.fromstring(text.encode("utf-8")).find("sequence")


def _items(track) -> list:
    return [[int(ci.findtext(t)) for t in ("start", "end", "in", "out")] for ci in track.findall("clipitem")]


def _transitions(track) -> list:
    return [[int(t.findtext("start")), int(t.findtext("end")), t.findtext("alignment"), t.findtext("effect/name")]
            for t in track.findall("transitionitem")]


def check_edit_xml_215d() -> bool:  # noqa: C901 — 한 검사 함수(다른 작업 검사와 같은 모양)
    bad: dict[str, list[str]] = {g: [] for g in GROUPS}
    seen: set[str] = set()

    def expect(group: str, got, want, what: str) -> None:
        seen.add(group)
        if got != want:
            bad[group].append(f"{what}\n      결과 {str(got)[:300]}\n      기대 {str(want)[:300]}")

    def crash(group: str, exc: BaseException) -> None:
        seen.add(group)
        bad[group].append("예외: " + repr(exc)[:300] + "\n" + traceback.format_exc()[-1200:])

    from src.vnew import edit as E  # noqa: PLC0415
    from src.vnew import edit_xml as X  # noqa: PLC0415
    from src.vnew import settings as vset  # noqa: PLC0415
    from src.vpoc.errors import VpocError  # noqa: PLC0415

    doc = {"clips": CLIPS}
    # ================= (px-a) xmeml =================
    try:
        seq = _parse(X.build_xmeml(doc, MEDIA, fps=24, width=1280, height=720, name="웹툰 편집 v3"))
        expect("px-a", [seq.findtext("duration"), seq.findtext("rate/timebase"), seq.findtext("rate/ntsc")], ["180", "24", "FALSE"],
               "시퀀스 길이 = 8초 − 디졸브 0.5초 = 180프레임 @24")
        v1 = seq.find("media/video/track")
        expect("px-a", _items(v1), [[0, 42, 0, 42], [42, 132, 54, 144], [132, 180, 72, 120]],
               "V1 start/end/in/out(디졸브 가운데 42 · 0.5배속 in = 1초 × 24 ÷ 0.5 + 6)")
        expect("px-a", [ci.findtext("duration") for ci in v1.findall("clipitem")], ["144", "288", "144"],
               "clipitem duration(속도 반영)")
        expect("px-a", _transitions(v1), [[36, 48, "center", "Cross Dissolve"]], "교차 디졸브 12프레임(36~48)")
        remap = {p.findtext("parameterid"): p.findtext("value") for p in v1.findall("clipitem")[1].findall("filter/effect/parameter")}
        expect("px-a", [v1.findall("clipitem")[1].findtext("filter/effect/effectid"), remap.get("speed")], ["timeremap", "50"],
               "0.5배속 = Time Remap speed 50")
        expect("px-a", [ci.find("file").get("id") for ci in v1.findall("clipitem")], ["file-1", "file-2", "file-1"], "file id 재사용")
        full = [f.get("id") for f in seq.iter("file") if f.find("pathurl") is not None]
        expect("px-a", sorted(full), ["file-1", "file-2"], "file 내용은 id 마다 한 번")
        first = next(f for f in seq.iter("file") if f.get("id") == "file-1" and f.find("pathurl") is not None)
        expect("px-a", [first.findtext("pathurl"), first.findtext("duration")], ["media/clip-01.mp4", "144"], "상대 경로 · 원본 길이")
        tracks = seq.findall("media/audio/track")
        expect("px-a", len(tracks), 4, "소리 트랙: A1/A2 + L 컷으로 겹친 c3 은 A3/A4")
        expect("px-a", [_items(tracks[0]), _transitions(tracks[0])],
               [[[0, 42, 0, 42], [42, 144, 54, 156]], [[36, 48, "center", "Cross Fade (+3dB)"]]],
               "A1: c1 · c2(L 컷 0.5초 = 12프레임 더) · 디졸브 자리 크로스 페이드")
        expect("px-a", _items(tracks[2]), [[132, 180, 72, 120]], "A3: c3")
        expect("px-a", [tracks[0].find("clipitem/sourcetrack/trackindex").text, tracks[1].find("clipitem/sourcetrack/trackindex").text,
                        tracks[1].findtext("outputchannelindex")], ["1", "2", "2"], "A1 = 원본 소리 1, A2 = 2")
    except Exception as exc:  # noqa: BLE001
        crash("px-a", exc)

    # ================= (px-b) EDL =================
    try:
        edl = X.build_edl(doc, MEDIA, fps=24, name="prj_px-webtoon-1").split("\n")
        expect("px-b", edl[:2], ["TITLE: prj_px-webtoon-1", "FCM: NON-DROP FRAME"], "머리")
        want = [
            "001  AX       V     C        00:00:00:00 00:00:01:12 00:00:00:00 00:00:01:12",
            "002  AX       AA    C        00:00:00:00 00:00:01:18 00:00:00:00 00:00:01:18",
            "003  AX       V     C        00:00:01:12 00:00:01:12 00:00:01:12 00:00:01:12",
            "003  AX       V     D    012 00:00:01:00 00:00:05:00 00:00:01:12 00:00:05:12",
            "004  AX       AA    C        00:00:01:03 00:00:05:09 00:00:01:18 00:00:06:00",
            "005  AX       V     C        00:00:03:00 00:00:05:00 00:00:05:12 00:00:07:12",
            "006  AX       AA    C        00:00:03:00 00:00:05:00 00:00:05:12 00:00:07:12",
        ]
        expect("px-b", [line for line in edl if line[:3].isdigit()], want, "이벤트 줄(디졸브 두 줄 · L 컷 소리 6:00 까지)")
        expect("px-b", [line for line in edl if line.startswith("M2")],
               ["M2   AX       012.0                00:00:01:00", "M2   AX       012.0                00:00:01:03"], "M2(0.5배속 = 12fps)")
        expect("px-b", "* TO CLIP NAME: clip-02.mp4" in edl, True, "디졸브 TO CLIP NAME")
    except Exception as exc:  # noqa: BLE001
        crash("px-b", exc)

    # ================= (px-c) audioTracks =================
    try:
        count = lambda d, m=MEDIA: len(_parse(X.build_xmeml(d, m, fps=24, width=1280, height=720, name="s")).findall("media/audio/track"))  # noqa: E731
        expect("px-c", [count(doc), count({**doc, "audioTracks": "x"}), count({**doc, "audioTracks": [{"items": "bad"}, 3]})],
               [4, 4, 4], "audioTracks 없음 · 틀린 모양 → 클립 소리만")
        media = {**MEDIA, K3: {"name": "audio-01.mp3", "hasAudio": True}}
        seq = _parse(X.build_xmeml({**doc, "audioTracks": BGM}, media, fps=24, width=1280, height=720, name="s"))
        tracks = seq.findall("media/audio/track")
        expect("px-c", len(tracks), 6, "audioTracks 1개 → 트랙 2개 더")
        expect("px-c", [_items(tracks[4]), _transitions(tracks[4])],
               [[[24, 120, 0, 96]], [[24, 36, "start-black", "Cross Fade (+3dB)"], [96, 120, "end-black", "Cross Fade (+3dB)"]]],
               "BGM 1초부터 4초 · 페이드 인 0.5초 · 아웃 1초")
        level = tracks[4].find("clipitem/filter/effect")
        expect("px-c", [level.findtext("effectid"), abs(float(level.findtext("parameter/value")) - 10 ** (-6 / 20)) < 1e-4],
               ["audiolevels", True], "Audio Levels −6 dB(선형 0.501)")
        expect("px-c", [f.findtext("pathurl") for f in seq.iter("file") if f.get("id") == "file-3" and f.find("pathurl") is not None],
               ["media/audio-01.mp3"], "추가 소리 file-3")
    except Exception as exc:  # noqa: BLE001
        crash("px-c", exc)

    # ================= (px-d) package · 백그라운드 =================
    saved = {name: getattr(E, name) for name in ("STORE", "_load_gen", "_saved_docs", "_all_items", "_retake_state", "_ensure_meta")}
    saved_spawn, saved_flag = X._spawn, vset.VNEW_EDIT  # noqa: SLF001
    store = _Store()
    box = {"doc": {"version": 3, "variant": "webtoon", "updatedAt": "", "clips": copy.deepcopy(CLIPS), "audioTracks": BGM}}
    try:
        store.blobs.update({K1: b"A" * 4000, K2: b"B" * 3000, K3: b"ID3" + b"m" * 500})
        E.STORE = store
        E._load_gen = lambda owner, pid: ({}, {}, "g1")  # noqa: SLF001
        E._saved_docs = lambda owner, pid, gen_id: {  # noqa: SLF001
            "real": {"doc": None, "etag": "", "source": "", "version": 0},
            "webtoon": {"doc": copy.deepcopy(box["doc"]), "etag": "e1", "source": "own", "version": 3}}
        E._all_items = lambda gen, variant, retakes: [{"sourceKey": K1}, {"sourceKey": K2}]  # noqa: SLF001
        E._retake_state = lambda *a: None  # noqa: SLF001
        E._ensure_meta = lambda owner, pid, gen_id, keys, budget_s=None: ({k: META[k] for k in keys if k in META}, True)  # noqa: SLF001
        X._spawn = lambda owner, pid, gen_id, n, claim: X.run_premiere(owner, pid, gen_id, n, claim)  # noqa: SLF001
        result = X.package(OWNER, PID, "webtoon")
        archive = zipfile.ZipFile(io.BytesIO(store.blobs[result["key"]]))
        project = result["fileName"][:-4]
        expect("px-d", sorted(archive.namelist()), sorted([f"{project}.xml", f"{project}.edl", "README.txt", "media/clip-01.mp4",
                                                          "media/clip-02.mp4", "media/audio-01.mp3"]), "zip 항목")
        expect("px-d", [archive.read("media/clip-01.mp4") == store.blobs[K1], archive.read("media/audio-01.mp3") == store.blobs[K3]],
               [True, True], "media 바이트 = S3 원본")
        readme = archive.read("README.txt").decode("utf-8")
        expect("px-d", ["가져오기" in readme, "미디어 연결" in readme, f"{project}.xml" in readme], [True, True, True], "README 한국어 여는 법")
        expect("px-d", len(_parse(archive.read(f"{project}.xml").decode("utf-8")).findall("media/audio/track")), 6, "zip 안 XML(BGM 트랙 포함)")
        expect("px-d", [result["key"].startswith(BASE + "vnew/") and result["key"].endswith(".zip"), result["url"].startswith("https://signed.example/")],
               [True, True], "영상 버킷 gen 루트 zip · presigned URL")
        first = X.start_premiere(OWNER, PID, "webtoon")
        view = X.premiere_view(OWNER, PID, "webtoon")["premiere"]
        expect("px-d", [first["started"], view["status"], view["n"], view["fileName"], view["url"].endswith("premiere-1.zip")],
               [True, "done", 1, "prj_px-webtoon-1.zip", True], "시작 → 스레드 → done · URL")
        again = X.start_premiere(OWNER, PID, "webtoon")
        expect("px-d", [again["started"], again["premiere"]["status"], again["premiere"]["matchesDoc"]], [False, "done", True],
               "같은 문서 다시 = 끝난 묶음 재사용")
        box["doc"]["clips"][2]["out"] = 4.5
        expect("px-d", [X.start_premiere(OWNER, PID, "webtoon")["started"], X.premiere_view(OWNER, PID, None)["premiere"]["n"]],
               [True, 2], "문서가 바뀌면 새 묶음 n=2")
        try:
            X.start_premiere(OWNER, PID, "real")
            got = ["통과"]
        except VpocError as exc:
            got = [exc.kind, exc.status]
        expect("px-d", got, ["EditNotSaved", 409], "저장 안 된 그림체 409")
    except Exception as exc:  # noqa: BLE001
        crash("px-d", exc)

    # ================= (px-e) 라우트 =================
    try:
        from flask import Blueprint, Flask, jsonify  # noqa: PLC0415

        from src.vpoc.errors import envelope, status_for  # noqa: PLC0415

        writes: list = []

        class _Deps:
            require_auth = staticmethod(lambda fn: fn)

        def project_for(pid: str, *, write: bool) -> str:
            writes.append(write)
            if pid != PID:
                raise VpocError("vnew.plan", "NotFound", "프로젝트를 찾을 수 없습니다.", 404)
            return OWNER

        def fail(exc):
            body = envelope("vnew", exc)
            return jsonify({"error": body}), status_for(body)

        bp = Blueprint("vnew_px", __name__, url_prefix="/api/vnew")
        X.register_routes(bp, _Deps, project_for, fail)
        app = Flask("px")
        app.register_blueprint(bp)
        client = app.test_client()
        url = lambda pid: f"/api/vnew/p/{pid}/edit/premiere"  # noqa: E731
        expect("px-e", [client.post(url("other"), json={}).status_code, client.get(url("other")).status_code], [404, 404],
               "남의 프로젝트 404")
        posted = client.post(url(PID), json={"variant": "webtoon"})
        status = client.get(url(PID) + "?variant=webtoon")
        expect("px-e", [posted.status_code, posted.get_json().get("ok"), status.status_code, status.get_json()["premiere"]["status"]],
               [202, True, 200, "done"], "소유자 POST 202 · GET 200")
        expect("px-e", set(writes), {True}, "두 라우트 모두 쓰기(소유자) 권한으로 확인")
        vset.VNEW_EDIT = False
        expect("px-e", [client.post(url(PID), json={}).status_code, client.get(url(PID)).status_code], [404, 404], "VNEW_EDIT 끔 404")
    except Exception as exc:  # noqa: BLE001
        crash("px-e", exc)
    finally:
        for name, value in saved.items():
            setattr(E, name, value)
        X._spawn, vset.VNEW_EDIT = saved_spawn, saved_flag  # noqa: SLF001

    # ================= (px-f) LF · import =================
    try:
        files = [ROOT / "app" / "src" / "vnew" / "edit_xml.py", Path(__file__).resolve()]
        expect("px-f", [p.name for p in files if b"\r\n" in p.read_bytes()], [], "새 파일 LF 줄 끝")
        import src.vnew.routes as routes_mod  # noqa: PLC0415

        expect("px-f", callable(routes_mod.create_blueprint), True, "src.vnew.routes import")
    except Exception as exc:  # noqa: BLE001
        crash("px-f", exc)

    passed = True
    for group, title in GROUPS.items():
        if bad[group]:
            passed = False
            for item in bad[group]:
                print(f"[FAIL] 작업 215d ({group}) {item}"[:1500])
        elif group not in seen:
            passed = False
            print(f"[FAIL] 작업 215d ({group}) {title}: 검사하지 못했습니다")
        else:
            print(f"[ok  ] 작업 215d ({group}) {title}")
    print(f"[hook] routes.create_blueprint() 안 `return bp` 앞: {HOOK}")
    return passed


if __name__ == "__main__":
    sys.exit(0 if check_edit_xml_215d() else 1)
