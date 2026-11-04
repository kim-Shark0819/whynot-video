# 122 ⑥ 사운드 — 실제 말 / 속마음 (영상은 그대로) — tasks

- [x] 1. 서버 `app/src/vnew/sound.py`: 원본 고르기(`_source` ⑦ 내보내기 done n 최대 → ⑤ 완성본), 사운드 문서 `{gen}/sound/{variant}/sound.json`(source·lines·versions·job), GET 자동 생성(voice = 대본 그대로·auto true, 원본·대본 대사가 바뀌면 다시 만들고 고친 줄 voice 유지)
- [x] 2. 구간 추정 `estimate_spans` 한 곳: 컷 구간(⑤ units/segments 순서·길이, ⑦ 내보내기 clips·재료 cutIds) → 그 안 말소리(silencedetect 상수 문턱)를 줄 순서대로 → 말소리 없으면 컷 고르게 → 컷 모르면 전체 나눔, 최소 0.2초
- [x] 3. PUT(voice·start·end 만, 0 ≤ start < end ≤ 길이·0.2초 이상, 바뀐 줄 auto false, plan 쓰기 없음)·POST apply(백그라운드, running 409, 소리 없음 400 "이 영상에는 소리가 없어 바꿀 수 없습니다", ffmpeg 1번 `-map 0:v:0 -c:v copy` + THOUGHT_FILTER 속마음 구간만·20ms 경계, 새 키 `sound/<variant>/v<n>.mp4`, 끊김 정리)
- [x] 4. `routes.py` 경로 3개(소유자만 — 그 밖 404, VNEW_SOUND 끔 404), `settings.py` `VNEW_SOUND`(기본 true), `deploy/config.env.example` 설명 1줄
- [x] 5. 화면 `vnew.js`·`vnew.css`: 원음/무음 토글·"저장하지 않습니다" 삭제, 원본·반영한 소리 플레이어·버전 고르기·내려받기, 대사 표([실제 말 | 속마음]·자동 표시·시작/끝·[▶ 이 구간 듣기]), [소리에 반영](PUT → apply → 폴링), 안내 1줄, 주석 ⑥
- [x] 6. 검사 `check_vnew_sound_122` snd-a~e(로컬 ffmpeg 합성 4초: 영상 md5 같음·길이 차 ≤ 0.05초·속마음 구간 소리 다름·그 밖 PCM 같음, 변형 1회), node --check, check_vpoc 전체 1회
