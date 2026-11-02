# 작업 — vnew-edit-retake (⑦ 편집: 테이크·컷을 AI로 고쳐 다시 만들기)

> 작성 2026-11-02 · Kiro(claude-opus-5.5) · `requirements.md`·`design.md` 기준. 같은 실행에서 스펙·구현·검사를 했다(지시서 116).
> 완료 조건이 통과한 작업만 체크했다. 커밋·push·배포는 하지 않는다.

- [x] 1. 조사(R1 배경, design §0)
  - ⑤ 제출 경로(`_do_submit_take`·`_do_submit_previz`·`_submit_take`·`_submit_previz_edit`·payload 3개·`retry_segment`·`start_real`)가 칸 상태에 무엇을 남기는지 읽는다.
  - 완료 조건: design §0 표. 됨(마지막 시도의 `promptEn`·`promptBytes`만, 모델은 gen 단위, 매개변수·참고 키·이전 시도는 없음).
- [x] 2. 프롬프트 기록(R1·R2, design D1~D6)
  - `retake.py`: `prompts_key`·`log_submit`·`log_result`·`read_log`·`_mutate_log`·`_log_entry`·`_clean_text`·`_clip_bytes`·`_safe_key`·`_plain_params`. `generate.py`: `_RefTrace`·`_ref_note_load`·`_ref_note_fit`·`_take_params`·`_cut_params`·
    `_log_attempt`·`_log_result`, 관찰 줄(`_load_jpeg`·`_segment_panel_jpegs`·`_fit_ref`·`_do_submit_take`·`_do_submit_previz`·`_do_poll_previz`).
  - 완료 조건: rtk-b·rtk-c, 기존 검사 기대값 그대로. 됨(기존 72개 그대로 통과, 기록 쓰기 경고 0).
- [x] 3. AI 고치기 서버(R3~R7·R9, design D7~D11·D14)
  - `retake.py`: 상태·대상·기준·시도·버전 보기, `start_preview`·`run_draft`·`_result_frames`·`_grab_frame`·`check_prompt`, `submit`·`run_retake`·`reconcile_state`, `estimate`.
    `generate.resubmit_cell`. `routes.py` 새 라우트 3개. `settings.py`·`errors.py`.
  - 완료 조건: rtk-d~h·rtk-l·rtk-m. 됨.
- [x] 4. 편집 통합(R8, design D12)
  - `edit.py`: `_retake_state`·`_all_items`·`_retake_reconcile`·`_describe`·`edit_view`·`save_doc`·`start_export`·`media_view`.
  - 완료 조건: rtk-i, 기존 edt·edu·ert 그대로. 됨.
- [x] 5. 화면(R4·R8, design D13)
  - `vnew.js` ⑦ 구역(재료 줄·고른 클립 버튼·패널·순수 함수·단축키 막음·떠날 때 정리), `vnew.css` 116 구역.
  - 완료 조건: `node --check`, rtk-j·rtk-k, edu·ert 그대로. 됨.
- [x] 6. 배선(R9.1)
  - `workloads.yaml.tmpl`·`config.env.example`·`config.prod.env`, `PARITY_ENV_KEYS`.
  - 완료 조건: rtk-a, rw-a~c·edt-a·term-a·ob-d·pz 그대로. 됨.
- [x] 7. 검사기 배선·전체 검사·커버리지·변형 검사·리뷰·문서(R10, quality-week 4~6)
  - `check_vnew_edit_retake`(rtk-a~m)를 `main()`의 `check_vnew_edit_retime()` 뒤에.
  - 완료 조건: `python -X utf8 tools/check_vpoc.py` "전부 통과", 바뀐 서버 실행문 커버리지 80% 이상, 변형 검사, WORKLOG 116·ANALYSIS, 보안 → 성능 → 운영성 리뷰,
    줄 끝 LF(운영 overlay BOM 그대로)·공백만 바뀐 줄 0·부산물 0(pyc 포함).
  - 결과: 전부 통과(ok 73줄, FAIL·WARN 0, 130초), 바뀐 서버 실행문 1,042/1,046(99.6%)·바뀐 줄 가지 289/304(95.1%), 변형 100개 모두 잡힘, 리뷰·문서는 WORKLOG 116.
- [ ] 8. dev 손 확인(사용자, 배포 뒤): WORKLOG 116 「dev 확인 순서」. 이 실행에서는 하지 않는다(배포·브라우저 없음).


## 보완 1 — 모델 고르기 · 클립 구간만 고치기(지시서 116 보완 1)

> `requirements.md` R11~R13, `design.md` §6 기준. 같은 실행에서 스펙·구현·검사를 한다. 완료 조건이 통과한 작업만 체크한다.

- [ ] 9. 스펙 보완(R11~R13, design §6, 이 목록)
  - 완료 조건: 세 파일에 보완 1 절. 116 절은 그대로.
- [ ] 10. 서버 — 견적·제출(generate.py 새 구역, design D20·D22)
  - `retake_path`·`_retake_resolution`·`_retake_price`·`retake_plan`·`_retake_cell`·`_retake_like`·`_retake_video`·`_pad_clip`·`_retake_payload`·`retake_template`·`_charge_retake`·`resubmit_model`.
  - 완료 조건: rtk-o·rtk-q·rtk-u, 기존 검사 그대로.
- [ ] 11. 서버 — 모델 목록·구간·초안·제출(retake.py, routes.py, errors.py, design D18·D19·D21·D23~D25·D27)
  - 완료 조건: rtk-n·rtk-p·rtk-q·rtk-r·rtk-t, rtk-a~m 그대로.
- [ ] 12. 화면(vnew.js·vnew.css, design D26)
  - 완료 조건: `node --check`, rtk-s·rtk-t, rtk-j·edu·ert 그대로(rtk-k 클래스 수만 새 값, 이유 보고).
- [ ] 13. 검사기 배선·전체 검사·커버리지·변형 검사
  - 완료 조건: `python -X utf8 tools/check_vpoc.py` "전부 통과", 바뀐 서버 실행문 커버리지 80% 이상, 변형 검사, 줄 끝 LF·공백만 바뀐 줄 0·부산물 0.
- [ ] 14. 리뷰·문서: 보안 → 성능 → 운영성, WORKLOG 116-fix1, ANALYSIS ⑦ 9 표.
- [ ] 15. dev 손 확인(사용자, 배포 뒤): WORKLOG 116-fix1 「dev 확인 순서」. 이 실행에서는 하지 않는다.
