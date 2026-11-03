# 117 속마음 대사 — tasks

- [x] 1. 대본 스키마·규칙: entries.voice(spoken|thought) enum, SCRIPT_SYSTEM 규칙 9, 판정 `prompts.is_thought`, `_normalize_script` voice 채움
- [x] 2. 대본 화면: 대사 옆 "속마음" 체크(`thoughtToggle`·`entryVoice`·`isThoughtEntry`), 저장 경로 보존(update_script 그대로)
- [x] 3. 콘티 전달: `_cut_lines` 줄에 thought, 콘티 입력에 `목소리=속마음`, 콘티 카드 표시(`contiThoughtSpeakers`)
- [x] 4. 영상 프롬프트: 컷 DIALOGUE·통 테이크 TIMELINE(보통·최소형)·RULES 문장, 웹툰·실사 공통
- [x] 5. 프리비즈: `_mute_thought_speakers`(thought만 → speaking False)
- [x] 6. AI로 고치기: 지침 2곳 inner 표시 유지, `_trim_protected` inner 보호, 콘티 글 (속마음)
- [x] 7. 검사 `check_vnew_inner_voice` iv-a~f + 변형 1회, node --check, check_vpoc 전체 1회
