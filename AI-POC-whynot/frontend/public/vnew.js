/* 영상만들기_신규(vnew, 설계자 2026-10-20) 화면.

   #vnew-root(영상만들기_신규)는 아직 틀(준비 중 카드)이다. #characters-root(캐릭터 만들기)는
   실제 화면 — 목록 ↔ 상세(업로드 → 만들기 → 실사·웹툰 3D·시트 보기)를 직접 그린다.

   기존 마크업을 찾지 않고, 이벤트는 자기가 만든 요소에만 단다. 어떤 요소가 없어도 예외로
   멈추지 않는다. CSS 클래스는 vnew- 접두사(vnew.css). styles.css 의 버튼·카드 클래스는 재사용.

   서버 연결: Authorization: Bearer <토큰>(vpoc.js 와 같은 토큰 키). 응답 모양은 지시서 019 의
   app/src/vnew/routes.py · characters.py 를 그대로 따른다.

   전역: window.vnew = { mount(projectId), mountCharacters(), unmount(), listCharacters() } */
(function () {
  "use strict";

  var TOKEN_KEY = "toon-change-ai-pj:token";
  var ROOT_ID = "vnew-root";
  var CHARACTERS_ROOT_ID = "characters-root";
  var POLL_MS = 5000; // 진행 중 상세를 다시 불러오는 간격

  // vnew-root(영상만들기_신규) 준비 중 카드에서 쓰는 단계 목록. 지금은 목록으로만 보인다.
  var STEPS = [
    ["①", "웹툰 이미지 올리기"],
    ["②", "대본·상황 정리"],
    ["③", "콘티"],
    ["④", "영상(실사·애니(웹툰))"],
    ["⑤", "사운드(베타)"]
  ];

  // variant 표시 정보. 서버 variant 키 → 한국어 제목.
  var VARIANTS = [
    { key: "real", title: "실사" },
    { key: "webtoon", title: "웹툰 그림체" }
  ];
  // 작업 123: ⑤ 영상·⑥·⑦ 의 그림체 이름. 저장값(webtoon)은 그대로 두고 보이는 이름만 "애니(웹툰)"(캐릭터 화면은 VARIANTS).
  var GEN_WEBTOON_TITLE = "애니(웹툰)";
  var GEN_VARIANTS = [
    { key: "real", title: "실사" },
    { key: "webtoon", title: GEN_WEBTOON_TITLE }
  ];

  // variant.status / char.status 배지 문구.
  var STATUS_LABEL = {
    draft: "초안",
    building: "만드는 중",
    ready: "완료",
    failed: "실패",
    queued: "대기",
    images: "그림 그리는 중",
    model3d: "3D 만드는 중"
  };

  var state = {
    mounted: false,
    projectId: "",
    // 캐릭터 화면 전용
    view: "list", // list | detail
    charId: "",
    detail: null, // 마지막으로 받은 상세
    pollTimer: 0,
    glbCache: {}, // variant → blob URL (현재 상세 것만)
    glbLoading: {}, // variant → true (중복 요청 방지)
    // 영상만들기_신규 계획(①입력 ②대본 ③콘티) 전용
    plan: null, // 마지막으로 받은 /plan 응답
    planStep: 1, // 지금 보고 있는 단계(1 입력 · 2 대본 · 3 콘티 · 4 영상 · 5 사운드)
    planPollTimer: 0, // ②③ 진행 중 다시 불러오기 타이머
    progressTicker: 0, // 진행 표시 경과 시간 1초 갱신 타이머
    genViewSig: "", // ⑤ 마지막으로 그린 gen 상태 서명(genViewSignature). ⑤가 아니거나 시작 화면이면 ""
    orderSaveTimer: 0 // ① 순서 저장 디바운스
  };

  /* ---------------------------------------------------------------- 도구 */

  function byId(id) {
    return document.getElementById(id);
  }

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clear(node) {
    while (node && node.firstChild) node.removeChild(node.firstChild);
  }

  function token() {
    try {
      return sessionStorage.getItem(TOKEN_KEY) || "";
    } catch (error) {
      return "";
    }
  }

  function statusLabel(value) {
    return STATUS_LABEL[value] || value || "";
  }

  /* ---------------------------------------------------------------- 통신 */

  /* ok:true 봉투를 기대하는 요청(health 처럼 {ok:true} 를 주는 라우트 전용). */
  function apiOk(path, options) {
    var init = options || {};
    init.headers = init.headers || {};
    init.headers["Authorization"] = "Bearer " + token();
    if (init.body && !(init.body instanceof FormData)) {
      init.headers["Content-Type"] = "application/json";
    }
    return fetch(path, init).then(function (response) {
      return response
        .json()
        .catch(function () {
          return { ok: false };
        })
        .then(function (payload) {
          if (!payload || payload.ok !== true) {
            throw (payload && payload.error) || { message: "서버 응답을 확인하지 못했습니다." };
          }
          return payload;
        });
    });
  }

  /* 캐릭터 API 전용. 성공 응답이 {ok:true} 가 아니라 데이터 객체이므로, HTTP 상태로 성공을
     판단하고 에러는 {error:{...}} 봉투(vpoc.errors.envelope)를 그대로 던진다.
     409 등 상태 코드가 필요한 곳을 위해 status 를 error 에 실어 준다.
     작업 150: 본문이 JSON 이 아닌 502·503·504(ALB·nginx 가 배포·재시작 중에 주는 HTML)나 fetch 자체 실패는
     같은 요청을 다시 보내도 되는 GET·PUT(문서 통째 저장)만 1·2·4초 뒤 최대 3번 다시 보낸다. POST·DELETE 는
     유료 작업을 시작할 수 있어 다시 보내지 않는다(예전 그대로). JSON 오류 응답은 다시 보내지 않고 예전 그대로다. */
  var API_RETRY_DELAYS_MS = [1000, 2000, 4000];
  var API_TRANSIENT_MESSAGE =
    "서버가 잠시 응답하지 않습니다(배포·재시작 중일 수 있습니다). 잠시 후 [다시 시도]를 눌러 주세요.";
  var API_NOT_JSON = {};

  function api(path, options) {
    var init = options || {};
    init.headers = init.headers || {};
    init.headers["Authorization"] = "Bearer " + token();
    if (init.body && !(init.body instanceof FormData)) {
      init.headers["Content-Type"] = "application/json";
    }
    var method = String(init.method || "GET").toUpperCase();
    var retryable = method === "GET" || method === "PUT";

    function again(attempt, status) {
      if (attempt < API_RETRY_DELAYS_MS.length) {
        return new Promise(function (resolve) {
          window.setTimeout(resolve, API_RETRY_DELAYS_MS[attempt]);
        }).then(function () {
          return send(attempt + 1);
        });
      }
      return Promise.reject({ message: API_TRANSIENT_MESSAGE, status: status });
    }

    function send(attempt) {
      return fetch(path, init).then(
        function (response) {
          var httpStatus = response.status;
          return response
            .json()
            .catch(function () {
              return API_NOT_JSON;
            })
            .then(function (payload) {
              var notJson = payload === API_NOT_JSON;
              if (notJson) payload = null;
              if (response.ok) {
                return payload || {};
              }
              if (notJson && retryable && (httpStatus === 502 || httpStatus === 503 || httpStatus === 504)) {
                return again(attempt, httpStatus);
              }
              var err = (payload && payload.error) || { message: "서버 오류가 발생했습니다." };
              if (typeof err === "object" && err && err.status === undefined) {
                err.status = httpStatus;
              }
              throw err;
            });
        },
        function (networkError) {
          if (!retryable) throw networkError;
          return again(attempt, 0);
        }
      );
    }

    return send(0);
  }

  function errMessage(error) {
    if (!error) return "알 수 없는 오류가 발생했습니다.";
    if (typeof error === "string") return error;
    return error.message || "알 수 없는 오류가 발생했습니다.";
  }

  /* 작업 206: 확인창이 열린 사이 금액이 바뀜(409 CostMismatch, 운영에서도 type 은 남는다) — 확인창 공통 처리.
     isCostMismatch(err) 로 가르고, rereadPlanView(pid) 로 계획 보기를 다시 읽어(state.plan·화면 갱신) 새 금액을 확인창에
     제자리로 넣은 뒤 costChangedText(옛 금액, 새 금액) 를 보인다. 확인창은 닫지 않는다. */
  function isCostMismatch(err) {
    return !!(err && err.type === "CostMismatch");
  }

  function costChangedText(oldUsd, newUsd) {
    return "금액이 바뀌었습니다: " + castingUsdText(oldUsd) + " → " + castingUsdText(newUsd) + ". 내용을 확인한 뒤 다시 눌러 주세요.";
  }

  function rereadPlanView(pid) {
    return api("/api/vnew/p/" + encodeURIComponent(pid) + "/plan").then(function (fresh) {
      if (fresh && state.projectId === pid) {
        state.plan = fresh;
        if (state.mounted) renderPlan();
      }
      return fresh || {};
    });
  }

  /* ---------------------------------------------------------------- vnew-root(준비 중) */

  function healthRow() {
    var row = el("p", "vnew-health", "서버 연결 확인 중…");
    apiOk("/api/vnew/health")
      .then(function () {
        row.className = "vnew-health is-ok";
        row.textContent = "서버 연결됨 (vnew 준비 완료)";
      })
      .catch(function () {
        row.className = "vnew-health is-bad";
        row.textContent = "서버에 아직 연결되지 않았습니다.";
      });
    return row;
  }

  function readyCard(title, lead, withSteps) {
    var card = el("div", "vnew-card");
    card.appendChild(el("span", "vnew-badge", "준비 중"));
    card.appendChild(el("h1", "vnew-title", title));
    card.appendChild(el("p", "vnew-lead", lead));
    if (withSteps) {
      var list = el("ol", "vnew-steps");
      STEPS.forEach(function (step) {
        var item = el("li", "vnew-step");
        item.appendChild(el("span", "vnew-step-mark", step[0]));
        item.appendChild(el("span", "vnew-step-text", step[1]));
        list.appendChild(item);
      });
      card.appendChild(list);
    }
    card.appendChild(healthRow());
    return card;
  }

  function renderReadyInto(host, title, lead, withSteps) {
    if (!host) return;
    clear(host);
    host.appendChild(readyCard(title, lead, withSteps));
  }

  /* ---------------------------------------------------------------- GLB 캐시 */

  function revokeAllGlb() {
    Object.keys(state.glbCache).forEach(function (key) {
      try {
        URL.revokeObjectURL(state.glbCache[key]);
      } catch (error) {
        /* 무시 */
      }
    });
    state.glbCache = {};
    state.glbLoading = {};
  }

  /* GLB 를 인증 헤더로 받아 blob URL 로 캐시한다. 상세를 열 때 variant 당 한 번만. */
  function loadGlb(charId, variant) {
    if (state.glbCache[variant] || state.glbLoading[variant]) return;
    state.glbLoading[variant] = true;
    fetch("/api/vnew/characters/" + encodeURIComponent(charId) + "/model/" + variant + ".glb", {
      headers: { Authorization: "Bearer " + token() }
    })
      .then(function (response) {
        if (!response.ok) throw new Error("glb " + response.status);
        return response.blob();
      })
      .then(function (blob) {
        // 비동기 사이에 다른 캐릭터로 떠났으면 버린다.
        if (state.view !== "detail" || state.charId !== charId) {
          return;
        }
        var url = URL.createObjectURL(blob);
        state.glbCache[variant] = url;
        delete state.glbLoading[variant];
        // 이미 그려진 model-viewer 가 있으면 src 를 꽂는다.
        var viewer = byId("vnew-mv-" + variant);
        if (viewer) viewer.setAttribute("src", url);
      })
      .catch(function () {
        delete state.glbLoading[variant];
        var note = byId("vnew-mv-note-" + variant);
        if (note) note.textContent = "3D 모델을 불러오지 못했습니다.";
      });
  }

  /* 프리비즈 텍스처(벽·바닥)를 Authorization 헤더로 받아 blob → objectURL 로 바꾼다.
     같은 출처 blob 이라 three.js 가 캔버스를 오염시키지 않아 captureStream 녹화가 된다.
     (버킷에 CORS 설정이 없어 S3 presigned URL 직통은 쓰지 않는다.)
     apiUrl 은 서버가 준 /api/vnew/p/<pid>/previz/texture?key=... 경로. 실패하면 null(색 대체).
     objectURL 의 revoke 는 previz 컨트롤러(dispose)가 맡는다. */
  function previzTextureFetch(apiUrl) {
    if (!apiUrl) return Promise.resolve(null);
    return fetch(apiUrl, { headers: { Authorization: "Bearer " + token() } })
      .then(function (response) {
        if (!response.ok) throw new Error("texture " + response.status);
        return response.blob();
      })
      .then(function (blob) {
        return URL.createObjectURL(blob);
      })
      .catch(function () {
        return null;
      });
  }

  /* ---------------------------------------------------------------- 이미지 확대 오버레이 */

  function openLightbox(url, alt) {
    if (!url) return;
    var scrim = el("div", "vnew-lightbox");
    var img = el("img", "vnew-lightbox-img");
    img.src = url;
    img.alt = alt || "";
    scrim.appendChild(img);
    function close() {
      if (scrim.parentNode) scrim.parentNode.removeChild(scrim);
      document.removeEventListener("keydown", onKey);
    }
    function onKey(event) {
      if (event.key === "Escape") close();
    }
    scrim.addEventListener("click", close);
    document.addEventListener("keydown", onKey);
    document.body.appendChild(scrim);
  }

  /* ---------------------------------------------------------------- 목록 화면 */

  function renderList(host) {
    if (!host) return;
    stopPolling();
    clear(host);

    var head = el("div", "vnew-page-head");
    var titles = el("div");
    titles.appendChild(el("h1", "vnew-h1", "캐릭터 만들기"));
    titles.appendChild(
      el(
        "p",
        "vnew-lead",
        "웹툰 이미지를 넣으면 같은 캐릭터를 실사·웹툰 그림체로, 5면 시트와 3D 모델까지 만듭니다."
      )
    );
    head.appendChild(titles);

    var newBtn = el("button", "primary-button", "새 캐릭터");
    newBtn.type = "button";
    newBtn.addEventListener("click", function () {
      createCharacterFlow(host);
    });
    head.appendChild(newBtn);
    host.appendChild(head);

    var error = el("p", "login-error vnew-list-error");
    error.setAttribute("role", "alert");
    error.style.display = "none";
    host.appendChild(error);

    var grid = el("div", "vnew-card-grid");
    host.appendChild(grid);

    var empty = el("div", "empty-block vnew-empty");
    empty.appendChild(el("strong", null, "아직 만든 캐릭터가 없습니다"));
    empty.appendChild(el("p", null, "[새 캐릭터] 를 눌러 웹툰 이미지를 올려 보세요."));
    empty.style.display = "none";
    host.appendChild(empty);

    var loading = el("p", "vnew-boot", "불러오는 중…");
    grid.appendChild(loading);

    api("/api/vnew/characters")
      .then(function (payload) {
        if (state.view !== "list") return;
        clear(grid);
        var rows = (payload && payload.characters) || [];
        if (!rows.length) {
          empty.style.display = "";
          return;
        }
        empty.style.display = "none";
        rows.forEach(function (row) {
          grid.appendChild(characterCard(host, row));
        });
      })
      .catch(function (err) {
        clear(grid);
        error.style.display = "";
        error.textContent = "목록을 불러오지 못했습니다: " + errMessage(err);
      });
  }

  function characterCard(host, row) {
    var card = el("button", "vnew-char-card");
    card.type = "button";

    var thumbWrap = el("div", "vnew-char-thumb");
    // 목록 썸네일: 서버가 주는 thumbUrl(3D 썸네일). 없으면 머리글자.
    if (row.thumbUrl) {
      var thumbImg = el("img");
      thumbImg.src = row.thumbUrl;
      thumbImg.alt = row.name || "";
      thumbWrap.appendChild(thumbImg);
    } else {
      thumbWrap.appendChild(el("span", "vnew-char-initial", (row.name || "?").slice(0, 1)));
    }
    card.appendChild(thumbWrap);

    var body = el("div", "vnew-char-body");
    body.appendChild(el("strong", "vnew-char-name", row.name || "이름 없는 캐릭터"));
    var badge = el("span", "vnew-badge-status is-" + (row.status || "draft"), statusLabel(row.status));
    body.appendChild(badge);
    card.appendChild(body);

    card.addEventListener("click", function () {
      openDetail(host, row.id);
    });
    return card;
  }

  /* 새 캐릭터: 이름 입력 → 생성 → 상세로. prompt 로 간단히 받는다. */
  function createCharacterFlow(host) {
    var name = "";
    try {
      name = window.prompt("새 캐릭터 이름을 입력하세요.", "");
    } catch (error) {
      name = "";
    }
    if (name === null) return; // 취소
    api("/api/vnew/characters", {
      method: "POST",
      body: JSON.stringify({ name: (name || "").trim() })
    })
      .then(function (payload) {
        if (payload && payload.id) {
          openDetail(host, payload.id);
        }
      })
      .catch(function (err) {
        var error = host.querySelector(".vnew-list-error");
        if (error) {
          error.style.display = "";
          error.textContent = "캐릭터를 만들지 못했습니다: " + errMessage(err);
        }
      });
  }

  /* ---------------------------------------------------------------- 상세 화면 */

  function openDetail(host, charId) {
    stopPolling();
    revokeAllGlb();
    state.view = "detail";
    state.charId = charId;
    state.detail = null;
    refreshDetail(host, true);
  }

  /* 상세를 받아 다시 그린다. first 면 로딩 문구를 먼저 보인다. */
  function refreshDetail(host, first) {
    if (!host || state.view !== "detail") return;
    if (first) {
      clear(host);
      host.appendChild(el("p", "vnew-boot", "불러오는 중…"));
    }
    var charId = state.charId;
    api("/api/vnew/characters/" + encodeURIComponent(charId))
      .then(function (detail) {
        if (state.view !== "detail" || state.charId !== charId) return;
        state.detail = detail;
        renderDetail(host, detail);
        schedulePoll(host, detail);
      })
      .catch(function (err) {
        if (state.view !== "detail" || state.charId !== charId) return;
        clear(host);
        var back = backRow(host);
        host.appendChild(back);
        var error = el("p", "login-error");
        error.setAttribute("role", "alert");
        error.textContent = "불러오지 못했습니다: " + errMessage(err);
        host.appendChild(error);
      });
  }

  function schedulePoll(host, detail) {
    stopPolling();
    var building = detail && detail.status === "building";
    // 실사 캐스팅 뽑기 중에도 다시 불러온다(vnew-live-fx RB7.2). 끔이면 casting 이 {enabled:false} 라 status 가 없다.
    var drawing = !!(detail && detail.casting && detail.casting.status === "drawing");
    // 추가 복장을 그리는 중(어느 복장이든 images)에도 다시 불러온다(지시서 102 피드백). 끝나면(ready·failed) 멈춘다.
    var outfitDrawing = outfitsBuilding(detail);
    if (!building && !drawing && !outfitDrawing) return;
    state.pollTimer = window.setTimeout(function () {
      // 화면을 떠났으면 멈춘다(schedulePoll/ stopPolling 가 보장).
      if (state.view !== "detail") return;
      refreshDetail(host, false);
    }, POLL_MS);
  }

  /* 상세의 추가 복장 중 하나라도 그리는 중(어느 그림체든 images)인가. outfitCard 의 "만드는 중"과 같은 기준이다
     (서버 start_build_outfit 의 409 기준. queued 는 아직 안 만듦이고, 멈춘 그리기는 서버가 failed 로 준다). */
  function outfitsBuilding(detail) {
    var outfits = detail && Array.isArray(detail.outfits) ? detail.outfits : [];
    return outfits.some(function (outfit) {
      var variants = (outfit && outfit.variants) || {};
      return VARIANTS.some(function (meta) {
        return (variants[meta.key] || {}).status === "images";
      });
    });
  }

  function stopPolling() {
    if (state.pollTimer) {
      window.clearTimeout(state.pollTimer);
      state.pollTimer = 0;
    }
  }

  function backRow(host) {
    var row = el("div", "vnew-detail-head");
    var back = el("button", "text-action", "← 목록으로");
    back.type = "button";
    back.addEventListener("click", function () {
      stopPolling();
      revokeAllGlb();
      state.view = "list";
      state.charId = "";
      state.detail = null;
      renderList(host);
    });
    row.appendChild(back);
    return row;
  }

  /* ---- 캐릭터 충실도 표시(char-fidelity T3, design §11.2) ----

     상세 응답의 features·analysis(baseSourceFileId·sidedItems)·variant leftUrl·rightUrl·model3d.endpoint·추가 복장 kind 를
     화면 글로 바꾸는 순수 함수(DOM 없음). 검사 cf-s 가 node 로 실행한다. 표에 없는 값은 표시하지 않는다(빈 목록·""). */
  var CHAR_PART_KO = { wrist: "손목", hand: "손", arm: "팔", shoulder: "어깨", ear: "귀", eye: "눈", cheek: "볼",
    chest: "가슴", hip: "허리 옆", leg: "다리", ankle: "발목", foot: "발", hair: "머리" };
  var CHAR_SIDE_KO = { left: "왼쪽", right: "오른쪽" };
  // 3D 이름표(T2b model3d.endpoint → 이름). 고정 표다. 표에 없는 endpoint 는 이름표를 달지 않는다.
  var CHAR_3D_ENDPOINT_LABEL = {
    "tripo3d/h3.1/multiview-to-3d": "Tripo H3.1",
    "tripo3d/h3.1/image-to-3d": "Tripo H3.1",
    "fal-ai/hunyuan3d-v3/image-to-3d": "Hunyuan3D v3"
  };
  var CHAR_VIEW_PAIRS = [["frontUrl", "정면"], ["backUrl", "후면"], ["leftUrl", "왼쪽"], ["rightUrl", "오른쪽"]];
  var OUTFITS_HINT = "기본 복장 위에 더 입히는 옷차림(예: 정장 자켓)을 따로 만듭니다. 3D 는 기본 복장 하나만 만듭니다.";
  var OUTFITS_HINT_FIDELITY =
    "사진 속 다른 옷차림(사진 속 옷)은 그 사진 그대로, 직접 넣은 옷은 기본 복장 위에 더 입혀 그립니다. 3D 는 기본 복장 하나만 만듭니다.";

  /* 표에서 key 의 값. 표 자신의 키만 본다(constructor·__proto__ 같은 이름은 ""). */
  function charTableValue(table, key) {
    return typeof key === "string" && Object.prototype.hasOwnProperty.call(table, key) ? table[key] : "";
  }

  function charFidelityOn(detail) {
    return !!(detail && detail.features && detail.features.charFidelity === true);
  }

  /* 분석 카드 새 줄 [{label, text}]. features.charFidelity 가 아니면 []. 기준 사진은 지금 업로드 목록에 있을 때만(지운 그림이면 없음). */
  function analysisFidelityLines(detail) {
    if (!charFidelityOn(detail)) return [];
    var analysis = detail.analysis || {};
    var sources = Array.isArray(detail.sources) ? detail.sources : [];
    var lines = [];
    var baseId = analysis.baseSourceFileId;
    var baseIndex = -1;
    if (typeof baseId === "string" && baseId) {
      sources.forEach(function (source, index) {
        if (baseIndex < 0 && source && source.fileId === baseId) baseIndex = index;
      });
    }
    if (baseIndex >= 0) lines.push({ label: "기준 사진", text: baseIndex + 1 + "번째" });
    var items = [];
    (Array.isArray(analysis.sidedItems) ? analysis.sidedItems : []).forEach(function (item) {
      if (!item || !charTableValue(CHAR_SIDE_KO, item.side)) return;
      var name = String(item.labelKo || item.itemEn || "").trim();
      if (!name) return;
      var part = charTableValue(CHAR_PART_KO, item.partEn) || String(item.partEn || "").trim();
      items.push((name + " — " + CHAR_SIDE_KO[item.side] + " " + part).trim());
    });
    if (items.length) lines.push({ label: "소품", text: items.join(", ") });
    return lines;
  }

  /* variant 열 작은 그림: url 이 있는 것만 [[urlKey, 이름]] (정면·후면·왼쪽·오른쪽 순서). */
  function variantViewPairs(v) {
    var variant = v || {};
    return CHAR_VIEW_PAIRS.filter(function (pair) {
      return !!variant[pair[0]];
    });
  }

  /* 추가 복장 배지: kind photo + features.charFidelity → "사진 속 옷", source auto → "후보", 그 밖 "". */
  function outfitBadgeText(outfit, detail) {
    var o = outfit || {};
    if (o.kind === "photo" && charFidelityOn(detail)) return "사진 속 옷";
    return o.source === "auto" ? "후보" : "";
  }

  /* 추가 복장 안내 문장(결정 S11). features.charFidelity 면 사진 속 옷 문장, 아니면 지금 문장. */
  function outfitsHintText(detail) {
    return charFidelityOn(detail) ? OUTFITS_HINT_FIDELITY : OUTFITS_HINT;
  }

  /* 3D 이름표 "3D: Tripo H3.1". endpoint 가 없거나(옛 데이터·Hunyuan 요청) 표에 없으면 "". */
  function model3dEndpointLabel(model3d) {
    var name = charTableValue(CHAR_3D_ENDPOINT_LABEL, (model3d || {}).endpoint);
    return name ? "3D: " + name : "";
  }

  function renderDetail(host, detail) {
    clear(host);

    var head = backRow(host);
    head.appendChild(el("h1", "vnew-h1 vnew-detail-title", detail.name || "이름 없는 캐릭터"));
    head.appendChild(
      el("span", "vnew-badge-status is-" + (detail.status || "draft"), statusLabel(detail.status))
    );
    host.appendChild(head);

    // 업로드 영역
    host.appendChild(uploadSection(host, detail));

    // 만들기 버튼 + 진행/오류
    host.appendChild(buildSection(host, detail));

    // 분석 결과
    var analysis = detail.analysis || {};
    if (
      analysis.descriptionKo ||
      analysis.model ||
      analysis.styleEn ||
      analysis.outfitEn ||
      (analysis.expressionsKo && analysis.expressionsKo.length)
    ) {
      var card = el("div", "vnew-analysis");
      if (analysis.descriptionKo) card.appendChild(el("p", "vnew-analysis-desc", analysis.descriptionKo));
      if (analysis.styleEn) {
        var styleRow = el("p", "vnew-analysis-meta");
        styleRow.appendChild(el("span", "vnew-analysis-label", "그림체"));
        styleRow.appendChild(el("span", null, analysis.styleEn));
        card.appendChild(styleRow);
      }
      if (analysis.outfitEn) {
        var outfitRow = el("p", "vnew-analysis-meta");
        outfitRow.appendChild(el("span", "vnew-analysis-label", "대표 옷차림"));
        outfitRow.appendChild(el("span", null, analysis.outfitEn));
        card.appendChild(outfitRow);
      }
      if (detail.baseOutfitEn || analysis.baseOutfitEn) {
        var baseRow = el("p", "vnew-analysis-meta");
        baseRow.appendChild(el("span", "vnew-analysis-label", "기본 복장"));
        baseRow.appendChild(el("span", null, detail.baseOutfitEn || analysis.baseOutfitEn));
        card.appendChild(baseRow);
      }
      // char-fidelity(R5.2): "기준 사진: N번째"·"소품: 시계 — 오른쪽 손목". features.charFidelity 이고 값이 있을 때만.
      analysisFidelityLines(detail).forEach(function (line) {
        var fidelityRow = el("p", "vnew-analysis-meta");
        fidelityRow.appendChild(el("span", "vnew-analysis-label", line.label));
        fidelityRow.appendChild(el("span", null, line.text));
        card.appendChild(fidelityRow);
      });
      if (analysis.expressionsKo && analysis.expressionsKo.length) {
        var exprRow = el("p", "vnew-analysis-meta");
        exprRow.appendChild(el("span", "vnew-analysis-label", "표정"));
        exprRow.appendChild(el("span", null, analysis.expressionsKo.join(", ")));
        card.appendChild(exprRow);
      }
      if (analysis.model) card.appendChild(el("p", "vnew-analysis-model", "분석 모델: " + analysis.model));
      host.appendChild(card);
    }

    // 실사 vs 웹툰 나란히
    var columns = el("div", "vnew-variant-grid");
    VARIANTS.forEach(function (meta) {
      columns.appendChild(variantColumn(host, detail, meta));
    });
    host.appendChild(columns);

    // 실사 캐스팅(vnew-live-fx 묶음 B). detail.casting.enabled 가 아니면 그리지 않는다.
    var castingNode = castingSection(host, detail);
    if (castingNode) host.appendChild(castingNode);

    // 추가 복장 섹션(기본 복장 위에 더 입히는 옷차림)
    host.appendChild(outfitsSection(host, detail));
  }

  /* ---- 업로드 영역 ---- */

  function uploadSection(host, detail) {
    var section = el("section", "panel vnew-upload");
    var headRow = el("div", "vnew-section-head");
    headRow.appendChild(el("h2", null, "웹툰 이미지 넣기"));
    section.appendChild(headRow);
    section.appendChild(
      el(
        "p",
        "vnew-hint",
        "같은 캐릭터가 잘 보이는 웹툰 컷을 넣어 주세요(정면·전신이 있으면 좋아요). 1~8장."
      )
    );

    var sources = detail.sources || [];
    var busy = detail.status === "building";

    // 드롭존 + 파일 선택
    var input = el("input");
    input.type = "file";
    input.accept = "image/png,image/jpeg,image/webp";
    input.multiple = true;
    input.style.display = "none";
    section.appendChild(input);

    var drop = el("button", "dropzone compact vnew-drop");
    drop.type = "button";
    var dropInner = el("div", "drop-empty");
    dropInner.appendChild(el("strong", null, "이미지를 끌어놓거나 클릭하세요"));
    dropInner.appendChild(el("p", null, "PNG · JPG · WEBP · 최대 8장"));
    drop.appendChild(dropInner);
    if (busy) {
      drop.disabled = true;
    }
    section.appendChild(drop);

    var error = el("p", "login-error vnew-upload-error");
    error.setAttribute("role", "alert");
    error.style.display = "none";
    section.appendChild(error);

    function showError(message) {
      error.style.display = "";
      error.textContent = message;
    }

    function doUpload(fileList) {
      var files = Array.prototype.slice.call(fileList || []);
      if (!files.length) return;
      var remaining = 8 - sources.length;
      if (remaining <= 0) {
        showError("이미지는 최대 8장까지 올릴 수 있습니다.");
        return;
      }
      if (files.length > remaining) {
        files = files.slice(0, remaining);
      }
      var form = new FormData();
      files.forEach(function (file) {
        form.append("files", file, file.name);
      });
      drop.disabled = true;
      api("/api/vnew/characters/" + encodeURIComponent(detail.id) + "/uploads", {
        method: "POST",
        body: form
      })
        .then(function () {
          refreshDetail(host, false);
        })
        .catch(function (err) {
          drop.disabled = false;
          showError("올리지 못했습니다: " + errMessage(err));
        });
    }

    drop.addEventListener("click", function () {
      input.click();
    });
    input.addEventListener("change", function () {
      doUpload(input.files);
      input.value = "";
    });
    drop.addEventListener("dragover", function (event) {
      event.preventDefault();
      drop.classList.add("is-drag");
    });
    drop.addEventListener("dragleave", function () {
      drop.classList.remove("is-drag");
    });
    drop.addEventListener("drop", function (event) {
      event.preventDefault();
      drop.classList.remove("is-drag");
      if (event.dataTransfer) doUpload(event.dataTransfer.files);
    });

    // 올린 이미지 썸네일(삭제 가능). 목록 응답엔 URL 이 없어 fileId 로 머리말만 보이되,
    // 상세 sources 에는 미리보기 URL 이 없으므로 이름·치수를 카드로 보여 준다.
    var thumbs = el("div", "vnew-source-list");
    sources.forEach(function (src) {
      var item = el("div", "vnew-source-item");
      item.appendChild(el("span", "vnew-source-name", src.name || src.fileId));
      if (src.width && src.height) {
        item.appendChild(el("span", "vnew-source-dim", src.width + "×" + src.height));
      }
      var del = el("button", "text-action danger vnew-source-del", "삭제");
      del.type = "button";
      if (busy) del.disabled = true;
      del.addEventListener("click", function () {
        del.disabled = true;
        api(
          "/api/vnew/characters/" +
            encodeURIComponent(detail.id) +
            "/uploads/" +
            encodeURIComponent(src.fileId),
          { method: "DELETE" }
        )
          .then(function () {
            refreshDetail(host, false);
          })
          .catch(function (err) {
            del.disabled = false;
            showError("삭제하지 못했습니다: " + errMessage(err));
          });
      });
      item.appendChild(del);
      thumbs.appendChild(item);
    });
    if (sources.length) section.appendChild(thumbs);

    return section;
  }

  /* ---- 만들기 버튼 + 진행 표시 ---- */

  function buildSection(host, detail) {
    var section = el("section", "vnew-build");
    var sources = detail.sources || [];
    var hasSources = sources.length >= 1;
    var building = detail.status === "building";
    var madeBefore = (detail.version || 0) > 0;

    var error = el("p", "login-error vnew-build-error");
    error.setAttribute("role", "alert");
    error.style.display = "none";

    var btn = el("button", "primary-button vnew-build-btn", madeBefore ? "다시 만들기" : "캐릭터 만들기");
    btn.type = "button";
    btn.disabled = building || !hasSources;

    // 추가 요청 창(다시 만들기 때만). 숨겨 두었다가 버튼을 누르면 펼친다.
    var panel = el("div", "vnew-rebuild-panel");
    panel.style.display = "none";
    panel.appendChild(el("label", "vnew-rebuild-label", "추가 요청 (선택)"));
    var textarea = el("textarea", "vnew-rebuild-text");
    textarea.rows = 3;
    textarea.maxLength = 500;
    textarea.placeholder = "후드티 말고 베이지 셔츠로 / 연필 선 느낌을 더 살려서 / 표정은 무표정으로";
    textarea.value = detail.extraPromptKo || "";
    panel.appendChild(textarea);
    panel.appendChild(
      el("p", "vnew-rebuild-cost", "다시 만들면 새 버전이 생기고 비용이 듭니다.")
    );
    var panelBtns = el("div", "vnew-rebuild-btns");
    var confirmBtn = el("button", "primary-button", "다시 만들기");
    confirmBtn.type = "button";
    var cancelBtn = el("button", "outline-button", "취소");
    cancelBtn.type = "button";
    panelBtns.appendChild(confirmBtn);
    panelBtns.appendChild(cancelBtn);
    panel.appendChild(panelBtns);

    function doBuild(body) {
      btn.disabled = true;
      confirmBtn.disabled = true;
      error.style.display = "none";
      api("/api/vnew/characters/" + encodeURIComponent(detail.id) + "/build", {
        method: "POST",
        body: body ? JSON.stringify(body) : undefined,
        headers: body ? { "Content-Type": "application/json" } : undefined
      })
        .then(function () {
          refreshDetail(host, false);
        })
        .catch(function (err) {
          btn.disabled = false;
          confirmBtn.disabled = false;
          error.style.display = "";
          if (err && err.status === 409) {
            error.textContent = "이미 만들고 있습니다.";
          } else {
            error.textContent = "만들기를 시작하지 못했습니다: " + errMessage(err);
          }
        });
    }

    btn.addEventListener("click", function () {
      if (madeBefore) {
        // 추가 요청 창을 펼친다(토글).
        panel.style.display = panel.style.display === "none" ? "" : "none";
        if (panel.style.display === "") textarea.focus();
        return;
      }
      doBuild(null);
    });

    confirmBtn.addEventListener("click", function () {
      doBuild({ extraPromptKo: textarea.value || "" });
    });
    cancelBtn.addEventListener("click", function () {
      panel.style.display = "none";
    });

    section.appendChild(btn);
    if (madeBefore) section.appendChild(panel);
    if (!hasSources) {
      section.appendChild(el("span", "vnew-hint", "이미지를 1장 이상 올리면 만들 수 있습니다."));
    }
    section.appendChild(error);

    if (detail.buildError) {
      var be = el("p", "login-error");
      be.textContent = detail.buildError;
      section.appendChild(be);
    }

    // 진행 표시(variant 별): 분석 → 그림 → 3D → 완료
    if (building || madeBefore) {
      section.appendChild(progressBoard(detail));
    }

    return section;
  }

  // variant.status 를 진행 단계 인덱스로. queued=분석대기, images=그림, model3d=3D, ready/failed=끝
  function progressBoard(detail) {
    var board = el("div", "vnew-progress-board");
    VARIANTS.forEach(function (meta) {
      var v = (detail.variants || {})[meta.key] || {};
      var line = el("div", "vnew-progress-line");
      line.appendChild(el("span", "vnew-progress-variant", meta.title));
      var steps = [
        ["분석", ["images", "model3d", "ready"]],
        ["그림(시트·정면)", ["model3d", "ready"]],
        ["3D 모델", ["ready"]],
        ["완료", ["ready"]]
      ];
      var status = v.status || "queued";
      var failed = status === "failed";
      steps.forEach(function (step) {
        var done = step[1].indexOf(status) >= 0;
        var cls = "vnew-progress-step";
        if (done) cls += " is-done";
        var chip = el("span", cls, step[0]);
        line.appendChild(chip);
      });
      if (failed) {
        line.appendChild(el("span", "vnew-progress-step is-failed", "실패"));
      }
      board.appendChild(line);
    });
    return board;
  }

  /* ---- variant 열(3D + 시트·정면·후면) ---- */

  // variant 상태 한 줄: 배지 + (진행 중) 단계 / (실패·중단) [이 그림체만 다시 만들기].
  // 추가 요청 입력 창을 재사용(펼침). rebuild 는 그 그림체만 처음부터 다시 만든다.
  function variantStatusRow(host, detail, meta, v) {
    var row = el("div", "vnew-variant-status");
    var status = v.status || "queued";
    var label = STATUS_LABEL[status] || status;
    var badge = el("span", "vnew-variant-badge is-" + status, label);
    row.appendChild(badge);

    var building = status === "queued" || status === "images" || status === "model3d";
    if (building) {
      // 진행 중이면 단계만 보인다(버튼 없음).
      var stepText =
        status === "images" ? "그림 그리는 중…" : status === "model3d" ? "3D 만드는 중…" : "준비 중…";
      row.appendChild(el("span", "vnew-variant-step", stepText));
      return row;
    }

    if (status !== "failed" && status !== "ready") {
      return row;
    }

    // 실패·중단: [이 그림체만 다시 만들기], 완성(ready): [실사 다시 만들기]·[애니 다시 만들기](작업 126) + 추가 요청 창(재사용).
    // 어느 쪽이든 패널의 비용 안내를 보고 [다시 만들기]를 한 번 더 눌러야 보낸다(금액 확인).
    var ready = status === "ready";
    var error = el("p", "login-error vnew-variant-rebuild-error");
    error.setAttribute("role", "alert");
    error.style.display = "none";

    var btn = el("button", "outline-action vnew-variant-rebuild-btn",
      ready ? (meta.key === "real" ? "실사 다시 만들기" : "애니 다시 만들기") : "이 그림체만 다시 만들기");
    btn.type = "button";

    var panel = el("div", "vnew-rebuild-panel");
    panel.style.display = "none";
    panel.appendChild(el("label", "vnew-rebuild-label", "추가 요청 (선택)"));
    var textarea = el("textarea", "vnew-rebuild-text");
    textarea.rows = 3;
    textarea.maxLength = 500;
    textarea.placeholder = "후드티 말고 베이지 셔츠로 / 연필 선 느낌을 더 살려서 / 표정은 무표정으로";
    textarea.value = detail.extraPromptKo || "";
    panel.appendChild(textarea);
    panel.appendChild(el("p", "vnew-rebuild-cost", ready
      ? "이 그림체의 시트·정면·후면·3D를 처음부터 새로 그립니다. 지금 그림은 새 그림으로 바뀌고 비용이 듭니다."
      : "이 그림체만 새로 그립니다. 비용이 듭니다."));
    var panelBtns = el("div", "vnew-rebuild-btns");
    var confirmBtn = el("button", "primary-button", "다시 만들기");
    confirmBtn.type = "button";
    var cancelBtn = el("button", "outline-button", "취소");
    cancelBtn.type = "button";
    panelBtns.appendChild(confirmBtn);
    panelBtns.appendChild(cancelBtn);
    panel.appendChild(panelBtns);

    function doRebuild() {
      btn.disabled = true;
      confirmBtn.disabled = true;
      error.style.display = "none";
      api(
        "/api/vnew/characters/" +
          encodeURIComponent(detail.id) +
          "/variants/" +
          encodeURIComponent(meta.key) +
          "/rebuild",
        {
          method: "POST",
          body: JSON.stringify({ extraPromptKo: textarea.value || "" }),
          headers: { "Content-Type": "application/json" }
        }
      )
        .then(function () {
          refreshDetail(host, false);
        })
        .catch(function (err) {
          btn.disabled = false;
          confirmBtn.disabled = false;
          error.style.display = "";
          if (err && err.status === 409) {
            error.textContent = "이 그림체를 이미 만들고 있습니다.";
          } else {
            error.textContent = "다시 만들기를 시작하지 못했습니다: " + errMessage(err);
          }
        });
    }

    btn.addEventListener("click", function () {
      panel.style.display = panel.style.display === "none" ? "" : "none";
      if (panel.style.display === "") textarea.focus();
    });
    confirmBtn.addEventListener("click", doRebuild);
    cancelBtn.addEventListener("click", function () {
      panel.style.display = "none";
    });

    row.appendChild(btn);
    row.appendChild(panel);
    row.appendChild(error);
    return row;
  }

  function variantColumn(host, detail, meta) {
    var v = (detail.variants || {})[meta.key] || {};
    var model3d = v.model3d || {};
    var column = el("div", "vnew-variant-col");
    column.appendChild(el("h2", "vnew-variant-title", meta.title));

    // variant 상태 배지 + 진행/실패 안내 + [이 그림체만 다시 만들기]
    column.appendChild(variantStatusRow(host, detail, meta, v));

    // 사람 수 경고(정면/후면에 여러 사람이 보일 수 있음)
    if (v.warning) {
      column.appendChild(el("p", "vnew-variant-warning", "⚠ " + v.warning));
    }

    // (a) 3D 보기
    column.appendChild(viewer3d(host, detail, meta, v, model3d));
    // 3D 이름표(T2b model3d.endpoint, 작은 글씨). endpoint 가 없으면(옛 데이터) 아무것도 달지 않는다.
    var endpointLabel = model3dEndpointLabel(model3d);
    if (endpointLabel) column.appendChild(el("p", "vnew-3d-endpoint", endpointLabel));

    // (b) 5면 시트 + 정면·후면
    var sheets = el("div", "vnew-sheets");
    if (v.sheetUrl) {
      var sheet = el("img", "vnew-sheet-img");
      sheet.src = v.sheetUrl;
      sheet.alt = meta.title + " 5면 시트";
      sheet.addEventListener("click", function () {
        openLightbox(v.sheetUrl, meta.title + " 5면 시트");
      });
      sheets.appendChild(sheet);
    } else if (v.status && v.status !== "failed") {
      sheets.appendChild(el("p", "vnew-hint", "시트를 그리는 중입니다…"));
    }

    var smalls = el("div", "vnew-sheet-smalls");
    // 정면·후면 + 옆모습(char-fidelity R5.3, leftUrl·rightUrl 이 있을 때만)
    variantViewPairs(v).forEach(function (pair) {
      var url = v[pair[0]];
      if (!url) return;
      var fig = el("figure", "vnew-sheet-small");
      var img = el("img");
      img.src = url;
      img.alt = meta.title + " " + pair[1];
      img.addEventListener("click", function () {
        openLightbox(url, meta.title + " " + pair[1]);
      });
      fig.appendChild(img);
      fig.appendChild(el("figcaption", null, pair[1]));
      smalls.appendChild(fig);
    });
    if (smalls.childNodes.length) sheets.appendChild(smalls);

    column.appendChild(sheets);
    return column;
  }

  /* ---- 실사 캐스팅(vnew-live-fx 묶음 B, RB7 · design §B.6) ----

     캐릭터 상세의 실사·웹툰 열 아래, 추가 복장 위. detail.casting.enabled 가 false 면(VNEW_CASTING 끔) 아무것도 그리지 않는다.
     사진 src 는 응답의 presigned url 만 쓰고, 글은 모두 textContent(el)·속성으로만 넣는다. 뽑기 확인창에는 금액 숫자를
     보인다(결정 1). 409 CostMismatch 면 상세를 다시 읽어 새 금액을 버튼에 보이고, 다시 눌러 새 금액으로 확인해야 보낸다.
     뽑는 중·캐릭터를 만드는 중에는 5초마다 다시 그리므로(schedulePoll) 입력 칸을 막아 쓰던 글이 사라지지 않게 한다.
     아래 순수 함수(DOM 없음)와 castingSection 은 검사 cast-l 이 node 가짜 DOM 으로 실행한다. */
  var CASTING_GENDER_LABEL = { male: "남", female: "여", unknown: "모름" };
  var CASTING_LEAD =
    "웹툰 그림 없이 글로 실사 배우 사진을 만듭니다. 고른 사진은 통 테이크 실사 버전의 인물 참고로 씁니다(고르지 않으면 실사 정면).";
  var CASTING_NO_LOOK = "먼저 캐릭터를 만들어 분석해 주세요.";

  function castingCount(value) {
    var n = Number(value);
    return isFinite(n) && n > 0 ? Math.floor(n) : 0;
  }

  function castingUsdText(value) {
    var usd = Number(value);
    return "$" + (isFinite(usd) && usd > 0 ? usd : 0).toFixed(2);
  }

  function castingGenderLabel(value) {
    return CASTING_GENDER_LABEL[value] || CASTING_GENDER_LABEL.unknown;
  }

  /* 성별 고르기: "" = 자동(옆에 지금 정해진 값 genderAuto, 091 남은 이슈 3) · male · female · unknown. */
  function castingGenderOptions(cast) {
    var c = cast || {};
    return [{ value: "", label: "자동(분석: " + castingGenderLabel(c.genderAuto) + ")" }].concat(
      ["male", "female", "unknown"].map(function (key) {
        return { value: key, label: CASTING_GENDER_LABEL[key] };
      })
    );
  }

  function castingDrawLabel(cast) {
    var c = cast || {};
    return "실사 캐스팅 뽑기 (" + castingCount(c.count) + "장 · " + castingUsdText(c.totalUsd) + ")";
  }

  function castingConfirmText(cast) {
    var c = cast || {};
    return "실사 캐스팅 사진 " + castingCount(c.count) + "장을 만듭니다. 예상 비용 " + castingUsdText(c.totalUsd) +
      "입니다. 계속할까요?";
  }

  function castingCostChangedText(cast) {
    return "금액이 바뀌었습니다. 새 금액은 " + castingUsdText((cast || {}).totalUsd) +
      "입니다. 다시 누르면 새 금액으로 확인합니다.";
  }

  /* 느낌별 금액(지시서 109): 서버가 presets[].totalUsd 를 주면 그 느낌의 금액, 주지 않으면(옛 서버·되돌림) 지금처럼 totalUsd.
     돌려주는 {count, totalUsd} 가 버튼 글·확인창·confirmUsd 에 그대로 쓰인다. */
  function castingPresetPrice(cast, presetKey) {
    var c = cast || {};
    var total = c.totalUsd;
    (Array.isArray(c.presets) ? c.presets : []).forEach(function (item) {
      if (item && item.key === presetKey && typeof item.totalUsd === "number" && isFinite(item.totalUsd)) total = item.totalUsd;
    });
    return { count: c.count, totalUsd: total };
  }

  /* 진행: 끝난 칸(ready·failed) / 전체 칸. */
  function castingProgress(cast) {
    var c = cast || {};
    var slots = Array.isArray(c.slots) ? c.slots : [];
    var done = slots.filter(function (slot) {
      return slot && (slot.status === "ready" || slot.status === "failed");
    }).length;
    var total = slots.length || castingCount(c.count);
    return { done: done, total: total, text: "사진을 만드는 중… (" + done + "/" + total + ")" };
  }

  function castingUsingText(cast) {
    return (cast || {}).chosen
      ? "실사 버전에 쓰는 사진: 고른 캐스팅 사진"
      : "실사 버전에 쓰는 사진: 실사 정면 (기본 · 캐스팅 사진은 [이 사진으로]를 눌러야 쓰입니다)";
  }

  /* 사진 아래 한 줄: "아이돌풍 · 2회차"(느낌 라벨은 상세의 presets 에서 찾는다). */
  function castingPhotoCaption(photo, cast) {
    var p = photo || {};
    var presets = Array.isArray((cast || {}).presets) ? cast.presets : [];
    var label = "";
    presets.forEach(function (item) {
      if (item && item.key === p.preset) label = String(item.label || "");
    });
    var round = castingCount(p.round);
    return [label, round ? round + "회차" : ""].filter(Boolean).join(" · ");
  }

  /* 입력을 막는 이유(다시 그리기 중 쓰던 글이 사라지지 않게). 막지 않으면 "". */
  function castingLockText(detail) {
    var d = detail || {};
    if ((d.casting || {}).status === "drawing") return "사진을 만드는 동안에는 느낌·성별·외모 문장을 바꿀 수 없습니다.";
    if (d.status === "building") return "캐릭터를 만드는 동안에는 실사 캐스팅을 쓸 수 없습니다.";
    return "";
  }

  /* BL 유형(지시서 103·104, VNEW_BL_LOOK). 104부터 화면에서 고르지 않는다. 서버가 사용자가 쓴 캐릭터 설명(추가 요청,
     extraPromptKo)에서 찾은 유형(blDetected)과 참고 외모 문장(blHintsEn)을 주고, 화면은 읽기 전용 한 줄로 보이기만 한다
     (castingBlLine). 103 고르기 함수는 104 피드백(Claude 승인)으로 지웠다. */
  var CASTING_BL_HINT_LEAD = "참고로 더해지는 외모 설명(영어): ";
  var CASTING_BL_FOUND_LEAD = "캐릭터 설명에서 찾은 BL 유형: ";
  var CASTING_BL_GUIDE = "캐릭터 설명에 '떡대공'처럼 BL 유형을 적으면 실사 사진에 참고로 반영됩니다.";
  // 지시서 112: 설명에서 유형 이름 밖에 따로 찾은 일반 외모 용어(서버 termsDetected, 지금은 "떡대"만).
  var CASTING_TERM_FOUND_LEAD = "캐릭터 설명에서 찾은 외모 용어: ";
  var CASTING_TERM_AFTER_BL = " · 외모 용어: ";

  function castingBlHintText(cast) {
    var text = String((cast || {}).blHintsEn || "");
    return text ? CASTING_BL_HINT_LEAD + text : "";
  }

  /* 읽기 전용 BL 한 줄(지시서 104). blDetected 가 배열이 아니면(VNEW_BL_LOOK 끔) null — 줄이 없다.
     찾았으면 {found: true, text: "캐릭터 설명에서 찾은 BL 유형: 떡대공 — 참고로 더해지는 외모 설명(영어): …"}(참고 문장이 비면
     앞부분만, 유형이 둘이면 ", " 로 잇는다), 못 찾았으면 {found: false, text: 안내}.
     지시서 112: termsDetected 가 있으면 "캐릭터 설명에서 찾은 외모 용어: 떡대"(유형도 있으면 "… BL 유형: 무심공 · 외모 용어: 떡대")로
     찾은 줄이 된다. termsDetected 가 없으면(떡대 없음·112 전 서버) 104 와 같다. */
  function castingBlLine(cast) {
    var c = cast || {};
    if (!Array.isArray(c.blDetected)) return null;
    var labels = [];
    c.blDetected.forEach(function (item) {
      var label = item ? String(item.label || item.id || "") : "";
      if (label) labels.push(label);
    });
    var terms = [];
    (Array.isArray(c.termsDetected) ? c.termsDetected : []).forEach(function (item) {
      var term = item ? String(item.label || item.id || "") : "";
      if (term) terms.push(term);
    });
    if (!labels.length && !terms.length) return { found: false, text: CASTING_BL_GUIDE };
    var text = labels.length ? CASTING_BL_FOUND_LEAD + labels.join(", ") : "";
    if (terms.length) text += (labels.length ? CASTING_TERM_AFTER_BL : CASTING_TERM_FOUND_LEAD) + terms.join(", ");
    var hint = castingBlHintText(c);
    return { found: true, text: hint ? text + " — " + hint : text };
  }

  /* 지시서 113: 캐릭터 설명의 인터넷 외모 용어를 웹 검색으로 풀이해 캐스팅 문장에 더한 조각(서버 termsApplied, 켬일 때만 온다).
     사용자가 해석이 틀렸는지 볼 수 있게 용어마다 "설명의 '꽃미남'을 'a …' · 'b …'으로 해석했어요" 한 줄. 조사 을/를은 용어 끝 한글 음절의
     받침을 따르고, 한글이 아니면 "을(를)". 글이 아닌 항목·빈 조각은 건너뛴다(끔·옛 서버면 키가 없어 줄이 없다). */
  function castingObjectParticle(word) {
    var text = String(word || "");
    var code = text.charCodeAt(text.length - 1);
    if (code >= 0xac00 && code <= 0xd7a3) return (code - 0xac00) % 28 ? "을" : "를";
    return "을(를)";
  }

  function castingTermLines(cast) {
    var items = Array.isArray((cast || {}).termsApplied) ? cast.termsApplied : [];
    var lines = [];
    items.forEach(function (item) {
      var term = item && typeof item.term === "string" ? item.term : "";
      var pieces = (item && Array.isArray(item.pieces) ? item.pieces : []).filter(function (piece) {
        return typeof piece === "string" && piece;
      });
      if (!term || !pieces.length) return;
      lines.push("설명의 '" + term + "'" + castingObjectParticle(term) + " " +
        pieces.map(function (piece) { return "'" + piece + "'"; }).join(" · ") + "으로 해석했어요");
    });
    return lines;
  }

  function castingSection(host, detail) {
    var cast = (detail && detail.casting) || {};
    if (!cast.enabled) return null;
    var base = "/api/vnew/characters/" + encodeURIComponent(detail.id) + "/casting";
    var drawing = cast.status === "drawing";
    var lock = castingLockText(detail);
    var form = { preset: cast.preset || "idol", gender: cast.gender || "" };
    // 금액은 고른 느낌을 따른다(지시서 109). priceSource 는 금액을 읽을 보기(409 CostMismatch 뒤에는 다시 읽은 보기).
    var priceSource = cast;
    var price = castingPresetPrice(priceSource, form.preset);
    var loadedLook = String(cast.lookEn || "");

    var section = el("section", "panel vnew-casting");
    var headRow = el("div", "vnew-section-head");
    headRow.appendChild(el("h2", null, "실사 캐스팅"));
    section.appendChild(headRow);
    section.appendChild(el("p", "vnew-hint vnew-casting-lead", CASTING_LEAD));

    var message = el("p", "login-error vnew-casting-msg");
    message.setAttribute("role", "alert");
    message.style.display = "none";
    function showMessage(text) {
      message.textContent = text;
      message.style.display = "";
    }

    // 느낌 · 성별 · 외모 문장
    var formBox = el("div", "vnew-casting-form");
    var presetRow = el("div", "vnew-casting-presets");
    presetRow.appendChild(el("span", "vnew-field-label", "느낌"));
    (Array.isArray(cast.presets) ? cast.presets : []).forEach(function (item) {
      var label = el("label", "vnew-casting-preset");
      var radio = el("input");
      radio.type = "radio";
      radio.name = "vnew-casting-preset-" + detail.id;
      radio.value = item.key;
      radio.checked = item.key === form.preset;
      radio.disabled = !!lock;
      radio.addEventListener("change", function () {
        if (!radio.checked) return;
        form.preset = item.key;
        // 느낌을 바꾸면 버튼 금액·확인창·confirmUsd 도 그 느낌 금액으로(지시서 109). 옛 서버면 같은 totalUsd 그대로.
        price = castingPresetPrice(priceSource, form.preset);
        drawBtn.textContent = castingDrawLabel(price);
      });
      label.appendChild(radio);
      label.appendChild(el("span", null, item.label));
      presetRow.appendChild(label);
    });
    formBox.appendChild(presetRow);

    var genderWrap = el("label", "vnew-field vnew-casting-gender");
    genderWrap.appendChild(el("span", "vnew-field-label", "성별"));
    var select = el("select", "vnew-field-input");
    castingGenderOptions(cast).forEach(function (opt) {
      var option = el("option", null, opt.label);
      option.value = opt.value;
      select.appendChild(option);
    });
    select.value = form.gender;
    select.disabled = !!lock;
    select.addEventListener("change", function () {
      form.gender = select.value;
    });
    genderWrap.appendChild(select);
    formBox.appendChild(genderWrap);
    // BL 유형 한 줄(지시서 104, 읽기 전용): 캐릭터 설명에서 찾았으면 유형·참고 외모 설명, 못 찾았으면 작은 안내. 끔이면 없다.
    var blLine = castingBlLine(cast);
    if (blLine) {
      formBox.appendChild(el("p", blLine.found ? "vnew-casting-bl-found" : "vnew-hint vnew-casting-bl-guide", blLine.text));
    }
    // 웹 검색 용어 풀이(지시서 113, 읽기 전용): 용어마다 작은 글씨 한 줄. termsApplied 가 없거나 비면 줄이 없다.
    castingTermLines(cast).forEach(function (text) {
      formBox.appendChild(el("p", "vnew-hint vnew-casting-term-note", text));
    });

    var lookWrap = el("label", "vnew-field vnew-casting-look");
    lookWrap.appendChild(el("span", "vnew-field-label", "외모 문장 (영어, 300자 이하)"));
    var textarea = el("textarea", "vnew-rebuild-text vnew-casting-look-text");
    textarea.rows = 3;
    textarea.maxLength = 300;
    textarea.placeholder = "캐릭터를 만들어 분석하면 외모 문장이 채워집니다.";
    textarea.value = loadedLook;
    textarea.disabled = !!lock;
    lookWrap.appendChild(textarea);
    formBox.appendChild(lookWrap);
    if (cast.patternWarning) formBox.appendChild(el("p", "vnew-casting-warn", cast.patternWarning));

    var formBtns = el("div", "vnew-casting-btns");
    var saveBtn = el("button", "outline-button vnew-casting-save", "저장");
    saveBtn.type = "button";
    var revertBtn = el("button", "text-action vnew-casting-revert", "분석 문장으로 되돌리기");
    revertBtn.type = "button";
    formBtns.appendChild(saveBtn);
    formBtns.appendChild(revertBtn);
    formBox.appendChild(formBtns);
    section.appendChild(formBox);
    if (lock) section.appendChild(el("p", "vnew-hint vnew-casting-lock", lock));

    // [실사 캐스팅 뽑기 (N장 · $X)]
    var drawBtn = el("button", "primary-button vnew-casting-draw", castingDrawLabel(price));
    drawBtn.type = "button";
    section.appendChild(drawBtn);
    // 그림체 다시 뽑기 비용 안내(지시서 107). 서버가 실사 사진 검사를 켰을 때만 photoCheckNote 를 보낸다(작은 글씨 한 줄).
    if (cast.photoCheckNote) section.appendChild(el("p", "vnew-hint vnew-casting-redraw-note", String(cast.photoCheckNote)));
    section.appendChild(message);

    function lookChanged() {
      return textarea.value !== loadedLook;
    }

    function updateButtons() {
      var hasLook = !!textarea.value.trim();
      saveBtn.disabled = !!lock;
      revertBtn.disabled = !!lock || !(cast.lookEdited || lookChanged());
      drawBtn.disabled = !!lock || !hasLook;
      drawBtn.title = hasLook ? "" : CASTING_NO_LOOK;
    }

    // 저장(PUT). lookValue 가 null 이면 외모 문장은 보내지 않는다(고치지 않은 자동 문장을 "고친 문장"으로 굳히지 않게).
    function save(lookValue) {
      var body = { preset: form.preset, gender: form.gender };
      if (lookValue !== null) body.lookEn = lookValue;
      saveBtn.disabled = true;
      revertBtn.disabled = true;
      message.style.display = "none";
      api(base, { method: "PUT", body: JSON.stringify(body) })
        .then(function () {
          refreshDetail(host, false);
        })
        .catch(function (err) {
          updateButtons();
          showMessage("저장하지 못했습니다: " + errMessage(err));
        });
    }

    // 409 CostMismatch: 상세를 다시 읽어 새 금액을 버튼에 보인다. 여기서는 보내지 않는다(다시 눌러 새 금액으로 확인).
    function rereadPrice() {
      return api("/api/vnew/characters/" + encodeURIComponent(detail.id))
        .then(function (fresh) {
          var next = (fresh && fresh.casting) || {};
          if (!next.enabled) {
            refreshDetail(host, false);
            return;
          }
          priceSource = next;
          price = castingPresetPrice(priceSource, form.preset);
          drawBtn.textContent = castingDrawLabel(price);
          updateButtons();
          showMessage(castingCostChangedText(price));
        })
        .catch(function (err) {
          updateButtons();
          showMessage("새 금액을 불러오지 못했습니다: " + errMessage(err));
        });
    }

    function choose(candidateId, button) {
      button.disabled = true;
      message.style.display = "none";
      api(base + "/choose", { method: "POST", body: JSON.stringify({ candidateId: candidateId }) })
        .then(function () {
          refreshDetail(host, false);
        })
        .catch(function (err) {
          button.disabled = false;
          showMessage("사진을 고르지 못했습니다: " + errMessage(err));
        });
    }

    textarea.addEventListener("input", updateButtons);
    saveBtn.addEventListener("click", function () {
      save(lookChanged() ? textarea.value : null);
    });
    revertBtn.addEventListener("click", function () {
      save("");
    });
    drawBtn.addEventListener("click", function () {
      if (!window.confirm(castingConfirmText(price))) return;
      var body = { confirmUsd: Number(price.totalUsd), preset: form.preset, gender: form.gender };
      if (lookChanged()) body.lookEn = textarea.value;
      drawBtn.disabled = true;
      message.style.display = "none";
      api(base + "/draw", { method: "POST", body: JSON.stringify(body) })
        .then(function () {
          refreshDetail(host, false);
        })
        .catch(function (err) {
          if (err && err.type === "CostMismatch") return rereadPrice();
          updateButtons();
          showMessage("실사 캐스팅 뽑기를 시작하지 못했습니다: " + errMessage(err));
        });
    });
    updateButtons();

    // 진행 막대(뽑는 중) / 오류 문구(실패·일부 실패)
    if (drawing) {
      var progress = castingProgress(cast);
      var bar = el("div", "vnew-casting-progress");
      bar.appendChild(el("p", "vnew-progress-label", progress.text));
      var track = el("div", "vnew-progress-track");
      var fill = el("div", "vnew-casting-progress-fill");
      fill.style.width = Math.max(5, progress.total ? Math.round((100 * progress.done) / progress.total) : 0) + "%";
      track.appendChild(fill);
      bar.appendChild(track);
      section.appendChild(bar);
    } else if (cast.error) {
      section.appendChild(
        el("p", (cast.status === "failed" ? "login-error" : "vnew-hint") + " vnew-casting-error", cast.error)
      );
    }

    // 실사 버전에 쓰는 사진 + [실사 정면 쓰기]
    var usingRow = el("div", "vnew-casting-using");
    usingRow.appendChild(el("span", null, castingUsingText(cast)));
    if (cast.chosen) {
      var frontBtn = el("button", "text-action vnew-casting-front", "실사 정면 쓰기");
      frontBtn.type = "button";
      frontBtn.title = "캐스팅 사진을 고르지 않고 실사 정면을 씁니다";
      frontBtn.addEventListener("click", function () {
        choose("", frontBtn);
      });
      usingRow.appendChild(frontBtn);
    }
    section.appendChild(usingRow);

    function photoCard(photo) {
      var chosen = photo.id === cast.chosen;
      var fig = el("figure", "vnew-casting-photo" + (chosen ? " is-chosen" : ""));
      if (photo.url) {
        var img = el("img");
        img.src = photo.url;
        img.alt = "실사 캐스팅 사진";
        img.addEventListener("click", function () {
          openLightbox(photo.url, "실사 캐스팅 사진");
        });
        fig.appendChild(img);
      } else {
        fig.appendChild(el("p", "vnew-hint", "사진을 불러오지 못했습니다."));
      }
      var caption = castingPhotoCaption(photo, cast);
      if (caption) fig.appendChild(el("figcaption", null, caption));
      if (chosen) {
        fig.appendChild(el("span", "vnew-casting-badge", "실사 버전에 쓰는 사진"));
      } else {
        var pick = el("button", "outline-button vnew-casting-pick", "이 사진으로");
        pick.type = "button";
        pick.addEventListener("click", function () {
          choose(photo.id, pick);
        });
        fig.appendChild(pick);
      }
      return fig;
    }

    function photoGrid(list) {
      var grid = el("div", "vnew-casting-grid");
      list.forEach(function (photo) {
        grid.appendChild(photoCard(photo));
      });
      return grid;
    }

    function validPhotos(list) {
      return (Array.isArray(list) ? list : []).filter(function (photo) {
        return photo && photo.id;
      });
    }

    var candidates = validPhotos(cast.candidates);
    if (candidates.length) section.appendChild(photoGrid(candidates));
    var history = validPhotos(cast.history);
    if (history.length) {
      var fold = el("details", "vnew-casting-history");
      fold.appendChild(el("summary", null, "이전 사진 (" + history.length + ")"));
      fold.appendChild(photoGrid(history));
      // 고른 사진이 이전 사진에 있으면 펼쳐 둔다(배지가 보이게).
      fold.open = !!cast.chosen && history.some(function (photo) {
        return photo.id === cast.chosen;
      });
      section.appendChild(fold);
    }
    return section;
  }

  /* ---- 추가 복장 섹션 ---- */

  // 추가 복장: 기본 복장 위에 더 입히는 옷차림. auto 후보(분석)·사용자 추가 카드를 보여 주고,
  // [이미지 만들기]/[다시 만들기]/삭제, 결과는 두 그림체 정면·후면을 나란히. 3D 는 만들지 않는다.
  function outfitsSection(host, detail) {
    var section = el("section", "panel vnew-outfits");
    var headRow = el("div", "vnew-section-head");
    headRow.appendChild(el("h2", null, "추가 복장"));
    section.appendChild(headRow);
    section.appendChild(
      el(
        "p",
        "vnew-hint",
        outfitsHintText(detail)
      )
    );

    var madeBefore = (detail.version || 0) > 0;
    var outfits = detail.outfits || [];

    var list = el("div", "vnew-outfit-list");
    if (!outfits.length) {
      list.appendChild(el("p", "vnew-hint", "아직 추가 복장이 없습니다. 아래에서 넣어 보세요."));
    }
    outfits.forEach(function (outfit) {
      list.appendChild(outfitCard(host, detail, outfit, madeBefore));
    });
    section.appendChild(list);

    // [추가 복장 넣기] → 이름·설명 입력 창
    section.appendChild(addOutfitForm(host, detail));
    return section;
  }

  function outfitCard(host, detail, outfit, madeBefore) {
    var card = el("div", "vnew-outfit-card");
    var top = el("div", "vnew-outfit-top");
    var title = el("span", "vnew-outfit-name", outfit.labelKo || outfit.promptEn || "추가 복장");
    top.appendChild(title);
    // 배지: 사진 속 옷(kind photo, char-fidelity R5.4) · 후보(auto) · 없음
    var badge = outfitBadgeText(outfit, detail);
    if (badge) {
      top.appendChild(el("span", "vnew-outfit-badge", badge));
    }
    card.appendChild(top);
    if (outfit.promptEn) {
      card.appendChild(el("p", "vnew-outfit-prompt", outfit.promptEn));
    }

    var variants = outfit.variants || {};
    // 상태: 하나라도 images 면 만드는 중(schedulePoll 의 outfitsBuilding 과 같은 기준), 하나라도 ready·failed 면 결과 칸.
    // queued 는 "아직 안 만듦"(서버 _empty_outfit_variant 의 기본값)이다. 서버 start_build_outfit 도 images 일 때만 409 다.
    var anyBuilding = false;
    var anyReady = false;
    var anyFailed = false;
    VARIANTS.forEach(function (meta) {
      var ov = variants[meta.key] || {};
      if (ov.status === "images") anyBuilding = true;
      if (ov.status === "ready") anyReady = true;
      if (ov.status === "failed") anyFailed = true;
    });

    var error = el("p", "login-error vnew-outfit-error");
    error.setAttribute("role", "alert");
    error.style.display = "none";

    // 결과: 두 그림체 정면·후면 나란히. 실패한 그림체는 오류 문구, 그리는 그림체는 "만드는 중…".
    if (anyReady || anyFailed) {
      var grid = el("div", "vnew-outfit-grid");
      VARIANTS.forEach(function (meta) {
        var ov = variants[meta.key] || {};
        var col = el("div", "vnew-outfit-col");
        col.appendChild(el("h3", "vnew-outfit-col-title", meta.title));
        if (ov.status === "ready") {
          [["frontUrl", "정면"], ["backUrl", "후면"]].forEach(function (pair) {
            var url = ov[pair[0]];
            if (!url) return;
            var fig = el("figure", "vnew-outfit-fig");
            var img = el("img");
            img.src = url;
            img.alt = meta.title + " " + pair[1];
            img.addEventListener("click", function () {
              openLightbox(url, (outfit.labelKo || "추가 복장") + " " + meta.title + " " + pair[1]);
            });
            fig.appendChild(img);
            fig.appendChild(el("figcaption", null, pair[1]));
            col.appendChild(fig);
          });
        } else if (ov.status === "failed") {
          col.appendChild(el("p", "login-error", ov.error || "만들기에 실패했습니다."));
        } else if (ov.status === "images") {
          col.appendChild(el("p", "vnew-hint", "만드는 중…"));
        }
        grid.appendChild(col);
      });
      card.appendChild(grid);
    } else if (anyBuilding) {
      // 결과 칸이 없을 때(그린 그림·오류가 아직 없음)도 버튼 줄 바로 위에 한 줄을 둔다.
      card.appendChild(el("p", "vnew-hint vnew-outfit-building", "만드는 중…"));
    }

    // 버튼 줄: [이미지 만들기]/[다시 만들기] + 삭제
    var btnRow = el("div", "vnew-outfit-btns");
    var makeBtn = el("button", "primary-button vnew-outfit-make", anyReady ? "다시 만들기" : "이미지 만들기");
    makeBtn.type = "button";
    makeBtn.disabled = anyBuilding || !madeBefore;
    if (!madeBefore) {
      makeBtn.title = "먼저 캐릭터(기본 복장)를 만들어 주세요.";
    }
    makeBtn.addEventListener("click", function () {
      var okConfirm = true;
      try {
        okConfirm = window.confirm("추가 복장 이미지를 만듭니다(두 그림체 정면·후면). 비용이 듭니다. 계속할까요?");
      } catch (e) {
        okConfirm = true;
      }
      if (!okConfirm) return;
      makeBtn.disabled = true;
      error.style.display = "none";
      api(
        "/api/vnew/characters/" +
          encodeURIComponent(detail.id) +
          "/outfits/" +
          encodeURIComponent(outfit.id) +
          "/build",
        { method: "POST" }
      )
        .then(function () {
          refreshDetail(host, false);
        })
        .catch(function (err) {
          makeBtn.disabled = false;
          error.style.display = "";
          error.textContent = "만들기를 시작하지 못했습니다: " + errMessage(err);
        });
    });
    btnRow.appendChild(makeBtn);

    var delBtn = el("button", "text-action danger vnew-outfit-del", "삭제");
    delBtn.type = "button";
    delBtn.disabled = anyBuilding;
    delBtn.addEventListener("click", function () {
      var okConfirm = true;
      try {
        okConfirm = window.confirm("이 추가 복장을 삭제할까요?");
      } catch (e) {
        okConfirm = true;
      }
      if (!okConfirm) return;
      delBtn.disabled = true;
      api(
        "/api/vnew/characters/" +
          encodeURIComponent(detail.id) +
          "/outfits/" +
          encodeURIComponent(outfit.id),
        { method: "DELETE" }
      )
        .then(function () {
          refreshDetail(host, false);
        })
        .catch(function (err) {
          delBtn.disabled = false;
          error.style.display = "";
          error.textContent = "삭제하지 못했습니다: " + errMessage(err);
        });
    });
    btnRow.appendChild(delBtn);
    card.appendChild(btnRow);
    card.appendChild(error);
    return card;
  }

  function addOutfitForm(host, detail) {
    var wrap = el("div", "vnew-outfit-add");
    var toggle = el("button", "outline-button vnew-outfit-add-toggle", "추가 복장 넣기");
    toggle.type = "button";
    wrap.appendChild(toggle);

    var panel = el("div", "vnew-outfit-add-panel");
    panel.style.display = "none";
    panel.appendChild(el("label", "vnew-rebuild-label", "이름"));
    var nameInput = el("input", "vnew-outfit-name-input");
    nameInput.type = "text";
    nameInput.maxLength = 60;
    nameInput.placeholder = "예: 정장 자켓";
    panel.appendChild(nameInput);
    panel.appendChild(el("label", "vnew-rebuild-label", "설명 (선택)"));
    var descInput = el("textarea", "vnew-rebuild-text");
    descInput.rows = 2;
    descInput.maxLength = 300;
    descInput.placeholder = "예: 셔츠 위에 검은 정장 자켓을 걸친다";
    panel.appendChild(descInput);

    var error = el("p", "login-error vnew-outfit-add-error");
    error.setAttribute("role", "alert");
    error.style.display = "none";
    panel.appendChild(error);

    var btns = el("div", "vnew-rebuild-btns");
    var saveBtn = el("button", "primary-button", "넣기");
    saveBtn.type = "button";
    var cancelBtn = el("button", "outline-button", "취소");
    cancelBtn.type = "button";
    btns.appendChild(saveBtn);
    btns.appendChild(cancelBtn);
    panel.appendChild(btns);
    wrap.appendChild(panel);

    toggle.addEventListener("click", function () {
      panel.style.display = panel.style.display === "none" ? "" : "none";
      if (panel.style.display === "") nameInput.focus();
    });
    cancelBtn.addEventListener("click", function () {
      panel.style.display = "none";
    });
    saveBtn.addEventListener("click", function () {
      if (!nameInput.value && !descInput.value) {
        error.style.display = "";
        error.textContent = "이름이나 설명을 넣어 주세요.";
        return;
      }
      saveBtn.disabled = true;
      error.style.display = "none";
      api("/api/vnew/characters/" + encodeURIComponent(detail.id) + "/outfits", {
        method: "POST",
        body: JSON.stringify({ labelKo: nameInput.value || "", promptKo: descInput.value || "" }),
        headers: { "Content-Type": "application/json" }
      })
        .then(function () {
          refreshDetail(host, false);
        })
        .catch(function (err) {
          saveBtn.disabled = false;
          error.style.display = "";
          error.textContent = "넣지 못했습니다: " + errMessage(err);
        });
    });
    return wrap;
  }

  function viewer3d(host, detail, meta, v, model3d) {
    var wrap = el("div", "vnew-3d");
    var status = model3d.status || "queued";

    if (status === "ready" && model3d.hasGlb) {
      var viewer = document.createElement("model-viewer");
      viewer.id = "vnew-mv-" + meta.key;
      viewer.className = "vnew-model-viewer";
      viewer.setAttribute("camera-controls", "");
      viewer.setAttribute("touch-action", "pan-y");
      viewer.setAttribute("auto-rotate", "");
      viewer.setAttribute("shadow-intensity", "0.6");
      viewer.setAttribute("exposure", "1.1");
      if (model3d.thumbUrl) viewer.setAttribute("poster", model3d.thumbUrl);
      if (state.glbCache[meta.key]) {
        viewer.setAttribute("src", state.glbCache[meta.key]);
      } else {
        loadGlb(detail.id, meta.key);
      }
      wrap.appendChild(viewer);

      // 카메라 각도 버튼 + 자동 회전 토글
      var controls = el("div", "vnew-3d-controls");
      [
        ["정면", "0deg 75deg 105%"],
        ["측면", "90deg 75deg 105%"],
        ["후면", "180deg 75deg 105%"]
      ].forEach(function (pair) {
        var b = el("button", "outline-button vnew-3d-btn", pair[0]);
        b.type = "button";
        b.addEventListener("click", function () {
          viewer.setAttribute("camera-orbit", pair[1]);
        });
        controls.appendChild(b);
      });
      var rotate = el("button", "outline-button vnew-3d-btn is-on", "자동 회전 끄기");
      rotate.type = "button";
      rotate.addEventListener("click", function () {
        if (viewer.hasAttribute("auto-rotate")) {
          viewer.removeAttribute("auto-rotate");
          rotate.textContent = "자동 회전 켜기";
          rotate.classList.remove("is-on");
        } else {
          viewer.setAttribute("auto-rotate", "");
          rotate.textContent = "자동 회전 끄기";
          rotate.classList.add("is-on");
        }
      });
      controls.appendChild(rotate);
      wrap.appendChild(controls);

      var note = el("p", "vnew-hint vnew-mv-note");
      note.id = "vnew-mv-note-" + meta.key;
      wrap.appendChild(note);
    } else if (status === "failed") {
      var err = el("div", "vnew-3d-state is-failed");
      err.appendChild(el("p", "login-error", model3d.error || v.error || "3D 만들기에 실패했습니다."));
      // 정면 이미지가 있으면 시트·정면은 다시 그리지 않고 3D 만 다시 만든다(비용 절약).
      var canRetry3d = !!v.frontUrl;
      var retry = el("button", "outline-action", canRetry3d ? "3D만 다시 만들기" : "다시 만들기");
      retry.type = "button";
      retry.addEventListener("click", function () {
        var okConfirm = false;
        var msg = canRetry3d
          ? "3D 만 다시 만듭니다(시트·정면은 그대로). 비용이 듭니다. 계속할까요?"
          : "다시 만들면 새 버전이 생기고 비용이 듭니다. 계속할까요?";
        try {
          okConfirm = window.confirm(msg);
        } catch (e) {
          okConfirm = true;
        }
        if (!okConfirm) return;
        retry.disabled = true;
        var url = canRetry3d
          ? "/api/vnew/characters/" + encodeURIComponent(detail.id) + "/variants/" + encodeURIComponent(meta.key) + "/retry3d"
          : "/api/vnew/characters/" + encodeURIComponent(detail.id) + "/build";
        api(url, { method: "POST" })
          .then(function () {
            refreshDetail(host, false);
          })
          .catch(function () {
            retry.disabled = false;
          });
      });
      err.appendChild(retry);
      wrap.appendChild(err);
    } else {
      var text = "3D 모델을 준비하고 있습니다…";
      if (status === "queued" && (!v.status || v.status === "queued" || v.status === "draft")) {
        text = "아직 3D 모델이 없습니다.";
      }
      var pending = el("div", "vnew-3d-state");
      pending.appendChild(el("p", "vnew-hint", text));
      wrap.appendChild(pending);
    }
    return wrap;
  }

  /* ================================================================ 영상만들기_신규 계획(①②③)

     #vnew-root 에 ①입력 ②대본·상황 ③콘티 화면을 그린다. 서버 /api/vnew/p/<pid>/* 응답(plan.py)
     모양을 그대로 쓴다. ④영상·⑤사운드는 다음 단계라 안내만 보인다. */

  var PLAN_STEPS = [
    ["①", "웹툰 이미지"],
    ["②", "대본·상황"],
    ["③", "콘티"],
    ["④", "프리비즈(3D)"],
    ["⑤", "영상"],
    ["⑥", "사운드"]
  ];
  var PLAN_POLL_MS = 3000; // ②③ 진행 중 다시 불러오는 간격
  var GEN_POLL_FAST_MS = 3000; // ④ 이미지·제출 단계
  var GEN_POLL_SLOW_MS = 5000; // ④ 그 밖(제출 후 조회 대기)
  var ORDER_SAVE_MS = 600; // ① 순서 바꾼 뒤 저장까지 기다리는 디바운스
  var MIN_SECONDS = 1;
  var MAX_SECONDS = 60;

  function planHost() {
    return byId(ROOT_ID);
  }

  function stopPlanPolling() {
    if (state.planPollTimer) {
      window.clearTimeout(state.planPollTimer);
      state.planPollTimer = 0;
    }
  }

  /* ================================================================ 진행 표시(재사용 컴포넌트)

     긴 작업의 진행을 공통으로 보여 주는 조각. 서버 progress({step,done,total,startedAt,updatedAt})를
     받아 회전 스피너 + 단계 문구 + 진행 막대 + 경과 시간을 그린다. 경과 시간은 1초마다 갱신하고
     (startProgressTicker), 마지막 갱신(updatedAt)이 3분 넘게 없으면 '응답이 늦어지고 있습니다'를 덧붙인다.
     공간 카드만이 아니라 다른 긴 작업에도 그대로 쓸 수 있다.

     options(선택): { stepLabels, designSteps } 로 단계 문구를 바꿀 수 있다. 기본은 공간 시트용. */

  var PROGRESS_STALE_MS = 3 * 60 * 1000; // 마지막 갱신이 이보다 오래면 '응답이 늦어지고 있습니다'.

  /* 공간 시트 기본 단계 문구. step → 문구(함수면 done/total 을 받는다). */
  function locProgressLabel(step, done, total) {
    if (step === "design") return "공간 설계 중";
    if (step === "facts") return "웹툰에서 사실 읽는 중";
    if (step === "prompts") return "상세 프롬프트 작성 중";
    if (step === "images") return "벽·바닥 그림 그리는 중 " + (done || 0) + "/" + (total || 0);
    if (step === "verify") return "웹툰과 비교·보정 중";
    if (step === "cut") return "벽 잘라 펴는 중";
    if (step === "sheet") return "4면 시트 합치는 중";
    if (step === "done") return "마무리 중";
    return "준비 중";
  }

  /* 프리비즈 설계 단계 문구. step → 문구(함수면 done/total 을 받는다). */
  function previzProgressLabel(step, done, total) {
    var tail = total > 0 ? " " + (done || 0) + "/" + (total || 0) : "";
    if (step === "panels") return "웹툰 컷 연출 읽는 중" + tail;
    if (step === "design") return "인물·소품 배치 중" + tail;
    if (step === "camera") return "카메라 자리 계산 중" + tail;
    if (step === "done") return "마무리 중";
    return "준비 중";
  }

  /* 경과 초 → "2분 14초 경과" / "14초 경과". */
  function formatElapsed(totalSeconds) {    var s = Math.max(0, Math.floor(totalSeconds));
    var mins = Math.floor(s / 60);
    var secs = s % 60;
    if (mins > 0) return mins + "분 " + secs + "초 경과";
    return secs + "초 경과";
  }

  function parseTime(iso) {
    if (!iso) return 0;
    var ms = Date.parse(iso);
    return isNaN(ms) ? 0 : ms;
  }

  /* 진행 표시 한 조각. progress 가 없거나 step 이 비면 null(그릴 것 없음).
     labelFn(step, done, total) 로 단계 문구를 바꿀 수 있다(기본 공간 시트용). */
  function progressIndicator(progress, labelFn, opts) {
    progress = progress || {};
    var step = progress.step || "";
    if (!step || step === "done") return null; // done 이면 곧 ready — 굳이 안 보인다.
    var label = labelFn || locProgressLabel;
    var done = Number(progress.done) || 0;
    var total = Number(progress.total) || 0;
    // 기본: 그림 단계(images)만 done/total 막대. 설계·사실·프롬프트·비교는 움직이는 막대.
    // determinateSteps 를 주면 그 step 들을 done/total 막대로 본다(프리비즈 panels·design·fit).
    var determinateSteps = opts && opts.determinateSteps;
    var isDeterminateStep = determinateSteps
      ? determinateSteps.indexOf(step) !== -1
      : step === "images";
    var indeterminate = !isDeterminateStep || total <= 0;

    var wrap = el("div", "vnew-prog");
    wrap.setAttribute("data-progress-live", "1");
    wrap.setAttribute("data-started", progress.startedAt || "");
    wrap.setAttribute("data-updated", progress.updatedAt || "");

    var top = el("div", "vnew-prog-top");
    top.appendChild(el("span", "vnew-prog-spinner", ""));
    top.appendChild(el("span", "vnew-prog-step", label(step, done, total)));
    wrap.appendChild(top);

    var bar = el("div", "vnew-prog-bar" + (indeterminate ? " is-indeterminate" : ""));
    var fill = el("div", "vnew-prog-fill");
    if (!indeterminate) {
      var pct = total > 0 ? Math.round((done / total) * 100) : 0;
      fill.style.width = Math.max(0, Math.min(100, pct)) + "%";
    }
    bar.appendChild(fill);
    wrap.appendChild(bar);

    var elapsed = el("div", "vnew-prog-elapsed", "");
    wrap.appendChild(elapsed);

    // 즉시 한 번 경과 시간을 채우고, 1초 ticker 를 켠다.
    updateProgressElapsed(wrap);
    startProgressTicker();
    return wrap;
  }

  /* 한 진행 조각의 경과 시간·지연 안내를 지금 시각 기준으로 갱신한다. */
  function updateProgressElapsed(node) {
    if (!node) return;
    var started = parseTime(node.getAttribute("data-started"));
    var updated = parseTime(node.getAttribute("data-updated"));
    var now = Date.now();
    var elapsed = node.querySelector(".vnew-prog-elapsed");
    if (!elapsed) return;
    var text = started ? formatElapsed((now - started) / 1000) : "";
    if (updated && now - updated > PROGRESS_STALE_MS) {
      elapsed.textContent = text + " · 응답이 늦어지고 있습니다";
      elapsed.classList.add("is-stale");
    } else {
      elapsed.textContent = text;
      elapsed.classList.remove("is-stale");
    }
  }

  /* 1초마다 화면의 모든 진행 조각 경과 시간을 갱신한다. 조각이 하나도 없으면 스스로 멈춘다. */
  function startProgressTicker() {
    if (state.progressTicker) return;
    state.progressTicker = window.setInterval(function () {
      if (!state.mounted) {
        stopProgressTicker();
        return;
      }
      var nodes = document.querySelectorAll("[data-progress-live]");
      if (!nodes.length) {
        stopProgressTicker();
        return;
      }
      for (var i = 0; i < nodes.length; i++) updateProgressElapsed(nodes[i]);
    }, 1000);
  }

  function stopProgressTicker() {
    if (state.progressTicker) {
      window.clearInterval(state.progressTicker);
      state.progressTicker = 0;
    }
  }

  /* ① 영상 모델 단가 문구(073 R16.8, 설계자 2026-10-28). 단가를 모르면(priceKnown false) "단가 미정".
     목록(infoLine false)은 "$0.206/초", 고른 모델 설명 줄(infoLine true)은 "초당 $0.206".
     priceResolution(서버가 실제 전송 해상도로 계산한 기준)이 있으면 끝에 "(480p)"처럼 붙인다. */
  function previzPriceText(model, infoLine) {
    if (!model || !model.priceKnown) return "단가 미정";
    var amount = "$" + (Number(model.usdPerSecond) || 0).toFixed(3);
    var text = infoLine ? "초당 " + amount : amount + "/초";
    if (model.priceResolution) text += "(" + String(model.priceResolution) + ")";
    return text;
  }

  /* 선택 모델의 한 번 생성 최대 길이(초). plan.models 에서 찾는다(없으면 0). */
  function modelMaxSeconds() {
    var plan = state.plan || {};
    var models = plan.models || [];
    var chosen = (plan.input || {}).videoModel || "";
    for (var i = 0; i < models.length; i++) {
      if (models[i] && models[i].id === chosen) {
        return Number(models[i].maxDurationS) || 0;
      }
    }
    return 0;
  }

  /* 웹툰 이미지(order 순)의 썸네일 URL 목록. 콘티·대본에서 참고 컷으로 쓴다. */
  function orderedImages() {
    var inp = (state.plan || {}).input || {};
    var byId2 = {};
    (inp.images || []).forEach(function (img) {
      byId2[img.fileId] = img;
    });
    return (inp.order || []).map(function (fid) {
      return byId2[fid] || null;
    });
  }

  /* 패널 번호(정수)로 참고 웹툰 컷 썸네일을 돌려준다. 패널은 타일 기준이라 1:1 이 아니므로
     이미지 개수로 나눠 근사한다(없으면 null). */
  function panelThumb(panel) {
    var imgs = orderedImages().filter(function (x) {
      return x;
    });
    if (!imgs.length) return null;
    var index = Math.max(0, Math.min(imgs.length - 1, Number(panel) || 0));
    return imgs[index];
  }

  /* ---------------------------------------------------------------- 단계 막대 */

  /* 어느 단계까지 갈 수 있는가. ①②③은 콘티 ready 까지의 규칙을 따르고(기존),
     ④ 프리비즈는 콘티가 ready 면, ⑤ 영상은 프리비즈 녹화가 모두 끝나면(previz.recordedAll),
     ⑥ 사운드는 gen.selected 가 있으면 열린다(설계자 2026-10-21).
     ⑦ 편집은 gen.selected 가 있고 서버가 편집 재료가 있다고 줄 때(gen.edit.available, VNEW_EDIT 켬) 열린다(지시서 111). */
  function editAvailable() {
    var gen = (state.plan || {}).gen || {};
    return !!(gen.edit && gen.edit.available === true);
  }

  /* 잠긴 ⑦ 칩의 이유(지시서 115): 서버 gen.edit.reason(gen 이 없어도 온다), 없으면 기본 문구. */
  var EDIT_LOCK_TEXT = "⑤에서 영상을 먼저 만들면 편집할 수 있습니다.";

  function editLockReason() {
    var edit = ((state.plan || {}).gen || {}).edit || {};
    return typeof edit.reason === "string" && edit.reason ? edit.reason : EDIT_LOCK_TEXT;
  }

  /* 단계 막대 아래 한 줄 안내(잠긴 칩을 눌렀을 때, 지시서 115). 막대 끝에 처음 한 번 붙이고 다음부터 글만 바꾼다. */
  function planStepNote(bar, text) {
    var note = bar._note;
    if (!note) {
      note = el("p", "vnew-hint vnew-plan-step-note", "");
      note.setAttribute("role", "status");
      bar._note = note;
      bar.appendChild(note);
    }
    note.textContent = text;
  }

  function maxReachableStep() {
    var plan = state.plan || {};
    var conti = plan.conti || {};
    var previz = plan.previz || {};
    var gen = plan.gen || {};
    if (gen.selected && gen.edit && gen.edit.available === true) return 7;
    if (gen.selected) return 6;
    if (previz.recordedAll) return 5;
    if (conti.status === "ready" && (conti.cuts || []).length) return 4;
    return 3;
  }

  /* 단계 막대 목록. ⑦ 편집은 서버가 gen.edit 을 줄 때만(설정 VNEW_EDIT 켬, design §5.1) 보인다. */
  function planStepList() {
    var gen = (state.plan || {}).gen || {};
    return gen.edit && typeof gen.edit === "object" ? PLAN_STEPS.concat([["⑦", "편집"]]) : PLAN_STEPS;
  }

  function planStepBar() {
    var bar = el("nav", "vnew-plan-steps");
    var maxStep = maxReachableStep();
    var busy = planBusySteps(state.plan);
    planStepList().forEach(function (step, index) {
      var stepNo = index + 1;
      var item = el("button", "vnew-plan-step");
      item.type = "button";
      if (stepNo === state.planStep) item.classList.add("is-current");
      if (stepNo < state.planStep) item.classList.add("is-done");
      item.appendChild(el("span", "vnew-plan-step-mark", step[0]));
      item.appendChild(el("span", "vnew-plan-step-text", step[1]));
      // 다른 단계에서 작업이 진행 중이면 그 칩에 "진행 중"만 단다. 화면은 옮기지 않는다(지시서 129).
      if (busy[stepNo] && stepNo !== state.planStep) {
        item.classList.add("is-busy");
        item.appendChild(el("span", "vnew-plan-step-busy", "진행 중"));
      }
      // 갈 수 있는 단계까지만 활성화. 그 뒤 단계는 아직 안내만(is-soon).
      // ⑦ 편집은 영상 선택 없이도(부분 성공 gen) 서버가 재료가 있다고 주면 연다(dev 화면 확인 2026-11-02).
      var canGo = stepNo <= maxStep || (stepNo === 7 && editAvailable());
      // 지금 보고 있는 칩은 흐리지 않는다(⑦ 에 있는 동안 재료가 없어져도, 지시서 115).
      if (!canGo && stepNo !== state.planStep) item.classList.add("is-soon");
      // 잠긴 ⑦ 은 이유를 title 로 보이고, 누르면 막대 아래 한 줄로 보인다(지시서 115).
      if (stepNo === 7 && !canGo) {
        item.title = editLockReason();
        item.setAttribute("aria-disabled", "true");
      }
      item.addEventListener("click", function () {
        if (stepNo === state.planStep) return;
        if (stepNo <= maxStep || (stepNo === 7 && editAvailable())) {
          // 작업 201: ④ 프리비즈에서 저장하지 않은 수정이 있으면 떠나기 전에 묻는다.
          if (state.planStep === 4 && !previzConfirmLeave()) return;
          if (state.planStep === 7) editLeave(); // ⑦ 을 떠나면 남은 저장을 보내고 재생·키를 멈춘다
          state.planStep = stepNo;
          renderPlan();
        } else if (stepNo === 7) {
          planStepNote(bar, editLockReason());
        }
      });
      bar.appendChild(item);
    });
    return bar;
  }

  /* 공통 오류 줄(서버 envelope message). */
  function planError(message) {
    var p = el("p", "login-error vnew-plan-error");
    p.setAttribute("role", "alert");
    if (message) {
      p.textContent = message;
    } else {
      p.style.display = "none";
    }
    return p;
  }

  /* ---------------------------------------------------------------- 불러오기 · 저장 */

  /* fromPoll(선택): schedulePlanPoll 의 폴링이면 true. ⑤에서 화면에 보이는 상태가 그대로면 다시 그리지 않는다(genPollUnchanged). */
  function loadPlan(first, fromPoll) {
    var host = planHost();
    if (!host) return;
    if (first) {
      state.genViewSig = "";
      clear(host);
      host.appendChild(el("p", "vnew-boot", "불러오는 중…"));
    }
    var pid = state.projectId;
    api("/api/vnew/p/" + encodeURIComponent(pid) + "/plan")
      .then(function (plan) {
        if (!state.mounted || state.projectId !== pid) return;
        var prevPlan = state.plan;
        state.plan = plan;
        // 지시서 118b: 서버에 저장된 대본 서명(속마음 찾기 전 저장 여부). scriptSigOf 와 같은 꼴을 여기서 바로 만든다.
        var savedScript = (plan || {}).script || {};
        state.scriptSig = JSON.stringify({ setting: savedScript.setting || {}, entries: savedScript.entries || [] });
        var editOpen = editOpenFor(pid);
        var stepBefore = state.planStep;
        decidePlanStep();
        // ⑦ 편집이 열려 있으면(지시서 115) 처음 불러오기가 아닌 응답으로 다시 그리지 않는다 — ⑦ 이 ⑤ 로 밀려나거나 처음부터
        // 그려져 실행 취소·재생헤드를 잃지 않게(⑦ 은 자기 GET /edit 만 부른다). 단계가 바뀌었으면 남은 저장을 먼저 보낸다.
        if (editOpen && !first && state.planStep === 7) return;
        if (editOpen && !first) editLeave();
        // 지시서 129: 다른 단계의 작업만 진행 중인 폴링이면(지금 단계는 전·후 모두 진행 중 아님) 본문을 다시 그리지 않는다 —
        // 이전 단계에서 고치던 내용이 지워지지 않게. 단계 막대("진행 중" 표시·갈 수 있는 단계)만 맞춘다.
        if (fromPoll === true && !first && state.planStep === stepBefore && !planBusySteps(prevPlan)[state.planStep] &&
            !planBusySteps(plan)[state.planStep] && planBarSync(host)) {
          schedulePlanPoll();
          return;
        }
        // ⑤ 폴링: 상태 서명이 지금 그린 ⑤와 같으면 다시 그리지 않고 폴링만 다시 건다(재생 중인 영상이 끊기지 않게).
        // state.plan 은 위에서 새 값으로 바꿨다(다음 버튼 동작이 새 데이터를 쓴다).
        if (fromPoll === true && genPollUnchanged()) {
          schedulePlanPoll();
          return;
        }
        renderPlan();
      })
      .catch(function (err) {
        if (!state.mounted || state.projectId !== pid) return;
        state.genViewSig = "";
        clear(host);
        host.appendChild(el("h1", "vnew-h1", "영상만들기_신규"));
        host.appendChild(planError("불러오지 못했습니다: " + errMessage(err)));
        var retry = el("button", "outline-button", "다시 시도");
        retry.type = "button";
        retry.addEventListener("click", function () {
          loadPlan(true);
        });
        host.appendChild(retry);
      });
  }

  /* 처음 열 때(또는 task 진행 중) 알맞은 단계를 고른다. 처음 열 때(planStep 0)만 진행 중인 작업 단계를 보여 준다.
     이미 단계를 보고 있으면(1~7) 폴링·다시 불러오기로는 옮기지 않는다(지시서 129) — 다른 단계의 작업은 단계 막대에
     "진행 중"으로만 보인다. 작업을 시작한 버튼이 그 단계로 가야 하면 그 처리기가 state.planStep 을 정한다. */
  function decidePlanStep() {
    var plan = state.plan || {};
    var task = plan.task || {};
    var previz = plan.previz || {};
    var gen = plan.gen || {};
    // ⑦ 편집에 있으면(서버가 gen.edit 을 주는 동안) 그대로 둔다(지시서 115). 폴링·다시 불러오기로 gen 이 진행 중·partial 이
    // 되거나(실사 회차 시작·다시 만들기), gen.selected 가 비거나, 재료가 없어져도 ⑤ 로 밀려나지 않는다 — ⑦ 화면이 안내한다.
    if (state.planStep === 7 && gen.edit && typeof gen.edit === "object") return;
    // 사용자가 이미 단계를 골랐으면(1~7) 그대로 둔다(단, 갈 수 없는 단계면 되돌린다).
    if (state.planStep >= 1 && state.planStep <= 7) {
      var maxStep = maxReachableStep();
      if (state.planStep > maxStep) state.planStep = maxStep;
      return;
    }
    // 처음 열 때: 영상 생성이 진행 중(running·partial)이면 ⑤를 보여 준다.
    if (gen.status === "running" || gen.status === "partial") {
      state.planStep = 5;
      return;
    }
    // 프리비즈 설계가 진행 중이면 ④를 보여 준다.
    if (previz.status === "designing") {
      state.planStep = 4;
      return;
    }
    if (task.status === "running") {
      // 콘티·기호 채우기(fxFill, vnew-live-fx)는 ③, 대본은 ②.
      state.planStep = task.name === "conti" || task.name === "fxFill" ? 3 : 2;
      if (task.name === "rereadActions") state.planStep = 3; // 작업 162: 손 주인 다시 읽기도 ③
      return;
    }
    // 진행 중인 작업이 없으면: gen.selected 면 ⑥, gen 이 있으면 ⑤, 프리비즈가 있으면 ④, 아니면 ①.
    if (gen.selected) {
      state.planStep = 6;
      return;
    }
    if (gen.status && gen.status !== "none") {
      state.planStep = 5;
      return;
    }
    if (previz.status && previz.status !== "none") {
      state.planStep = 4;
      return;
    }
    state.planStep = 1;
  }

  /* 진행 중인 작업이 있는 단계 {단계 번호: true}(지시서 129). 폴링을 거는 조건(schedulePlanPoll)과 같은 것만 본다:
     task(콘티·기호 채우기는 ③, 그 밖은 ②)·공간 시트(③)·프리비즈 설계(④)·영상 생성·촬영 지시서(⑤). partial 은 진행 중이 아니다. */
  function planBusySteps(plan) {
    var p = plan || {};
    var task = p.task || {};
    var previz = p.previz || {};
    var busy = {};
    if (task.status === "running") busy[task.name === "conti" || task.name === "fxFill" || task.name === "rereadActions" ? 3 : 2] = true;
    if (locationsBuilding(p)) busy[3] = true;
    if (previz.status === "designing") busy[4] = true;
    if ((p.gen || {}).status === "running" || (previz.shotBrief || {}).status === "running") busy[5] = true;
    return busy;
  }

  /* 폴링에서 본문을 다시 그리지 않을 때 단계 막대만 맞춘다(지시서 129). 막대 칩(글·클래스·title)이 그대로면 그대로 두고,
     바뀌었으면 막대만 갈아 끼운다. 그린 막대가 화면에 없으면(오류 화면 등) false — 호출자가 다시 그린다. */
  function planBarSync(host) {
    var old = state.planBar;
    if (!old || old.parentNode !== host) return false;
    function sig(bar) {
      return JSON.stringify(Array.prototype.slice.call(bar.children || []).map(function (c) { return [c.className, c.textContent, c.title || ""]; }));
    }
    var bar = planStepBar();
    if (sig(bar) === sig(old)) return true;
    host.replaceChild(bar, old);
    state.planBar = bar;
    return true;
  }

  /* 진행 중(task.running 또는 gen.running)이면 다시 불러온다. 영상 생성은 이미지·제출 단계에서
     3초, 그 밖에는 5초 간격(설계자 2026-10-20). */
  function schedulePlanPoll() {
    stopPlanPolling();
    // ⑦ 편집은 계획을 다시 불러오지 않는다(다시 그리면 편집 화면이 처음부터 그려진다). ⑦ 은 자기 GET /edit 만 부른다.
    if (state.planStep === 7) return;
    var plan = state.plan || {};
    var task = plan.task || {};
    var previz = plan.previz || {};
    var gen = plan.gen || {};
    // 작업 221: 통 테이크 실사 칸을 만드는 중(takeRealCounts active)이면 gen.status 가 partial(멈춤 보기)이어도 다시 불러온다.
    // 만드는 칸이 없으면 지금처럼 멈춘다.
    var genRunning = gen.status === "running" || takeRealActive(gen);
    var previzDesigning = previz.status === "designing";
    var locBuilding = locationsBuilding(plan);
    var briefRunning = (previz.shotBrief || {}).status === "running"; // 촬영 지시서 만드는 중(약 1분)도 다시 불러온다
    if (task.status !== "running" && !genRunning && !previzDesigning && !locBuilding && !briefRunning) return;
    var interval = PLAN_POLL_MS;
    if (genRunning) {
      // 이미지(opening)·제출 단계면 더 자주(3초), 그 밖에는 5초.
      interval = genBusyFast(gen) ? GEN_POLL_FAST_MS : GEN_POLL_SLOW_MS;
    }
    state.planPollTimer = window.setTimeout(function () {
      if (!state.mounted) return;
      loadPlan(false, true);
    }, interval);
  }

  /* 공간 시트를 만드는 중인 장소가 하나라도 있으면 true(③ 콘티 화면 폴링용). */
  function locationsBuilding(plan) {
    var locations = (plan || {}).locations || [];
    for (var i = 0; i < locations.length; i++) {
      var t = (locations[i].task || {}).status;
      if (t === "running" || locations[i].status === "building") return true;
    }
    return false;
  }

  /* opening 이 아직이거나 어떤 칸이 preparing/queued/waiting(제출 직전)이면 빠른 폴링. */
  function genBusyFast(gen) {
    var opening = gen.opening || {};
    if (opening.status && opening.status !== "done" && opening.status !== "failed") return true;
    var cells = (gen.units && gen.units.length) ? gen.units : (gen.segments || []);
    for (var i = 0; i < cells.length; i++) {
      var r = (cells[i].real || {}).status;
      var w = (cells[i].webtoon || {}).status;
      if (r === "preparing" || r === "queued" || r === "waiting") return true;
      if (w === "preparing" || w === "queued" || w === "waiting") return true;
    }
    return false;
  }

  /* 작업 221: 통 테이크 실사 칸을 만드는 중(queued·preparing·submitted)인 테이크가 있으면 true.
     takeRealCounts(gen).active > 0 과 같다(검사 check_take_retry_221 r-g 가 맞춰 본다). 폴링 구역 안에 두어 이 구역만 읽는 검사도 돈다. */
  function takeRealActive(gen) {
    var g = gen || {};
    if (g.mode !== "previz-take" || !Array.isArray(g.units)) return false;
    return g.units.some(function (unit) {
      var status = ((unit || {}).real || {}).status;
      return status === "queued" || status === "preparing" || status === "submitted";
    });
  }

  /* ⑤ 화면에 보이는 gen 상태 서명(095 피드백 2, 2026-10-30). 폴링으로 받은 plan 의 서명이 지금 그린 ⑤와 같으면
     ⑤를 다시 그리지 않는다(실사 회차 중 웹툰 영상 재생이 끊기지 않게). 넣는 것: gen.status, 칸별(units, 없으면
     segments — genBusyFast 와 같다) 웹툰·실사 status·failureKind, finalWebtoon·finalReal 의 status·key, realRun.n,
     realHistory 길이, selected. key 는 영상 주소에서 ?·# 뒤를 뺀 경로다(presigned 서명은 조회마다 바뀐다).
     gen 이 없거나(none) stale 이면 시작 화면(촬영 지시서 진행 등)이라 ""를 돌려주고, 그때는 지금처럼 늘 다시 그린다. */
  function genViewSignature(gen) {
    var g = gen || {};
    if (!g.status || g.status === "none" || g.stale) return "";
    function finalOf(final) {
      var f = final || {};
      return [f.status || "", String(f.videoUrl || "").split("#")[0].split("?")[0]];
    }
    var cells = (g.units && g.units.length) ? g.units : (g.segments || []);
    return JSON.stringify([
      g.status,
      cells.map(function (cell) {
        var w = (cell || {}).webtoon || {};
        var r = (cell || {}).real || {};
        var fb = r.fallback ? [r.fallback.step || 0, r.fallback.done || 0, r.fallback.total || 0, realCutQaLines(r).join("|")] : null; // 작업 130 단계·진행, 133 QA
        return [w.status || "", w.failureKind || "", r.status || "", r.failureKind || "", fb];
      }),
      finalOf(g.finalWebtoon),
      finalOf(g.finalReal),
      g.realRun ? { n: g.realRun.n == null ? null : g.realRun.n } : null,
      Array.isArray(g.realHistory) ? g.realHistory.length : 0,
      g.selected || null
    ]);
  }

  /* 폴링 응답(state.plan 을 새 값으로 바꾸고 decidePlanStep 을 지난 뒤)이 지금 그린 ⑤와 같은 상태인가.
     마지막으로 그린 화면이 ⑤ 결과 화면이고(state.genViewSig 가 비어 있지 않다), 지금 단계도 ⑤이며 서명이 같을 때만 true. */
  function genPollUnchanged() {
    return state.planStep === 5 && !!state.genViewSig && genViewSignature((state.plan || {}).gen) === state.genViewSig;
  }

  /* input 일부를 저장(PUT /input). 성공하면 plan 을 갱신한다(다시 그리기는 호출자 선택). */
  function putInput(patch) {
    var pid = state.projectId;
    return api("/api/vnew/p/" + encodeURIComponent(pid) + "/input", {
      method: "PUT",
      body: JSON.stringify(patch)
    }).then(function (plan) {
      state.plan = plan;
      return plan;
    });
  }

  /* ---------------------------------------------------------------- 그리기 분기 */

  function renderPlan() {
    var host = planHost();
    if (!host || !state.mounted) return;
    schedulePlanPoll();
    clear(host);
    // ⑤를 그리면 그 gen 상태 서명을 둔다(폴링이 같은 상태를 다시 그리지 않게, loadPlan). ⑤가 아니면 비운다.
    state.genViewSig = state.planStep === 5 ? genViewSignature((state.plan || {}).gen) : "";
    // 작업 199: ⑤ 를 새로 열었는가(바로 앞에 그린 단계가 ⑤ 가 아님) — renderGenStep 이 모델 에러 로그 건수를 한 번 받는다.
    state.genFresh = state.planStep === 5 && state.drawnStep !== 5;
    state.drawnStep = state.planStep;

    var head = el("div", "vnew-plan-head");
    head.appendChild(el("h1", "vnew-h1", "영상만들기_신규"));
    head.appendChild(
      el("p", "vnew-lead", "웹툰 이미지를 넣으면 대본·상황을 읽고 콘티까지 만듭니다.")
    );
    host.appendChild(head);
    state.planBar = planStepBar(); // 폴링이 막대만 갈아 끼울 때 쓴다(planBarSync)
    host.appendChild(state.planBar);

    var body = el("div", "vnew-plan-body");
    host.appendChild(body);

    if (state.planStep === 1) {
      renderInputStep(body);
    } else if (state.planStep === 2) {
      renderScriptStep(body);
    } else if (state.planStep === 3) {
      renderContiStep(body);
    } else if (state.planStep === 4) {
      renderPrevizStep(body);
    } else if (state.planStep === 5) {
      renderGenStep(body);
    } else if (state.planStep === 7) {
      renderEditStep(body);
    } else {
      renderSoundStep(body);
    }
  }

  /* ================================================================ ① 입력 */

  function renderInputStep(host) {
    var inp = (state.plan || {}).input || {};
    var images = inp.images || [];
    var order = inp.order || [];

    var error = planError("");
    host.appendChild(error);
    function showError(message) {
      error.style.display = "";
      error.textContent = message;
    }

    // --- 드롭존 ---
    var section = el("section", "panel vnew-input-drop-sec");
    section.appendChild(el("h2", "vnew-sec-title", "웹툰 이미지"));
    section.appendChild(
      el("p", "vnew-hint", "장면 순서대로 올려 주세요. 세로로 긴 웹툰도 됩니다. 최대 40장.")
    );

    var fileInput = el("input");
    fileInput.type = "file";
    fileInput.accept = "image/png,image/jpeg,image/webp";
    fileInput.multiple = true;
    fileInput.style.display = "none";
    section.appendChild(fileInput);

    var drop = el("button", "dropzone vnew-drop");
    drop.type = "button";
    var dropInner = el("div", "drop-empty");
    dropInner.appendChild(el("strong", null, "이미지를 끌어놓거나 클릭하세요"));
    dropInner.appendChild(el("p", null, "PNG · JPG · WEBP · 여러 장"));
    drop.appendChild(dropInner);
    section.appendChild(drop);

    function doUpload(fileList) {
      var files = Array.prototype.slice.call(fileList || []);
      if (!files.length) return;
      var form = new FormData();
      files.forEach(function (file) {
        form.append("files", file, file.name);
      });
      drop.disabled = true;
      api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/uploads", {
        method: "POST",
        body: form
      })
        .then(function (payload) {
          state.plan = payload.plan || state.plan;
          renderPlan();
        })
        .catch(function (err) {
          drop.disabled = false;
          showError("올리지 못했습니다: " + errMessage(err));
        });
    }

    drop.addEventListener("click", function () {
      fileInput.click();
    });
    fileInput.addEventListener("change", function () {
      doUpload(fileInput.files);
      fileInput.value = "";
    });
    drop.addEventListener("dragover", function (event) {
      event.preventDefault();
      drop.classList.add("is-drag");
    });
    drop.addEventListener("dragleave", function () {
      drop.classList.remove("is-drag");
    });
    drop.addEventListener("drop", function (event) {
      event.preventDefault();
      drop.classList.remove("is-drag");
      if (event.dataTransfer) doUpload(event.dataTransfer.files);
    });
    host.appendChild(section);

    // --- 썸네일 그리드(순서·삭제) ---
    if (images.length) {
      var gridSec = el("section", "panel vnew-thumbs-sec");
      var gridHead = el("div", "vnew-sec-head");
      gridHead.appendChild(el("h2", "vnew-sec-title", "순서 (" + order.length + "장)"));
      var sortBtn = el("button", "outline-button vnew-sort-btn", "이름순 자동 정렬");
      sortBtn.type = "button";
      sortBtn.addEventListener("click", function () {
        sortBtn.disabled = true;
        api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/input/sort", {
          method: "POST"
        })
          .then(function (payload) {
            state.plan = payload.plan || state.plan;
            renderPlan();
          })
          .catch(function (err) {
            sortBtn.disabled = false;
            showError("정렬하지 못했습니다: " + errMessage(err));
          });
      });
      gridHead.appendChild(sortBtn);
      gridSec.appendChild(gridHead);

      var grid = el("div", "vnew-thumb-grid");
      var byId2 = {};
      images.forEach(function (img) {
        byId2[img.fileId] = img;
      });
      order.forEach(function (fid, index) {
        var img = byId2[fid];
        if (!img) return;
        grid.appendChild(thumbCard(img, index, order, showError));
      });
      gridSec.appendChild(grid);
      host.appendChild(gridSec);
    }

    // --- 설정(길이 · 모델) ---
    host.appendChild(inputSettings(showError));

    // --- 캐릭터 ---
    host.appendChild(characterSection(showError));

    // --- 부가 설명 ---
    var notesSec = el("section", "panel vnew-notes-sec");
    notesSec.appendChild(el("h2", "vnew-sec-title", "부가 설명 (선택)"));
    notesSec.appendChild(
      el("p", "vnew-hint", "분위기·강조할 점 등 자유롭게. 대본·콘티를 쓸 때 참고합니다.")
    );
    var notes = el("textarea", "vnew-notes");
    notes.rows = 3;
    notes.value = inp.notes || "";
    notes.placeholder = "예: 긴장감 있게, 비 오는 분위기로";
    var notesSaved = el("span", "vnew-saved-mark");
    var notesTimer = 0;
    notes.addEventListener("input", function () {
      if (notesTimer) window.clearTimeout(notesTimer);
      notesTimer = window.setTimeout(function () {
        putInput({ notes: notes.value })
          .then(function () {
            notesSaved.textContent = "저장됨";
          })
          .catch(function (err) {
            showError("부가 설명을 저장하지 못했습니다: " + errMessage(err));
          });
      }, ORDER_SAVE_MS);
    });
    notesSec.appendChild(notes);
    notesSec.appendChild(notesSaved);
    host.appendChild(notesSec);

    // --- 다음 버튼 ---
    var footer = el("div", "vnew-plan-footer");
    var nextBtn = el("button", "primary-button", "대본·상황 정리하기");
    nextBtn.type = "button";
    nextBtn.disabled = order.length < 1;
    nextBtn.addEventListener("click", function () {
      nextBtn.disabled = true;
      // 현재 order 를 저장한 뒤 대본 분석 시작.
      putInput({ order: order })
        .then(function () {
          return api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/script", {
            method: "POST"
          });
        })
        .then(function () {
          state.planStep = 2;
          loadPlan(false);
        })
        .catch(function (err) {
          nextBtn.disabled = false;
          showError("대본 정리를 시작하지 못했습니다: " + errMessage(err));
        });
    });
    footer.appendChild(nextBtn);
    if (order.length < 1) {
      footer.appendChild(el("span", "vnew-hint", "이미지를 1장 이상 올리면 다음으로 갈 수 있습니다."));
    }
    host.appendChild(footer);
  }

  /* 썸네일 카드(번호 배지 · 이름 · 삭제 · 순서 drag·◀▶). */
  function thumbCard(img, index, order, showError) {
    var card = el("div", "vnew-thumb-card");
    card.setAttribute("draggable", "true");
    card.setAttribute("data-fid", img.fileId);

    var badge = el("span", "vnew-thumb-no", String(index + 1));
    card.appendChild(badge);

    var figure = el("div", "vnew-thumb-figure");
    if (img.url) {
      var image = el("img");
      image.src = img.url;
      image.alt = img.name || "";
      image.loading = "lazy";
      image.addEventListener("click", function () {
        openLightbox(img.url, img.name || "");
      });
      figure.appendChild(image);
    } else {
      figure.appendChild(el("span", "vnew-thumb-missing", "미리보기 없음"));
    }
    card.appendChild(figure);

    card.appendChild(el("span", "vnew-thumb-name", img.name || img.fileId));

    var controls = el("div", "vnew-thumb-controls");
    var left = el("button", "icon-button vnew-thumb-move", "◀");
    left.type = "button";
    left.title = "앞으로";
    left.disabled = index === 0;
    left.addEventListener("click", function () {
      moveInOrder(index, index - 1, showError);
    });
    var right = el("button", "icon-button vnew-thumb-move", "▶");
    right.type = "button";
    right.title = "뒤로";
    right.disabled = index === order.length - 1;
    right.addEventListener("click", function () {
      moveInOrder(index, index + 1, showError);
    });
    var del = el("button", "icon-button danger vnew-thumb-del", "×");
    del.type = "button";
    del.title = "삭제";
    del.addEventListener("click", function () {
      del.disabled = true;
      api(
        "/api/vnew/p/" +
          encodeURIComponent(state.projectId) +
          "/uploads/" +
          encodeURIComponent(img.fileId),
        { method: "DELETE" }
      )
        .then(function (plan) {
          state.plan = plan;
          renderPlan();
        })
        .catch(function (err) {
          del.disabled = false;
          showError("삭제하지 못했습니다: " + errMessage(err));
        });
    });
    controls.appendChild(left);
    controls.appendChild(right);
    controls.appendChild(del);
    card.appendChild(controls);

    // HTML5 drag & drop 순서 바꾸기
    card.addEventListener("dragstart", function (event) {
      if (event.dataTransfer) {
        event.dataTransfer.setData("text/plain", String(index));
        event.dataTransfer.effectAllowed = "move";
      }
      card.classList.add("is-dragging");
    });
    card.addEventListener("dragend", function () {
      card.classList.remove("is-dragging");
    });
    card.addEventListener("dragover", function (event) {
      event.preventDefault();
      card.classList.add("is-over");
    });
    card.addEventListener("dragleave", function () {
      card.classList.remove("is-over");
    });
    card.addEventListener("drop", function (event) {
      event.preventDefault();
      card.classList.remove("is-over");
      var from = Number(event.dataTransfer ? event.dataTransfer.getData("text/plain") : NaN);
      if (isNaN(from) || from === index) return;
      moveInOrder(from, index, showError);
    });
    return card;
  }

  /* order 배열에서 from → to 로 옮기고 디바운스 저장한 뒤 다시 그린다. */
  function moveInOrder(from, to, showError) {
    var inp = (state.plan || {}).input || {};
    var order = (inp.order || []).slice();
    if (from < 0 || from >= order.length || to < 0 || to >= order.length) return;
    var moved = order.splice(from, 1)[0];
    order.splice(to, 0, moved);
    inp.order = order; // 즉시 화면에 반영
    renderPlan();
    if (state.orderSaveTimer) window.clearTimeout(state.orderSaveTimer);
    state.orderSaveTimer = window.setTimeout(function () {
      putInput({ order: order }).catch(function (err) {
        if (showError) showError("순서를 저장하지 못했습니다: " + errMessage(err));
      });
    }, ORDER_SAVE_MS);
  }

  /* 길이 · 모델 설정 영역. */
  function inputSettings(showError) {
    var plan = state.plan || {};
    var inp = plan.input || {};
    var models = plan.models || [];

    var section = el("section", "panel vnew-settings-sec");
    section.appendChild(el("h2", "vnew-sec-title", "영상 설정"));

    var grid = el("div", "vnew-settings-grid");

    // 길이
    var lenField = el("div", "vnew-field");
    var lenLabel = el("label", "vnew-field-label", "영상 길이 (초)");
    lenField.appendChild(lenLabel);
    var lenInput = el("input", "vnew-field-input");
    lenInput.type = "number";
    lenInput.min = String(MIN_SECONDS);
    lenInput.max = String(MAX_SECONDS);
    lenInput.step = "1";
    lenInput.value = String(inp.totalSeconds || 30);
    lenField.appendChild(lenInput);
    var maxN = modelMaxSeconds();
    var lenHint = el(
      "p",
      "vnew-field-hint",
      maxN > 0
        ? "모델 최대 " + maxN + "초를 넘으면 여러 번 만들어 자동으로 이어 붙입니다."
        : "모델 최대 길이를 넘으면 여러 번 만들어 자동으로 이어 붙입니다."
    );
    lenField.appendChild(lenHint);
    lenInput.addEventListener("change", function () {
      var value = Math.round(Number(lenInput.value) || 0);
      if (value < MIN_SECONDS) value = MIN_SECONDS;
      if (value > MAX_SECONDS) value = MAX_SECONDS;
      lenInput.value = String(value);
      putInput({ totalSeconds: value }).catch(function (err) {
        showError("길이를 저장하지 못했습니다: " + errMessage(err));
      });
    });
    grid.appendChild(lenField);

    // 모델(설계자 2026-10-22): 기본(Kling O3 edit, 첫 번째·추천) + 등록된 previzCapable fal 모델.
    // 각 항목에 이름·초당 단가·참고 방식(elements/이미지 목록). 프리비즈 미지원 모델은 회색·선택 불가.
    var modelField = el("div", "vnew-field vnew-model-field");
    var modelHead = el("div", "vnew-model-head");
    modelHead.appendChild(el("label", "vnew-field-label", "영상 모델"));
    var catalogBtn = el("button", "outline-button vnew-catalog-btn", "fal 카탈로그 열기");
    catalogBtn.type = "button";
    catalogBtn.addEventListener("click", function () {
      openFalCatalog(showError);
    });
    modelHead.appendChild(catalogBtn);
    modelField.appendChild(modelHead);

    var select = el("select", "vnew-field-input");
    if (!models.length) {
      var opt = el("option", null, inp.videoModel || "기본 모델");
      opt.value = inp.videoModel || "";
      select.appendChild(opt);
      select.disabled = true;
    } else {
      models.forEach(function (model) {
        var refText =
          model.refMode === "elements"
            ? "참고: 인물 요소(elements)"
            : model.refMode === "imageList"
            ? "참고: 이미지 목록"
            : "참고: 없음";
        var priceText = previzPriceText(model, false);
        var label =
          (model.label || model.id) +
          (model.recommended ? " · 추천" : "") +
          " · " +
          priceText +
          " · " +
          refText;
        var option = el("option", null, label);
        option.value = model.id;
        // 프리비즈 미지원 모델은 선택 불가(회색 표시).
        if (model.previzCapable === false || model.available === false) {
          option.disabled = true;
          option.textContent =
            (model.label || model.id) +
            " · " +
            (model.previzCapable === false ? "프리비즈 미지원" : model.reason || "사용 불가");
        }
        if (model.id === inp.videoModel) option.selected = true;
        select.appendChild(option);
      });
    }
    // 고른 모델 정보 줄(단가·참고 방식).
    var modelInfo = el("p", "vnew-field-hint vnew-model-info", "");
    function updateModelInfo(modelId) {
      var chosen = null;
      for (var i = 0; i < models.length; i++) {
        if (models[i] && models[i].id === modelId) {
          chosen = models[i];
          break;
        }
      }
      if (!chosen) {
        modelInfo.textContent = "";
        return;
      }
      var refText =
        chosen.refMode === "elements"
          ? "참고 이미지: 인물 요소(elements)"
          : chosen.refMode === "imageList"
          ? "참고 이미지: 이미지 목록"
          : "참고 이미지: 없음";
      var priceText = previzPriceText(chosen, true);
      modelInfo.textContent = priceText + " · " + refText;
    }
    select.addEventListener("change", function () {
      updateModelInfo(select.value);
      putInput({ videoModel: select.value })
        .then(function () {
          renderPlan(); // 최대 길이·단가 안내가 바뀌니 다시 그린다
        })
        .catch(function (err) {
          showError("모델을 저장하지 못했습니다: " + errMessage(err));
        });
    });
    modelField.appendChild(select);
    modelField.appendChild(modelInfo);
    updateModelInfo(select.value);
    grid.appendChild(modelField);

    section.appendChild(grid);
    return section;
  }

  /* ① fal 카탈로그 열기 모달(설계자 2026-10-22). 카테고리 탭(영상→영상 기본·이미지→영상·텍스트→영상),
     검색, 결과 카드(썸네일·이름·설명·카테고리·단가), 더 보기(cursor). [활성화] → POST /fal/models
     → 목록 갱신·"활성화됨". 이미 등록된 건 [활성화됨]. 게이트(404/409)면 버튼 숨기고 안내 한 줄.
     기존 vpoc 카탈로그 API 를 그대로 쓴다(수정 금지). */
  var FAL_CAT_TABS = [
    ["video-to-video", "영상→영상"],
    ["image-to-video", "이미지→영상"],
    ["text-to-video", "텍스트→영상"]
  ];

  function openFalCatalog(showError) {
    var scrim = el("div", "vnew-modal-scrim");
    var modal = el("div", "vnew-modal vnew-fal-modal");
    var head = el("div", "vnew-modal-head");
    head.appendChild(el("h3", null, "fal 카탈로그"));
    var close = el("button", "icon-button", "×");
    close.type = "button";
    close.addEventListener("click", closeModal);
    head.appendChild(close);
    modal.appendChild(head);

    // 상태.
    var ctx = {
      category: "video-to-video",
      q: "",
      cursor: "",
      models: [],
      hasMore: false,
      registered: {} // endpointId -> true
    };

    // 탭.
    var tabs = el("div", "vnew-fal-tabs");
    var tabBtns = {};
    FAL_CAT_TABS.forEach(function (pair) {
      var btn = el("button", "vnew-fal-tab", pair[1]);
      btn.type = "button";
      btn.addEventListener("click", function () {
        if (ctx.category === pair[0]) return;
        ctx.category = pair[0];
        ctx.q = "";
        ctx.cursor = "";
        searchInput.value = "";
        setActiveTab();
        reload();
      });
      tabBtns[pair[0]] = btn;
      tabs.appendChild(btn);
    });
    modal.appendChild(tabs);
    function setActiveTab() {
      FAL_CAT_TABS.forEach(function (pair) {
        tabBtns[pair[0]].classList.toggle("is-active", pair[0] === ctx.category);
      });
    }
    setActiveTab();

    // 검색.
    var searchRow = el("div", "vnew-fal-search");
    var searchInput = el("input", "vnew-field-input");
    searchInput.type = "search";
    searchInput.placeholder = "모델 이름 검색";
    var searchBtn = el("button", "outline-button", "검색");
    searchBtn.type = "button";
    searchBtn.addEventListener("click", function () {
      ctx.q = searchInput.value.trim();
      ctx.cursor = "";
      reload();
    });
    searchInput.addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        event.preventDefault();
        searchBtn.click();
      }
    });
    searchRow.appendChild(searchInput);
    searchRow.appendChild(searchBtn);
    modal.appendChild(searchRow);

    // 안내 한 줄(게이트·오류).
    var notice = el("p", "vnew-fal-notice", "");
    notice.style.display = "none";
    modal.appendChild(notice);
    function showNotice(message) {
      notice.textContent = message;
      notice.style.display = "";
    }

    // 결과 영역.
    var bodyWrap = el("div", "vnew-modal-body vnew-fal-body");
    modal.appendChild(bodyWrap);

    var moreBtn = el("button", "outline-button vnew-fal-more", "더 보기");
    moreBtn.type = "button";
    moreBtn.style.display = "none";
    moreBtn.addEventListener("click", function () {
      loadPage(true);
    });
    modal.appendChild(moreBtn);

    scrim.appendChild(modal);
    scrim.addEventListener("click", function (event) {
      if (event.target === scrim) closeModal();
    });
    document.body.appendChild(scrim);

    function closeModal() {
      if (scrim.parentNode) scrim.parentNode.removeChild(scrim);
      // 닫을 때 ① 을 다시 그려 등록 모델이 목록에 반영되게 한다.
      renderPlan();
    }

    // 등록된 fal 모델 endpoint 집합(활성화됨 표시용).
    function loadRegistered() {
      return api("/api/vpoc/fal/models")
        .then(function (payload) {
          ctx.registered = {};
          (payload.models || []).forEach(function (m) {
            if (m && m.endpointId) ctx.registered[m.endpointId] = true;
          });
        })
        .catch(function () {
          // 게이트(404)·오류면 등록 목록을 비워 둔다(활성화 버튼은 catalog 결과로만 판단).
          ctx.registered = {};
        });
    }

    function reload() {
      ctx.cursor = "";
      ctx.models = [];
      clear(bodyWrap);
      bodyWrap.appendChild(el("p", "vnew-boot", "불러오는 중…"));
      loadRegistered().then(function () {
        loadPage(false);
      });
    }

    function loadPage(append) {
      var url =
        "/api/vpoc/fal/catalog?category=" +
        encodeURIComponent(ctx.category) +
        "&q=" +
        encodeURIComponent(ctx.q) +
        "&cursor=" +
        encodeURIComponent(ctx.cursor);
      moreBtn.disabled = true;
      api(url)
        .then(function (payload) {
          if (!append) {
            clear(bodyWrap);
            ctx.models = [];
          }
          notice.style.display = "none";
          var rows = payload.models || [];
          ctx.models = ctx.models.concat(rows);
          ctx.cursor = payload.nextCursor || "";
          ctx.hasMore = !!payload.hasMore;
          drawCards(rows, append);
          moreBtn.disabled = false;
          moreBtn.style.display = ctx.hasMore ? "" : "none";
          if (!ctx.models.length) {
            var empty = el("p", "vnew-hint", "결과가 없습니다.");
            bodyWrap.appendChild(empty);
          }
        })
        .catch(function (err) {
          moreBtn.disabled = false;
          moreBtn.style.display = "none";
          clear(bodyWrap);
          var status = err && err.status;
          if (status === 404 || status === 409) {
            // 게이트(모델 고르기 꺼짐 · fal 키 없음): 안내 한 줄만.
            showNotice(
              "fal 카탈로그를 쓸 수 없습니다: " + errMessage(err) + " (관리자 설정이 필요합니다.)"
            );
          } else {
            showNotice("불러오지 못했습니다: " + errMessage(err));
          }
        });
    }

    function drawCards(rows, append) {
      if (!append) clear(bodyWrap);
      var grid = bodyWrap.querySelector(".vnew-fal-grid");
      if (!grid) {
        grid = el("div", "vnew-fal-grid");
        bodyWrap.appendChild(grid);
      }
      rows.forEach(function (model) {
        grid.appendChild(catalogCard(model));
      });
    }

    function catalogCard(model) {
      var card = el("div", "vnew-fal-card");
      var thumb = el("div", "vnew-fal-thumb");
      if (model.thumbnailUrl) {
        var img = el("img");
        img.src = model.thumbnailUrl;
        img.alt = model.label || "";
        thumb.appendChild(img);
      } else {
        thumb.appendChild(el("span", "vnew-fal-thumb-empty", (model.label || "?").slice(0, 1)));
      }
      card.appendChild(thumb);

      var info = el("div", "vnew-fal-info");
      info.appendChild(el("strong", "vnew-fal-name", model.label || model.endpointId));
      info.appendChild(el("span", "vnew-fal-cat", model.category || ctx.category));
      if (model.description) {
        info.appendChild(el("p", "vnew-fal-desc", model.description));
      }
      // 금지 모델은 활성화 비활성(설계자 2026-10-22).
      var denied = isDeniedEndpoint(model.endpointId);
      var registered = !!ctx.registered[model.endpointId];
      var actionRow = el("div", "vnew-fal-actions");
      if (registered) {
        actionRow.appendChild(el("span", "vnew-fal-badge is-on", "활성화됨"));
      } else if (denied) {
        var deniedBtn = el("button", "outline-button", "사용 금지");
        deniedBtn.type = "button";
        deniedBtn.disabled = true;
        actionRow.appendChild(deniedBtn);
      } else {
        var actBtn = el("button", "primary-button vnew-fal-activate", "활성화");
        actBtn.type = "button";
        actBtn.addEventListener("click", function () {
          actBtn.disabled = true;
          actBtn.textContent = "활성화 중…";
          api("/api/vpoc/fal/models", {
            method: "POST",
            body: JSON.stringify({ endpointId: model.endpointId })
          })
            .then(function (payload) {
              ctx.registered[model.endpointId] = true;
              var caps = ((payload && payload.model) || {}).caps || {};
              clear(actionRow);
              actionRow.appendChild(el("span", "vnew-fal-badge is-on", "활성화됨"));
              actionRow.appendChild(
                el(
                  "span",
                  "vnew-fal-badge " + (caps.previzCapable ? "is-previz" : "is-noprev"),
                  caps.previzCapable ? "프리비즈 지원" : "프리비즈 미지원"
                )
              );
            })
            .catch(function (err) {
              actBtn.disabled = false;
              actBtn.textContent = "활성화";
              var status = err && err.status;
              if (status === 404 || status === 409) {
                showNotice("활성화할 수 없습니다: " + errMessage(err));
              } else {
                showError && showError("활성화하지 못했습니다: " + errMessage(err));
                actBtn.textContent = "활성화 실패";
              }
            });
        });
        actionRow.appendChild(actBtn);
      }
      info.appendChild(actionRow);
      card.appendChild(info);
      return card;
    }

    reload();
  }

  /* 금지 endpoint 패턴(설계자 2026-10-22, 서버 FAL_DENY_ENDPOINTS 와 같은 기본 규칙).
     서버가 최종 거절하지만, 화면도 미리 [활성화] 를 비활성해 혼란을 줄인다. */
  function isDeniedEndpoint(endpoint) {
    var eid = String(endpoint || "").toLowerCase();
    if (!eid) return false;
    // 기본 규칙: seedance 2.x fast 계열.
    return /seedance.*2.*fast/.test(eid);
  }

  /* 캐릭터 연결 영역. [캐릭터 정보 불러오기] → 고르기 창 → 역할명 입력. */
  function characterSection(showError) {
    var inp = (state.plan || {}).input || {};
    var chars = inp.characters || [];

    var section = el("section", "panel vnew-char-sec");
    var head = el("div", "vnew-sec-head");
    head.appendChild(el("h2", "vnew-sec-title", "등장인물 (선택)"));
    var loadBtn = el("button", "outline-button", "캐릭터 정보 불러오기");
    loadBtn.type = "button";
    loadBtn.addEventListener("click", function () {
      openCharacterPicker(showError);
    });
    head.appendChild(loadBtn);
    section.appendChild(head);
    section.appendChild(
      el("p", "vnew-hint", "캐릭터 만들기에서 만든 캐릭터를 골라 웹툰 속 이름을 적어 주세요.")
    );

    if (!chars.length) {
      section.appendChild(el("p", "vnew-hint vnew-char-empty", "아직 연결한 캐릭터가 없습니다."));
    } else {
      var list = el("div", "vnew-char-chosen");
      chars.forEach(function (row, index) {
        list.appendChild(chosenCharCard(row, index, showError));
      });
      section.appendChild(list);
    }
    return section;
  }

  function chosenCharCard(row, index, showError) {
    var card = el("div", "vnew-char-chosen-item");
    var thumb = el("div", "vnew-char-chosen-thumb");
    if (row.thumbUrl) {
      var img = el("img");
      img.src = row.thumbUrl;
      img.alt = row.name || "";
      thumb.appendChild(img);
    } else {
      thumb.appendChild(el("span", "vnew-char-initial", (row.name || "?").slice(0, 1)));
    }
    card.appendChild(thumb);

    var body = el("div", "vnew-char-chosen-body");
    body.appendChild(el("strong", "vnew-char-chosen-name", row.name || row.charId));
    if (row.status === "missing") {
      body.appendChild(el("span", "vnew-hint", "찾을 수 없는 캐릭터입니다."));
    }
    var roleWrap = el("label", "vnew-role-field");
    roleWrap.appendChild(el("span", "vnew-field-label", "웹툰 속 이름"));
    var roleInput = el("input", "vnew-field-input");
    roleInput.type = "text";
    roleInput.value = row.roleName || "";
    roleInput.placeholder = "예: 김부장";
    var roleTimer = 0;
    roleInput.addEventListener("input", function () {
      if (roleTimer) window.clearTimeout(roleTimer);
      roleTimer = window.setTimeout(function () {
        saveCharacters(showError);
      }, ORDER_SAVE_MS);
      // 로컬 모델에도 즉시 반영(저장 payload 가 최신이 되도록)
      var chars = ((state.plan || {}).input || {}).characters || [];
      if (chars[index]) chars[index].roleName = roleInput.value;
    });
    roleWrap.appendChild(roleInput);
    body.appendChild(roleWrap);
    card.appendChild(body);

    var del = el("button", "icon-button danger", "×");
    del.type = "button";
    del.title = "연결 해제";
    del.addEventListener("click", function () {
      var chars = ((state.plan || {}).input || {}).characters || [];
      chars.splice(index, 1);
      saveCharacters(showError, true);
    });
    card.appendChild(del);
    return card;
  }

  /* 현재 input.characters 를 서버에 저장. redraw 면 저장 후 다시 그린다. */
  function saveCharacters(showError, redraw) {
    var chars = ((state.plan || {}).input || {}).characters || [];
    var payload = chars.map(function (row) {
      return { charId: row.charId, roleName: row.roleName || "" };
    });
    putInput({ characters: payload })
      .then(function () {
        if (redraw) renderPlan();
      })
      .catch(function (err) {
        if (showError) showError("캐릭터를 저장하지 못했습니다: " + errMessage(err));
      });
  }

  /* 캐릭터 고르기 오버레이. listCharacters() 로 ready 캐릭터를 받아 카드로 보인다. */
  function openCharacterPicker(showError) {
    var scrim = el("div", "vnew-modal-scrim");
    var modal = el("div", "vnew-modal");
    var head = el("div", "vnew-modal-head");
    head.appendChild(el("h3", null, "캐릭터 고르기"));
    var close = el("button", "icon-button", "×");
    close.type = "button";
    close.addEventListener("click", closeModal);
    head.appendChild(close);
    modal.appendChild(head);

    var bodyWrap = el("div", "vnew-modal-body");
    bodyWrap.appendChild(el("p", "vnew-boot", "불러오는 중…"));
    modal.appendChild(bodyWrap);
    scrim.appendChild(modal);
    scrim.addEventListener("click", function (event) {
      if (event.target === scrim) closeModal();
    });
    document.body.appendChild(scrim);

    function closeModal() {
      if (scrim.parentNode) scrim.parentNode.removeChild(scrim);
    }

    window.vnew.listCharacters().then(function (rows) {
      clear(bodyWrap);
      if (!rows.length) {
        var empty = el("div", "empty-block");
        empty.appendChild(el("strong", null, "완성된 캐릭터가 없습니다"));
        empty.appendChild(
          el("p", null, "캐릭터 만들기에서 먼저 만들어 주세요. 완성(ready)된 캐릭터만 고를 수 있습니다.")
        );
        var goLink = el("button", "text-action", "캐릭터 만들기로 가기");
        goLink.type = "button";
        goLink.addEventListener("click", function () {
          closeModal();
          goToCharacters();
        });
        empty.appendChild(goLink);
        bodyWrap.appendChild(empty);
        return;
      }
      var chosen = ((state.plan || {}).input || {}).characters || [];
      var chosenIds = {};
      chosen.forEach(function (c) {
        chosenIds[c.charId] = true;
      });
      var grid = el("div", "vnew-picker-grid");
      rows.forEach(function (row) {
        var card = el("button", "vnew-picker-card");
        card.type = "button";
        if (chosenIds[row.id]) {
          card.classList.add("is-chosen");
          card.disabled = true;
        }
        var thumb = el("div", "vnew-char-chosen-thumb");
        if (row.thumbUrl) {
          var img = el("img");
          img.src = row.thumbUrl;
          img.alt = row.name || "";
          thumb.appendChild(img);
        } else {
          thumb.appendChild(el("span", "vnew-char-initial", (row.name || "?").slice(0, 1)));
        }
        card.appendChild(thumb);
        card.appendChild(el("span", "vnew-picker-name", row.name || "이름 없는 캐릭터"));
        if (chosenIds[row.id]) card.appendChild(el("span", "vnew-hint", "이미 추가됨"));
        card.addEventListener("click", function () {
          var chars = ((state.plan || {}).input || {}).characters || [];
          chars.push({ charId: row.id, roleName: "", name: row.name || "", status: row.status || "ready", thumbUrl: row.thumbUrl || "" });
          saveCharacters(showError, true);
          closeModal();
        });
        grid.appendChild(card);
      });
      bodyWrap.appendChild(grid);
    });
  }

  /* 캐릭터 만들기 메뉴로 이동(app.js 가 해시 라우팅을 쓰면 그 해시로, 아니면 안내만). */
  function goToCharacters() {
    try {
      window.location.hash = "#characters";
    } catch (error) {
      /* 무시 */
    }
  }

  /* ================================================================ ② 대본·상황 */

  function renderScriptStep(host) {
    var plan = state.plan || {};
    var task = plan.task || {};
    var script = plan.script || {};
    var error = planError("");
    host.appendChild(error);
    function showError(message) {
      error.style.display = "";
      error.textContent = message;
    }

    // 진행 중 띠 + 취소
    if (task.status === "running" && task.name === "script") {
      host.appendChild(progressBar("대본·상황을 읽고 있습니다…", showError));
      return;
    }
    if (script.status === "running") {
      host.appendChild(progressBar("대본·상황을 읽고 있습니다…", showError));
      return;
    }
    if (script.status === "failed") {
      host.appendChild(planError(script.error || "대본 정리에 실패했습니다."));
    }
    if (script.status === "none" || !(script.entries || []).length) {
      if (script.status !== "failed") {
        host.appendChild(el("p", "vnew-hint", "아직 대본이 없습니다. ① 입력에서 [대본·상황 정리하기] 를 눌러 주세요."));
      }
      host.appendChild(scriptActions(host, showError, false));
      return;
    }

    // setting 카드
    host.appendChild(settingCard(script.setting || {}));

    // entries 표
    host.appendChild(entriesTable(script.entries || [], showError));

    host.appendChild(scriptActions(host, showError, true));
  }

  /* 진행 띠 + [취소]. */
  function progressBar(label, showError) {
    var wrap = el("div", "vnew-progress-wrap");
    wrap.appendChild(el("p", "vnew-progress-label", label));
    var track = el("div", "vnew-progress-track");
    track.appendChild(el("div", "vnew-progress-fill"));
    wrap.appendChild(track);
    var cancel = el("button", "outline-button danger", "취소");
    cancel.type = "button";
    cancel.addEventListener("click", function () {
      cancel.disabled = true;
      api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/cancel", { method: "POST" })
        .then(function () {
          loadPlan(false);
        })
        .catch(function (err) {
          cancel.disabled = false;
          if (showError) showError("취소하지 못했습니다: " + errMessage(err));
        });
    });
    wrap.appendChild(cancel);
    return wrap;
  }

  /* setting 편집 카드. 저장은 entries 와 함께 [저장] 버튼으로. */
  function settingCard(setting) {
    var card = el("section", "panel vnew-setting-card");
    card.appendChild(el("h2", "vnew-sec-title", "상황 설정"));
    var grid = el("div", "vnew-setting-grid");

    function field(labelText, key, placeholder) {
      var wrap = el("label", "vnew-field");
      wrap.appendChild(el("span", "vnew-field-label", labelText));
      var input = el("input", "vnew-field-input");
      input.type = "text";
      input.value = setting[key] || "";
      if (placeholder) input.placeholder = placeholder;
      input.addEventListener("input", function () {
        setting[key] = input.value;
      });
      wrap.appendChild(input);
      return wrap;
    }

    grid.appendChild(field("장소 (한국어)", "locationKo", "예: 교장실"));
    grid.appendChild(field("장소 (영어)", "locationEn", "예: A principal's office"));
    grid.appendChild(field("시간", "timeKo", "예: 낮"));
    grid.appendChild(field("분위기", "moodKo", "예: 긴장"));

    // 닫힌 공간 여부
    var enclosedWrap = el("label", "vnew-field vnew-check-field");
    var checkbox = el("input");
    checkbox.type = "checkbox";
    checkbox.checked = !!setting.enclosed;
    checkbox.addEventListener("change", function () {
      setting.enclosed = checkbox.checked;
    });
    enclosedWrap.appendChild(checkbox);
    enclosedWrap.appendChild(el("span", "vnew-field-label", "닫힌 공간"));
    grid.appendChild(enclosedWrap);

    card.appendChild(grid);

    // 공간 배치(한국어·영어) — 넓게
    var layoutKo = el("label", "vnew-field");
    layoutKo.appendChild(el("span", "vnew-field-label", "공간 배치 (한국어)"));
    var layoutKoInput = el("textarea", "vnew-field-input");
    layoutKoInput.rows = 2;
    layoutKoInput.value = setting.layoutKo || "";
    layoutKoInput.addEventListener("input", function () {
      setting.layoutKo = layoutKoInput.value;
    });
    layoutKo.appendChild(layoutKoInput);
    card.appendChild(layoutKo);

    var layoutEn = el("label", "vnew-field");
    layoutEn.appendChild(el("span", "vnew-field-label", "공간 배치 (영어)"));
    var layoutEnInput = el("textarea", "vnew-field-input");
    layoutEnInput.rows = 2;
    layoutEnInput.value = setting.layoutEn || "";
    layoutEnInput.addEventListener("input", function () {
      setting.layoutEn = layoutEnInput.value;
    });
    layoutEn.appendChild(layoutEnInput);
    card.appendChild(layoutEn);

    return card;
  }

  /* entries 표(행 편집·추가·삭제·이동). 변경은 로컬 배열에 반영하고 [저장]으로 PUT. */
  function entriesTable(entries, showError) {
    var section = el("section", "panel vnew-entries-sec");
    section.appendChild(el("h2", "vnew-sec-title", "대본·상황 (" + entries.length + "줄)"));
    section.appendChild(el("p", "vnew-hint", "순서대로 대사·상황을 고칠 수 있습니다. 배경·장소는 웹툰 그대로 두세요."));

    var list = el("div", "vnew-entry-list");
    entries.forEach(function (entry, index) {
      list.appendChild(entryRow(entry, index, entries, showError));
    });
    section.appendChild(list);

    var addBtn = el("button", "outline-button vnew-add-row", "+ 줄 추가");
    addBtn.type = "button";
    addBtn.addEventListener("click", function () {
      entries.push({
        id: "e" + (entries.length + 1) + "_" + Date.now(),
        panels: [],
        speaker: "",
        lineKo: "",
        voice: "spoken",
        situationKo: "",
        emotionKo: "",
        locationKo: "",
        locationEn: "",
        characters: []
      });
      renderPlan();
    });
    section.appendChild(addBtn);
    return section;
  }

  /* ---- 속마음 대사(지시서 117) ----
     꼬리 없는 둥근 말풍선 = voice "thought"(입 다문 채 목소리만). 값이 없으면 spoken 으로 본다(서버 prompts.is_thought 와 같다). */
  function entryVoice(checked) {
    return checked ? "thought" : "spoken";
  }

  function isThoughtEntry(entry) {
    return !!entry && (String(entry.voice || "").trim().toLowerCase() === "thought" || entry.thought === true);
  }

  /* 콘티 컷 panels 와 겹치는 대본 줄 중 속마음 대사의 화자 이름(겹침 규칙은 서버 generate._cut_lines 와 같다). */
  function contiThoughtSpeakers(cut, entries) {
    var mine = {};
    ((cut && cut.panels) || []).forEach(function (p) { mine[String(p)] = true; });
    var out = [];
    (entries || []).forEach(function (entry) {
      if (!isThoughtEntry(entry) || !String(entry.lineKo || "").trim()) return;
      var hit = (entry.panels || []).some(function (p) { return mine[String(p)]; });
      var name = String(entry.speaker || "").trim() || "화자";
      if (hit && out.indexOf(name) < 0) out.push(name);
    });
    return out;
  }

  function thoughtToggle(entry) {
    var wrap = el("label", "vnew-field vnew-thought-toggle");
    var box = el("input", "");
    box.type = "checkbox";
    box.checked = isThoughtEntry(entry);
    box.addEventListener("change", function () {
      entry.voice = entryVoice(box.checked);
    });
    wrap.appendChild(box);
    wrap.appendChild(el("span", "vnew-field-label", "속마음 (입 다문 채 목소리만)"));
    return wrap;
  }
  /* ---- 속마음 대사 끝 ---- */

  function entryRow(entry, index, entries, showError) {
    var card = el("div", "vnew-entry-card");

    var head = el("div", "vnew-entry-head");
    head.appendChild(el("span", "vnew-entry-no", String(index + 1)));
    // 참고 웹툰 컷 썸네일(panels 첫 값)
    var thumb = panelThumb((entry.panels || [])[0]);
    if (thumb && thumb.url) {
      var img = el("img", "vnew-entry-thumb");
      img.src = thumb.url;
      img.alt = "참고 컷";
      img.loading = "lazy";
      img.addEventListener("click", function () {
        openLightbox(thumb.url, "참고 컷");
      });
      head.appendChild(img);
    }
    head.appendChild(el("span", "vnew-entry-panels", "컷 " + ((entry.panels || []).join(", ") || "-")));

    var moveUp = el("button", "icon-button vnew-entry-move", "▲");
    moveUp.type = "button";
    moveUp.title = "위로";
    moveUp.disabled = index === 0;
    moveUp.addEventListener("click", function () {
      swapEntries(entries, index, index - 1);
    });
    var moveDown = el("button", "icon-button vnew-entry-move", "▼");
    moveDown.type = "button";
    moveDown.title = "아래로";
    moveDown.disabled = index === entries.length - 1;
    moveDown.addEventListener("click", function () {
      swapEntries(entries, index, index + 1);
    });
    var del = el("button", "icon-button danger", "×");
    del.type = "button";
    del.title = "삭제";
    del.addEventListener("click", function () {
      entries.splice(index, 1);
      renderPlan();
    });
    var controls = el("div", "vnew-entry-controls");
    controls.appendChild(moveUp);
    controls.appendChild(moveDown);
    controls.appendChild(del);
    head.appendChild(controls);
    card.appendChild(head);

    var grid = el("div", "vnew-entry-grid");
    grid.appendChild(textField("화자", entry, "speaker", false));
    grid.appendChild(textField("장소", entry, "locationKo", false));
    grid.appendChild(textField("대사", entry, "lineKo", true));
    grid.appendChild(thoughtToggle(entry));
    grid.appendChild(textField("상황", entry, "situationKo", true));
    grid.appendChild(textField("감정", entry, "emotionKo", false));
    card.appendChild(grid);

    // 인물 위치·동작(간단 요약 편집 — characters[0] 기준, 없으면 만들지 않음)
    var who = (entry.characters || []);
    if (who.length) {
      var whoWrap = el("div", "vnew-entry-who");
      who.forEach(function (person, pIndex) {
        var row = el("div", "vnew-entry-person");
        row.appendChild(el("span", "vnew-entry-person-name", person.name || "인물 " + (pIndex + 1)));
        row.appendChild(textField("위치", person, "positionKo", false));
        row.appendChild(textField("동작", person, "actionKo", false));
        whoWrap.appendChild(row);
      });
      card.appendChild(whoWrap);
    }
    return card;
  }

  /* 라벨 + input/textarea 필드. 입력은 object[key] 에 바로 반영(저장은 [저장] 버튼). */
  function textField(labelText, obj, key, multiline) {
    var wrap = el("label", "vnew-field");
    wrap.appendChild(el("span", "vnew-field-label", labelText));
    var input = el(multiline ? "textarea" : "input", "vnew-field-input");
    if (multiline) {
      input.rows = 2;
    } else {
      input.type = "text";
    }
    input.value = obj[key] || "";
    input.addEventListener("input", function () {
      obj[key] = input.value;
    });
    wrap.appendChild(input);
    return wrap;
  }

  function swapEntries(entries, a, b) {
    if (b < 0 || b >= entries.length) return;
    var tmp = entries[a];
    entries[a] = entries[b];
    entries[b] = tmp;
    renderPlan();
  }

  /* ② 저장 · 다시 정리 · 콘티 만들기 버튼 묶음. */
  /* 대본 setting·entries 서명(지시서 118b). 화면에서 고친 뒤 저장하지 않았는지 비교한다. */
  function scriptSigOf(script) {
    var s = script || {};
    return JSON.stringify({ setting: s.setting || {}, entries: s.entries || [] });
  }

  function scriptActions(host, showError, hasScript) {
    var footer = el("div", "vnew-plan-footer");

    if (hasScript) {
      var saveBtn = el("button", "outline-button", "저장");
      saveBtn.type = "button";
      saveBtn.addEventListener("click", function () {
        var script = (state.plan || {}).script || {};
        saveBtn.disabled = true;
        api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/script", {
          method: "PUT",
          body: JSON.stringify({ setting: script.setting || {}, entries: script.entries || [] })
        })
          .then(function (plan) {
            state.plan = plan;
            state.scriptSig = scriptSigOf((plan || {}).script);
            renderPlan();
          })
          .catch(function (err) {
            saveBtn.disabled = false;
            showError("저장하지 못했습니다: " + errMessage(err));
          });
      });
      footer.appendChild(saveBtn);
    }

    var redoBtn = el("button", "outline-button", "다시 정리하기");
    redoBtn.type = "button";
    redoBtn.addEventListener("click", function () {
      var okConfirm = false;
      try {
        okConfirm = window.confirm("다시 정리하면 지금 고친 내용이 덮어써집니다. 계속할까요?");
      } catch (e) {
        okConfirm = true;
      }
      if (!okConfirm) return;
      redoBtn.disabled = true;
      api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/script", { method: "POST" })
        .then(function () {
          loadPlan(false);
        })
        .catch(function (err) {
          redoBtn.disabled = false;
          showError("다시 정리를 시작하지 못했습니다: " + errMessage(err));
        });
    });
    footer.appendChild(redoBtn);

    // 지시서 118b: [속마음 찾기] — 말풍선 모양만 다시 보고 줄마다 voice 만 고친다(서버 POST /script/voices, 백그라운드).
    if (hasScript) {
      var vplan = state.plan || {};
      var vtask = vplan.task || {};
      var vscan = (vplan.script || {}).voiceScan || {};
      var vrunning = vtask.status === "running" && vtask.name === "voiceScan";
      var voiceBtn = el("button", "outline-button", vrunning ? "속마음 찾는 중…" : "속마음 찾기");
      voiceBtn.type = "button";
      voiceBtn.disabled = vrunning || vtask.status === "running";
      if (vrunning) redoBtn.disabled = true;
      voiceBtn.addEventListener("click", function () {
        var okConfirm = false;
        try {
          okConfirm = window.confirm("말풍선 모양을 다시 보고 속마음 표시만 고칩니다. 다른 내용은 그대로입니다.");
        } catch (e) {
          okConfirm = true;
        }
        if (!okConfirm) return;
        voiceBtn.disabled = true;
        redoBtn.disabled = true;
        voiceBtn.textContent = "속마음 찾는 중…";
        var base = "/api/vnew/p/" + encodeURIComponent(state.projectId) + "/script";
        var script = (state.plan || {}).script || {};
        // 저장하지 않은 화면 수정이 있으면 먼저 저장한다(다시 불러오면 사라지므로). 없으면 저장하지 않아 revision 이 그대로다.
        var save = scriptSigOf(script) !== state.scriptSig
          ? api(base, { method: "PUT", body: JSON.stringify({ setting: script.setting || {}, entries: script.entries || [] }) })
          : Promise.resolve(null);
        save
          .then(function () {
            return api(base + "/voices", { method: "POST" });
          })
          .then(function () {
            loadPlan(false);
          })
          .catch(function (err) {
            voiceBtn.disabled = false;
            redoBtn.disabled = false;
            voiceBtn.textContent = "속마음 찾기";
            showError("속마음 찾기를 시작하지 못했습니다: " + errMessage(err));
          });
      });
      footer.appendChild(voiceBtn);
      if (!vrunning && vscan.status === "done") {
        var thoughtN = ((vplan.script || {}).entries || []).filter(function (entry) {
          return isThoughtEntry(entry) && String(entry.lineKo || "").trim();
        }).length;
        footer.appendChild(el("span", "vnew-hint", "속마음 " + thoughtN + "줄 표시" +
          (Number(vscan.changed) > 0 ? " (바뀐 줄 " + Number(vscan.changed) + ")" : " (바뀐 줄 없음)")));
      } else if (!vrunning && vscan.status === "failed") {
        footer.appendChild(planError("속마음 찾기에 실패했습니다: " + (vscan.error || "알 수 없는 오류")));
      }
    }

    if (hasScript) {
      var contiBtn = el("button", "primary-button", "콘티 만들기");
      contiBtn.type = "button";
      contiBtn.addEventListener("click", function () {
        contiBtn.disabled = true;
        // 지시서 129: 콘티가 이미 있으면(콘티 다시) ② 에 머문다 — ③ 칩에 "진행 중"이 보인다. 처음 만들 때만 ③ 으로 간다.
        var conti0 = (state.plan || {}).conti || {};
        var contiAgain = conti0.status === "ready" && (conti0.cuts || []).length > 0;
        // 먼저 현재 수정본을 저장한 뒤 콘티 시작(덮어쓰기 방지).
        var script = (state.plan || {}).script || {};
        api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/script", {
          method: "PUT",
          body: JSON.stringify({ setting: script.setting || {}, entries: script.entries || [] })
        })
          .then(function () {
            return api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/conti", {
              method: "POST"
            });
          })
          .then(function () {
            if (!contiAgain) state.planStep = 3;
            loadPlan(false);
          })
          .catch(function (err) {
            contiBtn.disabled = false;
            showError("콘티 만들기를 시작하지 못했습니다: " + errMessage(err));
          });
      });
      footer.appendChild(contiBtn);
    }
    return footer;
  }

  /* ================================================================ ③ 콘티 */

  function renderContiStep(host) {
    var plan = state.plan || {};
    var task = plan.task || {};
    var conti = plan.conti || {};
    var error = planError("");
    host.appendChild(error);
    function showError(message) {
      error.style.display = "";
      error.textContent = message;
    }

    if ((task.status === "running" && task.name === "conti") || conti.status === "running") {
      host.appendChild(progressBar("콘티를 쓰고 있습니다…", showError));
      return;
    }
    // vnew-live-fx RA7.2: 채우기 진행 중이면 진행 막대만(설정을 꺼도 이미 시작한 작업은 끝까지 보인다).
    if (task.status === "running" && task.name === "fxFill") {
      host.appendChild(progressBar("기호·분위기를 채우고 있습니다…", showError));
      return;
    }
    // 작업 162: 콘티 연기 다시 읽기(손 주인) 진행 중.
    if (task.status === "running" && task.name === "rereadActions") {
      host.appendChild(progressBar("컷마다 패널을 다시 보고 손 주인·연기를 읽고 있습니다…", showError));
      return;
    }
    if (conti.status === "failed") {
      host.appendChild(planError(conti.error || "콘티 만들기에 실패했습니다."));
    }
    if (conti.status === "none" || !(conti.cuts || []).length) {
      if (conti.status !== "failed") {
        host.appendChild(el("p", "vnew-hint", "아직 콘티가 없습니다. ② 대본·상황에서 [콘티 만들기] 를 눌러 주세요."));
      }
      var backBtn = el("button", "outline-button", "← 대본·상황으로");
      backBtn.type = "button";
      backBtn.addEventListener("click", function () {
        state.planStep = 2;
        renderPlan();
      });
      host.appendChild(backBtn);
      return;
    }

    var cuts = conti.cuts || [];
    var segments = conti.segments || [];
    var totalSeconds = ((plan.input || {}).totalSeconds) || 0;
    var sumSeconds = cuts.reduce(function (acc, cut) {
      return acc + (Number(cut.seconds) || 0);
    }, 0);

    // 합계 · 자동 맞춤
    host.appendChild(contiSummary(totalSeconds, sumSeconds, showError));

    // 공간 디테일 시트(③ 콘티 위) — 콘티의 장소마다 4면 입면도 시트.
    host.appendChild(locationsCard(showError));

    // segments 안내
    if (segments.length) {
      host.appendChild(segmentsPanel(segments, cuts));
    }

    // vnew-live-fx RA7.2: 채우기 실패 문구 + [기호·분위기 채우기](컷 목록 위).
    if (fxEnabled()) {
      var fxError = fxFillErrorText(plan.fxFill);
      if (fxError) host.appendChild(planError(fxError));
      host.appendChild(fxToolbar(cuts, showError));
    }
    // 작업 162: [손 주인 다시 읽기] — 콘티 인물 연기·프리비즈 손만 다시 읽는다(컷·초·대사·기호·카메라·승인 그대로).
    host.appendChild(rereadActionsToolbar(showError));

    // 컷 카드
    var list = el("div", "vnew-cut-list");
    cuts.forEach(function (cut, index) {
      list.appendChild(cutCard(cut, index, cuts, segments));
    });
    host.appendChild(list);

    var addBtn = el("button", "outline-button vnew-add-row", "+ 컷 추가");
    addBtn.type = "button";
    addBtn.addEventListener("click", function () {
      cuts.push({
        id: "c" + (cuts.length + 1) + "_" + Date.now(),
        no: cuts.length + 1,
        seconds: 3,
        panels: [],
        shotKo: "",
        cameraEn: "",
        locationEn: "",
        characters: [],
        dialogueKo: "",
        situationKo: "",
        promptEn: ""
      });
      renderPlan();
    });
    host.appendChild(addBtn);

    // 저장 · 영상 만들기
    var footer = el("div", "vnew-plan-footer");
    var saveBtn = el("button", "outline-button", "저장");
    saveBtn.type = "button";
    saveBtn.addEventListener("click", function () {
      saveBtn.disabled = true;
      api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/conti", {
        method: "PUT",
        body: JSON.stringify({ cuts: cuts })
      })
        .then(function (plan) {
          state.plan = plan;
          renderPlan();
        })
        .catch(function (err) {
          saveBtn.disabled = false;
          showError("저장하지 못했습니다: " + errMessage(err));
        });
    });
    footer.appendChild(saveBtn);

    // 프리비즈(3D)로: 합계가 목표와 맞아야(콘티가 서버 초 맞춤을 거친 상태) 다음으로 갈 수 있다.
    var makeBtn = el("button", "primary-button", "프리비즈(3D) 만들기");
    makeBtn.type = "button";
    var lengthMismatch = totalSeconds !== sumSeconds;
    makeBtn.disabled = lengthMismatch;
    if (lengthMismatch) {
      makeBtn.title = "먼저 길이를 맞춰 주세요";
    }
    makeBtn.addEventListener("click", function () {
      state.planStep = 4;
      renderPlan();
    });
    footer.appendChild(makeBtn);
    if (lengthMismatch) {
      footer.appendChild(el("span", "vnew-hint", "합계를 목표에 맞춘 뒤 프리비즈로 넘어갈 수 있습니다."));
    }
    host.appendChild(footer);
  }

  /* ================================================================ ③ 공간 디테일 시트 */

  /* 공간 카드: 콘티의 장소마다 4면(N/E/S/W) 입면도 시트(실사·웹툰 탭), 벽 설명·빛 편집,
     [공간 시트 만들기]/[다시 만들기]. 콘티를 만들면 목록만 생기고 자동으로 그리지 않는다. */
  function locationsCard(showError) {
    var plan = state.plan || {};
    var locations = plan.locations || [];
    var card = el("section", "panel vnew-loc-card");
    var head = el("div", "vnew-loc-head");
    head.appendChild(el("h2", "vnew-sec-title", "공간"));
    head.appendChild(
      el("p", "vnew-hint", "콘티에 나오는 장소마다 4면이 보이는 고정 공간 이미지를 만듭니다. 프리비즈의 벽·바닥 텍스처로 씁니다.")
    );
    card.appendChild(head);

    if (!locations.length) {
      card.appendChild(el("p", "vnew-hint", "아직 장소가 없습니다. 콘티를 만들면 장소가 정리됩니다."));
      return card;
    }

    // [공간 시트 모두 만들기] — 아직 안 만든 장소를 한꺼번에.
    var allBtn = el("button", "outline-button", "공간 시트 모두 만들기");
    allBtn.type = "button";
    allBtn.addEventListener("click", function () {
      allBtn.disabled = true;
      api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/locations/build-all", {
        method: "POST"
      })
        .then(function () {
          loadPlan(false);
        })
        .catch(function (err) {
          allBtn.disabled = false;
          if (showError) showError("만들지 못했습니다: " + errMessage(err));
        });
    });
    card.appendChild(allBtn);

    var listWrap = el("div", "vnew-loc-list");
    locations.forEach(function (loc) {
      listWrap.appendChild(locationItem(loc, showError));
    });
    card.appendChild(listWrap);
    return card;
  }

  /* 한 장소 항목. 상태·4면 시트(탭)·벽 설명·빛 편집·만들기 버튼. */
  function locationItem(loc, showError) {
    var item = el("div", "vnew-loc-item");
    var header = el("div", "vnew-loc-item-head");
    var title = el("span", "vnew-loc-name", loc.nameKo || loc.nameEn || "(이름 없음)");
    header.appendChild(title);
    header.appendChild(el("span", "vnew-loc-status vnew-loc-status-" + (loc.status || "none"), locStatusLabel(loc.status)));
    item.appendChild(header);

    if (loc.status === "building" || (loc.task || {}).status === "running") {
      var prog = progressIndicator(loc.progress);
      if (prog) {
        item.appendChild(prog);
      } else {
        item.appendChild(el("p", "vnew-loc-building", "공간 시트를 만들고 있습니다…"));
      }
    }
    if (loc.status === "failed" && loc.error) {
      item.appendChild(el("p", "vnew-plan-error", loc.error));
    }

    // 4면 시트(실사·웹툰 탭). ready 인 그림체만 이미지를 보인다.
    item.appendChild(locationSheet(loc));

    // 참고 사진(사용자 실제 사진이 배경 생성을 이끈다, 설계자 2026-10-22).
    item.appendChild(locationPhotos(loc, showError));

    // 벽별 "웹툰과 일치/보정함" 배지(비교·보정 결과).
    var badges = locationCheckBadges(loc);
    if (badges) item.appendChild(badges);

    // 추출된 사실(벽별 물건) 접어서 보기·수정. 수정하면 그 사실로 프롬프트를 다시 쓴다.
    item.appendChild(locationFactsEditor(loc, showError));

    // 벽 설명·빛 편집.
    item.appendChild(locationDesignEditor(loc, showError));

    // 만들기 / 다시 만들기(추가 요청).
    item.appendChild(locationActions(loc, showError));
    return item;
  }

  function locStatusLabel(status) {
    if (status === "ready") return "완료";
    if (status === "building") return "만드는 중";
    if (status === "failed") return "실패";
    return "아직 안 만듦";
  }

  /* 참고 사진 미리보기를 Authorization 헤더로 받아 blob → objectURL 로 img.src 에 꽂는다.
     버킷에 CORS 가 없어 presigned 직통 대신 같은 출처 API(GET .../photos/<photoId>)를 쓴다.
     objectURL 은 이미지가 교체·제거될 때 브라우저가 회수한다(짧게 살다 간다). 실패하면 조용히 둔다. */
  function photoThumbFetch(img, apiUrl) {
    if (!img || !apiUrl) return;
    fetch(apiUrl, { headers: { Authorization: "Bearer " + token() } })
      .then(function (response) {
        if (!response.ok) throw new Error("photo " + response.status);
        return response.blob();
      })
      .then(function (blob) {
        img.src = URL.createObjectURL(blob);
      })
      .catch(function () {
        /* 썸네일만 비운다(목록·면 선택은 그대로 동작). */
      });
  }

  /* 참고 사진 영역(설계자 2026-10-22): 사용자 실제 사진을 붙여 배경 생성을 '사진대로' 끌고 간다.
     [사진 첨부](여러 장·드래그&드롭), 썸네일 목록(같은 출처 API·blob URL), 각 사진에 면 선택·메모·삭제.
     6장 꽉 차면 첨부 비활성. 빌드 중에는 첨부·삭제·변경 비활성. */
  function locationPhotos(loc, showError) {
    var wrap = el("section", "vnew-loc-photos");
    wrap.appendChild(el("h3", "vnew-loc-photos-title", "참고 사진"));
    wrap.appendChild(
      el(
        "p",
        "vnew-hint vnew-loc-photos-hint",
        "실제 장소 사진을 넣으면 배경을 사진대로 만듭니다. 사진마다 어느 면인지 고르면 더 정확합니다. 사진을 바꾼 뒤 [다시 만들기]를 눌러 주세요."
      )
    );

    var photos = loc.photos || [];
    var maxPhotos = loc.maxPhotos || 6;
    var sideLabels = loc.photoSideLabels || {
      any: "전체 분위기",
      back: "메인배경(뒤)",
      left: "왼쪽",
      right: "오른쪽",
      front: "앞(맞은편)",
      floor: "바닥"
    };
    // 화면 select 순서(전체 분위기/메인배경(뒤)/왼쪽/오른쪽/앞(맞은편)/바닥).
    var sideOrder = ["any", "back", "left", "right", "front", "floor"];
    var busy = loc.status === "building" || (loc.task || {}).status === "running";
    var full = photos.length >= maxPhotos;
    var base = "/api/vnew/p/" + encodeURIComponent(state.projectId) + "/locations/" + encodeURIComponent(loc.id);

    function reloadWith(plan) {
      if (plan) {
        state.plan = plan;
        renderPlan();
      } else {
        loadPlan(false);
      }
    }

    function uploadFiles(fileList, side) {
      var files = [];
      var i;
      for (i = 0; i < (fileList ? fileList.length : 0); i += 1) files.push(fileList[i]);
      if (!files.length) return;
      var form = new FormData();
      files.forEach(function (f) {
        form.append("files", f);
      });
      if (side) form.append("side", side);
      api(base + "/photos", { method: "POST", body: form })
        .then(function (res) {
          reloadWith(res && res.location ? patchedPlanWithLoc(res.location) : null);
        })
        .catch(function (err) {
          if (showError) showError("사진을 올리지 못했습니다: " + errMessage(err));
          loadPlan(false);
        });
    }

    // 한 장소만 바뀐 location_view 를 현재 plan 에 끼워 넣는다(없으면 null → 전체 새로고침).
    function patchedPlanWithLoc(locView) {
      var plan = state.plan;
      if (!plan || !plan.locations) return null;
      var next = [];
      plan.locations.forEach(function (l) {
        next.push(l.id === locView.id ? locView : l);
      });
      var copy = {};
      Object.keys(plan).forEach(function (k) {
        copy[k] = plan[k];
      });
      copy.locations = next;
      return copy;
    }

    // 첨부 버튼 + 드래그&드롭 영역.
    var drop = el("div", "vnew-loc-photos-drop" + (full ? " is-full" : "") + (busy ? " is-busy" : ""));
    var addBtn = el("button", "outline-button vnew-loc-photos-add", full ? "사진 가득 참 (" + maxPhotos + "장)" : "사진 첨부");
    addBtn.type = "button";
    addBtn.disabled = busy || full;
    var fileInput = el("input", "vnew-loc-photos-file");
    fileInput.type = "file";
    fileInput.accept = "image/*";
    fileInput.multiple = true;
    fileInput.style.display = "none";
    fileInput.addEventListener("change", function () {
      uploadFiles(fileInput.files, "");
      fileInput.value = "";
    });
    addBtn.addEventListener("click", function () {
      if (!addBtn.disabled) fileInput.click();
    });
    drop.appendChild(addBtn);
    drop.appendChild(fileInput);
    drop.appendChild(el("span", "vnew-loc-photos-droptip", busy ? "만드는 중에는 바꿀 수 없습니다." : "여기로 끌어다 놓아도 됩니다."));

    if (!busy && !full) {
      drop.addEventListener("dragover", function (e) {
        e.preventDefault();
        drop.classList.add("is-drag");
      });
      drop.addEventListener("dragleave", function () {
        drop.classList.remove("is-drag");
      });
      drop.addEventListener("drop", function (e) {
        e.preventDefault();
        drop.classList.remove("is-drag");
        if (e.dataTransfer && e.dataTransfer.files) uploadFiles(e.dataTransfer.files, "");
      });
    }
    wrap.appendChild(drop);

    // 썸네일 목록.
    if (!photos.length) {
      wrap.appendChild(el("p", "vnew-hint", "아직 참고 사진이 없습니다."));
      return wrap;
    }

    var grid = el("div", "vnew-loc-photos-grid");
    photos.forEach(function (photo) {
      var cell = el("div", "vnew-loc-photo-cell");
      var img = el("img", "vnew-loc-photo-thumb");
      img.alt = photo.name || "참고 사진";
      photoThumbFetch(img, base + "/photos/" + encodeURIComponent(photo.photoId));
      cell.appendChild(img);

      // 면 선택.
      var sideSel = el("select", "vnew-loc-photo-side");
      sideSel.disabled = busy;
      sideOrder.forEach(function (s) {
        var opt = el("option", null, sideLabels[s] || s);
        opt.value = s;
        if ((photo.side || "any") === s) opt.selected = true;
        sideSel.appendChild(opt);
      });
      sideSel.addEventListener("change", function () {
        api(base + "/photos/" + encodeURIComponent(photo.photoId), {
          method: "PUT",
          body: JSON.stringify({ side: sideSel.value })
        })
          .then(function (res) {
            reloadWith(res && res.location ? patchedPlanWithLoc(res.location) : null);
          })
          .catch(function (err) {
            if (showError) showError("면을 바꾸지 못했습니다: " + errMessage(err));
            loadPlan(false);
          });
      });
      cell.appendChild(sideSel);

      // 메모.
      var note = el("input", "vnew-loc-photo-note");
      note.type = "text";
      note.maxLength = 200;
      note.placeholder = "메모(선택)";
      note.value = photo.noteKo || "";
      note.disabled = busy;
      note.addEventListener("change", function () {
        api(base + "/photos/" + encodeURIComponent(photo.photoId), {
          method: "PUT",
          body: JSON.stringify({ noteKo: note.value || "" })
        })
          .then(function (res) {
            reloadWith(res && res.location ? patchedPlanWithLoc(res.location) : null);
          })
          .catch(function (err) {
            if (showError) showError("메모를 저장하지 못했습니다: " + errMessage(err));
            loadPlan(false);
          });
      });
      cell.appendChild(note);

      // 삭제.
      var del = el("button", "text-action vnew-loc-photo-del", "삭제");
      del.type = "button";
      del.disabled = busy;
      del.addEventListener("click", function () {
        del.disabled = true;
        api(base + "/photos/" + encodeURIComponent(photo.photoId), { method: "DELETE" })
          .then(function (res) {
            reloadWith(res && res.location ? patchedPlanWithLoc(res.location) : null);
          })
          .catch(function (err) {
            del.disabled = false;
            if (showError) showError("사진을 지우지 못했습니다: " + errMessage(err));
            loadPlan(false);
          });
      });
      cell.appendChild(del);

      grid.appendChild(cell);
    });
    wrap.appendChild(grid);
    return wrap;
  }

  /* 공간 시트(실사·웹툰 탭). 새 방식(hasViews)이면 뷰 A/B 크게 + 펴진 벽 4장·바닥을 한국어 라벨과 함께
     보인다. 옛 데이터(views 없음)면 기존 2x2 sheetUrl 한 장을 그대로 보인다. */
  function locationSheet(loc) {
    var wrap = el("div", "vnew-loc-sheet");
    var variants = loc.variants || {};
    var tabs = el("div", "vnew-loc-tabs");
    var views = el("div", "vnew-loc-sheet-views");
    var order = ["real", "webtoon"];
    var labels = { real: "실사", webtoon: "웹툰" };
    var active = loc._activeVariant || "real";

    order.forEach(function (variant) {
      var tab = el("button", "vnew-loc-tab" + (variant === active ? " is-active" : ""), labels[variant]);
      tab.type = "button";
      tab.addEventListener("click", function () {
        loc._activeVariant = variant;
        renderPlan();
      });
      tabs.appendChild(tab);
    });
    wrap.appendChild(tabs);

    var v = variants[active] || {};
    if (loc.hasViews) {
      views.appendChild(locationViewBlock(loc, v));
    } else if (v.sheetUrl) {
      var img = el("img", "vnew-loc-sheet-img");
      img.src = v.sheetUrl;
      img.alt = (loc.nameEn || "") + " " + active + " sheet";
      views.appendChild(img);
    } else {
      views.appendChild(el("p", "vnew-hint", "아직 이 그림체의 공간 시트가 없습니다."));
    }
    wrap.appendChild(views);
    return wrap;
  }

  /* 새 방식(048): 뷰 A(메인배경 뒤 보기)·뷰 B(앞 보기)를 크게, 그 아래 펴진 벽 4장 + 바닥을 한국어
     라벨과 함께 보인다. 라벨은 서버가 내려 준 loc.viewLabels 를 쓴다(없으면 기본 한국어). */
  function locationViewBlock(loc, v) {
    var box = el("div", "vnew-loc-viewblock");
    var vl = loc.viewLabels || {};
    var viewUrls = v.viewUrls || {};

    // 뷰 A / 뷰 B 큰 그림.
    var bigRow = el("div", "vnew-loc-views-big");
    [
      { key: "back", label: vl.back || "메인배경(뒤) 보기 — 왼쪽·뒤·오른쪽이 이어진 모습" },
      { key: "front", label: vl.front || "앞(맞은편) 보기" }
    ].forEach(function (row) {
      var cell = el("figure", "vnew-loc-view-cell");
      if (viewUrls[row.key]) {
        var img = el("img", "vnew-loc-view-img");
        img.src = viewUrls[row.key];
        img.alt = (loc.nameEn || "") + " " + row.key + " view";
        img.addEventListener("click", function () {
          openLightbox(viewUrls[row.key], (loc.nameKo || loc.nameEn || "") + " " + row.label);
        });
        cell.appendChild(img);
      } else {
        cell.appendChild(el("p", "vnew-hint", "아직 없습니다."));
      }
      cell.appendChild(el("figcaption", "vnew-loc-view-cap", row.label));
      bigRow.appendChild(cell);
    });
    box.appendChild(bigRow);

    // 펴진 벽 4장 + 바닥.
    var wallUrls = v.wallUrls || {};
    var tex = el("div", "vnew-loc-views-tex");
    [
      { url: wallUrls.west, label: vl.left || "왼쪽" },
      { url: wallUrls.north, label: vl.north || "메인배경(뒤)" },
      { url: wallUrls.east, label: vl.right || "오른쪽" },
      { url: wallUrls.south, label: vl.south || "앞(맞은편)" },
      { url: v.floorUrl, label: vl.floor || "바닥" }
    ].forEach(function (row) {
      var cell = el("figure", "vnew-loc-tex-cell");
      if (row.url) {
        var img = el("img", "vnew-loc-tex-img");
        img.src = row.url;
        img.alt = row.label;
        cell.appendChild(img);
      } else {
        cell.appendChild(el("span", "vnew-hint", "—"));
      }
      cell.appendChild(el("figcaption", "vnew-loc-tex-cap", row.label));
      tex.appendChild(cell);
    });
    box.appendChild(tex);
    return box;
  }

  /* 벽별 "웹툰과 일치/보정함" 배지. 활성 그림체(또는 real)의 checks[side] 를 읽는다.
     checks[side] = {matches, fixedEn, fixesEn}. 비교 결과가 하나도 없으면 null. */
  function locationCheckBadges(loc) {
    var variants = loc.variants || {};
    var active = loc._activeVariant || "real";
    var v = variants[active] || {};
    var checks = v.checks || {};
    var sides = ["north", "east", "south", "west"];
    var sideLabels = { north: "메인배경(뒤)", east: "오른쪽", south: "앞", west: "왼쪽" };
    var any = sides.some(function (s) {
      return checks[s];
    });
    if (!any) return null;
    var wrap = el("div", "vnew-loc-badges");
    wrap.appendChild(el("span", "vnew-loc-badges-label", "웹툰 비교:"));
    sides.forEach(function (side) {
      var chk = checks[side];
      if (!chk) return;
      var corrected = chk.matches === false;
      var text = sideLabels[side] + " " + (corrected ? "보정함" : "일치");
      var badge = el("span", "vnew-loc-badge " + (corrected ? "is-corrected" : "is-match"), text);
      if (corrected && (chk.fixesEn || []).length) {
        badge.title = (chk.fixesEn || []).join("\n");
      }
      wrap.appendChild(badge);
    });
    return wrap;
  }

  /* 추출된 사실(벽별 물건) 접어서 보기·수정. 수정하면 PUT /locations/<lid> {facts:{...}} 로 저장하고
     다음 [다시 만들기] 때 그 사실로 프롬프트를 다시 쓴다(factsEditedAt). */
  function locationFactsEditor(loc, showError) {
    var facts = loc.facts || {};
    var walls = facts.walls || {};
    var editor = el("details", "vnew-loc-facts");
    editor.appendChild(el("summary", "vnew-loc-facts-summary", "추출된 사실(벽별 물건) 보기·수정"));

    var sides = ["north", "east", "south", "west"];
    var sideLabels = { north: "메인배경(뒤)", east: "오른쪽", south: "앞", west: "왼쪽" };
    var hasAny = sides.some(function (s) {
      var w = walls[s] || {};
      return (w.objects || []).length || w.wallMaterialEn || w.wallColorEn;
    });
    if (!hasAny) {
      editor.appendChild(el("p", "vnew-hint", "아직 사실이 없습니다. 공간 시트를 만들면 웹툰에서 벽별 물건을 읽어 정리합니다."));
      return editor;
    }

    // 편집 패치를 쌓는다. 각 벽의 재질·물건(종류·수·색·재질·위치·크기)을 한 줄 입력으로 편집한다.
    var patch = { facts: { walls: {} } };

    sides.forEach(function (side) {
      var wall = walls[side] || {};
      var block = el("div", "vnew-loc-facts-wall");
      var head = el("div", "vnew-loc-facts-wall-head");
      head.appendChild(el("span", "vnew-loc-facts-wall-name", sideLabels[side]));
      if (wall.estimated) head.appendChild(el("span", "vnew-loc-facts-est", "추정"));
      block.appendChild(head);

      patch.facts.walls[side] = {};

      // 벽 재질·색.
      var matField = el("label", "vnew-field");
      matField.appendChild(el("span", "vnew-field-label", "벽 재질·색"));
      var matInput = el("input", "vnew-field-input");
      matInput.type = "text";
      matInput.value = [(wall.wallColorEn || ""), (wall.wallMaterialEn || "")].filter(Boolean).join(" ");
      matInput.addEventListener("input", function () {
        var parts = (matInput.value || "").trim().split(/\s+/);
        patch.facts.walls[side].wallColorEn = parts.length > 1 ? parts.shift() : "";
        patch.facts.walls[side].wallMaterialEn = parts.join(" ");
      });
      matField.appendChild(matInput);
      block.appendChild(matField);

      // 물건 목록(한 줄에 하나: "종류 | 수 | 색 | 재질 | 위치 | 크기").
      var objField = el("label", "vnew-field");
      objField.appendChild(el("span", "vnew-field-label", "물건(한 줄에 하나: 종류 | 수 | 색 | 재질 | 위치 | 크기)"));
      var objArea = el("textarea", "vnew-field-input vnew-loc-facts-objects");
      objArea.rows = Math.max(2, (wall.objects || []).length + 1);
      objArea.value = (wall.objects || [])
        .map(function (o) {
          return [o.kind, o.count, o.colorEn, o.materialEn, o.position, o.sizeEn].join(" | ");
        })
        .join("\n");
      objArea.addEventListener("input", function () {
        patch.facts.walls[side].objects = parseFactsObjects(objArea.value);
      });
      objField.appendChild(objArea);
      block.appendChild(objField);

      editor.appendChild(block);
    });

    // 바닥·천장·빛·시대·나라.
    var extra = el("div", "vnew-loc-facts-extra");
    [
      { key: "floorEn", label: "바닥(색·재질·패턴)" },
      { key: "ceilingEn", label: "천장" },
      { key: "lightEn", label: "빛(방향·색온도·세기)" },
      { key: "eraEn", label: "시대" },
      { key: "countryEn", label: "나라" }
    ].forEach(function (row) {
      var field = el("label", "vnew-field");
      field.appendChild(el("span", "vnew-field-label", row.label));
      var input = el("input", "vnew-field-input");
      input.type = "text";
      input.value = facts[row.key] || "";
      input.addEventListener("input", function () {
        patch.facts[row.key] = input.value;
      });
      field.appendChild(input);
      extra.appendChild(field);
    });
    editor.appendChild(extra);

    var saveBtn = el("button", "outline-button", "사실 저장(다시 만들 때 반영)");
    saveBtn.type = "button";
    saveBtn.addEventListener("click", function () {
      saveBtn.disabled = true;
      api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/locations/" + encodeURIComponent(loc.id), {
        method: "PUT",
        body: JSON.stringify(patch)
      })
        .then(function (plan) {
          state.plan = plan;
          renderPlan();
        })
        .catch(function (err) {
          saveBtn.disabled = false;
          if (showError) showError("저장하지 못했습니다: " + errMessage(err));
        });
    });
    editor.appendChild(saveBtn);
    return editor;
  }

  /* "종류 | 수 | 색 | 재질 | 위치 | 크기" 한 줄씩을 물건 객체 배열로 바꾼다. 빈 줄·종류 없는 줄은 뺀다. */
  function parseFactsObjects(text) {
    var out = [];
    (text || "").split("\n").forEach(function (line) {
      var parts = line.split("|").map(function (p) {
        return (p || "").trim();
      });
      var kind = parts[0] || "";
      if (!kind) return;
      out.push({
        kind: kind,
        count: Number(parts[1]) || 1,
        colorEn: parts[2] || "",
        materialEn: parts[3] || "",
        position: parts[4] || "center mid",
        sizeEn: parts[5] || "",
        attachedTo: "wall"
      });
    });
    return out;
  }

  /* 벽 설명·빛 편집. 저장은 PUT /locations/<lid>. 저장 뒤 [다시 만들기] 로 반영. */
  function locationDesignEditor(loc, showError) {
    var design = loc.design || {};
    var walls = design.walls || {};
    var editor = el("details", "vnew-loc-design");
    var summary = el("summary", "vnew-loc-design-summary", "벽 설명·빛 편집");
    editor.appendChild(summary);

    var patch = { walls: {} };
    var sides = ["north", "east", "south", "west"];
    // 048: north=메인배경(뒤), east=오른쪽, south=앞(맞은편), west=왼쪽. 영문 벽 이름 옆에 한국어 표기.
    var sideLabels = {
      north: "north (메인배경/뒤)",
      east: "east (오른쪽)",
      south: "south (앞/맞은편)",
      west: "west (왼쪽)"
    };

    sides.forEach(function (side) {
      var wall = walls[side] || {};
      patch.walls[side] = {};
      var field = el("label", "vnew-field");
      field.appendChild(el("span", "vnew-field-label", sideLabels[side] + " 벽 설명"));
      var input = el("textarea", "vnew-field-input");
      input.rows = 2;
      input.value = wall.descEn || "";
      input.addEventListener("input", function () {
        patch.walls[side].descEn = input.value;
      });
      field.appendChild(input);
      editor.appendChild(field);
    });

    var lightField = el("label", "vnew-field");
    lightField.appendChild(el("span", "vnew-field-label", "빛(어디서 오는지)"));
    var lightInput = el("textarea", "vnew-field-input");
    lightInput.rows = 2;
    lightInput.value = design.lightEn || "";
    lightInput.addEventListener("input", function () {
      patch.lightEn = lightInput.value;
    });
    lightField.appendChild(lightInput);
    editor.appendChild(lightField);

    var saveBtn = el("button", "outline-button", "설계 저장");
    saveBtn.type = "button";
    saveBtn.addEventListener("click", function () {
      saveBtn.disabled = true;
      api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/locations/" + encodeURIComponent(loc.id), {
        method: "PUT",
        body: JSON.stringify(patch)
      })
        .then(function (plan) {
          state.plan = plan;
          renderPlan();
        })
        .catch(function (err) {
          saveBtn.disabled = false;
          if (showError) showError("저장하지 못했습니다: " + errMessage(err));
        });
    });
    editor.appendChild(saveBtn);
    return editor;
  }

  /* 만들기 / 다시 만들기(추가 요청). building 중이면 비활성. */
  function locationActions(loc, showError) {
    var wrap = el("div", "vnew-loc-actions");
    var busy = loc.status === "building" || (loc.task || {}).status === "running";
    var hasSheet = ((loc.variants || {}).real || {}).sheetUrl || ((loc.variants || {}).webtoon || {}).sheetUrl;

    var extraInput = el("input", "vnew-field-input vnew-loc-extra");
    extraInput.type = "text";
    extraInput.placeholder = "다시 만들 때 추가 요청(선택)";

    function build() {
      var body = {};
      var extra = (extraInput.value || "").trim();
      if (extra) body.extraPromptKo = extra;
      api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/locations/" + encodeURIComponent(loc.id) + "/build", {
        method: "POST",
        body: JSON.stringify(body)
      })
        .then(function () {
          loadPlan(false);
        })
        .catch(function (err) {
          if (showError) showError("만들지 못했습니다: " + errMessage(err));
          loadPlan(false);
        });
    }

    var buildBtn = el("button", "primary-button", hasSheet ? "다시 만들기" : "공간 시트 만들기");
    buildBtn.type = "button";
    buildBtn.disabled = busy;
    buildBtn.addEventListener("click", function () {
      buildBtn.disabled = true;
      build();
    });

    if (hasSheet) wrap.appendChild(extraInput);
    wrap.appendChild(buildBtn);
    return wrap;
  }

  /* ③ → ④ : 영상 만들기 확인창. 구간 수·'예상 비용이 듭니다'·설명을 보이고,
     확인하면 POST /generate {confirmUsd: estimate.totalUsd} → ④로. 409 메시지는 그대로 보인다
     (CharacterRealMissing 이면 캐릭터 만들기 링크를 함께 보인다). */
  function openGenerateConfirm(showError, opts) {
    opts = opts || {};
    var plan = state.plan || {};
    var gen = plan.gen || {};
    var est = gen.estimate || {};
    var segCount = (est.segments || []).length || ((plan.conti || {}).segments || []).length;

    var scrim = el("div", "vnew-modal-scrim");
    var modal = el("div", "vnew-modal vnew-gen-confirm");
    var head = el("div", "vnew-modal-head");
    head.appendChild(el("h3", null, "영상 만들기"));
    var close = el("button", "icon-button", "×");
    close.type = "button";
    close.addEventListener("click", closeModal);
    head.appendChild(close);
    modal.appendChild(head);

    var bodyWrap = el("div", "vnew-modal-body");
    var isTake = est.mode === "previz-take";
    var styles = null; // 작업 123: 통 테이크 "만들 영상" 체크 2개(genStylesBox)
    var only = opts.onlyChanged || null; // 작업 205: 바뀐 테이크만 다시 만들기(gen.onlyChanged 금액)
    var onlyLead = null; // 작업 206: 금액이 바뀌면 테이크 목록·금액 글을 제자리에서 바꾼다
    var takeLeadSpan = null;
    var takeHint = null;
    function onlyLeadText() {
      return takeStaleNames(gen, only.takes) + "만 새로 만들고, 나머지 테이크는 이전 영상을 그대로 씁니다.";
    }
    function takeSecsOf(e) {
      var secs = 0;
      ((e || {}).takes || []).forEach(function (t) { secs += Number(t.seconds) || 0; });
      return Math.round(secs);
    }
    function takeLeadText() {
      return GEN_WEBTOON_TITLE + "로 " + takeSecsOf(est) + "초를 한 번에 만듭니다 (480p 초안).";
    }
    if (only) {
      head.firstChild.textContent = "바뀐 테이크만 다시 만들기";
      onlyLead = el("p", "vnew-only-changed-takes", onlyLeadText());
      bodyWrap.appendChild(onlyLead);
      bodyWrap.appendChild(
        el("p", "vnew-hint", only.real ? GEN_WEBTOON_TITLE + "와 실사를 함께 만듭니다(이전 영상과 같은 그림체)." : GEN_WEBTOON_TITLE + "만 만듭니다(이전 영상과 같은 그림체).")
      );
    } else if (isTake) {
      var takeLead = el("p", null, "통 테이크(베타): 승인한 프리비즈 녹화를 통째로 넣어 ");
      takeLeadSpan = el("span", null, takeLeadText());
      takeLead.appendChild(takeLeadSpan);
      bodyWrap.appendChild(takeLead);
      takeHint = bodyWrap.appendChild(
        el("p", "vnew-hint", takePlanHintText("confirm", (est.takes || []).length, takeRealEnabledOf(gen, plan)))
      );
      styles = genStylesBox(est, updateCost);
      bodyWrap.appendChild(styles.node);
    } else {
      bodyWrap.appendChild(
        el("p", null, "승인한 프리비즈 녹화에 실사·웹툰 그림체를 각각 입혀 영상을 만듭니다.")
      );
      bodyWrap.appendChild(el("p", "vnew-hint", "구간 " + segCount + "개를 실사·웹툰 두 벌로 만듭니다."));
    }
    // 이미 결과(done/partial)가 있거나 stale 이면 지금 결과가 '이전 영상'으로만 남는다고 알린다.
    if (opts.hadResult && !only) {
      bodyWrap.appendChild(
        el("p", "vnew-hint vnew-remake-note", "지금 결과는 '이전 영상'으로만 남고 새로 만듭니다.")
      );
    }
    // 금액 숫자는 보이지 않는다(설계자 2026-10-20) — 문구만. 작업 123: 통 테이크는 고른 것의 합을 숫자로 보인다.
    var cost = el("p", "vnew-gen-cost", costText());
    bodyWrap.appendChild(cost);
    function costText() {
      return only ? "예상 비용 " + castingUsdText(only.usd) + " (바뀐 테이크 몫만)"
        : styles ? genStylesCostText(est, styles.real()) : "예상 비용이 듭니다.";
    }
    function updateCost() {
      if (styles) cost.textContent = genStylesCostText(est, styles.real());
    }
    // 보낼 본문(금액 = 지금 확인창이 보이는 금액). 작업 206: 금액이 바뀌면 다시 읽은 값으로 다시 만든다.
    function generateBody() {
      var body = { confirmUsd: Number((gen.estimate || {}).totalUsd) || 0 };
      if (only) {
        body = { confirmUsd: Number(only.usd) || 0, onlyChanged: true };
      } else if (styles) {
        var withReal = styles.real();
        body.styles = withReal ? ["webtoon", "real"] : ["webtoon"];
        if (withReal) body.confirmUsd = takeUsdSum(est.totalUsd, est.realUsd);
      }
      return body;
    }
    // 작업 206: 409 CostMismatch — 계획 보기를 다시 읽어 금액·테이크 목록을 제자리에서 바꾸고 한국어로 알린다(창은 열어 둔다).
    function applyFreshPrice(oldUsd) {
      return rereadPlanView(state.projectId)
        .then(function (fresh) {
          plan = fresh || plan;
          gen = plan.gen || {};
          est = gen.estimate || {};
          if (only) {
            only = gen.onlyChanged || {};
            if (onlyLead) onlyLead.textContent = onlyLeadText();
            if (!only.available) {
              okBtn.disabled = true;
              cancelBtn.disabled = false;
              showModalError(only.block || "바뀐 테이크가 없습니다.");
              cost.textContent = costText();
              return;
            }
          } else {
            if (takeLeadSpan) takeLeadSpan.textContent = takeLeadText();
            if (takeHint) takeHint.textContent = takePlanHintText("confirm", (est.takes || []).length, takeRealEnabledOf(gen, plan));
          }
          cost.textContent = costText();
          okBtn.disabled = false;
          cancelBtn.disabled = false;
          showModalError(costChangedText(oldUsd, generateBody().confirmUsd));
        })
        .catch(function (err) {
          okBtn.disabled = false;
          cancelBtn.disabled = false;
          showModalError("새 금액을 불러오지 못했습니다: " + errMessage(err));
        });
    }

    var error = planError("");
    bodyWrap.appendChild(error);
    function showModalError(message, isMissing) {
      error.style.display = "";
      error.textContent = message;
      if (isMissing) {
        var link = el("button", "text-action", "캐릭터 만들기로 가기");
        link.type = "button";
        link.addEventListener("click", function () {
          closeModal();
          goToCharacters();
        });
        error.appendChild(document.createElement("br"));
        error.appendChild(link);
      }
    }

    var actions = el("div", "vnew-modal-actions");
    var cancelBtn = el("button", "outline-button", "취소");
    cancelBtn.type = "button";
    cancelBtn.addEventListener("click", closeModal);
    var okBtn = el("button", "primary-button", "만들기 시작");
    okBtn.type = "button";
    okBtn.addEventListener("click", function () {
      okBtn.disabled = true;
      cancelBtn.disabled = true;
      var genBody = generateBody();
      api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/generate", {
        method: "POST",
        body: JSON.stringify(genBody)
      })
        .then(function (payload) {
          state.plan = payload.plan || state.plan;
          closeModal();
          state.planStep = 5;
          loadPlan(false);
        })
        .catch(function (err) {
          if (isCostMismatch(err)) return applyFreshPrice(genBody.confirmUsd);
          okBtn.disabled = false;
          cancelBtn.disabled = false;
          var isMissing = err && err.type === "CharacterRealMissing";
          var msg = errMessage(err);
          if (isMissing) {
            msg = msg + " 캐릭터 만들기에서 실사 버전을 완성해 주세요.";
          }
          showModalError(msg, isMissing);
          if (err && err.type === "PrevizStale") {
            // 작업 200: 고친 장면을 다시 녹화하지 않았다 — 버튼 하나로 ④ 에 가서 다시 녹화.
            var rerecord = previzRerecordButton();
            rerecord.addEventListener("click", closeModal);
            error.appendChild(document.createElement("br"));
            error.appendChild(rerecord);
          }
        });
    });
    actions.appendChild(cancelBtn);
    actions.appendChild(okBtn);
    bodyWrap.appendChild(actions);

    modal.appendChild(bodyWrap);
    scrim.appendChild(modal);
    scrim.addEventListener("click", function (event) {
      if (event.target === scrim) closeModal();
    });
    document.body.appendChild(scrim);

    function closeModal() {
      if (scrim.parentNode) scrim.parentNode.removeChild(scrim);
    }
  }

  function contiSummary(totalSeconds, sumSeconds, showError) {
    var wrap = el("section", "panel vnew-conti-summary");
    var line = el("div", "vnew-conti-summary-line");
    line.appendChild(el("span", null, "목표 " + totalSeconds + "초"));
    line.appendChild(el("span", null, "현재 합계 " + sumSeconds + "초"));
    wrap.appendChild(line);
    if (totalSeconds !== sumSeconds) {
      wrap.classList.add("is-warn");
      wrap.appendChild(
        el("p", "vnew-hint", "합계가 목표와 다릅니다. [길이 자동 맞춤] 으로 초를 맞출 수 있습니다.")
      );
      var fitBtn = el("button", "outline-button", "길이 자동 맞춤");
      fitBtn.type = "button";
      fitBtn.addEventListener("click", function () {
        fitBtn.disabled = true;
        api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/conti/fit", { method: "POST" })
          .then(function (plan) {
            state.plan = plan;
            renderPlan();
          })
          .catch(function (err) {
            fitBtn.disabled = false;
            showError("자동 맞춤에 실패했습니다: " + errMessage(err));
          });
      });
      wrap.appendChild(fitBtn);
    }
    return wrap;
  }

  function segmentsPanel(segments, cuts) {
    var wrap = el("section", "panel vnew-segments");
    var maxN = modelMaxSeconds();
    var lead = maxN > 0
      ? "모델 최대 " + maxN + "초 → " + segments.length + "번 나눠 만들고 이어 붙입니다."
      : segments.length + "번 나눠 만들고 이어 붙입니다.";
    wrap.appendChild(el("p", "vnew-hint", lead));

    // 컷 id → 구간 색 인덱스
    var segOfCut = {};
    segments.forEach(function (seg, segIndex) {
      (seg.cutIds || []).forEach(function (cid) {
        segOfCut[cid] = segIndex;
      });
    });

    var bars = el("div", "vnew-seg-bars");
    segments.forEach(function (seg, segIndex) {
      var bar = el("div", "vnew-seg-bar vnew-seg-c" + (segIndex % 6));
      bar.appendChild(el("span", "vnew-seg-label", "구간 " + (segIndex + 1)));
      bar.appendChild(el("span", "vnew-seg-seconds", (seg.seconds || 0) + "초"));
      bar.appendChild(el("span", "vnew-seg-cuts", "컷 " + (seg.cutIds || []).length + "개"));
      bars.appendChild(bar);
    });
    wrap.appendChild(bars);
    wrap._segOfCut = segOfCut;
    return wrap;
  }

  /* ---- 콘티 샷 크기(지시서 120) ---- */
  // 서버 prompts.SHOT_SIZES 와 같은 키·순서. 화면에는 한국어 이름만 쓴다.
  var SHOT_SIZE_KO = {
    wide: "와이드",
    medium: "미디엄",
    closeup: "클로즈업",
    extreme_closeup: "익스트림 클로즈업",
    over_shoulder: "어깨 너머",
    profile: "옆모습",
    insert: "인서트"
  };

  /* 컷의 대표 패널 번호(0 이상 정수). 없거나 맞지 않으면 null(옛 컷). */
  function contiSourcePanel(cut) {
    var p = cut ? cut.sourcePanel : null;
    return typeof p === "number" && isFinite(p) && p >= 0 && Math.floor(p) === p ? p : null;
  }

  /* 컷 카드 머리 글: "패널 N · 클로즈업". 값이 있는 것만, 둘 다 없으면 "". */
  function contiShotHead(cut) {
    var parts = [];
    var panel = contiSourcePanel(cut);
    if (panel !== null) parts.push("패널 " + panel);
    var size = cut && typeof cut.shotSize === "string" ? cut.shotSize : "";
    if (size && Object.prototype.hasOwnProperty.call(SHOT_SIZE_KO, size)) parts.push(SHOT_SIZE_KO[size]);
    return parts.join(" · ");
  }
  /* ---- 콘티 샷 크기 끝 ---- */

  function cutCard(cut, index, cuts, segments) {
    var card = el("div", "vnew-cut-card");
    // 구간 색 띠
    var segIndex = -1;
    segments.forEach(function (seg, si) {
      if ((seg.cutIds || []).indexOf(cut.id) >= 0) segIndex = si;
    });
    if (segIndex >= 0) {
      card.classList.add("vnew-seg-edge-c" + (segIndex % 6));
    }

    var head = el("div", "vnew-cut-head");
    head.appendChild(el("span", "vnew-cut-no", "컷 " + (index + 1)));
    var shotHead = contiShotHead(cut);
    if (shotHead) head.appendChild(el("span", "vnew-cut-shot", shotHead));

    // 초 입력
    var secWrap = el("label", "vnew-cut-sec");
    secWrap.appendChild(el("span", "vnew-field-label", "초"));
    var secInput = el("input", "vnew-field-input");
    secInput.type = "number";
    secInput.min = "1";
    secInput.value = String(cut.seconds || 1);
    secInput.addEventListener("input", function () {
      cut.seconds = Math.max(1, Math.round(Number(secInput.value) || 1));
    });
    secWrap.appendChild(secInput);
    head.appendChild(secWrap);

    // 참고 웹툰 컷 썸네일(지시서 120: 대표 패널이 있으면 그 패널을 테두리로 강조)
    var sourcePanel = contiSourcePanel(cut);
    var thumb = panelThumb(sourcePanel !== null ? sourcePanel : (cut.panels || [])[0]);
    if (thumb && thumb.url) {
      var img = el("img", "vnew-cut-thumb" + (sourcePanel !== null ? " is-source" : ""));
      img.src = thumb.url;
      img.alt = "참고 컷";
      if (sourcePanel !== null) img.title = "원본 패널 " + sourcePanel;
      img.loading = "lazy";
      img.addEventListener("click", function () {
        openLightbox(thumb.url, "참고 컷");
      });
      head.appendChild(img);
    }

    var moveUp = el("button", "icon-button vnew-entry-move", "▲");
    moveUp.type = "button";
    moveUp.title = "위로";
    moveUp.disabled = index === 0;
    moveUp.addEventListener("click", function () {
      swapCuts(cuts, index, index - 1);
    });
    var moveDown = el("button", "icon-button vnew-entry-move", "▼");
    moveDown.type = "button";
    moveDown.title = "아래로";
    moveDown.disabled = index === cuts.length - 1;
    moveDown.addEventListener("click", function () {
      swapCuts(cuts, index, index + 1);
    });
    var del = el("button", "icon-button danger", "×");
    del.type = "button";
    del.title = "삭제";
    del.addEventListener("click", function () {
      cuts.splice(index, 1);
      renderPlan();
    });
    var controls = el("div", "vnew-entry-controls");
    controls.appendChild(moveUp);
    controls.appendChild(moveDown);
    controls.appendChild(del);
    head.appendChild(controls);
    card.appendChild(head);

    var grid = el("div", "vnew-cut-grid");
    grid.appendChild(textField("샷 종류", cut, "shotKo", false));
    grid.appendChild(textField("카메라 (영어)", cut, "cameraEn", false));
    grid.appendChild(textField("장소 (영어)", cut, "locationEn", false));
    grid.appendChild(textField("대사", cut, "dialogueKo", true));
    grid.appendChild(textField("상황", cut, "situationKo", true));
    card.appendChild(grid);
    if (fxEnabled()) card.appendChild(fxRow(cut));
    var thinkers = contiThoughtSpeakers(cut, (((state.plan || {}).script || {}).entries) || []);
    if (thinkers.length) card.appendChild(el("p", "vnew-hint", "속마음: " + thinkers.join(", ") + " (입 다문 채 목소리만)"));

    // 인물 위치·동작
    var who = cut.characters || [];
    if (who.length) {
      var whoWrap = el("div", "vnew-entry-who");
      who.forEach(function (person, pIndex) {
        whoWrap.appendChild(contiPersonRow(person, pIndex));
      });
      card.appendChild(whoWrap);
    }

    // 영상 프롬프트(영어) — 접어 두기
    var details = document.createElement("details");
    details.className = "vnew-cut-prompt";
    var summary = document.createElement("summary");
    summary.textContent = "영상 프롬프트 (영어)";
    details.appendChild(summary);
    var promptInput = el("textarea", "vnew-field-input");
    promptInput.rows = 3;
    promptInput.value = cut.promptEn || "";
    promptInput.addEventListener("input", function () {
      cut.promptEn = promptInput.value;
    });
    details.appendChild(promptInput);
    card.appendChild(details);

    return card;
  }

  function swapCuts(cuts, a, b) {
    if (b < 0 || b >= cuts.length) return;
    var tmp = cuts[a];
    cuts[a] = cuts[b];
    cuts[b] = tmp;
    renderPlan();
  }

  /* ---- 작업 162 fix1: 컷 인물 줄(이름 · 나옴 · 한국어 동작). 콘티는 컷마다 그 장면의 모든 인물과 하는 일을 적는다.
     나옴(presence)·동작을 고치면 그 인물 객체(cut.characters[i])가 바뀌고 [저장](PUT /conti)이 보낸다(프리비즈 승인은 지금처럼 풀린다).
     presence 가 없는 옛 인물은 "(미정)"으로 보이고, 고르기 전까지 값을 넣지 않는다. 글은 textContent(el)로만 넣는다. ---- */
  var PRESENCE_ORDER = ["on_screen", "hand_only", "voice_only", "off_screen"];
  var PRESENCE_KO = { on_screen: "화면에 보임", hand_only: "손만 보임", voice_only: "목소리만", off_screen: "화면 밖" };

  function contiPersonRow(person, pIndex) {
    var row = el("div", "vnew-entry-person");
    if (person.presence) row.classList.add("vnew-presence-" + person.presence);
    row.appendChild(el("span", "vnew-entry-person-name", person.name || "인물 " + (pIndex + 1)));
    var presWrap = el("label", "vnew-field vnew-entry-presence");
    presWrap.appendChild(el("span", "vnew-field-label", "나옴"));
    var select = el("select", "vnew-field-input");
    var options = (PRESENCE_ORDER.indexOf(person.presence) >= 0 ? [] : [""]).concat(PRESENCE_ORDER);
    options.forEach(function (value) {
      var opt = el("option", "", value ? PRESENCE_KO[value] : "(미정)");
      opt.value = value;
      select.appendChild(opt);
    });
    select.value = PRESENCE_ORDER.indexOf(person.presence) >= 0 ? person.presence : "";
    select.addEventListener("change", function () {
      if (PRESENCE_ORDER.indexOf(select.value) < 0) return;
      person.presence = select.value;
      if (person.presence === "off_screen" || person.presence === "voice_only") person.hands = {};
    });
    presWrap.appendChild(select);
    row.appendChild(presWrap);
    row.appendChild(textField("동작 (한국어)", person, "actionKo", false));
    row.appendChild(textField("위치 (영어)", person, "positionEn", false));
    row.appendChild(textField("동작 (영어)", person, "actionEn", false));
    return row;
  }
  /* ---- 작업 162 fix1 끝 ---- */

  /* ================================================================ ③ 기호·분위기 (vnew-live-fx 묶음 A, RA7)

     컷 필드: fx(적용 0~2) · fxSuggest(추천, 좋은 순) · fxDrawn(패널에 그려진 기호) · moodKo(60자) · moodEn(영어, 서버가 채움).
     plan.features.fx 가 false 면 칩·분위기 칸·채우기 버튼·④ 한 줄을 그리지 않는다. 글은 모두 textContent(el)로만 넣는다.
     FX_LABEL_KO 는 vnew/fx.py 와 같은 글자다(검사 fx-l). 아래 순수 함수(DOM 없음)는 검사가 node 로 실행한다. */
  var FX_KEYS = ["tear streams", "sweat drop", "blush lines", "pink sparkle background", "speed lines"];
  var FX_LABEL_KO = {
    "tear streams": "눈물 줄기",
    "sweat drop": "땀 한 방울",
    "blush lines": "볼 홍조",
    "pink sparkle background": "분홍 반짝이 배경",
    "speed lines": "속도선"
  };
  var FX_APPLY_MAX = 2;
  var FX_MOOD_PLACEHOLDER = "분위기·표정 한 줄 (예: 민지가 당황해 굳은 웃음, 어색한 공기)";
  var FX_TRANSLATE_HINT = "영상용 영어 문장이 아직 없습니다. 영어로 옮기려면 [기호·분위기 채우기]를 누르세요.";

  /* 알려진 기호만, 중복 없이, limit 개(서버 fx_list 와 같은 규칙: 소문자·공백 정리). */
  function fxKeys(value, limit) {
    var list = typeof value === "string" ? [value] : (Array.isArray(value) ? value : []);
    var out = [];
    for (var i = 0; i < list.length && out.length < limit; i++) {
      if (typeof list[i] !== "string") continue;
      var key = list[i].toLowerCase().split(/\s+/).filter(Boolean).join(" ");
      if (FX_KEYS.indexOf(key) >= 0 && out.indexOf(key) < 0) out.push(key);
    }
    return out;
  }

  /* 칩 순서·상태: 추천(fxSuggest) 먼저, 그다음 나머지 FX_KEYS. 적용이 2개면 나머지 칩은 full. */
  function fxChipStates(cut) {
    var c = cut || {};
    var applied = fxKeys(c.fx, FX_APPLY_MAX);
    var suggest = fxKeys(c.fxSuggest, FX_APPLY_MAX);
    var drawn = fxKeys(c.fxDrawn, FX_KEYS.length);
    var order = suggest.concat(FX_KEYS.filter(function (key) { return suggest.indexOf(key) < 0; }));
    return order.map(function (key) {
      var on = applied.indexOf(key) >= 0;
      return {
        key: key,
        label: FX_LABEL_KO[key],
        on: on,
        suggest: suggest.indexOf(key) >= 0,
        drawn: drawn.indexOf(key) >= 0,
        full: !on && applied.length >= FX_APPLY_MAX
      };
    });
  }

  /* 칩을 누른 뒤 적용 목록. 있으면 빼고, 없으면 2개 미만일 때만 넣는다(2개면 그대로). */
  function fxToggled(list, key) {
    var applied = fxKeys(list, FX_APPLY_MAX);
    var at = applied.indexOf(key);
    if (at >= 0) return applied.slice(0, at).concat(applied.slice(at + 1));
    if (FX_KEYS.indexOf(key) < 0 || applied.length >= FX_APPLY_MAX) return applied;
    return applied.concat([key]);
  }

  /* 작업 127: 인물 표현 기호(누구에게 붙일지 고른다). 배경 기호는 대상이 없다(fx.py PERSON_FX 와 같은 글자). */
  var FX_PERSON_KEYS = ["tear streams", "sweat drop", "blush lines"];
  var FX_WHO_MAX = 4;

  /* 컷 인물 이름(characters[].name, 공백 정리, 중복 없이). 대상 칩 후보다. */
  function fxCutNames(cut) {
    var chars = cut && Array.isArray(cut.characters) ? cut.characters : [];
    var out = [];
    chars.forEach(function (c) {
      var name = c && typeof c.name === "string" ? c.name.replace(/\s+/g, " ").trim() : "";
      if (name && out.indexOf(name) < 0) out.push(name);
    });
    return out;
  }

  function fxWhoOf(cut) {
    var who = (cut || {}).fxWho;
    return who && typeof who === "object" && !Array.isArray(who) ? who : {};
  }

  /* 고른 인물 표현 기호마다 대상 칩 상태: [{key, label, names: [{name, on}]}]. 컷 인물이 없으면 []. */
  function fxWhoStates(cut) {
    var c = cut || {};
    var names = fxCutNames(c);
    if (!names.length) return [];
    var who = fxWhoOf(c);
    return fxKeys(c.fx, FX_APPLY_MAX).filter(function (key) { return FX_PERSON_KEYS.indexOf(key) >= 0; }).map(function (key) {
      var picked = Array.isArray(who[key]) ? who[key] : [];
      return {
        key: key,
        label: FX_LABEL_KO[key],
        names: names.map(function (name) { return { name: name, on: picked.indexOf(name) >= 0 }; })
      };
    });
  }

  /* 대상 칩을 누른 뒤 fxWho(새 객체). 그 컷 인물·인물 표현 기호만 남기고, 있으면 빼고 없으면 넣는다(최대 4명).
     빈 기호는 키째 뺀다(서버 fx_who 와 같은 규칙). */
  function fxWhoToggled(cut, key, name) {
    var names = fxCutNames(cut);
    var who = fxWhoOf(cut);
    var out = {};
    Object.keys(who).forEach(function (k) {
      if (FX_PERSON_KEYS.indexOf(k) < 0 || !Array.isArray(who[k])) return;
      var list = who[k].filter(function (n, i) { return names.indexOf(n) >= 0 && who[k].indexOf(n) === i; });
      if (list.length) out[k] = list.slice(0, FX_WHO_MAX);
    });
    if (FX_PERSON_KEYS.indexOf(key) < 0 || names.indexOf(name) < 0) return out;
    var cur = (out[key] || []).slice();
    var at = cur.indexOf(name);
    if (at >= 0) cur.splice(at, 1);
    else if (cur.length < FX_WHO_MAX) cur.push(name);
    if (cur.length) out[key] = cur;
    else delete out[key];
    return out;
  }

  /* 번역 안내를 보일지: 분위기(한국어)는 있는데 영상용 영어가 비었다(고친 뒤 저장했거나 아직 옮기지 않음). */
  function fxNeedsTranslation(cut) {
    var c = cut || {};
    return !!String(c.moodKo || "").trim() && !String(c.moodEn || "").trim();
  }

  function fxCount(value) {
    var n = Number(value);
    return isFinite(n) && n > 0 ? Math.floor(n) : 0;
  }

  /* [기호·분위기 채우기] 버튼 글·확인창 글·오류 글(plan.fxFill: targets·translate·estimateUsd·status·error). */
  function fxFillButtonText(fill) {
    var f = fill || {};
    return "기호·분위기 채우기 (빈 컷 " + fxCount(f.targets) + "개 · 영어 옮기기 " + fxCount(f.translate) + "개)";
  }

  function fxFillConfirmText(fill) {
    var f = fill || {};
    var usd = Number(f.estimateUsd);
    return "빈 컷 " + fxCount(f.targets) + "개 · 영어 옮기기 " + fxCount(f.translate) + "개를 채웁니다. 예상 비용 약 $" +
      (isFinite(usd) && usd > 0 ? usd : 0).toFixed(2) + "입니다. 계속할까요?";
  }

  function fxFillErrorText(fill) {
    var f = fill || {};
    if (f.status !== "failed") return "";
    return (String(f.error || "").trim() || "기호·분위기를 채우지 못했습니다.") + " [기호·분위기 채우기]로 다시 시도할 수 있습니다.";
  }

  /* ④ 프리비즈 컷 카드 한 줄(읽기 전용): "기호: 볼 홍조 · 땀 한 방울 · 분위기: …". 둘 다 없으면 "". */
  function previzFxText(contiCut) {
    var c = contiCut || {};
    var parts = [];
    var labels = fxKeys(c.fx, FX_APPLY_MAX).map(function (key) { return FX_LABEL_KO[key]; });
    if (labels.length) parts.push("기호: " + labels.join(" · "));
    var mood = String(c.moodKo || "").replace(/\s+/g, " ").trim();
    if (mood) parts.push("분위기: " + mood);
    return parts.join(" · ");
  }

  function fxEnabled() {
    return !!(((state.plan || {}).features || {}).fx);
  }

  /* ③ 컷 카드의 기호 칩 줄 + 분위기 한 줄. 칩을 누르면 cut.fx 만 바꾸고 칩 줄만 다시 그린다(저장은 [저장]). */
  function fxRow(cut) {
    var wrap = el("div", "vnew-fx-row");
    var chips = el("div", "vnew-fx-chips");
    // 작업 127: 고른 인물 표현 기호의 대상 인물 칩(화면에서는 CSS order 로 칩 줄 바로 아래, DOM 은 분위기 칸 뒤).
    var whoRow = el("div", "vnew-fx-who");

    function drawWho() {
      clear(whoRow);
      var groups = fxWhoStates(cut);
      whoRow.style.display = groups.length ? "" : "none";
      groups.forEach(function (group) {
        var line = el("div", "vnew-fx-who-group");
        line.appendChild(el("span", "vnew-field-label vnew-fx-label", group.label + " 대상"));
        group.names.forEach(function (item) {
          var btn = el("button", "vnew-fx-chip vnew-fx-who-chip" + (item.on ? " is-on" : ""), item.name);
          btn.type = "button";
          btn.setAttribute("aria-pressed", item.on ? "true" : "false");
          btn.title = "이 기호를 " + item.name + "에게만 붙입니다 (아무도 고르지 않으면 대상 없이)";
          btn.addEventListener("click", function () {
            cut.fxWho = fxWhoToggled(cut, group.key, item.name);
            drawWho();
          });
          line.appendChild(btn);
        });
        whoRow.appendChild(line);
      });
    }

    function drawChips() {
      drawWho();
      clear(chips);
      chips.appendChild(el("span", "vnew-field-label vnew-fx-label", "기호"));
      var states = fxChipStates(cut);
      var noneBtn = el("button", "vnew-fx-chip" + (fxKeys(cut.fx, FX_APPLY_MAX).length ? "" : " is-on"), "없음");
      noneBtn.type = "button";
      noneBtn.title = "기호를 쓰지 않습니다";
      noneBtn.addEventListener("click", function () {
        cut.fx = [];
        drawChips();
      });
      chips.appendChild(noneBtn);
      states.forEach(function (chip) {
        var cls = "vnew-fx-chip" + (chip.on ? " is-on" : "") + (chip.suggest ? " is-suggest" : "") +
          (chip.drawn ? " is-drawn" : "") + (chip.full ? " is-full" : "");
        var btn = el("button", cls, chip.label);
        btn.type = "button";
        btn.setAttribute("aria-pressed", chip.on ? "true" : "false");
        var titles = [];
        if (chip.drawn) titles.push("패널에 그려진 기호");
        if (chip.suggest) titles.push("이 컷에 추천하는 기호");
        if (chip.full) titles.push("최대 2개까지 고를 수 있습니다");
        if (titles.length) btn.title = titles.join(" · ");
        if (chip.suggest) btn.appendChild(el("span", "vnew-fx-chip-tag", "추천"));
        if (chip.drawn) btn.appendChild(el("span", "vnew-fx-chip-tag is-drawn", "그림"));
        btn.addEventListener("click", function () {
          if (chip.full) return;
          cut.fx = fxToggled(cut.fx, chip.key);
          drawChips();
        });
        chips.appendChild(btn);
      });
    }

    drawChips();
    wrap.appendChild(chips);

    var moodWrap = el("label", "vnew-field vnew-fx-mood");
    moodWrap.appendChild(el("span", "vnew-field-label", "분위기·표정"));
    var input = el("input", "vnew-field-input vnew-mood-input");
    input.type = "text";
    input.maxLength = 60;
    input.placeholder = FX_MOOD_PLACEHOLDER;
    input.value = cut.moodKo || "";
    var loaded = input.value;
    var hint = el("p", "vnew-hint vnew-fx-hint", FX_TRANSLATE_HINT);
    function updateHint() {
      // 저장된 영어가 없거나, 분위기를 고쳐 저장하면 영어가 비워지는 경우(서버 update_conti 규칙) 안내한다.
      var changed = input.value.trim() && input.value !== loaded;
      hint.style.display = fxNeedsTranslation(cut) || changed ? "" : "none";
    }
    input.addEventListener("input", function () {
      cut.moodKo = input.value;
      updateHint();
    });
    moodWrap.appendChild(input);
    moodWrap.appendChild(hint);
    updateHint();
    wrap.appendChild(moodWrap);
    wrap.appendChild(whoRow);
    return wrap;
  }

  /* ③ 컷 목록 위 [기호·분위기 채우기]. 확인창에 대상 수·예상 금액(Claude 결정 1) → 지금 수정본 PUT → POST /conti/fx.
     대상 수·금액은 서버 plan_view.fxFill(저장된 콘티 기준)이다. 대상이 0이면 누를 수 없다. 저장으로 대상 수·금액이
     바뀌면(예: 고친 분위기가 영어 옮기기 대상이 됨) 새 숫자로 한 번 더 묻고, 대상이 0이 되면 시작하지 않는다. */
  function fxSameFill(a, b) {
    return fxCount((a || {}).targets) === fxCount((b || {}).targets) &&
      fxCount((a || {}).translate) === fxCount((b || {}).translate) &&
      Number((a || {}).estimateUsd || 0).toFixed(2) === Number((b || {}).estimateUsd || 0).toFixed(2);
  }

  function fxToolbar(cuts, showError) {
    var fill = (state.plan || {}).fxFill || {};
    var wrap = el("div", "vnew-fx-toolbar");
    var btn = el("button", "outline-button vnew-fx-fill", fxFillButtonText(fill));
    btn.type = "button";
    var empty = fxCount(fill.targets) + fxCount(fill.translate) === 0;
    btn.disabled = empty;
    btn.title = empty ? "채울 컷이 없습니다(고친 분위기는 먼저 [저장]하면 영어 옮기기 대상이 됩니다)" : "";
    btn.addEventListener("click", function () {
      if (!window.confirm(fxFillConfirmText(fill))) return;
      btn.disabled = true;
      var base = "/api/vnew/p/" + encodeURIComponent(state.projectId);
      api(base + "/conti", { method: "PUT", body: JSON.stringify({ cuts: cuts }) })
        .then(function (plan) {
          state.plan = plan;
          var next = plan.fxFill || {};
          btn.textContent = fxFillButtonText(next);
          if (fxCount(next.targets) + fxCount(next.translate) === 0) {
            showError("저장했습니다. 채울 컷이 없습니다.");
            return null;
          }
          if (!fxSameFill(fill, next)) {
            fill = next;
            if (!window.confirm(fxFillConfirmText(next))) {
              btn.disabled = false;
              return null;
            }
          }
          return api(base + "/conti/fx", { method: "POST" });
        })
        .then(function (started) {
          if (started) loadPlan(false);
        })
        .catch(function (err) {
          btn.disabled = false;
          showError("기호·분위기 채우기를 시작하지 못했습니다: " + errMessage(err));
        });
    });
    wrap.appendChild(btn);
    wrap.appendChild(el("span", "vnew-hint", "빈 컷만 채우고, 고친 분위기는 영어로 옮깁니다. 프리비즈 승인은 그대로입니다."));
    return wrap;
  }

  /* 작업 162: ③ [손 주인 다시 읽기] → POST /conti/reread-actions. 화면 밖에서 들어온 손을 주인(옷·소매로 판단)에게 돌려 컷 인물
     연기와 프리비즈 손만 다시 읽는다. 컷·초·대사·기호·카메라·승인·녹화는 그대로이고, 끝나면 ④ 에 [포즈 다시 맞추기]가 보인다. */
  function rereadActionsToolbar(showError) {
    var wrap = el("div", "vnew-fx-toolbar vnew-reread-toolbar");
    var btn = el("button", "outline-button vnew-reread-actions", "손 주인 다시 읽기");
    btn.type = "button";
    btn.addEventListener("click", function () {
      if (!window.confirm("컷마다 웹툰 패널을 다시 보고 인물 연기(누구의 손인지)만 다시 읽습니다. 컷마다 분석 1회 비용이 듭니다. " +
        "컷·초·대사·기호·카메라·프리비즈 승인은 그대로입니다. 시작할까요?")) return;
      btn.disabled = true;
      api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/conti/reread-actions", { method: "POST" })
        .then(function () {
          loadPlan(false);
        })
        .catch(function (err) {
          btn.disabled = false;
          showError("손 주인 다시 읽기를 시작하지 못했습니다: " + errMessage(err));
        });
    });
    wrap.appendChild(btn);
    wrap.appendChild(el("span", "vnew-hint", "화면 밖에서 들어온 손을 그 손 주인의 연기로 고칩니다(인물 연기·프리비즈 손만)."));
    return wrap;
  }

  /* 작업 162: ④ previz.posesStale 이면 [포즈 다시 맞추기] → POST /previz/refresh-poses(카메라·배치 그대로, 승인 해제 → 다시 녹화). */
  function previzPosesStaleSection(previz, showError) {
    if (!previz || !previz.posesStale) return null;
    var wrap = el("div", "vnew-fx-toolbar vnew-previz-poses-stale");
    var btn = el("button", "primary-button vnew-refresh-poses", "포즈 다시 맞추기");
    btn.type = "button";
    btn.addEventListener("click", function () {
      btn.disabled = true;
      api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/previz/refresh-poses", { method: "POST" })
        .then(function () {
          loadPlan(false);
        })
        .catch(function (err) {
          btn.disabled = false;
          showError("포즈를 다시 맞추지 못했습니다: " + errMessage(err));
        });
    });
    wrap.appendChild(btn);
    wrap.appendChild(el("span", "vnew-hint", "콘티 연기를 다시 읽었습니다. 마네킹 손·자세를 새 연기에 맞춥니다. 카메라·배치는 그대로이고, " +
      "다시 승인하면 다시 녹화합니다."));
    return wrap;
  }

  /* ④ 프리비즈 컷 카드(받침 줄 아래) 읽기 전용 한 줄. previz 컷(cutId)으로 콘티 컷을 찾는다. */
  function previzFxLine(previzCut) {
    if (!fxEnabled()) return null;
    var cutId = previzCut && previzCut.cutId;
    var cuts = (((state.plan || {}).conti || {}).cuts) || [];
    var contiCut = null;
    for (var i = 0; i < cuts.length; i++) {
      if (cuts[i] && cuts[i].id === cutId) {
        contiCut = cuts[i];
        break;
      }
    }
    var text = previzFxText(contiCut);
    if (!text) return null;
    var line = el("p", "vnew-previz-fx-line", text);
    line.title = "③ 콘티에서 정한 기호·분위기입니다. 영상 프롬프트에 들어갑니다(마네킹·장면은 바꾸지 않습니다).";
    return line;
  }

  /* ================================================================ ④ 프리비즈(3D)

     서버 previz_view(previz.py) 모양을 쓴다: status·scenes(장면 JSON + 텍스처 URL)·segments·
     recordings·recordedAll·approved·task. 설계 전이면 [3D 장면 설계하기](POST /previz/design),
     설계 후이면 three.js 뷰포트(window.vnewPreviz)로 재생·탐색·장면 전환하고, 우측에서 현재 컷의
     카메라·인물을 편집(PUT /previz/scene)한다. [승인하고 녹화]→approve→구간마다 renderFrames(프레임
     오프라인 렌더)→50장 묶음 업로드(POST frames)→finish 로 서버가 ffmpeg mp4 를 만든다. 실시간이
     아니므로 창을 가려도 계속된다. */

  // 프리비즈 뷰포트 컨트롤러(window.vnewPreviz.create 결과)와 녹화 상태를 화면 단위로 둔다.
  var previzCtl = null; // 현재 화면의 controller
  var previzCutIndex = 0; // 우측 패널이 보여 주는 timeline 컷 인덱스(평탄화)
  var previzRecording = null; // 녹화 진행 상태 {total, done, failed:[], current, aborted}
  var previzDirty = false; // 작업 200: 편집 패널에서 고치고 아직 [저장]하지 않았다(녹화 전에 저장해야 한다)
  var PREVIZ_LEAVE_TEXT = "저장하지 않은 프리비즈 수정이 있습니다. 저장하지 않고 떠날까요?";
  // 작업 207: 마지막으로 불러오거나 저장한 장면(scenes 배열)과 그때의 정리한 JSON. 되돌리기·같은 값 다시 입력이면 dirty 가 풀린다.
  var previzSaved = { scenes: null, snap: null };
  var PREVIZ_LABELS_KEY = "vnew-previz-labels"; // 작업 207: [이름표] 끔 = "0"(localStorage)

  function previzLabelsOn() {
    try {
      return window.localStorage.getItem(PREVIZ_LABELS_KEY) !== "0";
    } catch (error) {
      return true;
    }
  }

  function previzSetLabelsPref(on) {
    try {
      window.localStorage.setItem(PREVIZ_LABELS_KEY, on ? "1" : "0");
    } catch (error) {
      /* 무시 — 저장소를 못 쓰면 이번 화면에서만 */
    }
  }

  /* 작업 213: 편집 패널(스크롤 칸) 안에서만 그 카드가 보이게 굴린다. scrollIntoView 는 페이지도 굴려 왼쪽 뷰포트·키 줄이 움직였다. */
  function previzScrollPanelTo(panel, card) {
    if (!panel || !card || !panel.getBoundingClientRect || !card.getBoundingClientRect) return;
    var pr = panel.getBoundingClientRect();
    var cr = card.getBoundingClientRect();
    var pad = 8;
    if (cr.top < pr.top) {
      panel.scrollTop += cr.top - pr.top - pad;
    } else if (cr.bottom > pr.bottom) {
      panel.scrollTop += Math.min(cr.top - pr.top - pad, cr.bottom - pr.bottom + pad);
    }
  }


  /* 작업 209b: [포즈 모음] 사용자 저장 포즈 — 이 브라우저의 localStorage 에 [{id, nameKo, rig}] (최대 40개).
     읽기·쓰기는 try/catch(저장소를 못 쓰면 이번 화면에서만). rig = 15개 관절 + rootY(서기 기준). */
  var PREVIZ_POSE_LIBRARY_KEY = "vnew-previz-pose-library";
  var PREVIZ_POSE_LIBRARY_MAX = 40;
  var previzPoseLibraryMemory = null;

  function previzUserPoses() {
    if (previzPoseLibraryMemory) return previzPoseLibraryMemory.slice();
    var list = [];
    try {
      var raw = JSON.parse(window.localStorage.getItem(PREVIZ_POSE_LIBRARY_KEY) || "[]");
      if (Array.isArray(raw)) {
        list = raw.filter(function (p) {
          return p && typeof p === "object" && typeof p.nameKo === "string" && p.rig && typeof p.rig === "object";
        });
      }
    } catch (error) {
      list = [];
    }
    previzPoseLibraryMemory = list.slice(0, PREVIZ_POSE_LIBRARY_MAX);
    return previzPoseLibraryMemory.slice();
  }

  function previzSaveUserPoses(list) {
    previzPoseLibraryMemory = (list || []).slice(0, PREVIZ_POSE_LIBRARY_MAX);
    try {
      window.localStorage.setItem(PREVIZ_POSE_LIBRARY_KEY, JSON.stringify(previzPoseLibraryMemory));
    } catch (error) {
      /* 무시 — 저장소를 못 쓰면 이번 화면에서만 */
    }
  }

  /* 작업 207: 비교용 JSON — 키 정렬 · 숫자 소수 셋째 자리 · undefined 키 뺌(저장 형식은 그대로, 비교만). */
  function previzNormJson(value) {
    if (Array.isArray(value)) return "[" + value.map(previzNormJson).join(",") + "]";
    if (value && typeof value === "object") {
      return "{" + Object.keys(value).sort().filter(function (k) {
        return value[k] !== undefined && typeof value[k] !== "function";
      }).map(function (k) {
        return JSON.stringify(k) + ":" + previzNormJson(value[k]);
      }).join(",") + "}";
    }
    if (typeof value === "number") return isFinite(value) ? String(Math.round(value * 1000) / 1000) : "null";
    return JSON.stringify(value === undefined ? null : value);
  }

  /* 고친 컷(장면 전체 timeline·인물 포함)의 정리한 JSON. */
  function previzEditSnapshot(scenes) {
    return previzNormJson(scenes || []);
  }

  /* 불러온 직후·저장 직후: 지금 장면을 기준으로 삼는다. */
  function previzMarkSaved(scenes) {
    previzSaved.scenes = scenes || null;
    previzSaved.snap = scenes ? previzEditSnapshot(scenes) : null;
    previzDirty = false;
  }

  /* previzDirty = 지금 장면 ≠ 기준. [저장] 아래 줄·녹화 막기·떠날 때 확인이 모두 이 값을 쓴다. */
  function previzRefreshDirty() {
    if (previzSaved.scenes) previzDirty = previzEditSnapshot(previzSaved.scenes) !== previzSaved.snap;
    return previzDirty;
  }

  /* 작업 201: 저장하지 않은 프리비즈 수정이 있으면 떠나기 전에 묻는다(단계 이동·창 닫기). 떠나도 되면 true. */
  function previzConfirmLeave() {
    if (!previzCtl || !previzRefreshDirty()) return true;
    if (!window.confirm(PREVIZ_LEAVE_TEXT)) return false;
    previzDirty = false;
    previzSaved.scenes = null;
    return true;
  }
  window.addEventListener("beforeunload", function (event) {
    if (previzCtl && previzRefreshDirty()) {
      event.preventDefault();
      event.returnValue = PREVIZ_LEAVE_TEXT;
    }
  });

  function destroyPreviz() {
    if (previzCtl) {
      try {
        previzCtl.destroy();
      } catch (error) {
        /* 무시 */
      }
      previzCtl = null;
    }
  }

  var POSE_LABELS = [
    ["stand", "서기"],
    ["sit", "앉기"],
    ["kneel", "무릎 꿇기"],
    ["walk", "걷기"],
    ["lie", "눕기"]
  ];
  var GESTURE_LABELS = [
    ["", "없음"],
    ["point", "가리키기"],
    ["bow", "숙이기"],
    ["hands_on_knees", "무릎에 손"],
    ["cover_face", "얼굴 가리기"],
    // 작업 200: 연기 동작 전부(previz_schema.GESTURES 와 같은 문자열). 설계가 붙인 동작이 "없음"으로 보이지 않게.
    ["hand_to_own_face", "자기 얼굴에 손"],
    ["wipe_tears", "눈물 닦기"],
    ["hand_on_other_cheek", "상대 뺨에 손"],
    ["both_hands_other_cheeks", "두 손으로 상대 얼굴"],
    ["hand_on_chest", "가슴에 손"],
    ["press_hand_to_other_chest", "상대 가슴에 손"],
    ["lean_in", "다가가기"],
    ["laugh", "웃기"],
    ["look_down", "내려다보기"],
    ["look_up", "올려다보기"],
    ["nod", "끄덕이기"],
    ["turn_head_to_other", "상대 쪽 보기"]
  ];
  // 상대(gestureTarget)가 필요한 동작.
  var GESTURES_WITH_TARGET = ["hand_on_other_cheek", "both_hands_other_cheeks", "press_hand_to_other_chest", "lean_in", "turn_head_to_other"];
  /* 작업 209a: 포즈 모드 관절 이름(vnew-previz.js RIG_JOINTS) → 패널 글. root = 골반(rig.rootY). */
  var PREVIZ_JOINT_KO = {
    head: "머리", neck: "목", spine: "가슴",
    shoulderL: "왼어깨", shoulderR: "오른어깨", elbowL: "왼팔꿈치", elbowR: "오른팔꿈치", wristL: "왼손목", wristR: "오른손목",
    hipL: "왼엉덩이", hipR: "오른엉덩이", kneeL: "왼무릎", kneeR: "오른무릎", ankleL: "왼발목", ankleR: "오른발목",
    root: "골반"
  };
  // 채널 박스 칸: [글, 값 순서(x 숙이기 · y 돌리기 · z 기울이기)]
  var PREVIZ_JOINT_AXES = [["돌리기", 1], ["숙이기", 0], ["기울이기", 2]];
  var PREVIZ_JOINT_STEP_DEG = 5;
  var PREVIZ_POSE_NOTE = "목록에서 고른 자세 위에 직접 바꾼 관절이 덮어씁니다";
  /* 작업 200: 카메라 범위·렌즈(previz_schema 와 같은 값). 렌즈는 풀프레임 환산 — 16:9 세로 화각 = 2·atan(10.125 / mm). */
  var PREVIZ_FOV_MIN = 8;
  var PREVIZ_FOV_MAX = 120;
  var PREVIZ_LENS_MIN = 6;
  var PREVIZ_LENS_MAX = 144;
  var PREVIZ_LENS_PRESETS = [18, 24, 35, 50, 85, 135];
  /* 작업 213: 키 줄 — 마름모를 이만큼(px, vnew-previz.js CLICK_SLOP_PX 와 같다) 넘게 끌면 스크러버 끌기 · 스크러버 손잡이 반폭(px). */
  var PREVIZ_KEY_CLICK_SLOP_PX = 4;
  var PREVIZ_SCRUB_THUMB_INSET_PX = 8;
  /* 작업 213: 관절 고르기 판(화면에서 마주 본 인물 — 인물의 오른쪽이 왼편). [관절, 줄, 칸, 글]. 손·발(IK) = 손목·발목. */
  var PREVIZ_JOINT_PICKER = [
    ["head", 1, 2, "머리"], ["neck", 2, 2, "목"],
    ["shoulderR", 3, 1, "오른어깨"], ["spine", 3, 2, "가슴"], ["shoulderL", 3, 3, "왼어깨"],
    ["elbowR", 4, 1, "오른팔꿈치"], ["elbowL", 4, 3, "왼팔꿈치"],
    ["wristR", 5, 1, "오른손 (IK)"], ["root", 5, 2, "골반"], ["wristL", 5, 3, "왼손 (IK)"],
    ["hipR", 6, 1, "오른엉덩이"], ["hipL", 6, 3, "왼엉덩이"],
    ["kneeR", 7, 1, "오른무릎"], ["kneeL", 7, 3, "왼무릎"],
    ["ankleR", 8, 1, "오른발 (IK)"], ["ankleL", 8, 3, "왼발 (IK)"]
  ];
  var PREVIZ_ROLL_MAX = 45;
  var PREVIZ_CAMERA_MARGIN_M = 3;
  var PREVIZ_ACTOR_WALL_M = 0.2;
  var PREVIZ_STALE_TEXT = "프리비즈를 다시 녹화해야 합니다";
  var PREVIZ_DIRTY_TEXT = "저장하지 않은 수정이 있습니다. [저장]을 누른 뒤 녹화해 주세요.";

  function previzFovFromLens(mm) {
    return Math.round(2 * Math.atan(10.125 / mm) * 180 / Math.PI * 10) / 10;
  }

  function previzLensFromFov(fov) {
    return Math.round(10.125 / Math.tan(fov * Math.PI / 360));
  }

  /* 녹화가 없거나 stale(장면을 고친 뒤 다시 녹화해야 함)인 구간. */
  function previzPendingSegments(previz) {
    var recs = (previz || {}).recordings || {};
    return ((previz || {}).segments || []).filter(function (seg) {
      var rec = recs[seg.id] || {};
      return rec.status !== "ready" || !!rec.stale;
    });
  }

  /* 승인 → 녹화. all 이면 모든 구간, 아니면 녹화가 없거나 stale 인 구간만(작업 200). 저장 안 한 수정이 있으면 멈춘다. */
  function previzApproveAndRecord(force, all, showError, onFail) {
    previzRefreshDirty();
    if (previzDirty) {
      showError(PREVIZ_DIRTY_TEXT);
      if (onFail) onFail();
      return Promise.resolve();
    }
    return api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/previz/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(force ? { force: true } : {})
    })
      .then(function (plan) {
        state.plan = plan;
        var pv = (state.plan || {}).previz || {};
        var segs = all ? pv.segments || [] : previzPendingSegments(pv);
        if (!segs.length) {
          loadPlan(false);
          return null;
        }
        return recordSegments(segs, showError);
      })
      .catch(function (err) {
        if (onFail) onFail();
        if (err && err.status === 409) {
          showError(errMessage(err) || "시점 일치도가 낮은 컷이 있습니다.");
        } else {
          showError("승인하지 못했습니다: " + errMessage(err));
        }
      });
  }

  /* 모든 장면 timeline 을 평탄화해 [{sceneIndex, cutIndex, cut, scene}] 로. 타임라인 눈금·패널에 쓴다. */
  function flattenCuts(scenes) {
    var flat = [];
    (scenes || []).forEach(function (scene, si) {
      (scene.timeline || []).forEach(function (cut, ci) {
        flat.push({ sceneIndex: si, cutIndex: ci, cut: cut, scene: scene });
      });
    });
    return flat;
  }

  /* 패널 분석 열거값 → 한국어 라벨(읽기 전용 표시용). */
  var SHOT_SIZE_LABELS = {
    extreme_close: "익스트림 클로즈업",
    close: "클로즈업",
    medium_close: "미디엄 클로즈업",
    medium: "미디엄",
    full: "풀샷",
    wide: "와이드"
  };
  var CAMERA_HEIGHT_LABELS = { low: "로우(아래에서)", eye: "눈높이", high: "하이(위에서)", top: "탑(수직)" };
  var SHOT_ANGLE_LABELS = {
    front: "정면",
    three_quarter_left: "좌 3/4",
    three_quarter_right: "우 3/4",
    side_left: "좌측면",
    side_right: "우측면",
    back: "뒷면",
    over_shoulder: "오버숄더"
  };

  /* 049 연출 문법 열거값 → 한국어 라벨(읽기 전용 표시용). previz_grammar 의 LABELS_KO 와 같은 뜻. */
  var GRAMMAR_SIZE_LABELS = {
    ECU: "익스트림 클로즈업", CU: "클로즈업", MCU: "바스트샷", MS: "웨이스트샷",
    MLS: "니샷", FS: "풀샷", LS: "롱샷"
  };
  var GRAMMAR_TYPE_LABELS = {
    clean_single: "클린 싱글", dirty_single: "더티 싱글", over_shoulder: "오버더숄더",
    two_shot: "투샷", profile_two_shot: "프로필 투샷", pov: "주관샷",
    insert: "인서트", establishing: "설정샷"
  };
  var GRAMMAR_VANTAGE_LABELS = {
    front: "정면", front3q: "살짝 돌린 정면", "3q": "3/4", side: "옆(프로필)",
    back3q: "뒤 3/4", back: "뒤"
  };
  var GRAMMAR_HEIGHT_LABELS = { low: "앙각", eye: "아이레벨", high: "부감", overhead: "버즈아이" };
  /* 샷 크기 → 렌즈(표시용, previz_camera.LENS 와 같은 뜻). */
  var GRAMMAR_LENS = {
    ECU: "85mm", CU: "70mm", MCU: "50mm", MS: "35mm", MLS: "24mm", FS: "24mm", LS: "18mm"
  };

  /* 시점 일치도(fit.match 0~1)를 배지로. match 가 작을수록 어긋남. 70%(approveMinMatch) 미만은 빨강.
     옛 데이터(fit.err 형식, match 없음)는 err 로 환산해 보여 준다(호환). */
  function fidelityBadge(fitEntry, minMatch) {
    var threshold = Number(minMatch);
    if (!(threshold > 0)) threshold = 0.7;
    var badge = el("span", "vnew-previz-fit-badge");
    if (!fitEntry) {
      badge.classList.add("vnew-previz-fit-none");
      badge.textContent = "일치도 —";
      return badge;
    }
    // 049: match 기준(grammar·manual·astra). match null 이면 판정 불가(낮은 컷으로 센다).
    if (Object.prototype.hasOwnProperty.call(fitEntry, "match")) {
      var m = fitEntry.match;
      if (m === null || m === undefined) {
        badge.classList.add("vnew-previz-fit-bad");
        badge.textContent = "일치도 판정 불가";
        badge.title = "이 컷은 웹툰 연출을 읽지 못해(패널 없음 등) 카메라를 자동 계산하지 못했습니다.";
        return badge;
      }
      var pctM = Math.max(0, Math.min(100, Math.round(Number(m) * 100)));
      var good = Number(m) >= threshold;
      badge.classList.add(good ? "vnew-previz-fit-good" : "vnew-previz-fit-bad");
      var methodKo = "연출 계산";
      if (fitEntry.method === "manual") methodKo = "사용자 조정";
      else if (fitEntry.method === "astra") methodKo = "자동";
      badge.textContent = "시점 일치도 " + pctM + "%" + (good ? "" : " · 낮음");
      badge.title = "카메라를 웹툰 연출 문법으로 계산했습니다(" + methodKo + "). 기준 " +
        Math.round(threshold * 100) + "% 이상이면 승인할 수 있습니다.";
      return badge;
    }
    // --- 옛 데이터(err 형식) 호환 ---
    var err = Number(fitEntry.err) || 0;
    var pct = Math.max(0, Math.min(100, Math.round((1 - err) * 100)));
    var label;
    if (fitEntry.method === "user") {
      badge.classList.add("vnew-previz-fit-user");
      badge.textContent = "사용자 지시 · 일치도 " + pct + "%";
      badge.title =
        "사용자 지시로 바뀐 컷이라 자동 보정을 하지 않았습니다. 웹툰 패널과의 오차 " + err.toFixed(3);
      return badge;
    }
    if (err <= 0.12) {
      badge.classList.add("vnew-previz-fit-good");
      label = "좋음";
    } else if (err <= 0.3) {
      badge.classList.add("vnew-previz-fit-ok");
      label = "보통";
    } else {
      badge.classList.add("vnew-previz-fit-bad");
      label = "다름";
    }
    badge.textContent = "시점 일치도 " + pct + "% · " + label;
    badge.title =
      "웹툰 패널과 카메라 투영의 오차 " + err.toFixed(3) +
      " (보정 방법: " + (fitEntry.method || "astra") + ")";
    return badge;
  }

  /* 컷 편집 패널 상단: 웹툰 패널 썸네일 + 시점 일치도 배지 + 연출 문법(한국어, 읽기 전용) + 배지. */
  function previzPanelMatchCard(cut, previz) {
    var card = el("div", "vnew-previz-edit-card vnew-previz-panel-match");
    var cutId = cut && cut.cutId;
    var fit = (previz && previz.fit) || {};
    var fitEntry = fit[cutId];
    var panelGrammar = (previz && previz.panelGrammar) || {};
    var overrides = (previz && previz.grammarOverrides) || {};
    var cached = panelGrammar[cutId];
    var grammar = cached && cached.grammar;
    var panelShots = (previz && previz.panelShots) || {};
    var shot = panelShots[cutId]; // 옛 데이터(045) 호환

    var head = el("div", "vnew-previz-actor-head");
    head.appendChild(el("strong", "vnew-previz-edit-head", "웹툰 패널"));
    head.appendChild(fidelityBadge(fitEntry, previz && previz.approveMinMatch));
    card.appendChild(head);

    // 썸네일(같은 출처 패널 API). 없으면 조용히 생략.
    if (cutId) {
      var thumb = el("img", "vnew-previz-panel-thumb");
      thumb.alt = "웹툰 패널";
      thumb.style.display = "none";
      var apiUrl =
        "/api/vnew/p/" + encodeURIComponent(state.projectId) +
        "/previz/panel?cutId=" + encodeURIComponent(cutId);
      previzTextureFetch(apiUrl).then(function (objectUrl) {
        if (objectUrl) {
          thumb.src = objectUrl;
          thumb.style.display = "";
          thumb.addEventListener("click", function () {
            openLightbox(objectUrl, "웹툰 패널");
          });
        }
      });
      card.appendChild(thumb);
    }

    if (grammar) {
      // 049: 연출 문법 한 줄 + 렌즈·거리(fit). 샷: 바스트샷 · 더티 싱글 · 각도: 3/4 · 높이: 아이레벨 …
      var size = grammar.shotSize || "MCU";
      var parts = [
        "샷: " + (GRAMMAR_SIZE_LABELS[size] || size),
        GRAMMAR_TYPE_LABELS[grammar.shotType] || grammar.shotType || "",
        "각도: " + (GRAMMAR_VANTAGE_LABELS[grammar.vantage] || grammar.vantage || "—"),
        "높이: " + (GRAMMAR_HEIGHT_LABELS[grammar.cameraHeight] || grammar.cameraHeight || "—")
      ];
      var lens = (fitEntry && fitEntry.lens) || GRAMMAR_LENS[size];
      if (lens) parts.push("렌즈 " + lens);
      if (fitEntry && fitEntry.distanceM != null) {
        parts.push("거리 " + (Number(fitEntry.distanceM)).toFixed(1) + "m");
      }
      var dl = el("div", "vnew-previz-panel-analysis");
      dl.appendChild(el("p", "vnew-previz-panel-line", parts.filter(Boolean).join(" · ")));
      if (grammar.mainSubject) {
        dl.appendChild(el("p", "vnew-previz-panel-subj", "· 주인공: " + grammar.mainSubject));
      }
      card.appendChild(dl);

      // 배지: 라인 넘음(crossed) · 배우 조정(cheats) · 사용자 수정(override)
      var badges = el("div", "vnew-previz-panel-badges");
      if (fitEntry && fitEntry.crossed) {
        badges.appendChild(grammarTag("라인 넘음", "vnew-previz-tag-crossed",
          "웹툰 감정 장면이라 액션 라인을 넘었습니다(의도된 연출)."));
      }
      (((fitEntry && fitEntry.cheats) || [])).forEach(function (c) {
        var name = cheatActorName(c, cut);
        if (c.type === "hide") {
          badges.appendChild(grammarTag("배우 조정: " + name + " 화면 밖", "vnew-previz-tag-cheat",
            "이 컷 구도에 맞춰 " + name + " 을(를) 화면에서 뺐습니다."));
        } else if (c.type === "move") {
          var dist = (Number(c.dist) || 0).toFixed(1);
          badges.appendChild(grammarTag("배우 조정: " + name + " " + dist + "m 이동", "vnew-previz-tag-cheat",
            "구도에 맞춰 " + name + " 을(를) 살짝 옮겼습니다."));
        }
      });
      if (fitEntry && (fitEntry.override || (overrides && overrides[cutId]))) {
        badges.appendChild(grammarTag("사용자 수정", "vnew-previz-tag-override",
          "사용자 '다시 만들기' 지시로 이 컷 연출을 바꿨습니다."));
      }
      if (badges.childNodes.length) card.appendChild(badges);
    } else if (shot) {
      // 옛 데이터(panelShots) — 기존 표시 그대로.
      var dl2 = el("div", "vnew-previz-panel-analysis");
      dl2.appendChild(
        el("p", "vnew-previz-panel-line",
          "샷: " + (SHOT_SIZE_LABELS[shot.shotSize] || shot.shotSize || "—") +
          " · 높이: " + (CAMERA_HEIGHT_LABELS[shot.cameraHeight] || shot.cameraHeight || "—") +
          " · 각도: " + (SHOT_ANGLE_LABELS[shot.angle] || shot.angle || "—"))
      );
      (shot.subjects || []).forEach(function (s) {
        dl2.appendChild(
          el("p", "vnew-previz-panel-subj",
            "· " + (s.name || "인물") +
            " (화면 " + Math.round((Number(s.screenX) || 0) * 100) + "%, " +
            Math.round((Number(s.screenY) || 0) * 100) + "%, 크기 " +
            Math.round((Number(s.heightFrac) || 0) * 100) + "%)")
        );
      });
      card.appendChild(dl2);
    } else {
      card.appendChild(el("p", "vnew-hint", "이 컷의 웹툰 연출을 읽지 못했습니다(패널 없음)."));
    }
    var collisionBadges = previzCollisionBadges(cut, previz);
    if (collisionBadges) card.appendChild(collisionBadges);
    var supportLines = previzSupportLines(cut, previz);
    if (supportLines) card.appendChild(supportLines);
    var fxLine = previzFxLine(cut);
    if (fxLine) card.appendChild(fxLine);
    return card;
  }

  /* 받침 판단(previz-support-reasoning R7). fit[cutId].support = [{name, seen, posture, supportKind, supportEn, contact, feet,
     heightM, propId, propTopM, heightFix?, status}] — 패널 그림에서 읽은 받침과 서버가 맞춘 소품 높이. 인물마다 한 줄.
     그림에 받침이 안 보인 항목(status hidden)은 보이지 않는다.
     fit.support 가 없으면(옛 계획·설정 끔) 아무것도 보이지 않는다. 글은 textContent(el)로만 넣는다.
     라벨 표는 previz_support.py 의 *_KO 와 같은 글자다(검사 sr-k). */
  var SUPPORT_POSTURE_KO = {
    stand: "서 있음", sit: "앉음", kneel: "무릎 꿇음", lie: "누움", crouch: "웅크림", lean: "기댐", walk: "걷기"
  };
  var SUPPORT_CONTACT_KO = {
    perch: "걸터앉기", seat: "의자처럼 앉기", straddle: "올라타기", lean: "기대기",
    kneel_on: "무릎 꿇기", lie_on: "눕기", stand_on: "올라서기", floor: "바닥"
  };
  var SUPPORT_FEET_KO = { floor: "바닥에 닿음", dangling: "띄움", on_prop: "받침에 올림", tucked: "접음" };
  var SUPPORT_KIND_KO = {
    floor: "바닥", chair: "의자", sofa: "소파", bench: "벤치", stool: "스툴", step: "디딤", stairs: "계단",
    rail: "난간", bollard: "주차봉", ledge: "턱", box: "상자", table: "탁자", counter: "카운터", vehicle: "차량",
    bed: "침대", wall: "벽", pole: "기둥", other: "물체"
  };

  /* 받침 한 줄 문구(순수 함수 — DOM 없음, 검사가 node 로 실행한다). 판단 불가(skipped)·그림에 안 보임(hidden)·빈 항목은 "". */
  function previzSupportText(entry) {
    if (!entry || typeof entry !== "object") return "";
    if (entry.status === "skipped" || entry.status === "hidden" || entry.seen === "hidden") return "";
    var name = String(entry.name || "인물");
    var posture = SUPPORT_POSTURE_KO[entry.posture] || "";
    var text;
    if (entry.supportKind === "floor" || entry.contact === "floor") {
      text = "받침: " + name + " — 바닥에 " + (posture || "있음");
    } else {
      var kind = SUPPORT_KIND_KO[entry.supportKind] || "";
      var en = String(entry.supportEn || "");
      var what = kind && en ? kind + "(" + en + ")" : (kind || en || "받침");
      var top = entry.propTopM != null ? Number(entry.propTopM) : (entry.heightM != null ? Number(entry.heightM) : null);
      var how = SUPPORT_CONTACT_KO[entry.contact] || posture;
      text = "받침: " + name + " — " + what;
      if (top != null && isFinite(top)) text += " 윗면 " + top.toFixed(2) + "m";
      if (how) text += (top != null && isFinite(top) ? "에 " : " ") + how;
    }
    if (SUPPORT_FEET_KO[entry.feet]) text += " · 발 " + SUPPORT_FEET_KO[entry.feet];
    var fix = entry.heightFix;
    if (fix && fix.fromTopM != null && fix.toTopM != null) {
      text += " · 소품 높이 " + Number(fix.fromTopM).toFixed(2) + "→" + Number(fix.toTopM).toFixed(2) + "m 맞춤";
    }
    if (entry.status === "noProp") text += " · 맞는 소품을 찾지 못함";
    return text;
  }

  function previzSupportLines(cut, previz) {
    var cutId = cut && cut.cutId;
    var fit = (previz && previz.fit) || {};
    var entries = cutId && fit[cutId] && fit[cutId].support;
    if (!entries || !entries.length) return null;
    var wrap = el("div", "vnew-previz-support");
    entries.forEach(function (entry) {
      var text = previzSupportText(entry);
      if (!text) return;
      var cls = "vnew-previz-support-line" + (entry.status === "noProp" ? " is-warn" : "");
      var line = el("p", cls, text);
      line.title = "패널 그림에서 읽은 받침 판단입니다. 서버가 이 값으로 앉을 소품과 높이를 맞췄습니다.";
      wrap.appendChild(line);
    });
    return wrap.childNodes.length ? wrap : null;
  }

  /* 소품 관통 보정(084 R1.8·R1.9·R1.11) 배지. previz.collision[cutId] = {fixes, unresolved, source}.
     source "view" 는 보정과 함께 설계된 장면을 화면에서 다시 맞춘 것(저장 안 됨). 옛 장면(보정 전에 설계)은
     사본을 바꾸지 않고 previz.collision.needsRedesign·cuts 로만 알린다. 승인은 막지 않는다. 글은 textContent 로만 넣는다. */
  var COLLISION_VIEW_TITLE = "화면에서만 보정했습니다. 다시 설계하면 저장됩니다.";
  var COLLISION_REDESIGN_TEXT = "이 프리비즈는 소품 관통 보정 전에 만들었습니다. '다시 설계'하면 인물이 소품 위에 제대로 앉고 카메라도 다시 맞춰집니다.";

  function previzCollisionBadges(cut, previz) {
    var cutId = cut && cut.cutId;
    var all = (previz && previz.collision) || {};
    var col = cutId ? all[cutId] : null;
    var wrap = el("div", "vnew-previz-collision");
    if (all.needsRedesign && cutId && (all.cuts || []).indexOf(cutId) >= 0) {
      wrap.appendChild(el("p", "vnew-hint vnew-previz-collision-redesign", COLLISION_REDESIGN_TEXT));
    }
    if (!col || typeof col !== "object" || !col.source) return wrap.childNodes.length ? wrap : null;
    var viewOnly = col.source === "view";
    var box = el("div", "vnew-previz-panel-badges");
    (col.fixes || []).forEach(function (fix) {
      var name = cheatActorName({ actor: fix.actor }, cut);
      var dist = (Number(fix.dist) || 0).toFixed(2);
      box.appendChild(grammarTag("소품 겹침 보정: " + name + " " + dist + "m", "vnew-previz-tag-collision",
        viewOnly ? COLLISION_VIEW_TITLE : "인물이 소품과 겹치지 않게 서버가 자리를 맞췄습니다(" + collisionPropLabel(fix.prop) + ")."));
    });
    (col.unresolved || []).forEach(function (item) {
      var name = cheatActorName({ actor: item.actor }, cut);
      box.appendChild(grammarTag("소품과 겹침을 풀지 못했습니다: " + name + " ↔ " + collisionPropLabel(item.prop),
        "vnew-previz-tag-collision is-warn",
        viewOnly ? COLLISION_VIEW_TITLE : "자리를 옮길 곳이 없어 그대로 두었습니다. 승인은 할 수 있습니다."));
    });
    if (box.childNodes.length) wrap.appendChild(box);
    return wrap.childNodes.length ? wrap : null;
  }

  /* 소품 id → 화면 이름(labelKo → labelEn → id). */
  function collisionPropLabel(propId) {
    var scenes = ((state.plan || {}).previz || {}).scenes || [];
    for (var si = 0; si < scenes.length; si++) {
      var props = scenes[si].props || [];
      for (var pi = 0; pi < props.length; pi++) {
        if (props[pi].id === propId) return props[pi].labelKo || props[pi].labelEn || propId;
      }
    }
    return propId || "소품";
  }

  /* 연출 문법 배지 한 조각(라인 넘음·배우 조정·사용자 수정). */
  function grammarTag(text, cls, title) {
    var tag = el("span", "vnew-previz-panel-tag " + (cls || ""), text);
    if (title) tag.title = title;
    return tag;
  }

  /* cheat 의 actor(id)를 그 컷 scene.actors 이름으로 바꾼다(없으면 id). */
  function cheatActorName(cheat, cut) {
    var id = cheat && cheat.actor;
    var scenes = ((state.plan || {}).previz || {}).scenes || [];
    for (var si = 0; si < scenes.length; si++) {
      var actors = scenes[si].actors || [];
      for (var ai = 0; ai < actors.length; ai++) {
        if (actors[ai].id === id) return actors[ai].name || id;
      }
    }
    return id || "인물";
  }

  /* 결과 위 '다시 만들기' 영역: 최근 지시 요약(lastRebuild) + 펼침형 지시 입력(textarea·대상 select).
     캐릭터 [다시 만들기]처럼 버튼을 누르면 입력창이 펼쳐지고, 지시·대상을 넣어 다시 설계한다.
     대상 select 는 전체 / 컷1…컷N(화면의 컷 번호). 제출은 POST /previz/design {extraPromptKo, cutId}. */
  function previzRebuildSection(previz, scenes, showError) {
    var section = el("section", "panel vnew-previz-rebuild");

    // 최근 지시(lastRebuild) 요약 한 줄 + 바뀐 연출(editsEn).
    var last = previz.lastRebuild;
    var lastEdits = (last && (last.editsEn || last.changesEn)) || [];
    if (last && (last.extraPromptKo || lastEdits.length)) {
      var summary = el("div", "vnew-previz-rebuild-last");
      if (last.extraPromptKo) {
        summary.appendChild(el("p", "vnew-previz-rebuild-last-line", "최근 지시: " + last.extraPromptKo));
      }
      if (lastEdits.length) {
        var ul = el("ul", "vnew-previz-rebuild-changes");
        lastEdits.forEach(function (c) {
          var label = (c.cutId && c.cutId !== "all") ? ("[" + c.cutId + "] ") : "[전체] ";
          var detail;
          if (c.fields) {
            // 049 editsEn: {cutId, fields:{field:value}}
            detail = Object.keys(c.fields).map(function (k) {
              return k + "=" + c.fields[k];
            }).join(", ");
          } else {
            detail = c.whatEn || ""; // 옛 047 형식
          }
          ul.appendChild(el("li", null, label + detail));
        });
        summary.appendChild(ul);
      }
      section.appendChild(summary);
    }

    // 펼침형 버튼.
    var btn = el("button", "outline-button vnew-previz-rebuild-btn", "다시 만들기");
    btn.type = "button";
    section.appendChild(btn);

    var panel = el("div", "vnew-rebuild-panel");
    panel.style.display = "none";

    panel.appendChild(el("label", "vnew-rebuild-label", "어떻게 다시 만들지 (선택)"));
    var textarea = el("textarea", "vnew-rebuild-text");
    textarea.rows = 3;
    textarea.maxLength = 1000;
    textarea.placeholder =
      "예) 컷1은 범건우 옆모습 클로즈업, 컷3은 위에서 내려다보게(부감), 두 사람은 난간에 나란히 앉아 있게";
    textarea.value = previz.extraPromptKo || "";
    panel.appendChild(textarea);

    // 대상 select: 전체 / 컷1…컷N(평탄화한 화면 컷 번호).
    var targetWrap = el("label", "vnew-field");
    targetWrap.appendChild(el("span", "vnew-field-label", "대상"));
    var sel = el("select", "vnew-field-input vnew-previz-rebuild-target");
    var allOpt = el("option", null, "전체");
    allOpt.value = "";
    sel.appendChild(allOpt);
    var flat = flattenCuts(scenes);
    flat.forEach(function (item, idx) {
      var cutId = item.cut && item.cut.cutId;
      if (!cutId) return;
      var opt = el("option", null, "컷 " + (idx + 1));
      opt.value = cutId;
      if ((previz.extraCutId || "") === cutId) opt.selected = true;
      sel.appendChild(opt);
    });
    targetWrap.appendChild(sel);
    panel.appendChild(targetWrap);

    panel.appendChild(
      el("p", "vnew-rebuild-cost", "다시 만들면 승인·녹화가 무효가 되고 비용이 듭니다.")
    );

    var panelBtns = el("div", "vnew-rebuild-btns");
    var confirmBtn = el("button", "primary-button", "이 지시로 다시 만들기");
    confirmBtn.type = "button";
    var cancelBtn = el("button", "outline-button", "취소");
    cancelBtn.type = "button";
    panelBtns.appendChild(confirmBtn);
    panelBtns.appendChild(cancelBtn);
    panel.appendChild(panelBtns);
    section.appendChild(panel);

    btn.addEventListener("click", function () {
      panel.style.display = panel.style.display === "none" ? "" : "none";
      if (panel.style.display === "") textarea.focus();
    });
    cancelBtn.addEventListener("click", function () {
      panel.style.display = "none";
    });
    confirmBtn.addEventListener("click", function () {
      confirmBtn.disabled = true;
      var body = { extraPromptKo: textarea.value || "", cutId: sel.value || "" };
      api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/previz/design", {
        method: "POST",
        body: JSON.stringify(body),
        headers: { "Content-Type": "application/json" }
      })
        .then(function () {
          loadPlan(false);
        })
        .catch(function (err) {
          confirmBtn.disabled = false;
          if (showError) {
            if (err && err.status === 409) {
              showError("이미 장면을 설계하고 있습니다.");
            } else {
              showError("다시 만들기를 시작하지 못했습니다: " + errMessage(err));
            }
          }
        });
    });

    return section;
  }

  function renderPrevizStep(host) {
    var plan = state.plan || {};
    var previz = plan.previz || {};
    var error = planError("");
    host.appendChild(error);
    function showError(message) {
      error.style.display = "";
      error.textContent = message;
    }

    // 뷰포트를 다시 그리므로 이전 컨트롤러는 정리한다.
    destroyPreviz();

    var status = previz.status || "none";
    var scenes = previz.scenes || [];

    // --- 설계 전 / 진행 중 ---
    if (status === "designing") {
      // 공통 진행 표시(단계·막대·경과 시간) + 취소. progress 가 없으면 옛 한 줄 띠로 대체.
      var progWrap = el("div", "vnew-progress-wrap");
      var ind = progressIndicator(previz.progress, previzProgressLabel, {
        determinateSteps: ["panels", "design", "camera"]
      });
      if (ind) {
        progWrap.appendChild(ind);
      } else {
        progWrap.appendChild(el("p", "vnew-progress-label", "3D 장면을 설계하고 있습니다…"));
      }
      var cancelBtn = el("button", "outline-button danger", "취소");
      cancelBtn.type = "button";
      cancelBtn.addEventListener("click", function () {
        cancelBtn.disabled = true;
        api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/cancel", { method: "POST" })
          .then(function () {
            loadPlan(false);
          })
          .catch(function (err) {
            cancelBtn.disabled = false;
            showError("취소하지 못했습니다: " + errMessage(err));
          });
      });
      progWrap.appendChild(cancelBtn);
      host.appendChild(progWrap);
      return;
    }
    if (status === "none" || !scenes.length) {
      if (status === "failed") {
        host.appendChild(planError(previz.error || "장면 설계에 실패했습니다."));
      }
      var intro = el("section", "panel vnew-previz-intro");
      intro.appendChild(el("h2", "vnew-sec-title", "프리비즈(3D)"));
      intro.appendChild(
        el(
          "p",
          "vnew-hint",
          "콘티를 바탕으로 간단한 3D 장면(방·빛·소품·마네킹)을 설계합니다. 이 마네킹 영상을 녹화해 " +
            "다음 단계에서 실사·웹툰으로 바꿉니다. 배치·카메라·인원이 그대로 유지됩니다."
        )
      );
      var designBtn = el("button", "primary-button", "3D 장면 설계하기");
      designBtn.type = "button";
      designBtn.addEventListener("click", function () {
        designBtn.disabled = true;
        api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/previz/design", { method: "POST" })
          .then(function () {
            loadPlan(false);
          })
          .catch(function (err) {
            designBtn.disabled = false;
            showError("장면 설계를 시작하지 못했습니다: " + errMessage(err));
          });
      });
      intro.appendChild(designBtn);
      host.appendChild(intro);
      return;
    }

    // --- 설계 후: 뷰포트 + 타임라인 + 장면 탭 + 우측 편집 패널 ---
    // 결과 위: 최근 지시(lastRebuild) 요약 + 지시를 넣는 [다시 만들기](펼침형).
    var posesStale = previzPosesStaleSection(previz, showError);
    if (posesStale) host.appendChild(posesStale);
    host.appendChild(previzRebuildSection(previz, scenes, showError));

    var layout = el("div", "vnew-previz-layout");

    // (좌) 뷰포트·타임라인·장면 탭
    var left = el("div", "vnew-previz-left");

    // 장면(장소) 전환 탭
    if (scenes.length > 1) {
      var tabs = el("div", "vnew-previz-scene-tabs");
      scenes.forEach(function (scene, si) {
        var tab = el("button", "vnew-previz-scene-tab", "장면 " + (si + 1));
        tab.type = "button";
        tab.addEventListener("click", function () {
          if (previzCtl) previzCtl.setScene(si);
        });
        tabs.appendChild(tab);
      });
      left.appendChild(tabs);
    }

    // 작업 201: 뷰포트 위 도구 줄(시점·도구·눈금·되돌리기) + 조준 줄(이 인물 보기·카메라를 여기로·렌즈) + 도움말 한 줄.
    var bar = previzEditorBar();
    left.appendChild(bar.root);

    // 16:9 뷰포트
    var viewport = el("div", "vnew-previz-viewport");
    left.appendChild(viewport);

    // 재생/일시정지 + 타임라인 바
    var transport = el("div", "vnew-previz-transport");
    var playBtn = el("button", "outline-button vnew-previz-play", "▶ 재생");
    playBtn.type = "button";
    transport.appendChild(playBtn);
    var timeLabel = el("span", "vnew-previz-time", "0.0s");
    transport.appendChild(timeLabel);
    left.appendChild(transport);

    // 작업 201: 이 컷 카메라의 시작/끝 키 + 미리 보기 스크러버. 끝 키가 없으면 카메라는 고정.
    var keys = previzKeyStrip();
    left.appendChild(keys.root);

    // 타임라인(컷 경계 눈금·컷 번호·대사 말풍선)
    var flat = flattenCuts(scenes);
    if (!flat[previzCutIndex]) previzCutIndex = 0; // 다시 설계로 컷 수가 줄었으면 처음 컷
    previzMarkSaved(scenes); // 새로 그린 장면은 서버 저장본이다(작업 207: 이것이 dirty 비교 기준)
    var timeline = el("div", "vnew-previz-timeline");
    left.appendChild(timeline);

    layout.appendChild(left);

    // (우) 편집 패널
    var right = el("aside", "vnew-previz-panel");
    layout.appendChild(right);

    host.appendChild(layout);

    // 승인·녹화 영역
    var recSection = el("section", "panel vnew-previz-record");
    host.appendChild(recSection);

    // --- 컨트롤러 생성 ---
    // 텍스처(벽·바닥)는 scene.room 안 textureUrl(= 서버 텍스처 API 경로)로 들어 있다. three.js 가
    // S3 를 직접 부르면(버킷 CORS 없음) 텍스처가 안 불러지거나 캔버스가 오염돼 녹화가 막히므로,
    // previzTextureFetch(Authorization 헤더 → blob → objectURL)로 같은 출처에서 받아 넘긴다.
    var duration = 0;
    var createPromise;
    try {
      // vnew-previz.js 는 app.js 가 동적 import 한다(073 R9). 로드가 끝난 뒤(window.vnewPrevizReady)
      // 뷰포트를 만든다. 로드 실패면 E7("3D 뷰어를 불러오지 못했습니다: <원인>")을 아래 .catch 로 보인다.
      createPromise = Promise.resolve(window.vnewPrevizReady).then(function () {
        if (!window.vnewPreviz || typeof window.vnewPreviz.create !== "function") {
          var cause = window.vnewPrevizError
            ? errMessage(window.vnewPrevizError)
            : "프리비즈 모듈(vnew-previz.js)이 로드되지 않았습니다.";
          var loadError = new Error("3D 뷰어를 불러오지 못했습니다: " + cause);
          loadError.previzLoad = true;
          throw loadError;
        }
        // 기다리는 사이 화면을 떠났으면 three.js 장면을 만들지 않는다.
        if (!viewport.isConnected) return null;
        return window.vnewPreviz.create(viewport, {
          scenes: scenes,
          textureFetch: previzTextureFetch,
          width: 1280,
          height: 720
        });
      });
    } catch (err) {
      showError("3D 뷰어를 열지 못했습니다: " + errMessage(err));
      return;
    }

    // create 는 텍스처를 받아 첫 프레임을 그린 뒤 controller 를 resolve 한다. 비동기 사이에 화면을
    // 떠났으면(뷰포트가 DOM 에서 빠졌으면) 결과를 버리고 그 controller 를 정리한다.
    createPromise
      .then(function (ctl) {
        if (!ctl) return;
        if (!viewport.isConnected) {
          try {
            ctl.destroy();
          } catch (error) {
            /* 무시 */
          }
          return;
        }
        previzCtl = ctl;
        duration = previzCtl.duration || 0;
        buildTimeline();
        wireEditor();
        // 작업 200: 다시 그린 뒤(저장 → 다시 불러오기 포함) 패널이 보던 컷의 시작을 그린다 — 저장한 값이 그 컷에 보인다.
        var focus = flat[previzCutIndex];
        if (focus) previzCtl.seek(Number(focus.cut.start) || 0);
        // ⑤ 의 [다시 녹화]로 왔으면 바로 다시 녹화한다(stale·녹화 없는 구간만).
        if (state.previzRerecord) {
          state.previzRerecord = false;
          previzApproveAndRecord(!!previz.approvedForced, false, showError);
        }
      })
      .catch(function (err) {
        // 모듈 로드 실패(E7)는 그 문구 그대로, 그 밖(텍스처·WebGL 등)은 기존 문구로 보인다.
        showError(err && err.previzLoad ? err.message : "3D 뷰어를 열지 못했습니다: " + errMessage(err));
      });

    // 타임라인 눈금: 컷마다 한 칸(길이 비례). 번호·대사 말풍선.
    function buildTimeline() {
      timeline.style.position = "relative";
      flat.forEach(function (item, idx) {
        var cut = item.cut;
        var start = Number(cut.start) || 0;
        var end = Number(cut.end) || start;
        var leftPct = duration > 0 ? (start / duration) * 100 : 0;
        var widthPct = duration > 0 ? ((end - start) / duration) * 100 : 0;
        var tick = el("button", "vnew-previz-tick");
        tick.type = "button";
        tick.style.left = leftPct.toFixed(2) + "%";
        tick.style.width = Math.max(1, widthPct).toFixed(2) + "%";
        tick.title = "컷 " + (idx + 1);
        tick.appendChild(el("span", "vnew-previz-tick-no", String(idx + 1)));
        // 대사 말풍선(이 컷의 cutId 로 콘티에서 찾는다)
        var line = contiDialogueFor(cut.cutId);
        if (line) {
          var bubble = el("span", "vnew-previz-tick-line", line);
          tick.appendChild(bubble);
        }
        tick.addEventListener("click", function () {
          if (previzCtl) {
            previzCtl.pause();
            previzCtl.seek(start);
          }
          previzCutIndex = idx;
          renderCutPanel();
        });
        timeline.appendChild(tick);
      });
      // 재생 위치 바늘
      var playhead = el("div", "vnew-previz-playhead");
      timeline.appendChild(playhead);
      previzCtl.onTime(function (t) {
        timeLabel.textContent = (Number(t) || 0).toFixed(1) + "s";
        var pct = duration > 0 ? (t / duration) * 100 : 0;
        playhead.style.left = pct.toFixed(2) + "%";
        playBtn.textContent = previzCtl.isPlaying ? "⏸ 일시정지" : "▶ 재생";
        // 현재 컷을 우측 패널과 맞춘다(재생 중 따라감).
        var activeIdx = flat.findIndex(function (it) {
          return t >= Number(it.cut.start) && t < Number(it.cut.end);
        });
        if (activeIdx >= 0 && activeIdx !== previzCutIndex && !isEditingPanel) {
          previzCutIndex = activeIdx;
          renderCutPanel();
        }
      });
    }

    playBtn.addEventListener("click", function () {
      if (!previzCtl) return;
      if (previzCtl.isPlaying) {
        previzCtl.pause();
        playBtn.textContent = "▶ 재생";
      } else {
        previzCtl.play();
        playBtn.textContent = "⏸ 일시정지";
      }
    });

    // --- 작업 201: 편집기 도구 줄·키 줄·연결 ---
    var selected = null; // 뷰포트에서 고른 것({type, id}) — 패널이 그것을 보인다

    function toolButton(parent, text, title, onClick) {
      var b = el("button", "outline-button vnew-previz-tool", text);
      b.type = "button";
      if (title) b.title = title;
      b.addEventListener("click", function () {
        if (previzCtl) onClick();
      });
      parent.appendChild(b);
      return b;
    }

    function previzEditorBar() {
      var root = el("div", "vnew-previz-editbar");
      var row = el("div", "vnew-previz-toolbar");
      var views = el("div", "vnew-previz-toolgroup");
      var workBtn = toolButton(views, "작업 시점", "자유롭게 둘러보는 시점(녹화되지 않습니다)", function () {
        previzCtl.setViewMode("work");
      });
      var shotBtn = toolButton(views, "카메라 시점", "이 컷 카메라로 봅니다 — 녹화되는 화면", function () {
        previzCtl.setViewMode("shot");
      });
      row.appendChild(views);
      var tools = el("div", "vnew-previz-toolgroup");
      var toolBtns = {
        select: toolButton(tools, "선택 (Q)", "눌러서 고르기", function () { previzCtl.setTool("select"); }),
        translate: toolButton(tools, "이동 (W)", "인물은 바닥에서, 카메라·바라보는 곳은 높이까지", function () { previzCtl.setTool("translate"); }),
        rotate: toolButton(tools, "회전 (E)", "인물 방향 / 카메라 좌우·위아래", function () { previzCtl.setTool("rotate"); })
      };
      toolButton(tools, "맞춰 보기 (F)", "F = 선택한 것에 맞춰 보기", function () { previzCtl.frameSelection(); });
      // 작업 209a: [포즈 (P)] — 고른 인물 하나의 관절을 직접 잡는다(Maya 식 고리·손발 네모·골반 네모).
      var poseBtn = toolButton(tools, "포즈 (P)", "고른 인물의 관절을 직접 잡습니다 — 고리 = 관절 돌리기 · 손·발 네모 = 끌어서 팔다리 굽히기 · 골반 네모 = 높이",
        function () {
          if (previzCtl.setPoseMode) previzCtl.setPoseMode(!previzCtl.poseMode);
        });
      poseBtn.classList.add("vnew-previz-pose-toggle");
      // 작업 209b: [키 찍기 (S)] — 고른 인물의 지금 자세를 키 스크러버 자리에 키로(Maya S). [잔상] — 키 자세 반투명 마네킹.
      var keyBtn = toolButton(tools, "키 찍기 (S)", "고른 인물의 지금 자리·방향·포즈를 이 시점에 키로 저장합니다(같은 시점이면 바꿉니다)",
        function () {
          if (previzCtl.setPoseKey) previzCtl.setPoseKey();
        });
      keyBtn.classList.add("vnew-previz-setkey");
      row.appendChild(tools);
      var ghostWrap = el("label", "vnew-previz-check vnew-previz-ghosts");
      ghostWrap.title = "키 자세마다 반투명한 잔상을 보입니다(녹화에는 나오지 않습니다) — 키가 2개 이상이면 저절로 켜집니다";
      var ghostBox = el("input");
      ghostBox.type = "checkbox";
      ghostBox.addEventListener("change", function () {
        if (previzCtl && previzCtl.setGhosts) previzCtl.setGhosts(ghostBox.checked);
      });
      ghostWrap.appendChild(ghostBox);
      ghostWrap.appendChild(el("span", null, "잔상"));
      row.appendChild(ghostWrap);
      var snapWrap = el("label", "vnew-previz-check vnew-previz-snap");
      var snap = el("input");
      snap.type = "checkbox";
      snap.addEventListener("change", function () {
        if (previzCtl) previzCtl.setSnap(snap.checked);
      });
      snapWrap.appendChild(snap);
      snapWrap.appendChild(el("span", null, "눈금 맞춤 (10cm / 15°)"));
      row.appendChild(snapWrap);
      // 작업 207: [이름표] — 장소·소품 이름표 보이기(인물 이름표는 늘 보인다). localStorage 에 기억.
      var labelsWrap = el("label", "vnew-previz-check vnew-previz-labels");
      labelsWrap.title = "장소·소품 이름표 보이기(인물 이름표는 늘 보입니다)";
      var labelsBox = el("input");
      labelsBox.type = "checkbox";
      labelsBox.checked = previzLabelsOn();
      labelsBox.addEventListener("change", function () {
        previzSetLabelsPref(labelsBox.checked);
        if (previzCtl && previzCtl.setLabels) previzCtl.setLabels(labelsBox.checked);
      });
      labelsWrap.appendChild(labelsBox);
      labelsWrap.appendChild(el("span", null, "이름표"));
      row.appendChild(labelsWrap);
      var hist = el("div", "vnew-previz-toolgroup");
      var undoBtn = toolButton(hist, "↶ 되돌리기", "Ctrl+Z", function () { previzCtl.undo(); });
      var redoBtn = toolButton(hist, "↷ 다시 하기", "Ctrl+Shift+Z", function () { previzCtl.redo(); });
      row.appendChild(hist);
      root.appendChild(row);

      var aimRow = el("div", "vnew-previz-toolbar vnew-previz-aimbar");
      var aimBtn = toolButton(aimRow, "이 인물 보기", "카메라를 고른 뒤 인물을 누르면 그 머리 높이를 바라봅니다", function () {
        var on = !previzCtl.state().aim;
        if (on) previzCtl.select({ type: "camera" });
        previzCtl.setAimMode(on);
      });
      var hereBtn = toolButton(aimRow, "카메라를 여기로", "작업 시점의 지금 자리·방향으로 촬영 카메라를 옮깁니다", function () {
        previzCtl.placeCameraHere();
      });
      var lensBtns = {};
      PREVIZ_LENS_PRESETS.forEach(function (mm) {
        lensBtns[mm] = toolButton(aimRow, mm + "mm", "렌즈 " + mm + "mm", function () { previzCtl.setLens(mm); });
        lensBtns[mm].classList.add("vnew-previz-lens");
      });
      var info = el("span", "vnew-previz-caminfo", "");
      aimRow.appendChild(info);
      root.appendChild(aimRow);
      root.appendChild(el("p", "vnew-hint vnew-previz-help", "Q 선택 · W 이동 · E 회전 · F 맞춰 보기 · 휠 확대 · Shift+드래그 이동 · Ctrl+Z 되돌리기" +
        " · 인물 끌어 옮기기 · 방향키 0.1m(Shift 0.5m) · [ ] 15° · P 포즈"));

      function sync(st) {
        if (!previzCtl || !st) return;
        workBtn.classList.toggle("is-on", st.view === "work");
        shotBtn.classList.toggle("is-on", st.view === "shot");
        Object.keys(toolBtns).forEach(function (k) {
          toolBtns[k].classList.toggle("is-on", st.tool === k);
        });
        snap.checked = !!st.snap;
        undoBtn.disabled = !st.canUndo;
        redoBtn.disabled = !st.canRedo;
        aimBtn.classList.toggle("is-on", !!st.aim);
        poseBtn.classList.toggle("is-on", !!st.pose);
        poseBtn.disabled = !(st.selection && st.selection.type === "actor");
        keyBtn.disabled = !(st.selection && st.selection.type === "actor");
        ghostBox.checked = !!(st.poseKeys && st.poseKeys.ghosts);
        ghostBox.disabled = !(st.poseKeys && st.poseKeys.keys.length);
        hereBtn.disabled = st.view !== "work";
        var ci = previzCtl.cameraInfo();
        Object.keys(lensBtns).forEach(function (mm) {
          lensBtns[mm].classList.toggle("is-on", ci.lensSet && Number(ci.lensMm) === Number(mm));
        });
        info.textContent = st.aim
          ? "인물을 누르세요(Esc 취소)"
          : st.pose
            ? "포즈: 고리 끌기 = 가로 돌리기 · 세로 숙이기 · Shift 기울이기 · 손발 네모 끌기(Alt 앞뒤) · 골반 네모 = 높이 · Tab 다음 관절 · Esc 끝"
            : "렌즈 " + ci.lensMm + "mm · 화각 " + ci.fov + "° · 카메라 높이 " + ci.heightM.toFixed(2) + "m";
        info.title = info.textContent; // 작업 213: 한 줄 칸(길면 … — 전체 글은 툴팁)
      }
      return { root: root, sync: sync };
    }

    function previzKeyStrip() {
      var root = el("div", "vnew-previz-keys");
      root.appendChild(el("strong", "vnew-previz-keys-head", "카메라 키"));
      var startBtn = toolButton(root, "시작 키", "지금 보이는 카메라를 이 컷의 시작 자세로", function () { previzCtl.setKey("start"); });
      var endBtn = toolButton(root, "끝 키", "지금 보이는 카메라를 이 컷의 끝 자세로(시작 → 끝으로 움직입니다)", function () { previzCtl.setKey("end"); });
      var clearBtn = toolButton(root, "끝 키 지우기", "카메라를 고정합니다", function () { previzCtl.clearEndKey(); });
      var scrub = el("input", "vnew-previz-keys-scrub");
      scrub.type = "range";
      scrub.min = "0";
      scrub.max = "1";
      scrub.step = "0.01";
      scrub.value = "0";
      scrub.title = "이 컷 안에서 미리 보기";
      scrub.addEventListener("input", function () {
        if (previzCtl) previzCtl.scrubKey(parseFloat(scrub.value) || 0);
      });
      // 작업 209b: 스크러버 위 마름모 — 카메라 키(파랑, 시작·끝)와 고른 인물의 자세 키(주황). 누르면 그 키로 간다.
      // 작업 213: 마름모는 트랙 바로 위 얇은 줄(스크러버 손잡이를 가리지 않는다). 마름모를 누른 채 움직이면 스크러버를 끈다.
      var scrubWrap = el("div", "vnew-previz-keys-track");
      var diamonds = el("div", "vnew-previz-keys-diamonds");
      scrubWrap.appendChild(diamonds);
      scrubWrap.appendChild(scrub);
      root.appendChild(scrubWrap);
      // 작업 213: 키 줄 Home / End = 컷 시작 / 끝.
      root.addEventListener("keydown", function (event) {
        var k = event && event.key;
        if ((k !== "Home" && k !== "End") || !previzCtl) return;
        var t = event.target;
        if (t && t !== scrub && /^(INPUT|TEXTAREA|SELECT)$/.test(String(t.tagName || "").toUpperCase())) return;
        if (event.preventDefault) event.preventDefault();
        previzCtl.scrubKey(k === "Home" ? 0 : 1);
        scrub.value = k === "Home" ? "0" : "1";
      });
      var note = el("span", "vnew-hint vnew-previz-keys-note", "");
      root.appendChild(note);
      // 작업 209b: 자세 키 줄 — [◀ 키](,) [키 찍기 (S)] [키 ▶](.) [키 지우기] · 안내(키 사이에서 고치면 S 로 저장).
      var poseRow = el("div", "vnew-previz-posekeys");
      poseRow.appendChild(el("strong", "vnew-previz-keys-head", "자세 키"));
      var prevKeyBtn = toolButton(poseRow, "◀ 키 (,)", "앞 자세 키로", function () { previzCtl.gotoPoseKey(-1); });
      var nextKeyBtn = toolButton(poseRow, "키 ▶ (.)", "뒤 자세 키로", function () { previzCtl.gotoPoseKey(1); });
      var delKeyBtn = toolButton(poseRow, "키 지우기", "고른 마름모(지금 시점의 키)를 지웁니다 · Delete", function () { previzCtl.deletePoseKey(); });
      var poseHint = el("span", "vnew-hint vnew-previz-posekeys-hint", "");
      poseRow.appendChild(poseHint);
      root.appendChild(poseRow);
      void startBtn;
      void endBtn;
      // 작업 213: 스크러버 위 x → u(손잡이 반폭만큼 안쪽 — 마름모 줄도 같은 안쪽에서 그린다).
      function scrubUAt(clientX) {
        var r = scrub.getBoundingClientRect();
        var w = Math.max(1, r.width - 2 * PREVIZ_SCRUB_THUMB_INSET_PX);
        return Math.max(0, Math.min(1, (clientX - r.left - PREVIZ_SCRUB_THUMB_INSET_PX) / w));
      }
      // 작업 213: 마름모 누름 — 움직이지 않고 떼면 그 키로(onClick), CLICK_SLOP 넘게 움직이면 그 자리부터 스크러버 끌기.
      // sync 가 끄는 동안 마름모를 다시 그리므로 움직임·뗌은 window 에서 받는다.
      var diamondDrag = null;
      function endDiamondDrag() {
        diamondDrag = null;
        window.removeEventListener("pointermove", onDiamondMove);
        window.removeEventListener("pointerup", onDiamondUp);
        window.removeEventListener("pointercancel", endDiamondDrag);
      }
      function onDiamondMove(event) {
        var d = diamondDrag;
        if (!d) return;
        if (!d.moved && Math.abs(event.clientX - d.x) + Math.abs(event.clientY - d.y) < PREVIZ_KEY_CLICK_SLOP_PX) return;
        d.moved = true;
        if (event.preventDefault) event.preventDefault();
        var u = scrubUAt(event.clientX);
        scrub.value = String(u);
        if (previzCtl) previzCtl.scrubKey(u);
      }
      function onDiamondUp() {
        var d = diamondDrag;
        endDiamondDrag();
        if (d && !d.moved && previzCtl) d.onClick();
      }
      function diamond(u, kind, title, onClick, selected) {
        var d = el("button", "vnew-previz-key-diamond is-" + kind + (selected ? " is-selected" : ""), "");
        d.type = "button";
        d.title = title + " · 누른 채 끌면 스크러버";
        d.style.left = (Math.max(0, Math.min(1, u)) * 100) + "%";
        d.addEventListener("pointerdown", function (event) {
          if (event.button !== undefined && event.button !== 0) return;
          if (event.preventDefault) event.preventDefault();
          if (event.stopPropagation) event.stopPropagation();
          endDiamondDrag();
          diamondDrag = { x: event.clientX, y: event.clientY, moved: false, onClick: onClick };
          window.addEventListener("pointermove", onDiamondMove);
          window.addEventListener("pointerup", onDiamondUp);
          window.addEventListener("pointercancel", endDiamondDrag);
        });
        d.addEventListener("click", function (event) {
          if (event && event.stopPropagation) event.stopPropagation();
          // 마우스 누름은 pointerdown/up 이 다룬다. 여기는 키보드(Enter·Space, detail 0)만.
          if (event && event.detail) return;
          if (previzCtl) onClick();
        });
        diamonds.appendChild(d);
        return d;
      }
      function sync() {
        if (!previzCtl) return;
        var ci = previzCtl.cameraInfo();
        clearBtn.disabled = !ci.hasEndKey;
        if (document.activeElement !== scrub) scrub.value = String(ci.keyU);
        note.textContent = ci.hasEndKey
          ? (ci.editingEnd ? "끝 키를 고치는 중" : "시작 키를 고치는 중") + " · 시작 → 끝으로 부드럽게 움직입니다"
          : "끝 키가 없으면 카메라는 고정입니다";
        clear(diamonds);
        diamond(0, "camera", "카메라 시작 키", function () { previzCtl.scrubKey(0); }, false);
        if (ci.hasEndKey) diamond(1, "camera", "카메라 끝 키", function () { previzCtl.scrubKey(1); }, false);
        var pk = previzCtl.poseKeyInfo ? previzCtl.poseKeyInfo() : null;
        (pk ? pk.keys : []).forEach(function (u) {
          diamond(u, "pose", "자세 키 " + Math.round(u * 100) + "% — 누르면 이 키로(Delete = 지우기)", function () {
            previzCtl.jumpToPoseKey(pk.id, u);
          }, pk.selected !== null && Math.abs(pk.selected - u) < 0.0005);
        });
        var count = pk ? pk.keys.length : 0;
        prevKeyBtn.disabled = !count;
        nextKeyBtn.disabled = !count;
        delKeyBtn.disabled = !(pk && (pk.onKey || pk.selected !== null));
        poseRow.classList.toggle("is-live", !!(pk && pk.live));
        poseHint.textContent = !pk
          ? "인물을 고르면 자세 키를 찍을 수 있습니다"
          : pk.msg
            ? pk.msg
            : pk.hint
              ? pk.hint + (pk.live ? " (지금은 미리보기 — 시점을 옮기면 사라집니다)" : "")
              : count
                ? "키 " + count + "/" + pk.max + " · 이 키를 고치는 중"
                : "키가 없으면 이 컷 내내 같은 자세입니다 · 키 찍기(S)로 움직임을 만듭니다";
        poseHint.title = poseHint.textContent; // 작업 213: 한 줄 칸(길면 … — 전체 글은 툴팁)
        note.title = note.textContent;
      }
      return { root: root, sync: sync };
    }

    var panelCommitTimer = 0;
    function schedulePanelCommit(cut) {
      if (panelCommitTimer) window.clearTimeout(panelCommitTimer);
      panelCommitTimer = window.setTimeout(function () {
        panelCommitTimer = 0;
        if (previzCtl && previzCtl.commitEdit) previzCtl.commitEdit(cut);
      }, 600);
    }

    function markDirty() {
      // 작업 207: 고친 뒤 기준과 다시 같아졌으면(되돌리기·같은 값) 줄을 숨긴다.
      previzRefreshDirty();
      if (dirtyNote) dirtyNote.style.display = previzDirty ? "" : "none";
    }

    function focusCutOf(cut) {
      var idx = flat.findIndex(function (it) { return it.cut === cut; });
      if (idx >= 0 && idx !== previzCutIndex) {
        previzCutIndex = idx;
        return true;
      }
      return false;
    }

    /* 뷰포트 편집기 ↔ 패널: 고르면 패널이 그것으로 넘어가고, 끄는 동안 숫자가 따라 바뀌고, 놓으면(한 단계) 패널을 다시 그린다. */
    function wireEditor() {
      if (!previzCtl.onState) return;
      // 작업 207: 컨트롤러가 장면을 만들며 채운 기본값이 "수정"으로 보이지 않게 — 아직 고치지 않았으면 기준을 다시 잡는다.
      if (!previzDirty) previzMarkSaved(scenes);
      if (previzCtl.setLabels) previzCtl.setLabels(previzLabelsOn());
      previzCtl.onState(function (st) {
        bar.sync(st);
        keys.sync();
        // 작업 209b: 자세 키 있는 인물의 키 시점이 바뀌면(마름모 · , / . · 스크러버) 방향·위치 칸이 그 키를 보게 패널을 다시 그린다.
        if (st && st.poseKeys && st.poseKeys.keys.length && previzKeyMark() !== panelKeyMark) {
          renderCutPanel();
          return;
        }
        fieldRefreshers.forEach(function (fn) { fn(); });
      });
      previzCtl.onSelect(function (sel) {
        selected = sel;
        renderCutPanel();
        var card = right.querySelector(".vnew-previz-edit-card.is-selected");
        // 작업 213: scrollIntoView 는 페이지까지 굴려 키 줄·스크러버가 움직였다 — 패널 안만 굴린다.
        if (card) previzScrollPanelTo(right, card);
      });
      // 작업 209a: 포즈 모드를 켜고 끄거나 관절을 고르면 채널 박스를 다시 그린다.
      if (previzCtl.onPose) {
        previzCtl.onPose(function () {
          renderCutPanel();
        });
      }
      previzCtl.onEdit(function (ev) {
        markDirty();
        var moved = ev && ev.cut ? focusCutOf(ev.cut) : false;
        if (ev && ev.kind === "live" && !moved) {
          fieldRefreshers.forEach(function (fn) { fn(); });
        } else {
          renderCutPanel();
        }
        bar.sync(previzCtl.state());
        keys.sync();
      });
      bar.sync(previzCtl.state());
      keys.sync();
    }

    // --- 우측 편집 패널: 현재 컷의 카메라·인물 ---
    var isEditingPanel = false;
    var dirtyNote = null; // 작업 200: [저장] 아래 "저장하지 않은 수정" 줄

    function renderCutPanel() {
      clear(right);
      var item = flat[previzCutIndex];
      if (!item) {
        right.appendChild(el("p", "vnew-hint", "컷을 고르세요."));
        return;
      }
      var scene = item.scene;
      var cut = item.cut;
      var room = scene.room || {};
      fieldRefreshers = [];
      right.appendChild(el("h3", "vnew-previz-panel-title", "컷 " + (previzCutIndex + 1) + " 편집"));

      // 작업 201: 목록(아웃라이너) — 누르면 뷰포트에서도 고른다. 패널은 고른 것의 값(채널 박스)만 보인다.
      var actorsMeta = {};
      (scene.actors || []).forEach(function (a) {
        actorsMeta[a.id] = a;
      });
      var selActor = selected && selected.type === "actor" ? (cut.actors || []).filter(function (ca) { return ca.id === selected.id; })[0] : null;
      var outliner = el("div", "vnew-previz-outliner");
      function outlinerItem(text, sel, on, color) {
        var b = el("button", "vnew-previz-outliner-item" + (on ? " is-on" : ""));
        b.type = "button";
        if (color) {
          var dot = el("i", "vnew-previz-dot");
          dot.style.background = color;
          b.appendChild(dot);
        }
        b.appendChild(el("span", null, text));
        b.addEventListener("click", function () {
          if (previzCtl && previzCtl.select) previzCtl.select(sel);
          else {
            selected = sel;
            renderCutPanel();
          }
        });
        outliner.appendChild(b);
      }
      outlinerItem("카메라", { type: "camera" }, !selActor && (!selected || selected.type === "camera"));
      outlinerItem("바라보는 곳", { type: "target" }, !!(selected && selected.type === "target"));
      (cut.actors || []).forEach(function (ca) {
        var meta = actorsMeta[ca.id] || {};
        outlinerItem(meta.name || ca.id, { type: "actor", id: ca.id }, selActor === ca, meta.color || "#ffffff");
      });
      right.appendChild(outliner);

      // 작업 200: 직접 놓은 값 때문에 생긴 문제(옮기지 않고 알리기만 한다) — 저장한 값 기준(서버 보기).
      var warnList = cutWarnings(cut);
      if (warnList.length) {
        var warnBox = el("div", "vnew-previz-manual-warn");
        warnList.forEach(function (text) {
          warnBox.appendChild(el("p", null, text));
        });
        right.appendChild(warnBox);
      }

      // 카메라(카메라·바라보는 곳을 골랐거나 아무것도 안 골랐을 때)
      var camCard = el("div", "vnew-previz-edit-card" + (selActor ? "" : " is-selected"));
      var camHead = el("div", "vnew-previz-actor-head");
      camHead.appendChild(el("strong", "vnew-previz-edit-head", "카메라"));
      var cam = cut.camera || (cut.camera = { pos: [0, 1.5, 4], target: [0, 1.2, 0], fov: 50 });
      camHead.appendChild(manualTag(cam, "카메라", function () {
        delete cam.manual;
      }));
      camCard.appendChild(camHead);
      var camNote = el("p", "vnew-hint vnew-previz-limit-note");
      var camBounds = cameraBounds(room);
      // 작업 201: 끝 키를 고치는 중이면 위치·바라보는 곳은 끝 키(moveTo) 값이다.
      var editingEnd = !!(cam.moveTo && previzCtl && previzCtl.cameraInfo && previzCtl.cameraInfo().editingEnd);
      var keyObj = editingEnd ? cam.moveTo : cam;
      var keyLabel = editingEnd ? " · 끝 키" : "";
      camCard.appendChild(vec3Field("위치 (x,y,z)" + keyLabel, keyObj, "pos", camBounds, camNote, scene, cut, function (axis, delta) {
        cam.manual = true;
        if (!editingEnd) shiftMove(cam, "pos", axis, delta);
      }));
      camCard.appendChild(vec3Field("바라보는 곳 (x,y,z)" + keyLabel, keyObj, "target", camBounds, camNote, scene, cut, function (axis, delta) {
        cam.manual = true;
        if (!editingEnd) shiftMove(cam, "target", axis, delta);
      }));
      // 바라보는 곳을 인물 머리 높이로 한 번에(작업 200) — 계산은 뷰포트 편집기(lookAtActor) 하나로.
      var lookRow = el("div", "vnew-previz-look-row");
      (cut.actors || []).forEach(function (ca) {
        var meta = actorsMeta[ca.id] || {};
        var btn = el("button", "outline-button vnew-previz-look", (meta.name || ca.id) + " 보기");
        btn.type = "button";
        btn.addEventListener("click", function () {
          if (!previzCtl || !previzCtl.lookAtActor) return;
          if (previzCtl.editCut() !== cut) previzCtl.seek(Number(cut.start) || 0);
          previzCtl.lookAtActor(ca.id);
        });
        lookRow.appendChild(btn);
      });
      if (lookRow.childNodes.length) camCard.appendChild(lookRow);
      var fovWrap = numField("화각(fov, 도)", cam, "fov", PREVIZ_FOV_MIN, PREVIZ_FOV_MAX, camNote, scene, cut, function () {
        cam.manual = true;
        delete cam.lensMm; // 화각을 직접 고치면 렌즈 값은 뗀다(렌즈는 화각에서 다시 보인다)
        lensInput.value = "";
        lensInput.placeholder = String(previzLensFromFov(Number(cam.fov) || 50));
      });
      var fovInput = fovWrap.querySelector("input");
      if (fovInput) fovInput.step = "0.1";
      camCard.appendChild(fovWrap);
      // 렌즈(mm, 풀프레임 환산) + 프리셋
      var lensWrap = el("label", "vnew-field");
      lensWrap.appendChild(el("span", "vnew-field-label", "렌즈(mm, 풀프레임 환산)"));
      var lensInput = el("input", "vnew-field-input");
      lensInput.type = "number";
      lensInput.min = String(PREVIZ_LENS_MIN);
      lensInput.max = String(PREVIZ_LENS_MAX);
      lensInput.value = cam.lensMm ? String(cam.lensMm) : "";
      lensInput.placeholder = String(previzLensFromFov(Number(cam.fov) || 50));
      lensInput.addEventListener("input", function () {
        var mm = parseFloat(lensInput.value);
        if (!isFinite(mm) || mm <= 0) return;
        setLens(Math.max(PREVIZ_LENS_MIN, Math.min(PREVIZ_LENS_MAX, mm)), mm);
      });
      lensWrap.appendChild(lensInput);
      camCard.appendChild(lensWrap);
      // 렌즈 프리셋(18~135mm)은 작업 201부터 뷰포트 위 조준 줄에 있다(같은 lensMm).
      function setLens(mm, asked) {
        cam.lensMm = mm;
        cam.fov = previzFovFromLens(mm);
        cam.manual = true;
        if (fovInput) fovInput.value = String(cam.fov);
        camNote.textContent = mm !== asked ? "렌즈는 " + PREVIZ_LENS_MIN + "~" + PREVIZ_LENS_MAX + "mm 라 " + mm + "mm 로 맞췄습니다." : "";
        applyEdit(scene, cut);
      }
      camCard.appendChild(numField("기울기(roll, 도)", cam, "roll", -PREVIZ_ROLL_MAX, PREVIZ_ROLL_MAX, camNote, scene, cut, function () {
        cam.manual = true;
      }));
      if (cam.moveTo) {
        // 카메라 이동(끝 키)이 있는 컷: 고정은 아래 [끝 키 지우기].
        camCard.appendChild(el("p", "vnew-hint", editingEnd
          ? "끝 키 값을 고치는 중입니다(키 줄 스크러버를 앞쪽으로 옮기면 시작 키)."
          : "이 컷은 카메라가 움직입니다(위치를 고치면 끝 위치도 같이 옮깁니다)."));
      }
      camCard.appendChild(camNote);
      if (!selActor) right.appendChild(camCard);

      // 인물(고른 인물 하나 — 색 칩·이름·pose·위치)
      (cut.actors || []).forEach(function (ca) {
        if (ca !== selActor) return;
        var meta = actorsMeta[ca.id] || {};
        var card = el("div", "vnew-previz-edit-card is-selected");
        var headRow = el("div", "vnew-previz-actor-head");
        var chip = el("i", "vnew-previz-dot");
        chip.style.background = meta.color || "#ffffff";
        headRow.appendChild(chip);
        headRow.appendChild(el("strong", "vnew-previz-edit-head", meta.name || ca.id));
        headRow.appendChild(manualTag(ca, meta.name || ca.id, function () {
          delete ca.manual;
        }));
        card.appendChild(headRow);
        var actorNote = el("p", "vnew-hint vnew-previz-limit-note");
        // 작업 209b: 자세 키가 있는 인물의 방향·위치 칸은 지금 키(키 위) 또는 저장 전 미리보기(키 사이)를 고친다.
        var poseObj = previzCtl && previzCtl.poseFieldTarget ? previzCtl.poseFieldTarget(ca.id, cut) || ca : ca;
        panelKeyMark = previzKeyMark();
        card.appendChild(selectField("자세", ca, "pose", POSE_LABELS, scene, cut, function () {
          ca.manual = true;
          delete ca.posture; // 패널에서 읽은 자세 기울기보다 고른 자세가 먼저
          if (ca.pose !== "sit") {
            delete ca.sit;
            delete ca.sitOn;
          }
        }));
        card.appendChild(selectField("동작", ca, "gesture", GESTURE_LABELS, scene, cut, function () {
          ca.manual = true;
          // 고른 동작이 보이게 패널에서 읽은 손·머리 방향을 뗀다(렌더러가 손·머리를 동작 위에 덮는다).
          delete ca.hands;
          delete ca.head;
          if (GESTURES_WITH_TARGET.indexOf(ca.gesture) >= 0 && !ca.gestureTarget) {
            var other = (cut.actors || []).filter(function (o) { return o.id !== ca.id; })[0];
            if (other) ca.gestureTarget = other.id;
          }
        }));
        card.appendChild(numField("방향(도)", poseObj, "facingDeg", -360, 360, actorNote, scene, cut, function () {
          if (poseObj !== ca) {
            previzCtl.poseFieldChanged(ca.id, cut); // 작업 209b: 키 위 = 그 키 · 키 사이 = 미리보기
            return;
          }
          ca.manual = true;
          // 앉은 인물은 다리 방향(sit.legYawDeg)도 같이 돌려 미리 본다(저장하면 서버가 앉은 모양을 다시 잡는다).
          if (ca.sit && typeof ca.sit === "object") ca.sit.legYawDeg = Number(ca.facingDeg) || 0;
        }));
        card.appendChild(vec3Field("위치 (x,z)", poseObj, "pos", actorBounds(room), actorNote, scene, cut, function () {
          if (poseObj !== ca) previzCtl.poseFieldChanged(ca.id, cut);
          else ca.manual = true;
        }, [0, 2]));
        var speakWrap = el("label", "vnew-previz-check");
        var speak = el("input");
        speak.type = "checkbox";
        speak.checked = !!ca.speaking;
        speak.addEventListener("change", function () {
          ca.speaking = speak.checked;
          ca.manual = true;
          applyEdit(scene, cut);
        });
        speakWrap.appendChild(speak);
        speakWrap.appendChild(el("span", null, "말하는 중(머리 끄덕임)"));
        card.appendChild(speakWrap);
        card.appendChild(previzPoseSection(scene, cut, ca));
        card.appendChild(actorNote);
        right.appendChild(card);
      });

      // 웹툰 패널 썸네일 + 시점 일치도 배지 + 패널 분석(읽기 전용) — 작업 201: 채널 박스 아래로.
      right.appendChild(previzPanelMatchCard(cut, previz));

      // 저장 버튼
      var saveBtn = el("button", "primary-button vnew-previz-save", "저장");
      saveBtn.type = "button";
      saveBtn.addEventListener("click", function () {
        saveBtn.disabled = true;
        // 작업 209a: 인물이 직접 잡은 관절(cut actor 의 rig)도 이 scenes 안에 그대로 실린다(따로 고르지 않는다).
        // 작업 209b: 자세 키(cut actor 의 keys = [{u, pos, facingDeg, rig}])도 rig 옆에 같은 scenes 로 실린다(dirty 비교도 같은 scenes).
        api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/previz/scene", {
          method: "PUT",
          body: JSON.stringify({ scenes: scenes })
        })
          .then(function (plan) {
            // 작업 200: 저장본을 다시 불러와 그린다(뷰포트는 이 컷 시작 — previzCutIndex 는 그대로).
            previzDirty = false;
            state.plan = plan;
            loadPlan(false);
          })
          .catch(function (err) {
            saveBtn.disabled = false;
            showError("저장하지 못했습니다: " + errMessage(err));
          });
      });
      right.appendChild(saveBtn);
      dirtyNote = el("p", "vnew-hint vnew-previz-dirty", "저장하지 않은 수정이 있습니다.");
      dirtyNote.style.display = previzDirty ? "" : "none";
      right.appendChild(dirtyNote);
      if (previzCtl && previzCtl.state) {
        bar.sync(previzCtl.state());
        keys.sync();
      }
    }

    var fieldRefreshers = []; // 작업 201: 기즈모를 끄는 동안 숫자 칸을 따라 바꾼다(입력 중인 칸은 그대로)
    var panelKeyMark = ""; // 작업 209b: 패널을 그린 때의 고른 인물 자세 키 시점

    function previzKeyMark() {
      var pk = previzCtl && previzCtl.poseKeyInfo ? previzCtl.poseKeyInfo() : null;
      return pk && pk.keys.length ? pk.id + "@" + pk.u : "";
    }

    /* 작업 200: 이 컷의 경고 — 서버가 본 직접 놓은 카메라 문제 + 직접 놓은 인물이 소품과 겹침(풀지 않고 알림). */
    function cutWarnings(cut) {
      var out = ((previz.manualWarnings || {})[cut.cutId] || []).slice();
      var col = (previz.collision || {})[cut.cutId] || {};
      var names = {};
      flat.forEach(function (it) {
        (it.scene.actors || []).forEach(function (a) {
          names[a.id] = a.name || a.id;
        });
      });
      var manualIds = {};
      (cut.actors || []).forEach(function (ca) {
        if (ca.manual) manualIds[ca.id] = true;
      });
      (col.unresolved || []).forEach(function (u) {
        if (manualIds[u.actor]) out.push((names[u.actor] || u.actor) + "이(가) 소품과 겹칩니다(직접 놓은 자리라 옮기지 않았습니다).");
      });
      return out;
    }

    /* 작업 209a: 포즈(채널 박스). 포즈 모드에서 관절을 골랐으면 그 관절 값(도)을 돌리기·숙이기·기울이기 칸과 ±5° 단추로,
       골반이면 높이(cm). 아래 [포즈 초기화](rig 지우기)·[좌우 반전]과 안내 한 줄. 값은 편집기가 관절 한계 안으로 자르고,
       칸 입력은 잠시 뒤 한 단계(applyEdit), 단추는 바로 한 단계로 기록한다. 고친 인물은 직접 고침(manual)이 된다. */
    function previzPoseSection(scene, cut, ca) {
      var box = el("div", "vnew-previz-pose");
      box.appendChild(el("strong", "vnew-previz-pose-head", "포즈"));
      var ctl = previzCtl && previzCtl.jointDeg ? previzCtl : null;
      var joint = ctl && ctl.poseMode ? ctl.poseJoint : null;
      function stepButton(row, text, onClick) {
        var b = el("button", "outline-button vnew-previz-pose-step", text);
        b.type = "button";
        b.addEventListener("click", onClick);
        row.appendChild(b);
        return b;
      }
      function numberRow(label, value, onInput, onStep, readBack) {
        var row = el("div", "vnew-previz-pose-row");
        row.appendChild(el("span", "vnew-field-label vnew-previz-pose-label", label));
        stepButton(row, "−" + PREVIZ_JOINT_STEP_DEG, function () { onStep(-PREVIZ_JOINT_STEP_DEG); });
        var input = el("input", "vnew-field-input vnew-previz-pose-input");
        input.type = "number";
        input.step = "1";
        input.value = String(value);
        input.addEventListener("input", function () {
          var raw = parseFloat(input.value);
          if (!isFinite(raw)) return;
          onInput(raw);
          applyEdit(scene, cut);
        });
        input.addEventListener("change", function () {
          input.value = String(readBack());
        });
        fieldRefreshers.push(function () {
          if (document.activeElement !== input) input.value = String(readBack());
        });
        row.appendChild(input);
        stepButton(row, "+" + PREVIZ_JOINT_STEP_DEG, function () { onStep(PREVIZ_JOINT_STEP_DEG); });
        box.appendChild(row);
      }
      if (ctl && ctl.poseMode && ctl.poseJointList) box.appendChild(previzJointPicker(ctl, joint));
      if (!ctl) {
        box.appendChild(el("p", "vnew-hint", "뷰포트가 준비되면 관절을 직접 잡을 수 있습니다."));
      } else if (!ctl.poseMode) {
        box.appendChild(el("p", "vnew-hint", "[포즈 (P)]를 켜고 몸의 고리를 누르면 관절을 고릅니다."));
      } else if (!joint) {
        box.appendChild(el("p", "vnew-hint", "위 판의 단추나 고리를 누르면(Tab = 다음 관절) 그 관절을 고릅니다. 손·발 네모는 끌어서 팔다리를 굽히고, 골반 네모는 위아래로 끕니다."));
      } else if (joint === "root") {
        box.appendChild(el("p", "vnew-previz-pose-joint", "관절: " + PREVIZ_JOINT_KO.root));
        var readCm = function () { return Math.round(ctl.rootYOf(ca.id, cut) * 1000) / 10; };
        numberRow("높이(cm)", readCm(), function (cm) {
          ctl.setRootY(ca.id, cm / 100, false, cut);
        }, function (delta) {
          ctl.setRootY(ca.id, (readCm() + delta) / 100, true, cut);
        }, readCm);
      } else {
        box.appendChild(el("p", "vnew-previz-pose-joint", "관절: " + (PREVIZ_JOINT_KO[joint] || joint)));
        var readDeg = function () { return ctl.jointDeg(ca.id, joint, cut) || [0, 0, 0]; };
        PREVIZ_JOINT_AXES.forEach(function (pair) {
          var axis = pair[1];
          numberRow(pair[0] + "(도)", readDeg()[axis], function (deg) {
            var v = readDeg();
            v[axis] = deg;
            ctl.setJointDeg(ca.id, joint, v, false, cut);
          }, function (delta) {
            var v = readDeg();
            v[axis] += delta;
            ctl.setJointDeg(ca.id, joint, v, true, cut);
          }, function () { return readDeg()[axis]; });
        });
      }
      if (joint) {
        var resetOne = el("button", "link-button vnew-previz-pose-reset-joint", "이 관절 초기화");
        resetOne.type = "button";
        resetOne.disabled = !(ca.rig && (joint === "root" ? typeof ca.rig.rootY === "number" : Array.isArray(ca.rig[joint])));
        resetOne.addEventListener("click", function () {
          ctl.resetJoint(ca.id, joint, cut);
        });
        box.appendChild(resetOne);
      }
      var actions = el("div", "vnew-previz-pose-actions");
      var resetAll = el("button", "outline-button vnew-previz-pose-reset", "포즈 초기화");
      resetAll.type = "button";
      resetAll.title = "직접 바꾼 관절을 모두 지웁니다(목록에서 고른 자세로 돌아갑니다)";
      resetAll.disabled = !ctl || !ca.rig;
      resetAll.addEventListener("click", function () {
        if (previzCtl && previzCtl.resetPose) previzCtl.resetPose(ca.id, cut);
      });
      actions.appendChild(resetAll);
      var mirror = el("button", "outline-button vnew-previz-pose-mirror", "좌우 반전");
      mirror.type = "button";
      mirror.title = "왼쪽·오른쪽 관절을 서로 바꿉니다";
      mirror.disabled = !ctl;
      mirror.addEventListener("click", function () {
        if (previzCtl && previzCtl.mirrorPose) previzCtl.mirrorPose(ca.id, cut);
      });
      actions.appendChild(mirror);
      box.appendChild(actions);
      box.appendChild(el("p", "vnew-hint vnew-previz-pose-note", PREVIZ_POSE_NOTE));
      if (ctl && ctl.posePresets) box.appendChild(previzPoseLibrary(cut, ca));
      return box;
    }

    /* 작업 213: 관절 고르기 판(Maya 아웃라이너·채널 박스 식) — 몸 모양 단추 격자. 누르면 그 관절을 고르고 뷰포트 고리가 밝아진다.
       목록은 편집기(poseJointList) 순서와 같고 Tab / Shift+Tab 도 같은 순서로 돈다. */
    function previzJointPicker(ctl, joint) {
      var grid = el("div", "vnew-previz-joint-picker");
      grid.setAttribute("role", "group");
      grid.setAttribute("aria-label", "관절 고르기");
      var known = {};
      ctl.poseJointList().forEach(function (j) {
        known[j.id] = j;
      });
      PREVIZ_JOINT_PICKER.forEach(function (spec) {
        if (!known[spec[0]]) return;
        var b = el("button", "outline-button vnew-previz-joint-btn" + (joint === spec[0] ? " is-on" : ""), spec[3]);
        b.type = "button";
        b.title = (known[spec[0]].nameKo || spec[3]) + " 고르기 · 뷰포트에서 Tab / Shift+Tab = 다음 / 앞 관절";
        b.style.gridRow = String(spec[1]);
        b.style.gridColumn = String(spec[2]);
        b.addEventListener("click", function () {
          if (previzCtl && previzCtl.selectJoint) previzCtl.selectJoint(spec[0]);
        });
        grid.appendChild(b);
      });
      return grid;
    }

    /* 작업 209b: [포즈 모음] — 기본 포즈(목록 자세·손 동작에서 만든 관절 값)와 이 브라우저에 저장한 포즈. 이름을 누르면 고른 인물
       (키가 있으면 지금 키 · 키 사이면 미리보기)의 rig 로 붙이고, [반전]은 좌우 반전 붙여넣기. [이 포즈 저장] → 이름 → localStorage.
       붙이기 한 번 = 되돌리기 한 단계(편집기 applyPosePreset). */
    function previzPoseLibrary(cut, ca) {
      var box = el("div", "vnew-previz-poselib");
      box.appendChild(el("strong", "vnew-previz-pose-head", "포즈 모음"));
      function presetRow(list, preset, onDelete) {
        var row = el("div", "vnew-previz-poselib-row");
        var apply = el("button", "outline-button vnew-previz-poselib-apply", preset.nameKo);
        apply.type = "button";
        apply.title = "이 포즈를 붙입니다";
        apply.addEventListener("click", function () {
          if (previzCtl && previzCtl.applyPosePreset) previzCtl.applyPosePreset(ca.id, preset, false, cut);
        });
        row.appendChild(apply);
        var flip = el("button", "link-button vnew-previz-poselib-mirror", "반전");
        flip.type = "button";
        flip.title = "좌우 반전 붙여넣기 — 왼쪽·오른쪽을 바꿔 붙입니다";
        flip.addEventListener("click", function () {
          if (previzCtl && previzCtl.applyPosePreset) previzCtl.applyPosePreset(ca.id, preset, true, cut);
        });
        row.appendChild(flip);
        if (onDelete) {
          var del = el("button", "link-button vnew-previz-poselib-delete", "지우기");
          del.type = "button";
          del.title = "저장한 포즈를 지웁니다(이 브라우저)";
          del.addEventListener("click", onDelete);
          row.appendChild(del);
        }
        list.appendChild(row);
      }
      var builtIn = el("div", "vnew-previz-poselib-list");
      previzCtl.posePresets().forEach(function (preset) {
        presetRow(builtIn, preset, null);
      });
      box.appendChild(builtIn);
      box.appendChild(el("span", "vnew-field-label", "내 포즈(이 브라우저)"));
      var mine = el("div", "vnew-previz-poselib-list is-user");
      var saved = previzUserPoses();
      if (!saved.length) mine.appendChild(el("p", "vnew-hint", "저장한 포즈가 없습니다."));
      saved.forEach(function (preset) {
        presetRow(mine, preset, function () {
          previzSaveUserPoses(previzUserPoses().filter(function (p) { return p.id !== preset.id; }));
          renderCutPanel();
        });
      });
      box.appendChild(mine);
      var save = el("button", "outline-button vnew-previz-poselib-save", "이 포즈 저장");
      save.type = "button";
      save.title = "지금 보이는 자세를 이름을 붙여 이 브라우저에 저장합니다";
      save.addEventListener("click", function () {
        if (!previzCtl || !previzCtl.capturePose) return;
        var pose = previzCtl.capturePose(ca.id);
        if (!pose) return;
        var name = window.prompt("포즈 이름", "");
        name = String(name || "").trim().slice(0, 40);
        if (!name) return;
        var list = previzUserPoses();
        list.unshift({ id: "u" + Date.now().toString(36), nameKo: name, rig: pose.rig });
        previzSaveUserPoses(list);
        renderCutPanel();
      });
      box.appendChild(save);
      box.appendChild(el("p", "vnew-hint", "키가 있으면 지금 키에 붙입니다. 키 사이에서는 미리보기라 키 찍기(S)로 저장하세요."));
      return box;
    }

    /* 직접 고친 값 표시 + [자동으로 되돌리기](표시만 뗀다 — 다음 다시 만들기 때 자동 계산). */
    function manualTag(obj, label, release) {
      var wrap = el("span", "vnew-previz-manual");
      if (!obj.manual) return wrap;
      wrap.appendChild(el("span", "vnew-badge vnew-previz-manual-badge", "직접 고침"));
      var undo = el("button", "link-button vnew-previz-manual-undo", "자동으로 되돌리기");
      undo.type = "button";
      undo.title = label + " 값을 다음 [다시 만들기] 때 자동으로 계산합니다(지금 값은 그대로 둡니다).";
      undo.addEventListener("click", function () {
        release();
        previzRefreshDirty();
        renderCutPanel();
      });
      wrap.appendChild(undo);
      return wrap;
    }

    function cameraBounds(room) {
      var hw = (Number(room.width) || 6) / 2 + PREVIZ_CAMERA_MARGIN_M;
      var hd = (Number(room.depth) || 6) / 2 + PREVIZ_CAMERA_MARGIN_M;
      return [[-hw, hw], [0.05, (Number(room.height) || 2.8) + PREVIZ_CAMERA_MARGIN_M], [-hd, hd]];
    }

    function actorBounds(room) {
      var hw = (Number(room.width) || 6) / 2 - PREVIZ_ACTOR_WALL_M;
      var hd = (Number(room.depth) || 6) / 2 - PREVIZ_ACTOR_WALL_M;
      return [[-hw, hw], [0, 0], [-hd, hd]];
    }

    /* 카메라 이동(moveTo)이 있으면 시작을 옮긴 만큼 끝도 옮긴다(옛 끝으로 되돌아가지 않게). */
    function shiftMove(cam, key, axis, delta) {
      if (!cam.moveTo || !Array.isArray(cam.moveTo[key]) || !delta) return;
      cam.moveTo[key][axis] = Math.round(((Number(cam.moveTo[key][axis]) || 0) + delta) * 1000) / 1000;
    }

    // 편집값을 즉시 뷰포트에 반영: 그 컷을 보고 있으면 지금 자세 그대로(키 스크러버 유지), 아니면 그 컷 시작을 그린다.
    // 작업 201: 잠시 멈춘 뒤(0.6초) 되돌리기 한 단계로 기록한다.
    function applyEdit(scene, cut) {
      isEditingPanel = true;
      markDirty();
      if (previzCtl) {
        previzCtl.pause();
        if (previzCtl.editCut && previzCtl.editCut() === cut) previzCtl.refresh();
        else previzCtl.seek(Number(cut.start) || 0);
        schedulePanelCommit(cut);
      }
      window.setTimeout(function () {
        isEditingPanel = false;
      }, 50);
    }

    /* 범위로 자른 값과 안내. 자르지 않았으면 note 를 비운다. */
    function clampNote(value, lo, hi, note, label) {
      var v = Math.max(lo, Math.min(hi, value));
      if (note) note.textContent = v !== value ? label + " 값은 " + lo + "~" + hi + " 범위라 " + v + "(으)로 맞췄습니다." : "";
      return v;
    }

    // 숫자 벡터 입력(axes = 보이는 칸, 기본 x·y·z). 숫자가 아닌 글(입력 중 "-")은 무시한다. bounds = [[lo,hi]×3].
    function vec3Field(label, obj, key, bounds, note, scene, cut, onEdit, axes) {
      var wrap = el("label", "vnew-field vnew-previz-vec");
      wrap.appendChild(el("span", "vnew-field-label", label));
      var row = el("div", "vnew-previz-vec-row");
      if (!Array.isArray(obj[key])) obj[key] = [0, 0, 0];
      var names = ["x", "y", "z"];
      (axes || [0, 1, 2]).forEach(function (i) {
        var input = el("input", "vnew-field-input");
        input.type = "number";
        input.step = "0.05";
        input.value = String(Number(obj[key][i]) || 0);
        input.addEventListener("input", function () {
          var raw = parseFloat(input.value);
          if (!isFinite(raw)) return;
          var arr = obj[key]; // 작업 201: 편집기가 바꾼 뒤에도 지금 배열을 쓴다
          var before = Number(arr[i]) || 0;
          var v = clampNote(raw, Math.round(bounds[i][0] * 100) / 100, Math.round(bounds[i][1] * 100) / 100, note, names[i]);
          arr[i] = v;
          if (onEdit) onEdit(i, v - before);
          applyEdit(scene, cut);
        });
        input.addEventListener("change", function () {
          input.value = String(Number(obj[key][i]) || 0);
        });
        fieldRefreshers.push(function () {
          if (document.activeElement !== input && Array.isArray(obj[key])) input.value = String(Number(obj[key][i]) || 0);
        });
        row.appendChild(input);
      });
      wrap.appendChild(row);
      return wrap;
    }

    function numField(label, obj, key, min, max, note, scene, cut, onEdit) {
      var wrap = el("label", "vnew-field");
      wrap.appendChild(el("span", "vnew-field-label", label));
      var input = el("input", "vnew-field-input");
      input.type = "number";
      if (min !== undefined) input.min = String(min);
      if (max !== undefined) input.max = String(max);
      input.value = String(Number(obj[key]) || 0);
      input.addEventListener("input", function () {
        var raw = parseFloat(input.value);
        if (!isFinite(raw)) return;
        obj[key] = clampNote(raw, min, max, note, label);
        if (onEdit) onEdit();
        applyEdit(scene, cut);
      });
      input.addEventListener("change", function () {
        input.value = String(Number(obj[key]) || 0);
      });
      fieldRefreshers.push(function () {
        if (document.activeElement !== input) input.value = String(Number(obj[key]) || 0);
      });
      wrap.appendChild(input);
      return wrap;
    }

    function selectField(label, obj, key, options, scene, cut, onEdit) {
      var wrap = el("label", "vnew-field");
      wrap.appendChild(el("span", "vnew-field-label", label));
      var sel = el("select", "vnew-field-input");
      options.forEach(function (pair) {
        var opt = el("option", null, pair[1]);
        opt.value = pair[0];
        if ((obj[key] || "") === pair[0]) opt.selected = true;
        sel.appendChild(opt);
      });
      sel.addEventListener("change", function () {
        obj[key] = sel.value || null;
        if (onEdit) onEdit();
        applyEdit(scene, cut);
      });
      wrap.appendChild(sel);
      return wrap;
    }

    renderCutPanel();

    // --- 승인하고 녹화 ---
    renderRecordSection(recSection, previz, showError);
  }

  /* 현재 콘티에서 cutId 의 대사(dialogueKo)를 찾는다(타임라인 말풍선용). */
  function contiDialogueFor(cutId) {
    var cuts = ((state.plan || {}).conti || {}).cuts || [];
    for (var i = 0; i < cuts.length; i++) {
      if (cuts[i].id === cutId) return cuts[i].dialogueKo || "";
    }
    return "";
  }

  /* 진행 띠 + [취소] (프리비즈 설계용). */
  function previzProgressBar(label, showError) {
    var wrap = el("div", "vnew-progress-wrap");
    wrap.appendChild(el("p", "vnew-progress-label", label));
    var track = el("div", "vnew-progress-track");
    track.appendChild(el("div", "vnew-progress-fill"));
    wrap.appendChild(track);
    var cancel = el("button", "outline-button danger", "취소");
    cancel.type = "button";
    cancel.addEventListener("click", function () {
      cancel.disabled = true;
      api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/cancel", { method: "POST" })
        .then(function () {
          loadPlan(false);
        })
        .catch(function (err) {
          cancel.disabled = false;
          if (showError) showError("취소하지 못했습니다: " + errMessage(err));
        });
    });
    wrap.appendChild(cancel);
    return wrap;
  }

  /* [승인하고 녹화] → approve → conti.segments 마다 record(seg 시작~끝) → POST recordings/<segId>.
     진행 표시 "구간 n/N 녹화 중… 화면을 닫지 마세요". 끝나면 각 구간 mp4 미리보기. 실패 구간 [다시 녹화]. */
  function renderRecordSection(host, previz, showError) {
    clear(host);
    host.appendChild(el("h2", "vnew-sec-title", "승인하고 녹화"));

    var segments = previz.segments || [];
    var recordings = previz.recordings || {};

    host.appendChild(
      el(
        "p",
        "vnew-hint",
        "장면을 확인했으면 승인하고 구간별로 영상을 만듭니다. 이제 실시간 녹화가 아니라 프레임을 한 장씩 " +
          "그려 모으므로, 창을 가리거나 다른 탭으로 넘어가도 계속 진행됩니다(시간은 조금 걸립니다)."
      )
    );

    // 진행 상태(녹화 중)
    var progress = el("div", "vnew-previz-rec-progress");
    host.appendChild(progress);

    if (previzRecording && !previzRecording.aborted && previzRecording.done < previzRecording.total) {
      progress.textContent = recProgressText();
    }

    // 구간별 상태·미리보기
    var list = el("div", "vnew-previz-rec-list");
    segments.forEach(function (seg, idx) {
      var row = el("div", "vnew-previz-rec-row");
      row.appendChild(el("span", "vnew-previz-rec-no", "구간 " + (idx + 1)));
      row.appendChild(el("span", "vnew-previz-rec-sec", (seg.seconds || 0) + "초"));
      var rec = recordings[seg.id] || {};
      if (rec.status === "ready" && rec.url) {
        var video = el("video", "vnew-previz-rec-video");
        video.src = rec.url;
        video.controls = true;
        video.setAttribute("playsinline", "");
        row.appendChild(video);
        if (rec.stale) row.appendChild(el("span", "vnew-badge vnew-previz-rec-stale", "고친 장면 · 다시 녹화 필요"));
      } else {
        row.appendChild(el("span", "vnew-previz-rec-state", "아직 영상 전"));
        var redo = el("button", "outline-button vnew-previz-rec-redo", "다시 만들기");
        redo.type = "button";
        redo.addEventListener("click", function () {
          recordSegments([seg], showError);
        });
        row.appendChild(redo);
      }
      list.appendChild(row);
    });
    host.appendChild(list);

    // 소품과 겹침을 풀지 못한 컷(084 R1.9) — 한 줄 안내. 승인은 막지 않는다.
    var collisionMap = previz.collision || {};
    var stuckCuts = [];
    flattenCuts(previz.scenes || []).forEach(function (item, idx) {
      var col = collisionMap[item.cut && item.cut.cutId];
      if (col && (col.unresolved || []).length) stuckCuts.push("컷 " + (idx + 1));
    });
    if (stuckCuts.length) {
      host.appendChild(el("p", "vnew-hint vnew-previz-collision-note",
        "소품과 겹침을 풀지 못한 컷: " + stuckCuts.join(", ") + " — 승인은 할 수 있습니다."));
    }
    // 보정 전에 만든 옛 프리비즈(084 R1.11) — 화면·녹화는 그대로 두고 다시 설계를 권한다. 승인·녹화는 막지 않는다.
    if (collisionMap.needsRedesign) {
      host.appendChild(el("p", "vnew-hint vnew-previz-collision-redesign", COLLISION_REDESIGN_TEXT));
    }

    // 승인하고 녹화 버튼
    var btn = el("button", "primary-button vnew-previz-approve", previz.approved ? "모두 녹화" : "승인하고 녹화");
    btn.type = "button";
    var busy = !!(previzRecording && !previzRecording.aborted && previzRecording.done < previzRecording.total);

    // --- 70% 승인 관문: 낮은 컷이 있으면 승인 비활성 + 안내 + [그래도 승인] ---
    var lowCuts = (previz.lowMatchCuts) || [];
    var alreadyApproved = !!previz.approved;

    // approve → (옵션 force) → 녹화. force 면 confirm 창(비용 포함) 후 진행.
    // 작업 200: 이미 승인한 장면의 [모두 녹화]는 모든 구간, 승인할 때는 녹화가 없거나 stale 인 구간만 녹화한다.
    function doApprove(force) {
      btn.disabled = true;
      previzApproveAndRecord(force, alreadyApproved, showError, function () {
        btn.disabled = false;
      });
    }

    // 작업 200: 장면을 고쳐 다시 녹화해야 하는 구간 — 안내 + 버튼 하나(승인 → 그 구간만 녹화).
    var staleIds = previz.staleSegments || [];
    if (staleIds.length) {
      var staleBox = el("div", "vnew-previz-stale");
      var staleNos = segments.map(function (seg, i) { return staleIds.indexOf(seg.id) >= 0 ? "구간 " + (i + 1) : ""; })
        .filter(function (t) { return t; });
      staleBox.appendChild(el("p", "vnew-previz-stale-msg", PREVIZ_STALE_TEXT + " (" + staleNos.join(", ") + ")"));
      var staleBtn = el("button", "primary-button vnew-previz-stale-btn", "변경된 컷만 다시 녹화");
      staleBtn.type = "button";
      staleBtn.disabled = busy;
      staleBtn.addEventListener("click", function () {
        staleBtn.disabled = true;
        previzApproveAndRecord(!!previz.approvedForced, false, showError, function () {
          staleBtn.disabled = false;
        });
      });
      staleBox.appendChild(staleBtn);
      host.appendChild(staleBox);
    }

    if (lowCuts.length && !alreadyApproved && previz.matchGate) {
      // 낮은 컷 안내 + 번호 나열(70% 미만). 승인 버튼은 비활성.
      btn.disabled = true;
      var minPct = Math.round((Number(previz.approveMinMatch) || 0.7) * 100);
      var nums = lowCuts.map(function (c) { return "컷 " + c.no; }).join(", ");
      var warn = el("div", "vnew-previz-lowmatch");
      warn.appendChild(
        el("p", "vnew-previz-lowmatch-msg",
          "시점 일치도 " + minPct + "% 미만 컷: " + nums +
          " — 다시 만들기 지시 또는 카메라를 직접 조정하세요")
      );
      // 작은 [그래도 승인] 링크 버튼.
      var forceLink = el("button", "link-button vnew-previz-force", "그래도 승인");
      forceLink.type = "button";
      forceLink.addEventListener("click", function () {
        var est = ((state.plan || {}).gen || {}).estimate || {};
        var usd = Number(est.totalUsd) || 0;
        var costMsg = usd > 0 ? (" 예상 영상 비용 $" + usd.toFixed(2) + "(⑤ 견적)를 쓰게 됩니다.") : "";
        var ok = window.confirm(
          "일치도가 낮은 컷이 있어 결과 영상이 웹툰과 다를 수 있습니다." + costMsg + " 승인할까요?"
        );
        if (ok) doApprove(true);
      });
      warn.appendChild(forceLink);
      host.appendChild(warn);
    } else if (lowCuts.length && !alreadyApproved) {
      // 작업 231: 관문 꺼짐(기본) — 낮은 컷은 안내만 하고 승인은 막지 않는다(링크·확인 창 없음).
      var infoPct = Math.round((Number(previz.approveMinMatch) || 0.7) * 100);
      var infoNums = lowCuts.map(function (c) { return "컷 " + c.no; }).join(", ");
      host.appendChild(el("p", "vnew-hint vnew-previz-lowmatch-info",
        "시점 일치도 " + infoPct + "% 미만 컷: " + infoNums +
        " — 그대로 진행해도 됩니다. 결과가 웹툰과 다를 수 있어요."));
      btn.disabled = busy;
    } else {
      btn.disabled = busy;
    }

    btn.addEventListener("click", function () {
      // 이미 [그래도 승인]으로 승인한 장면을 다시 녹화할 때는 같은 force 로 보낸다(아니면 409 LowMatch).
      doApprove(!!(alreadyApproved && previz.approvedForced));
    });
    host.appendChild(btn);

    // 모두 녹화 완료 안내 + 다음 단계로(작업 200: 다시 녹화해야 하는 구간이 있으면 위 안내가 대신한다)
    if (previz.recordedAll && !staleIds.length) {
      var done = el("p", "vnew-previz-rec-done", "녹화 완료 — ⑤ 영상으로 넘어갈 수 있습니다.");
      host.appendChild(done);
      var next = el("button", "outline-button", "⑤ 영상으로");
      next.type = "button";
      next.addEventListener("click", function () {
        state.planStep = 5;
        renderPlan();
      });
      host.appendChild(next);
    }
  }

  // 프레임 묶음 크기(요구사항: 50장씩)·fps·묶음 실패 재시도 횟수.
  var FRAME_CHUNK = 50;
  var FRAME_FPS = 24;
  var CHUNK_RETRIES = 2;

  /* 구간들을 순서대로 '프레임 단위 오프라인 렌더'한다: renderFrames 로 jpeg 프레임을 모아 50장씩 묶어
     올리고(POST frames?start), 마지막에 finish({frames,fps}) 로 서버가 ffmpeg 로 mp4 를 만든다.
     실시간·captureStream 이 아니라 rAF 와 무관하므로 창을 가려도 계속된다(프레임 안 빠짐).
     실패한 묶음만 2회까지 재시도한다. */
  function recordSegments(segments, showError) {
    if (!previzCtl || !previzCtl.renderFrames) {
      if (showError) showError("3D 뷰어가 준비되지 않았습니다.");
      return Promise.resolve();
    }
    if (!segments || !segments.length) return Promise.resolve();
    previzRecording = {
      total: segments.length,
      done: 0,
      failed: [],
      current: 0,
      aborted: false,
      phase: "render", // render | upload
      frameDone: 0,
      frameTotal: 0
    };

    var pid = state.projectId;

    function uploadChunk(segId, start, chunk) {
      // 묶음 하나(jpeg Blob 배열)를 multipart 로 올린다. 실패하면 CHUNK_RETRIES 회까지 재시도.
      function attempt(tries) {
        var form = new FormData();
        for (var i = 0; i < chunk.length; i++) {
          form.append("f", chunk[i], String(start + i).padStart(5, "0") + ".jpg");
        }
        return fetch(
          "/api/vnew/p/" + encodeURIComponent(pid) + "/previz/recordings/" +
            encodeURIComponent(segId) + "/frames?start=" + encodeURIComponent(start),
          { method: "POST", headers: { Authorization: "Bearer " + token() }, body: form }
        )
          .then(function (response) {
            if (!response.ok) throw new Error("frames " + response.status);
            return true;
          })
          .catch(function (err) {
            if (tries < CHUNK_RETRIES) return attempt(tries + 1);
            throw err;
          });
      }
      return attempt(0);
    }

    function uploadFinish(segId, frameCount) {
      return fetch(
        "/api/vnew/p/" + encodeURIComponent(pid) + "/previz/recordings/" +
          encodeURIComponent(segId) + "/finish",
        {
          method: "POST",
          headers: { Authorization: "Bearer " + token(), "Content-Type": "application/json" },
          body: JSON.stringify({ frames: frameCount, fps: FRAME_FPS })
        }
      ).then(function (response) {
        if (response.ok) return true;
        return response
          .json()
          .catch(function () {
            return null;
          })
          .then(function (payload) {
            var msg = (payload && payload.error && payload.error.message) || "마무리(finish) 실패";
            throw new Error(msg);
          });
      });
    }

    function recordOne(i) {
      if (i >= segments.length) {
        previzRecording.aborted = true; // 끝
        loadPlan(false);
        return Promise.resolve();
      }
      previzRecording.current = i;
      previzRecording.phase = "render";
      previzRecording.frameDone = 0;
      previzRecording.frameTotal = 0;
      refreshRecProgress();

      var seg = segments[i];
      var from = Number(seg.start) || 0;
      var to = Number(seg.end) || from;
      // seg.start/end 가 없으면 cutIds 로 구간 시각을 찾는다.
      if (!(to > from)) {
        var bounds = segmentBounds(seg);
        from = bounds.from;
        to = bounds.to;
      }

      // 1) 프레임을 모은다(오프라인 렌더). 2) 50장씩 올린다. 3) finish.
      return previzCtl
        .renderFrames(from, to, FRAME_FPS, function (done, total) {
          previzRecording.phase = "render";
          previzRecording.frameDone = done;
          previzRecording.frameTotal = total;
          refreshRecProgress();
        })
        .then(function (frames) {
          previzRecording.phase = "upload";
          previzRecording.frameTotal = frames.length;
          previzRecording.frameDone = 0;
          refreshRecProgress();

          var start = 0;
          function nextChunk() {
            if (start >= frames.length) {
              return uploadFinish(seg.id, frames.length);
            }
            var chunk = frames.slice(start, start + FRAME_CHUNK);
            var chunkStart = start;
            return uploadChunk(seg.id, chunkStart, chunk).then(function () {
              start += chunk.length;
              previzRecording.frameDone = start;
              refreshRecProgress();
              return nextChunk();
            });
          }
          return nextChunk();
        })
        .then(function () {
          previzRecording.done += 1;
          refreshRecProgress();
          return recordOne(i + 1);
        })
        .catch(function (err) {
          previzRecording.failed.push(seg.id);
          previzRecording.aborted = true;
          if (showError) showError("구간 " + (i + 1) + " 영상 만들기에 실패했습니다: " + errMessage(err));
          loadPlan(false);
        });
    }

    return recordOne(0);
  }

  /* segments 항목에 start/end 가 없으면 콘티 컷에서 cutIds 로 구간 시각을 구한다. */
  function segmentBounds(seg) {
    var previz = (state.plan || {}).previz || {};
    var scenes = previz.scenes || [];
    var cutIds = seg.cutIds || [];
    var from = Infinity;
    var to = 0;
    scenes.forEach(function (scene) {
      (scene.timeline || []).forEach(function (cut) {
        if (cutIds.indexOf(cut.cutId) >= 0) {
          from = Math.min(from, Number(cut.start) || 0);
          to = Math.max(to, Number(cut.end) || 0);
        }
      });
    });
    if (!isFinite(from)) from = 0;
    return { from: from, to: to };
  }

  /* 진행 문구를 만든다: "구간 n/N · 프레임 x/total 그리는 중 / 올리는 중". */
  function recProgressText() {
    if (!previzRecording) return "";
    var seg = "구간 " + (previzRecording.current + 1) + "/" + previzRecording.total;
    var phase = previzRecording.phase === "upload" ? "올리는 중" : "그리는 중";
    var total = previzRecording.frameTotal || 0;
    var done = previzRecording.frameDone || 0;
    if (total > 0) {
      return seg + " · 프레임 " + done + "/" + total + " " + phase + " (창을 가려도 계속됩니다)";
    }
    return seg + " · 준비 중 (창을 가려도 계속됩니다)";
  }

  /* 녹화 진행 문구만 갱신한다(전체 다시 그리지 않음). */
  function refreshRecProgress() {
    var node = planHost() && planHost().querySelector(".vnew-previz-rec-progress");
    if (!node || !previzRecording) return;
    if (previzRecording.aborted || previzRecording.done >= previzRecording.total) {
      node.textContent = "";
      return;
    }
    node.textContent = recProgressText();
  }

  /* ================================================================ ⑤ 영상 생성

     서버 gen_view(generate.py) 모양을 그대로 쓴다: opening·segments[real/webtoon]·finalReal·
     finalWebtoon·selected·status. 진행 중이면 schedulePlanPoll 이 3~5초마다 다시 부른다. */

  // 구간 상태 배지 문구(설계자 2026-10-20).
  var GEN_STATUS_LABEL = {
    waiting: "앞 구간 대기",
    queued: "대기",
    preparing: "준비 중",
    submitted: "만드는 중",
    completed: "완료",
    failed: "실패",
    cancelled: "취소됨",
    skipped: "건너뜀"
  };

  function genStatusLabel(value) {
    return GEN_STATUS_LABEL[value] || value || "";
  }

  /* 영상 내려받기 링크(presigned URL). 서버가 video/mp4 content-type 으로 서명한다. */
  function downloadLink(url, label) {
    var a = el("a", "outline-button vnew-download", label || "영상 내려받기");
    a.href = url;
    a.setAttribute("download", "");
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noopener");
    return a;
  }

  /* ⑤ 시작 화면(촬영 지시서 카드 + [영상 만들기])을 그린다(설계자 2026-10-22, ⑤).
     staleGen 이 주어지면(프리비즈가 바뀐 옛 gen) 위에 안내를 붙이고, 이전 결과(최종 영상)는
     접이식 "이전 영상 보기" 로만 보인다. staleGen 이 null 이면 평범한 시작 화면. */
  /* 작업 205: 바뀐 테이크 이름 — "테이크 1(컷 1–6)" 처럼 gen.units 순서 번호와 컷 범위. ids 순서가 아니라 테이크 순서로 잇는다. */
  function takeStaleNames(gen, ids) {
    var want = ids || [];
    var names = [];
    ((gen || {}).units || []).forEach(function (unit, i) {
      if (want.indexOf(unit.id) < 0) return;
      var range = "";
      if (unit.startCutNo != null && unit.endCutNo != null) {
        range = unit.startCutNo === unit.endCutNo ? "(컷 " + unit.startCutNo + ")" : "(컷 " + unit.startCutNo + "–" + unit.endCutNo + ")";
      }
      names.push("테이크 " + (i + 1) + range);
    });
    return names.length ? names.join(", ") : "바뀐 테이크";
  }

  function takeStaleText(gen) {
    return "프리비즈가 바뀌었습니다 — " + takeStaleNames(gen, (gen || {}).staleUnits) + "이 지금 영상과 다릅니다.";
  }

  /* 작업 206: 촬영 지시서를 (다시) 만드는 중인지(서버 BRIEF_BUILDING_STATUSES 와 같다). */
  var BRIEF_BUILDING_LABEL = "촬영 지시서 마무리 중…";
  function briefBuilding(previz) {
    var status = (((previz || {}).shotBrief) || {}).status;
    return status === "running" || status === "queued" || status === "building";
  }

  function renderGenStart(host, showError, staleGen) {
    var plan = state.plan || {};
    var previz = plan.previz || {};
    var staleNotice = previzStaleNotice(); // 작업 200: 장면을 고친 뒤 다시 녹화해야 하는 구간
    if (staleNotice) host.appendChild(staleNotice);

    if (staleGen && staleGen.mode === "previz-take" && (staleGen.staleUnits || []).length) {
      // 작업 205: 통 테이크 — 바뀐 테이크를 이름으로 알리고 [바뀐 테이크만 다시 만들기 ($x)] 를 함께 보인다.
      var takeNotice = el("section", "panel vnew-take-stale");
      takeNotice.appendChild(el("p", "vnew-take-stale-msg", takeStaleText(staleGen)));
      var only = staleGen.onlyChanged || {};
      if (briefBuilding(previz) && previz.recordedAll) {
        // 작업 206: 지시서를 다시 만드는 중 — 바뀐 테이크 판정·금액이 끝나야 정해진다(서버도 409 BriefBuilding).
        var waitBtn = el("button", "primary-button vnew-only-changed-btn is-waiting", BRIEF_BUILDING_LABEL);
        waitBtn.type = "button";
        waitBtn.disabled = true;
        takeNotice.appendChild(waitBtn);
      } else if (only.available && previz.recordedAll) {
        var onlyBtn = el("button", "primary-button vnew-only-changed-btn",
          "바뀐 테이크만 다시 만들기 (" + castingUsdText(only.usd) + ")");
        onlyBtn.type = "button";
        onlyBtn.addEventListener("click", function () {
          openGenerateConfirm(showError, { onlyChanged: only });
        });
        takeNotice.appendChild(onlyBtn);
      } else if (only.block) {
        takeNotice.appendChild(el("p", "vnew-hint vnew-only-changed-block", only.block));
      }
      host.appendChild(takeNotice);
      host.appendChild(prevGenFold(staleGen));
    } else if (staleGen) {
      host.appendChild(
        el(
          "p",
          "vnew-hint vnew-stale-note",
          "프리비즈가 바뀌어 이전 영상 계획(취소/완료)은 새 녹화와 맞지 않습니다. 새로 만들어 주세요."
        )
      );
      host.appendChild(prevGenFold(staleGen));
    }

    // 촬영 지시서 카드(승인 후). 진행 표시 → 완료되면 컷별 접이식.
    host.appendChild(shotBriefCard(previz, showError));
    var startEst = ((plan.gen || {}).estimate) || {};
    if (startEst.mode === "previz-take") {
      var takeSecs0 = 0;
      (startEst.takes || []).forEach(function (t) { takeSecs0 += Number(t.seconds) || 0; });
      var banner = el("section", "panel vnew-take-banner");
      var bTitle = el("h2", "vnew-sec-title", "통 테이크");
      bTitle.appendChild(el("span", "vnew-badge vnew-beta-badge", "BETA"));
      banner.appendChild(bTitle);
      banner.appendChild(
        el("p", "vnew-hint", takePlanHintText("start", Math.round(takeSecs0), takeRealEnabledOf(plan.gen, plan)))
      );
      host.appendChild(banner);
      // 장면 의상·목소리 표(설계자 2026-10-26, 064).
      host.appendChild(castTableCard(showError));
    }
    if (previz.recordedAll) {
      host.appendChild(
        el("p", "vnew-hint", "프리비즈 녹화가 끝났습니다. 이 녹화를 바탕으로 실사·웹툰 영상을 만듭니다.")
      );
      var startBtn = el("button", "primary-button", staleGen && staleGen.mode === "previz-take" ? "새로 영상 만들기" : "영상 만들기");
      startBtn.type = "button";
      startBtn.addEventListener("click", function () {
        openGenerateConfirm(showError, staleGen ? { hadResult: true } : null);
      });
      host.appendChild(startBtn);
    } else {
      host.appendChild(
        el("p", "vnew-hint", "아직 프리비즈 녹화가 끝나지 않았습니다. ④ 프리비즈(3D)에서 녹화를 마쳐 주세요.")
      );
    }
    var backBtn = el("button", "outline-button", "← 프리비즈로");
    backBtn.type = "button";
    backBtn.addEventListener("click", function () {
      state.planStep = 4;
      renderPlan();
    });
    host.appendChild(backBtn);
  }

  /* 작업 200: 장면을 고친 뒤 다시 녹화하지 않은 구간이 있으면 ⑤ 에도 알린다(없으면 null). [다시 녹화] = ④ 로 가서 그 구간만
     다시 녹화한다(뷰포트가 준비되면 승인 → 녹화). 영상 생성은 그동안 409 PrevizStale 로 막힌다. */
  function previzStaleNotice() {
    var previz = (state.plan || {}).previz || {};
    var stale = previz.staleSegments || [];
    if (!stale.length) return null;
    var nos = (previz.segments || []).map(function (seg, i) { return stale.indexOf(seg.id) >= 0 ? "구간 " + (i + 1) : ""; })
      .filter(function (t) { return t; });
    var box = el("section", "panel vnew-previz-stale");
    box.appendChild(el("p", "vnew-previz-stale-msg", "프리비즈를 다시 녹화해야 합니다" + (nos.length ? " (" + nos.join(", ") + ")" : "")));
    box.appendChild(previzRerecordButton());
    return box;
  }

  function previzRerecordButton() {
    var btn = el("button", "primary-button vnew-previz-stale-btn", "다시 녹화");
    btn.type = "button";
    btn.addEventListener("click", function () {
      state.previzRerecord = true;
      state.planStep = 4;
      renderPlan();
    });
    return btn;
  }

  /* 접이식 "이전 영상 보기" — 이전 gen 의 최종 영상(있으면)만 보인다(설계자 2026-10-22, ⑤). */
  function prevGenFold(gen) {
    var details = el("details", "vnew-prev-gen");
    details.appendChild(el("summary", "vnew-prev-gen-summary", "이전 영상 보기"));
    var body = el("div", "vnew-prev-gen-body");
    var shown = false;
    GEN_VARIANTS.forEach(function (meta) {
      var final = (meta.key === "real" ? gen.finalReal : gen.finalWebtoon) || {};
      if (final.status === "done" && final.videoUrl) {
        shown = true;
        var col = el("div", "vnew-prev-gen-col");
        col.appendChild(el("h3", "vnew-variant-title", meta.title));
        var video = el("video", "vnew-final-player");
        video.src = final.videoUrl;
        video.controls = true;
        video.setAttribute("playsinline", "");
        col.appendChild(video);
        body.appendChild(col);
      }
    });
    if (!shown) {
      body.appendChild(el("p", "vnew-hint", "이전에 완성된 영상이 없습니다."));
    }
    details.appendChild(body);
    return details;
  }

  function renderGenStep(host) {
    var plan = state.plan || {};
    var gen = plan.gen || {};
    var error = planError("");
    host.appendChild(error);
    function showError(message) {
      error.style.display = "";
      error.textContent = message;
    }

    if (!gen.status || gen.status === "none") {
      renderGenStart(host, showError, null);
      return;
    }

    // 프리비즈가 바뀌어 이 gen(취소·완료 등)이 새 녹화와 맞지 않으면 → 시작 화면으로 유도하고
    // 이전 결과는 접이식으로만 보인다(설계자 2026-10-22, ⑤).
    if (gen.stale) {
      renderGenStart(host, showError, gen);
      return;
    }

    var running = gen.status === "running";
    // 진행 중이 아니면(취소·실패·일부·완료) 언제든 새 gen 을 시작할 수 있다(설계자 2026-10-22, ⑤).
    var canRemake = !running && gen.status && gen.status !== "none";
    var staleNotice = previzStaleNotice(); // 작업 200
    if (staleNotice) host.appendChild(staleNotice);
    mnotesGenWatch(state.genFresh === true, running); // 작업 199: 모델 에러 로그 건수(⑤ 를 열 때 · 생성이 끝날 때만)
    state.genFresh = false;

    // --- 진행 상태 띠 + 취소 ---
    var statusBar = el("section", "panel vnew-gen-statusbar");
    var statusText = {
      running: "영상을 만들고 있습니다…",
      done: "두 영상이 모두 준비됐습니다. 아래에서 하나를 골라 사운드로 넘어가세요.",
      partial: "일부 구간이 실패했습니다. 실패한 칸에서 다시 시도하거나, 새로 만들 수 있습니다.",
      failed: "영상 생성에 실패했습니다.",
      cancelled: "영상 생성을 취소했습니다."
    };
    // 통 테이크 웹툰만 끝났고 실사 회차가 없으면(실사 열을 그릴 때) 안내를 바꾼다(vnew-live-fx C.11, takeStatusText).
    statusBar.appendChild(el("p", "vnew-gen-status-text", takeStatusText(gen) || statusText[gen.status] || gen.status));
    if (running) {
      var track = el("div", "vnew-progress-track");
      track.appendChild(el("div", "vnew-progress-fill"));
      statusBar.appendChild(track);
      var cancelBtn = el("button", "outline-button danger", "생성 취소");
      cancelBtn.type = "button";
      cancelBtn.addEventListener("click", function () {
        var okConfirm = false;
        try {
          okConfirm = window.confirm("정말 취소할까요? 지금까지 만든 구간은 남지만, 진행 중인 작업은 멈춥니다.");
        } catch (e) {
          okConfirm = true;
        }
        if (!okConfirm) return;
        cancelBtn.disabled = true;
        api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/cancel", { method: "POST" })
          .then(function () {
            loadPlan(false);
          })
          .catch(function (err) {
            cancelBtn.disabled = false;
            showError("취소하지 못했습니다: " + errMessage(err));
          });
      });
      statusBar.appendChild(cancelBtn);
    }
    if (canRemake) {
      // done/partial 이면 지금 결과는 '이전 영상'으로만 남는다는 안내를 확인창에서 보인다.
      var remakeBtn = el("button", "outline-button", "새로 영상 만들기");
      remakeBtn.type = "button";
      remakeBtn.addEventListener("click", function () {
        openGenerateConfirm(showError, { hadResult: gen.status === "done" || gen.status === "partial" });
      });
      statusBar.appendChild(remakeBtn);
    }
    statusBar.appendChild(mnotesButton("gen", { pid: state.projectId, variant: "all" })); // 작업 199
    host.appendChild(statusBar);

    // --- 안내: 프리비즈 기반(설계자 2026-10-21 · 컷 단위 2026-10-22 · 통 테이크 2026-10-26) ---
    if (gen.mode === "previz") {
      host.appendChild(
        el("p", "vnew-hint", "프리비즈를 바탕으로 실사와 웹툰 그림체를 각각 만듭니다. 컷마다 프리비즈 미리보기 옆에 실사·웹툰 결과가 나옵니다.")
      );
    } else if (gen.mode === "previz-take") {
      var takeBanner = el("section", "panel vnew-take-banner");
      var takeTitle = el("h2", "vnew-sec-title", "통 테이크");
      takeTitle.appendChild(el("span", "vnew-badge vnew-beta-badge", "BETA"));
      takeBanner.appendChild(takeTitle);
      var takeSecs = Math.round(takeTotalSeconds(gen));
      takeBanner.appendChild(
        el("p", "vnew-hint", takePlanHintText("result", takeSecs, takeRealEnabledOf(gen, plan)))
      );
      host.appendChild(takeBanner);
    }

    // --- 맨 위 결과: 통 테이크는 웹툰만, 그 외는 실사·웹툰 두 열 ---
    host.appendChild(finalResults(gen, showError));

    // --- 시작 장면(opening) — 옛 모드만(프리비즈·통 테이크는 시작 장면이 없다) ---
    if (gen.mode !== "previz" && gen.mode !== "previz-take") {
      host.appendChild(openingCard(gen.opening || {}));
    }

    // --- 타임라인: 통 테이크(테이크 행) · 컷 단위 · 옛 구간 ---
    if (gen.mode === "previz-take") {
      host.appendChild(takeTimeline(gen.units || [], showError, gen));
    } else if (gen.units && gen.units.length) {
      host.appendChild(cutTimeline(gen.units, showError));
    } else {
      host.appendChild(segmentTimeline(gen.segments || [], showError, gen.mode || ""));
    }
  }

  /* 통 테이크 gen 의 총 초(테이크 seconds 합). */
  function takeTotalSeconds(gen) {
    var units = (gen || {}).units || [];
    var total = 0;
    units.forEach(function (u) {
      total += Number(u.seconds) || 0;
    });
    return total;
  }

  /* 장면 의상·목소리 표(설계자 2026-10-26, 064). 인물별 자동 읽기 값(있으면) + 수정 입력칸(의상·목소리)
     + [저장]. [의상 읽기] 로 미리 자동 읽기(POST /cast-notes). 저장은 PUT /input {castOverrides}. */
  function castTableCard(showError) {
    var plan = state.plan || {};
    var input = plan.input || {};
    var rows = input.sceneCast || [];
    var card = el("section", "panel vnew-cast-card");
    var head = el("div", "vnew-sec-head");
    head.appendChild(el("h2", "vnew-sec-title", "장면 의상·목소리"));
    card.appendChild(head);
    card.appendChild(
      el(
        "p",
        "vnew-hint",
        "원작 패널에서 읽은 이 장면의 의상입니다. 다르면 고쳐 주세요(예: 검은 와이셔츠)."
      )
    );

    if (!rows.length) {
      card.appendChild(el("p", "vnew-hint", "등장 인물이 아직 없습니다."));
    }

    var table = el("div", "vnew-cast-table");
    var inputs = {};  // name -> {costume, voice}
    rows.forEach(function (r) {
      var name = String(r.name || "");
      var line = el("div", "vnew-cast-row");
      line.appendChild(el("span", "vnew-cast-name", name));

      var costumeWrap = el("label", "vnew-cast-field");
      costumeWrap.appendChild(el("span", "vnew-cast-flabel", "의상"));
      var costume = el("input", "vnew-cast-input");
      costume.type = "text";
      costume.maxLength = 200;
      costume.value = String(r.costume || "");
      costume.placeholder = r.costumeAuto ? ("자동: " + r.costumeAuto) : "원작 패널에서 읽은 의상";
      costumeWrap.appendChild(costume);
      line.appendChild(costumeWrap);

      var voiceWrap = el("label", "vnew-cast-field");
      voiceWrap.appendChild(el("span", "vnew-cast-flabel", "목소리"));
      var voice = el("input", "vnew-cast-input");
      voice.type = "text";
      voice.maxLength = 80;
      voice.value = String(r.voice || "");
      voice.placeholder = r.voiceAuto ? ("자동: " + r.voiceAuto) : "성별·나이대·톤";
      voiceWrap.appendChild(voice);
      line.appendChild(voiceWrap);

      inputs[name] = { costume: costume, voice: voice };
      table.appendChild(line);
    });
    card.appendChild(table);

    var tools = el("div", "vnew-cast-tools");

    var readBtn = el("button", "outline-button", "의상 읽기");
    readBtn.type = "button";
    readBtn.title = "원작 패널에서 이 장면의 의상·목소리를 읽습니다(무료에 가까운 텍스트 비용).";
    readBtn.addEventListener("click", function () {
      readBtn.disabled = true;
      api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/cast-notes", { method: "POST" })
        .then(function (payload) {
          state.plan = (payload && payload.plan) || state.plan;
          loadPlan(false);
        })
        .catch(function (err) {
          readBtn.disabled = false;
          if (showError) showError("의상을 읽지 못했습니다: " + errMessage(err));
        });
    });
    tools.appendChild(readBtn);

    var saveBtn = el("button", "primary-button", "저장");
    saveBtn.type = "button";
    saveBtn.addEventListener("click", function () {
      var overrides = {};
      Object.keys(inputs).forEach(function (name) {
        var c = (inputs[name].costume.value || "").trim();
        var v = (inputs[name].voice.value || "").trim();
        var entry = {};
        if (c) entry.costume = c;
        if (v) entry.voice = v;
        if (entry.costume || entry.voice) overrides[name] = entry;
      });
      saveBtn.disabled = true;
      api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/input", {
        method: "PUT",
        body: JSON.stringify({ castOverrides: overrides })
      })
        .then(function (payload) {
          state.plan = payload || state.plan;
          loadPlan(false);
        })
        .catch(function (err) {
          saveBtn.disabled = false;
          if (showError) showError("저장하지 못했습니다: " + errMessage(err));
        });
    });
    tools.appendChild(saveBtn);
    card.appendChild(tools);
    return card;
  }

  /* 통 테이크 타임라인(설계자 2026-10-26, ⑤): 테이크마다 한 줄 — 컷 범위·초,
     프리비즈 미리보기(테이크 녹화)·웹툰 결과 칸·실사 칸("건너뜀"). 보낸 프롬프트 접이식.
     gen(선택, vnew-live-fx C.11): 실사 회차(gen.realRun)가 있으면 실사 칸을 실제 상태로 그린다(takeRow). */
  function takeTimeline(units, showError, gen) {
    var section = el("section", "panel vnew-seg-timeline");
    section.appendChild(el("h2", "vnew-sec-title", "테이크별 진행"));
    if (!units.length) {
      section.appendChild(el("p", "vnew-hint", "테이크가 없습니다."));
      return section;
    }
    section.appendChild(
      el("p", "vnew-hint", "프리비즈 녹화를 통째(≤30초)로 넣어 웹툰 그림체를 한 번에 만듭니다. 테이크가 여러 개면 끝난 뒤 순서대로 이어 붙입니다.")
    );
    var list = el("div", "vnew-seg-rows");
    units.forEach(function (unit, index) {
      list.appendChild(takeRow(unit, index, showError, gen));
    });
    section.appendChild(list);
    return section;
  }

  function takeRow(unit, index, showError, gen) {
    var row = el("div", "vnew-seg-row");
    var meta = el("div", "vnew-seg-meta");
    meta.appendChild(el("span", "vnew-seg-no", "테이크 " + (index + 1)));
    var startNo = unit.startCutNo;
    var endNo = unit.endCutNo;
    var cutLabel = "";
    if (startNo != null && endNo != null) {
      cutLabel = (startNo === endNo) ? ("컷 " + startNo) : ("컷 " + startNo + "–" + endNo);
    } else {
      cutLabel = "컷 " + ((unit.cutIds || []).length) + "개";
    }
    meta.appendChild(el("span", "vnew-seg-range", cutLabel));
    var seconds = Math.round((Number(unit.seconds) || 0) * 10) / 10;
    meta.appendChild(el("span", "vnew-seg-seconds", "(" + seconds + "초)"));
    row.appendChild(meta);

    var cells = el("div", "vnew-seg-cells has-previz");
    cells.appendChild(previzPreviewCell(unit));
    // 웹툰 결과 칸.
    cells.appendChild(cutCell(unit, { key: "webtoon", title: GEN_WEBTOON_TITLE }, showError));
    // 실사 회차(gen.realRun)가 있으면 실사 칸도 실제 상태(배지·영상·실패 문구·다시 시도)로 그린다(vnew-live-fx RC9.2).
    if (gen && gen.realRun) {
      cells.appendChild(cutCell(unit, { key: "real", title: "실사" }, showError));
      row.appendChild(cells);
      return row;
    }
    // 실사 칸은 "건너뜀" 안내만.
    var realCell = el("div", "vnew-seg-cell");
    var realHead = el("div", "vnew-seg-cell-head");
    realHead.appendChild(el("span", "vnew-seg-cell-title", "실사"));
    realHead.appendChild(el("span", "vnew-badge-gen is-skipped", "건너뜀"));
    realCell.appendChild(realHead);
    var note = (unit.real || {}).note || "실사는 웹툰이 끝나면 기본 모델(Seedance)로 이어서 만듭니다.";
    realCell.appendChild(el("p", "vnew-hint", note));
    cells.appendChild(realCell);
    row.appendChild(cells);
    return row;
  }

  /* 촬영 지시서 카드(설계자 2026-10-22, ⑤). 진행 표시 → 완료되면 장소 SETTING 1회 + 컷마다 접이식.
     [촬영 지시서 다시 만들기](POST /previz/brief). */
  function shotBriefCard(previz, showError) {
    var brief = (previz || {}).shotBrief || {};
    var card = el("section", "panel vnew-brief-card");
    var head = el("div", "vnew-sec-head");
    head.appendChild(el("h2", "vnew-sec-title", "촬영 지시서"));
    head.appendChild(
      el("span", "vnew-badge-gen is-" + briefBadge(brief.status), briefStatusLabel(brief.status))
    );
    card.appendChild(head);
    card.appendChild(
      el("p", "vnew-hint", "영상 모델에게 보낼 촬영 전문 언어(샷·블로킹·연기·배경·대사)입니다. 프리비즈를 다시 승인하면 다시 만듭니다.")
    );

    if (brief.status === "running") {
      var track = el("div", "vnew-progress-track");
      track.appendChild(el("div", "vnew-progress-fill"));
      card.appendChild(track);
      card.appendChild(el("p", "vnew-hint", "촬영 지시서를 만드는 중입니다…"));
    } else if (brief.status === "failed") {
      card.appendChild(el("p", "login-error", brief.error || "촬영 지시서 작성에 실패했습니다."));
    } else if (brief.status === "ready") {
      var sceneMap = brief.scene || {};
      Object.keys(sceneMap).forEach(function (locId) {
        var sc = el("div", "vnew-brief-scene");
        sc.appendChild(el("div", "vnew-brief-label", "SETTING (장소)"));
        sc.appendChild(el("p", "vnew-brief-text", sceneMap[locId] || ""));
        card.appendChild(sc);
      });
      var cuts = brief.cuts || {};
      var cutIds = Object.keys(cuts);
      cutIds.forEach(function (cutId, idx) {
        card.appendChild(briefCutRow(cutId, idx, cuts[cutId]));
      });
    } else {
      card.appendChild(el("p", "vnew-hint", "아직 촬영 지시서가 없습니다."));
    }

    var regen = el("button", "outline-button", "촬영 지시서 다시 만들기");
    regen.type = "button";
    if (brief.status === "running") regen.disabled = true;
    regen.addEventListener("click", function () {
      regen.disabled = true;
      api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/previz/brief", { method: "POST" })
        .then(function (payload) {
          state.plan = (payload && payload.plan) || state.plan;
          loadPlan(false);
        })
        .catch(function (err) {
          regen.disabled = false;
          if (showError) showError("다시 만들지 못했습니다: " + errMessage(err));
        });
    });
    card.appendChild(regen);
    return card;
  }

  function briefBadge(status) {
    if (status === "ready") return "completed";
    if (status === "running") return "preparing";
    if (status === "failed") return "failed";
    return "waiting";
  }

  function briefStatusLabel(status) {
    var map = { ready: "완료", running: "만드는 중", failed: "실패", none: "없음" };
    return map[status] || "없음";
  }

  /* 컷 하나의 촬영 지시서(접이식): SHOT·BLOCKING·ACTION·MOOD·BACKGROUND·대사(영어 그대로 + 한국어 라벨). */
  function briefCutRow(cutId, idx, brief) {
    brief = brief || {};
    var details = el("details", "vnew-brief-cut");
    var summary = el("summary", "vnew-brief-cut-summary", "컷 " + (idx + 1) + " (" + cutId + ")");
    details.appendChild(summary);
    var body = el("div", "vnew-brief-cut-body");
    var fields = [
      ["SHOT (카메라)", brief.shotEn],
      ["BLOCKING (배치)", brief.blockingEn],
      ["ACTION (연기)", brief.actionEn],
      ["MOOD (분위기)", brief.moodEn],
      ["BACKGROUND (배경)", brief.backgroundEn]
    ];
    fields.forEach(function (f) {
      if (!f[1]) return;
      var row = el("div", "vnew-brief-field");
      row.appendChild(el("span", "vnew-brief-label", f[0]));
      row.appendChild(el("p", "vnew-brief-text", f[1]));
      body.appendChild(row);
    });
    var dlg = brief.dialogue || [];
    if (dlg.length) {
      var drow = el("div", "vnew-brief-field");
      drow.appendChild(el("span", "vnew-brief-label", "DIALOGUE (대사)"));
      dlg.forEach(function (d) {
        var line = (d.speaker || "") + ': "' + (d.lineKo || "") + '"' + (d.deliveryEn ? " — " + d.deliveryEn : "");
        drow.appendChild(el("p", "vnew-brief-text", line));
      });
      body.appendChild(drow);
    }
    details.appendChild(body);
    return details;
  }

  /* 컷 단위 타임라인(설계자 2026-10-22, ⑤): 컷마다 한 줄 — 번호·초, 프리비즈 미리보기·실사·웹툰. */
  function cutTimeline(units, showError) {
    var section = el("section", "panel vnew-seg-timeline");
    section.appendChild(el("h2", "vnew-sec-title", "컷별 진행"));
    if (!units.length) {
      section.appendChild(el("p", "vnew-hint", "컷이 없습니다."));
      return section;
    }
    // 안내 한 줄: 녹화 구간 n 개를 컷 m 개로 나눠 컷마다 만들고 이어 붙인다.
    var segIds = {};
    units.forEach(function (u) {
      var sid = u.recSegId;
      if (sid) segIds[sid] = true;
    });
    var n = Object.keys(segIds).length;
    var m = units.length;
    section.appendChild(
      el(
        "p",
        "vnew-hint",
        "프리비즈 녹화 " + n + "개 구간을 컷 " + m + "개로 나눠 컷마다 영상을 만들고, 끝나면 순서대로 이어 붙입니다."
      )
    );
    var list = el("div", "vnew-seg-rows");
    units.forEach(function (unit, index) {
      list.appendChild(cutRow(unit, index, showError));
    });
    section.appendChild(list);
    return section;
  }

  function cutRow(unit, index, showError) {
    var row = el("div", "vnew-seg-row");
    var meta = el("div", "vnew-seg-meta");
    // 콘티 컷 번호(cutNo)가 있으면 그것, 없으면 표 순서(index+1).
    var cutNo = (unit.cutNo != null) ? unit.cutNo : (index + 1);
    var start = Math.round((Number(unit.startSec) || 0) * 10) / 10;
    var end = Math.round((Number(unit.endSec) || 0) * 10) / 10;
    var seconds = Math.round((Number(unit.seconds) || 0) * 10) / 10;
    meta.appendChild(el("span", "vnew-seg-no", "컷 " + cutNo));
    meta.appendChild(el("span", "vnew-seg-range", start + "–" + end + "초"));
    meta.appendChild(el("span", "vnew-seg-seconds", "(" + seconds + "초)"));
    // 3초 미만 컷은 영상 모델 최소 길이(3초)로 채워 만든다는 작은 안내.
    if (seconds > 0 && seconds < 3) {
      meta.appendChild(el("span", "vnew-seg-fill-hint", "3초로 채워 생성"));
    }
    row.appendChild(meta);

    var cells = el("div", "vnew-seg-cells has-previz");
    cells.appendChild(previzPreviewCell(unit));
    GEN_VARIANTS.forEach(function (variant) {
      cells.appendChild(cutCell(unit, variant, showError));
    });
    row.appendChild(cells);
    return row;
  }

  function cutCell(unit, variant, showError) {
    var v = (unit[variant.key] || {});
    var cell = el("div", "vnew-seg-cell");
    var head = el("div", "vnew-seg-cell-head");
    head.appendChild(el("span", "vnew-seg-cell-title", variant.title));
    head.appendChild(
      el("span", "vnew-badge-gen is-" + (v.status || "waiting"), genStatusLabel(v.status || "waiting"))
    );
    cell.appendChild(head);
    var fallbackLine = variant.key === "real" ? realFallbackText(v, unit) : ""; // 작업 130: likeness 단계 문구
    if (fallbackLine) cell.appendChild(el("p", "vnew-hint vnew-real-fallback", fallbackLine));
    if (v.contentFallback) cell.appendChild(el("p", "vnew-hint vnew-content-fallback", "콘텐츠 검사로 Seedance 로 만들었습니다")); // 작업 153
    if (v.animeFallback && v.animeFallback.text) cell.appendChild(el("p", "vnew-hint vnew-anime-fallback", v.animeFallback.text)); // 작업 228
    if (v.animeKling && v.animeKling.text) cell.appendChild(el("p", "vnew-hint vnew-anime-kling", v.animeKling.text)); // 작업 229
    if (variant.key === "real") {
      realCutQaLines(v).forEach(function (line) { cell.appendChild(el("p", "vnew-hint vnew-real-cut-qa", line)); }); // 작업 133
    }

    if (v.status === "completed" && v.videoUrl) {
      var video = el("video", "vnew-seg-video");
      video.src = v.videoUrl;
      video.controls = true;
      video.setAttribute("playsinline", "");
      cell.appendChild(video);
    } else if (v.status === "failed" || v.status === "cancelled") {
      // 실패·취소된 컷은 [다시 시도]로 그 컷만 다시 만든다(설계자 2026-10-22, ⑤).
      if (v.status === "failed") {
        cell.appendChild(el("p", "login-error vnew-seg-error", v.error || "실패했습니다."));
      } else {
        cell.appendChild(el("p", "vnew-hint vnew-seg-error", "취소됨"));
      }
      var retry = el("button", "outline-button vnew-seg-retry", variant.key === "real" ? "실사 다시" : "웹툰 다시");
      retry.type = "button";
      retry.addEventListener("click", function () {
        retry.disabled = true;
        api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/generate/retry", {
          method: "POST",
          body: JSON.stringify({ segId: unit.id, variant: variant.key })
        })
          .then(function () {
            loadPlan(false);
          })
          .catch(function (err) {
            retry.disabled = false;
            if (showError) showError("다시 시도하지 못했습니다: " + errMessage(err));
          });
      });
      cell.appendChild(retry);
      // 소리 검사 거절(failureKind "audio", vnew-live-fx RC6)이면 그 칸만 소리 없이(generate_audio false) 다시 만든다.
      // 웹툰·실사 모두이고 VNEW_TAKE_REAL 과 상관없이 보인다(결함 수정, R0.3 예외).
      if (v.status === "failed" && v.failureKind === "audio") {
        var silent = el("button", "outline-button vnew-seg-retry vnew-seg-silent", "소리 없이 다시 만들기");
        silent.type = "button";
        silent.addEventListener("click", function () {
          silent.disabled = true;
          retry.disabled = true;
          api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/generate/retry", {
            method: "POST",
            body: JSON.stringify({ segId: unit.id, variant: variant.key, audio: false })
          })
            .then(function () {
              loadPlan(false);
            })
            .catch(function (err) {
              silent.disabled = false;
              retry.disabled = false;
              if (showError) showError("다시 시도하지 못했습니다: " + errMessage(err));
            });
        });
        cell.appendChild(silent);
      }
    }

    // 보낸 지시서 보기(접이식) — promptEn 이 있으면.
    if (v.promptEn) {
      var details = el("details", "vnew-prompt-details");
      var summary = el("summary", "vnew-prompt-summary", "보낸 지시서 보기" + (v.promptBytes ? " (" + v.promptBytes + " bytes)" : ""));
      details.appendChild(summary);
      var pre = el("pre", "vnew-prompt-pre", v.promptEn);
      details.appendChild(pre);
      cell.appendChild(details);
    }
    return cell;
  }

  /* 맨 위 결과 두 열: finalReal · finalWebtoon. 둘 다 done 이면 각 열 아래 [이 영상으로 사운드 편집]. */
  function finalResults(gen, showError) {
    var section = el("section", "vnew-final-grid");
    // 통 테이크(vnew-live-fx 묶음 C, design §C.11): 실사 열은 takeRealColumn(take 이고 realEnabled 또는 realRun 일 때),
    // 웹툰 열은 done 이고 gen 이 진행 중이 아니면 [이걸로 고르기], 두 final 이 done 이면 아래에 나란히 보기(takeCompare).
    var isTake = gen.mode === "previz-take";
    var bothReady =
      !isTake && (gen.finalReal || {}).status === "done" && (gen.finalWebtoon || {}).status === "done";

    GEN_VARIANTS.forEach(function (meta) {
      if (isTake && meta.key === "real" && takeRealShown(gen)) {
        section.appendChild(takeRealColumn(gen, showError));
        return;
      }
      var final = (meta.key === "real" ? gen.finalReal : gen.finalWebtoon) || {};
      var col = el("div", "vnew-final-col");
      col.appendChild(el("h2", "vnew-variant-title", meta.title));

      if (final.status === "done" && final.videoUrl) {
        var video = el("video", "vnew-final-player");
        video.src = final.videoUrl;
        video.controls = true;
        video.setAttribute("playsinline", "");
        col.appendChild(video);
        var tools = el("div", "vnew-final-tools");
        tools.appendChild(downloadLink(final.videoUrl, "내려받기"));
        col.appendChild(tools);
        if (bothReady) {
          var pick = el("button", "primary-button vnew-pick-btn", "이 영상으로 사운드 편집");
          pick.type = "button";
          pick.addEventListener("click", function () {
            pick.disabled = true;
            api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/select", {
              method: "POST",
              body: JSON.stringify({ variant: meta.key })
            })
              .then(function (payload) {
                state.plan = payload.plan || state.plan;
                state.planStep = 6;
                loadPlan(false);
              })
              .catch(function (err) {
                pick.disabled = false;
                if (showError) showError("고르지 못했습니다: " + errMessage(err));
              });
          });
          col.appendChild(pick);
        } else if (isTake && meta.key === "webtoon" && gen.status !== "running") {
          col.appendChild(takePickButton("webtoon", showError));
        }
      } else if (final.status === "failed") {
        col.appendChild(el("p", "login-error", final.error || "이어 붙이기에 실패했습니다."));
      } else if (final.status === "skipped") {
        var skip = el("div", "vnew-final-pending");
        skip.appendChild(
          el("p", "vnew-hint", final.note || "실사는 웹툰이 끝나면 기본 모델(Seedance)로 이어서 만듭니다.")
        );
        col.appendChild(skip);
      } else {
        var place = el("div", "vnew-final-pending");
        place.appendChild(el("p", "vnew-hint", "모든 구간이 끝나면 자동으로 이어 붙입니다."));
        col.appendChild(place);
      }
      section.appendChild(col);
    });
    if (isTake) {
      var compare = takeCompare(gen);
      if (compare) section.appendChild(compare);
    }
    return section;
  }

  /* ---- 통 테이크 실사 버전(vnew-live-fx 묶음 C, RC5·RC9 · design §C.11) ----

     ⑤ take gen 결과의 실사 열·확인창·나란히 보기. 서버 gen_view 의 realEnabled·realRun·realHistory·realEstimate·
     canStartReal·realBlock 을 그대로 쓴다. 실사 열은 take 이면서 (realEnabled 또는 realRun) 일 때만 그린다. 설정을 껐고
     회차가 없으면 지금 화면("건너뜀")이고, 회차를 시작한 뒤 설정을 꺼도 그 결과는 보인다(R0.5).
     확인창에는 금액 숫자를 보인다(결정 1). 409 CostMismatch 면 계획을 다시 읽어 새 금액을 확인창에 보이고, 다시 눌러
     새 금액으로 확인해야 보낸다(캐스팅 화면과 같은 방식). 영상 src 는 응답의 presigned URL 만 쓰고, 글은 모두
     textContent(el)로 넣는다. 아래 순수 함수와 DOM 조립은 검사 lr-n·lr-o 가 node 가짜 DOM 으로 실행한다. */
  var TAKE_REAL_LEAD = "같은 프리비즈 녹화로 실사 버전을 만들 수 있습니다. 웹툰 결과는 그대로 남습니다.";
  var TAKE_REAL_DONE_TEXT = "웹툰 영상이 준비됐습니다. 실사 버전을 만들거나 웹툰을 골라 사운드로 넘어가세요.";
  var TAKE_REAL_HISTORY_STATUS = { done: "완료", partial: "일부 실패", failed: "실패", cancelled: "취소됨" };

  /* ---- 작업 123: 처음부터 실사도 함께(⑤ 시작 확인창 "만들 영상" 체크 2개 · 결과 실사 열 자동 시작 안내) ---- */
  var TAKE_REAL_AUTO_TEXT = "애니가 끝나면 실사를 자동으로 시작합니다";

  /* 자동 시작을 기다리는 중: 회차 없음 · gen 진행 중 · 서버 autoReal 이 아직 시작·오류 전. 오류·멈춤이면 지금 버튼이 보인다. */
  function takeRealAutoPending(gen) {
    var g = gen || {};
    var auto = g.autoReal;
    return !g.realRun && g.status === "running" && !!auto && typeof auto === "object" && !auto.startedAt && !auto.error;
  }

  function takeUsdSum(a, b) {
    return Math.round(((Number(a) || 0) + (Number(b) || 0)) * 100) / 100;
  }

  /* [실사] 체크를 흐리게 할 이유: 서버 estimate.realBlock(realBlock 문구), 금액이 없으면(옛 서버) 기본 문구. 되면 "". */
  function genRealBlockOf(est) {
    var e = est || {};
    if (typeof e.realBlock === "string" && e.realBlock) return e.realBlock;
    return Number(e.realUsd) > 0 ? "" : "지금은 실사 버전을 만들 수 없습니다.";
  }

  /* "애니 $A + 실사 $B = $합계" · 실사를 안 고르면 "애니 $A". */
  function genStylesCostText(est, real) {
    var e = est || {};
    if (!real) return "애니 " + takeRealUsdText(e.totalUsd);
    return "애니 " + takeRealUsdText(e.totalUsd) + " + 실사 " + takeRealUsdText(e.realUsd) + " = " +
      takeRealUsdText(takeUsdSum(e.totalUsd, e.realUsd));
  }

  /* 체크 2개: [애니(웹툰)](항상 켬·끌 수 없음) · [실사](기본 켬, 못 만들면 흐리고 이유를 title). 반환 {node, real()}. */
  function genStylesBox(est, onChange) {
    var box = el("div", "vnew-gen-styles");
    box.appendChild(el("span", "vnew-field-label", "만들 영상"));
    var webLabel = el("label", "vnew-gen-style");
    var webBox = el("input");
    webBox.type = "checkbox";
    webBox.checked = true;
    webBox.disabled = true;
    webLabel.title = GEN_WEBTOON_TITLE + "는 항상 만듭니다.";
    webLabel.appendChild(webBox);
    webLabel.appendChild(el("span", null, GEN_WEBTOON_TITLE));
    box.appendChild(webLabel);
    var block = genRealBlockOf(est);
    var realLabel = el("label", "vnew-gen-style" + (block ? " is-off" : ""));
    var realBox = el("input");
    realBox.type = "checkbox";
    realBox.checked = !block;
    realBox.disabled = !!block;
    if (block) {
      realLabel.title = block;
      realBox.title = block;
    }
    realBox.addEventListener("change", function () {
      if (onChange) onChange();
    });
    realLabel.appendChild(realBox);
    realLabel.appendChild(el("span", null, "실사"));
    box.appendChild(realLabel);
    return {
      node: box,
      real: function () {
        return !realBox.disabled && realBox.checked === true;
      }
    };
  }

  /* 실사 열을 그리는가: take 이면서 설정 켬(realEnabled) 또는 이미 시작한 회차(realRun)가 있을 때. */
  function takeRealShown(gen) {
    var g = gen || {};
    return g.mode === "previz-take" && (g.realEnabled === true || !!g.realRun);
  }

  function takeRealUsdText(value) {
    var usd = Number(value);
    return "$" + (isFinite(usd) && usd > 0 ? usd : 0).toFixed(2);
  }

  function takeRealSecondsText(value) {
    var secs = Number(value);
    return (isFinite(secs) && secs > 0 ? Math.round(secs * 10) / 10 : 0) + "초";
  }

  /* [실사 버전 만들기 ($X)] — 회차가 이미 있으면 [실사 버전 다시 만들기 ($X)]. 금액은 서버 realEstimate.usd. */
  function takeRealStartLabel(gen) {
    var g = gen || {};
    return (g.realRun ? "실사 버전 다시 만들기 (" : "실사 버전 만들기 (") + takeRealUsdText((g.realEstimate || {}).usd) + ")";
  }

  function takeRealConfirmText(est) {
    var e = est || {};
    var takes = Number(e.takes);
    takes = isFinite(takes) && takes > 0 ? Math.floor(takes) : 0;
    return "같은 프리비즈 녹화로 실사 버전을 만듭니다. 테이크 " + takes + "개 · " + takeRealSecondsText(e.seconds) + " · " +
      String(e.resolution || "480p") + " 초안 · 예상 비용 " + takeRealUsdText(e.usd) + ". 웹툰 결과는 그대로 남습니다.";
  }

  function takeRealCostChangedText(est) {
    return "금액이 바뀌었습니다. 새 금액은 " + takeRealUsdText((est || {}).usd) + "입니다. 다시 누르면 새 금액으로 확인합니다.";
  }

  /* 테이크 실사 칸 세기: total·completed·failed·cancelled·active(queued·preparing·submitted). */
  function takeRealCounts(gen) {
    var units = Array.isArray((gen || {}).units) ? gen.units : [];
    var counts = { total: units.length, completed: 0, failed: 0, cancelled: 0, active: 0 };
    units.forEach(function (unit) {
      var status = ((unit || {}).real || {}).status;
      if (status === "completed") counts.completed += 1;
      else if (status === "failed") counts.failed += 1;
      else if (status === "cancelled") counts.cancelled += 1;
      else if (status === "queued" || status === "preparing" || status === "submitted") counts.active += 1;
    });
    return counts;
  }

  /* 실사 열 상태: none(회차 없음) · running(만드는 중·이어 붙이는 중) · done · failed · cancelled.
     작업 221: 만드는 칸(active)이 하나라도 있으면 gen.status(partial·멈춤 보기 등)와 상관없이 running —
     테이크별 [실사 다시] 중에 "멈췄습니다"·[실사 버전 다시 만들기 ($X)](회차 전체 다시 계상)를 보이지 않는다. */
  function takeRealState(gen) {
    var g = gen || {};
    if (!g.realRun) return "none";
    var counts = takeRealCounts(g);
    if (counts.active > 0) return "running";
    var final = g.finalReal || {};
    if (final.status === "done") return "done";
    if (final.status === "failed") return "failed";
    if (g.status === "running" && counts.completed === counts.total) return "running";
    return counts.cancelled > 0 && counts.failed === 0 ? "cancelled" : "failed";
  }

  /* 진행: 끝난(completed) 테이크 / 전체 테이크. */
  function takeRealProgress(gen) {
    var counts = takeRealCounts(gen);
    return {
      done: counts.completed,
      total: counts.total,
      text: "실사 버전을 만드는 중… (" + counts.completed + "/" + counts.total + ")"
    };
  }

  /* failed·cancelled 문구. 칸별 다시 시도·소리 없이 다시 만들기는 아래 테이크 행(cutCell)에 있다. */
  function takeRealFailedText(gen) {
    var g = gen || {};
    var final = g.finalReal || {};
    if (final.status === "failed") return final.error || "실사 테이크를 이어 붙이지 못했습니다.";
    var counts = takeRealCounts(g);
    if (counts.cancelled > 0 && counts.failed === 0) {
      return "실사 버전 만들기를 취소했습니다. 아래 테이크 행에서 다시 시도할 수 있습니다.";
    }
    // 실패 칸 없이 끝난 회차 = 30분 넘게 진행이 없어 보기에서 멈춤(partial)으로 본 경우(_reconcile_gen_stale).
    if (counts.failed === 0) return "실사 버전 만들기가 멈췄습니다. 다시 만들어 주세요.";
    return "실사 테이크 " + counts.failed + "개를 만들지 못했습니다. 아래 테이크 행에서 다시 시도할 수 있습니다.";
  }

  /* 이전 회차 한 줄: "2회차 · 완료 · 소리 없음". */
  function takeRealHistoryLabel(item) {
    var it = item || {};
    var n = Number(it.n);
    var parts = [(isFinite(n) && n > 0 ? Math.floor(n) : 0) + "회차", TAKE_REAL_HISTORY_STATUS[it.status] || "기록"];
    if (it.audio === false) parts.push("소리 없음");
    return parts.join(" · ");
  }

  /* 상태 띠: take 웹툰이 done 이고 실사 회차가 없으면(실사 열을 그릴 때) 안내 문구, 그 밖에는 ""(기존 문구를 쓴다). */
  function takeStatusText(gen) {
    var g = gen || {};
    return g.status === "done" && !g.realRun && takeRealShown(g) ? TAKE_REAL_DONE_TEXT : "";
  }

  /* 통 테이크 안내의 실사 한 줄(095 피드백 1, 2026-10-30): 생성 확인창 · ⑤ 시작 화면 배너 · ⑤ 결과 화면 배너.
     실사 버전 설정을 아는 값은 gen.realEnabled(gen_view) → plan.features.takeReal(design §2 의 키) 순으로 boolean 일 때만 쓰고,
     둘 다 없으면 null 이다. 지금 서버는 gen 이 없으면({status:"none", estimate}) realEnabled 를 보내지 않고 features 에는
     fx 만 있어, 첫 시작 화면·첫 확인창은 null 이다. */
  var TAKE_PLAN_SKIP_TEXT = "실사는 건너뜁니다";
  var TAKE_PLAN_LATER_TEXT = "실사는 웹툰이 끝난 뒤 따로 만듭니다";

  function takeRealEnabledOf(gen, plan) {
    var g = gen || {};
    if (typeof g.realEnabled === "boolean") return g.realEnabled;
    var features = (plan || {}).features || {};
    return typeof features.takeReal === "boolean" ? features.takeReal : null;
  }

  /* where: "confirm"(생성 확인창, amount = 테이크 수) · "start"(⑤ 시작 화면 배너, amount = 초) · 그 밖은 ⑤ 결과 화면 배너(초).
     enabled true → 웹툰이 끝나면 같은 녹화로 실사 버전을 만들 수 있다는 문구, false → 지금 문구 그대로,
     null(모름) → 지금 문구에서 TAKE_PLAN_SKIP_TEXT 만 TAKE_PLAN_LATER_TEXT 로 바꾼다. */
  function takePlanHintText(where, amount, enabled) {
    var lead = where === "confirm"
      ? "테이크 " + amount + "개로 만듭니다. "
      : "웹툰 그림체로 " + amount + "초를 한 번에 만듭니다 (480p 초안). ";
    if (enabled === true) {
      return lead + (where === "confirm"
        ? "웹툰이 끝나면 같은 프리비즈 녹화로 실사 버전을 따로 만들 수 있습니다."
        : "웹툰이 끝나면 같은 녹화로 실사 버전을 만들 수 있습니다.");
    }
    return lead + (enabled === false ? TAKE_PLAN_SKIP_TEXT : TAKE_PLAN_LATER_TEXT) +
      (where === "start" ? "." : " — 기본 모델로 따로 만들 수 있습니다.");
  }

  /* [이걸로 고르기] → POST /select. 받은 plan 으로 ⑥을 바로 그린다(loadPlan 을 거치면 decidePlanStep 이 partial gen 을
     ⑤로 되돌린다 — 실사가 실패해 partial 이어도 웹툰을 고를 수 있어야 한다, RC5.1). */
  function takePickButton(variant, showError) {
    var pick = el("button", "primary-button vnew-pick-btn", "이걸로 고르기");
    pick.type = "button";
    pick.addEventListener("click", function () {
      pick.disabled = true;
      api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/select", {
        method: "POST",
        body: JSON.stringify({ variant: variant })
      })
        .then(function (payload) {
          state.plan = (payload && payload.plan) || state.plan;
          state.planStep = 6;
          renderPlan();
        })
        .catch(function (err) {
          pick.disabled = false;
          if (showError) showError("고르지 못했습니다: " + errMessage(err));
        });
    });
    return pick;
  }

  /* 실사 열: 회차 없음(안내 + 시작 버튼 또는 realBlock) · 진행(막대) · done(영상·내려받기·고르기) · failed·cancelled(문구),
     끝난 상태에서는 [실사 버전 다시 만들기 ($X)](canStartReal), 아래에 접기 "이전 실사 보기 (n)". */
  function takeRealColumn(gen, showError) {
    var g = gen || {};
    var col = el("div", "vnew-final-col vnew-real-col");
    col.appendChild(el("h2", "vnew-variant-title", "실사"));
    var castLine = takeRealCastText((g.realRun || {}).cast); // 작업 126: 인물 바꾸기 회차 머리
    if (castLine) col.appendChild(el("p", "vnew-hint vnew-real-cast", castLine));
    var stateName = takeRealState(g);
    var final = g.finalReal || {};
    var autoPending = takeRealAutoPending(g); // 작업 123: 처음부터 실사도 골랐고 애니(웹툰)가 아직 진행 중
    if (stateName === "none") {
      col.appendChild(autoPending ? el("p", "vnew-hint vnew-real-auto", TAKE_REAL_AUTO_TEXT)
        : el("p", "vnew-hint vnew-real-lead", TAKE_REAL_LEAD));
    } else if (stateName === "running") {
      var progress = takeRealProgress(g);
      var bar = el("div", "vnew-real-progress");
      bar.appendChild(el("p", "vnew-progress-label", progress.text));
      var track = el("div", "vnew-progress-track");
      var fill = el("div", "vnew-real-progress-fill");
      fill.style.width = Math.max(5, progress.total ? Math.round((100 * progress.done) / progress.total) : 0) + "%";
      track.appendChild(fill);
      bar.appendChild(track);
      col.appendChild(bar);
    } else if (stateName === "done") {
      if (final.videoUrl) {
        var video = el("video", "vnew-final-player");
        video.src = final.videoUrl;
        video.controls = true;
        video.setAttribute("playsinline", "");
        col.appendChild(video);
        var tools = el("div", "vnew-final-tools");
        tools.appendChild(downloadLink(final.videoUrl, "내려받기"));
        col.appendChild(tools);
      } else {
        col.appendChild(el("p", "vnew-hint vnew-real-error", "영상을 불러오지 못했습니다."));
      }
      if (g.status !== "running") col.appendChild(takePickButton("real", showError));
    } else {
      col.appendChild(
        el("p", (stateName === "cancelled" ? "vnew-hint" : "login-error") + " vnew-real-error", takeRealFailedText(g))
      );
    }
    if (stateName !== "running") {
      if (g.canStartReal) {
        var startBtn = el(
          "button",
          (stateName === "none" ? "primary-button" : "outline-button") + " vnew-real-start",
          takeRealStartLabel(g)
        );
        startBtn.type = "button";
        startBtn.addEventListener("click", function () {
          openRealConfirm(g, showError);
        });
        col.appendChild(startBtn);
        if (stateName !== "none") {
          // 작업 126: 실사 결과 아래 [인물 바꿔 다시 만들기] — 인물마다 캐릭터·모습(실사|애니)을 고른 뒤 금액 확인창으로.
          var castBtn = el("button", "outline-button vnew-real-cast-btn", TAKE_REAL_CAST_BUTTON);
          castBtn.type = "button";
          castBtn.addEventListener("click", function () {
            openRealCastPicker(g, showError);
          });
          col.appendChild(castBtn);
        }
      } else if (stateName === "none" && !autoPending) {
        col.appendChild(el("p", "vnew-hint vnew-real-block", g.realBlock || "지금은 실사 버전을 만들 수 없습니다."));
      }
    }
    var history = takeRealHistory(g);
    if (history) col.appendChild(history);
    return col;
  }

  /* 접기 "이전 실사 보기 (n)": realHistory(최대 3, 최근 것부터). 영상은 finalUrl(presigned)만, 누를 때까지 받지 않는다. */
  function takeRealHistory(gen) {
    var items = (Array.isArray((gen || {}).realHistory) ? gen.realHistory : []).filter(function (item) {
      return !!item && typeof item === "object";
    });
    if (!items.length) return null;
    var details = el("details", "vnew-real-history");
    details.appendChild(el("summary", null, "이전 실사 보기 (" + items.length + ")"));
    items.slice().reverse().forEach(function (item) {
      var box = el("div", "vnew-real-history-item");
      box.appendChild(el("p", "vnew-real-history-label", takeRealHistoryLabel(item)));
      var castText = takeRealCastText(item.cast);
      if (castText) box.appendChild(el("p", "vnew-hint vnew-real-cast", castText));
      if (item.finalUrl) {
        var video = el("video", "vnew-final-player");
        video.src = item.finalUrl;
        video.controls = true;
        video.preload = "none";
        video.setAttribute("playsinline", "");
        box.appendChild(video);
      } else {
        box.appendChild(el("p", "vnew-hint", "완성된 영상이 없습니다."));
      }
      details.appendChild(box);
    });
    return details;
  }

  /* ---- 작업 126: 실사 속 인물 바꿔 다시 만들기 ----

     ⑤ 실사 결과 아래 [인물 바꿔 다시 만들기] → 작은 창(GET /generate/real/cast): 이 테이크 인물마다 캐릭터(기본 = 지금 캐릭터,
     소유자 캐릭터 목록에서 바꿈)와 모습 [실사]|[애니]. 고를 수 없는 캐릭터·모습은 흐리게(is-off·disabled) 두고 이유를 title 로.
     [다음] → 지금 실사 확인창(openRealConfirm, realEstimate 금액·409 CostMismatch 다시 읽기)에 cast 를 실어 보낸다.
     글은 모두 textContent(el)·속성으로만 넣는다. */
  var TAKE_REAL_CAST_BUTTON = "인물 바꿔 다시 만들기";
  var TAKE_REAL_CAST_LEAD = "인물마다 캐릭터와 모습을 고르세요. 이 회차만 바뀌고 대본·프리비즈·지난 회차는 그대로 남습니다.";
  var TAKE_REAL_CAST_LOOKS = [
    { key: "real", title: "실사" },
    { key: "anime", title: "애니" }
  ];

  /* "인물: 에이→철수(애니), 비→비(실사)". cast 가 없거나 비면 "". */
  function takeRealCastText(cast) {
    if (!Array.isArray(cast) || !cast.length) return "";
    var parts = [];
    cast.forEach(function (item) {
      if (!item || typeof item !== "object") return;
      var look = item.look === "anime" ? "애니" : "실사";
      parts.push(String(item.name || "") + "→" + String(item.charName || item.charId || "") + "(" + look + ")");
    });
    return parts.length ? "인물: " + parts.join(", ") : "";
  }

  /* 그 캐릭터의 그 모습을 고를 수 없는 이유(서버 realBlock·animeBlock). 목록에 없으면 기본 문구, 고를 수 있으면 "". */
  function realCastLookBlock(chars, charId, look) {
    var found = null;
    (Array.isArray(chars) ? chars : []).forEach(function (c) {
      if (c && c.id === charId) found = c;
    });
    if (!found) return "캐릭터 목록에 없습니다.";
    var reason = look === "anime" ? found.animeBlock : found.realBlock;
    return typeof reason === "string" ? reason : "";
  }

  /* 두 모습 모두 못 고르면 그 이유(캐릭터를 흐리게), 하나라도 되면 "". */
  function realCastCharBlock(chars, charId) {
    var real = realCastLookBlock(chars, charId, "real");
    var anime = realCastLookBlock(chars, charId, "anime");
    if (!real || !anime) return "";
    return real === anime ? real : "실사: " + real + " / 애니: " + anime;
  }

  function openRealCastPicker(gen, showError) {
    var pid = state.projectId;
    var scrim = el("div", "vnew-modal-scrim");
    var modal = el("div", "vnew-modal vnew-real-cast-picker");
    var head = el("div", "vnew-modal-head");
    head.appendChild(el("h3", null, TAKE_REAL_CAST_BUTTON));
    var close = el("button", "icon-button", "×");
    close.type = "button";
    close.addEventListener("click", closeModal);
    head.appendChild(close);
    modal.appendChild(head);
    var bodyWrap = el("div", "vnew-modal-body");
    bodyWrap.appendChild(el("p", "vnew-hint", TAKE_REAL_CAST_LEAD));
    var list = el("div", "vnew-real-cast-list");
    list.appendChild(el("p", "vnew-hint", "인물과 캐릭터 목록을 불러오는 중…"));
    bodyWrap.appendChild(list);
    var message = planError("");
    bodyWrap.appendChild(message);
    var actions = el("div", "vnew-modal-actions");
    var cancelBtn = el("button", "outline-button", "취소");
    cancelBtn.type = "button";
    cancelBtn.addEventListener("click", closeModal);
    var nextBtn = el("button", "primary-button", "다음 (금액 확인)");
    nextBtn.type = "button";
    nextBtn.disabled = true;
    actions.appendChild(cancelBtn);
    actions.appendChild(nextBtn);
    bodyWrap.appendChild(actions);
    modal.appendChild(bodyWrap);
    scrim.appendChild(modal);
    scrim.addEventListener("click", function (event) {
      if (event.target === scrim) closeModal();
    });
    document.body.appendChild(scrim);

    var rows = [];
    function showMessage(text) {
      message.textContent = text;
      message.style.display = "";
    }
    function refreshNext() {
      nextBtn.disabled = !rows.length || rows.some(function (row) {
        return !row.charId || !!realCastLookBlock(row.chars, row.charId, row.look);
      });
    }

    function personRow(person, chars) {
      var row = { name: String(person.name || ""), charId: String(person.charId || ""), look: "real", chars: chars };
      var line = el("div", "vnew-real-cast-row");
      line.appendChild(el("span", "vnew-real-cast-name", row.name));
      var select = el("select", "vnew-real-cast-select");
      var options = chars.slice();
      if (row.charId && !options.some(function (c) { return c && c.id === row.charId; })) {
        options.unshift({ id: row.charId, name: person.charName || row.charId, realBlock: "캐릭터 목록에 없습니다.",
          animeBlock: "캐릭터 목록에 없습니다." });
        row.chars = options;
      }
      options.forEach(function (c) {
        var option = el("option", null, String(c.name || c.id || ""));
        option.value = String(c.id || "");
        var block = realCastCharBlock(row.chars, option.value);
        if (block) {
          option.disabled = true;
          option.title = block;
          option.className = "is-off";
        }
        if (option.value === row.charId) option.selected = true;
        select.appendChild(option);
      });
      select.value = row.charId;
      line.appendChild(select);
      var looks = el("div", "vnew-real-cast-looks");
      var lookBtns = {};
      TAKE_REAL_CAST_LOOKS.forEach(function (meta) {
        var btn = el("button", "outline-button vnew-real-cast-look", meta.title);
        btn.type = "button";
        btn.addEventListener("click", function () {
          if (btn.disabled) return;
          row.look = meta.key;
          paint();
        });
        lookBtns[meta.key] = btn;
        looks.appendChild(btn);
      });
      line.appendChild(looks);

      function paint() {
        // 고른 캐릭터에서 지금 모습을 못 쓰면 다른 모습으로 옮긴다(둘 다 안 되면 그대로 두고 [다음]을 막는다).
        if (realCastLookBlock(row.chars, row.charId, row.look)) {
          var other = row.look === "real" ? "anime" : "real";
          if (!realCastLookBlock(row.chars, row.charId, other)) row.look = other;
        }
        TAKE_REAL_CAST_LOOKS.forEach(function (meta) {
          var btn = lookBtns[meta.key];
          var block = realCastLookBlock(row.chars, row.charId, meta.key);
          btn.disabled = !!block;
          btn.title = block;
          btn.className = "outline-button vnew-real-cast-look" + (block ? " is-off" : "") +
            (row.look === meta.key && !block ? " is-on" : "");
        });
        refreshNext();
      }
      select.addEventListener("change", function () {
        row.charId = String(select.value || "");
        paint();
      });
      rows.push(row);
      paint();
      return line;
    }

    api("/api/vnew/p/" + encodeURIComponent(pid) + "/generate/real/cast")
      .then(function (payload) {
        clear(list);
        var people = Array.isArray((payload || {}).people) ? payload.people : [];
        var chars = (Array.isArray((payload || {}).characters) ? payload.characters : []).filter(function (c) {
          return !!c && typeof c === "object" && typeof c.id === "string";
        });
        if (!people.length) {
          list.appendChild(el("p", "vnew-hint", "이 테이크에 바꿀 인물이 없습니다."));
          return;
        }
        people.forEach(function (person) {
          if (person && typeof person === "object") list.appendChild(personRow(person, chars));
        });
        refreshNext();
      })
      .catch(function (err) {
        clear(list);
        showMessage("인물 목록을 불러오지 못했습니다: " + errMessage(err));
      });

    nextBtn.addEventListener("click", function () {
      if (nextBtn.disabled) return;
      var cast = rows.map(function (row) {
        var picked = null;
        row.chars.forEach(function (c) {
          if (c && c.id === row.charId) picked = c;
        });
        return { name: row.name, charId: row.charId, look: row.look, charName: String((picked || {}).name || row.charId) };
      });
      closeModal();
      openRealConfirm(gen, showError, cast);
    });

    function closeModal() {
      if (scrim.parentNode) scrim.parentNode.removeChild(scrim);
    }
    return scrim;
  }

  /* ---- 작업 130: 실사 likeness 거절 단계(⑤ 실사 행 문구·확인창 최대 금액) ---- */
  /* 확인창 안내: 서버 gen.realFallbackMax {usd, model}(컷 단위 Kling 을 쓸 수 없으면 없다) → 문구, 없으면 "". */
  function realFallbackMaxText(gen) {
    var max = (gen || {}).realFallbackMax;
    var usd = Number((max || {}).usd);
    if (!max || !(usd > 0)) return "";
    if (max.qa) return "거절되면 컷 단위 Kling으로 바뀌어 금액이 달라질 수 있음(QA로 다시 만든 컷 포함 최대 $" + usd.toFixed(2) + ")"; // 작업 133
    return "거절되면 컷 단위 Kling으로 바뀌어 금액이 달라질 수 있음(최대 $" + usd.toFixed(2) + ")";
  }

  /* 작업 133: 실사 칸 컷별 QA 결과(칸 fallback.qa [{no, ok, items, firstItems, retried}], 문제 있던 컷만) → 문구 목록.
     예 "컷 7: 옷이 다름(1번 다시 만듦)". 다시 만들어도 틀리면 끝에 ", 그래도 다름". 문제 없는 컷·목록 없음이면 []. */
  var REAL_CUT_QA_LABELS = { animeStyle: "그림체가 애니", outfit: "옷이 다름", location: "장소가 다름", people: "인물 수·위치가 다름" };
  function realCutQaLines(v) {
    var rows = (((v || {}).fallback || {}).qa) || [];
    var out = [];
    rows.forEach(function (row) {
      var items = row.ok === false ? (row.items || []) : (row.firstItems || []);
      var labels = items.map(function (key) { return REAL_CUT_QA_LABELS[key] || key; }).filter(Boolean);
      if (!labels.length) return;
      var tail = row.retried ? "1번 다시 만듦" + (row.ok === false ? ", 그래도 다름" : "") : "다시 만들지 못함";
      out.push("컷 " + (row.no != null ? row.no : row.cutId) + ": " + labels.join(", ") + "(" + tail + ")");
    });
    return out;
  }

  /* 실사 칸의 단계 문구(칸 fallback {step, done, total}). 단계가 없거나 실패·취소면 "". 컷 수는 제출 전이면 테이크 컷 수. */
  function realFallbackText(v, unit) {
    var fb = (v || {}).fallback;
    if (!fb || !fb.step || v.status === "failed" || v.status === "cancelled") return "";
    var total = Number(fb.total) || ((unit || {}).cutIds || []).length;
    if (fb.step === 1) return v.status === "completed" ? "실존 인물 검사에 걸려 정면 1장으로 다시 만들었습니다" : "실존 인물 검사에 걸려 정면 1장으로 다시 만드는 중";
    if (v.status === "completed") return "Seedance 거절 → 컷 단위 Kling으로 만들었습니다 (" + total + "컷)";
    return "Seedance 거절 → 컷 단위 Kling으로 만드는 중 (" + (Number(fb.done) || 0) + "/" + total + "컷)";
  }

  /* 확인창: 테이크 수·초·해상도·금액 숫자 + "소리도 만들기"(기본 켬) → POST /generate/real {confirmUsd, audio}.
     409 CostMismatch(운영에서도 type 은 남는다) → GET /plan 으로 새 금액을 읽어 확인창 글을 바꾸고, 다시 누를 때 새 금액으로
     보낸다. 다시 읽은 gen 이 시작할 수 없으면(설정 끔·진행 중 등) 창을 닫고 화면을 다시 그린다(realBlock 이 보인다). */
  function openRealConfirm(gen, showError, cast) {
    var est = (gen && gen.realEstimate) || {};
    var pid = state.projectId;
    var withCast = Array.isArray(cast) && cast.length > 0; // 작업 126: 인물 바꿔 다시 만들기에서 왔을 때만 cast 를 보낸다
    var scrim = el("div", "vnew-modal-scrim");
    var modal = el("div", "vnew-modal vnew-real-confirm");
    var head = el("div", "vnew-modal-head");
    head.appendChild(el("h3", null, withCast ? TAKE_REAL_CAST_BUTTON : "실사 버전 만들기"));
    var close = el("button", "icon-button", "×");
    close.type = "button";
    close.addEventListener("click", closeModal);
    head.appendChild(close);
    modal.appendChild(head);

    var bodyWrap = el("div", "vnew-modal-body");
    var lead = el("p", "vnew-real-confirm-text", takeRealConfirmText(est));
    bodyWrap.appendChild(lead);
    var fallbackMax = realFallbackMaxText(gen); // 작업 130 보완 1
    if (fallbackMax) bodyWrap.appendChild(el("p", "vnew-hint vnew-real-fallback-max", fallbackMax));
    if (withCast) bodyWrap.appendChild(el("p", "vnew-hint vnew-real-cast", takeRealCastText(cast)));
    if (gen && gen.realRun) {
      bodyWrap.appendChild(el("p", "vnew-hint", "지금 실사 결과는 '이전 실사 보기'로 남습니다."));
    }
    var audioLabel = el("label", "vnew-real-audio");
    var audioBox = el("input");
    audioBox.type = "checkbox";
    audioBox.checked = true;
    audioLabel.appendChild(audioBox);
    audioLabel.appendChild(el("span", null, "소리도 만들기"));
    bodyWrap.appendChild(audioLabel);
    var message = planError("");
    bodyWrap.appendChild(message);
    function showMessage(text) {
      message.textContent = text;
      message.style.display = "";
    }

    var actions = el("div", "vnew-modal-actions");
    var cancelBtn = el("button", "outline-button", "취소");
    cancelBtn.type = "button";
    cancelBtn.addEventListener("click", closeModal);
    var okBtn = el("button", "primary-button", "만들기 시작");
    okBtn.type = "button";
    function setBusy(busy) {
      okBtn.disabled = busy;
      cancelBtn.disabled = busy;
    }

    function rereadEstimate() {
      return api("/api/vnew/p/" + encodeURIComponent(pid) + "/plan")
        .then(function (fresh) {
          if (!state.mounted || state.projectId !== pid) {
            closeModal();
            return;
          }
          var next = (fresh && fresh.gen) || {};
          state.plan = fresh || state.plan;
          renderPlan();
          if (!next.canStartReal) {
            closeModal();
            return;
          }
          est = next.realEstimate || {};
          lead.textContent = takeRealConfirmText(est);
          setBusy(false);
          showMessage(takeRealCostChangedText(est));
        })
        .catch(function (err) {
          setBusy(false);
          showMessage("새 금액을 불러오지 못했습니다: " + errMessage(err));
        });
    }

    okBtn.addEventListener("click", function () {
      setBusy(true);
      message.style.display = "none";
      var realBody = { confirmUsd: Number(est.usd) || 0, audio: audioBox.checked === true };
      if (withCast) {
        realBody.cast = cast.map(function (item) {
          return { name: item.name, charId: item.charId, look: item.look };
        });
      }
      api("/api/vnew/p/" + encodeURIComponent(pid) + "/generate/real", {
        method: "POST",
        body: JSON.stringify(realBody)
      })
        .then(function (payload) {
          closeModal();
          if (!state.mounted || state.projectId !== pid) return;
          state.plan = (payload && payload.plan) || state.plan;
          loadPlan(false);
        })
        .catch(function (err) {
          if (err && err.type === "CostMismatch") return rereadEstimate();
          setBusy(false);
          showMessage("실사 버전을 시작하지 못했습니다: " + errMessage(err));
        });
    });
    actions.appendChild(cancelBtn);
    actions.appendChild(okBtn);
    bodyWrap.appendChild(actions);

    modal.appendChild(bodyWrap);
    scrim.appendChild(modal);
    scrim.addEventListener("click", function (event) {
      if (event.target === scrim) closeModal();
    });
    document.body.appendChild(scrim);

    function closeModal() {
      if (scrim.parentNode) scrim.parentNode.removeChild(scrim);
    }
    return scrim;
  }

  /* 나란히 보기 — 같이 재생·멈춤·시간 맞추기·소리 고르기(순수 동작, 검사 lr-n 이 가짜 video 로 실행한다). */
  function takeCompareSoundLabel(which) {
    return which === "real" ? "소리: 실사" : "소리: " + GEN_WEBTOON_TITLE;
  }

  /* 소리는 한쪽만: which 가 "real" 이면 실사, 그 밖에는 웹툰(기본). */
  function takeCompareSound(webVideo, realVideo, which) {
    webVideo.muted = which === "real";
    realVideo.muted = which !== "real";
  }

  /* 한쪽(src)을 옮기면(seeked) 다른 쪽(dst)을 같은 시간으로. 맞추려고 옮긴 쪽의 seeked 는 한 번 건너뛴다(되부름 막기).
     0.05초 안이면 옮기지 않는다. 옮겼으면 true. */
  function takeCompareFollow(guard, src, dst) {
    if (guard.skip === src) {
      guard.skip = null;
      return false;
    }
    var time = Number(src.currentTime) || 0;
    if (Math.abs((Number(dst.currentTime) || 0) - time) < 0.05) return false;
    guard.skip = dst;
    dst.currentTime = time;
    return true;
  }

  /* [같이 재생]: 둘 다 0초로 되감고 재생. play() 거절(자동 재생 정책 등)은 삼킨다. */
  function takeComparePlay(guard, webVideo, realVideo) {
    guard.skip = null;
    [webVideo, realVideo].forEach(function (video) {
      video.currentTime = 0;
      var played = video.play();
      if (played && typeof played.catch === "function") played.catch(function () {});
    });
  }

  function takeComparePause(webVideo, realVideo) {
    webVideo.pause();
    realVideo.pause();
  }

  /* 두 final 이 done 이면 웹툰·실사를 나란히 둔다(RC5.2). 아니면 null. 결과 격자(vnew-final-grid)의 한 줄 전체를 쓴다. */
  function takeCompare(gen) {
    var g = gen || {};
    var web = g.finalWebtoon || {};
    var real = g.finalReal || {};
    if (web.status !== "done" || real.status !== "done" || !web.videoUrl || !real.videoUrl) return null;
    var section = el("section", "panel vnew-take-compare");
    section.appendChild(el("h2", "vnew-sec-title", "나란히 보기"));
    section.appendChild(
      el("p", "vnew-hint", "웹툰과 실사를 같은 시간으로 맞춰 봅니다. 한쪽을 옮기면 다른 쪽도 따라갑니다. 소리는 한쪽만 납니다.")
    );
    var row = el("div", "vnew-take-compare-row");
    function player(title, url) {
      var item = el("div", "vnew-take-compare-item");
      item.appendChild(el("h3", "vnew-variant-title", title));
      var video = el("video", "vnew-final-player vnew-take-compare-video");
      video.src = url;
      video.controls = true;
      video.preload = "metadata";
      video.setAttribute("playsinline", "");
      item.appendChild(video);
      row.appendChild(item);
      return video;
    }
    var webVideo = player(GEN_WEBTOON_TITLE, web.videoUrl);
    var realVideo = player("실사", real.videoUrl);
    section.appendChild(row);

    var guard = { skip: null };
    var sound = "webtoon";
    takeCompareSound(webVideo, realVideo, sound);
    webVideo.addEventListener("seeked", function () {
      takeCompareFollow(guard, webVideo, realVideo);
    });
    realVideo.addEventListener("seeked", function () {
      takeCompareFollow(guard, realVideo, webVideo);
    });

    var tools = el("div", "vnew-take-compare-tools");
    var playBtn = el("button", "primary-button vnew-take-compare-play", "같이 재생");
    playBtn.type = "button";
    playBtn.addEventListener("click", function () {
      takeComparePlay(guard, webVideo, realVideo);
    });
    var pauseBtn = el("button", "outline-button vnew-take-compare-pause", "멈춤");
    pauseBtn.type = "button";
    pauseBtn.addEventListener("click", function () {
      takeComparePause(webVideo, realVideo);
    });
    var soundBtn = el("button", "outline-button vnew-take-compare-sound", takeCompareSoundLabel(sound));
    soundBtn.type = "button";
    soundBtn.title = "누르면 소리를 다른 영상으로 바꿉니다";
    soundBtn.addEventListener("click", function () {
      sound = sound === "real" ? "webtoon" : "real";
      takeCompareSound(webVideo, realVideo, sound);
      soundBtn.textContent = takeCompareSoundLabel(sound);
    });
    tools.appendChild(playBtn);
    tools.appendChild(pauseBtn);
    tools.appendChild(soundBtn);
    section.appendChild(tools);
    return section;
  }

  /* 시작 장면 카드: 상태 + 이미지(opening). */
  function openingCard(opening) {
    var card = el("section", "panel vnew-opening-card");
    var head = el("div", "vnew-sec-head");
    head.appendChild(el("h2", "vnew-sec-title", "시작 장면"));
    head.appendChild(
      el("span", "vnew-badge-gen is-" + (opening.status || "waiting"), genStatusLabel(opening.status || "waiting"))
    );
    card.appendChild(head);
    if (opening.status === "done" && opening.imageUrl) {
      var img = el("img", "vnew-opening-img");
      img.src = opening.imageUrl;
      img.alt = "시작 장면";
      img.loading = "lazy";
      img.addEventListener("click", function () {
        openLightbox(opening.imageUrl, "시작 장면");
      });
      card.appendChild(img);
    } else if (opening.status === "failed") {
      card.appendChild(el("p", "login-error", opening.error || "시작 장면을 그리지 못했습니다."));
    } else {
      card.appendChild(el("p", "vnew-hint", "시작 장면을 그리고 있습니다…"));
    }
    return card;
  }

  /* 구간 타임라인: 구간마다 한 줄 — 번호·초·컷, (프리비즈 미리보기)·실사 칸·웹툰 칸. */
  function segmentTimeline(segments, showError, mode) {
    var section = el("section", "panel vnew-seg-timeline");
    section.appendChild(el("h2", "vnew-sec-title", "구간별 진행"));
    if (!segments.length) {
      section.appendChild(el("p", "vnew-hint", "구간이 없습니다."));
      return section;
    }
    var list = el("div", "vnew-seg-rows");
    segments.forEach(function (seg, index) {
      list.appendChild(segmentRow(seg, index, showError, mode));
    });
    section.appendChild(list);
    return section;
  }

  function segmentRow(seg, index, showError, mode) {
    var row = el("div", "vnew-seg-row");

    var meta = el("div", "vnew-seg-meta");
    meta.appendChild(el("span", "vnew-seg-no", "구간 " + (index + 1)));
    meta.appendChild(el("span", "vnew-seg-seconds", (seg.seconds || 0) + "초"));
    meta.appendChild(el("span", "vnew-seg-cuts", "컷 " + ((seg.cutIds || []).join(", ") || "-")));
    row.appendChild(meta);

    var cells = el("div", "vnew-seg-cells" + (mode === "previz" ? " has-previz" : ""));
    // 프리비즈 모드: 맨 왼쪽에 프리비즈 녹화 미리보기(작게).
    if (mode === "previz") {
      cells.appendChild(previzPreviewCell(seg));
    }
    GEN_VARIANTS.forEach(function (variant) {
      cells.appendChild(segmentCell(seg, variant, showError, mode));
    });
    row.appendChild(cells);
    return row;
  }

  /* 프리비즈 미리보기 칸(작은 영상). 녹화가 있으면 재생, 없으면 안내.
     컷 단위(unit): startSec/endSec 가 있고 아직 그 컷만 잘라 둔 클립(clipped)이 아니면,
     녹화 전체에서 그 컷 구간만 보이게 한다 — src 에 #t={start},{end} 를 붙이고, timeupdate 에서
     endSec 에 닿으면 startSec 로 되감아 멈춘다(재생 버튼으로 다시). 복제처럼 보이던 문제 수정. */
  function previzPreviewCell(seg) {
    var cell = el("div", "vnew-seg-cell vnew-seg-previz");
    var head = el("div", "vnew-seg-cell-head");
    head.appendChild(el("span", "vnew-seg-cell-title", "프리비즈"));
    cell.appendChild(head);
    if (seg.previzUrl) {
      var start = Number(seg.startSec) || 0;
      var end = Number(seg.endSec) || 0;
      var clipByTime = !seg.clipped && end > start;
      var video = el("video", "vnew-seg-video vnew-seg-previz-video");
      // #t={start},{end} 로 그 컷 구간만 가리킨다(클립이 따로 없을 때만).
      video.src = clipByTime ? (seg.previzUrl + "#t=" + start + "," + end) : seg.previzUrl;
      video.controls = true;
      video.muted = true;
      video.setAttribute("playsinline", "");
      if (clipByTime) {
        // end 에 닿으면 start 로 되감아 멈춘다(그 컷 구간만 반복 없이 보이게).
        video.addEventListener("timeupdate", function () {
          if (video.currentTime >= end) {
            video.pause();
            video.currentTime = start;
          }
        });
        // 재생 버튼을 다시 누르면 구간 처음부터.
        video.addEventListener("play", function () {
          if (video.currentTime >= end || video.currentTime < start) {
            video.currentTime = start;
          }
        });
      }
      cell.appendChild(video);
    } else {
      cell.appendChild(el("p", "vnew-hint", "녹화 없음"));
    }
    return cell;
  }

  function segmentCell(seg, variant, showError, mode) {
    var v = (seg[variant.key] || {});
    var cell = el("div", "vnew-seg-cell");
    var head = el("div", "vnew-seg-cell-head");
    head.appendChild(el("span", "vnew-seg-cell-title", variant.title));
    head.appendChild(
      el("span", "vnew-badge-gen is-" + (v.status || "waiting"), genStatusLabel(v.status || "waiting"))
    );
    cell.appendChild(head);

    if (v.status === "completed" && v.videoUrl) {
      var video = el("video", "vnew-seg-video");
      video.src = v.videoUrl;
      video.controls = true;
      video.setAttribute("playsinline", "");
      cell.appendChild(video);
    } else if (v.status === "failed" || v.status === "cancelled") {
      if (v.status === "failed") {
        cell.appendChild(el("p", "login-error vnew-seg-error", v.error || "실패했습니다."));
      } else {
        cell.appendChild(el("p", "vnew-hint vnew-seg-error", "취소됨"));
      }
      var retryLabel;
      if (mode === "previz") {
        retryLabel = variant.key === "real" ? "실사 다시" : "웹툰 다시";
      } else {
        retryLabel = variant.key === "real" ? "이 구간부터 다시" : "변환 다시";
      }
      var retry = el("button", "outline-button vnew-seg-retry", retryLabel);
      retry.type = "button";
      retry.addEventListener("click", function () {
        retry.disabled = true;
        api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/generate/retry", {
          method: "POST",
          body: JSON.stringify({ segId: seg.id, variant: variant.key })
        })
          .then(function () {
            loadPlan(false);
          })
          .catch(function (err) {
            retry.disabled = false;
            if (showError) showError("다시 시도하지 못했습니다: " + errMessage(err));
          });
      });
      cell.appendChild(retry);
    }
    return cell;
  }

  /* ================================================================ ⑥ 사운드 (베타)

     고른 영상(gen.selected)의 그림체로 대사마다 실제 말/속마음을 정하고 원래 소리에만 반영한다(작업 122, soundMount).
     영상은 그대로다(서버가 영상 스트림을 복사만 한다). [다른 영상 고르기] → ⑤. */

  function renderSoundStep(host) {
    var plan = state.plan || {};
    var gen = plan.gen || {};
    var error = planError("");
    host.appendChild(error);

    if (!gen.selected) {
      host.appendChild(el("p", "vnew-hint", "먼저 ⑤ 영상에서 결과 하나를 골라 주세요."));
      var goBtn = el("button", "outline-button", "← 영상으로");
      goBtn.type = "button";
      goBtn.addEventListener("click", function () {
        state.planStep = 5;
        renderPlan();
      });
      host.appendChild(goBtn);
      return;
    }

    var selected = gen.selected; // "real" | "webtoon"
    var meta = selected === "real" ? VARIANTS[0] : { key: "webtoon", title: "애니(웹툰)" }; // 작업 123: 보이는 이름만
    var final = (selected === "real" ? gen.finalReal : gen.finalWebtoon) || {};

    // --- 머리글 + BETA 배지 ---
    var head = el("section", "vnew-sound-head");
    var titleWrap = el("div");
    var title = el("h2", "vnew-sec-title", "사운드");
    title.appendChild(el("span", "vnew-badge vnew-beta-badge", "BETA"));
    titleWrap.appendChild(title);
    titleWrap.appendChild(el("p", "vnew-hint", "고른 영상(" + meta.title + ")의 대사마다 실제 말과 속마음을 정해 소리에 반영하는 단계입니다."));
    head.appendChild(titleWrap);
    host.appendChild(head);

    // --- 도구: [다른 영상 고르기] [영상 내려받기] [⑦ 편집으로]. 원본·반영한 소리 플레이어는 아래 soundMount 가 그린다 ---
    var playerSec = el("section", "panel vnew-sound-player-sec");

    var playerTools = el("div", "vnew-sound-tools");
    var reselect = el("button", "outline-button", "다른 영상 고르기");
    reselect.type = "button";
    reselect.addEventListener("click", function () {
      state.planStep = 5;
      renderPlan();
    });
    playerTools.appendChild(reselect);
    if (final.status === "done" && final.videoUrl) {
      playerTools.appendChild(downloadLink(final.videoUrl, "영상 내려받기"));
    }
    // ⑦ 편집으로(지시서 111): 서버가 편집 재료가 있다고 줄 때만(gen.edit.available, VNEW_EDIT 켬).
    if (gen.edit && gen.edit.available === true) {
      var toEdit = el("button", "primary-button vnew-sound-to-edit", "⑦ 편집으로");
      toEdit.type = "button";
      toEdit.title = "컷을 타임라인에 놓고 자르기·순서·소리를 고쳐 mp4 로 내보냅니다";
      toEdit.addEventListener("click", function () {
        state.planStep = 7;
        renderPlan();
      });
      playerTools.appendChild(toEdit);
    }
    playerSec.appendChild(playerTools);
    host.appendChild(playerSec);

    // --- 대사별 실제 말/속마음 · 구간 · [소리에 반영](작업 122) ---
    soundMount(host, selected, final);
  }

  /* 대본 entries 의 화자·대사·컷 시간대를 읽기 전용으로. */
  function dialogueList() {
    var plan = state.plan || {};
    var script = plan.script || {};
    var entries = script.entries || [];
    var section = el("section", "panel vnew-dialogue-sec");
    section.appendChild(el("h2", "vnew-sec-title", "대사 (" + entries.length + "줄)"));
    section.appendChild(
      el("p", "vnew-hint", "영상 소리는 대사만 만듭니다. 배경음·효과음은 따로 넣어 주세요.")
    );
    if (!entries.length) {
      section.appendChild(el("p", "vnew-hint", "대사가 없습니다."));
      return section;
    }
    var list = el("div", "vnew-dialogue-list");
    entries.forEach(function (entry, index) {
      var item = el("div", "vnew-dialogue-item");
      item.appendChild(el("span", "vnew-dialogue-no", String(index + 1)));
      var body = el("div", "vnew-dialogue-body");
      var speaker = el("span", "vnew-dialogue-speaker", entry.speaker || "(지문)");
      body.appendChild(speaker);
      body.appendChild(el("span", "vnew-dialogue-line", entry.lineKo || entry.situationKo || "-"));
      var panels = (entry.panels || []).join(", ");
      if (panels) {
        body.appendChild(el("span", "vnew-dialogue-panels", "컷 " + panels));
      }
      item.appendChild(body);
      list.appendChild(item);
    });
    section.appendChild(list);
    return section;
  }

  /* ================================================================ ⑥ 사운드 — 대사별 실제 말/속마음(작업 122)

     서버 /api/vnew/p/<pid>/sound(sound.py). GET 이 처음 문서를 만든다(voice = 대본 그대로, "자동"), PUT 은 바뀐 줄의 voice·start·end 만
     보내고, POST /sound/apply 가 백그라운드로 속마음 줄 구간에만 소리 효과를 건 새 mp4 를 만든다(영상은 복사만). 반영 중이면
     SOUND_POLL_MS 마다 GET(작업 상태·반영 기록만 바꾸고 고치던 줄은 그대로 둔다). 글은 모두 textContent(el). */

  var SOUND_POLL_MS = 3000; // 반영 running 동안 상태 다시 읽기
  var SOUND_HINT = "영상은 그대로 두고 소리만 바뀝니다. 속마음 줄은 속으로 울리는 목소리로 바뀝니다.";
  var SOUND_NO_AUDIO = "이 영상에는 소리가 없어 바꿀 수 없습니다";
  var SOUND_VOICES = [["spoken", "실제 말"], ["thought", "속마음"]];
  // 서버 거절 kind → 화면 문구(운영에서 서버 문구가 감춰지는 새 kind 도 알아보게).
  var SOUND_ERRORS = {
    SoundRunning: "소리를 반영하는 중입니다. 끝난 뒤 다시 눌러 주세요.",
    SoundChanged: "원본 영상이 바뀌었습니다. 다시 불러온 뒤 고쳐 주세요.",
    SoundNotReady: "사운드 화면을 다시 불러와 주세요.",
    NoSoundSource: "소리를 바꿀 영상이 아직 없습니다. ⑤에서 영상을 먼저 만들어 주세요.",
    BadLineRange: "구간은 원본 길이 안에서 0.2초 이상이어야 합니다.",
    BadLineTime: "시작·끝 시간이 올바르지 않습니다.",
    BadLine: "고친 줄을 찾을 수 없습니다. 다시 불러온 뒤 고쳐 주세요.",
    BadVoice: "목소리 종류가 올바르지 않습니다."
  };
  var soundCtx = null; // 지금 열린 ⑥ 사운드 화면. 다시 그리면 새 값으로 바뀌고 옛 화면의 타이머·응답은 버린다

  function soundErrText(err) {
    var kind = err && err.type;
    return (kind && SOUND_ERRORS[kind]) || errMessage(err);
  }

  function soundClone(value) {
    return JSON.parse(JSON.stringify(value || []));
  }

  /* 화면 줄과 저장된 줄을 견줘 바뀐 값(voice·start·end)만 담은 PUT 목록. 순수 함수(DOM 없음). */
  function soundPatch(lines, saved) {
    var byId = {};
    (saved || []).forEach(function (item) {
      if (item && item.id) byId[item.id] = item;
    });
    var out = [];
    (lines || []).forEach(function (line) {
      var before = line && byId[line.id];
      if (!before) return;
      var item = { id: line.id };
      var changed = false;
      ["voice", "start", "end"].forEach(function (key) {
        if (line[key] !== before[key]) {
          item[key] = line[key];
          changed = true;
        }
      });
      if (changed) out.push(item);
    });
    return out;
  }

  /* 칸에 적은 초 → 숫자(밀리초 반올림). 숫자가 아니면 null. */
  function soundSeconds(text) {
    var raw = String(text === undefined || text === null ? "" : text).trim();
    if (!raw) return null;
    var value = Number(raw);
    if (!isFinite(value)) return null;
    return Math.round(value * 1000) / 1000;
  }

  /* 보내기 전 구간 검사(서버와 같은 규칙: 0 ≤ 시작 < 끝 ≤ 원본 길이, 최소 길이). 맞으면 "". */
  function soundRangeProblem(line, duration, minSeconds) {
    if (line.start === null || line.end === null || line.start === undefined || line.end === undefined) {
      return "시작·끝 시간을 숫자로 적어 주세요.";
    }
    if (line.start < 0 || line.end > duration + 0.001 || line.end - line.start < minSeconds - 1e-9) {
      return "구간은 0초부터 " + duration.toFixed(2) + "초 안에서 " + minSeconds + "초 이상이어야 합니다.";
    }
    return "";
  }

  function soundPath() {
    return "/api/vnew/p/" + encodeURIComponent(state.projectId) + "/sound";
  }

  function soundAlive(ctx) {
    if (ctx && ctx.inEdit) return editTxLrdAlive(ctx); // 작업 215b: ⑦ 안 대사 고치기 창
    return soundCtx === ctx && !!state.mounted && state.planStep === 6;
  }

  function soundMount(host, variant, final) {
    if (soundCtx && soundCtx.timer) window.clearTimeout(soundCtx.timer);
    var ctx = {
      root: el("section", "panel vnew-sound"), variant: variant, final: final || {}, view: null, saved: [], lines: [],
      timer: 0, stopAt: null, busy: false, pickN: null, versionSig: "", player: null, resultBox: null, jobLine: null, applyBtn: null
    };
    soundCtx = ctx;
    host.appendChild(ctx.root);
    ctx.root.appendChild(el("p", "vnew-hint", "소리 정보를 불러오는 중…"));
    soundLoad(ctx);
  }

  function soundLoad(ctx) {
    api(soundPath() + "?variant=" + encodeURIComponent(ctx.variant))
      .then(function (view) {
        if (!soundAlive(ctx)) return;
        ctx.view = view || {};
        ctx.saved = soundClone(ctx.view.lines);
        ctx.lines = soundClone(ctx.view.lines);
        soundRender(ctx);
        soundSchedule(ctx);
      })
      .catch(function (err) {
        if (!soundAlive(ctx)) return;
        clear(ctx.root);
        if (err && err.type === "SoundDisabled") {
          soundFallback(ctx);
          return;
        }
        ctx.root.appendChild(planError("소리 정보를 불러오지 못했습니다: " + soundErrText(err)));
        var retry = el("button", "outline-button", "다시 시도");
        retry.type = "button";
        retry.addEventListener("click", function () {
          clear(ctx.root);
          ctx.root.appendChild(el("p", "vnew-hint", "소리 정보를 불러오는 중…"));
          soundLoad(ctx);
        });
        ctx.root.appendChild(retry);
      });
  }

  /* VNEW_SOUND 가 꺼져 있으면(404 SoundDisabled) 예전처럼 고른 영상과 읽기 전용 대사 목록만 보인다. */
  function soundFallback(ctx) {
    var final = ctx.final || {};
    if (final.status === "done" && final.videoUrl) {
      var player = el("video", "vnew-final-player vnew-sound-player");
      player.src = final.videoUrl;
      player.controls = true;
      player.setAttribute("playsinline", "");
      ctx.root.appendChild(player);
    } else {
      ctx.root.appendChild(el("p", "vnew-hint", "고른 영상을 불러오지 못했습니다."));
    }
    ctx.root.appendChild(dialogueList());
  }

  function soundRender(ctx) {
    var view = ctx.view || {};
    var source = view.source || {};
    clear(ctx.root);
    ctx.lrd = null; // 작업 212: 줄마다 대사 고치기 기록(아래 soundTable 이 다시 채운다)
    ctx.sldRows = []; // 작업 224: 줄마다 내려받기 단추(아래 soundTable 이 다시 채운다)
    ctx.root.appendChild(el("h2", "vnew-sec-title", "대사 소리"));
    ctx.root.appendChild(el("p", "vnew-hint vnew-sound-lead", SOUND_HINT));

    var players = el("div", "vnew-sound-players");
    var sourceBox = el("div", "vnew-sound-pbox");
    sourceBox.appendChild(el("h3", "vnew-sound-ptitle", "원본 · " + (source.label || "")));
    var player = el("video", "vnew-sound-player");
    player.controls = true;
    player.preload = "metadata";
    player.setAttribute("playsinline", "");
    if (source.videoUrl) player.src = source.videoUrl;
    // [▶ 이 구간 듣기]: 끝 시각에 멈춘다(사용자가 직접 재생하면 stopAt 이 없어 끝까지 간다).
    player.addEventListener("timeupdate", function () {
      if (ctx.stopAt !== null && player.currentTime >= ctx.stopAt) {
        player.pause();
        ctx.stopAt = null;
      }
    });
    ctx.player = player;
    sourceBox.appendChild(player);
    if (typeof sepMountSound === "function") sepMountSound(ctx, sourceBox); // 작업 217: 섞인 소리 분리하기(잘라 낸 검사 하네스에는 없다)
    players.appendChild(sourceBox);
    ctx.resultBox = el("div", "vnew-sound-pbox");
    players.appendChild(ctx.resultBox);
    ctx.root.appendChild(players);
    ctx.versionSig = "";
    soundRenderResult(ctx);
    if (!source.hasAudio) ctx.root.appendChild(el("p", "vnew-hint vnew-sound-noaudio", SOUND_NO_AUDIO));
    lrdRevisionBar(ctx); // 작업 212: 대사 고친 판 · 이전 영상으로 되돌리기

    vocMount(ctx); // 작업 203: 캐릭터 목소리(대사 목록 위, voices 가 없으면 그리지 않음)
    ctx.root.appendChild(soundTable(ctx));

    var actions = el("div", "vnew-sound-actions");
    var applyBtn = el("button", "primary-button vnew-sound-apply", "소리에 반영");
    applyBtn.type = "button";
    applyBtn.title = "바꾼 내용을 저장한 뒤 속마음 줄 구간에만 소리 효과를 겁니다";
    applyBtn.addEventListener("click", function () {
      soundApply(ctx);
    });
    ctx.applyBtn = applyBtn;
    actions.appendChild(applyBtn);
    vocApplyButton(ctx, actions); // 작업 203
    ctx.jobLine = el("p", "vnew-hint vnew-sound-job");
    ctx.jobLine.setAttribute("role", "status");
    actions.appendChild(ctx.jobLine);
    ctx.root.appendChild(actions);
    soundRenderJob(ctx);
  }

  /* "반영한 소리" 플레이어(고른 버전, 기본은 최신)·버전 고르기·내려받기. 버전 목록이 그대로면 다시 그리지 않는다(재생이 끊기지 않게). */
  function soundRenderResult(ctx) {
    var box = ctx.resultBox;
    if (!box) return;
    var versions = (ctx.view || {}).versions || [];
    var sig = JSON.stringify([ctx.pickN, versions.map(function (v) {
      return [v.n, v.currentSource, !!v.outdated];
    })]);
    if (sig === ctx.versionSig) return;
    ctx.versionSig = sig;
    clear(box);
    box.appendChild(el("h3", "vnew-sound-ptitle", "반영한 소리"));
    if (!versions.length) {
      box.appendChild(el("p", "vnew-hint", "아직 반영한 소리가 없습니다. 아래에서 고친 뒤 [소리에 반영]을 누르세요."));
      if (typeof sldZipButton === "function") { // 작업 224: 반영본이 없으면 원본 소리를 대사별로
        var srcTools = el("div", "vnew-sound-vtools");
        sldZipButton(ctx, srcTools);
        box.appendChild(srcTools);
      }
      return;
    }
    var chosen = versions.filter(function (v) {
      return v.n === ctx.pickN;
    })[0] || versions[versions.length - 1];
    var video = el("video", "vnew-sound-player");
    video.controls = true;
    video.preload = "metadata";
    video.setAttribute("playsinline", "");
    if (chosen.videoUrl) video.src = chosen.videoUrl;
    box.appendChild(video);
    var tools = el("div", "vnew-sound-vtools");
    var select = el("select", "vnew-sound-version");
    select.setAttribute("aria-label", "반영한 소리 버전");
    versions.slice().reverse().forEach(function (v) {
      var label = "v" + v.n + " · 속마음 " + (v.thoughtIds || []).length + "줄" + (v.currentSource ? "" : " · 이전 원본") + vocVersionTag(v);
      if (v.outdated) label += " · " + (v.outdatedText || LRD_OUTDATED); // 작업 212
      var option = el("option", "", label);
      option.value = String(v.n);
      if (v.n === chosen.n) option.selected = true;
      select.appendChild(option);
    });
    select.addEventListener("change", function () {
      ctx.pickN = Number(select.value);
      soundRenderResult(ctx);
    });
    tools.appendChild(select);
    var vocBadge = vocVersionBadge(chosen); // 작업 203
    if (vocBadge) tools.appendChild(vocBadge);
    if (chosen.videoUrl) tools.appendChild(downloadLink(chosen.videoUrl, "반영본 내려받기"));
    if (typeof sldZipButton === "function") sldZipButton(ctx, tools); // 작업 224: 대사별 내려받기(zip)
    box.appendChild(tools);
    if (chosen.outdated) box.appendChild(el("p", "vnew-hint vnew-lrd-outdated", chosen.outdatedText || LRD_OUTDATED)); // 작업 212
  }

  /* ==== 작업 224 시작 ==== */
  /* ⑥ 대사별 내려받기: 고른 반영본(없으면 원본)의 소리를 줄 구간으로 잘라 한 줄 mp3 · 모든 줄 zip. 서버가 presigned 주소를 주면 숨긴
     <a download> 로 받는다. 소리를 반영하는 중(job running)에는 막고, 한 번에 요청 하나만 보낸다(ctx.sldBusy). */
  var SLD_ZIP_LABEL = "대사별 내려받기 (zip)";
  var SLD_LINE_LABEL = "이 줄 내려받기";
  var SLD_WAIT_LABEL = "만드는 중…";
  var SLD_RUNNING_HINT = "소리를 반영하는 중에는 내려받을 수 없습니다. 끝난 뒤 눌러 주세요.";
  var SLD_ERRORS = {
    NoLines: "내려받을 대사가 없습니다.",
    LinesNoSource: "소리 원본을 찾지 못했습니다. 영상을 먼저 만들어 주세요.",
    LinesBadLine: "고른 대사를 찾지 못했습니다. 화면을 다시 불러와 주세요.",
    NoSoundVersion: "그 반영본을 찾지 못했습니다. 화면을 다시 불러와 주세요.",
    TooManyLines: "대사는 한 번에 200줄까지 내려받을 수 있습니다.",
    SourceTooLong: "원본이 15분보다 길어 대사별로 내려받을 수 없습니다.",
    LinesFailed: "대사 소리를 자르지 못했습니다. 잠시 뒤 다시 시도해 주세요.",
    BadAudio: "이 영상에는 소리가 없어 대사를 내려받을 수 없습니다."
  };

  function sldErrText(err) {
    var kind = err && err.type;
    return (kind && SLD_ERRORS[kind]) || errMessage(err);
  }

  /* 지금 고른 반영본 번호(반영한 소리 목록에서 고른 것, 기본 최신). 반영본이 없으면 0(원본). */
  function sldChosenN(ctx) {
    var versions = (ctx.view || {}).versions || [];
    if (!versions.length) return 0;
    var chosen = versions.filter(function (v) {
      return v.n === ctx.pickN;
    })[0] || versions[versions.length - 1];
    return chosen.n;
  }

  function sldRunning(ctx) {
    return (((ctx.view || {}).job || {}).status || "") === "running";
  }

  function sldZipButton(ctx, tools) {
    var button = el("button", "outline-button vnew-sld-zip", SLD_ZIP_LABEL);
    button.type = "button";
    button.addEventListener("click", function () {
      sldDownload(ctx, "zip", null);
    });
    var hint = el("span", "vnew-hint vnew-sld-hint", "");
    ctx.sldZip = button;
    ctx.sldHint = hint;
    tools.appendChild(button);
    tools.appendChild(hint);
    sldPaint(ctx);
  }

  function sldRowButton(ctx, line, cell) {
    if (ctx.inEdit) return;
    var number = ctx.lines.indexOf(line) + 1;
    var button = el("button", "outline-button vnew-sld-line", "↓");
    button.type = "button";
    button.title = SLD_LINE_LABEL;
    button.setAttribute("aria-label", SLD_LINE_LABEL + ": " + number + "번 줄 " + (line.speaker || "(이름 없음)"));
    button.addEventListener("click", function () {
      sldDownload(ctx, "line:" + line.id, [line.id]);
    });
    if (!ctx.sldRows) ctx.sldRows = [];
    ctx.sldRows.push({ key: "line:" + line.id, button: button });
    cell.appendChild(button);
    sldPaint(ctx);
  }

  /* 단추 상태: 반영 중이거나 요청 하나가 도는 중이면 모두 막는다. 도는 단추는 "만드는 중…". */
  function sldPaint(ctx) {
    var running = sldRunning(ctx);
    var busy = ctx.sldBusy || "";
    if (ctx.sldZip) {
      ctx.sldZip.disabled = running || !!busy;
      ctx.sldZip.textContent = busy === "zip" ? SLD_WAIT_LABEL : SLD_ZIP_LABEL;
      ctx.sldZip.title = running ? SLD_RUNNING_HINT : "";
    }
    if (ctx.sldHint) {
      ctx.sldHint.textContent = running ? SLD_RUNNING_HINT : "";
      ctx.sldHint.hidden = !running;
    }
    (ctx.sldRows || []).forEach(function (rec) {
      rec.button.disabled = running || !!busy;
      rec.button.textContent = busy === rec.key ? SLD_WAIT_LABEL : "↓";
    });
  }

  function sldSave(url, filename) {
    var a = downloadLink(url, "");
    a.setAttribute("download", filename || "");
    a.style.display = "none";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  function sldDownload(ctx, key, lineIds) {
    if (ctx.sldBusy || sldRunning(ctx)) return;
    ctx.sldBusy = key;
    sldPaint(ctx);
    var body = { variant: ctx.variant, n: sldChosenN(ctx) };
    if (lineIds) body.lineIds = lineIds;
    function done() {
      ctx.sldBusy = "";
      sldPaint(ctx);
    }
    api(soundPath() + "/lines/download", { method: "POST", body: JSON.stringify(body) })
      .then(function (res) {
        done();
        if (!soundAlive(ctx)) return;
        if (res && res.url) sldSave(res.url, res.filename);
        else soundJobText(ctx, "내려받을 주소를 받지 못했습니다. 잠시 뒤 다시 시도해 주세요.", true);
      })
      .catch(function (err) {
        done();
        if (soundAlive(ctx)) soundJobText(ctx, "대사를 내려받지 못했습니다: " + sldErrText(err), true);
      });
  }
  /* ==== 작업 224 끝 ==== */

  function soundTable(ctx) {
    var wrap = el("div", "vnew-sound-table");
    if (!ctx.lines.length) {
      wrap.appendChild(el("p", "vnew-hint", "대본에 대사가 없습니다. ②에서 대사를 먼저 넣어 주세요."));
      return wrap;
    }
    var head = el("div", "vnew-sound-row vnew-sound-row-head");
    ["말하는 사람", "대사", "목소리", "시작(초)", "끝(초)", ""].forEach(function (text) {
      head.appendChild(el("span", "vnew-sound-cell", text));
    });
    wrap.appendChild(head);
    ctx.lines.forEach(function (line) {
      wrap.appendChild(soundRow(ctx, line));
    });
    return wrap;
  }

  function soundRow(ctx, line) {
    var row = el("div", "vnew-sound-row");
    row.appendChild(el("span", "vnew-sound-cell vnew-sound-speaker", line.speaker || "(이름 없음)"));
    var lineCell = el("span", "vnew-sound-cell vnew-sound-line", line.lineKo || "");
    row.appendChild(lineCell);
    var voiceCell = el("span", "vnew-sound-cell vnew-sound-voice");
    var toggle = el("div", "vnew-sound-toggle");
    toggle.setAttribute("role", "group");
    toggle.setAttribute("aria-label", "목소리 종류");
    var autoTag = el("span", "vnew-badge vnew-sound-auto", "자동");
    autoTag.title = "대본에서 처음 정한 값입니다";
    var buttons = SOUND_VOICES.map(function (pair) {
      var button = el("button", "outline-button vnew-sound-voice-btn", pair[1]);
      button.type = "button";
      button.addEventListener("click", function () {
        if (line.voice === pair[0]) return;
        line.voice = pair[0];
        line.auto = false;
        paint();
      });
      toggle.appendChild(button);
      return [pair[0], button];
    });
    function paint() {
      buttons.forEach(function (item) {
        var on = line.voice === item[0];
        if (on) item[1].classList.add("is-on");
        else item[1].classList.remove("is-on");
        item[1].setAttribute("aria-pressed", on ? "true" : "false");
      });
      autoTag.style.display = line.auto ? "" : "none";
      if (line.voice === "thought") row.classList.add("is-thought");
      else row.classList.remove("is-thought");
    }
    voiceCell.appendChild(toggle);
    voiceCell.appendChild(autoTag);
    row.appendChild(voiceCell);
    ["start", "end"].forEach(function (field) {
      var cell = el("span", "vnew-sound-cell");
      var input = el("input", "vnew-sound-time");
      input.type = "number";
      input.step = "0.1";
      input.min = "0";
      input.value = line[field] === undefined || line[field] === null ? "" : String(line[field]);
      input.setAttribute("aria-label", field === "start" ? "시작(초)" : "끝(초)");
      input.addEventListener("change", function () {
        line[field] = soundSeconds(input.value);
        line.auto = false;
        paint();
      });
      cell.appendChild(input);
      row.appendChild(cell);
    });
    var listenCell = el("span", "vnew-sound-cell");
    var listen = el("button", "outline-button vnew-sound-listen", "▶ 이 구간 듣기");
    listen.type = "button";
    listen.addEventListener("click", function () {
      soundListen(ctx, line);
    });
    listenCell.appendChild(listen);
    if (typeof sldRowButton === "function") sldRowButton(ctx, line, listenCell); // 작업 224: 이 줄 내려받기
    vocRowTools(ctx, line, row, listenCell); // 작업 203
    lrdRowTools(ctx, line, row, lineCell, listenCell); // 작업 212: 대사 고치기
    row.appendChild(listenCell);
    paint();
    return row;
  }

  /* 원본 플레이어로 그 줄 구간만 재생한다(끝에서 timeupdate 가 멈춘다). */
  function soundListen(ctx, line) {
    var player = ctx.player;
    if (!player || line.start === null || line.end === null || !(line.end > line.start)) return;
    ctx.stopAt = line.end;
    try {
      player.currentTime = line.start;
    } catch (error) {
      return;
    }
    var playing = player.play();
    if (playing && playing.catch) playing.catch(function () {});
  }

  function soundJobText(ctx, text, bad) {
    if (!ctx.jobLine) return;
    ctx.jobLine.textContent = text;
    if (bad) ctx.jobLine.classList.add("is-bad");
    else ctx.jobLine.classList.remove("is-bad");
  }

  function soundRenderJob(ctx) {
    var view = ctx.view || {};
    var job = view.job || {};
    var hasAudio = !!(view.source || {}).hasAudio;
    if (ctx.applyBtn) ctx.applyBtn.disabled = ctx.busy || !hasAudio || job.status === "running";
    if (job.status === "running") soundJobText(ctx, "소리를 반영하는 중… 영상은 그대로 두고 소리만 바꿉니다.", false);
    else if (job.status === "done") soundJobText(ctx, "반영했습니다. 위 '반영한 소리'에서 들어 보세요.", false);
    else if (job.status === "failed") soundJobText(ctx, "반영하지 못했습니다: " + (job.error || "알 수 없는 오류"), true);
    else soundJobText(ctx, "", false);
    vocRenderJob(ctx); // 작업 203: 목소리 맞춤 진행·결과
    if (typeof sldPaint === "function") sldPaint(ctx); // 작업 224: 반영 중이면 대사별 내려받기를 막는다
  }

  /* [소리에 반영]: 구간 검사 → 바뀐 줄만 PUT → POST apply → 반영이 끝날 때까지 폴링. */
  function soundApply(ctx) {
    if (ctx.busy) return;
    var view = ctx.view || {};
    var duration = Number((view.source || {}).duration) || 0;
    var minSeconds = Number((view.limits || {}).minLineSeconds) || 0.2;
    for (var i = 0; i < ctx.lines.length; i++) {
      var problem = soundRangeProblem(ctx.lines[i], duration, minSeconds);
      if (problem) {
        soundJobText(ctx, i + 1 + "번째 줄: " + problem, true);
        return;
      }
    }
    var patch = soundPatch(ctx.lines, ctx.saved);
    ctx.busy = true;
    if (ctx.applyBtn) ctx.applyBtn.disabled = true;
    soundJobText(ctx, patch.length ? "고친 내용을 저장하는 중…" : "소리 반영을 시작하는 중…", false);
    var save = patch.length
      ? api(soundPath(), { method: "PUT", body: JSON.stringify({ variant: ctx.variant, lines: patch }) })
      : Promise.resolve(null);
    save
      .then(function (saved) {
        if (!soundAlive(ctx)) return null;
        if (saved) {
          ctx.saved = soundClone(saved.lines);
          // 화면 줄 객체는 그대로 두고(행이 쥐고 있다) 서버 값(auto 포함)만 옮긴다.
          var byId = {};
          ctx.saved.forEach(function (item) {
            byId[item.id] = item;
          });
          ctx.lines.forEach(function (line) {
            var after = byId[line.id];
            if (after) ["voice", "start", "end", "auto"].forEach(function (key) {
              line[key] = after[key];
            });
          });
        }
        soundJobText(ctx, "소리 반영을 시작하는 중…", false);
        return api(soundPath() + "/apply", { method: "POST", body: JSON.stringify({ variant: ctx.variant }) });
      })
      .then(function (result) {
        if (!result || !soundAlive(ctx)) return;
        ctx.busy = false;
        ctx.view.job = result.job || { status: "running" };
        soundRenderJob(ctx);
        soundSchedule(ctx);
      })
      .catch(function (err) {
        if (!soundAlive(ctx)) return;
        ctx.busy = false;
        if (err && err.type === "SoundRunning") {
          ctx.view.job = { status: "running" };
          soundRenderJob(ctx);
          soundSchedule(ctx);
          return;
        }
        soundRenderJob(ctx);
        soundJobText(ctx, "반영하지 못했습니다: " + soundErrText(err), true);
      });
  }

  /* 반영 running 이면 SOUND_POLL_MS 뒤 GET — 작업 상태·반영 기록만 바꾼다(고치던 줄은 그대로). */
  function soundSchedule(ctx) {
    if (ctx.inEdit) return editTxLrdPoll(ctx); // 작업 215b: ⑦ 안 대사 고치기 창
    if (ctx.timer) window.clearTimeout(ctx.timer);
    ctx.timer = 0;
    var lrdWas = lrdBusy(ctx.view); // 작업 212: 대사 영상 다시 만들기 진행 중이면 같은 GET 으로 함께 본다
    if (((ctx.view || {}).job || {}).status !== "running" && !lrdWas) return;
    ctx.timer = window.setTimeout(function () {
      ctx.timer = 0;
      if (!soundAlive(ctx)) return;
      api(soundPath() + "?variant=" + encodeURIComponent(ctx.variant))
        .then(function (view) {
          if (!soundAlive(ctx)) return;
          if (lrdWas && lrdChanged(ctx.view, view)) {
            lrdReload(ctx, view); // 끝났다(새 판·실패) — 원본이 바뀌었을 수 있어 화면을 새로 그린다
            return;
          }
          ctx.view.job = (view || {}).job || {};
          ctx.view.versions = (view || {}).versions || [];
          if ((view || {}).lineRedo) ctx.view.lineRedo = view.lineRedo;
          if (ctx.view.job.status !== "running") ctx.pickN = null; // 끝나면 최신 버전을 보인다
          soundRenderResult(ctx);
          soundRenderJob(ctx);
          lrdPaintRows(ctx);
          soundSchedule(ctx);
        })
        .catch(function () {
          if (soundAlive(ctx)) soundSchedule(ctx);
        });
    }, SOUND_POLL_MS);
  }

  /* ================================================================ ⑥ 사운드 — 대사 고치기 · 그 대사 영상 다시 만들기(작업 212)

     서버 line_redo.py(라우트 /sound/lines/*). 줄마다 [대사 고치기] → 새 대사·말하는 사람·실제 말/속마음·말투 메모를 적고(또는
     [AI로 다시 쓰기] 후보 3줄에서 고르고) 저장한다. 원래 대본은 [대본에도 반영]을 고를 때만 바뀐다(원래 대사는 회색으로 함께 보인다).
     [이 대사 영상 다시 만들기]는 견적(quote, 영상 호출 0) → 확인 창(금액·모델) → 시작(redo, confirmUsd). 금액이 바뀌면(409 CostMismatch)
     견적을 다시 받아 바뀐 금액을 보이고 창은 그대로 둔다(작업 206 costChangedText). 진행은 ⑥ 폴링(soundSchedule)이 같이 본다.
     끝나면 새 판이 ⑥ 원본이 되고, [이전 영상으로 되돌리기]로 앞 판(원래 영상 포함)을 고를 수 있다. 글은 모두 textContent(el). */

  var LRD_OUTDATED = "목소리 맞춤을 다시 반영해 주세요";
  var LRD_ERRORS = {
    LineNoOverride: "먼저 [대사 고치기]로 새 대사를 저장해 주세요.",
    LineRedoRunning: "이 대사 영상을 다시 만드는 중입니다. 끝난 뒤 다시 눌러 주세요.",
    LineRedoNoQuote: "먼저 금액을 확인해 주세요.",
    LineRangeShort: "이 대사가 들어 있는 영상 조각이 너무 짧아 다시 만들 수 없습니다.",
    LineNoRange: "이 대사 구간의 영상을 찾지 못했습니다. 화면을 다시 불러와 주세요.",
    LineRedoLimit: "이 영상은 대사를 더 다시 만들 수 없습니다.",
    BadLineText: "새 대사를 적어 주세요.",
    BadSpeaker: "말하는 사람은 등장인물 목록에서 골라 주세요.",
    BadDelivery: "말투 메모가 너무 깁니다.",
    SuggestFailed: "새 대사를 만들지 못했습니다. 잠시 뒤 다시 눌러 주세요.",
    RetakeNotTarget: "이 대사 구간의 영상은 AI로 다시 만들 수 없습니다.",
    RetakeNoPrompt: "이 대사 구간의 영상은 프롬프트 기록이 없어 다시 만들 수 없습니다.",
    RetakeRunning: "이 구간의 영상을 ⑦에서 다시 만드는 중입니다. 끝난 뒤 다시 눌러 주세요.",
    RetakeNoDraft: "견적이 바뀌었습니다. 금액을 다시 확인해 주세요.",
    FalKeyMissing: "영상 모델 키가 설정되지 않았습니다. 관리자에게 알려 주세요."
  };

  function lrdErr(err) {
    var kind = err && err.type;
    return (kind && (LRD_ERRORS[kind] || SOUND_ERRORS[kind])) || errMessage(err);
  }

  function lrdInfo(ctx) {
    var info = (ctx.view || {}).lineRedo;
    return info && info.enabled ? info : null;
  }

  function lrdBusy(view) {
    var info = (view || {}).lineRedo;
    return !!(info && info.busy);
  }

  /* 폴링 결과가 다시 그려야 할 만큼 바뀌었나(진행이 끝났거나 원본이 다른 판으로 바뀜). 순수 함수. */
  function lrdChanged(before, after) {
    var a = (before || {}).lineRedo || {};
    var b = (after || {}).lineRedo || {};
    return !b.busy || a.current !== b.current || ((before || {}).source || {}).label !== ((after || {}).source || {}).label;
  }

  function lrdReload(ctx, view) {
    if (ctx.inEdit) return editTxLrdReload(ctx, view); // 작업 215b: ⑦ 안 대사 고치기 창
    ctx.view = view || {};
    ctx.saved = soundClone(ctx.view.lines);
    ctx.lines = soundClone(ctx.view.lines);
    ctx.pickN = null;
    soundRender(ctx);
    soundSchedule(ctx);
  }

  function lrdPath(tail) {
    return soundPath() + "/lines/" + tail;
  }

  function lrdUsd(usd) {
    return typeof usd === "number" ? castingUsdText(usd) : "가격 확인 불가";
  }

  /* 작업 상태 한 줄 {text, bad}. 순수 함수. */
  function lrdJobText(job) {
    if (!job || !job.status) return { text: "", bad: false };
    var model = job.modelLabel || job.model || "";
    var price = lrdUsd(job.usd) + (model ? " · " + model : "");
    if (job.status === "quoted") return { text: "금액 확인됨: " + price, bad: false };
    if (job.status === "running") return { text: "영상을 다시 만드는 중… (" + price + ")", bad: false };
    if (job.status === "splicing") return { text: "새 대사 영상을 원본에 잇는 중… (" + price + ")", bad: false };
    if (job.status === "done") {
      return { text: "다시 만들었습니다 (" + price + ")." + (job.message ? " " + job.message : ""), bad: false };
    }
    if (job.status === "failed") return { text: "다시 만들지 못했습니다: " + (job.error || "알 수 없는 오류"), bad: true };
    return { text: "", bad: false };
  }

  /* 편집 창 단추 상태. entry = lineRedo.lines[id](override·job), form = {text, dirty, busy}. 순수 함수. */
  function lrdControls(entry, form) {
    entry = entry || {};
    form = form || {};
    var job = entry.job || {};
    var live = job.status === "running" || job.status === "splicing";
    var saved = !!entry.override;
    var text = String(form.text || "").trim();
    var price = job.status === "quoted" ? lrdUsd(job.usd) : "금액 확인";
    return {
      save: { disabled: !!form.busy || live || !text },
      suggest: { disabled: !!form.busy || live },
      redo: { disabled: !!form.busy || live || !saved || !!form.dirty, label: "이 대사 영상 다시 만들기 (" + price + ")" },
      take: { visible: !!(saved && entry.override.scriptApplied) },
      hint: !saved ? "먼저 새 대사를 저장해 주세요." : form.dirty ? "바꾼 내용을 먼저 저장해 주세요." : ""
    };
  }

  function lrdConfirmText(line, job) {
    var range = job.range || {};
    var span = typeof range.start === "number" ? range.start.toFixed(1) + "–" + range.end.toFixed(1) + "초" : "";
    return (line.speaker || "이 줄") + " 대사 구간(" + span + ")만 영상을 다시 만듭니다. 그림·카메라·인물·옷·타이밍은 그대로 두고 " +
      "입 모양·연기·목소리를 새 대사에 맞춥니다. 모델: " + (job.modelLabel || job.model || "-") + " · 비용: " + lrdUsd(job.usd);
  }

  /* 작업 212 fix2: 확인 창의 모델 줄 [{id, text, checked}](싼 순서 그대로, 고른 줄 = job.model). 순수 함수. */
  function lrdModelChoices(job) {
    job = job || {};
    return (job.models || []).map(function (m) {
      return { id: m.id, text: (m.label || m.id) + " · " + lrdUsd(m.usd) + (m.original ? " (원래 모델)" : ""), checked: m.id === job.model };
    });
  }

  /* 콘텐츠 검사 거절 뒤 다음으로 싼 모델로 올렸을 때의 안내(없으면 ""). 순수 함수. */
  function lrdStepUpText(job) {
    var up = (job || {}).stepUp;
    if (!up) return "";
    return (up.fromLabel || up.fromModel || "앞 모델") + " 모델이 내용 검사에서 거절해 다음으로 싼 " + (job.modelLabel || job.model || "모델") +
      "(" + lrdUsd(job.usd) + ")로 바꿨습니다. 금액을 확인한 뒤 시작해 주세요.";
  }

  function lrdQuoteBody(ctx, id, model) {
    var body = { variant: ctx.variant, lineId: id };
    if (model) body.model = model;
    return body;
  }

  /* 확인 창(편집 창 안): 설명 · 모델 고르기(라디오, 바꾸면 견적 다시) · [이 금액으로 시작] · [취소]. on = {pick(id), start(), cancel()}. */
  function lrdConfirmPaint(ed, line, job, on, busy) {
    if (!ed.confirm) {
      ed.confirm = el("div", "vnew-lrd-confirm");
      ed.box.appendChild(ed.confirm);
    }
    var box = ed.confirm;
    clear(box);
    box.appendChild(el("p", "vnew-lrd-confirm-text", lrdConfirmText(line, job)));
    var up = lrdStepUpText(job);
    if (up) box.appendChild(el("p", "vnew-hint is-bad vnew-lrd-stepup", up));
    var list = el("div", "vnew-lrd-models");
    list.setAttribute("role", "radiogroup");
    list.setAttribute("aria-label", "영상 모델");
    lrdModelChoices(job).forEach(function (c) {
      var label = el("label", "vnew-lrd-model");
      var radio = el("input", "vnew-lrd-model-radio");
      radio.type = "radio";
      radio.name = "vnew-lrd-model-" + line.id;
      radio.value = c.id;
      radio.checked = c.checked;
      radio.disabled = !!busy;
      radio.addEventListener("change", function () {
        if (c.id !== job.model) on.pick(c.id);
      });
      label.appendChild(radio);
      label.appendChild(el("span", "", c.text));
      list.appendChild(label);
    });
    box.appendChild(list);
    var tools = el("div", "vnew-lrd-tools");
    var go = el("button", "primary-button vnew-lrd-go", "이 금액으로 시작 (" + lrdUsd(job.usd) + ")");
    go.type = "button";
    go.disabled = !!busy || typeof job.usd !== "number";
    go.addEventListener("click", function () {
      on.start();
    });
    var cancel = el("button", "outline-button vnew-lrd-cancel", "취소");
    cancel.type = "button";
    cancel.disabled = !!busy;
    cancel.addEventListener("click", function () {
      on.cancel();
    });
    tools.appendChild(go);
    tools.appendChild(cancel);
    box.appendChild(tools);
    ed.go = go;
    return box;
  }

  function lrdConfirmClose(ed) {
    if (ed.confirm) {
      ed.box.removeChild(ed.confirm);
      ed.confirm = null;
      ed.go = null;
    }
  }

  /* 대사 칸: 고친 대사가 있으면 원래 대사(회색)와 새 대사를 함께. */
  function lrdPaintLine(cell, line, entry) {
    clear(cell);
    var ov = (entry || {}).override;
    if (!ov) {
      cell.textContent = line.lineKo || "";
      return;
    }
    var original = (ov.original || {}).lineKo || line.lineKo || "";
    if (original !== ov.textKo) cell.appendChild(el("span", "vnew-lrd-original", original));
    var now = el("span", "vnew-lrd-new", ov.textKo || "");
    cell.appendChild(now);
    var tags = [];
    if (ov.speaker && ov.speaker !== line.speaker) tags.push("말하는 사람: " + ov.speaker);
    tags.push(ov.thought ? "속마음" : "실제 말");
    if (ov.deliveryKo) tags.push("말투: " + ov.deliveryKo);
    tags.push(ov.scriptApplied ? "대본에 반영됨" : "대본은 그대로");
    cell.appendChild(el("span", "vnew-lrd-tags", tags.join(" · ")));
  }

  function lrdRowTools(ctx, line, row, lineCell, cell) {
    var info = lrdInfo(ctx);
    if (!info) return;
    if (!ctx.lrd) ctx.lrd = { rows: [] };
    var rec = { line: line, row: row, lineCell: lineCell, status: el("span", "vnew-hint vnew-lrd-status"), editor: null, form: null };
    rec.status.setAttribute("role", "status");
    var open = el("button", "outline-button vnew-lrd-open", "대사 고치기");
    open.type = "button";
    open.addEventListener("click", function () {
      if (rec.editor) {
        row.removeChild(rec.editor.box);
        rec.editor = null;
        open.textContent = "대사 고치기";
        return;
      }
      rec.editor = lrdEditor(ctx, rec);
      row.appendChild(rec.editor.box);
      open.textContent = "닫기";
      lrdPaintRow(ctx, rec);
    });
    rec.open = open;
    cell.appendChild(open);
    cell.appendChild(rec.status);
    ctx.lrd.rows.push(rec);
    lrdPaintRow(ctx, rec);
  }

  function lrdEntry(ctx, id) {
    var info = lrdInfo(ctx);
    return (info && info.lines && info.lines[id]) || {};
  }

  function lrdPaintRows(ctx) {
    if (!ctx.lrd) return;
    ctx.lrd.rows.forEach(function (rec) {
      lrdPaintRow(ctx, rec);
    });
  }

  function lrdPaintRow(ctx, rec) {
    var entry = lrdEntry(ctx, rec.line.id);
    lrdPaintLine(rec.lineCell, rec.line, entry);
    var job = lrdJobText(entry.job);
    rec.status.textContent = job.text;
    if (job.bad) rec.status.classList.add("is-bad");
    else rec.status.classList.remove("is-bad");
    if (!rec.editor) return;
    var ed = rec.editor;
    var state_ = lrdControls(entry, ed.form);
    ed.save.disabled = state_.save.disabled;
    ed.suggest.disabled = state_.suggest.disabled;
    ed.redo.disabled = state_.redo.disabled;
    ed.redo.textContent = state_.redo.label;
    ed.take.style.display = state_.take.visible ? "" : "none";
    ed.hint.textContent = state_.hint;
  }

  function lrdNote(ed, text, bad) {
    ed.note.textContent = text;
    if (bad) ed.note.classList.add("is-bad");
    else ed.note.classList.remove("is-bad");
  }

  function lrdEditor(ctx, rec) {
    var info = lrdInfo(ctx) || {};
    var line = rec.line;
    var entry = lrdEntry(ctx, line.id);
    var ov = entry.override || {};
    var box = el("div", "vnew-lrd-editor");
    var ed = { box: box, form: { text: ov.textKo || line.lineKo || "", dirty: false, busy: false } };
    function touch() {
      ed.form.text = ed.text.value;
      ed.form.dirty = true;
      lrdPaintRow(ctx, rec);
    }
    var textLabel = el("label", "vnew-lrd-field");
    textLabel.appendChild(el("span", "vnew-lrd-label", "새 대사"));
    ed.text = el("textarea", "vnew-lrd-text");
    ed.text.value = ed.form.text;
    ed.text.maxLength = (info.limits || {}).textMax || 200;
    ed.text.addEventListener("input", touch);
    textLabel.appendChild(ed.text);
    box.appendChild(textLabel);

    var opts = el("div", "vnew-lrd-opts");
    var spLabel = el("label", "vnew-lrd-field");
    spLabel.appendChild(el("span", "vnew-lrd-label", "말하는 사람"));
    ed.speaker = el("select", "vnew-lrd-speaker");
    var names = (info.cast || []).slice();
    var current = ov.speaker || line.speaker || "";
    if (current && names.indexOf(current) < 0) names.unshift(current);
    names.forEach(function (name) {
      var option = el("option", "", name);
      option.value = name;
      if (name === current) option.selected = true;
      ed.speaker.appendChild(option);
    });
    ed.speaker.value = current;
    ed.speaker.addEventListener("change", touch);
    spLabel.appendChild(ed.speaker);
    opts.appendChild(spLabel);

    var voLabel = el("label", "vnew-lrd-field");
    voLabel.appendChild(el("span", "vnew-lrd-label", "목소리"));
    ed.voice = el("select", "vnew-lrd-voice");
    SOUND_VOICES.forEach(function (pair) {
      var option = el("option", "", pair[1]);
      option.value = pair[0];
      ed.voice.appendChild(option);
    });
    ed.voice.value = ov.lineId ? (ov.thought ? "thought" : "spoken") : (line.voice || "spoken");
    ed.voice.addEventListener("change", touch);
    voLabel.appendChild(ed.voice);
    opts.appendChild(voLabel);

    var dLabel = el("label", "vnew-lrd-field");
    dLabel.appendChild(el("span", "vnew-lrd-label", "말투 메모(선택)"));
    ed.delivery = el("input", "vnew-lrd-delivery");
    ed.delivery.type = "text";
    ed.delivery.placeholder = "예: 작게, 떨리는 목소리";
    ed.delivery.value = ov.deliveryKo || "";
    ed.delivery.maxLength = (info.limits || {}).deliveryMax || 80;
    ed.delivery.addEventListener("input", touch);
    dLabel.appendChild(ed.delivery);
    opts.appendChild(dLabel);
    box.appendChild(opts);

    ed.choices = el("div", "vnew-lrd-choices");
    box.appendChild(ed.choices);

    var scriptLabel = el("label", "vnew-lrd-check");
    ed.script = el("input", "vnew-lrd-script");
    ed.script.type = "checkbox";
    ed.script.checked = false;
    scriptLabel.appendChild(ed.script);
    scriptLabel.appendChild(el("span", "", "대본에도 반영 (다음에 만드는 테이크·촬영 지시서가 새 대사를 씁니다)"));
    box.appendChild(scriptLabel);

    var tools = el("div", "vnew-lrd-tools");
    ed.suggest = el("button", "outline-button vnew-lrd-suggest", "AI로 다시 쓰기");
    ed.suggest.type = "button";
    ed.suggest.title = "장면과 인물에 맞는 새 대사 3줄을 제안합니다(텍스트 모델 1회, 아주 적은 금액)";
    ed.suggest.addEventListener("click", function () {
      lrdSuggest(ctx, rec);
    });
    ed.save = el("button", "primary-button vnew-lrd-save", "저장");
    ed.save.type = "button";
    ed.save.addEventListener("click", function () {
      lrdSave(ctx, rec);
    });
    ed.redo = el("button", "primary-button vnew-lrd-redo", "이 대사 영상 다시 만들기 (금액 확인)");
    ed.redo.type = "button";
    ed.redo.addEventListener("click", function () {
      lrdRedo(ctx, rec);
    });
    ed.take = el("button", "outline-button vnew-lrd-take", "테이크 전체 다시 만들기");
    ed.take.type = "button";
    ed.take.title = info.takeRedoText || "";
    ed.take.addEventListener("click", function () {
      if (!window.confirm((info.takeRedoText || "") + "\n⑤ 영상으로 갈까요?")) return;
      state.planStep = 5;
      renderPlan();
    });
    [ed.suggest, ed.save, ed.redo, ed.take].forEach(function (b) {
      tools.appendChild(b);
    });
    box.appendChild(tools);
    ed.hint = el("p", "vnew-hint vnew-lrd-hint");
    box.appendChild(ed.hint);
    ed.note = el("p", "vnew-hint vnew-lrd-note");
    ed.note.setAttribute("role", "status");
    box.appendChild(ed.note);
    return ed;
  }

  function lrdBody(ctx, rec) {
    var ed = rec.editor;
    return {
      variant: ctx.variant, lineId: rec.line.id, textKo: ed.text.value, speaker: ed.speaker.value,
      thought: ed.voice.value === "thought", deliveryKo: ed.delivery.value, applyToScript: !!ed.script.checked
    };
  }

  function lrdSuggest(ctx, rec) {
    var ed = rec.editor;
    if (!ed || ed.form.busy) return;
    ed.form.busy = true;
    lrdPaintRow(ctx, rec);
    lrdNote(ed, "새 대사를 만드는 중…", false);
    var body = { variant: ctx.variant, lineId: rec.line.id, speaker: ed.speaker.value, thought: ed.voice.value === "thought" };
    api(lrdPath("suggest"), { method: "POST", body: JSON.stringify(body) })
      .then(function (result) {
        if (!soundAlive(ctx) || rec.editor !== ed) return;
        ed.form.busy = false;
        clear(ed.choices);
        ((result || {}).lines || []).forEach(function (text) {
          var pick = el("button", "outline-button vnew-lrd-choice", text);
          pick.type = "button";
          pick.addEventListener("click", function () {
            ed.text.value = text;
            ed.form.text = text;
            ed.form.dirty = true;
            lrdPaintRow(ctx, rec);
          });
          ed.choices.appendChild(pick);
        });
        lrdNote(ed, "하나를 고르거나 고쳐 적은 뒤 [저장]을 누르세요.", false);
        lrdPaintRow(ctx, rec);
      })
      .catch(function (err) {
        if (!soundAlive(ctx) || rec.editor !== ed) return;
        ed.form.busy = false;
        lrdNote(ed, lrdErr(err), true);
        lrdPaintRow(ctx, rec);
      });
  }

  function lrdSave(ctx, rec) {
    var ed = rec.editor;
    if (!ed || ed.form.busy) return;
    ed.form.busy = true;
    lrdPaintRow(ctx, rec);
    lrdNote(ed, "저장하는 중…", false);
    api(lrdPath("override"), { method: "PUT", body: JSON.stringify(lrdBody(ctx, rec)) })
      .then(function (view) {
        if (!soundAlive(ctx)) return;
        lrdReload(ctx, view);
      })
      .catch(function (err) {
        if (!soundAlive(ctx) || rec.editor !== ed) return;
        ed.form.busy = false;
        lrdNote(ed, "저장하지 못했습니다: " + lrdErr(err), true);
        lrdPaintRow(ctx, rec);
      });
  }

  function lrdSetJob(ctx, id, job) {
    var info = lrdInfo(ctx);
    if (!info) return;
    info.lines = info.lines || {};
    info.lines[id] = info.lines[id] || {};
    info.lines[id].job = job;
    if (job && (job.status === "running" || job.status === "splicing")) info.busy = true;
  }

  /* 견적 → 확인 창(모델 고르기 · 금액) → 시작. 기본 모델은 서버가 고른 가장 싼 모델이다(작업 212 fix2). 다른 모델을 고르면 견적을
     다시 받고 그 금액을 보인다. 금액이 바뀌면(CostMismatch) 같은 모델로 견적을 다시 받아 바뀐 금액을 보이고 창은 그대로 둔다. */
  function lrdRedo(ctx, rec) {
    var ed = rec.editor;
    if (!ed || ed.form.busy) return;
    var id = rec.line.id;
    var job = null;
    ed.form.busy = true;
    lrdPaintRow(ctx, rec);
    lrdNote(ed, "구간과 금액을 확인하는 중…", false);
    function alive() {
      return soundAlive(ctx) && rec.editor === ed;
    }
    function quote(model) {
      return api(lrdPath("quote"), { method: "POST", body: JSON.stringify(lrdQuoteBody(ctx, id, model)) }).then(function (r) {
        var fresh = (r || {}).quote || {};
        lrdSetJob(ctx, id, fresh);
        return fresh;
      });
    }
    function fail(err) {
      if (!alive()) return;
      if (job) paint(false);
      else ed.form.busy = false;
      lrdNote(ed, lrdErr(err), true);
      lrdPaintRow(ctx, rec);
    }
    function paint(busy) {
      lrdConfirmPaint(ed, rec.line, job, { pick: pick, start: start, cancel: cancel }, busy);
    }
    function show(fresh, note, bad) {
      if (!alive()) return;
      job = fresh;
      paint(false);
      lrdPaintRow(ctx, rec);
      lrdNote(ed, note || "", !!bad);
    }
    function pick(model) {
      paint(true);
      lrdNote(ed, "고른 모델의 금액을 확인하는 중…", false);
      quote(model).then(function (fresh) {
        show(fresh, "");
      }).catch(fail);
    }
    function cancel() {
      lrdConfirmClose(ed);
      ed.form.busy = false;
      lrdNote(ed, "", false);
      lrdPaintRow(ctx, rec);
    }
    function start() {
      var shown = job;
      paint(true);
      lrdNote(ed, "다시 만들기를 시작하는 중…", false);
      api(lrdPath("redo"), { method: "POST", body: JSON.stringify({ variant: ctx.variant, lineId: id, confirmUsd: shown.usd }) })
        .then(function (result) {
          if (!soundAlive(ctx)) return;
          lrdConfirmClose(ed);
          ed.form.busy = false;
          lrdSetJob(ctx, id, (result || {}).job || { status: "running" });
          lrdNote(ed, "시작했습니다. 끝나면 새 영상이 ⑥ 원본이 됩니다.", false);
          lrdPaintRow(ctx, rec);
          soundSchedule(ctx);
        })
        .catch(function (err) {
          if (!alive()) return null;
          if (!isCostMismatch(err)) return fail(err);
          return quote(shown.model).then(function (fresh) {
            show(fresh, costChangedText(shown.usd, fresh.usd), true);
          }).catch(fail);
        });
    }
    quote("").then(function (fresh) {
      show(fresh, "");
    }).catch(fail);
  }

  /* 대사 고친 판 목록 · [이전 영상으로 되돌리기]. 판이 없으면 그리지 않는다. */
  function lrdRevisionBar(ctx) {
    var info = lrdInfo(ctx);
    if (!info) return;
    if (info.error) {
      ctx.root.appendChild(el("p", "vnew-hint is-bad", info.error));
      return;
    }
    var revisions = info.revisions || [];
    if (!revisions.length) return;
    var bar = el("div", "vnew-lrd-revisions");
    bar.appendChild(el("span", "vnew-lrd-label", "대사 고친 영상"));
    var select = el("select", "vnew-lrd-revision");
    select.setAttribute("aria-label", "되돌릴 영상");
    var items = [{ r: 0, label: "원래 영상" }].concat(revisions.map(function (rev) {
      return { r: rev.r, label: "대사 고침 " + rev.r + (rev.usd !== undefined && rev.usd !== null ? " · " + lrdUsd(rev.usd) : "") };
    }));
    items.forEach(function (item) {
      var option = el("option", "", item.label + (item.r === (info.current || 0) ? " (지금)" : ""));
      option.value = String(item.r);
      if (item.r === (info.current || 0)) option.selected = true;
      select.appendChild(option);
    });
    var back = el("button", "outline-button vnew-lrd-revert", "이전 영상으로 되돌리기");
    back.type = "button";
    var note = el("span", "vnew-hint vnew-lrd-revnote");
    function paint() {
      back.disabled = info.busy || Number(select.value) === (info.current || 0);
    }
    select.addEventListener("change", paint);
    back.addEventListener("click", function () {
      back.disabled = true;
      note.textContent = "되돌리는 중…";
      api(lrdPath("revert"), { method: "POST", body: JSON.stringify({ variant: ctx.variant, r: Number(select.value) }) })
        .then(function (view) {
          if (soundAlive(ctx)) lrdReload(ctx, view);
        })
        .catch(function (err) {
          if (!soundAlive(ctx)) return;
          note.textContent = "되돌리지 못했습니다: " + lrdErr(err);
          paint();
        });
    });
    bar.appendChild(select);
    bar.appendChild(back);
    bar.appendChild(note);
    if (info.voiceOutdated) bar.appendChild(el("span", "vnew-hint vnew-lrd-outdated", info.outdatedText || LRD_OUTDATED));
    paint();
    ctx.root.appendChild(bar);
  }

  /* ================================================================ ⑥ 사운드 — 인물 목소리(작업 203)

     서버 voices_routes.py(작업 202). PUT /sound/voices 는 인물 목소리(프로필)를 저장한다(mode off = 지우기, variant 를 주면 그 그림체만).
     POST /sound/voice-preview 는 한 줄만 바꿔 들어 보고, POST /sound/voice-apply 는 기존 반영 작업(진행·버전)으로 전체를 바꾼다.
     두 POST 는 dryRun true 면 견적만 준다. 값을 먼저 받아 확인 창에 보이고, 확인하면 그 값을 confirmUsd 로 보낸다.
     보기(GET /sound)에 voices 가 없거나 목소리 라우트가 404 면 이 구역을 모두 숨긴다(기존 사운드 기능은 그대로).
     글은 모두 textContent(el), 소리는 서버가 준 presigned 주소만 쓰고 자동 재생하지 않는다. */

  var VOC_HINT = "한 번 정하면 다음 영상에도 같은 목소리로 맞춥니다.";
  var VOC_NONE = "아직 정하지 않음 — 영상마다 목소리가 달라질 수 있음";
  var VOC_SAVED = "저장된 목소리 사용 중";
  var VOC_NO_PROFILE_TIP = "이 인물의 목소리를 위 '캐릭터 목소리'에서 먼저 정해 주세요";
  var VOC_BADGE = "목소리 맞춤";
  var VOC_MNOTES = "모델 에러 로그 보기";
  var VOC_ERRORS = {
    NoVoiceProfile: "목소리를 먼저 정해 주세요.",
    CostMismatch: "금액이 바뀌었습니다. 다시 눌러 주세요.",
    NoFalKey: "목소리 모델 키가 설정되지 않았습니다. 관리자에게 알려 주세요.",
    BadVoiceRefRange: "기준 대사는 0.5초 이상이어야 합니다. 다른 대사를 골라 주세요."
  };

  function vocErr(err) {
    var kind = err && err.type;
    return (kind && VOC_ERRORS[kind]) || soundErrText(err);
  }

  function vocVariantTitle(variant) {
    return variant === "real" ? "실사" : "애니";
  }

  function vocOn(ctx) {
    return !!(ctx && ctx.voc && !ctx.voc.off);
  }

  function vocView(ctx) {
    return ((ctx && ctx.view) || {}).voices || {};
  }

  function vocHasAudio(ctx) {
    return !!(((ctx && ctx.view) || {}).source || {}).hasAudio;
  }

  /* 대사가 있는 인물(서버 차례 그대로). */
  function vocSpeakers(ctx) {
    return (vocView(ctx).speakers || []).filter(function (sp) {
      return sp && sp.speaker && Number(sp.lines) > 0;
    });
  }

  function vocSpeaker(ctx, name) {
    return vocSpeakers(ctx).filter(function (sp) {
      return sp.speaker === name;
    })[0] || null;
  }

  /* 목소리가 정해진(이 그림체에 쓸 프로필이 있는) 인물. */
  function vocProfiled(ctx) {
    return vocSpeakers(ctx).filter(function (sp) {
      return sp.usingSaved === true && !!sp.profile;
    });
  }

  function vocLinesOf(ctx, name) {
    return (ctx.lines || []).filter(function (line) {
      return line && line.speaker === name;
    });
  }

  function vocModelLabel(ctx, id) {
    var models = vocView(ctx).models || [];
    for (var i = 0; i < models.length; i++) {
      if (models[i] && models[i].id === id && models[i].label) return String(models[i].label);
    }
    return String(id || "모델 모름");
  }

  /* 대사 id → 대본 줄(사운드 줄 id = 대본 entries[].id). */
  function vocEntry(lineId) {
    var entries = (((state.plan || {}).script) || {}).entries || [];
    for (var i = 0; i < entries.length; i++) {
      if (entries[i] && String(entries[i].id) === String(lineId)) return entries[i];
    }
    return null;
  }

  /* "컷 3" · "컷 3, 4"(대본 panels). 없으면 화면 줄 차례 "n번째 줄". */
  function vocCutText(ctx, lineId) {
    var entry = vocEntry(lineId);
    var panels = (entry && entry.panels) || [];
    if (panels.length) return "컷 " + panels.join(", ");
    for (var i = 0; i < (ctx.lines || []).length; i++) {
      if (ctx.lines[i].id === lineId) return i + 1 + "번째 줄";
    }
    return "지난 영상";
  }

  /* 인물 이름 → 늘 같은 색(이름 글자 해시로 색상만 바꾼다). */
  function vocColor(name) {
    var text = String(name || "");
    var hash = 0;
    for (var i = 0; i < text.length; i++) hash = (hash * 31 + text.charCodeAt(i)) % 360;
    return "hsl(" + hash + ", 55%, 52%)";
  }

  function vocProfileText(ctx, sp) {
    var profile = sp && sp.profile;
    if (!profile) return VOC_NONE;
    var text;
    if (profile.mode === "clip") {
      var ref = profile.ref || {};
      var entry = vocEntry(ref.lineId);
      var words = (entry && entry.lineKo) || "";
      if (!words) {
        vocLinesOf(ctx, sp.speaker).forEach(function (line) {
          if (line.id === ref.lineId) words = line.lineKo || "";
        });
      }
      text = words ? "기준 대사: \u201c" + words + "\u201d (" + vocCutText(ctx, ref.lineId) + ")" : "기준 대사: (지난 영상의 대사)";
    } else {
      text = "기본 목소리: " + vocModelLabel(ctx, profile.model) + " · " + (profile.preset || "");
    }
    if ((profile.scope || sp.scope) === "variant") text += " · " + vocVariantTitle(ctx.variant) + "만";
    return text;
  }

  /* 금액 글: 견적 전 "$…", 모르면 서버 문구(가격 확인 불가). */
  function vocMoney(ctx, usd, known) {
    if (known === false || usd === null) return vocView(ctx).priceUnknownLabel || "가격 확인 불가";
    if (typeof usd !== "number" || !isFinite(usd)) return "$…";
    return "$" + (usd > 0 && usd < 0.01 ? usd.toFixed(3) : usd.toFixed(2));
  }

  function vocConfirmUsd(est) {
    return est && est.priceKnown === true && typeof est.usd === "number" ? est.usd : 0;
  }

  function vocLinePrice(ctx, lineId) {
    var est = ctx.voc.est;
    if (!est || !est.lines) return vocMoney(ctx, undefined);
    for (var i = 0; i < est.lines.length; i++) {
      var item = est.lines[i] || {};
      if (item.lineId === lineId) return vocMoney(ctx, item.usd, item.usd === null ? false : true);
    }
    return vocMoney(ctx, undefined);
  }

  /* soundRender 가 대사 목록 바로 앞에서 부른다: 구역을 붙이고 견적을 받는다. voices 가 없으면(꺼짐) 아무것도 그리지 않는다. */
  function vocMount(ctx) {
    var voices = (ctx.view || {}).voices;
    ctx.voc = {
      off: !(voices && typeof voices === "object" && voices.enabled !== false), section: null, est: null, estSeq: 0, rows: [],
      split: {}, open: {}, note: null, busy: false, previewing: false, applyBtn: null, actions: null, mnBtn: null, applied: false
    };
    if (ctx.voc.off) return;
    ctx.voc.section = el("section", "vnew-voc");
    ctx.voc.section.setAttribute("aria-label", "캐릭터 목소리");
    ctx.root.appendChild(ctx.voc.section);
    vocPaint(ctx);
    vocEstimate(ctx);
  }

  /* 404(목소리 꺼짐) 면 구역·줄 버튼·반영 버튼을 모두 뗀다. */
  function vocDisable(ctx) {
    var voc = ctx.voc;
    if (!voc) return;
    voc.off = true;
    [voc.section, voc.applyBtn, voc.mnBtn].forEach(function (node) {
      if (node && node.parentNode) node.parentNode.removeChild(node);
    });
    voc.rows.forEach(function (rec) {
      [rec.wrap, rec.box].forEach(function (node) {
        if (node && node.parentNode) node.parentNode.removeChild(node);
      });
    });
    voc.rows = [];
  }

  function vocGone(ctx, err) {
    if (err && err.status === 404) {
      vocDisable(ctx);
      return true;
    }
    return false;
  }

  /* 견적(voice-apply dryRun): 줄마다 금액 · 합계. 목소리가 정해진 인물이 없으면 받지 않는다. */
  function vocEstimate(ctx) {
    if (!vocOn(ctx)) return;
    var seq = ++ctx.voc.estSeq;
    var names = vocProfiled(ctx).map(function (sp) {
      return sp.speaker;
    });
    ctx.voc.est = null;
    if (!names.length || !vocHasAudio(ctx)) {
      vocPaintRows(ctx);
      vocPaintApply(ctx);
      return;
    }
    api(soundPath() + "/voice-apply", {
      method: "POST", body: JSON.stringify({ variant: ctx.variant, speakers: names, dryRun: true })
    })
      .then(function (result) {
        if (!soundAlive(ctx) || !vocOn(ctx) || seq !== ctx.voc.estSeq) return;
        ctx.voc.est = (result || {}).estimate || null;
        vocPaintRows(ctx);
        vocPaintApply(ctx);
      })
      .catch(function (err) {
        if (!soundAlive(ctx) || !vocOn(ctx) || seq !== ctx.voc.estSeq) return;
        if (vocGone(ctx, err)) return;
        ctx.voc.est = null;
        vocPaintRows(ctx);
        vocPaintApply(ctx);
      });
  }

  /* 구역 다시 그리기(카드만). 줄 버튼·반영 버튼은 vocPaintRows·vocPaintApply. */
  function vocPaint(ctx) {
    var voc = ctx.voc;
    var section = voc.section;
    if (!section) return;
    clear(section);
    section.appendChild(el("h3", "vnew-voc-title", "캐릭터 목소리"));
    section.appendChild(el("p", "vnew-hint vnew-voc-hint", VOC_HINT));
    var speakers = vocSpeakers(ctx);
    if (!speakers.length) {
      section.appendChild(el("p", "vnew-hint", "목소리를 정할 대사가 없습니다."));
      return;
    }
    var cards = el("div", "vnew-voc-cards");
    speakers.forEach(function (sp) {
      cards.appendChild(vocCard(ctx, sp));
    });
    section.appendChild(cards);
  }

  function vocSplitOn(ctx, sp) {
    if (Object.prototype.hasOwnProperty.call(ctx.voc.split, sp.speaker)) return ctx.voc.split[sp.speaker];
    return (sp.scope || (sp.profile || {}).scope) === "variant";
  }

  function vocHasOverride(ctx, name) {
    var entry = (vocView(ctx).profiles || {})[name] || {};
    return !!((entry.perVariant || {})[ctx.variant]);
  }

  function vocCard(ctx, sp) {
    var name = sp.speaker;
    var card = el("div", "vnew-voc-card");
    var head = el("div", "vnew-voc-head");
    var chip = el("span", "vnew-voc-chip");
    chip.style.backgroundColor = vocColor(name);
    chip.setAttribute("aria-hidden", "true");
    head.appendChild(chip);
    head.appendChild(el("strong", "vnew-voc-name", name));
    head.appendChild(el("span", "vnew-voc-count", sp.lines + "줄"));
    if (sp.usingSaved === true) head.appendChild(el("span", "vnew-badge vnew-voc-saved", VOC_SAVED));
    card.appendChild(head);
    card.appendChild(el("p", "vnew-voc-current", vocProfileText(ctx, sp)));
    var profile = sp.profile || {};
    if (profile.mode === "clip" && profile.ref && profile.ref.url) {
      var refAudio = el("audio", "vnew-voc-audio");
      refAudio.controls = true;
      refAudio.preload = "none";
      refAudio.src = profile.ref.url;
      refAudio.setAttribute("aria-label", name + " 기준 목소리");
      card.appendChild(refAudio);
    }

    var tools = el("div", "vnew-voc-tools");
    var clipBtn = el("button", "outline-button vnew-voc-clip", "기준 대사로 맞추기");
    clipBtn.type = "button";
    clipBtn.setAttribute("aria-expanded", ctx.voc.open[name] === "clip" ? "true" : "false");
    clipBtn.addEventListener("click", function () {
      ctx.voc.open[name] = ctx.voc.open[name] === "clip" ? "" : "clip";
      vocPaint(ctx);
    });
    var presetBtn = el("button", "outline-button vnew-voc-presetbtn", "기본 목소리 고르기");
    presetBtn.type = "button";
    presetBtn.setAttribute("aria-expanded", ctx.voc.open[name] === "preset" ? "true" : "false");
    presetBtn.addEventListener("click", function () {
      ctx.voc.open[name] = ctx.voc.open[name] === "preset" ? "" : "preset";
      vocPaint(ctx);
    });
    tools.appendChild(clipBtn);
    tools.appendChild(presetBtn);

    var splitLabel = el("label", "vnew-voc-split");
    var split = el("input", "vnew-voc-split-input");
    split.type = "checkbox";
    split.checked = vocSplitOn(ctx, sp);
    split.addEventListener("change", function () {
      if (split.checked) {
        ctx.voc.split[name] = true;
        ctx.voc.note = { speaker: name, text: "지금부터 고르는 목소리는 " + vocVariantTitle(ctx.variant) + "에만 씁니다.", bad: false };
        vocPaint(ctx);
        return;
      }
      if (vocHasOverride(ctx, name)) {
        var other = vocVariantTitle(ctx.variant);
        if (!window.confirm(other + " 전용 목소리를 지우고 두 그림체가 함께 쓰는 목소리로 돌아갈까요?")) {
          split.checked = true;
          return;
        }
        ctx.voc.split[name] = false;
        vocSave(ctx, name, { speaker: name, mode: "off", variant: ctx.variant }, "함께 쓰는 목소리로 돌아갔습니다.");
        return;
      }
      ctx.voc.split[name] = false;
      ctx.voc.note = null;
      vocPaint(ctx);
    });
    splitLabel.appendChild(split);
    splitLabel.appendChild(el("span", null, "실사/애니 따로 정하기"));
    splitLabel.title = "켜면 지금 그림체(" + vocVariantTitle(ctx.variant) + ")에만 쓰는 목소리를 따로 정합니다";
    tools.appendChild(splitLabel);
    card.appendChild(tools);

    if (ctx.voc.open[name] === "clip") card.appendChild(vocClipPanel(ctx, sp));
    else if (ctx.voc.open[name] === "preset") card.appendChild(vocPresetPanel(ctx, sp));

    var note = ctx.voc.note && ctx.voc.note.speaker === name ? ctx.voc.note : null;
    var msg = el("p", "vnew-hint vnew-voc-msg" + (note && note.bad ? " is-bad" : ""), note ? note.text : "");
    msg.setAttribute("role", "status");
    card.appendChild(msg);
    return card;
  }

  /* 고르는 중인 값 + 그림체 덮어쓰기 켬이면 variant. */
  function vocBody(ctx, name, body) {
    body.speaker = name;
    var sp = vocSpeaker(ctx, name) || { speaker: name };
    if (vocSplitOn(ctx, sp)) body.variant = ctx.variant;
    return body;
  }

  function vocClipPanel(ctx, sp) {
    var panel = el("div", "vnew-voc-panel");
    var current = (sp.profile && sp.profile.mode === "clip" && sp.profile.ref) ? sp.profile.ref.lineId : null;
    var list = el("div", "vnew-voc-list");
    vocLinesOf(ctx, sp.speaker).forEach(function (line) {
      var item = el("div", "vnew-voc-item" + (line.id === current ? " is-on" : ""));
      item.appendChild(el("span", "vnew-voc-itemtext", vocCutText(ctx, line.id) + " · " + (line.lineKo || "")));
      var play = el("button", "outline-button vnew-voc-play", "▶");
      play.type = "button";
      play.title = "이 대사 구간을 원본 영상에서 듣기";
      play.setAttribute("aria-label", play.title);
      play.addEventListener("click", function () {
        soundListen(ctx, line);
      });
      var pick = el("button", "outline-button vnew-voc-pick", "이 목소리로 정하기");
      pick.type = "button";
      pick.disabled = !vocHasAudio(ctx) || ctx.voc.busy;
      pick.addEventListener("click", function () {
        var genId = vocView(ctx).genId;
        var ref = { lineId: line.id, variant: ctx.variant };
        if (genId) ref.genId = genId;
        vocSave(ctx, sp.speaker, vocBody(ctx, sp.speaker, { mode: "clip", ref: ref }), "기준 대사로 목소리를 정했습니다.");
      });
      item.appendChild(play);
      item.appendChild(pick);
      list.appendChild(item);
    });
    panel.appendChild(list);
    return panel;
  }

  function vocPresetPanel(ctx, sp) {
    var panel = el("div", "vnew-voc-panel");
    var choices = [];
    (vocView(ctx).models || []).forEach(function (model) {
      if (!model || (model.modes || []).indexOf("preset") < 0) return;
      (model.presets || []).forEach(function (preset) {
        choices.push({ model: model.id, preset: preset, label: (model.label || model.id) + " · " + preset });
      });
    });
    var row = el("div", "vnew-voc-presetrow");
    var select = el("select", "vnew-voc-preset");
    select.setAttribute("aria-label", sp.speaker + " 기본 목소리");
    var profile = sp.profile || {};
    choices.forEach(function (choice, index) {
      var option = el("option", "", choice.label);
      option.value = String(index);
      if (profile.mode === "preset" && profile.model === choice.model && profile.preset === choice.preset) option.selected = true;
      select.appendChild(option);
    });
    var save = el("button", "outline-button vnew-voc-presetsave", "이 목소리로 정하기");
    save.type = "button";
    save.disabled = !choices.length || ctx.voc.busy;
    save.addEventListener("click", function () {
      var choice = choices[Number(select.value) || 0];
      if (!choice) return;
      vocSave(ctx, sp.speaker, vocBody(ctx, sp.speaker, { mode: "preset", model: choice.model, preset: choice.preset }),
        "기본 목소리를 " + choice.label + "(으)로 정했습니다.");
    });
    row.appendChild(select);
    row.appendChild(save);

    var line = vocLinesOf(ctx, sp.speaker)[0];
    var holder = { box: el("div", "vnew-voc-cmp"), status: null, players: null };
    var listen = el("button", "outline-button vnew-voc-presetlisten",
      "미리 듣기 (" + (line ? vocLinePrice(ctx, line.id) : vocMoney(ctx, undefined)) + ")");
    listen.type = "button";
    var usable = !!line && sp.usingSaved === true && vocHasAudio(ctx);
    listen.disabled = !usable || ctx.voc.previewing;
    listen.title = usable ? "저장한 목소리로 " + vocCutText(ctx, line.id) + " 대사 하나를 바꿔 들어 봅니다(유료)" : VOC_NO_PROFILE_TIP;
    listen.addEventListener("click", function () {
      vocPreview(ctx, line, holder);
    });
    row.appendChild(listen);
    panel.appendChild(row);
    panel.appendChild(holder.box);
    return panel;
  }

  function vocSave(ctx, name, body, doneText) {
    if (ctx.voc.busy) return;
    ctx.voc.busy = true;
    ctx.voc.note = { speaker: name, text: "목소리를 저장하는 중…", bad: false };
    vocPaint(ctx);
    api(soundPath() + "/voices", { method: "PUT", body: JSON.stringify(body) })
      .then(function (result) {
        if (!soundAlive(ctx) || !vocOn(ctx)) return;
        ctx.voc.busy = false;
        if (result && result.voices) ctx.view.voices = result.voices;
        ctx.voc.open[name] = "";
        ctx.voc.note = { speaker: name, text: doneText, bad: false };
        vocPaint(ctx);
        vocEstimate(ctx);
      })
      .catch(function (err) {
        if (!soundAlive(ctx) || !vocOn(ctx)) return;
        ctx.voc.busy = false;
        if (vocGone(ctx, err)) return;
        ctx.voc.note = { speaker: name, text: "목소리를 저장하지 못했습니다: " + vocErr(err), bad: true };
        vocPaint(ctx);
      });
  }

  /* 줄마다 [목소리 맞춰 미리 듣기 ($x)] — soundRow 가 ▶ 이 구간 듣기 칸에 붙인다. 견줘 듣기 칸은 처음 누를 때 줄 아래에 붙는다. */
  function vocRowTools(ctx, line, row, cell) {
    if (!vocOn(ctx)) return;
    cell.classList.add("vnew-voc-cell");
    var wrap = el("span", "vnew-voc-pwrap");
    var btn = el("button", "outline-button vnew-voc-preview", "");
    btn.type = "button";
    var rec = { line: line, row: row, wrap: wrap, btn: btn, box: null, holder: null };
    btn.addEventListener("click", function () {
      if (!rec.holder) {
        rec.box = el("div", "vnew-voc-cmp vnew-voc-rowcmp");
        rec.holder = { box: rec.box, status: null, players: null };
        row.appendChild(rec.box);
      }
      vocPreview(ctx, line, rec.holder);
    });
    wrap.appendChild(btn);
    cell.appendChild(wrap);
    ctx.voc.rows.push(rec);
    vocPaintRow(ctx, rec);
  }

  function vocPaintRow(ctx, rec) {
    var sp = vocSpeaker(ctx, rec.line.speaker);
    var has = !!(sp && sp.usingSaved === true && sp.profile);
    rec.btn.textContent = "목소리 맞춰 미리 듣기 (" + vocLinePrice(ctx, rec.line.id) + ")";
    rec.btn.disabled = !has || !vocHasAudio(ctx) || ctx.voc.previewing;
    var tip = has ? "이 줄만 맞춘 목소리로 바꿔 원래 소리와 견줘 들어 봅니다(유료)" : VOC_NO_PROFILE_TIP;
    rec.btn.title = tip;
    rec.wrap.title = tip; // 꺼진 버튼은 브라우저에 따라 title 이 안 보여 감싼 칸에도 둔다
  }

  function vocPaintRows(ctx) {
    if (!vocOn(ctx)) return;
    ctx.voc.rows.forEach(function (rec) {
      vocPaintRow(ctx, rec);
    });
  }

  function vocPreviewConfirmText(ctx, line, est) {
    var item = ((est || {}).lines || [])[0] || {};
    var seconds = typeof item.seconds === "number" ? ", " + item.seconds.toFixed(1) + "초" : "";
    return line.speaker + "의 대사 한 줄(" + vocCutText(ctx, line.id) + seconds + ")을 맞춘 목소리로 바꿔 들어 봅니다.\n" +
      "비용: " + vocMoney(ctx, est ? est.usd : null, est ? est.priceKnown : false) + "\n계속할까요?";
  }

  function vocHolderStatus(holder, text, bad) {
    if (!holder.status) {
      holder.status = el("p", "vnew-hint vnew-voc-cmpstatus");
      holder.status.setAttribute("role", "status");
      holder.box.appendChild(holder.status);
    }
    holder.status.textContent = text;
    if (bad) holder.status.classList.add("is-bad");
    else holder.status.classList.remove("is-bad");
  }

  /* 한 줄 들어보기: dryRun 견적 → 확인 창(금액) → 보내기 → [원래 소리]·[맞춘 소리] 두 플레이어. */
  function vocPreview(ctx, line, holder) {
    if (!vocOn(ctx) || ctx.voc.previewing || !line) return;
    ctx.voc.previewing = true;
    vocPaintRows(ctx);
    vocHolderStatus(holder, "금액을 확인하는 중…", false);
    var path = soundPath() + "/voice-preview";
    function finish(text, bad) {
      ctx.voc.previewing = false;
      vocHolderStatus(holder, text, bad);
      vocPaintRows(ctx);
    }
    api(path, { method: "POST", body: JSON.stringify({ variant: ctx.variant, lineId: line.id, speaker: line.speaker, dryRun: true }) })
      .then(function (result) {
        if (!soundAlive(ctx) || !vocOn(ctx)) return null;
        var est = (result || {}).estimate || null;
        if (!window.confirm(vocPreviewConfirmText(ctx, line, est))) {
          finish("", false);
          return null;
        }
        vocHolderStatus(holder, "목소리를 맞추는 중… (1분 안쪽)", false);
        return api(path, {
          method: "POST",
          body: JSON.stringify({ variant: ctx.variant, lineId: line.id, speaker: line.speaker, confirmUsd: vocConfirmUsd(est) })
        });
      })
      .then(function (result) {
        if (!result || !soundAlive(ctx) || !vocOn(ctx)) return;
        var preview = result.preview || {};
        vocShowCompare(ctx, line, holder, preview);
        var charge = result.charge || {};
        finish("맞춘 소리를 만들었습니다" + (typeof charge.usd === "number" ? " · " + vocMoney(ctx, charge.usd, true) : "") + ".", false);
      })
      .catch(function (err) {
        if (!soundAlive(ctx) || !vocOn(ctx)) return;
        if (vocGone(ctx, err)) return;
        finish("들어 보지 못했습니다: " + vocErr(err), true);
      });
  }

  function vocPlayer(title, url, label) {
    var box = el("div", "vnew-voc-pbox");
    box.appendChild(el("span", "vnew-voc-ptitle", title));
    var audio = el("audio", "vnew-voc-audio");
    audio.controls = true;
    audio.preload = "metadata";
    audio.autoplay = false;
    audio.setAttribute("aria-label", label);
    if (url) audio.src = url;
    box.appendChild(audio);
    return box;
  }

  function vocShowCompare(ctx, line, holder, preview) {
    if (holder.players && holder.players.parentNode) holder.players.parentNode.removeChild(holder.players);
    var source = ((ctx.view || {}).source || {}).videoUrl || "";
    var start = typeof preview.start === "number" ? preview.start : line.start;
    var end = typeof preview.end === "number" ? preview.end : line.end;
    // 원래 소리 = 원본 영상 presigned 주소 + 미디어 조각(#t=시작,끝 — 주소의 서명은 그대로, 조각은 서버로 가지 않는다)
    var original = source ? source + "#t=" + Number(start || 0).toFixed(2) + "," + Number(end || 0).toFixed(2) : "";
    var players = el("div", "vnew-voc-players");
    players.appendChild(vocPlayer("원래 소리", original, line.speaker + " 원래 소리"));
    players.appendChild(vocPlayer("맞춘 소리", preview.url || "", line.speaker + " 맞춘 소리"));
    holder.players = players;
    holder.box.insertBefore(players, holder.status || null);
  }

  /* [목소리 맞춰 반영 ($total)] — soundRender 가 [소리에 반영] 바로 뒤에 붙인다. */
  function vocApplyButton(ctx, actions) {
    if (!vocOn(ctx)) return;
    var btn = el("button", "outline-button vnew-voc-apply", "");
    btn.type = "button";
    btn.addEventListener("click", function () {
      vocApply(ctx);
    });
    ctx.voc.applyBtn = btn;
    ctx.voc.actions = actions;
    actions.appendChild(btn);
    vocPaintApply(ctx);
  }

  function vocPaintApply(ctx) {
    if (!vocOn(ctx) || !ctx.voc.applyBtn) return;
    var est = ctx.voc.est;
    var names = vocProfiled(ctx);
    var running = (((ctx.view || {}).job) || {}).status === "running";
    ctx.voc.applyBtn.textContent = "목소리 맞춰 반영 (" + (est ? vocMoney(ctx, est.usd, est.priceKnown) : vocMoney(ctx, undefined)) + ")";
    ctx.voc.applyBtn.disabled = !names.length || !vocHasAudio(ctx) || ctx.busy || ctx.voc.busy || running;
    ctx.voc.applyBtn.title = names.length
      ? "목소리를 정한 인물의 대사를 모두 맞춘 목소리로 바꾼 새 버전을 만듭니다(유료)"
      : "위 '캐릭터 목소리'에서 인물 목소리를 먼저 정해 주세요";
  }

  /* 확인 창 글: 바꿀 인물·줄 수(견적 줄 기준)·합계. */
  function vocApplyConfirmText(ctx, names, est) {
    var counts = {};
    ((est || {}).lines || []).forEach(function (item) {
      counts[item.speaker] = (counts[item.speaker] || 0) + 1;
    });
    var total = 0;
    var rows = names.map(function (name) {
      total += counts[name] || 0;
      return "- " + name + ": " + (counts[name] || 0) + "줄";
    });
    return "목소리를 맞춰 새 버전을 만듭니다.\n" + rows.join("\n") + "\n합계 " + total + "줄 · " +
      vocMoney(ctx, est ? est.usd : null, est ? est.priceKnown : false) +
      "\n목소리를 바꾸지 못한 줄은 원래 소리를 그대로 둡니다. 계속할까요?";
  }

  /* 고친 구간이 있으면 먼저 저장(soundApply 와 같은 PUT). */
  function vocSaveLines(ctx) {
    var patch = soundPatch(ctx.lines, ctx.saved);
    if (!patch.length) return Promise.resolve(true);
    return api(soundPath(), { method: "PUT", body: JSON.stringify({ variant: ctx.variant, lines: patch }) }).then(function (saved) {
      if (!soundAlive(ctx)) return false;
      ctx.saved = soundClone((saved || {}).lines);
      var byId = {};
      ctx.saved.forEach(function (item) {
        byId[item.id] = item;
      });
      ctx.lines.forEach(function (line) {
        var after = byId[line.id];
        if (after) ["voice", "start", "end", "auto"].forEach(function (key) {
          line[key] = after[key];
        });
      });
      return true;
    });
  }

  function vocApply(ctx) {
    if (!vocOn(ctx) || ctx.busy || ctx.voc.busy) return;
    var names = vocProfiled(ctx).map(function (sp) {
      return sp.speaker;
    });
    if (!names.length) return;
    var view = ctx.view || {};
    var duration = Number((view.source || {}).duration) || 0;
    var minSeconds = Number((view.limits || {}).minLineSeconds) || 0.2;
    for (var i = 0; i < ctx.lines.length; i++) {
      var problem = soundRangeProblem(ctx.lines[i], duration, minSeconds);
      if (problem) {
        soundJobText(ctx, i + 1 + "번째 줄: " + problem, true);
        return;
      }
    }
    var path = soundPath() + "/voice-apply";
    ctx.voc.busy = true;
    vocPaintApply(ctx);
    soundJobText(ctx, "금액을 확인하는 중…", false);
    function stop(text, bad) {
      ctx.voc.busy = false;
      soundRenderJob(ctx);
      if (text) soundJobText(ctx, text, bad);
    }
    vocSaveLines(ctx)
      .then(function (ok) {
        if (!ok || !soundAlive(ctx) || !vocOn(ctx)) return null;
        return api(path, { method: "POST", body: JSON.stringify({ variant: ctx.variant, speakers: names, dryRun: true }) });
      })
      .then(function (result) {
        if (!result || !soundAlive(ctx) || !vocOn(ctx)) return null;
        var est = result.estimate || null;
        ctx.voc.est = est;
        vocPaintRows(ctx);
        if (!window.confirm(vocApplyConfirmText(ctx, names, est))) {
          stop("", false);
          return null;
        }
        soundJobText(ctx, "목소리 맞춤을 시작하는 중…", false);
        return api(path, { method: "POST", body: JSON.stringify({ variant: ctx.variant, speakers: names, confirmUsd: vocConfirmUsd(est) }) });
      })
      .then(function (result) {
        if (!result || !soundAlive(ctx) || !vocOn(ctx)) return;
        ctx.voc.busy = false;
        ctx.voc.applied = true;
        ctx.view.job = result.job || { status: "running" };
        soundRenderJob(ctx);
        soundSchedule(ctx);
      })
      .catch(function (err) {
        if (!soundAlive(ctx) || !vocOn(ctx)) return;
        if (vocGone(ctx, err)) {
          ctx.voc.busy = false;
          soundRenderJob(ctx);
          return;
        }
        if (err && err.type === "SoundRunning") {
          ctx.voc.busy = false;
          ctx.view.job = { status: "running" };
          soundRenderJob(ctx);
          soundSchedule(ctx);
          return;
        }
        if (err && err.type === "CostMismatch") vocEstimate(ctx);
        stop("목소리를 맞추지 못했습니다: " + vocErr(err), true);
      });
  }

  /* 가장 최근 버전의 목소리 맞춤 요약(없으면 null). */
  function vocLastSummary(ctx) {
    var versions = (ctx.view || {}).versions || [];
    var last = versions[versions.length - 1];
    return last && last.voices && typeof last.voices === "object" ? last.voices : null;
  }

  /* soundRenderJob 끝에서: 진행(n/m줄) · 끝 문구(n/m 줄 목소리 맞춤, k줄 원음 유지) · 원음 유지 줄이 있으면 [모델 에러 로그 보기]. */
  function vocRenderJob(ctx) {
    if (!vocOn(ctx)) return;
    var job = (ctx.view || {}).job || {};
    var progress = job.progress || null;
    if (job.status === "running" && progress && progress.total > 0) {
      soundJobText(ctx, "목소리를 맞추는 중… " + progress.done + "/" + progress.total + "줄", false);
    } else if (job.status === "done" && job.message) {
      soundJobText(ctx, job.message, false);
    }
    var summary = vocLastSummary(ctx);
    var showLog = (job.status === "done" && !!job.message && !!summary && Number(summary.kept) > 0) ||
      (job.status === "failed" && ctx.voc.applied);
    if (showLog && !ctx.voc.mnBtn && ctx.voc.actions) {
      var btn = el("button", "outline-button vnew-voc-mnotes", VOC_MNOTES);
      btn.type = "button";
      btn.title = "목소리를 바꾸지 못한 줄의 모델 기록을 봅니다";
      btn.addEventListener("click", function () {
        mnotesOpen({ pid: state.projectId, variant: ctx.variant });
      });
      ctx.voc.mnBtn = btn;
      ctx.voc.actions.appendChild(btn);
    } else if (!showLog && ctx.voc.mnBtn) {
      if (ctx.voc.mnBtn.parentNode) ctx.voc.mnBtn.parentNode.removeChild(ctx.voc.mnBtn);
      ctx.voc.mnBtn = null;
    }
    vocPaintApply(ctx);
  }

  /* 반영 버전 목록: 목소리 맞춤 버전 표시(글 · 배지). */
  function vocVersionTag(version) {
    return version && version.voices ? " · " + VOC_BADGE : "";
  }

  function vocVersionBadge(version) {
    return version && version.voices ? el("span", "vnew-badge vnew-voc-badge", VOC_BADGE) : null;
  }

  /* ---------------------------------------------------------------- 작업 203 끝 */

  /* ================================================================ ⑦ 편집(영상 편집 타임라인, 지시서 111)

     ⑤ 결과 영상을 클립으로 늘어놓고 자르기·끝 끌기·순서·삭제·소리 끄기를 고친다. 서버 /api/vnew/p/<pid>/edit*(edit.py,
     design §4)를 그대로 쓴다. 미리보기는 <video> 2개를 번갈아 쓰고(다음 클립을 미리 불러 둔다), 썸네일은 서버 필름스트립
     JPEG 를 CSS 배경으로 잘라 그리고(버킷 CORS 가 없어 캔버스에 그리지 않는다), 파형은 서버 피크 배열을 SVG 막대로 그린다.
     편집은 실행 취소 100단계, 바뀐 뒤 0.8초 디바운스로 PUT(한 번에 하나), 409 EditChanged 면 다시 불러온다.
     글은 모두 textContent(el), CSS 클래스는 vnew-edit- 접두사. edit* 순수 함수는 DOM 없이 검사한다(check_vpoc edu-*).
     지시서 115: 양 끝 끌기로 원본 처음·끝까지 늘이기, 클립 속도(0.5~2×, 타임라인 길이 = 원본 구간 ÷ 속도, 미리보기 playbackRate)·
     길이(초) 입력·[ ] 1프레임 다듬기, 재료가 없어지면 안내(editShowGone). 검사는 check_vpoc ert-*. */

  var EDIT_SAVE_MS = 800; // 바뀐 뒤 저장(PUT)까지 디바운스
  var EDIT_PROBE_RETRY_MS = 1000; // probing(서버가 영상 길이를 재는 중)이면 다시 불러오는 간격
  var EDIT_PROBE_MAX_TRIES = 90; // 1.5분 넘게 probing 이면 멈추고 안내한다
  var EDIT_EXPORT_POLL_MS = 3000; // 내보내기 queued·running 동안 상태 다시 읽기
  var EDIT_UNDO_MAX = 100; // 실행 취소 단계
  var EDIT_MIN_PPS = 20; // 확대 하한(px/초)
  var EDIT_MAX_PPS = 400; // 확대 상한(px/초)
  var EDIT_DEFAULT_FPS = 24; // 재료 fps 를 모를 때의 한 프레임
  var EDIT_MIN_CLIP_S = 0.04; // 서버 MIN_CLIP_S(GET limits.minClipSeconds 가 있으면 그 값)
  var EDIT_THUMB_H = 48; // 영상 트랙 썸네일 높이(px). vnew.css .vnew-edit-clip 높이와 같다
  var EDIT_TAIL_PX = 120; // 타임라인 끝 뒤 여백(놓을 자리·끝의 재생헤드)
  var EDIT_DRAG_PX = 6; // 클립 몸통을 이만큼 움직여야 순서 바꾸기(그보다 적으면 누르기 = 고르기)
  var EDIT_MEDIA_TTL_MS = 50 * 60 * 1000; // 필름스트립 presigned(1시간)가 끝나기 전에 다시 받는다
  var EDIT_URL_RETRY_MS = 15000; // 영상 오류(서명 만료 등)로 주소를 다시 받는 최소 간격
  var EDIT_HELP_KEY = "vnew-edit-help-closed"; // 도움말 닫음(localStorage)
  // 작업 219b: 사용자 지시(11-10) — xml · prproj 내보내기는 화면에서 뺀다(서버 기능은 남김). false 면 215b 프리미어 묶음 칸 ·
  // 215c Pr 버튼을 만들지 않는다(Tab 으로도 닿지 않고 /edit/premiere 도 부르지 않는다).
  var EDIT_PREMIERE_UI = false;
  var EDIT_HELP_TEXT = "재생헤드를 옮기고 ✂ 또는 S로 자르고, 클립 양 끝을 끌어 줄이거나 원본 길이까지 늘이세요. 고른 클립은 속도·길이(초)로도 바꿀 수 있습니다.";
  var EDIT_CHANGED_TEXT = "다른 곳에서 바뀌어 다시 불러왔습니다.";
  var EDIT_GONE_TEXT = "편집할 영상이 아직 없습니다. ⑤에서 영상을 먼저 만들어 주세요."; // 서버 문구가 없을 때(지시서 115)
  var EDIT_SPEEDS = [0.5, 0.75, 1, 1.25, 1.5, 2]; // 클립 속도(지시서 115, 서버 edit.SPEEDS 와 같다)
  // 작업 182: J 컷(audioLead)·L 컷(audioTail)·다음 클립으로 디졸브(transition). 서버 edit.AUDIO_SHIFT_*·DISSOLVE_*·OFFSCREEN_GAIN 과 같다.
  // 앞당김·이어 감은 타임라인 초 0~2(0.05 단위), 디졸브는 4값이고 앞뒤 두 클립 중 짧은 쪽(속도 반영)의 45% 까지다.
  var EDIT_SHIFT_MAX_S = 2;
  var EDIT_SHIFT_STEP_S = 0.05;
  var EDIT_DISSOLVES = [0.25, 0.5, 0.75, 1];
  var EDIT_DISSOLVE_SHARE = 0.45;
  var EDIT_OFFSCREEN_GAIN = 0.6; // 미리보기: 화면에 없는 클립 소리(앞당김·이어 감) 크기
  var EDIT_TR_SHORT = "클립이 짧아 쓸 수 없습니다";
  var EDIT_MUTED_NOTE = "소리 끈 클립";
  var EDIT_FX_NOTE = "J/L컷·디졸브·대사 타이밍은 내보낸 영상에서 정확히 확인할 수 있습니다"; // 작업 186: 대사 타이밍도 근사로만 보인다
  // 작업 186: 대사 타이밍(audioSlip) — 클립 소리만 화면 안에서 −1~1초(0.05초 단위, 양수 = 소리가 늦게). 서버 edit.AUDIO_SLIP_MAX_S 와 같다.
  var EDIT_SLIP_MAX_S = 1;
  var EDIT_SLIP_NOTE = "소리만 옮깁니다. 말하는 사람 입이 화면에 보이면 입 모양과 어긋날 수 있습니다.";
  // 작업 188: 고른 컷부터 재생([▶ 이 컷 처음부터 재생] · [이 컷만 재생] · 클립 두 번 누르기 · Enter / Shift+Enter).
  var EDIT_CUT_PLAY_NOTE = "더블클릭 또는 Enter: 이 컷부터 재생 · Shift+Enter: 이 컷만";
  var EDIT_CUT_PLAY_PAUSE = "❚❚ 일시정지"; // 그 버튼으로 재생 중일 때 보이는 글(기본 글과 겹쳐 둔다, editCutPlayBox)
  var EDIT_DBL_AFTER_DRAG_MS = 500; // 끌기 · 끝 다듬기를 막 끝낸 뒤 이 안의 두 번 누르기는 재생하지 않는다
  // 작업 186: 빈틈 다듬기(POST /edit/tighten 제안 → 확인 → 실행 취소 한 단계로 적용). 서버가 그대로 둔 이유(perClip skipped)의 글.
  var EDIT_TIGHTEN_SKIP = {
    muted: "그대로 둠 (소리 끈 클립)",
    noSound: "그대로 둠 (소리 없음)",
    noFilm: "그대로 둠 (파형 준비 중)",
    short: "그대로 둠 (이미 짧음)"
  };
  var EDIT_TIGHTEN_NONE = "그대로 둠 (빈틈 없음)";
  var EDIT_TIGHTEN_STALE = "그사이 편집이 바뀌었습니다. 빈틈 다듬기를 다시 눌러 주세요.";
  // 단축키 [ ]·Shift+[ ] 동작 → [끝(edge), 방향](지시서 115): out 앞(줄이기)·out 뒤(늘이기)·in 앞(늘이기)·in 뒤(줄이기)
  var EDIT_NUDGES = { outEarlier: ["out", -1], outLater: ["out", 1], inEarlier: ["in", -1], inLater: ["in", 1] };
  var EDIT_VARIANT_LABEL = { real: "실사", webtoon: "애니(웹툰)" }; // 작업 123: 서버 label("웹툰")보다 이 이름을 먼저 쓴다
  // 작업 190: 그림체 탭 title. 편집 문서는 그림체마다 따로 저장된다(서버 작업 189, edit-<variant>.json).
  var EDIT_VARIANT_TIP = { real: "실사 편집본(따로 저장됩니다)", webtoon: "애니(웹툰) 편집본(따로 저장됩니다)" };
  var EDIT_VARIANT_SAVED = "편집됨"; // 작업 190: 저장된 편집본이 있는 탭의 점(savedVariants) 설명
  // 작업 192: 여러 컷 고르기(Ctrl/Cmd+누르기 = 넣고 빼기, Shift+누르기 = 기준 클립부터 그 클립까지). ctx.sel = 첫 컷, ctx.multi = 고른 번호 목록.
  var EDIT_MULTI_NOTE = "여러 컷이 선택됨 — AI로 고치기·삭제만 할 수 있습니다";
  var EDIT_FIX_MIXED = "같은 테이크(원본 영상) 안의 컷만 함께 고칠 수 있습니다.";
  // 작업 194: 같은 칸(원본 + 그 버전)의 여러 클립 = segments [{sourceKey, in, out}](타임라인 순서). 원본이 섞이거나 원본 초가 이어지지 않을 때.
  var EDIT_FIX_SEG_MAX = 8;
  var EDIT_FIX_SEG_EPS = 0.01; // 같은 원본 클립의 원본 초가 이어지는가(앞 out = 다음 in)
  var EDIT_FIX_SEG_MANY = "한 번에 8개 컷까지 함께 고칠 수 있습니다.";
  var EDIT_FIX_SEG_SPEED = "속도를 바꾼 컷이 있어 함께 고칠 수 없습니다. 속도를 1배로 되돌린 뒤 다시 골라 주세요.";
  var EDIT_FIX_SEG_DISSOLVE = "고른 컷 사이에 디졸브가 있어 함께 고칠 수 없습니다. 디졸브를 컷으로 바꾼 뒤 다시 골라 주세요.";
  var EDIT_SVG_NS = "http://www.w3.org/2000/svg";
  // 눈금 [큰 눈금 간격(초), 작은 눈금 간격(초)]. 큰 눈금 사이가 64px 이상인 첫 간격을 쓴다(editTickStep).
  var EDIT_TICKS = [[1, 0.25], [2, 0.5], [5, 1], [10, 2]];
  var editCtx = null; // 지금 열린 ⑦ 화면(editNewCtx). 떠나면 null

  /* ---- 순수 함수(DOM 없음) ---- */

  function editRound3(value) {
    return Math.round(Number(value) * 1000) / 1000;
  }

  /* 클립 속도(지시서 115): 허용 목록(EDIT_SPEEDS) 안이면 그 값, 아니면(없음 포함) 1. */
  function editSpeedOf(clip) {
    var speed = Number((clip || {}).speed);
    return EDIT_SPEEDS.indexOf(speed) >= 0 ? speed : 1;
  }

  /* 원본 구간 길이(초) = out − in. */
  function editSourceLen(clip) {
    return Math.max(0, Number(clip.out) - Number(clip.in));
  }

  /* 타임라인에서 차지하는 길이(초) = 원본 구간 ÷ 속도(지시서 115). 너비·눈금·재생헤드·자르기가 모두 이 값을 쓴다. */
  function editClipLen(clip) {
    return editSourceLen(clip) / editSpeedOf(clip);
  }

  /* 원본 구간의 최소 길이: 타임라인 길이와 원본 구간이 둘 다 minLen 이상(빠른 클립은 원본이 더 길어야 한다). 한 프레임은 editTrimClip 이 본다. */
  function editMinSource(minLen, speed) {
    var min = Number(minLen) || EDIT_MIN_CLIP_S;
    return Math.max(min, min * (Number(speed) || 1));
  }

  function editCloneClip(clip) {
    var copy = {
      id: String(clip.id),
      sourceKey: String(clip.sourceKey),
      in: Number(clip.in),
      out: Number(clip.out),
      muted: clip.muted === true
    };
    if (typeof clip.label === "string" && clip.label) copy.label = clip.label;
    var speed = editSpeedOf(clip);
    if (speed !== 1) copy.speed = speed; // 1 은 보내지 않는다(서버도 저장하지 않는다)
    // 작업 182: J/L 컷·디졸브는 기본값(0·컷)이 아닐 때만 둔다 — 안 쓴 문서의 저장 바디는 182 전과 같다.
    var lead = editShiftOf(clip, "audioLead");
    if (lead) copy.audioLead = lead;
    var tail = editShiftOf(clip, "audioTail");
    if (tail) copy.audioTail = tail;
    var dissolve = editDissolveOf(clip);
    if (dissolve) copy.transition = { type: "dissolve", seconds: dissolve };
    // 작업 186: 대사 타이밍도 0 이 아닐 때만(소리 끈 클립도 받은 그대로 — 서버 _clean_slip 과 같다)
    var slip = editSlipOf(clip);
    if (slip) copy.audioSlip = slip;
    editTxCloneFx(clip, copy); // 작업 215b: 블랙/화이트 전환 · 색 보정
    edit215cCloneExtras(clip, copy); // 작업 215c: 빈틈·사용 안 함·역재생·게인·라벨 색(기본값이 아닐 때만)
    return copy;
  }

  /* 작업 182: 0.05초 격자·0~2초로 맞춘 앞당김·이어 감 값(숫자가 아니면 0). */
  function editShiftSnap(value) {
    var v = Number(value);
    if (!isFinite(v) || !(v > 0)) return 0;
    v = Math.round(Math.min(v, EDIT_SHIFT_MAX_S) / EDIT_SHIFT_STEP_S) * EDIT_SHIFT_STEP_S;
    return Math.round(v * 100) / 100;
  }

  /* 작업 182: 클립의 소리 앞당김(audioLead, J컷)·이어 감(audioTail, L컷) 초. 없거나 0 이하면 0. */
  function editShiftOf(clip, key) {
    return editShiftSnap((clip || {})[key]);
  }

  /* 작업 182: 다음 클립으로 가는 디졸브 초(transition {type: "dissolve", seconds}). 컷·없음·허용 값 밖이면 0. */
  function editDissolveOf(clip) {
    var tr = (clip || {}).transition;
    if (!tr || typeof tr !== "object" || tr.type !== "dissolve") return 0;
    var seconds = Number(tr.seconds);
    return EDIT_DISSOLVES.indexOf(seconds) >= 0 ? seconds : 0;
  }

  /* 작업 182: a → b 경계에 seconds 초 디졸브를 둘 수 있는가 = 두 클립 중 짧은 쪽 타임라인 길이의 45% 이하(서버 _check_overlaps). */
  function editDissolveFits(seconds, a, b) {
    if (!a || !b) return false;
    return Number(seconds) <= EDIT_DISSOLVE_SHARE * Math.min(editClipLen(a), editClipLen(b)) + 1e-9;
  }

  /* 작업 182: 경계 a → b 에 미리보기가 따로 다뤄야 할 효과가 있는가(디졸브·a 의 이어 감·b 의 앞당김, 소리 끈 클립의 소리 효과는 없음). */
  function editFxEdge(a, b) {
    if (!a || !b) return false;
    return !!(editDissolveOf(a) || (a.muted !== true && editShiftOf(a, "audioTail")) || (b.muted !== true && editShiftOf(b, "audioLead")));
  }

  /* 작업 182: 클립 하나라도 J/L 컷·디졸브를 쓰는가(미리보기 근사 안내를 보일지). 마지막 클립의 전환·첫 클립의 앞당김은 세지 않는다.
     작업 186: 소리 켠 클립의 대사 타이밍(audioSlip)도 센다. */
  function editHasFx(clips) {
    var list = clips || [];
    for (var i = 0; i + 1 < list.length; i++) {
      if (editFxEdge(list[i], list[i + 1])) return true;
    }
    return list.some(function (clip) {
      return !!clip && clip.muted !== true && !!editSlipOf(clip);
    });
  }

  /* 작업 182: 자리 규칙 정리(서버 _check_overlaps 와 같은 규칙). 옮기기·삭제·자르기·늘이기/줄이기·속도·재료 넣기 뒤 editCommit 이 부른다.
     첫 클립은 앞당김을, 마지막 클립은 이어 감·전환을 버리고, 이제 안 맞는 디졸브는 맞는 가장 긴 값으로 줄이거나(없으면) 컷으로 둔다.
     바꿀 것이 없으면 받은 목록 그대로, 있으면 바뀐 클립만 사본으로 바꾼 새 목록. */
  function editTidyFx(clips) {
    var list = Array.isArray(clips) ? clips : [];
    var out = list.slice();
    var changed = false;
    function own(i) {
      if (out[i] === list[i]) out[i] = editCloneClip(list[i]);
      changed = true;
      return out[i];
    }
    var last = list.length - 1;
    for (var i = 0; i <= last; i++) {
      var clip = list[i];
      if (!clip || typeof clip !== "object") continue;
      if (i === 0 && clip.audioLead !== undefined) delete own(i).audioLead;
      if (i === last) {
        if (clip.audioTail !== undefined) delete own(i).audioTail;
        if (clip.transition !== undefined) delete own(i).transition;
        continue;
      }
      var seconds = editDissolveOf(clip);
      if (!seconds || editDissolveFits(seconds, clip, list[i + 1])) continue;
      var fit = EDIT_DISSOLVES.filter(function (s) {
        return s < seconds && editDissolveFits(s, clip, list[i + 1]);
      });
      if (fit.length) own(i).transition = { type: "dissolve", seconds: fit[fit.length - 1] };
      else delete own(i).transition;
    }
    return changed ? out : list;
  }

  /* 작업 186: 빈틈 다듬기 목록의 초(0.1초 미만은 두 자리, 그 밖은 한 자리). */
  function editTightenSec(value) {
    var v = Math.max(0, Number(value) || 0);
    return v > 0 && v < 0.1 ? v.toFixed(2) : v.toFixed(1);
  }

  /* 작업 186: 줄인 앞·뒤 한 줄("앞 −0.8초 · 뒤 −1.2초"). 둘 다 0 이면 "". */
  function editTightenCutText(head, tail) {
    var parts = [];
    if (Number(head) > 0.0005) parts.push("앞 −" + editTightenSec(head) + "초");
    if (Number(tail) > 0.0005) parts.push("뒤 −" + editTightenSec(tail) + "초");
    return parts.join(" · ");
  }

  /* 작업 186: 줄이거나 뺀 디졸브 한 줄(서버 perClip transition {from, to}, to 0 = 컷). */
  function editTightenTrText(where, from, to) {
    var a = Number(from) || 0;
    var b = Number(to) || 0;
    return where + " 디졸브 " + a + "초 → " + (b ? "디졸브 " + b + "초" : "컷");
  }

  /* 작업 186: 빈틈 다듬기 응답(POST /edit/tighten {clips, perClip})을 지금 클립에 맞춘 적용 계획. 순수 함수.
     클립은 id 로 맞춰 in·out 만 받는다(줄이기만 — 늘어나거나 숫자가 아닌 값은 버린다). 이름·소리 끔·속도·J/L·대사 타이밍은 지금 그대로.
     onlyId 가 없으면(전체) 서버가 줄이거나 뺀 디졸브(perClip transition)도 받고, 있으면(이 클립 빈틈 다듬기) 그 클립 in·out 만 받는다.
     마지막에 editTidyFx 로(디졸브 45% 규칙) 정리한다 — 적용(editCommit)도 같은 정리를 한 번 더 지난다.
     반환 {clips, rows:[{id, index, head, tail, skipped, notes}], count(줄인 클립 수), saved(줄인 타임라인 초)}. */
  function editTightenPlan(clips, res, onlyId) {
    var list = Array.isArray(clips) ? clips : [];
    var r = res && typeof res === "object" ? res : {};
    var only = onlyId === undefined || onlyId === null ? "" : String(onlyId);
    var byId = {};
    var info = {};
    (Array.isArray(r.clips) ? r.clips : []).forEach(function (c) {
      if (c && typeof c === "object" && c.id !== undefined) byId[String(c.id)] = c;
    });
    (Array.isArray(r.perClip) ? r.perClip : []).forEach(function (p) {
      if (p && typeof p === "object" && p.id !== undefined) info[String(p.id)] = p;
    });
    var merged = list.map(function (clip) {
      var id = String(clip.id);
      var got = byId[id];
      if (!got || (only && id !== only)) return clip;
      var copy = editCloneClip(clip);
      var a = Number(got.in);
      var b = Number(got.out);
      if (isFinite(a) && isFinite(b) && a >= copy.in - 1e-9 && b <= copy.out + 1e-9 && b > a) {
        copy.in = editRound3(a);
        copy.out = editRound3(b);
      }
      if (!only && (info[id] || {}).transition) {
        var d = editDissolveOf(got);
        if (d) copy.transition = { type: "dissolve", seconds: d };
        else delete copy.transition;
      }
      return copy;
    });
    var tidy = editTidyFx(merged);
    function trNote(i, where) {
      var a = editDissolveOf(list[i]);
      var b = editDissolveOf(tidy[i]);
      return list[i] && a !== b ? editTightenTrText(where, a, b) : "";
    }
    var rows = [];
    var saved = 0;
    var count = 0;
    list.forEach(function (clip, index) {
      var id = String(clip.id);
      if (only && id !== only) return;
      var p = info[id] || {};
      var speed = editSpeedOf(clip);
      var head = Math.max(0, editRound3((Number(tidy[index].in) - Number(clip.in)) / speed));
      var tail = Math.max(0, editRound3((Number(clip.out) - Number(tidy[index].out)) / speed));
      var notes = [];
      var tr = p.transition && typeof p.transition === "object" && !only ? p.transition : null;
      var next = tr ? editTightenTrText("다음 전환", tr.from, tr.to) : trNote(index, "다음 전환");
      if (only && index > 0) {
        var before = trNote(index - 1, "앞 전환");
        if (before) notes.push(before);
      }
      if (next) notes.push(next);
      var skipped = typeof p.skipped === "string" && EDIT_TIGHTEN_SKIP[p.skipped] ? p.skipped : "";
      if (head + tail > 0.0005) {
        count += 1;
        saved += head + tail;
        skipped = "";
      }
      rows.push({ id: id, index: index, head: head, tail: tail, skipped: skipped, notes: notes });
    });
    return { clips: tidy, rows: rows, count: count, saved: editRound3(saved) };
  }

  function editCloneClips(clips) {
    return (Array.isArray(clips) ? clips : [])
      .filter(function (clip) {
        return clip && typeof clip === "object";
      })
      .map(editCloneClip);
  }

  /* 클립마다 타임라인 시작 시각(초)과 전체 길이. 클립은 붙어 있다(간격 없음, design D3).
     작업 182: 디졸브가 있으면 다음 클립이 그 초만큼 겹쳐 당겨지고 전체 길이도 그만큼 짧다(서버 _total). */
  function editStarts(clips) {
    var starts = [];
    var total = 0;
    var list = clips || [];
    list.forEach(function (clip, index) {
      total += edit215cGapOf(clip); // 작업 215c: 앞 빈틈(gapBefore)
      starts.push(total);
      total += editClipLen(clip);
      if (index < list.length - 1) total -= editDissolveOf(clip);
    });
    return { starts: starts, total: total };
  }

  /* 타임라인 시각 t → {index, clip, start, local(클립 안 타임라인 초), source(원본 초 = in + local × 속도)}. 경계는 뒤 클립(시작 포함·끝 제외),
     전체 길이 이상은 마지막 클립의 끝, 0 미만은 0. 클립이 없으면 null. 작업 182: 디졸브로 겹친 구간은 뒤 클립(미리보기가 바꿔 끼우는 시각). */
  function editLocate(clips, t) {
    var list = clips || [];
    if (!list.length) return null;
    var info = editStarts(list);
    var time = Math.max(0, Number(t) || 0);
    for (var i = 0; i < list.length; i++) {
      var len = editClipLen(list[i]);
      var end = i + 1 < list.length ? info.starts[i + 1] : info.starts[i] + len;
      if (time < end - 1e-9 || i === list.length - 1) {
        var local = Math.min(Math.max(0, time - info.starts[i]), len);
        return { index: i, clip: list[i], start: info.starts[i], local: local, source: editRound3(Number(list[i].in) + local * editSpeedOf(list[i])) };
      }
    }
    return null;
  }

  /* 프레임 격자(fps, 모르면 24)에 맞춘 초(밀리초 반올림). */
  function editSnap(value, fps) {
    var f = Number(fps) > 0 ? Number(fps) : EDIT_DEFAULT_FPS;
    return editRound3(Math.round(Number(value) * f) / f);
  }

  /* 재생헤드 t 에서 클립을 둘로 나눈다(같은 원본. 왼쪽은 id·이름·소리 그대로, 오른쪽은 newId·같은 이름·같은 소리).
     나눔점은 원본 fps 프레임에 맞추고, 두 쪽 모두 minLen(0.04초와 한 프레임 중 큰 값) 이상이어야 한다.
     반환 {clips, index(오른쪽 조각 번호)}, 못 나누면 null. */
  function editSplit(clips, t, newId, fps, minLen) {
    var loc = editLocate(clips, t);
    if (!loc) return null;
    var f = Number(fps) > 0 ? Number(fps) : EDIT_DEFAULT_FPS;
    var min = Math.max(Number(minLen) || EDIT_MIN_CLIP_S, 1 / f) - 1e-6;
    var cut = editSnap(loc.source, f);
    var clip = loc.clip;
    if (cut - Number(clip.in) < min || Number(clip.out) - cut < min) return null;
    var left = editCloneClip(clip);
    var right = editCloneClip(clip);
    left.out = cut;
    right.in = cut;
    right.id = String(newId);
    delete right.gapBefore; // 작업 215c: 빈틈은 앞 조각에만
    // 작업 182: 앞당김은 앞 조각(왼쪽 끝)에, 이어 감·다음 클립으로 가는 전환은 뒤 조각(오른쪽 끝)에 남는다. 새 경계는 컷.
    delete left.audioTail;
    delete left.transition;
    delete right.audioLead;
    var next = clips.slice();
    next.splice(loc.index, 1, left, right);
    return { clips: next, index: loc.index + 1 };
  }

  /* 클립 끝 끌기: edge("in"|"out")를 value(원본 초)로 옮긴 사본. 원본 0~duration 안, 길이 minLen(0.04초와 한 프레임 중
     큰 값) 이상, fps 프레임 격자에 맞춘다. out 이 원본 끝 반 프레임 안이면 원본 끝 그대로(원본 길이는 격자 밖일 수 있다).
     duration 을 모르면(0) 위 한도 없이. 더 줄일 수 없으면 원래 값 그대로의 사본. */
  function editTrimClip(clip, edge, value, duration, fps, minLen) {
    var f = Number(fps) > 0 ? Number(fps) : EDIT_DEFAULT_FPS;
    var min = Math.max(Number(minLen) || EDIT_MIN_CLIP_S, 1 / f);
    var dur = Number(duration) > 0 ? Number(duration) : 0;
    var start = Number(clip.in);
    var end = Number(clip.out);
    var next = editCloneClip(clip);
    var v = Math.round(Number(value) * f) / f;
    if (!isFinite(v)) return next;
    if (edge === "in") {
      var hi = end - min;
      if (v > hi) v = Math.floor(hi * f + 1e-6) / f;
      if (v < 0) v = 0;
      if (end - v < min - 1e-6) return next;
      next.in = editRound3(v);
    } else {
      if (dur && v >= dur - 0.5 / f) v = dur;
      var lo = start + min;
      if (v < lo) v = Math.ceil(lo * f - 1e-6) / f;
      if (dur && v > dur) v = dur;
      if (v - start < min - 1e-6) return next;
      next.out = editRound3(v);
    }
    return next;
  }

  /* 속도 바꾸기(지시서 115): speed 는 EDIT_SPEEDS 중 하나. in·out 은 그대로라 타임라인 길이 = 원본 구간 ÷ speed.
     타임라인 길이가 minLen 보다 짧아지거나 speed 가 목록 밖이면 null. */
  function editSetSpeed(clip, speed, minLen) {
    var s = Number(speed);
    if (EDIT_SPEEDS.indexOf(s) < 0) return null;
    var next = editCloneClip(clip);
    if (s === 1) delete next.speed;
    else next.speed = s;
    if (editClipLen(next) < (Number(minLen) || EDIT_MIN_CLIP_S) - 1e-6) return null;
    return next;
  }

  /* 길이 직접 입력(지시서 115, design D23): 타임라인 길이를 seconds 초로 맞춘 {clip, note}. 숫자가 아니거나 0 이하면 null.
     - 지금 속도로 원본 안(in + 길이 × speed ≤ 원본 길이)이면 out 만 바꾼다(원본 fps 프레임에 맞춤).
     - 원본보다 길면 지금보다 낮은 허용 속도 중 맞는 가장 큰 값으로 낮춘다.
     - 0.5× 로도 모자라면 0.5× · 원본 끝까지(가장 길게)로 맞추고 안내한다.
     minLen(0.04초)보다 짧으면 minLen 으로. 원본 길이를 모르면(0) 지금 out 을 원본 끝으로 본다. */
  function editRetime(clip, seconds, duration, fps, minLen) {
    var f = Number(fps) > 0 ? Number(fps) : EDIT_DEFAULT_FPS;
    var want = Number(seconds);
    if (!isFinite(want) || !(want > 0)) return null;
    var min = Number(minLen) || EDIT_MIN_CLIP_S;
    var start = Number(clip.in);
    var end = Number(clip.out);
    var dur = Number(duration) > 0 ? Number(duration) : end;
    var avail = Math.max(0, dur - start);
    var note = "";
    if (want < min) {
      want = min;
      note = "가장 짧은 길이(" + min + "초)로 맞췄습니다.";
    }
    var speed = editSpeedOf(clip);
    var tol = 0.5 / f;
    if (want * speed > avail + tol) {
      var lower = EDIT_SPEEDS.filter(function (s) {
        return s < speed && want * s <= avail + tol;
      });
      if (lower.length) {
        speed = lower[lower.length - 1];
        note = "원본이 모자라 속도를 " + speed + "×로 낮췄습니다.";
      } else {
        speed = EDIT_SPEEDS[0];
        want = avail / speed;
        note = "원본 끝까지 " + speed + "×로 늘려도 " + want.toFixed(2) + "초까지입니다. " + want.toFixed(2) + "초로 맞췄습니다.";
      }
    }
    var out = Math.round((start + want * speed) * f) / f;
    if (out > dur || dur - out < tol) out = dur;
    var lo = start + Math.max(editMinSource(min, speed), 1 / f);
    if (out < lo - 1e-6) out = Math.min(dur, Math.ceil(lo * f - 1e-6) / f);
    // 원본 범위를 벗어난 클립(시작이 원본 끝 가까이·뒤)은 맞출 수 없다 — 바꾸지 않는다
    if ((out - start) / speed < min - 1e-6 || out - start < 1 / f - 1e-6) return null;
    var next = editCloneClip(clip);
    next.out = editRound3(out);
    if (speed === 1) delete next.speed;
    else next.speed = speed;
    return { clip: next, note: note };
  }

  /* 단축키 [ ]·Shift+[ ](지시서 115): edge("in"|"out")를 원본 fps 한 프레임 dir(−1 앞·+1 뒤)만큼. 원본 0~duration 안,
     최소 길이 안에서(editTrimClip 규칙). 원본 길이를 모르면 끝은 지금보다 늘리지 않는다. 못 움직이면 같은 값의 사본. */
  function editNudge(clip, edge, dir, duration, fps, minLen) {
    var f = Number(fps) > 0 ? Number(fps) : EDIT_DEFAULT_FPS;
    var base = edge === "in" ? Number(clip.in) : Number(clip.out);
    var dur = Number(duration) > 0 ? Number(duration) : edge === "out" ? Number(clip.out) : 0;
    return editTrimClip(clip, edge, base + (dir < 0 ? -1 : 1) / f, dur, f, editMinSource(minLen, editSpeedOf(clip)));
  }

  function editRemove(clips, index) {
    var next = (clips || []).slice();
    if (index >= 0 && index < next.length) next.splice(index, 1);
    return next;
  }

  /* 작업 192: 고른 번호(indexes) 클립을 모두 뺀 목록(순수). 범위 밖 번호는 버린다. */
  function editRemoveMany(clips, indexes) {
    var drop = {};
    (indexes || []).forEach(function (i) {
      drop[i] = true;
    });
    return (clips || []).filter(function (clip, i) {
      return !drop[i];
    });
  }

  /* from 번째 클립을 to 자리(0~길이, 그 번호 앞)로 옮긴 순서. */
  function editMove(clips, from, to) {
    var next = (clips || []).slice();
    if (from < 0 || from >= next.length) return next;
    var target = Math.max(0, Math.min(next.length, Number(to) || 0));
    var item = next.splice(from, 1)[0];
    if (target > from) target -= 1;
    next.splice(target, 0, item);
    return next;
  }

  /* 끌어 놓을 자리: 타임라인 x(px)에서 가장 가까운 클립 경계 번호(0~길이). */
  function editDropIndex(clips, x, pps) {
    var info = editStarts(clips);
    var best = 0;
    var bestGap = Infinity;
    for (var k = 0; k <= info.starts.length; k++) {
      var at = (k < info.starts.length ? info.starts[k] : info.total) * pps;
      var gap = Math.abs(at - x);
      if (gap < bestGap) {
        best = k;
        bestGap = gap;
      }
    }
    return best;
  }

  /* 실행 취소 스택(design §5.5): {undo, redo}. 하나 쌓으면 다시 실행 스택을 비운다. 100단계를 넘으면 오래된 것부터 버린다. */
  function editSnapshotOf(variant, clips) {
    if (editCtx && editCtx.tx) return editTxSnapOf(editCtx, { variant: variant, clips: editCloneClips(clips) }); // 작업 215b: 자막 · 타이틀 · 페이드도
    return { variant: variant, clips: editCloneClips(clips) };
  }

  function editHistPush(hist, snapshot) {
    hist.undo.push(snapshot);
    while (hist.undo.length > EDIT_UNDO_MAX) hist.undo.shift();
    hist.redo.length = 0;
  }

  function editUndo(hist, current) {
    if (!hist.undo.length) return null;
    hist.redo.push(current);
    return hist.undo.pop();
  }

  function editRedo(hist, current) {
    if (!hist.redo.length) return null;
    hist.undo.push(current);
    while (hist.undo.length > EDIT_UNDO_MAX) hist.undo.shift();
    return hist.redo.pop();
  }

  function editClampPps(pps) {
    var p = Number(pps);
    if (!(p > 0)) p = EDIT_MIN_PPS;
    return Math.max(EDIT_MIN_PPS, Math.min(EDIT_MAX_PPS, p));
  }

  /* 기본 확대: 전체가 타임라인 너비에 들어가게(20~400px/초). 너비를 모르면 100. */
  function editFitPps(total, width) {
    var w = (Number(width) || 0) - 24;
    if (!(total > 0) || !(w > 0)) return 100;
    return editClampPps(Math.floor(w / total));
  }

  /* 확대 슬라이더(0~1000) ↔ px/초. 로그 눈금이라 낮은 확대에서도 고르게 움직인다. */
  function editSliderOf(pps) {
    return Math.round((1000 * Math.log(editClampPps(pps) / EDIT_MIN_PPS)) / Math.log(EDIT_MAX_PPS / EDIT_MIN_PPS));
  }

  function editPpsOf(slider) {
    var v = Math.max(0, Math.min(1000, Number(slider) || 0));
    return Math.round(EDIT_MIN_PPS * Math.pow(EDIT_MAX_PPS / EDIT_MIN_PPS, v / 1000) * 10) / 10;
  }

  /* 눈금 간격: 확대에 따라 큰 눈금 1·2·5·10초(사이 64px 이상인 첫 값), 작은 눈금은 그 사이(8px 미만이면 0 = 안 그림). */
  function editTickStep(pps) {
    var p = Number(pps) || EDIT_MIN_PPS;
    var pick = EDIT_TICKS[EDIT_TICKS.length - 1];
    for (var i = 0; i < EDIT_TICKS.length; i++) {
      if (EDIT_TICKS[i][0] * p >= 64) {
        pick = EDIT_TICKS[i];
        break;
      }
    }
    return { major: pick[0], minor: pick[1] * p >= 8 ? pick[1] : 0 };
  }

  function editPad2(n) {
    return (n < 10 ? "0" : "") + n;
  }

  /* 초 → "mm:ss.cc"(재생 시각). withCs 가 false 면 "mm:ss"(눈금 글). */
  function editTimeText(seconds, withCs) {
    var all = Math.floor(Math.max(0, Number(seconds) || 0) * 100 + 1e-6);
    var text = editPad2(Math.floor(all / 6000)) + ":" + editPad2(Math.floor((all % 6000) / 100));
    return withCs === false ? text : text + "." + editPad2(all % 100);
  }

  /* 필름스트립 칸 나누기(design D5). strip {tiles, interval, tileWidth, tileHeight}(media 응답)으로 클립 [in, out] 을 높이
     cellH 칸으로 채운다. 칸마다 그 칸 가운데 시각의 타일(원본 초 ÷ interval)을 background-position 으로 보인다.
     반환 {bgWidth, bgHeight, cells:[{left, width, x}]}, 꼴이 틀리면 null. 칸 수는 클립 너비 ÷ 칸 너비라, 타임라인 전체
     칸 수는 클립 수가 아니라 전체 너비로 정해진다(300초 × 400px/초 ≈ 1,400칸). */
  function editStripCells(clip, strip, pps, cellH) {
    var s = strip || {};
    var tiles = Math.floor(Number(s.tiles) || 0);
    var interval = Number(s.interval) || 0;
    var tw = Number(s.tileWidth) || 0;
    var th = Number(s.tileHeight) || 0;
    var h = Number(cellH) > 0 ? Number(cellH) : EDIT_THUMB_H;
    if (tiles < 1 || interval <= 0 || tw <= 0 || th <= 0 || !(Number(pps) > 0)) return null;
    var cw = Math.max(8, Math.round(((tw * h) / th) * 10) / 10);
    var width = editClipLen(clip) * pps;
    var speed = editSpeedOf(clip);
    var cells = [];
    for (var x = 0; x < width - 0.5 && cells.length < 2000; x += cw) {
      var w = Math.min(cw, width - x);
      var at = Number(clip.in) + ((x + w / 2) / pps) * speed; // 칸 가운데의 원본 시각(속도 반영, 지시서 115)
      var tile = Math.max(0, Math.min(tiles - 1, Math.floor(at / interval + 1e-9)));
      cells.push({ left: Math.round(x * 10) / 10, width: Math.round(w * 10) / 10, x: -Math.round(tile * cw * 10) / 10 });
    }
    return { bgWidth: Math.round(tiles * cw * 10) / 10, bgHeight: h, cells: cells };
  }

  /* 파형 SVG 길(path d, design D6). peaks {perSecond, values(0~1)} 의 [in, out] 부분을 가운데 기준 막대로, viewBox 0 0 n 100.
     반환 {n, d}, 값이 없으면 null. 화면은 preserveAspectRatio none 으로 클립 너비에 늘린다(확대가 바뀌어도 길은 그대로).
     작업 186: shift(원본 초 = 대사 타이밍 × 속도)가 있으면 들리는 창 [in − shift, out − shift] 을 그린다. 창 길이(n)는 그대로이고
     원본 밖(0 앞·끝 뒤)은 막대 없음(무음). shift 0 이면 186 전과 같다. */
  function editPeakPath(clip, peaks, shift) {
    var p = peaks || {};
    var values = Array.isArray(p.values) ? p.values : [];
    var per = Number(p.perSecond) || 0;
    if (!values.length || per <= 0) return null;
    var move = Number(shift) || 0;
    var i0 = Math.floor((Number(clip.in) - move) * per + 1e-9);
    var i1 = Math.ceil((Number(clip.out) - move) * per - 1e-9);
    if (!move) {
      i0 = Math.max(0, i0);
      i1 = Math.min(values.length, i1);
    }
    var lo = Math.max(0, i0);
    var hi = Math.min(values.length, i1);
    if (i1 <= i0 || hi <= lo) return null;
    var parts = [];
    for (var i = lo; i < hi; i++) {
      var v = Math.max(0, Math.min(1, Number(values[i]) || 0));
      var bar = Math.max(2, Math.round(v * 96));
      parts.push("M" + (i - i0) + " " + (100 - bar) / 2 + "h0.8v" + bar + "h-0.8z");
    }
    return { n: i1 - i0, d: parts.join("") };
  }

  /* 작업 186: −1~1초·0.05초 격자로 맞춘 대사 타이밍 값(숫자가 아니거나 0 이면 0, −0 도 0). */
  function editSlipSnap(value) {
    var v = Number(value);
    if (!isFinite(v) || v === 0) return 0;
    v = Math.max(-EDIT_SLIP_MAX_S, Math.min(EDIT_SLIP_MAX_S, v));
    v = Math.round(Math.round(v / EDIT_SHIFT_STEP_S) * EDIT_SHIFT_STEP_S * 100) / 100;
    return v || 0;
  }

  /* 작업 186: 클립의 대사 타이밍(audioSlip, 타임라인 초, 양수 = 소리가 늦게). 없으면 0. */
  function editSlipOf(clip) {
    return editSlipSnap((clip || {}).audioSlip);
  }

  /* 작업 186: 대사 타이밍 숫자 표시("+0.30초 늦게" · "−0.20초 빨리" · "0 (원래대로)"). */
  function editSlipText(value) {
    var v = editSlipSnap(value);
    if (v > 0) return "+" + v.toFixed(2) + "초 늦게";
    if (v < 0) return "−" + (-v).toFixed(2) + "초 빨리";
    return "0 (원래대로)";
  }

  /* 작업 186: 재료 소리 구간(media soundRanges, 원본 초) → 클립 소리 상자 안 띠 [{from, to}](클립 시작부터 타임라인 초).
     화면 t 에서 들리는 원본 = in + t × 속도 − 대사 타이밍 × 속도(서버 내보내기와 같다) → 띠 = (원본 − in) ÷ 속도 + 대사 타이밍.
     클립 밖은 자른다. 꼴이 틀린 구간은 버린다. */
  function editSoundBands(clip, ranges) {
    if (!Array.isArray(ranges) || !ranges.length || !clip) return [];
    var speed = editSpeedOf(clip);
    var slip = editSlipOf(clip);
    var len = editClipLen(clip);
    var start = Number(clip.in) || 0;
    var out = [];
    ranges.forEach(function (range) {
      if (!Array.isArray(range) || range.length < 2) return;
      var a = (Number(range[0]) - start) / speed + slip;
      var b = (Number(range[1]) - start) / speed + slip;
      if (!isFinite(a) || !isFinite(b)) return;
      a = Math.max(0, a);
      b = Math.min(len, b);
      if (b - a > 1e-6) out.push({ from: editRound3(a), to: editRound3(b) });
    });
    return out;
  }

  /* 소리 줄 한 칸(지시서 116 보완 10). 반환 {kind, title, path, from}. 재료에 audioFrom(소리 없는 다시 만든 버전 → 그 칸 v1 소리,
     서버 _audio_sources)이 있으면 is-silent 대신 v1 파형의 [offset + in × scale, offset + out × scale](end 까지)을 그리고,
     v1 파형이 아직 없으면 소리 있음 표시만 한다. 소리 끈 클립은 지금처럼 is-muted.
     작업 186: 대사 타이밍만큼 옮긴 파형, 그리고 bands(클립 자기 재료의 소리 구간 띠, editSoundBands — 빈틈 다듬기가 보는 구간). */
  function editAudioLane(clip, mat, media, fromMedia) {
    var m = media || {};
    var info = mat || {};
    var from = info.audioFrom && typeof info.audioFrom === "object" ? info.audioFrom : null;
    if (clip.muted === true) return { kind: "is-muted", title: "소리 끔", path: null, from: false, bands: [] };
    var slip = editSlipOf(clip);
    var shift = slip ? slip * editSpeedOf(clip) : 0; // 원본 초
    if (from) {
      var f = fromMedia || {};
      var off = Number(from.offset) || 0;
      var sc = Number(from.scale) > 0 ? Number(from.scale) : 1;
      var a = off + Number(clip.in) * sc;
      var b = off + Number(clip.out) * sc;
      if (Number(from.end) > 0) b = Math.min(b, Number(from.end));
      var fpeaks = f.status === "done" && f.data ? f.data.peaks : null;
      return { kind: "", title: "원본 소리 사용", path: b > a ? editPeakPath({ in: a, out: b }, fpeaks, shift * sc) : null, from: true, bands: [] };
    }
    var peaks = m.status === "done" && m.data ? m.data.peaks : null;
    var kind = info.hasAudio === false ? "is-silent" : "";
    var path = kind ? null : editPeakPath(clip, peaks, shift);
    if (!kind && !path) kind = m.status === "done" ? "is-silent" : "is-loading";
    var bands = !kind && m.data ? editSoundBands(clip, m.data.soundRanges) : [];
    return { kind: kind, title: info.hasAudio === false || kind === "is-silent" ? "소리 없음" : "소리", path: path, from: false, bands: bands };
  }

  /* 단축키(design §5.4) → 동작 이름(없으면 ""). 입력칸 초점·Alt 는 부르는 쪽이 거른다.
     글자 키는 code 도 본다(한글 자판에서 S 는 key "ㄴ", M 은 "ㅡ"). Ctrl 과 같은 자리의 Cmd(mod)도 같다. */
  function editKeyAction(key, code, mod, shift) {
    var k = String(key || "");
    var c = String(code || "");
    if (mod) {
      if (c === "KeyZ" || k === "z" || k === "Z") return shift ? "redo" : "undo";
      if (c === "KeyY" || k === "y" || k === "Y") return "redo";
      if (c === "KeyB" || k === "b" || k === "B") return "split";
      return "";
    }
    if (k === " " || c === "Space") return "play";
    if (k === "ArrowLeft") return shift ? "secBack" : "frameBack";
    if (k === "ArrowRight") return shift ? "secFwd" : "frameFwd";
    if (k === "Home") return "home";
    if (k === "End") return "end";
    if (c === "KeyS" || k === "s" || k === "S") return "split";
    if (k === "Delete" || k === "Backspace") return "delete";
    if (c === "KeyM" || k === "m" || k === "M") return "mute";
    // 작업 219b: 붙기는 Shift+N(그냥 N 은 215c 롤링 도구가 먼저 받는다 — edit215cOnKey)
    if (c === "KeyN" || k === "n" || k === "N") return "snap";
    if (k === "+" || k === "=" || c === "NumpadAdd") return "zoomIn";
    if (k === "-" || k === "_" || c === "NumpadSubtract") return "zoomOut";
    // 고른 클립 1프레임 다듬기(지시서 115): [ ] 는 끝, Shift 는 시작. 괄호는 한글 자판에서도 같은 글자다.
    if (c === "BracketLeft" || k === "[" || k === "{") return shift || k === "{" ? "inEarlier" : "outEarlier";
    if (c === "BracketRight" || k === "]" || k === "}") return shift || k === "}" ? "inLater" : "outLater";
    return "";
  }

  /* PUT /edit 바디(design §4.2). 클립은 서버가 받는 키만. */
  function editSaveBody(etag, variant, clips) {
    return { etag: String(etag || ""), variant: variant, clips: editCloneClips(clips), audio: [] };
  }

  /* 보이는 시간 [lo, hi] 와 겹치는 클립의 재료 mid 먼저(타임라인 순서), 그다음 나머지. 같은 mid 는 한 번(design §5.6). */
  function editMediaOrder(clips, midOf, lo, hi) {
    var list = clips || [];
    var info = editStarts(list);
    var out = [];
    var seen = {};
    function add(clip) {
      var mid = midOf(clip.sourceKey);
      if (mid && !seen[mid]) {
        seen[mid] = true;
        out.push(mid);
      }
    }
    list.forEach(function (clip, i) {
      var a = info.starts[i];
      if (a + editClipLen(clip) > lo && a < hi) add(clip);
    });
    list.forEach(add);
    return out;
  }

  /* 내보내기 상태 한 줄(design §5.2 아래 줄). */
  function editExportText(ex) {
    var e = ex || {};
    var status = e.status || "none";
    if (status === "queued") return "대기 중 — 앞의 내보내기가 끝나면 시작합니다.";
    if (status === "running") {
      var p = e.progress || {};
      return "만드는 중 " + (Number(p.done) || 0) + "/" + (Number(p.total) || 0);
    }
    if (status === "done") return "끝 — " + editTimeText(e.seconds) + (e.hasAudio === false ? " · 소리 없음" : "");
    if (status === "failed") return "실패 — " + (e.error || "내보내지 못했습니다.");
    return "아직 내보낸 영상이 없습니다. [내보내기]를 누르면 지금 편집대로 소리 없는 영상(mp4)과 소리(mp3)를 zip 하나로 만듭니다."; // 작업 216-fix1b
  }

  /* CSS url("…") 안에 넣을 주소. 따옴표·역슬래시·줄바꿈을 %로 바꿔 선언 밖으로 나가지 않게 한다. */
  function editCssUrl(url) {
    return String(url || "").replace(/["\\\n\r\f]/g, function (ch) {
      return encodeURIComponent(ch);
    });
  }

  function editNewId(clips) {
    var used = {};
    (clips || []).forEach(function (clip) {
      used[clip.id] = true;
    });
    var id = "";
    do {
      id = "c" + Date.now().toString(36) + Math.floor(Math.random() * 1679616).toString(36);
    } while (used[id]);
    return id;
  }

  /* ---- 화면 상태 ---- */

  function editPath(ctx) {
    return "/api/vnew/p/" + encodeURIComponent(ctx.pid) + "/edit";
  }

  /* 이 ⑦ 화면이 아직 보이는가. 단계를 옮기거나 프로젝트를 떠나면 false(타이머·키·재생이 스스로 멈춘다). */
  function editAlive(ctx) {
    return !!ctx && ctx.alive && editCtx === ctx && state.mounted && state.planStep === 7 && state.projectId === ctx.pid;
  }

  /* 이 프로젝트의 ⑦ 화면이 열려 있는가(단계 값과 상관없이, loadPlan 이 응답 전후로 본다 — 지시서 115). */
  function editOpenFor(pid) {
    return !!editCtx && editCtx.alive && editCtx.pid === pid;
  }

  function editRaf(fn) {
    if (typeof window.requestAnimationFrame === "function") return window.requestAnimationFrame(fn);
    return window.setTimeout(fn, 16);
  }

  function editCancelRaf(id) {
    if (!id) return;
    if (typeof window.cancelAnimationFrame === "function") window.cancelAnimationFrame(id);
    else window.clearTimeout(id);
  }

  function editConfirm(message) {
    try {
      return window.confirm(message);
    } catch (error) {
      return true;
    }
  }

  function editHelpClosed() {
    try {
      return window.localStorage.getItem(EDIT_HELP_KEY) === "1";
    } catch (error) {
      return false;
    }
  }

  function editRememberHelp() {
    try {
      window.localStorage.setItem(EDIT_HELP_KEY, "1");
    } catch (error) {
      /* 저장 못 해도 이번 화면에서는 닫힌다 */
    }
  }

  function editNewCtx() {
    var ctx = {
      pid: state.projectId,
      alive: true,
      root: el("section", "vnew-edit"),
      dom: {},
      variant: "",
      variants: [],
      clips: [],
      etag: "",
      saved: false,
      defaults: {},
      matList: {},
      materials: {},
      binTake: {}, // 작업 145: 그림체별 고른 테이크(sourceKey) — 다시 그리기·폴링에도 남는다
      binCut: {}, // 작업 145: 그림체별 고른 컷(sourceKey). 작업 149: 통 테이크 안 컷이면 "cut:<cutId>"
      binFollow: {}, // 작업 149: 그림체별 재료 메뉴가 마지막으로 따라간 타임라인 클립(editBinFollow)
      limits: {},
      invalidIds: {},
      media: {},
      mediaQueue: [],
      mediaBusy: false,
      hist: { undo: [], redo: [] },
      hists: {}, // 작업 190: 그림체별 실행 취소 스택(hist 는 지금 그림체 것). 이 화면 안에서만 — 다시 열면 비어 있다
      savedVariants: {}, // 작업 190: 그림체별 저장된 편집본이 있는가(GET savedVariants, 저장 성공이면 true)
      rev: 0,
      savedRev: 0,
      saving: false,
      saveAgain: false,
      saveTimer: 0,
      saveFailed: false,
      saveWaiters: [],
      busy: false,
      exporting: false,
      sel: -1,
      multi: [], // 작업 192: 고른 클립 번호(오름차순). 하나면 [sel], 없으면 []. ctx.sel 은 늘 그 첫 번호(editSelIndexes)
      anchor: -1, // 작업 192: Shift+누르기 범위의 기준 클립(마지막으로 그냥 누르거나 Ctrl 로 넣고 뺀 클립)
      t: 0,
      pps: 0,
      playing: false,
      raf: 0,
      zoomRaf: 0,
      zoomPending: 0,
      vids: [],
      audio: null, // 지시서 118b: 원본 소리 전용 숨은 <video>(editBuild)
      cur: 0,
      curIndex: -1,
      clipEls: [],
      exportState: { status: "none" },
      exportHistory: [],
      exportSig: "",
      exportTimer: 0,
      probeTimer: 0,
      urlRetryAt: 0,
      drag: null,
      dragEndAt: 0, // 작업 188: 마지막으로 움직인 끌기 · 끝 다듬기가 끝난 때(Date.now) — 그 직후 두 번 누르기는 무시(editClipDblClick)
      cutPlay: null, // 작업 188: 고른 컷부터 재생 {mode: "from"|"only", index, stopAt}(editPlayCut). 멈춤 · 옮기기 · 편집 · 다른 클립 고르기면 null
      notice: "",
      fix: null, // 지시서 116: 열린 [AI로 고치기] 패널(editFixOpen). 닫으면 null
      trEls: [], // 작업 182: 경계마다 {btn(전환 손잡이), wedge(디졸브 겹침)} — editRenderFx
      trMenu: null, // 작업 182: 열린 전환 메뉴 {el, index, onDoc}(editTrOpen). 닫으면 null
      fxHold: null, // 작업 182 미리보기: 바꿔 끼운 뒤에도 잠깐 더 재생하는 앞 클립(디졸브·L컷, editFxHoldStart)
      fxLead: null, // 작업 182 미리보기: 화면보다 먼저 소리를 내는 다음 클립(J컷, editFxLeadTick)
      tighten: null, // 작업 186: 빈틈 다듬기 {busy, open, rev, only, plan}(editTightenStart). 닫거나 편집이 바뀌면 null
      mix: editMixNorm({}), // 작업 215a: 오디오 트랙 {tracks, a1}(editMixNorm)
      mixSel: null, // 작업 215a: 고른 소리 아이템 {t, i}
      mixKey: null, // 작업 215a: 고른 볼륨 키 {t, i, k}
      mixStems: false, // 작업 215a: 내보내기에 스템 zip 도
      mixLive: null, // 작업 215a: WebAudio 미리듣기 예약(editMixStart)
      mixAc: null,
      sounds: { list: [], byId: {}, peaks: {}, peakFail: {}, buffers: {}, busy: false, peakBusy: false, error: "" }, // 작업 215a: 내 소리
      retakeOn: false // GET /edit 의 retake.enabled(VNEW_EDIT_RETAKE). false 면 AI로 고치기 버튼이 없다
    };
    ctx.onKey = function (event) {
      editOnKey(ctx, event);
    };
    return ctx;
  }

  function editLimit(ctx, key, fallback) {
    var value = Number((ctx.limits || {})[key]);
    return value > 0 ? value : fallback;
  }

  function editMinLen(ctx) {
    return editLimit(ctx, "minClipSeconds", EDIT_MIN_CLIP_S);
  }

  function editFpsOf(ctx, clip) {
    var fps = Number((ctx.materials[(clip || {}).sourceKey] || {}).fps);
    return fps > 0 ? fps : EDIT_DEFAULT_FPS;
  }

  function editUrlOf(ctx, sourceKey) {
    return String((ctx.materials[sourceKey] || {}).videoUrl || "");
  }

  /* ⑦ 단계 그리기(renderPlan). 이전 ⑦ 화면이 있으면 먼저 정리한다(남은 저장은 보낸다). */
  function renderEditStep(host) {
    editLeave();
    var ctx = editNewCtx();
    editCtx = ctx;
    host.appendChild(ctx.root);
    editShowWait(ctx, "편집 문서를 불러오는 중…");
    document.addEventListener("keydown", ctx.onKey);
    editLoad(ctx, "", "");
    mnotesRefresh(ctx.pid); // 작업 199: ⑦ 를 열 때 모델 에러 로그 건수 한 번
  }

  /* ⑦ 을 떠날 때(단계 이동·unmount·다시 그리기): 재생·타이머·키를 멈추고, 남은 저장이 있으면 한 번 보낸다(design §5.5). */
  function editLeave() {
    var ctx = editCtx;
    if (!ctx) return;
    editCtx = null;
    ctx.alive = false;
    mnotesClose(); // 작업 199: 열린 모델 에러 로그 창
    editTrClose(ctx); // 작업 182: 열린 전환 메뉴
    ctx.tighten = null; // 작업 186: 빈틈 다듬기 제안(받는 중이면 응답을 버린다)
    editFixStop(ctx); // 지시서 116: AI로 고치기 패널 조회 타이머·영상
    editTxLeave(ctx); // 작업 215b: 메뉴 · 글 칸 · 대사 창 · 프리미어 조회
    editPause(ctx);
    if (ctx.mixAc && ctx.mixAc.close) {
      try {
        ctx.mixAc.close(); // 작업 215a: 오디오 트랙 미리듣기
      } catch (error) {
        /* 이미 닫혔다 */
      }
    }
    ctx.mixAc = null;
    editCancelRaf(ctx.zoomRaf);
    ctx.zoomRaf = 0;
    if (ctx.probeTimer) window.clearTimeout(ctx.probeTimer);
    if (ctx.exportTimer) window.clearTimeout(ctx.exportTimer);
    ctx.probeTimer = 0;
    ctx.exportTimer = 0;
    document.removeEventListener("keydown", ctx.onKey);
    if (ctx.drag && ctx.drag.cleanup) ctx.drag.cleanup();
    ctx.drag = null;
    ctx.vids.forEach(function (video) {
      try {
        video.pause();
        video.removeAttribute("src");
        video.load();
      } catch (error) {
        /* 이미 떨어진 요소 */
      }
    });
    if (ctx.audio) {
      // 지시서 118b: 원본 소리 요소도 비운다
      try {
        ctx.audio.pause();
        ctx.audio.removeAttribute("src");
        ctx.audio.load();
      } catch (error) {
        /* 이미 떨어진 요소 */
      }
    }
    if (ctx.saveTimer || ctx.rev !== ctx.savedRev) editFlushSave(ctx);
  }

  /* GET /edit(?variant=). probing(길이 재는 중)이면 1초 뒤 다시 부르고(onWait 로 안내), 다 읽은 보기를 준다. */
  function editFetchView(ctx, variant, onWait) {
    var url = editPath(ctx) + (variant ? "?variant=" + encodeURIComponent(variant) : "");
    var tries = 0;
    return new Promise(function (resolve, reject) {
      function once() {
        ctx.probeTimer = 0;
        if (!editAlive(ctx)) {
          reject({ left: true, message: "" });
          return;
        }
        api(url).then(function (view) {
          if (!view || view.probing !== true) {
            resolve(view || {});
            return;
          }
          tries += 1;
          if (tries >= EDIT_PROBE_MAX_TRIES) {
            reject({ message: "영상 길이를 다 읽지 못했습니다. 잠시 뒤 다시 열어 주세요." });
            return;
          }
          if (onWait) onWait(view);
          ctx.probeTimer = window.setTimeout(once, EDIT_PROBE_RETRY_MS);
        }, reject);
      }
      once();
    });
  }

  /* 처음 열기·다시 불러오기(409). notice 는 다 그린 뒤 머리 아래 한 줄. */
  function editLoad(ctx, variant, notice) {
    ctx.busy = true;
    editFetchView(ctx, variant, function () {
      editShowWait(ctx, "영상 길이를 읽는 중…");
    })
      .then(function (view) {
        ctx.busy = false;
        if (!editAlive(ctx)) return;
        editApplyView(ctx, view);
        ctx.notice = notice || "";
        editBuild(ctx);
      })
      .catch(function (err) {
        ctx.busy = false;
        if (!editAlive(ctx) || (err && err.left)) return;
        if (editGoneKind(err)) editShowGone(ctx, err);
        else editShowLoadError(ctx, err);
      });
  }

  /* 받은 보기를 상태에 넣는다. 실행 취소 스택·저장 차례는 새로 시작한다(재생헤드·확대는 둔다).
     작업 190: keepHist(그림체 탭 바꾸기)면 그 그림체가 이 화면에서 쓰던 실행 취소 스택을 되살린다(없으면 빈 스택). */
  function editApplyView(ctx, view, keepHist) {
    var variant = view.variant === "real" ? "real" : "webtoon";
    var savedVariants = view.savedVariants && typeof view.savedVariants === "object" ? view.savedVariants : {};
    ctx.variant = variant;
    ctx.variants = Array.isArray(view.variants) ? view.variants : [];
    ctx.clips = editCloneClips((view.doc || {}).clips);
    ctx.mix = editMixNorm(view.doc); // 작업 215a: 오디오 트랙(옛 문서는 빈 트랙)
    ctx.mixSel = null;
    ctx.mixKey = null;
    ctx.etag = String(view.etag || "");
    ctx.saved = view.saved === true;
    ctx.savedVariants = { real: savedVariants.real === true, webtoon: savedVariants.webtoon === true };
    if (ctx.saved) ctx.savedVariants[variant] = true;
    ctx.defaults[variant] = editCloneClips(view.defaultClips);
    ctx.limits = view.limits || {};
    ctx.retakeOn = !!(view.retake && view.retake.enabled === true); // 지시서 116
    ctx.invalidIds = {};
    (Array.isArray(view.invalidClips) ? view.invalidClips : []).forEach(function (item) {
      if (item && item.id) ctx.invalidIds[String(item.id)] = String(item.reason || "notAllowed");
    });
    editAbsorbMaterials(ctx, view);
    ctx.exportState = view.export || { status: "none" };
    ctx.exportHistory = Array.isArray(view.exportHistory) ? view.exportHistory : [];
    ctx.exportSig = "";
    if (ctx.saveTimer) window.clearTimeout(ctx.saveTimer);
    ctx.saveTimer = 0;
    ctx.hist = (keepHist && ctx.hists[variant]) || { undo: [], redo: [] };
    ctx.hists[variant] = ctx.hist;
    ctx.rev = 0;
    ctx.savedRev = 0;
    ctx.saveFailed = false;
    ctx.saveAgain = false;
    ctx.sel = -1;
    ctx.multi = []; // 작업 192
    ctx.anchor = -1;
    ctx.cutPlay = null; // 작업 190: 작업 188 고른 컷 재생 표시도 새 문서에서는 없다
    editTxApply(ctx, view); // 작업 215b: 자막 · 타이틀 · 페이드(문서 → ctx.tx)
    edit215cApplyView(ctx, view); // 작업 215c: 마커·트랙
  }

  /* 재료(그림체별 목록 + 원본 키별 정보). 두 그림체를 오가도 앞 그림체 재료 주소·길이를 남긴다(실행 취소로 돌아올 때). */
  function editAbsorbMaterials(ctx, view) {
    var list = (Array.isArray(view.materials) ? view.materials : []).filter(function (m) {
      return m && typeof m.sourceKey === "string" && m.sourceKey;
    });
    ctx.matList[view.variant === "real" ? "real" : "webtoon"] = list;
    list.forEach(function (m) {
      var old = ctx.materials[m.sourceKey] || {};
      var merged = {};
      Object.keys(m).forEach(function (key) {
        merged[key] = m[key];
      });
      if (!(Number(merged.duration) > 0) && Number(old.duration) > 0) {
        merged.duration = old.duration;
        merged.fps = old.fps;
        merged.hasAudio = old.hasAudio;
      }
      ctx.materials[m.sourceKey] = merged;
    });
  }

  function editShowWait(ctx, text) {
    editTrClose(ctx); // 작업 182
    ctx.tighten = null; // 작업 186: 다시 불러오면 빈틈 다듬기 제안도 버린다
    clear(ctx.root);
    ctx.dom = {};
    ctx.clipEls = [];
    var wait = el("div", "vnew-edit-wait");
    wait.appendChild(el("span", "vnew-prog-spinner", ""));
    wait.appendChild(el("span", "vnew-hint", text));
    ctx.root.appendChild(wait);
  }

  function editShowLoadError(ctx, err) {
    editTrClose(ctx); // 작업 182
    clear(ctx.root);
    ctx.dom = {};
    ctx.clipEls = [];
    ctx.root.appendChild(el("h2", "vnew-sec-title", "편집"));
    var message = err && err.type === "EditDisabled" ? "영상 편집 기능이 꺼져 있습니다." : errMessage(err);
    ctx.root.appendChild(planError("편집을 열지 못했습니다: " + message));
    var retry = el("button", "outline-button", "다시 시도");
    retry.type = "button";
    retry.addEventListener("click", function () {
      if (!editAlive(ctx)) return;
      editShowWait(ctx, "편집 문서를 불러오는 중…");
      editLoad(ctx, "", "");
    });
    ctx.root.appendChild(retry);
  }

  /* 재료가 없어진 오류인가(지시서 115): 409 NoMaterials(그림체 영상 없음)·404 NoGen(gen 없음). */
  function editGoneKind(err) {
    return !!err && (err.type === "NoMaterials" || err.type === "NoGen");
  }

  /* 재료가 없을 때: 오류 줄이 아니라 안내 문구(서버 문구)와 [다시 시도]·[⑤ 영상으로]. ⑦ 칩과 화면은 그대로다(지시서 115). */
  function editShowGone(ctx, err) {
    editPause(ctx);
    if (ctx.exportTimer) window.clearTimeout(ctx.exportTimer);
    ctx.exportTimer = 0;
    editTrClose(ctx); // 작업 182
    clear(ctx.root);
    ctx.dom = {};
    ctx.clipEls = [];
    ctx.root.appendChild(el("h2", "vnew-sec-title", "편집"));
    var box = el("div", "vnew-edit-gone");
    box.appendChild(el("p", "vnew-hint vnew-edit-gone-text", (err && err.message) || EDIT_GONE_TEXT));
    var row = el("div", "vnew-edit-gone-tools");
    var retry = el("button", "outline-button", "다시 시도");
    retry.type = "button";
    retry.addEventListener("click", function () {
      if (!editAlive(ctx)) return;
      editShowWait(ctx, "편집 문서를 불러오는 중…");
      editLoad(ctx, "", "");
    });
    var back = el("button", "outline-button", "⑤ 영상으로");
    back.type = "button";
    back.addEventListener("click", function () {
      if (!editAlive(ctx)) return;
      editLeave();
      state.planStep = 5;
      renderPlan();
    });
    row.appendChild(retry);
    row.appendChild(back);
    box.appendChild(row);
    ctx.root.appendChild(box);
  }

  function editShowError(ctx, message) {
    var node = ctx.dom.error;
    if (!node) return;
    node.textContent = message || "";
    node.style.display = message ? "" : "none";
  }

  function editNote(ctx, message) {
    var node = ctx.dom.notice;
    ctx.notice = message || "";
    if (!node) return;
    node.textContent = ctx.notice;
    node.style.display = ctx.notice ? "" : "none";
  }

  /* 도구 버튼: 아이콘 + 이름 + 단축키 표기, title·aria-label 에도 단축키(지시서 111 사용 편의). */
  function editToolButton(icon, label, keys, title, onClick) {
    var btn = el("button", "vnew-edit-tool");
    btn.type = "button";
    btn.appendChild(el("span", "vnew-edit-tool-icon", icon));
    btn._label = el("span", "vnew-edit-tool-text", label);
    btn.appendChild(btn._label);
    if (keys) btn.appendChild(el("span", "vnew-edit-key", keys));
    editToolTitle(btn, title, keys);
    btn._keys = keys;
    btn.addEventListener("click", onClick);
    return btn;
  }

  function editToolTitle(btn, title, keys) {
    btn.title = title + (keys ? " (" + keys + ")" : "");
    btn.setAttribute("aria-label", btn.title);
  }

  /* ==== 작업 215c 시작 ==== */
  /* 프리미어식 편집 도구·단축키(작업 215c). 새 값은 기본값이 아닐 때만 둔다(215c 전 문서·저장 바디 그대로):
     클립 gapBefore(앞 빈틈 초)·disabled(사용 안 함)·reverse(역재생)·keepPitch(false 만)·gain(dB)·color(라벨)·unlinked(소리 연결 해제),
     문서 markers [{id, t, name, color}]·tracks {v1|a1: {locked, hidden}}. 서버 규칙은 app/src/vnew/edit_tools.py 와 같다.
     기존 함수에는 짧은 연결 줄만 넣었다(edit215c* 호출). 단축키는 두 묶음: "vnew"(⑦ 기본 — M 소리 끄기·Delete 지우고 당기기·
     Shift+←/→ 1초를 그대로 둔다)와 "premiere"(Premiere Pro 그대로 — M 마커·Delete 들어내기·Shift+←/→ 5프레임). ? 창에서 고른다. */
  var EDIT215C_GAP_MAX_S = 300;
  var EDIT215C_GAIN_MIN = -24;
  var EDIT215C_GAIN_MAX = 12;
  var EDIT215C_REVERSE_MAX_S = 15;
  var EDIT215C_COLORS = ["red", "orange", "yellow", "green", "teal", "blue", "violet", "pink", "gray"];
  var EDIT215C_COLOR_NAMES = {
    red: "빨강", orange: "주황", yellow: "노랑", green: "초록", teal: "청록", blue: "파랑", violet: "보라", pink: "분홍", gray: "회색"
  };
  var EDIT215C_SNAP_PX = 8;
  var EDIT215C_PRESET_KEY = "vnew.edit.keys215c";
  var EDIT215C_SNAP_KEY = "vnew.edit.snap215c";
  var EDIT215C_HEIGHT_KEY = "vnew.edit.heights215c";
  var EDIT215C_HEIGHTS = { v: [40, 56, 84], a: [28, 40, 64] };
  var EDIT215C_LOCKED_NOTE = "영상 트랙이 잠겨 있습니다. 트랙 이름 칸의 🔒 를 눌러 풀어 주세요.";
  var EDIT215C_TOOLS = [
    { key: "select", icon: "↖", label: "선택 도구", keys: "V" },
    { key: "razor", icon: "✂", label: "자르기 도구 — 클립을 누른 자리에서 자르기", keys: "C" },
    { key: "ripple", icon: "⇤", label: "잔물결 편집 도구 — 끝을 끌면 뒤 클립이 함께 당겨지거나 밀립니다", keys: "B" },
    { key: "roll", icon: "⇹", label: "롤링 편집 도구 — 두 클립 사이 편집점을 옮깁니다(전체 길이 그대로)", keys: "N" },
    { key: "slip", icon: "⇆", label: "밀어넣기 도구 — 자리·길이는 그대로 두고 보이는 원본 구간을 옮깁니다", keys: "Y" },
    { key: "slide", icon: "⇔", label: "밀기 도구 — 클립을 이웃 사이에서 옮깁니다(이웃 길이 합 그대로)", keys: "U" }
  ];
  /* 단축키 표(? 창). keys 가 글이면 두 묶음 같고, {vnew, premiere} 면 묶음마다 다르다. */
  var EDIT215C_KEYMAP = [
    ["도구", "V", "선택 도구"], ["도구", "C", "자르기 도구"], ["도구", "B", "잔물결 편집 도구"], ["도구", "N", "롤링 편집 도구"],
    ["도구", "Y", "밀어넣기 도구"], ["도구", "U", "밀기 도구"],
    ["재생", "Space", "재생 / 멈춤"], ["재생", "L", "앞으로 재생 — 다시 누르면 2× · 4×"],
    ["재생", "J", "뒤로 재생(브라우저가 거꾸로 재생을 못 하면 화면을 뒤로 넘깁니다) — 다시 누르면 2× · 4×"], ["재생", "K", "멈춤"],
    ["재생", "K+J / K+L", "K 를 누른 채로 한 프레임 뒤 / 앞"], ["재생", "← / →", "한 프레임 뒤 / 앞"],
    ["재생", { vnew: "Alt+← / Alt+→", premiere: "Shift+← / Shift+→" }, "5프레임 뒤 / 앞"],
    ["재생", { vnew: "Shift+← / Shift+→", premiere: "" }, "1초 뒤 / 앞"],
    ["재생", "↑ / ↓", "앞 / 다음 편집점으로"], ["재생", "Home / End", "처음 / 끝으로"],
    ["편집", "S / Ctrl+B", "재생헤드에서 자르기"],
    ["편집", { vnew: "Alt+Delete", premiere: "Delete" }, "들어내기(고른 클립을 빼고 빈틈을 남김)"],
    ["편집", { vnew: "Delete · Shift+Delete", premiere: "Shift+Delete" }, "지우고 당기기(뒤 클립이 당겨짐)"],
    ["편집", "Shift+E", "클립 사용 / 사용 안 함"], ["편집", "Ctrl+R", "속도·지속 시간(역재생 · 음높이 유지)"],
    ["편집", "Ctrl+Alt+C / Ctrl+Alt+V", "속성 복사 / 붙여넣기(라벨 색 · 속도 · 게인)"],
    ["편집", { vnew: "M", premiere: "" }, "고른 클립 소리 끄기"], ["편집", "Ctrl+Z / Ctrl+Shift+Z", "실행 취소 / 다시 실행"],
    ["편집", "[ ] / Shift+[ ]", "고른 클립 끝 / 시작 한 프레임 다듬기"],
    ["표시", "I / O", "시작 표시 / 끝 표시"], ["표시", "X", "재생헤드 아래 클립을 표시"],
    ["표시", ";", "표시 구간 들어내기(빈틈 남김)"], ["표시", "'", "표시 구간 뽑아내기(뒤가 당겨짐)"],
    ["표시", { vnew: "Alt+M", premiere: "M" }, "마커 추가"], ["표시", "Shift+M / Ctrl+Shift+M", "다음 / 앞 마커로"],
    ["타임라인", "= / -", "확대 / 축소"], ["타임라인", "Alt+휠", "확대 / 축소"], ["타임라인", "\\", "시퀀스 전체 맞추기"],
    ["타임라인", "Shift+N", "붙기(스냅) 켜기 / 끄기 — 클립 · 소리 끌기 모두"],
    ["타임라인", "?", "이 단축키 표"], ["타임라인", "Esc", "메뉴 · 창 닫기"]
  ];
  var edit215cRate = 1; // J/K/L 셔틀 배속(editSetRate 가 클립 속도에 곱한다, 평소 1)

  function edit215cGapOf(clip) {
    var g = Number((clip || {}).gapBefore);
    return isFinite(g) && g > 0 ? Math.min(editRound3(g), EDIT215C_GAP_MAX_S) : 0;
  }

  function edit215cGainOf(clip) {
    var g = Number((clip || {}).gain);
    return isFinite(g) && g >= EDIT215C_GAIN_MIN && g <= EDIT215C_GAIN_MAX ? Math.round(g * 2) / 2 : 0;
  }

  /* editCloneClip 연결: 215c 값을 기본값이 아닐 때만 사본에 옮긴다. */
  function edit215cCloneExtras(clip, copy) {
    var gap = edit215cGapOf(clip);
    if (gap) copy.gapBefore = gap;
    if (clip.disabled === true) copy.disabled = true;
    if (clip.reverse === true) copy.reverse = true;
    if (clip.keepPitch === false) copy.keepPitch = false;
    var gain = edit215cGainOf(clip);
    if (gain) copy.gain = gain;
    if (EDIT215C_COLORS.indexOf(clip.labelColor) >= 0) copy.labelColor = clip.labelColor;
    if (clip.unlinked === true) copy.unlinked = true;
  }

  /* 클립마다 {start, end}(타임라인 초, 빈틈·디졸브 반영 — editStarts). */
  function edit215cLayout(clips) {
    var info = editStarts(clips);
    return (clips || []).map(function (clip, i) {
      return { clip: clip, start: info.starts[i], end: info.starts[i] + editClipLen(clip) };
    });
  }

  /* 편집점(클립 시작·끝, 0·전체 길이). 정렬, 같은 값은 하나. */
  function edit215cEditPoints(clips) {
    var seen = {};
    var out = [];
    var pts = [0, editStarts(clips).total];
    edit215cLayout(clips).forEach(function (it) {
      pts.push(it.start, it.end);
    });
    pts.forEach(function (t) {
      var k = editRound3(t);
      if (!seen[k]) {
        seen[k] = true;
        out.push(k);
      }
    });
    return out.sort(function (a, b) { return a - b; });
  }

  /* 스냅: on 이면 points 중 EDIT215C_SNAP_PX 화면 px 안의 가장 가까운 점, 아니면 t 그대로. */
  function edit215cSnapTo(t, points, pps, on) {
    if (!on || !(pps > 0)) return t;
    var best = t;
    var dist = EDIT215C_SNAP_PX / pps;
    (points || []).forEach(function (p) {
      if (Math.abs(p - t) <= dist) {
        dist = Math.abs(p - t);
        best = p;
      }
    });
    return best;
  }

  /* 자리(pieces [{clip, start}], 타임라인 순서)에서 빈틈을 다시 센 클립 목록. 겹치면(디졸브 말고) null. */
  function edit215cPack(pieces) {
    var out = [];
    var prevEnd = 0;
    for (var i = 0; i < pieces.length; i++) {
      var clip = editCloneClip(pieces[i].clip);
      delete clip.gapBefore;
      var overlap = i > 0 ? editDissolveOf(out[i - 1]) : 0;
      var gap = editRound3(pieces[i].start - (prevEnd - overlap));
      if (gap < -0.0015) return null;
      if (gap > 0.0005) clip.gapBefore = gap;
      out.push(clip);
      prevEnd = pieces[i].start + editClipLen(clip);
    }
    return out;
  }

  /* 들어내기(Lift): 고른 클립을 빼고 그 자리를 빈틈으로 남긴다(뒤 클립 자리 그대로). 뺀 클립으로 가던 전환은 지운다. */
  function edit215cLift(clips, indexes) {
    var drop = {};
    (indexes || []).forEach(function (i) { drop[i] = true; });
    var pieces = [];
    edit215cLayout(clips).forEach(function (it, i) {
      if (drop[i]) return;
      var clip = it.clip;
      if (drop[i + 1] && editDissolveOf(clip)) {
        clip = editCloneClip(clip);
        delete clip.transition;
        delete clip.audioTail;
      }
      pieces.push({ clip: clip, start: it.start });
    });
    return edit215cPack(pieces);
  }

  /* 지우고 당기기(Ripple Delete): 고른 클립을 빼고 뒤를 당긴다. 뺀 클립 앞에 있던 빈틈은 남는다(Premiere 와 같다). */
  function edit215cRippleDelete(clips, indexes) {
    var drop = {};
    (indexes || []).forEach(function (i) { drop[i] = true; });
    var out = [];
    var carry = 0;
    (clips || []).forEach(function (clip, i) {
      if (drop[i]) {
        carry += edit215cGapOf(clip);
        if (out.length && editDissolveOf(out[out.length - 1])) delete out[out.length - 1].transition;
        return;
      }
      var copy = editCloneClip(clip);
      var gap = editRound3(edit215cGapOf(clip) + carry);
      carry = 0;
      if (gap > 0.0005) copy.gapBefore = gap;
      out.push(copy);
    });
    return out;
  }

  /* 빈틈 지우기: index 클립 앞 빈틈을 없애 뒤를 당긴다. */
  function edit215cCloseGap(clips, index) {
    if (!clips[index] || !edit215cGapOf(clips[index])) return null;
    var next = clips.slice();
    next[index] = editCloneClip(clips[index]);
    delete next[index].gapBefore;
    return next;
  }

  /* 표시 구간 [tin, tout] 들어내기(extract false — 빈틈 남김) · 뽑아내기(extract true — 뒤가 당겨짐). 걸친 클립은 나눈다(새 id 는 makeId()).
     구간에 닿은 경계의 전환·J/L 은 지운다. 바뀐 것이 없으면 null. */
  function edit215cRange(clips, tin, tout, extract, makeId, fps, minLen) {
    var a = Math.max(0, Math.min(Number(tin), Number(tout)));
    var b = Math.max(Number(tin), Number(tout));
    if (!(b - a > 1e-3)) return null;
    var cut = b - a;
    var f = Number(fps) > 0 ? Number(fps) : EDIT_DEFAULT_FPS;
    var min = Math.max(Number(minLen) || EDIT_MIN_CLIP_S, 1 / f) - 1e-6;
    var lay = edit215cLayout(clips);
    var pieces = [];
    var changed = false;
    lay.forEach(function (it, i) {
      var c = it.clip;
      var sp = editSpeedOf(c);
      var touched = it.end > a + 1e-6 && it.start < b - 1e-6;
      if (!touched) {
        var keep = c;
        var after = lay[i + 1];
        if (after && after.end > a + 1e-6 && after.start < b - 1e-6 && (editDissolveOf(c) || editShiftOf(c, "audioTail"))) {
          keep = editCloneClip(c);
          delete keep.transition;
          delete keep.audioTail;
        }
        pieces.push({ clip: keep, start: extract && it.start >= b - 1e-6 ? it.start - cut : it.start });
        return;
      }
      changed = true;
      if (it.start < a - 1e-6) {
        var left = editCloneClip(c);
        left.out = editSnap(Number(c.in) + (a - it.start) * sp, f);
        delete left.transition;
        delete left.audioTail;
        if (editSourceLen(left) >= min * sp) pieces.push({ clip: left, start: it.start });
      }
      if (it.end > b + 1e-6) {
        var right = editCloneClip(c);
        right.in = editSnap(Number(c.in) + (b - it.start) * sp, f);
        delete right.audioLead;
        delete right.gapBefore;
        if (it.start < a - 1e-6) right.id = String(makeId());
        if (editSourceLen(right) >= min * sp) pieces.push({ clip: right, start: extract ? a : b });
      }
    });
    if (!changed && !extract) return null;
    if (extract) {
      // 뽑아내기: 구간 안 빈틈도 줄인다(자리를 다시 셌으므로 pack 이 맞춘다)
      changed = changed || lay.some(function (it) { return it.start - edit215cGapOf(it.clip) < b - 1e-6 && it.start > a + 1e-6; });
      if (!changed) return null;
    }
    return edit215cPack(pieces);
  }

  /* 롤링 편집: index 와 index+1 사이 편집점을 dt(타임라인 초)만큼. 앞 클립 out·뒤 클립 in(뒤에 빈틈이 있으면 빈틈)이 함께 바뀌고
     전체 길이는 그대로. durL 은 앞 클립 원본 길이(모르면 늘리지 않는다). 못 옮기면 null. */
  function edit215cRoll(clips, index, dt, durL, fps, minLen) {
    var a = clips[index];
    var b = clips[index + 1];
    if (!a || !b) return null;
    var f = Number(fps) > 0 ? Number(fps) : EDIT_DEFAULT_FPS;
    var min = Math.max(Number(minLen) || EDIT_MIN_CLIP_S, 1 / f);
    var sa = editSpeedOf(a);
    var sb = editSpeedOf(b);
    var gap = edit215cGapOf(b);
    var lo = (Number(a.in) + min * sa - Number(a.out)) / sa;
    var hi = Number(durL) > 0 ? (Number(durL) - Number(a.out)) / sa : 0;
    if (gap) {
      hi = Math.min(hi, gap);
    } else {
      lo = Math.max(lo, -Number(b.in) / sb);
      hi = Math.min(hi, (Number(b.out) - min * sb - Number(b.in)) / sb);
    }
    var d = Math.round(Number(dt) * f) / f;
    d = Math.max(lo, Math.min(hi, d));
    if (!isFinite(d) || lo > hi + 1e-9 || Math.abs(d) < 1e-6) return null;
    var next = clips.slice();
    var na = editCloneClip(a);
    var nb = editCloneClip(b);
    na.out = editRound3(Number(a.out) + d * sa);
    if (gap) {
      var g = editRound3(gap - d);
      if (g > 0.0005) nb.gapBefore = g;
      else delete nb.gapBefore;
    } else {
      nb.in = editRound3(Number(b.in) + d * sb);
    }
    next[index] = na;
    next[index + 1] = nb;
    return next;
  }

  /* 밀기(Slide): index 클립을 dt 만큼 옮긴다. 클립 in/out 그대로, 앞 이웃 out(또는 앞 빈틈)과 뒤 이웃 in(또는 뒤 빈틈)이 바뀌어
     이웃 길이 합·전체 길이가 그대로. 뒤 이웃이 없거나 앞 이웃·빈틈이 없으면 null. durPrev 는 앞 이웃 원본 길이. */
  function edit215cSlide(clips, index, dt, durPrev, fps, minLen) {
    var c = clips[index];
    var p = clips[index - 1];
    var n = clips[index + 1];
    if (!c || !n) return null;
    var gapC = edit215cGapOf(c);
    var gapN = edit215cGapOf(n);
    if (!gapC && !p) return null;
    var f = Number(fps) > 0 ? Number(fps) : EDIT_DEFAULT_FPS;
    var min = Math.max(Number(minLen) || EDIT_MIN_CLIP_S, 1 / f);
    var lo = -Infinity;
    var hi = Infinity;
    var sp = p ? editSpeedOf(p) : 1;
    var sn = editSpeedOf(n);
    if (gapC) {
      lo = -gapC;
    } else {
      lo = (Number(p.in) + min * sp - Number(p.out)) / sp;
      hi = Number(durPrev) > 0 ? (Number(durPrev) - Number(p.out)) / sp : 0;
    }
    if (gapN) {
      hi = Math.min(hi, gapN);
    } else {
      hi = Math.min(hi, (Number(n.out) - min * sn - Number(n.in)) / sn);
      lo = Math.max(lo, -Number(n.in) / sn);
    }
    var d = Math.round(Number(dt) * f) / f;
    d = Math.max(lo, Math.min(hi, d));
    if (!isFinite(d) || lo > hi + 1e-9 || Math.abs(d) < 1e-6) return null;
    var next = clips.slice();
    if (gapC) {
      next[index] = editCloneClip(c);
      var g1 = editRound3(gapC + d);
      if (g1 > 0.0005) next[index].gapBefore = g1;
      else delete next[index].gapBefore;
    } else {
      next[index - 1] = editCloneClip(p);
      next[index - 1].out = editRound3(Number(p.out) + d * sp);
    }
    next[index + 1] = editCloneClip(n);
    if (gapN) {
      var g2 = editRound3(gapN - d);
      if (g2 > 0.0005) next[index + 1].gapBefore = g2;
      else delete next[index + 1].gapBefore;
    } else {
      next[index + 1].in = editRound3(Number(n.in) + d * sn);
    }
    return next;
  }

  /* 밀어넣기(Slip): 자리·길이 그대로, in/out 을 함께 옮긴다. 오른쪽으로 끌면(dt > 0) 앞 원본이 보인다. dur 은 원본 길이(모르면 늘리지 않는다). */
  function edit215cSlip(clip, dt, dur, fps) {
    var f = Number(fps) > 0 ? Number(fps) : EDIT_DEFAULT_FPS;
    var ds = Math.round(-Number(dt) * editSpeedOf(clip) * f) / f;
    var lo = -Number(clip.in);
    var hi = Number(dur) > 0 ? Number(dur) - Number(clip.out) : 0;
    ds = Math.max(lo, Math.min(hi, ds));
    if (!isFinite(ds) || Math.abs(ds) < 1e-6) return null;
    var next = editCloneClip(clip);
    next.in = editRound3(Number(clip.in) + ds);
    next.out = editRound3(Number(clip.out) + ds);
    return next;
  }

  /* 속도·지속 시간 창 적용: 속도(EDIT_SPEEDS)·역재생·음높이 유지. 반환 {clip} 또는 {error: 한국어 문구}. */
  function edit215cSpeedApply(clip, speed, reverse, keepPitch, minLen) {
    var next = editSetSpeed(clip, speed, minLen);
    if (!next) return { error: "이 속도로는 클립이 너무 짧아집니다." };
    if (reverse && editSourceLen(clip) > EDIT215C_REVERSE_MAX_S + 1e-6) {
      return { error: "역재생은 원본 구간 " + EDIT215C_REVERSE_MAX_S + "초 이하 클립만 됩니다(지금 " + editSourceLen(clip).toFixed(2) +
        "초). 클립을 나누거나 줄여 주세요." };
    }
    if (reverse) next.reverse = true;
    else delete next.reverse;
    if (keepPitch === false) next.keepPitch = false;
    else delete next.keepPitch;
    return { clip: next };
  }

  /* 속성 복사·붙여넣기(라벨 색·속도·게인). 붙이면 속도가 너무 짧아지는 클립은 속도만 그대로. */
  function edit215cCopyAttrs(clip) {
    return { color: EDIT215C_COLORS.indexOf(clip.labelColor) >= 0 ? clip.labelColor : "", speed: editSpeedOf(clip), gain: edit215cGainOf(clip) };
  }

  function edit215cPasteAttrs(clip, attrs, minLen) {
    var next = editSetSpeed(clip, attrs.speed, minLen) || editCloneClip(clip);
    if (attrs.color) next.labelColor = attrs.color;
    else delete next.labelColor;
    if (attrs.gain) next.gain = attrs.gain;
    else delete next.gain;
    return next;
  }

  /* 순수 단축키 표(editKeyAction 앞에서 본다). "" 이면 ⑦ 기존 단축키가 처리한다. */
  function edit215cKeyAction(key, code, mod, shift, alt, preset) {
    var k = String(key || "");
    var c = String(code || "");
    var pr = preset === "premiere";
    function is(letter) {
      return c === "Key" + letter || k === letter || k === letter.toLowerCase();
    }
    if (mod) {
      if (alt && is("C")) return "copyAttrs";
      if (alt && is("V")) return "pasteAttrs";
      if (!alt && !shift && is("R")) return "speedDialog";
      if (shift && !alt && is("M")) return "prevMarker";
      return "";
    }
    if (alt) {
      if (is("M")) return "marker";
      if (k === "ArrowLeft") return "step5Back";
      if (k === "ArrowRight") return "step5Fwd";
      if (k === "Delete" || k === "Backspace") return "lift";
      return "";
    }
    if (k === "?" || (shift && c === "Slash")) return "keymap";
    if (k === "Delete" || k === "Backspace") return shift ? "rippleDelete" : pr ? "lift" : "";
    if (k === "ArrowLeft" || k === "ArrowRight") return shift && pr ? (k === "ArrowLeft" ? "step5Back" : "step5Fwd") : "";
    if (k === "ArrowUp") return "prevEdit";
    if (k === "ArrowDown") return "nextEdit";
    if (c === "Backslash" || k === "\\") return "fit";
    if (c === "Semicolon" || k === ";") return "liftRange";
    if (c === "Quote" || k === "'") return "extractRange";
    if (is("M")) return shift ? "nextMarker" : pr ? "marker" : "";
    if (is("E")) return shift ? "toggleEnable" : "";
    if (shift) return "";
    var tools = { V: "select", C: "razor", B: "ripple", N: "roll", Y: "slip", U: "slide" };
    for (var letter in tools) {
      if (Object.prototype.hasOwnProperty.call(tools, letter) && is(letter)) return "tool:" + tools[letter];
    }
    var plain = { I: "markIn", O: "markOut", X: "markClip", J: "shuttleJ", K: "shuttleK", L: "shuttleL" };
    for (var name in plain) {
      if (Object.prototype.hasOwnProperty.call(plain, name) && is(name)) return plain[name];
    }
    return "";
  }

  function edit215cLoad(key, fallback) {
    try {
      var v = window.localStorage.getItem(key);
      return v === null || v === undefined ? fallback : String(v);
    } catch (error) {
      return fallback;
    }
  }

  function edit215cStore(key, value) {
    try {
      window.localStorage.setItem(key, String(value));
    } catch (error) {
      /* 저장소를 못 쓰면 이번 화면에서만 */
    }
  }

  function edit215cState(ctx) {
    if (!ctx.e215) {
      var heights = edit215cLoad(EDIT215C_HEIGHT_KEY, "1,1").split(",").map(Number);
      ctx.e215 = {
        tool: "select", snap: edit215cLoad(EDIT215C_SNAP_KEY, "1") !== "0",
        preset: edit215cLoad(EDIT215C_PRESET_KEY, "vnew") === "premiere" ? "premiere" : "vnew",
        markIn: null, markOut: null, markers: [], tracks: {}, kDown: false, rev: 0, revTimer: 0, attrs: null,
        menu: null, panel: null, toolBtns: {}, markEls: [],
        heights: [heights[0] >= 0 && heights[0] <= 2 ? heights[0] : 1, heights[1] >= 0 && heights[1] <= 2 ? heights[1] : 1]
      };
    }
    return ctx.e215;
  }

  function edit215cCleanMarkers(list) {
    return (Array.isArray(list) ? list : []).filter(function (m) {
      return m && typeof m.id === "string" && isFinite(Number(m.t));
    }).map(function (m) {
      return { id: m.id, t: editRound3(Number(m.t)), name: typeof m.name === "string" ? m.name : "",
               color: EDIT215C_COLORS.indexOf(m.color) >= 0 ? m.color : "green" };
    }).sort(function (a, b) { return a.t - b.t; });
  }

  function edit215cCleanTracks(raw) {
    var out = {};
    ["v1", "a1"].forEach(function (key) {
      var t = raw && typeof raw === "object" ? raw[key] : null;
      var flags = {};
      if (t && t.locked === true) flags.locked = true;
      if (t && t.hidden === true) flags.hidden = true;
      if (flags.locked || flags.hidden) out[key] = flags;
    });
    return out;
  }

  /* editApplyView 연결: 문서의 마커·트랙을 받는다(그림체마다 따로). 표시 구간은 지운다. */
  function edit215cApplyView(ctx, view) {
    var s = edit215cState(ctx);
    var doc = (view && view.doc) || {};
    s.markers = edit215cCleanMarkers(doc.markers);
    s.tracks = edit215cCleanTracks(doc.tracks);
    s.markIn = null;
    s.markOut = null;
  }

  /* editFlushSave 연결: 마커·트랙이 있을 때만 PUT 바디에 더한다. */
  function edit215cBody(ctx, body) {
    var s = ctx && ctx.e215;
    if (s && s.markers.length) body.markers = edit215cCleanMarkers(s.markers);
    if (s && Object.keys(s.tracks).length) body.tracks = edit215cCleanTracks(s.tracks);
    return body;
  }

  function edit215cSnapExtras(ctx, snap) {
    var s = ctx && ctx.e215;
    if (!s || !snap || snap.e215) return;
    snap.e215 = { markers: edit215cCleanMarkers(s.markers), tracks: edit215cCleanTracks(s.tracks) };
  }

  function edit215cRestore(ctx, snap) {
    var s = ctx && ctx.e215;
    if (!s || !snap || !snap.e215) return;
    s.markers = edit215cCleanMarkers(snap.e215.markers);
    s.tracks = edit215cCleanTracks(snap.e215.tracks);
    edit215cPaintTracks(ctx);
  }

  /* editCommit 연결: 빈틈 바로 앞 디졸브가 빈틈의 45% 를 넘으면 컷으로(서버 check_rules), 쌓은 상태에 마커·트랙을 함께 둔다. */
  function edit215cOnCommit(ctx) {
    var list = ctx.clips;
    for (var i = 0; i + 1 < list.length; i++) {
      var gap = edit215cGapOf(list[i + 1]);
      if (gap && editDissolveOf(list[i]) > EDIT_DISSOLVE_SHARE * gap + 1e-9) {
        list[i] = editCloneClip(list[i]);
        delete list[i].transition;
      }
    }
    edit215cSnapExtras(ctx, ctx.hist.undo[ctx.hist.undo.length - 1]);
  }

  /* 마커·트랙만 바꾸는 변경(실행 취소 한 단계). */
  function edit215cCommitMeta(ctx, markers, tracks) {
    var s = edit215cState(ctx);
    var before = editSnapshotOf(ctx.variant, ctx.clips);
    edit215cSnapExtras(ctx, before);
    s.markers = edit215cCleanMarkers(markers);
    s.tracks = edit215cCleanTracks(tracks);
    editCommit(ctx, editCloneClips(ctx.clips), { before: before });
    edit215cPaintTracks(ctx);
  }

  function edit215cLocked(ctx) {
    var s = ctx.e215;
    return !!(s && s.tracks.v1 && s.tracks.v1.locked);
  }

  /* editDo · edit215cDo 가 한 번씩 부른다(동작 길마다 한 곳): 영상 트랙이 잠겼으면 V1 클립을 바꾸는 단축키·버튼을 막는다.
     작업 219b: 고른 소리 아이템 지우기 · 소리 끄기는 V1 잠금과 무관하다(자르기는 editDo 가 소리 아이템만 나눈다). */
  function edit215cBlocked(ctx, action) {
    var edits = ["split", "delete", "mute", "reset", "inEarlier", "inLater", "outEarlier", "outLater", "rippleDelete", "lift",
      "liftRange", "extractRange", "toggleEnable", "pasteAttrs", "speedDialog"];
    if (!edit215cLocked(ctx) || edits.indexOf(action) < 0 || edit215cOffV1(ctx, action)) return false;
    editNote(ctx, EDIT215C_LOCKED_NOTE);
    return true;
  }

  /* 작업 219b: editDo 의 지우기 · 소리 끄기가 V1 클립이 아니라 고른 소리 아이템을 겨누는가(고른 자막 · 타이틀은 editDo 가
     editTxDo 로 먼저 처리하고, 들어내기 · 지우고 당기기 단축키는 edit215cOnKey 가 edit215cDeleteOther 로 먼저 돌린다). */
  function edit215cOffV1(ctx, action) {
    if (action !== "delete" && action !== "mute") return false;
    return !!(ctx.mix && editMixItemRef(ctx));
  }

  /* 작업 219b: V1 이 잠긴 채 자르기(S · Ctrl+B) — 재생헤드에 걸친 소리 아이템만 나누고 클립은 그대로. */
  function edit215cSplitLocked(ctx) {
    var mixSplit = ctx.mix ? editMixSplitAll(ctx.mix, ctx.t) : null;
    if (mixSplit) editMixCommit(ctx, mixSplit);
    else editNote(ctx, EDIT215C_LOCKED_NOTE);
  }

  /* 작업 219b: Delete · Shift+Delete(들어내기 · 지우고 당기기 단축키) — 고른 자막·타이틀, 그다음 고른 소리 키·아이템을 먼저 지운다.
     지웠으면 true(클립 들어내기 · 지우고 당기기로 넘어가지 않는다). */
  function edit215cDeleteOther(ctx) {
    if (!editAlive(ctx) || ctx.busy) return false;
    if (ctx.txSel && editTxDo(ctx, "delete")) return true;
    return !!(ctx.mix && editMixDelete(ctx));
  }

  /* 작업 219b: 붙기(스냅) 상태는 하나 — 215c 🧲 · 215a 🧲 붙기 · Shift+N 이 모두 이 값을 바꾸고 읽는다(localStorage 에 남는다). */
  function edit215cSnapOn(ctx) {
    return edit215cState(ctx).snap;
  }

  function edit215cSnapToggle(ctx) {
    var s = edit215cState(ctx);
    s.snap = !s.snap;
    edit215cStore(EDIT215C_SNAP_KEY, s.snap ? "1" : "0");
    edit215cPaintBar(ctx);
    if (ctx.mix && ctx.dom) editMixRenderMixer(ctx);
    return s.snap;
  }

  function edit215cSel(ctx) {
    var many = editSelIndexes(ctx);
    return many.length ? many : ctx.sel >= 0 && ctx.sel < ctx.clips.length ? [ctx.sel] : [];
  }

  function edit215cMakeId(ctx) {
    var used = ctx.clips.slice();
    return function () {
      var id = editNewId(used);
      used.push({ id: id });
      return id;
    };
  }

  function edit215cSnapPoints(ctx) {
    var s = edit215cState(ctx);
    var pts = edit215cEditPoints(ctx.clips).concat([ctx.t]);
    s.markers.forEach(function (m) { pts.push(m.t); });
    if (s.markIn !== null) pts.push(s.markIn);
    if (s.markOut !== null) pts.push(s.markOut);
    return pts;
  }

  /* 단축키 실행(edit215cKeyAction 결과·도구줄·메뉴). */
  function edit215cDo(ctx, action) {
    if (!editAlive(ctx) || ctx.busy) return;
    var s = edit215cState(ctx);
    if (edit215cBlocked(ctx, action)) return;
    var fps = EDIT_DEFAULT_FPS;
    var here = editLocate(ctx.clips, ctx.t);
    if (here) fps = editFpsOf(ctx, here.clip);
    var sel = edit215cSel(ctx);
    var next;
    if (action.indexOf("tool:") === 0) {
      edit215cSetTool(ctx, action.slice(5));
    } else if (action === "keymap") {
      edit215cKeymapPanel(ctx);
    } else if (action === "lift" || action === "rippleDelete") {
      if (!sel.length) return editNote(ctx, "먼저 클립을 골라 주세요.");
      next = action === "lift" ? edit215cLift(ctx.clips, sel) : edit215cRippleDelete(ctx.clips, sel);
      if (next) editCommit(ctx, next, { sel: -1 });
    } else if (action === "markIn" || action === "markOut") {
      s[action] = editRound3(ctx.t);
      edit215cRenderMarks(ctx);
    } else if (action === "markClip") {
      var lay = here ? edit215cLayout(ctx.clips)[here.index] : null;
      if (!lay) return;
      s.markIn = editRound3(lay.start);
      s.markOut = editRound3(lay.end);
      edit215cRenderMarks(ctx);
    } else if (action === "liftRange" || action === "extractRange") {
      if (s.markIn === null || s.markOut === null) return editNote(ctx, "I 로 시작, O 로 끝을 먼저 표시해 주세요(X 는 재생헤드 아래 클립).");
      if (ctx.clips.length + 1 > editLimit(ctx, "maxClips", 200)) return editNote(ctx, "클립 수 한도 때문에 나눌 수 없습니다.");
      next = edit215cRange(ctx.clips, s.markIn, s.markOut, action === "extractRange", edit215cMakeId(ctx), fps, editMinLen(ctx));
      if (!next) return editNote(ctx, "표시 구간에 클립이 없습니다.");
      if (action === "extractRange") ctx.t = Math.min(s.markIn, s.markOut);
      s.markIn = null;
      s.markOut = null;
      editCommit(ctx, next, { sel: -1 });
    } else if (action === "shuttleL" || action === "shuttleJ" || action === "shuttleK") {
      edit215cShuttle(ctx, action);
    } else if (action === "step5Back" || action === "step5Fwd") {
      editStep(ctx, action === "step5Back" ? -1 : 1, 5 / fps);
    } else if (action === "prevEdit" || action === "nextEdit") {
      var pts = edit215cEditPoints(ctx.clips);
      var t = ctx.t;
      var target = action === "nextEdit"
        ? pts.filter(function (p) { return p > t + 1e-3; })[0]
        : pts.filter(function (p) { return p < t - 1e-3; }).pop();
      editPause(ctx);
      if (target !== undefined) editSeek(ctx, target);
    } else if (action === "fit") {
      var sc = ctx.dom.scroller;
      editSetZoom(ctx, editFitPps(editStarts(ctx.clips).total, sc ? sc.clientWidth : 0));
    } else if (action === "marker") {
      var markers = s.markers.concat([{ id: "k" + editNewId(s.markers), t: editRound3(ctx.t), name: "마커 " + (s.markers.length + 1),
                                        color: "green" }]);
      edit215cCommitMeta(ctx, markers, s.tracks);
      editNote(ctx, "마커를 넣었습니다. 🔖 마커 목록에서 이름·색을 바꿉니다.");
    } else if (action === "nextMarker" || action === "prevMarker") {
      var now = ctx.t;
      var list = s.markers.map(function (m) { return m.t; });
      var to = action === "nextMarker" ? list.filter(function (x) { return x > now + 1e-3; })[0]
        : list.filter(function (x) { return x < now - 1e-3; }).pop();
      if (to === undefined) return editNote(ctx, "그쪽에 마커가 없습니다.");
      editPause(ctx);
      editSeek(ctx, to);
    } else if (action === "toggleEnable") {
      if (!sel.length) return editNote(ctx, "먼저 클립을 골라 주세요.");
      next = editCloneClips(ctx.clips);
      var off = !(next[sel[0]].disabled === true);
      sel.forEach(function (i) {
        if (off) next[i].disabled = true;
        else delete next[i].disabled;
      });
      editCommit(ctx, next, {});
    } else if (action === "copyAttrs") {
      if (!sel.length) return editNote(ctx, "먼저 클립을 골라 주세요.");
      s.attrs = edit215cCopyAttrs(ctx.clips[sel[0]]);
      editNote(ctx, "속성(라벨 색 · 속도 · 게인)을 복사했습니다.");
    } else if (action === "pasteAttrs") {
      if (!s.attrs || !sel.length) return editNote(ctx, s.attrs ? "먼저 클립을 골라 주세요." : "먼저 속성을 복사해 주세요(Ctrl+Alt+C).");
      next = editCloneClips(ctx.clips);
      sel.forEach(function (i) {
        next[i] = edit215cPasteAttrs(next[i], s.attrs, editMinSource(editMinLen(ctx), s.attrs.speed));
      });
      editCommit(ctx, next, {});
    } else if (action === "speedDialog") {
      if (sel.length !== 1) return editNote(ctx, "클립 하나를 골라 주세요.");
      edit215cSpeedDialog(ctx, sel[0]);
    }
  }

  /* J/K/L 셔틀. L: 앞으로 1× → 2× → 4×. J: 뒤로(화면을 0.1초마다 넘긴다 — 브라우저 <video> 는 거꾸로 재생을 거의 못 한다) 1× → 2× → 4×.
     K: 멈춤. K 를 누른 채 J/L = 한 프레임 뒤/앞. */
  function edit215cShuttle(ctx, action) {
    var s = edit215cState(ctx);
    if (action === "shuttleK") {
      s.kDown = true;
      editPause(ctx);
      return;
    }
    if (s.kDown) {
      editStep(ctx, action === "shuttleL" ? 1 : -1, 0);
      return;
    }
    if (action === "shuttleL") {
      var rate = ctx.playing && !s.rev ? Math.min(4, edit215cRate * 2) : 1;
      if (s.rev) editPause(ctx);
      edit215cRate = rate;
      if (!ctx.playing) editPlay(ctx);
      else if (ctx.vids[ctx.cur] && ctx.clips[ctx.curIndex]) editSetRate(ctx.vids[ctx.cur], ctx.clips[ctx.curIndex]);
      edit215cRate = rate; // editPlay 안의 멈춤 연결이 1 로 되돌렸어도
      editNote(ctx, "앞으로 재생 " + rate + "×");
      return;
    }
    var back = s.rev ? Math.min(4, s.rev * 2) : 1;
    editPause(ctx);
    s.rev = back;
    editNote(ctx, "뒤로 재생 " + back + "×");
    var step = function () {
      s.revTimer = 0;
      if (!s.rev || !editAlive(ctx)) return;
      editSeek(ctx, ctx.t - 0.1 * s.rev);
      if (ctx.t <= 1e-6) {
        s.rev = 0;
        return;
      }
      s.revTimer = window.setTimeout(step, 100);
    };
    step();
  }

  /* editPause 연결: 셔틀 배속·뒤로 재생을 끝낸다. */
  function edit215cShuttleStop(ctx) {
    edit215cRate = 1;
    var s = ctx && ctx.e215;
    if (!s) return;
    s.rev = 0;
    if (s.revTimer) window.clearTimeout(s.revTimer);
    s.revTimer = 0;
  }

  function edit215cKeyUp(ctx, event) {
    if (!editAlive(ctx)) {
      document.removeEventListener("keyup", ctx.e215KeyUp);
      return;
    }
    if (event && (event.code === "KeyK" || event.key === "k" || event.key === "K") && ctx.e215) ctx.e215.kDown = false;
  }

  /* editOnKey 연결(기존 단축키보다 먼저). 처리했으면 true. 메뉴·창이 열린 동안은 Esc 만 받고 ⑦ 단축키를 막는다. */
  function edit215cOnKey(ctx, event) {
    if (!event || event.defaultPrevented) return false;
    var s = edit215cState(ctx);
    if (s.menu || s.panel) {
      if (event.key === "Escape") {
        edit215cCloseAll(ctx);
        if (event.preventDefault) event.preventDefault();
      }
      return true;
    }
    if (editTypingTarget(event.target || {})) return false;
    var action = edit215cKeyAction(event.key, event.code, event.ctrlKey || event.metaKey, event.shiftKey, event.altKey, s.preset);
    if (!action) return false;
    if (action === "speedDialog" && edit215cSel(ctx).length !== 1) return false; // 작업 219b: 클립 하나가 아니면 Ctrl+R 은 브라우저(새로 고침)
    if (event.preventDefault) event.preventDefault();
    if (event.repeat && action !== "step5Back" && action !== "step5Fwd" && action !== "prevEdit" && action !== "nextEdit") return true;
    if ((action === "lift" || action === "rippleDelete") && edit215cDeleteOther(ctx)) return true; // 작업 219b: 고른 자막 · 소리 먼저
    edit215cDo(ctx, action);
    return true;
  }

  function edit215cSetTool(ctx, tool) {
    var s = edit215cState(ctx);
    if (!EDIT215C_TOOLS.some(function (t) { return t.key === tool; })) return;
    s.tool = tool;
    edit215cPaintBar(ctx);
  }

  function edit215cBtn(icon, label, keys, onClick) {
    var b = el("button", "vnew-e215c-tool", icon);
    b.type = "button";
    b.title = label + (keys ? " (" + keys + ")" : "");
    b.setAttribute("aria-label", b.title);
    b.addEventListener("click", onClick);
    return b;
  }

  function edit215cPaintBar(ctx) {
    var s = edit215cState(ctx);
    EDIT215C_TOOLS.forEach(function (t) {
      var b = s.toolBtns[t.key];
      if (!b) return;
      if (s.tool === t.key) b.classList.add("is-on");
      else b.classList.remove("is-on");
      b.setAttribute("aria-pressed", s.tool === t.key ? "true" : "false");
    });
    if (s.snapBtn) {
      if (s.snap) s.snapBtn.classList.add("is-on");
      else s.snapBtn.classList.remove("is-on");
      s.snapBtn.setAttribute("aria-pressed", s.snap ? "true" : "false");
    }
    var tl = ctx.dom && ctx.dom.scroller;
    if (tl) {
      EDIT215C_TOOLS.forEach(function (t) { tl.classList.remove("e215c-tool-" + t.key); });
      tl.classList.add("e215c-tool-" + s.tool);
    }
  }

  function edit215cApplyHeights(ctx) {
    var s = edit215cState(ctx);
    var tl = ctx.dom && ctx.dom.tl;
    if (!tl || !tl.style) return;
    var v = EDIT215C_HEIGHTS.v[s.heights[0]] + "px";
    var a = EDIT215C_HEIGHTS.a[s.heights[1]] + "px";
    if (tl.style.setProperty) {
      tl.style.setProperty("--e215c-vh", v);
      tl.style.setProperty("--e215c-ah", a);
    }
  }

  function edit215cPaintTracks(ctx) {
    var s = edit215cState(ctx);
    var tl = ctx.dom && ctx.dom.scroller;
    if (!tl) return;
    [["v1", "v"], ["a1", "a"]].forEach(function (pair) {
      var t = s.tracks[pair[0]] || {};
      ["locked", "hidden"].forEach(function (flag) {
        var cls = "e215c-" + pair[1] + "-" + flag;
        if (t[flag]) tl.classList.add(cls);
        else tl.classList.remove(cls);
        var btn = (s.trackBtns || {})[pair[0] + flag];
        if (btn) btn.setAttribute("aria-pressed", t[flag] ? "true" : "false");
      });
    });
  }

  /* editBuild 연결: 타임라인 왼쪽 세로 도구줄 · 트랙 이름 칸 단추(잠금 · 숨김 · 높이) · 오른쪽 클릭 메뉴 · Alt+휠 확대. */
  function edit215cBuild(ctx) {
    var s = edit215cState(ctx);
    var d = ctx.dom;
    s.menu = null;
    s.panel = null;
    s.markEls = [];
    if (!d.tl) return;
    var bar = el("div", "vnew-e215c-bar");
    bar.setAttribute("role", "toolbar");
    bar.setAttribute("aria-label", "편집 도구");
    s.toolBtns = {};
    EDIT215C_TOOLS.forEach(function (t) {
      var b = edit215cBtn(t.icon, t.label, t.keys, function () {
        edit215cSetTool(ctx, t.key);
      });
      s.toolBtns[t.key] = b;
      bar.appendChild(b);
    });
    bar.appendChild(el("span", "vnew-e215c-sep", ""));
    s.snapBtn = edit215cBtn("🧲", "스냅 켜기/끄기 — 끌 때 편집점·재생헤드·마커·표시 · 소리 아이템에 붙습니다", "Shift+N", function () {
      edit215cSnapToggle(ctx); // 작업 219b: 215a 🧲 붙기와 같은 상태
    });
    bar.appendChild(s.snapBtn);
    bar.appendChild(edit215cBtn("🔖", "마커 목록 — 마커 추가는 " + (s.preset === "premiere" ? "M" : "Alt+M"), "Shift+M 다음 마커", function () {
      edit215cMarkerPanel(ctx);
    }));
    bar.appendChild(edit215cBtn("⌨", "단축키 보기", "?", function () {
      edit215cKeymapPanel(ctx);
    }));
    if (EDIT_PREMIERE_UI) {
      // 작업 219b: 꺼져 있으면 Pr 버튼을 만들지 않는다(CSS 로 감추면 Tab · Enter 로 닿았다)
      bar.appendChild(edit215cBtn("Pr", "Premiere Pro 프로젝트로 내보내기(XML · EDL · 재료 zip)", "", function () {
        edit215cPremiere(ctx);
      }));
    }
    d.tl.appendChild(bar);
    var labels = d.tl.children[0];
    s.trackBtns = {};
    [["v1", 1, "영상", 0], ["a1", 2, "소리", 1]].forEach(function (row) {
      var label = labels && labels.children ? labels.children[row[1]] : null;
      if (!label) return;
      var box = el("span", "vnew-e215c-tbtns");
      [["locked", "🔒", "트랙 잠그기/풀기"], ["hidden", "👁", row[0] === "v1" ? "영상 트랙 숨기기/보이기(내보내기에서도 빠집니다)"
        : "소리 트랙 끄기/켜기(내보내기에서도 빠집니다)"]].forEach(function (f) {
        var b = el("button", "vnew-e215c-tbtn", f[1]);
        b.type = "button";
        b.title = row[2] + " " + f[2];
        b.setAttribute("aria-label", b.title);
        b.addEventListener("click", function () {
          if (!editAlive(ctx) || ctx.busy) return;
          var tracks = edit215cCleanTracks(s.tracks);
          var t = tracks[row[0]] || {};
          t[f[0]] = !t[f[0]];
          tracks[row[0]] = t;
          edit215cCommitMeta(ctx, s.markers, tracks);
        });
        s.trackBtns[row[0] + f[0]] = b;
        box.appendChild(b);
      });
      var hb = el("button", "vnew-e215c-tbtn", "↕");
      hb.type = "button";
      hb.title = row[2] + " 트랙 높이(작게 · 보통 · 크게)";
      hb.setAttribute("aria-label", hb.title);
      hb.addEventListener("click", function () {
        s.heights[row[3]] = (s.heights[row[3]] + 1) % 3;
        edit215cStore(EDIT215C_HEIGHT_KEY, s.heights.join(","));
        edit215cApplyHeights(ctx);
      });
      box.appendChild(hb);
      label.appendChild(box);
    });
    d.vtrack.addEventListener("contextmenu", function (event) {
      edit215cContext(ctx, event);
    });
    d.scroller.addEventListener("wheel", function (event) {
      if (!event || !event.altKey || !editAlive(ctx)) return;
      if (event.preventDefault) event.preventDefault();
      editSetZoom(ctx, Number(event.deltaY) < 0 ? ctx.pps * 1.25 : ctx.pps / 1.25);
    }, { passive: false });
    if (!ctx.e215KeyUp) {
      ctx.e215KeyUp = function (event) {
        edit215cKeyUp(ctx, event);
      };
      document.addEventListener("keyup", ctx.e215KeyUp);
    }
    edit215cPaintBar(ctx);
    edit215cApplyHeights(ctx);
    edit215cPaintTracks(ctx);
  }

  /* editPaintClip 연결: 앞 빈틈만큼 띄우고(디졸브 겹침 반영), 사용 안 함·역재생·라벨 색·연결 해제·게인 표시. */
  function edit215cPaintClip(ctx, parts, clip) {
    var gap = edit215cGapOf(clip);
    if (gap) {
      var prev = parts.index > 0 ? ctx.clips[parts.index - 1] : null;
      var overlap = prev ? editDissolveOf(prev) : 0;
      var margin = Math.round((gap - overlap) * ctx.pps * 10) / 10 + "px";
      parts.box.style.marginLeft = margin;
      parts.abox.style.marginLeft = margin;
    }
    if (clip.disabled === true) {
      parts.box.classList.add("is-e215c-off");
      parts.abox.classList.add("is-e215c-off");
      parts.box.title += " · 사용 안 함(검은 화면 · 소리 없음)";
    }
    if (clip.reverse === true) {
      parts.box.classList.add("is-e215c-rev");
      parts.name.textContent += " · ◀역재생";
      parts.box.title += " · 역재생(미리보기는 앞으로, 내보내기는 거꾸로)";
    }
    if (EDIT215C_COLORS.indexOf(clip.labelColor) >= 0) parts.box.classList.add("is-c-" + clip.labelColor);
    if (clip.unlinked === true) {
      parts.abox.classList.add("is-e215c-unlinked");
      parts.abox.title += " · 연결 해제(소리 상자를 끌어 대사 타이밍을 옮깁니다)";
    }
    var gain = edit215cGainOf(clip);
    if (gain) parts.abox.title += " · 게인 " + (gain > 0 ? "+" : "") + gain + "dB";
  }

  /* editClipBoxes 연결: 연결 해제된 클립의 소리 상자 끌기 = 대사 타이밍(audioSlip, 기존 editApplySlip 규칙). */
  function edit215cClipBoxes(ctx, parts) {
    parts.abox.addEventListener("pointerdown", function (event) {
      var clip = ctx.clips[parts.index];
      if (!clip || clip.unlinked !== true || clip.muted === true || !editPointerOk(ctx, event) || edit215cLocked(ctx)) return;
      if (event.stopPropagation) event.stopPropagation();
      editSelect(ctx, parts.index);
      var base = editSlipOf(clip);
      editTrack(ctx, parts.abox, event, {
        threshold: 3,
        move: function (dx) {
          parts.abox.style.transform = "translateX(" + Math.round(dx) + "px)";
        },
        end: function (dx, moved) {
          parts.abox.style.transform = "";
          if (moved) editApplySlip(ctx, base + dx / ctx.pps);
        }
      });
    });
  }

  /* editRenderTimeline 연결: 빈틈 상자(오른쪽 클릭 → 빈틈 지우기) · 마커 · 표시 구간. */
  function edit215cRenderMarks(ctx) {
    var s = edit215cState(ctx);
    var d = ctx.dom;
    if (!d.ruler) return;
    s.markEls.forEach(function (node) {
      if (node.parentNode) node.parentNode.removeChild(node);
    });
    s.markEls = [];
    var add = function (parent, node) {
      parent.appendChild(node);
      s.markEls.push(node);
    };
    edit215cLayout(ctx.clips).forEach(function (it, i) {
      var gap = edit215cGapOf(it.clip);
      if (!gap) return;
      var box = el("div", "vnew-e215c-gap");
      box.style.left = Math.round((it.start - gap) * ctx.pps * 10) / 10 + "px";
      box.style.width = Math.round(gap * ctx.pps * 10) / 10 + "px";
      box.title = "빈틈 " + gap.toFixed(2) + "초 — 오른쪽 클릭: 빈틈 지우기";
      box.addEventListener("contextmenu", function (event) {
        if (event.preventDefault) event.preventDefault();
        if (event.stopPropagation) event.stopPropagation();
        edit215cMenu(ctx, event, [{ label: "빈틈 지우기", run: function () {
          var next = edit215cCloseGap(ctx.clips, i);
          if (next) editCommit(ctx, next, {});
        } }]);
      });
      add(d.vtrack, box);
    });
    s.markers.forEach(function (m) {
      var pin = el("span", "vnew-e215c-marker is-c-" + m.color);
      pin.style.left = Math.round(m.t * ctx.pps * 10) / 10 + "px";
      pin.title = (m.name || "마커") + " · " + editTimeText(m.t, true);
      add(d.ruler, pin);
    });
    if (s.markIn !== null || s.markOut !== null) {
      var a = s.markIn !== null ? s.markIn : 0;
      var b = s.markOut !== null ? s.markOut : editStarts(ctx.clips).total;
      var band = el("span", "vnew-e215c-range");
      band.style.left = Math.round(Math.min(a, b) * ctx.pps * 10) / 10 + "px";
      band.style.width = Math.max(2, Math.round(Math.abs(b - a) * ctx.pps * 10) / 10) + "px";
      band.title = "표시 구간 " + editTimeText(Math.min(a, b), true) + " ~ " + editTimeText(Math.max(a, b), true) + " (; 들어내기 · ' 뽑아내기)";
      add(d.ruler, band);
    }
  }

  function edit215cRepaint(ctx, indexes) {
    indexes.forEach(function (i) {
      if (ctx.clipEls[i] && ctx.clips[i]) editPaintClip(ctx, ctx.clipEls[i], ctx.clips[i]);
    });
    ctx.dom.content.style.width = Math.ceil(editStarts(ctx.clips).total * ctx.pps) + EDIT_TAIL_PX + "px";
    editPlaceFx(ctx);
  }

  function edit215cRazorAt(ctx, t) {
    var loc = editLocate(ctx.clips, t);
    if (!loc) return;
    if (ctx.clips.length >= editLimit(ctx, "maxClips", 200)) return editNote(ctx, "클립 수 한도 때문에 자를 수 없습니다.");
    var result = editSplit(ctx.clips, t, editNewId(ctx.clips), editFpsOf(ctx, loc.clip), editMinSource(editMinLen(ctx), editSpeedOf(loc.clip)));
    if (!result) return editNote(ctx, "클립 끝에 너무 가까워 자를 수 없습니다.");
    editPause(ctx);
    ctx.t = t;
    editCommit(ctx, result.clips, { sel: result.index });
  }

  /* 끌기(밀어넣기 · 밀기 · 롤링). make(dt) 가 새 목록(못 하면 null), touched 는 다시 칠할 클립 번호. */
  function edit215cDrag(ctx, target, event, index, touched, make) {
    var orig = ctx.clips.slice();
    var before = editSnapshotOf(ctx.variant, ctx.clips);
    var s = edit215cState(ctx);
    var points = edit215cSnapPoints(ctx);
    var anchor = edit215cLayout(ctx.clips)[index];
    editPause(ctx);
    editSelect(ctx, index);
    editTrack(ctx, target, event, {
      threshold: 2,
      move: function (dx) {
        var dt = dx / ctx.pps;
        if (anchor && s.snap) {
          var edge = anchor.end + dt;
          dt = edit215cSnapTo(edge, points, ctx.pps, true) - anchor.end;
        }
        ctx.clips = make(orig, dt) || orig.slice();
        edit215cRepaint(ctx, touched);
      },
      end: function (dx, moved, cancelled) {
        if (cancelled || !moved || ctx.clips === orig || JSON.stringify(ctx.clips) === JSON.stringify(orig)) {
          ctx.clips = orig;
          edit215cRepaint(ctx, touched);
          editSeek(ctx, ctx.t);
          return;
        }
        editCommit(ctx, ctx.clips.slice(), { before: before, sel: index });
      }
    });
  }

  /* editClipPointer 연결. 처리했으면 true(선택 도구는 기존 길 — 고르기 · 옮기기). */
  function edit215cClipPointer(ctx, parts, event) {
    var s = ctx.e215;
    if (!s) return false;
    if (edit215cLocked(ctx)) {
      if (editPointerOk(ctx, event)) {
        editSelect(ctx, parts.index);
        editNote(ctx, EDIT215C_LOCKED_NOTE);
      }
      return true;
    }
    if (s.tool === "select" || s.tool === "ripple" || s.tool === "roll") return false;
    if (!editPointerOk(ctx, event)) return true;
    if (event.stopPropagation) event.stopPropagation();
    if (event.preventDefault) event.preventDefault();
    var index = parts.index;
    if (s.tool === "razor") {
      edit215cRazorAt(ctx, edit215cSnapTo(editContentX(ctx, event) / ctx.pps, edit215cSnapPoints(ctx), ctx.pps, s.snap));
      return true;
    }
    var clip = ctx.clips[index];
    var fps = editFpsOf(ctx, clip);
    if (s.tool === "slip") {
      edit215cDrag(ctx, parts.box, event, index, [index], function (list, dt) {
        var c = edit215cSlip(list[index], dt, Number((ctx.materials[clip.sourceKey] || {}).duration) || 0, fps);
        if (!c) return null;
        var next = list.slice();
        next[index] = c;
        return next;
      });
    } else {
      var prev = ctx.clips[index - 1];
      var durPrev = prev ? Number((ctx.materials[prev.sourceKey] || {}).duration) || 0 : 0;
      edit215cDrag(ctx, parts.box, event, index, [index - 1, index, index + 1], function (list, dt) {
        return edit215cSlide(list, index, dt, durPrev, fps, editMinLen(ctx));
      });
    }
    return true;
  }

  /* editEdgePointer 연결. 잔물결 편집은 기존 끝 끌기(뒤 클립이 함께 움직인다)와 같은 길, 롤링은 이웃과 함께, 그 밖 도구는 몸통과 같다. */
  function edit215cEdgePointer(ctx, parts, edge, event) {
    var s = ctx.e215;
    if (!s) return false;
    if (edit215cLocked(ctx)) return edit215cClipPointer(ctx, parts, event);
    if (s.tool === "select" || s.tool === "ripple") return false;
    if (s.tool !== "roll") return edit215cClipPointer(ctx, parts, event);
    if (!editPointerOk(ctx, event)) return true;
    if (event.stopPropagation) event.stopPropagation();
    if (event.preventDefault) event.preventDefault();
    var left = edge === "out" ? parts.index : parts.index - 1;
    if (left < 0 || left + 1 >= ctx.clips.length) {
      editNote(ctx, "롤링 편집은 두 클립 사이 편집점에서 합니다.");
      return true;
    }
    var a = ctx.clips[left];
    var durL = Number((ctx.materials[a.sourceKey] || {}).duration) || 0;
    var fps = editFpsOf(ctx, a);
    edit215cDrag(ctx, edge === "out" ? parts.outEdge : parts.inEdge, event, left, [left, left + 1], function (list, dt) {
      return edit215cRoll(list, left, dt, durL, fps, editMinLen(ctx));
    });
    return true;
  }

  function edit215cCloseAll(ctx) {
    var s = edit215cState(ctx);
    [s.menu, s.panel].forEach(function (node) {
      if (node && node.parentNode) node.parentNode.removeChild(node);
    });
    s.menu = null;
    s.panel = null;
  }

  /* 오른쪽 클릭 메뉴(items [{label, keys, disabled, run}]). 고르면 닫고 실행한다. */
  function edit215cMenu(ctx, event, items) {
    edit215cCloseAll(ctx);
    var s = edit215cState(ctx);
    var menu = el("div", "vnew-e215c-menu");
    menu.setAttribute("role", "menu");
    menu.style.left = (Number(event && event.clientX) || 0) + "px";
    menu.style.top = (Number(event && event.clientY) || 0) + "px";
    items.forEach(function (item) {
      var b = el("button", "vnew-e215c-mitem", item.label);
      b.type = "button";
      b.setAttribute("role", "menuitem");
      if (item.keys) b.appendChild(el("span", "vnew-e215c-mkey", item.keys));
      b.disabled = !!item.disabled;
      b.addEventListener("click", function () {
        edit215cCloseAll(ctx);
        if (editAlive(ctx) && !ctx.busy) item.run();
      });
      menu.appendChild(b);
    });
    var shut = el("button", "vnew-e215c-mitem is-close", "닫기");
    shut.type = "button";
    shut.addEventListener("click", function () {
      edit215cCloseAll(ctx);
    });
    menu.appendChild(shut);
    ctx.root.appendChild(menu);
    s.menu = menu;
    return menu;
  }

  function edit215cContext(ctx, event) {
    if (!editAlive(ctx) || ctx.busy) return;
    var target = event && event.target;
    var index = -1;
    ctx.clipEls.forEach(function (parts) {
      if (editInside(parts.box, target)) index = parts.index;
    });
    if (index < 0) return;
    if (event.preventDefault) event.preventDefault();
    if (!editIsSel(ctx, index)) editSelect(ctx, index);
    var clip = ctx.clips[index];
    var s = edit215cState(ctx);
    var pr = s.preset === "premiere";
    var items = [
      { label: "속도/지속 시간…", keys: "Ctrl+R", run: function () { edit215cSpeedDialog(ctx, index); } },
      { label: "오디오 게인…", run: function () { edit215cGainDialog(ctx, index); } },
      { label: clip.unlinked === true ? "소리 연결" : "소리 연결 해제", run: function () {
        var next = editCloneClips(ctx.clips);
        if (clip.unlinked === true) delete next[index].unlinked;
        else next[index].unlinked = true;
        editCommit(ctx, next, {});
      } },
      { label: clip.disabled === true ? "클립 사용" : "클립 사용 안 함", keys: "Shift+E", run: function () { edit215cDo(ctx, "toggleEnable"); } },
      { label: "속성 복사", keys: "Ctrl+Alt+C", run: function () { edit215cDo(ctx, "copyAttrs"); } },
      { label: "속성 붙여넣기", keys: "Ctrl+Alt+V", disabled: !s.attrs, run: function () { edit215cDo(ctx, "pasteAttrs"); } },
      { label: "라벨 색…", run: function () { edit215cColorMenu(ctx, event, index); } },
      { label: "들어내기(빈틈 남김)", keys: pr ? "Delete" : "Alt+Delete", run: function () { edit215cDo(ctx, "lift"); } },
      { label: "지우고 당기기", keys: "Shift+Delete", run: function () { edit215cDo(ctx, "rippleDelete"); } }
    ];
    if (edit215cGapOf(clip)) {
      items.push({ label: "빈틈 지우기", run: function () { editCommit(ctx, edit215cCloseGap(ctx.clips, index), {}); } });
    }
    edit215cMenu(ctx, event, items);
  }

  function edit215cColorMenu(ctx, event, index) {
    var items = [{ label: "색 없음", run: function () { edit215cSetColor(ctx, index, ""); } }];
    EDIT215C_COLORS.forEach(function (c) {
      items.push({ label: EDIT215C_COLOR_NAMES[c], run: function () { edit215cSetColor(ctx, index, c); } });
    });
    edit215cMenu(ctx, event, items);
  }

  function edit215cSetColor(ctx, index, color) {
    var next = editCloneClips(ctx.clips);
    edit215cSel(ctx).concat([index]).forEach(function (i) {
      if (!next[i]) return;
      if (color) next[i].labelColor = color;
      else delete next[i].labelColor;
    });
    editCommit(ctx, next, {});
  }

  /* 작은 창(제목 · 칸들 · 확인/취소). 확인 → onOk(): 문구를 돌려주면 창에 보이고 닫지 않는다. */
  function edit215cDialog(ctx, title, fields, onOk) {
    edit215cCloseAll(ctx);
    var s = edit215cState(ctx);
    var box = el("div", "vnew-e215c-dialog");
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-label", title);
    box.appendChild(el("h3", "vnew-e215c-dtitle", title));
    fields.forEach(function (f) {
      box.appendChild(f);
    });
    var msg = el("p", "vnew-e215c-dmsg", "");
    box.appendChild(msg);
    var row = el("div", "vnew-e215c-drow");
    var ok = el("button", "primary-button", "확인");
    ok.type = "button";
    ok.addEventListener("click", function () {
      var err = onOk ? onOk() : "";
      if (err) msg.textContent = err;
      else edit215cCloseAll(ctx);
    });
    var cancel = el("button", "secondary-button", "닫기");
    cancel.type = "button";
    cancel.addEventListener("click", function () {
      edit215cCloseAll(ctx);
    });
    if (onOk) row.appendChild(ok);
    row.appendChild(cancel);
    box.appendChild(row);
    ctx.root.appendChild(box);
    s.panel = box;
    return box;
  }

  function edit215cField(label, input) {
    var wrap = el("label", "vnew-e215c-field");
    wrap.appendChild(el("span", "vnew-e215c-flabel", label));
    wrap.appendChild(input);
    return wrap;
  }

  function edit215cSpeedDialog(ctx, index) {
    var clip = ctx.clips[index];
    if (!clip) return;
    var speed = el("select", "vnew-e215c-input");
    EDIT_SPEEDS.forEach(function (v) {
      var o = el("option", "", v + "×");
      o.value = String(v);
      speed.appendChild(o);
    });
    speed.value = String(editSpeedOf(clip));
    var rev = el("input", "");
    rev.type = "checkbox";
    rev.checked = clip.reverse === true;
    var pitch = el("input", "");
    pitch.type = "checkbox";
    pitch.checked = clip.keepPitch !== false;
    var info = el("p", "vnew-hint", "지금 " + editClipLen(clip).toFixed(2) + "초 · 역재생은 원본 구간 " + EDIT215C_REVERSE_MAX_S + "초 이하 클립만 됩니다.");
    edit215cDialog(ctx, "속도/지속 시간", [edit215cField("속도", speed), edit215cField("역재생", rev), edit215cField("음높이 유지", pitch), info],
      function () {
        var res = edit215cSpeedApply(ctx.clips[index], Number(speed.value), rev.checked === true, pitch.checked !== false,
          editMinSource(editMinLen(ctx), Number(speed.value)));
        if (res.error) return res.error;
        var next = ctx.clips.slice();
        next[index] = res.clip;
        editCommit(ctx, next, { sel: index });
        return "";
      });
  }

  function edit215cGainDialog(ctx, index) {
    var clip = ctx.clips[index];
    if (!clip) return;
    var input = el("input", "vnew-e215c-input");
    input.type = "number";
    input.min = String(EDIT215C_GAIN_MIN);
    input.max = String(EDIT215C_GAIN_MAX);
    input.step = "0.5";
    input.value = String(edit215cGainOf(clip));
    edit215cDialog(ctx, "오디오 게인(dB)", [edit215cField("게인", input)], function () {
      var v = Number(input.value);
      if (!isFinite(v) || v < EDIT215C_GAIN_MIN || v > EDIT215C_GAIN_MAX) {
        return "게인은 " + EDIT215C_GAIN_MIN + "~+" + EDIT215C_GAIN_MAX + "dB 사이로 넣어 주세요.";
      }
      var next = editCloneClips(ctx.clips);
      v = Math.round(v * 2) / 2;
      if (v) next[index].gain = v;
      else delete next[index].gain;
      editCommit(ctx, next, {});
      return "";
    });
  }

  function edit215cMarkerPanel(ctx) {
    var s = edit215cState(ctx);
    var rows = [];
    if (!s.markers.length) rows.push(el("p", "vnew-hint", "마커가 없습니다. 재생헤드에서 " + (s.preset === "premiere" ? "M" : "Alt+M") + " 으로 넣습니다."));
    s.markers.forEach(function (m, i) {
      var row = el("div", "vnew-e215c-mrow");
      var go = el("button", "vnew-e215c-mgo", editTimeText(m.t, true));
      go.type = "button";
      go.title = "이 마커로 가기";
      go.addEventListener("click", function () {
        editPause(ctx);
        editSeek(ctx, m.t);
      });
      var name = el("input", "vnew-e215c-input");
      name.type = "text";
      name.value = m.name;
      name.setAttribute("aria-label", "마커 이름");
      name.addEventListener("change", function () {
        var list = edit215cCleanMarkers(s.markers);
        list[i].name = String(name.value || "").slice(0, 40);
        edit215cCommitMeta(ctx, list, s.tracks);
      });
      var color = el("select", "vnew-e215c-input");
      EDIT215C_COLORS.forEach(function (c) {
        var o = el("option", "", EDIT215C_COLOR_NAMES[c]);
        o.value = c;
        color.appendChild(o);
      });
      color.value = m.color;
      color.setAttribute("aria-label", "마커 색");
      color.addEventListener("change", function () {
        var list = edit215cCleanMarkers(s.markers);
        list[i].color = color.value;
        edit215cCommitMeta(ctx, list, s.tracks);
      });
      var del = el("button", "vnew-e215c-mdel", "삭제");
      del.type = "button";
      del.addEventListener("click", function () {
        var list = edit215cCleanMarkers(s.markers);
        list.splice(i, 1);
        edit215cCommitMeta(ctx, list, s.tracks);
        edit215cMarkerPanel(ctx);
      });
      [go, name, color, del].forEach(function (n) { row.appendChild(n); });
      rows.push(row);
    });
    edit215cDialog(ctx, "마커 목록", rows, null);
  }

  function edit215cKeymapPanel(ctx) {
    var s = edit215cState(ctx);
    var pick = el("div", "vnew-e215c-preset");
    [["vnew", "⑦ 기본(M 소리 끄기 · Delete 지우고 당기기 · Shift+화살표 1초)"], ["premiere", "Premiere Pro 그대로"]].forEach(function (p) {
      var b = el("button", "vnew-e215c-pbtn" + (s.preset === p[0] ? " is-on" : ""), p[1]);
      b.type = "button";
      b.setAttribute("aria-pressed", s.preset === p[0] ? "true" : "false");
      b.addEventListener("click", function () {
        s.preset = p[0];
        edit215cStore(EDIT215C_PRESET_KEY, p[0]);
        edit215cKeymapPanel(ctx);
      });
      pick.appendChild(b);
    });
    var list = el("dl", "vnew-e215c-keys");
    var group = "";
    EDIT215C_KEYMAP.forEach(function (row) {
      var keys = typeof row[1] === "string" ? row[1] : row[1][s.preset];
      if (!keys) return;
      if (row[0] !== group) {
        group = row[0];
        list.appendChild(el("dt", "vnew-e215c-kgroup", group));
      }
      var item = el("dd", "vnew-e215c-krow");
      item.appendChild(el("kbd", "vnew-e215c-kbd", keys));
      item.appendChild(el("span", "vnew-e215c-ktext", row[2]));
      list.appendChild(item);
    });
    edit215cDialog(ctx, "단축키", [pick, list], null);
  }

  /* Premiere Pro 로 내보내기(작업 215d 라우트 POST·GET /p/<pid>/edit/premiere — routes.py 연결은 합칠 때 한다).
     저장을 먼저 끝내고 시작, 2초마다 상태를 보고 끝나면 내려받기 링크를 연다. 빈틈·사용 안 함·역재생은 215d XML 이 아직 모른다. */
  function edit215cPremiere(ctx) {
    if (!editAlive(ctx) || ctx.busy) return;
    var variant = ctx.variant;
    var path = editPath(ctx) + "/premiere";
    var tries = 0;
    editNote(ctx, "Premiere Pro 프로젝트를 만드는 중…");
    var poll = function () {
      api(path + "?variant=" + encodeURIComponent(variant)).then(function (res) {
        if (!editAlive(ctx)) return;
        var pr = (res && res.premiere) || {};
        if (pr.status === "done" && pr.url) {
          editNote(ctx, "Premiere Pro 프로젝트(zip)를 만들었습니다.");
          try {
            window.open(pr.url, "_blank", "noopener");
          } catch (error) {
            /* 팝업을 막으면 안내만 */
          }
        } else if (pr.status === "failed") {
          editShowError(ctx, "Premiere Pro 로 내보내지 못했습니다: " + (pr.error || "알 수 없는 오류"));
        } else if ((tries += 1) < 150) {
          window.setTimeout(poll, 2000);
        }
      }).catch(function (err) {
        if (editAlive(ctx)) editShowError(ctx, "Premiere Pro 내보내기 상태를 읽지 못했습니다: " + errMessage(err));
      });
    };
    editEnsureSaved(ctx).then(function () {
      return api(path, { method: "POST", body: JSON.stringify({ variant: variant }) });
    }).then(function () {
      if (editAlive(ctx)) poll();
    }).catch(function (err) {
      if (editAlive(ctx)) editShowError(ctx, "Premiere Pro 로 내보내지 못했습니다: " + errMessage(err));
    });
  }
  /* ==== 작업 215c 끝 ==== */

  /* ---- 화면 만들기(design §5.2) ---- */

  function editBuild(ctx) {
    var root = ctx.root;
    editFixClose(ctx); // 지시서 116: 다시 그리면 열린 AI로 고치기 패널을 닫는다
    editFixPickClose(ctx); // 지시서 119: 테이크·장면 고르기 창도
    editTrClose(ctx); // 작업 182: 전환 메뉴도
    ctx.tighten = null; // 작업 186: 빈틈 다듬기 확인 칸·받는 중인 제안도(다시 그린 화면에는 없다)
    clear(root);
    var d = (ctx.dom = {});
    ctx.clipEls = [];

    // 머리: 제목 · 그림체 · 저장 상태 · [내보내기]
    var head = el("div", "vnew-edit-head");
    head.appendChild(el("h2", "vnew-sec-title vnew-edit-title", "편집"));
    d.variantBox = el("div", "vnew-edit-variants");
    head.appendChild(d.variantBox);
    d.saveBox = el("div", "vnew-edit-save");
    head.appendChild(d.saveBox);
    head.appendChild(editMnotesButton(ctx)); // 작업 199: [모델 에러 로그 보기]
    d.exportBtn = el("button", "primary-button vnew-edit-export-btn", "내보내기");
    d.exportBtn.type = "button";
    // 작업 216-fix1b: [내보내기] 한 번 = zip 하나(소리 없는 영상 mp4 · 소리 mp3 · README.txt)
    d.exportBtn.title = "지금 편집대로 소리 없는 영상(mp4)과 소리(mp3)를 zip 하나로 만듭니다(저장이 남았으면 먼저 저장)";
    d.exportBtn.addEventListener("click", function () {
      editExport(ctx);
    });
    head.appendChild(d.exportBtn);
    root.appendChild(head);
    d.error = planError("");
    root.appendChild(d.error);
    d.notice = el("p", "vnew-hint vnew-edit-notice", "");
    root.appendChild(d.notice);
    editNote(ctx, ctx.notice);

    // 미리보기: 16:9 상자(<video> 2개) + 재생 줄
    var player = el("div", "vnew-edit-player");
    var screen = el("div", "vnew-edit-screen");
    ctx.vids = [editMakeVideo(ctx, 0), editMakeVideo(ctx, 1)];
    ctx.cur = 0;
    screen.appendChild(ctx.vids[0]);
    screen.appendChild(ctx.vids[1]);
    // 지시서 118b: 원본 소리 전용 숨은 <video>(소리 없는 다시 만든 버전 → 그 칸 v1 소리). 문서 안에 두어 다시 그리면 같이 멈춘다.
    ctx.audio = document.createElement("video");
    ctx.audio.preload = "auto";
    ctx.audio.playsInline = true;
    ctx.audio.hidden = true;
    ctx.audio.style.display = "none";
    ctx.audio.setAttribute("aria-hidden", "true");
    screen.appendChild(ctx.audio);
    d.empty = el("p", "vnew-edit-empty", "클립이 없습니다. ⟲ 처음 상태로 되돌리거나 아래 재료에서 넣어 주세요.");
    screen.appendChild(d.empty);
    editTxScreen(ctx, screen); // 작업 215b: 자막 · 타이틀 · 색 · 전환 미리보기 층
    player.appendChild(screen);
    var transport = el("div", "vnew-edit-transport");
    transport.appendChild(editToolButton("⏮", "처음", "Home", "처음으로", function () {
      editDo(ctx, "home");
    }));
    transport.appendChild(editToolButton("◀|", "한 프레임 뒤", "←", "한 프레임 뒤로(Shift: 1초)", function () {
      editDo(ctx, "frameBack");
    }));
    d.btnPlay = editToolButton("▶", "재생", "Space", "재생", function () {
      editDo(ctx, "play");
    });
    d.btnPlay.classList.add("is-play");
    transport.appendChild(d.btnPlay);
    transport.appendChild(editToolButton("|▶", "한 프레임 앞", "→", "한 프레임 앞으로(Shift: 1초)", function () {
      editDo(ctx, "frameFwd");
    }));
    transport.appendChild(editToolButton("⏭", "끝", "End", "끝으로", function () {
      editDo(ctx, "end");
    }));
    d.time = el("span", "vnew-edit-time", "");
    transport.appendChild(d.time);
    player.appendChild(transport);
    // 작업 182: 미리보기는 J/L 컷·디졸브를 근사로만 보인다(editFx*). 쓰는 클립이 있을 때만 보이는 안내 한 줄(editPlaceFx).
    d.fxNote = el("p", "vnew-hint vnew-edit-fx-note", EDIT_FX_NOTE);
    d.fxNote.style.display = "none";
    player.appendChild(d.fxNote);
    root.appendChild(player);

    // 도구줄: 실행 취소 · 다시 실행 · 삭제 · 자르기 · 소리 끄기 · 처음 상태로 · 확대/축소
    var tools = el("div", "vnew-edit-tools");
    d.btnUndo = editToolButton("↶", "실행 취소", "Ctrl+Z", "실행 취소", function () {
      editDo(ctx, "undo");
    });
    d.btnRedo = editToolButton("↷", "다시 실행", "Ctrl+Shift+Z", "다시 실행(Ctrl+Y 도 됩니다)", function () {
      editDo(ctx, "redo");
    });
    d.btnDelete = editToolButton("🗑", "삭제", "Delete", "고른 클립 삭제", function () {
      editDo(ctx, "delete");
    });
    d.btnSplit = editToolButton("✂", "자르기", "S", "재생헤드에서 자르기(Ctrl+B 도 됩니다)", function () {
      editDo(ctx, "split");
    });
    d.btnMute = editToolButton("🔇", "소리 끄기", "M", "고른 클립 소리 끄기", function () {
      editDo(ctx, "mute");
    });
    d.btnReset = editToolButton("⟲", "처음 상태로", "", "처음 상태(기본 문서)로 되돌리기 — 실행 취소로 돌아올 수 있습니다", function () {
      editDo(ctx, "reset");
    });
    [d.btnUndo, d.btnRedo, d.btnDelete, d.btnSplit, d.btnMute, d.btnReset].forEach(function (btn) {
      tools.appendChild(btn);
    });
    tools.appendChild(editTightenButton(ctx)); // 작업 186: 빈틈 다듬기(모든 클립)
    tools.appendChild(editRetimeBox(ctx));
    tools.appendChild(editAudioFxBox(ctx)); // 작업 182: 고른 클립 J/L 컷
    tools.appendChild(editDialogueBox(ctx)); // 작업 186: 고른 클립 대사 타이밍 · 이 클립 빈틈 다듬기
    tools.appendChild(editCutPlayBox(ctx)); // 작업 188: 고른 클립 이 컷 처음부터 재생 · 이 컷만 재생
    tools.appendChild(editMultiBox(ctx)); // 작업 192: 여러 컷 고름 안내 · [고른 컷 AI로 고치기]
    tools.appendChild(editTxToolsBox(ctx)); // 작업 215b: 자막 · 타이틀 · 색 보정 · 시작/끝 페이드
    var zoomWrap = el("label", "vnew-edit-zoom");
    zoomWrap.title = "확대/축소(+ / −)";
    zoomWrap.appendChild(el("span", "vnew-edit-tool-icon", "🔍"));
    zoomWrap.appendChild(el("span", "vnew-edit-tool-text", "확대"));
    d.zoom = el("input", "vnew-edit-zoom-range");
    d.zoom.type = "range";
    d.zoom.min = "0";
    d.zoom.max = "1000";
    d.zoom.step = "1";
    d.zoom.setAttribute("aria-label", "확대/축소(+ / −)");
    d.zoom.addEventListener("input", function () {
      editZoomFromSlider(ctx);
    });
    zoomWrap.appendChild(d.zoom);
    zoomWrap.appendChild(el("span", "vnew-edit-key", "+/−"));
    tools.appendChild(zoomWrap);
    d.tools = tools; // 작업 186: 빈틈 다듬기 확인 칸은 열 때 도구줄 맨 끝(한 줄 전체)에 붙인다(editRenderTighten)
    root.appendChild(tools);

    // 처음 들어오면 한 줄 도움말(닫으면 다시 보이지 않는다)
    if (!editHelpClosed()) {
      var help = el("p", "vnew-edit-help");
      help.appendChild(el("span", "vnew-edit-help-text", EDIT_HELP_TEXT));
      var close = el("button", "vnew-edit-help-close", "×");
      close.type = "button";
      close.title = "도움말 닫기";
      close.setAttribute("aria-label", "도움말 닫기");
      close.addEventListener("click", function () {
        editRememberHelp();
        if (help.parentNode) help.parentNode.removeChild(help);
      });
      help.appendChild(close);
      d.help = help;
      root.appendChild(help);
    }

    // 타임라인: 왼쪽 이름 칸 + 가로 스크롤 영역(눈금자 · 영상 · 소리 · 재생헤드)
    var tl = el("div", "vnew-edit-tl");
    // 작업 188 fix2: 클립 · 빈 곳을 누르면 버튼에 남은 초점을 이 칸으로 옮긴다(editFocusTimeline, 잡기 단계라 끌기 · 다듬기보다 먼저)
    tl.setAttribute("tabindex", "-1");
    var onTlDown = function (event) {
      editFocusTimeline(ctx, event);
    };
    tl.addEventListener("pointerdown", onTlDown, true);
    d.tl = tl;
    var labels = el("div", "vnew-edit-labels");
    labels.appendChild(el("span", "vnew-edit-label is-ruler", ""));
    labels.appendChild(el("span", "vnew-edit-label is-video", "영상"));
    labels.appendChild(el("span", "vnew-edit-label is-audio", "소리"));
    tl.appendChild(labels);
    d.scroller = el("div", "vnew-edit-scroll");
    d.content = el("div", "vnew-edit-content");
    d.ruler = el("div", "vnew-edit-ruler");
    d.ruler.title = "누르거나 끌어서 재생헤드 옮기기";
    d.vtrack = el("div", "vnew-edit-track is-video");
    d.atrack = el("div", "vnew-edit-track is-audio");
    d.head = el("div", "vnew-edit-playhead");
    d.handle = el("button", "vnew-edit-handle", "✂");
    d.handle.type = "button";
    d.handle.title = "재생헤드 — 끌어서 옮기고, 누르면 이 자리에서 자르기 (S)";
    d.handle.setAttribute("aria-label", d.handle.title);
    d.head.appendChild(d.handle);
    d.drop = el("div", "vnew-edit-drop");
    d.drop.style.display = "none";
    [d.ruler, d.vtrack, d.atrack, d.head, d.drop].forEach(function (node) {
      d.content.appendChild(node);
    });
    d.labels = labels; // 작업 215a: 오디오 트랙 이름 줄(editMixRender)
    d.mixLanes = el("div", "vnew-edit-mix-lanes"); // 머리 · 놓을 자리 표시는 절대 위치라 뒤에 붙여도 소리 줄 아래에 놓인다
    d.content.appendChild(d.mixLanes);
    editTxTracks(ctx, labels); // 작업 215b: 자막 줄 · V2 타이틀 줄
    d.scroller.appendChild(d.content);
    tl.appendChild(d.scroller);
    // 작업 215a: 트랙 머리(M · S · 이득 · 덕킹)와 내 소리는 타임라인 칸 아래 한 줄 전체(grid-column 1 / -1)
    d.mixer = el("div", "vnew-edit-mixer");
    tl.appendChild(d.mixer);
    d.sounds = el("section", "vnew-edit-sounds");
    tl.appendChild(d.sounds);
    root.appendChild(tl);
    edit215cBuild(ctx); // 작업 215c: 세로 도구줄 · 트랙 단추 · 오른쪽 클릭 메뉴
    d.ruler.addEventListener("pointerdown", function (event) {
      editRulerPointer(ctx, event);
    });
    d.handle.addEventListener("pointerdown", function (event) {
      editHandlePointer(ctx, event);
    });
    d.handle.addEventListener("click", function (event) {
      // 키보드(Enter·Space)로 누른 click 만(detail 0). 마우스·터치 누르기는 pointerup 에서 자른다.
      if (event && event.detail === 0) editDo(ctx, "split");
    });
    d.scroller.addEventListener("pointerdown", function () {
      if (editAlive(ctx) && !ctx.busy) editSelect(ctx, -1);
    });
    d.scroller.addEventListener("scroll", function () {
      editOnScroll(ctx);
    });

    // 재료(눌러 넣기) · 내보내기 상태
    d.bin = el("div", "vnew-edit-bin");
    root.appendChild(d.bin);
    d.exportBox = el("section", "vnew-edit-export");
    root.appendChild(d.exportBox);

    if (!ctx.pps) ctx.pps = editFitPps(editStarts(ctx.clips).total, d.scroller.clientWidth);
    d.zoom.value = String(editSliderOf(ctx.pps));
    editRenderVariants(ctx);
    editRenderSave(ctx);
    editRenderTimeline(ctx);
    editRenderBin(ctx);
    editRenderExport(ctx);
    editSeek(ctx, ctx.t);
    editRenderPlayButton(ctx);
    editScheduleExportPoll(ctx);
    editMixRenderSounds(ctx); // 작업 215a: 내 소리는 펼칠 때(또는 문서가 소리 트랙을 쓸 때) 불러온다
    if (!editMixEmpty(ctx.mix)) editMixLoadSounds(ctx);
  }

  function editMakeVideo(ctx, slot) {
    var video = el("video", "vnew-edit-video" + (slot === 0 ? " is-active" : ""));
    video.preload = "auto";
    video.setAttribute("playsinline", "");
    video.addEventListener("error", function () {
      editOnVideoError(ctx, slot);
    });
    return video;
  }

  /* 도구줄의 고른 클립 속도·길이(지시서 115, design D23). 속도 0.5~2×, 길이(초)는 Enter·칸 떠남에 맞추고 Esc 는 되돌린다. */
  function editRetimeBox(ctx) {
    var d = ctx.dom;
    var box = el("div", "vnew-edit-retime");
    var speedField = el("label", "vnew-edit-field");
    speedField.appendChild(el("span", "vnew-edit-field-text", "속도"));
    d.speed = el("select", "vnew-edit-speed");
    EDIT_SPEEDS.forEach(function (speed) {
      var option = el("option", null, speed + "×");
      option.value = String(speed);
      d.speed.appendChild(option);
    });
    d.speed.title = "고른 클립 속도 — 0.5×는 2배로 늘고 2×는 반으로 줄어듭니다(소리 높이는 그대로)";
    d.speed.setAttribute("aria-label", d.speed.title);
    d.speed.addEventListener("change", function () {
      editApplySpeed(ctx, d.speed.value);
    });
    speedField.appendChild(d.speed);
    var lenField = el("label", "vnew-edit-field");
    lenField.appendChild(el("span", "vnew-edit-field-text", "길이(초)"));
    d.len = el("input", "vnew-edit-len");
    d.len.type = "number";
    d.len.min = String(EDIT_MIN_CLIP_S);
    d.len.step = "0.01";
    d.len.setAttribute("inputmode", "decimal");
    d.len.title = "고른 클립 길이(초) — 원본 안이면 끝을 옮기고, 원본보다 길면 속도를 낮춰 맞춥니다(0.5×까지). [ ] 는 끝, Shift+[ ] 는 시작을 1프레임씩";
    d.len.setAttribute("aria-label", d.len.title);
    d.len.addEventListener("change", function () {
      editApplyLength(ctx, d.len.value);
    });
    d.len.addEventListener("keydown", function (event) {
      if (!event) return;
      if (event.key === "Enter") {
        if (event.preventDefault) event.preventDefault();
        editApplyLength(ctx, d.len.value);
      } else if (event.key === "Escape") {
        if (event.preventDefault) event.preventDefault();
        editRenderRetime(ctx, true);
      }
    });
    lenField.appendChild(d.len);
    box.appendChild(speedField);
    box.appendChild(lenField);
    return box;
  }

  /* 속도·길이 칸을 고른 클립 값으로(없으면 비활성). 길이 칸에 초점이 있으면(입력 중) force 일 때만 값을 덮는다. */
  function editRenderRetime(ctx, force) {
    var d = ctx.dom;
    if (!d.speed || !d.len) return;
    var clip = ctx.sel >= 0 && ctx.sel < ctx.clips.length ? ctx.clips[ctx.sel] : null;
    if (editMultiOn(ctx)) clip = null; // 작업 192: 여러 컷을 고르면 한 클립 칸은 막는다
    d.speed.disabled = !clip || ctx.busy;
    d.len.disabled = !clip || ctx.busy;
    d.speed.value = String(clip ? editSpeedOf(clip) : 1);
    if (force || document.activeElement !== d.len) d.len.value = clip ? editClipLen(clip).toFixed(2) : "";
  }

  /* 작업 182: 도구줄의 고른 클립 J/L 컷 — 소리 먼저 시작(audioLead)·소리 이어 가기(audioTail). 0~2초 0.05초 슬라이더와 숫자 표시.
     끄는 동안(input)은 숫자만 바꾸고, 놓으면(change) 실행 취소 한 단계로 바꾼다(editApplyShift). */
  function editAudioFxBox(ctx) {
    var d = ctx.dom;
    var box = el("div", "vnew-edit-afx");
    d.afxLead = editShiftField(ctx, "audioLead", "소리 먼저 시작 (J컷)",
      "고른 클립의 소리를 화면보다 먼저 시작합니다 — 앞 클립 끝에 겹쳐 들립니다(0~2초)");
    d.afxTail = editShiftField(ctx, "audioTail", "소리 이어 가기 (L컷)",
      "고른 클립의 소리를 다음 클립 화면까지 이어 갑니다(0~2초)");
    box.appendChild(d.afxLead.wrap);
    box.appendChild(d.afxTail.wrap);
    d.afxNote = el("span", "vnew-hint vnew-edit-afx-note", EDIT_MUTED_NOTE);
    d.afxNote.style.display = "none";
    box.appendChild(d.afxNote);
    return box;
  }

  function editShiftField(ctx, key, label, title) {
    var wrap = el("label", "vnew-edit-afx-field");
    wrap.title = title;
    wrap.appendChild(el("span", "vnew-edit-afx-text", label));
    var range = el("input", "vnew-edit-afx-range");
    range.type = "range";
    range.min = "0";
    range.max = String(EDIT_SHIFT_MAX_S);
    range.step = String(EDIT_SHIFT_STEP_S);
    range.value = "0";
    range.setAttribute("aria-label", label);
    var out = el("output", "vnew-edit-afx-value", "0.00초");
    out.setAttribute("aria-hidden", "true");
    range.addEventListener("input", function () {
      out.textContent = editShiftSnap(range.value).toFixed(2) + "초";
    });
    range.addEventListener("change", function () {
      editApplyShift(ctx, key, range.value);
    });
    wrap.appendChild(range);
    wrap.appendChild(out);
    return { wrap: wrap, range: range, out: out, key: key };
  }

  /* J/L 칸을 고른 클립 값으로. 첫 클립은 J, 마지막 클립은 L 을 숨기고(앞·뒤 클립이 없다), 소리 끈 클립은 둘 다 막고 "소리 끈 클립". */
  function editRenderAudioFx(ctx) {
    var d = ctx.dom;
    if (!d.afxLead || !d.afxTail) return;
    var clip = editSingle(ctx); // 작업 192: 여러 컷을 고르면 막는다
    var muted = !!clip && clip.muted === true;
    var last = ctx.clips.length - 1;
    [d.afxLead, d.afxTail].forEach(function (field) {
      var hide = !!clip && (field.key === "audioLead" ? ctx.sel === 0 : ctx.sel === last);
      var value = clip ? editShiftOf(clip, field.key) : 0;
      field.wrap.style.display = hide ? "none" : "";
      field.range.disabled = !clip || muted || ctx.busy;
      field.range.value = String(value);
      field.out.textContent = value.toFixed(2) + "초";
    });
    d.afxNote.style.display = muted ? "" : "none";
  }

  /* 고른 클립의 앞당김·이어 감을 바꾼다(0 이면 키를 지운다). 첫 클립 J·마지막 클립 L·소리 끈 클립은 받지 않는다. */
  function editApplyShift(ctx, key, raw) {
    var clip = editSingle(ctx);
    if (!editAlive(ctx) || ctx.busy || !clip || clip.muted === true) {
      editRenderAudioFx(ctx);
      return;
    }
    if ((key === "audioLead" && ctx.sel === 0) || (key === "audioTail" && ctx.sel === ctx.clips.length - 1)) {
      editRenderAudioFx(ctx);
      return;
    }
    var value = editShiftSnap(raw);
    if (value === editShiftOf(clip, key)) {
      editRenderAudioFx(ctx);
      return;
    }
    var next = editCloneClip(clip);
    if (value) next[key] = value;
    else delete next[key];
    editReplaceSel(ctx, next);
  }

  /* 작업 186: 도구줄의 고른 클립 대사 타이밍(audioSlip, −1~+1초 0.05초 슬라이더 · 숫자 · 원래대로)과 이 클립 빈틈 다듬기.
     끄는 동안(input)은 숫자와 그 클립 소리 줄(파형 · 소리 구간 띠)만 옮겨 보이고, 놓으면(change) 실행 취소 한 단계로 바꾼다
     (editApplySlip). 소리 끈 클립은 막고 "소리 끈 클립". */
  function editDialogueBox(ctx) {
    var d = ctx.dom;
    var box = el("div", "vnew-edit-dlg");
    var field = el("label", "vnew-edit-dlg-field");
    field.title = "고른 클립의 소리(대사)만 화면보다 늦게(+) 또는 빨리(−) 옮깁니다(−1~+1초)";
    field.appendChild(el("span", "vnew-edit-dlg-text", "대사 타이밍"));
    var range = el("input", "vnew-edit-dlg-range");
    range.type = "range";
    range.min = String(-EDIT_SLIP_MAX_S);
    range.max = String(EDIT_SLIP_MAX_S);
    range.step = String(EDIT_SHIFT_STEP_S);
    range.value = "0";
    range.setAttribute("aria-label", "대사 타이밍");
    var out = el("output", "vnew-edit-dlg-value", editSlipText(0));
    out.setAttribute("aria-hidden", "true");
    range.addEventListener("input", function () {
      var value = editSlipSnap(range.value);
      out.textContent = editSlipText(value);
      editSlipPreview(ctx, value);
    });
    range.addEventListener("change", function () {
      editApplySlip(ctx, range.value);
    });
    field.appendChild(range);
    field.appendChild(out);
    box.appendChild(field);
    var reset = el("button", "outline-button vnew-edit-dlg-reset", "원래대로");
    reset.type = "button";
    reset.title = "대사 타이밍을 0(원래대로)으로 되돌립니다";
    reset.addEventListener("click", function () {
      editApplySlip(ctx, 0);
    });
    box.appendChild(reset);
    var one = el("button", "outline-button vnew-edit-tighten-one", "이 클립 빈틈 다듬기");
    one.type = "button";
    one.title = "고른 클립 하나만 대사 앞뒤의 빈 소리 구간을 줄입니다 — 적용 전에 확인합니다";
    one.addEventListener("click", function () {
      editTightenStart(ctx, ctx.sel);
    });
    box.appendChild(one);
    var note = el("span", "vnew-hint vnew-edit-dlg-note", EDIT_SLIP_NOTE);
    var muted = el("span", "vnew-hint vnew-edit-dlg-muted", EDIT_MUTED_NOTE);
    muted.style.display = "none";
    box.appendChild(note);
    box.appendChild(muted);
    d.slip = { range: range, out: out, reset: reset, note: note, muted: muted };
    d.btnTightenOne = one;
    return box;
  }

  /* 대사 타이밍 칸을 고른 클립 값으로. 고른 클립이 없거나 소리 끈 클립이면 막고, 소리 끈 클립은 "소리 끈 클립". */
  function editRenderDialogue(ctx) {
    var s = ctx.dom.slip;
    if (!s) return;
    var clip = editSingle(ctx); // 작업 192: 여러 컷을 고르면 막는다
    var muted = !!clip && clip.muted === true;
    var value = clip ? editSlipOf(clip) : 0;
    s.range.disabled = !clip || muted || ctx.busy;
    s.reset.disabled = !clip || muted || ctx.busy || !value;
    s.range.value = String(value);
    s.out.textContent = editSlipText(value);
    s.muted.style.display = muted ? "" : "none";
    s.note.style.display = muted ? "none" : "";
    editRenderTightenTools(ctx);
  }

  /* 끄는 동안: 고른 클립 소리 줄을 value 만큼 옮겨 칠한다(문서는 그대로 — 놓으면 editApplySlip). */
  function editSlipPreview(ctx, value) {
    var clip = editSingle(ctx);
    var parts = clip ? ctx.clipEls[ctx.sel] : null;
    if (!parts || parts.index !== ctx.sel) return;
    var preview = editCloneClip(clip);
    if (value) preview.audioSlip = value;
    else delete preview.audioSlip;
    editPaintClip(ctx, parts, preview);
  }

  /* 고른 클립의 대사 타이밍을 바꾼다(0 이면 키를 지운다 — 저장 바디에 없다). 실행 취소 한 단계. 소리 끈 클립은 받지 않는다. */
  function editApplySlip(ctx, raw) {
    var clip = editSingle(ctx);
    if (!editAlive(ctx) || ctx.busy || !clip || clip.muted === true) {
      if (clip) editSlipPreview(ctx, editSlipOf(clip));
      editRenderDialogue(ctx);
      return;
    }
    var value = editSlipSnap(raw);
    if (value === editSlipOf(clip)) {
      editSlipPreview(ctx, value);
      editRenderDialogue(ctx);
      return;
    }
    var next = editCloneClip(clip);
    if (value) next.audioSlip = value;
    else delete next.audioSlip;
    editReplaceSel(ctx, next);
  }

  /* ---- 작업 186: 빈틈 다듬기(POST /edit/tighten 제안 → 확인 칸 → 적용 = 실행 취소 한 단계, 저장은 늘 하던 자동 저장) ---- */

  function editTightenButton(ctx) {
    var btn = el("button", "vnew-edit-tighten-btn");
    btn.type = "button";
    btn.appendChild(el("span", "vnew-edit-tool-icon", "⇥⇤"));
    btn.appendChild(el("span", "vnew-edit-tool-text", "빈틈 다듬기"));
    btn.title = "클립마다 대사 앞뒤의 빈 소리 구간을 줄입니다 — 적용 전에 확인하고, 실행 취소로 되돌릴 수 있습니다";
    btn.setAttribute("aria-label", btn.title);
    btn.addEventListener("click", function () {
      editTightenStart(ctx, -1);
    });
    ctx.dom.btnTighten = btn;
    return btn;
  }

  /* 저장 중(PUT 보내는 중)·내보내기 요청 중·불러오는 중·클립 없음·제안을 받는 중이면 빈틈 다듬기를 막는다. */
  function editTightenBlocked(ctx) {
    return !!(ctx.busy || ctx.saving || ctx.exporting || !ctx.clips.length || (ctx.tighten && ctx.tighten.busy));
  }

  function editRenderTightenTools(ctx) {
    var d = ctx.dom;
    var blocked = editTightenBlocked(ctx);
    if (d.btnTighten) d.btnTighten.disabled = blocked;
    if (d.btnTightenOne) {
      var clip = editSingle(ctx); // 작업 192: 이 클립 빈틈 다듬기는 한 컷만
      d.btnTightenOne.disabled = blocked || !clip || clip.muted === true;
    }
  }

  /* ---- 작업 188: 고른 컷부터 재생(늘 하던 editSeek · editPlay · editPause 길만 쓴다) ---- */

  /* 도구줄의 고른 클립 [▶ 이 컷 처음부터 재생] · [이 컷만 재생]과 안내 한 줄. 이 버튼으로 재생 중이면 그 버튼이 [일시정지](editRenderCutPlay). */
  function editCutPlayBox(ctx) {
    var box = el("div", "vnew-edit-cutplay");
    function button(className, text, only, title) {
      var btn = el("button", "outline-button " + className);
      btn.type = "button";
      // 작업 188 fix1: 기본 글과 [❚❚ 일시정지]를 한 칸에 겹쳐 두고 aria-pressed 로 보이는 쪽만 바꾼다(vnew.css).
      // 버튼 폭은 두 글 중 긴 쪽으로 늘 같아, 재생이 시작돼도 옆 버튼이 누르던 자리에서 밀리지 않는다.
      btn.appendChild(el("span", "vnew-edit-cutplay-idle", text));
      btn.appendChild(el("span", "vnew-edit-cutplay-pause", EDIT_CUT_PLAY_PAUSE));
      btn._idleTitle = title;
      btn._mode = only ? "only" : "from";
      btn.title = title;
      btn.addEventListener("click", function () {
        editPlayCut(ctx, only, false);
      });
      box.appendChild(btn);
      return btn;
    }
    var from = button("vnew-edit-cutplay-from", "▶ 이 컷 처음부터 재생", false,
      "고른 클립의 첫 프레임부터 재생합니다 — 다음 클립으로 이어집니다 (Enter · 클립 더블클릭)");
    var only = button("vnew-edit-cutplay-only", "이 컷만 재생", true,
      "고른 클립의 첫 프레임부터 그 클립 끝까지만 재생하고 멈춥니다 (Shift+Enter)");
    var note = el("span", "vnew-hint vnew-edit-cutplay-note", EDIT_CUT_PLAY_NOTE);
    box.appendChild(note);
    ctx.dom.cutPlay = { from: from, only: only, note: note };
    return box;
  }

  /* 불러오는 중 · 저장 중(PUT) · 내보내기 요청 중 · 클립 없음 · 고른 클립 없음 · 그 클립 영상(필름) 주소가 아직 없으면 막는다. */
  function editCutPlayBlocked(ctx) {
    var clip = editSelected(ctx);
    return !!(ctx.busy || ctx.saving || ctx.exporting || !ctx.clips.length || !clip || !editUrlOf(ctx, clip.sourceKey));
  }

  /* mode("from" | "only") 버튼으로 시작한 재생이 지금 도는 중인가. */
  function editCutPlayOn(ctx, mode) {
    return !!(ctx.playing && ctx.cutPlay && ctx.cutPlay.mode === mode);
  }

  /* 두 버튼 글 · 막기. 재생 중인 버튼은 [❚❚ 일시정지](저장이 시작돼도 누를 수 있게 불러오는 중에만 막는다).
     글은 바꿔 쓰지 않는다 — 겹쳐 둔 두 글 중 aria-pressed 가 고른 쪽이 보인다(작업 188 fix1, 버튼 폭 고정). */
  function editRenderCutPlay(ctx) {
    var c = ctx.dom.cutPlay;
    if (!c) return;
    var blocked = editCutPlayBlocked(ctx);
    [c.from, c.only].forEach(function (btn) {
      var on = editCutPlayOn(ctx, btn._mode);
      btn.title = on ? "일시정지" : btn._idleTitle;
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.disabled = on ? !!ctx.busy : blocked;
    });
  }

  /* 고른 컷의 타임라인 시작(editStarts.starts[sel])으로 옮겨 재생한다. J/L 컷 · 디졸브는 그 자리를 지나는 보통 재생과 같다(editSeek · editTick).
     only 면 그 컷 끝 — 다음 클립이 화면을 받는 자리(디졸브면 겹침 시작), 마지막 클립이면 전체 끝 — 에서 멈추도록 멈출 자리를
     지금 클립으로 잰다(그 자리 한 프레임 앞, 다음 클립 첫 프레임이 아니다). restart 가 아니면 이 버튼으로 재생 중일 때 다시 누르면 일시정지. */
  function editPlayCut(ctx, only, restart) {
    if (!editAlive(ctx) || ctx.busy) return;
    var mode = only ? "only" : "from";
    if (!restart && editCutPlayOn(ctx, mode)) {
      editPause(ctx);
      return;
    }
    if (editCutPlayBlocked(ctx)) return;
    var index = ctx.sel;
    var clip = ctx.clips[index];
    var info = editStarts(ctx.clips);
    var start = info.starts[index];
    var end = index + 1 < ctx.clips.length ? info.starts[index + 1] : info.total;
    var stopAt = Math.max(start, end - 1 / (editFpsOf(ctx, clip) * editSpeedOf(clip)));
    editPause(ctx);
    editSeek(ctx, start);
    editPlay(ctx);
    if (!ctx.playing) return;
    ctx.cutPlay = { mode: mode, index: index, stopAt: stopAt };
    editRenderCutPlay(ctx);
  }

  /* [이 컷만 재생]이 그 컷 끝에 닿았다(editTick): 멈추고(ctx.cutPlay 도 지운다) 그 컷 마지막 프레임에 재생헤드를 둔다. */
  function editCutPlayEnd(ctx) {
    var stopAt = ctx.cutPlay.stopAt;
    editPause(ctx);
    editSeek(ctx, stopAt);
    editFollowHead(ctx);
  }

  /* 클립(영상 상자 · 그 소리 상자) 두 번 누르기 = 고르고 그 컷 처음부터 재생. 양 끝 손잡이 위이거나, 끄는 중이거나,
     끌기 · 끝 다듬기를 막 끝낸(EDIT_DBL_AFTER_DRAG_MS 안) 두 번 누르기는 무시한다. 한 번 누르기 · 끌기 · 다듬기는 그대로다. */
  function editClipDblClick(ctx, parts, event) {
    if (!editAlive(ctx) || ctx.busy || ctx.drag) return;
    var target = event ? event.target : null;
    if (editInside(parts.inEdge, target) || editInside(parts.outEdge, target)) return;
    if (Date.now() - (ctx.dragEndAt || 0) < EDIT_DBL_AFTER_DRAG_MS) return;
    if (event && (event.ctrlKey || event.metaKey || event.shiftKey)) return; // 작업 192: 여러 컷 고르기의 두 번 누르기는 재생하지 않는다
    if (event && event.preventDefault) event.preventDefault();
    editSelect(ctx, parts.index);
    editPlayCut(ctx, false, true);
  }

  /* ⑦ 단축키(Space · 글자 키 · Enter)를 받지 않는 입력칸 초점: input(range 포함) · textarea · select · contenteditable.
     editOnKey 와 editCutKeyOk 가 같은 규칙을 쓴다(작업 188 fix1). */
  function editTypingTarget(target) {
    var t = target || {};
    var tag = String(t.tagName || "").toLowerCase();
    return tag === "input" || tag === "textarea" || tag === "select" || !!t.isContentEditable;
  }

  /* Enter / Shift+Enter 를 ⑦ 이 받아도 되는가: 고른 클립이 있고, 초점이 입력칸(editTypingTarget) · 버튼 · 링크 · summary · [role] 이
     아니며(그 요소의 Enter 는 그대로), ⑦ 화면 안이거나 ⑦ 을 감싼 요소(body · html · 클립을 누른 뒤 초점이 남는 <main> 등)일 때. */
  function editCutKeyOk(ctx, target) {
    if (!editSelected(ctx)) return false;
    var t = target || {};
    if (editTypingTarget(t)) return false;
    var tag = String(t.tagName || "").toLowerCase();
    if (tag === "button" || tag === "a" || tag === "summary" || tag === "option") return false;
    var role = t.getAttribute ? String(t.getAttribute("role") || "") : "";
    if (role) return false;
    return editInside(ctx.root, t) || editInside(t, ctx.root);
  }

  /* 작업 188 fix2: Enter · Space 를 브라우저가 스스로 누르는 초점 요소 — 버튼 · 링크 · select · summary · option,
     글이 아닌 input(버튼 · 체크 · 라디오 · range 등), 누르는 [role](button · link · menuitem · tab …). 글 입력칸은 아니다. */
  var EDIT_FOCUS_OFF_INPUTS = ["button", "submit", "reset", "checkbox", "radio", "range", "color", "file", "image"];
  var EDIT_FOCUS_OFF_ROLES = ["button", "link", "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "checkbox", "radio",
    "switch", "option", "slider"];

  function editControlTarget(target) {
    var t = target || {};
    var tag = String(t.tagName || "").toLowerCase();
    if (tag === "button" || tag === "a" || tag === "select" || tag === "summary" || tag === "option") return true;
    if (tag === "input") {
      var type = t.type || (t.getAttribute ? t.getAttribute("type") : "") || "text";
      return EDIT_FOCUS_OFF_INPUTS.indexOf(String(type).toLowerCase()) >= 0;
    }
    if (tag === "textarea" || t.isContentEditable) return false;
    var role = t.getAttribute ? String(t.getAttribute("role") || "").toLowerCase() : "";
    return EDIT_FOCUS_OFF_ROLES.indexOf(role) >= 0;
  }

  /* 작업 188 fix2: 타임라인 칸(클립 · 소리 상자 · 빈 곳 · 눈금자)을 누르면(pointerdown 잡기 단계) ⑦ 안의 버튼 · 링크 · select 등에
     남은 초점을 타임라인 칸(tabindex -1)으로 옮긴다. 그래야 다음 Enter · Space 가 그 버튼([이 컷만 재생] · [삭제] …)을 다시 누르지 않고
     editOnKey 로 간다. 글 입력칸 초점 · ⑦ 밖 초점은 그대로, 누른 곳이 버튼(재생헤드 · 전환 손잡이)이면 그 버튼에 맡긴다.
     막기 · 끌기는 하지 않는다(끌기 · 끝 다듬기 길은 그대로). */
  function editFocusTimeline(ctx, event) {
    var tl = ctx.dom.tl;
    var active = document.activeElement;
    if (!editAlive(ctx) || !tl || !active || active === tl) return;
    if (!editInside(ctx.root, active) || !editControlTarget(active)) return;
    for (var n = event ? event.target : null; n && n !== tl; n = n.parentNode) {
      if (editControlTarget(n)) return;
    }
    if (typeof tl.focus !== "function") return;
    try {
      tl.focus({ preventScroll: true });
    } catch (error) {
      /* 초점을 줄 수 없는 요소 */
    }
  }

  /* 클립들 재료 mid(소리 끈 클립 제외, 한 번씩). res 가 없으면 파형을 아직 안 받은 것만, 있으면 서버가 "noFilm" 으로 둔 것만. */
  function editTightenMids(ctx, clips, res) {
    var skipped = {};
    ((res && res.perClip) || []).forEach(function (p) {
      if (p && p.skipped === "noFilm") skipped[String(p.id)] = true;
    });
    var out = [];
    clips.forEach(function (clip) {
      var mid = String((ctx.materials[clip.sourceKey] || {}).mid || "");
      if (!mid || clip.muted === true || out.indexOf(mid) >= 0) return;
      if (res ? skipped[String(clip.id)] : (ctx.media[mid] || {}).status !== "done") out.push(mid);
    });
    return out;
  }

  /* 파형(필름)을 하나씩 차례로 받는다 — 서버 tighten 은 필름을 만들지 않고 이미 만든 필름의 소리 구간만 본다. */
  function editTightenMedia(ctx, mids, stale) {
    var chain = Promise.resolve();
    mids.forEach(function (mid) {
      chain = chain.then(function () {
        return stale() ? null : editMediaLoad(ctx, mid);
      });
    });
    return chain;
  }

  function editTightenCall(ctx) {
    return api(editPath(ctx) + "/tighten", {
      method: "POST",
      body: JSON.stringify({ variant: ctx.variant, clips: editCloneClips(ctx.clips) })
    });
  }

  /* [빈틈 다듬기](index < 0: 모든 클립) · [이 클립 빈틈 다듬기](index = 고른 클립). 지금(저장 전) 클립으로 제안을 받아 확인 칸을 연다.
     파형을 안 받은 재료는 먼저 받고, 그래도 "noFilm" 이 있으면 그 재료를 받아 한 번 더 부른다. 그사이 편집이 바뀌면 버린다. */
  function editTightenStart(ctx, index) {
    if (!editAlive(ctx) || editTightenBlocked(ctx)) return;
    var only = "";
    if (index >= 0) {
      if (editMultiOn(ctx)) return; // 작업 192: 여러 컷을 고르면 이 클립 빈틈 다듬기는 없다
      var picked = ctx.clips[index];
      if (!picked || picked.muted === true) return;
      only = String(picked.id);
    }
    editTightenClose(ctx);
    var job = { busy: true, open: false, rev: ctx.rev, only: only, plan: null };
    ctx.tighten = job;
    editShowError(ctx, "");
    editNote(ctx, "대사 구간을 찾는 중…");
    editRenderTightenTools(ctx);
    var targets = ctx.clips.filter(function (clip) {
      return !only || String(clip.id) === only;
    });
    function stale() {
      return !editAlive(ctx) || ctx.tighten !== job || ctx.rev !== job.rev;
    }
    editTightenMedia(ctx, editTightenMids(ctx, targets, null), stale)
      .then(function () {
        return stale() ? null : editTightenCall(ctx);
      })
      .then(function (res) {
        if (!res || stale()) return res;
        var again = editTightenMids(ctx, targets, res);
        if (!again.length) return res;
        return editTightenMedia(ctx, again, stale).then(function () {
          return stale() ? null : editTightenCall(ctx);
        });
      })
      .then(function (res) {
        if (ctx.tighten !== job) return;
        job.busy = false;
        if (!editAlive(ctx)) {
          ctx.tighten = null;
          return;
        }
        if (!res || ctx.rev !== job.rev) {
          ctx.tighten = null;
          editNote(ctx, EDIT_TIGHTEN_STALE);
          editRenderTightenTools(ctx);
          return;
        }
        job.plan = editTightenPlan(ctx.clips, res, only);
        job.open = true;
        editNote(ctx, "");
        editRenderTighten(ctx);
        editRenderTightenTools(ctx);
      })
      .catch(function (err) {
        if (ctx.tighten !== job) return;
        ctx.tighten = null;
        if (!editAlive(ctx)) return;
        editNote(ctx, "");
        editShowError(ctx, "빈틈을 다듬지 못했습니다: " + errMessage(err));
        editRenderTightenTools(ctx);
      });
  }

  /* 확인 칸: "클립 N개에서 앞뒤 빈 소리 구간을 줄입니다 · 총 X.X초 짧아짐" · 클립별 줄(앞 −0.8초 · 뒤 −1.2초 / 그대로 둠 (…) /
     줄이거나 뺀 디졸브) · [적용] [취소]. 도구줄 맨 끝에 한 줄 전체로 붙인다(열려 있을 때만 있다). */
  function editRenderTighten(ctx) {
    var d = ctx.dom;
    var job = ctx.tighten;
    if (d.tightenBox && d.tightenBox.parentNode) d.tightenBox.parentNode.removeChild(d.tightenBox);
    d.tightenBox = null;
    if (!job || !job.open || !job.plan || !d.tools) return;
    var plan = job.plan;
    var box = el("section", "vnew-edit-tighten");
    box.setAttribute("aria-label", "빈틈 다듬기");
    box.setAttribute("aria-live", "polite");
    box.appendChild(el("p", "vnew-edit-tighten-title", plan.count
      ? "클립 " + plan.count + "개에서 앞뒤 빈 소리 구간을 줄입니다 · 총 " + editTightenSec(plan.saved) + "초 짧아짐"
      : "줄일 빈 소리 구간이 없습니다"));
    var list = el("ul", "vnew-edit-tighten-list");
    plan.rows.forEach(function (row) {
      var clip = ctx.clips[row.index] || {};
      var mat = ctx.materials[clip.sourceKey] || {};
      var cut = editTightenCutText(row.head, row.tail);
      var item = el("li", "vnew-edit-tighten-row" + (cut ? "" : " is-kept"));
      item.appendChild(el("span", "vnew-edit-tighten-name", row.index + 1 + ". " + (clip.label || mat.label || "클립")));
      item.appendChild(el("span", "vnew-edit-tighten-cut", cut || EDIT_TIGHTEN_SKIP[row.skipped] || EDIT_TIGHTEN_NONE));
      row.notes.forEach(function (note) {
        item.appendChild(el("span", "vnew-edit-tighten-tr", note));
      });
      list.appendChild(item);
    });
    box.appendChild(list);
    var actions = el("div", "vnew-edit-tighten-actions");
    var apply = el("button", "primary-button vnew-edit-tighten-apply", "적용");
    apply.type = "button";
    apply.disabled = !plan.count;
    apply.title = "한 번에 적용합니다 — 실행 취소(Ctrl+Z) 한 번으로 되돌릴 수 있습니다";
    apply.addEventListener("click", function () {
      editTightenApply(ctx);
    });
    var cancel = el("button", "outline-button vnew-edit-tighten-cancel", "취소");
    cancel.type = "button";
    cancel.addEventListener("click", function () {
      editTightenClose(ctx, true);
    });
    actions.appendChild(apply);
    actions.appendChild(cancel);
    box.appendChild(actions);
    d.tools.appendChild(box);
    d.tightenBox = box;
    var first = plan.count ? apply : cancel;
    if (typeof first.focus === "function") {
      try {
        first.focus({ preventScroll: true });
      } catch (error) {
        /* 초점을 줄 수 없는 요소 */
      }
    }
  }

  /* [적용]: 받은 클립을 한 번의 변경(editCommit — 실행 취소 한 단계, 디졸브 정리 editTidyFx)으로 바꾼다. 저장은 늘 하던 자동 저장. */
  function editTightenApply(ctx) {
    var job = ctx.tighten;
    if (!editAlive(ctx) || ctx.busy || !job || !job.open || !job.plan || !job.plan.count) return;
    if (ctx.rev !== job.rev) {
      editTightenClose(ctx);
      editNote(ctx, EDIT_TIGHTEN_STALE);
      return;
    }
    var plan = job.plan;
    var max = editLimit(ctx, "maxTotalSeconds", 300);
    if (editStarts(plan.clips).total > max + 1e-6) {
      editNote(ctx, "타임라인 전체 길이는 " + max + "초까지입니다.");
      return;
    }
    editTightenClose(ctx);
    editCommit(ctx, plan.clips, { sel: ctx.sel });
    editNote(ctx, "빈틈을 다듬었습니다 · 총 " + editTightenSec(plan.saved) + "초 짧아짐 — 실행 취소(Ctrl+Z)로 되돌릴 수 있습니다.");
  }

  /* 확인 칸을 닫고 받는 중인 제안도 버린다. refocus 면 [빈틈 다듬기] 로 초점을 돌린다. */
  function editTightenClose(ctx, refocus) {
    var d = ctx.dom || {};
    var had = !!ctx.tighten;
    ctx.tighten = null;
    if (d.tightenBox && d.tightenBox.parentNode) d.tightenBox.parentNode.removeChild(d.tightenBox);
    d.tightenBox = null;
    if (!had) return;
    editRenderTightenTools(ctx);
    if (refocus && d.btnTighten && typeof d.btnTighten.focus === "function") {
      try {
        d.btnTighten.focus();
      } catch (error) {
        /* 이미 떨어진 버튼 */
      }
    }
  }

  /* 그림체 탭. 작업 190: 탭 = 그 그림체의 편집본(따로 저장). 지금 탭은 is-on · aria-pressed, 저장된 편집본이 있는 탭은 점(is-saved). */
  function editRenderVariants(ctx) {
    var box = ctx.dom.variantBox;
    if (!box) return;
    clear(box);
    ctx.variants.forEach(function (item) {
      if (!item || !EDIT_VARIANT_LABEL[item.key]) return;
      var label = EDIT_VARIANT_LABEL[item.key];
      var on = item.key === ctx.variant;
      var saved = (ctx.savedVariants || {})[item.key] === true;
      var btn = el("button", "vnew-edit-variant" + (on ? " is-on" : "") + (saved ? " is-saved" : ""), EDIT_VARIANT_LABEL[item.key]);
      btn.type = "button";
      btn.disabled = item.available !== true;
      btn.title = item.available === true ? EDIT_VARIANT_TIP[item.key] : "아직 " + label + " 영상이 없습니다";
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      btn.setAttribute("aria-label", label + (saved ? " · " + EDIT_VARIANT_SAVED : ""));
      if (saved) {
        var dot = el("span", "vnew-edit-variant-dot", "");
        dot.setAttribute("aria-hidden", "true");
        dot.title = EDIT_VARIANT_SAVED;
        btn.appendChild(dot);
      }
      btn.addEventListener("click", function () {
        editSwitchVariant(ctx, item.key);
      });
      box.appendChild(btn);
    });
  }

  /* 저장 상태: 저장 전 · 저장 중… · 저장됨 · [저장 실패 — 다시]. */
  function editSaveStateOf(ctx) {
    if (ctx.saveFailed) return "failed";
    if (ctx.saving || ctx.saveTimer || ctx.rev !== ctx.savedRev) return "saving";
    return ctx.saved ? "saved" : "draft";
  }

  function editRenderSave(ctx) {
    var box = ctx.dom.saveBox;
    if (!box) return;
    editRenderTightenTools(ctx); // 작업 186: 저장 중(PUT)에는 빈틈 다듬기를 막는다
    editRenderCutPlay(ctx); // 작업 188: 고른 컷부터 재생도
    var kind = editSaveStateOf(ctx);
    clear(box);
    box.className = "vnew-edit-save is-" + kind;
    if (kind === "failed") {
      var retry = el("button", "outline-button vnew-edit-save-retry", "저장 실패 — 다시");
      retry.type = "button";
      retry.addEventListener("click", function () {
        editFlushSave(ctx);
      });
      box.appendChild(retry);
      return;
    }
    box.textContent = kind === "saving" ? "저장 중…" : kind === "saved" ? "저장됨" : "저장 전";
  }

  function editRenderPlayButton(ctx) {
    var btn = ctx.dom.btnPlay;
    if (!btn) return;
    var icon = btn.children[0];
    if (icon) icon.textContent = ctx.playing ? "❚❚" : "▶";
    if (btn._label) btn._label.textContent = ctx.playing ? "멈춤" : "재생";
    editToolTitle(btn, ctx.playing ? "멈춤" : "재생", "Space");
    btn.disabled = !ctx.clips.length;
    editRenderCutPlay(ctx); // 작업 188: 재생 · 멈춤 · 도구줄을 그릴 때마다 [이 컷 …] 두 버튼도
  }

  function editRenderTools(ctx) {
    var d = ctx.dom;
    if (!d.btnUndo) return;
    var has = ctx.sel >= 0 && ctx.sel < ctx.clips.length;
    var muted = has && ctx.clips[ctx.sel].muted === true;
    var many = editSelIndexes(ctx).length; // 작업 192
    d.btnUndo.disabled = !ctx.hist.undo.length;
    d.btnRedo.disabled = !ctx.hist.redo.length;
    d.btnDelete.disabled = !has;
    d.btnMute.disabled = !has || many > 1;
    d.btnSplit.disabled = !ctx.clips.length;
    d.btnReset.disabled = !(ctx.defaults[ctx.variant] || []).length;
    d.btnMute._label.textContent = muted ? "소리 켜기" : "소리 끄기";
    editToolTitle(d.btnMute, muted ? "고른 클립 소리 켜기" : "고른 클립 소리 끄기", "M");
    editToolTitle(d.btnDelete, many > 1 ? "고른 클립 " + many + "개 삭제" : "고른 클립 삭제", "Delete"); // 작업 192
    d.exportBtn.disabled = ctx.exporting || !ctx.clips.length;
    editRenderRetime(ctx, false);
    editRenderAudioFx(ctx); // 작업 182
    editRenderDialogue(ctx); // 작업 186: 대사 타이밍 · 빈틈 다듬기 버튼
    editRenderPlayButton(ctx);
    editRenderMulti(ctx); // 작업 192: 여러 컷 고름 안내
    editFixRenderSel(ctx); // 지시서 116: [고른 클립 AI로 고치기] disabled·title 만(노드를 만들지 않는다)
    editTxRenderTools(ctx); // 작업 215b
  }

  /* ---- 타임라인 그리기 ---- */

  function editRenderTimeline(ctx) {
    var d = ctx.dom;
    if (!d.content) return;
    editTrClose(ctx); // 작업 182: 손잡이를 새로 그리므로 열린 전환 메뉴를 닫는다
    var info = editStarts(ctx.clips);
    var width = Math.ceil(info.total * ctx.pps) + EDIT_TAIL_PX;
    d.content.style.width = width + "px";
    editRenderRuler(ctx, width);
    clear(d.vtrack);
    clear(d.atrack);
    ctx.clipEls = [];
    ctx.clips.forEach(function (clip, index) {
      var parts = editClipBoxes(ctx, index);
      d.vtrack.appendChild(parts.box);
      d.atrack.appendChild(parts.abox);
      ctx.clipEls.push(parts);
    });
    if (!ctx.clips.length) d.vtrack.appendChild(el("p", "vnew-edit-track-empty", "클립이 없습니다"));
    editMixRender(ctx); // 작업 215a: 오디오 트랙 줄
    editRenderFx(ctx);
    editTxRender(ctx); // 작업 215b: 자막 · 타이틀 트랙 · 오른쪽 누르기 메뉴
    edit215cRenderMarks(ctx); // 작업 215c: 빈틈 · 마커 · 표시 구간
    editPlaceHead(ctx);
    editRenderTools(ctx);
    editQueueMedia(ctx);
  }

  function editRenderRuler(ctx, width) {
    var ruler = ctx.dom.ruler;
    clear(ruler);
    var step = editTickStep(ctx.pps);
    var unit = step.minor || step.major;
    var count = Math.floor(width / ctx.pps / unit + 1e-6);
    for (var k = 0; k <= count; k++) {
      var at = k * unit;
      var major = Math.abs(at / step.major - Math.round(at / step.major)) < 1e-6;
      var tick = el("span", "vnew-edit-tick" + (major ? " is-major" : ""));
      tick.style.left = Math.round(at * ctx.pps * 10) / 10 + "px";
      if (major) tick.appendChild(el("span", "vnew-edit-tick-label", editTimeText(at, false)));
      ruler.appendChild(tick);
    }
  }

  /* 클립 하나의 영상 상자(썸네일·이름·양 끝 손잡이)와 소리 상자. 너비 = (out − in) × px/초(경계 2px 틈은 CSS 테두리). */
  function editClipBoxes(ctx, index) {
    var box = el("div", "vnew-edit-clip");
    box.setAttribute("data-index", String(index));
    var thumbs = el("div", "vnew-edit-thumbs");
    var name = el("span", "vnew-edit-clip-label", "");
    var inEdge = el("div", "vnew-edit-edge is-in");
    var outEdge = el("div", "vnew-edit-edge is-out");
    inEdge.title = "끌어서 시작 줄이기·늘이기(원본 처음까지, Shift+[ ])";
    outEdge.title = "끌어서 끝 줄이기·늘이기(원본 끝까지, [ ])";
    box.appendChild(thumbs);
    box.appendChild(name);
    box.appendChild(inEdge);
    box.appendChild(outEdge);
    var abox = el("div", "vnew-edit-aclip");
    var parts = { index: index, box: box, thumbs: thumbs, name: name, inEdge: inEdge, outEdge: outEdge, abox: abox };
    editPaintClip(ctx, parts, ctx.clips[index]);
    box.addEventListener("pointerdown", function (event) {
      editClipPointer(ctx, parts, event);
    });
    inEdge.addEventListener("pointerdown", function (event) {
      editEdgePointer(ctx, parts, "in", event);
    });
    outEdge.addEventListener("pointerdown", function (event) {
      editEdgePointer(ctx, parts, "out", event);
    });
    abox.addEventListener("click", function () {
      if (editAlive(ctx) && !ctx.busy) {
        editSelect(ctx, parts.index);
        editSeekClip(ctx, parts.index); // 작업 190: 소리 상자 누르기도 그 컷 시작으로 재생헤드
      }
    });
    // 작업 188: 두 번 누르기 = 고르고 그 컷 처음부터 재생(끌기 · 끝 다듬기 뒤의 두 번 누르기는 editClipDblClick 이 거른다)
    box.addEventListener("dblclick", function (event) {
      editClipDblClick(ctx, parts, event);
    });
    abox.addEventListener("dblclick", function (event) {
      editClipDblClick(ctx, parts, event);
    });
    edit215cClipBoxes(ctx, parts); // 작업 215c: 연결 해제 소리 상자 끌기
    return parts;
  }

  /* 클립 상자 내용을 지금 값(clip)으로 다시 칠한다(끄는 동안·필름을 받은 뒤에도 같은 요소를 고친다). */
  function editPaintClip(ctx, parts, clip) {
    var mat = ctx.materials[clip.sourceKey] || {};
    var len = editClipLen(clip);
    var width = Math.max(1, len * ctx.pps);
    var invalid = ctx.invalidIds[clip.id];
    var selected = editIsSel(ctx, parts.index); // 작업 192: 여러 컷 고름도 같은 is-selected
    var speed = editSpeedOf(clip);
    parts.box.className =
      "vnew-edit-clip" + (selected ? " is-selected" : "") + (invalid ? " is-invalid" : "") + (width < 28 ? " is-narrow" : "") +
      (speed !== 1 ? " is-speed" : "");
    parts.box.style.width = width + "px";
    // 작업 182: 앞 클립이 이 클립으로 디졸브하면 그 초만큼 앞 클립 끝에 겹쳐 놓는다(타임라인 길이가 그만큼 짧다, editStarts).
    var prev = parts.index > 0 ? ctx.clips[parts.index - 1] : null;
    var overlap = prev ? editDissolveOf(prev) * ctx.pps : 0;
    var shift = overlap ? -Math.round(overlap * 10) / 10 + "px" : "";
    if (parts.box.style.marginLeft !== shift && (shift || parts.box.style.marginLeft)) parts.box.style.marginLeft = shift;
    if (parts.abox.style.marginLeft !== shift && (shift || parts.abox.style.marginLeft)) parts.abox.style.marginLeft = shift;
    var name = clip.label || mat.label || "클립";
    parts.box.title = name + " · " + len.toFixed(2) + "초" + (speed !== 1 ? " (" + speed + "× 속도)" : "") +
      (invalid ? " — 이 클립의 원본을 지금 쓸 수 없습니다(지우거나 바꿔 주세요)" : "");
    parts.name.textContent = name + " · " + len.toFixed(1) + "초" + (speed !== 1 ? " · " + speed + "×" : "");
    clear(parts.thumbs);
    var media = ctx.media[mat.mid] || {};
    var strip = media.status === "done" && media.data ? media.data.strip : null;
    var layout = strip && strip.url ? editStripCells(clip, strip, ctx.pps, EDIT_THUMB_H) : null;
    if (layout) {
      var image = 'url("' + editCssUrl(strip.url) + '")';
      layout.cells.forEach(function (cell) {
        var tile = el("span", "vnew-edit-thumb");
        tile.style.left = cell.left + "px";
        tile.style.width = cell.width + "px";
        tile.style.backgroundImage = image;
        tile.style.backgroundSize = layout.bgWidth + "px " + layout.bgHeight + "px";
        tile.style.backgroundPosition = cell.x + "px 0";
        parts.thumbs.appendChild(tile);
      });
    }
    var abox = parts.abox;
    clear(abox);
    abox.style.width = width + "px";
    var lane = editAudioLane(clip, mat, media, mat.audioFrom ? ctx.media[String(mat.audioFrom.mid || "")] : null);
    var kind = lane.kind;
    var path = lane.path;
    abox.className = "vnew-edit-aclip" + (kind ? " " + kind : "") + (selected ? " is-selected" : "");
    var slip = clip.muted === true ? 0 : editSlipOf(clip); // 작업 186
    abox.title = lane.title + (slip ? " · 대사 타이밍 " + editSlipText(slip) : "");
    edit215cPaintClip(ctx, parts, clip); // 작업 215c: 빈틈 자리 · 사용 안 함 · 역재생 · 라벨 색
    if (clip.muted === true) abox.appendChild(el("span", "vnew-edit-aclip-icon", "🔇"));
    if (lane.from && !path) abox.appendChild(el("span", "vnew-edit-aclip-icon", "🔊"));
    // 작업 186: 소리 구간(대사) 띠 — 파형 뒤 옅은 띠(대사 타이밍만큼 옮기고 클립 밖은 자른다). 띠 사이가 빈 소리 구간이다.
    (lane.bands || []).forEach(function (band) {
      var strip = el("span", "vnew-edit-sband");
      strip.setAttribute("aria-hidden", "true");
      strip.style.left = Math.round(band.from * ctx.pps * 10) / 10 + "px";
      strip.style.width = Math.max(1, Math.round((band.to - band.from) * ctx.pps * 10) / 10) + "px";
      abox.appendChild(strip);
    });
    if (path && typeof document.createElementNS === "function") {
      var svg = document.createElementNS(EDIT_SVG_NS, "svg");
      svg.setAttribute("class", "vnew-edit-wave");
      svg.setAttribute("viewBox", "0 0 " + path.n + " 100");
      svg.setAttribute("preserveAspectRatio", "none");
      svg.setAttribute("aria-hidden", "true");
      var line = document.createElementNS(EDIT_SVG_NS, "path");
      line.setAttribute("d", path.d);
      svg.appendChild(line);
      abox.appendChild(svg);
    }
  }

  /* ---- 작업 182: 전환 손잡이 · 디졸브 겹침 · J/L 막대 ---- */

  /* 경계마다 전환 손잡이(작은 마름모)를 영상 트랙에 둔다. 자리·겹침·J/L 막대는 editPlaceFx 가 맞춘다. */
  function editRenderFx(ctx) {
    var d = ctx.dom;
    ctx.trEls = [];
    if (!d.vtrack || !d.atrack) return;
    for (var i = 0; i + 1 < ctx.clips.length; i++) {
      ctx.trEls.push({ btn: editTrButton(ctx, i), wedge: null });
      d.vtrack.appendChild(ctx.trEls[i].btn);
    }
    editPlaceFx(ctx);
  }

  function editTrButton(ctx, index) {
    var btn = el("button", "vnew-edit-tr", "");
    btn.type = "button";
    btn.setAttribute("aria-haspopup", "menu");
    btn.setAttribute("aria-expanded", "false");
    btn.addEventListener("pointerdown", function (event) {
      // 빈 곳 누르기(고름 풀기)로 번지지 않게. 메뉴는 click 에서 연다(키보드 Enter·Space 도 같은 길).
      if (event && event.stopPropagation) event.stopPropagation();
    });
    btn.addEventListener("click", function () {
      if (ctx.trMenu && ctx.trMenu.index === index) editTrClose(ctx);
      else editTrOpen(ctx, index, btn);
    });
    return btn;
  }

  /* 전환 손잡이·디졸브 겹침(쐐기)·J/L 막대 자리와 글. 끝 끌기 중에도 부른다 — 값이 바뀌지 않으면 새 요소를 만들지 않는다. */
  function editPlaceFx(ctx) {
    var d = ctx.dom;
    if (!d.vtrack || !d.atrack) return;
    var clips = ctx.clips;
    var info = editStarts(clips);
    var pps = ctx.pps;
    function px(seconds) {
      return Math.round(seconds * pps * 10) / 10 + "px";
    }
    (ctx.trEls || []).forEach(function (item, i) {
      var a = clips[i];
      if (!a || !clips[i + 1]) return;
      var seconds = editDissolveOf(a);
      var at = info.starts[i + 1];
      var title = "클립 " + (i + 1) + " → " + (i + 2) + " 전환: " + (seconds ? "디졸브 " + seconds + "초" : "컷") + " — 눌러 바꾸기";
      item.btn.className = "vnew-edit-tr" + (seconds ? " is-dissolve" : "") + (ctx.trMenu && ctx.trMenu.index === i ? " is-open" : "");
      item.btn.style.left = px(at + seconds / 2);
      if (item.btn.title !== title) {
        item.btn.title = title;
        item.btn.setAttribute("aria-label", title);
      }
      if (seconds && !item.wedge) {
        item.wedge = el("span", "vnew-edit-xfade");
        item.wedge.setAttribute("aria-hidden", "true");
        d.vtrack.appendChild(item.wedge);
      }
      if (item.wedge) {
        item.wedge.style.display = seconds ? "" : "none";
        item.wedge.style.left = px(at);
        item.wedge.style.width = px(seconds);
      }
    });
    (ctx.clipEls || []).forEach(function (parts) {
      var i = parts.index;
      var clip = clips[i];
      if (!clip) return;
      var on = clip.muted !== true; // 소리 끈 클립의 앞당김·이어 감은 내보내기도 무시한다(값은 남는다)
      var lead = on && i > 0 ? editShiftOf(clip, "audioLead") : 0;
      var tail = on && i < clips.length - 1 ? editShiftOf(clip, "audioTail") : 0;
      parts.leadBar = editShiftBar(ctx, parts.leadBar, "is-lead", lead, info.starts[i] - lead, px,
        "소리 먼저 시작 " + lead.toFixed(2) + "초 (J컷)");
      parts.tailBar = editShiftBar(ctx, parts.tailBar, "is-tail", tail, info.starts[i] + editClipLen(clip), px,
        "소리 이어 가기 " + tail.toFixed(2) + "초 (L컷)");
    });
    if (d.fxNote) d.fxNote.style.display = editHasFx(clips) ? "" : "none";
    editTxPlaceFx(ctx); // 작업 215b: 블랙/화이트 전환 손잡이
  }

  /* 소리 줄의 앞당김(왼쪽, 앞 클립 소리 아래)·이어 감(오른쪽, 뒤 클립 소리 아래) 막대. 0 초면 숨긴다(없으면 만들지 않는다). */
  function editShiftBar(ctx, bar, kind, seconds, from, px, title) {
    if (!seconds) {
      if (bar) bar.style.display = "none";
      return bar || null;
    }
    if (!bar) {
      bar = el("span", "vnew-edit-ashift " + kind);
      ctx.dom.atrack.appendChild(bar);
    }
    bar.style.display = "";
    bar.style.left = px(from);
    bar.style.width = px(seconds);
    bar.title = title;
    return bar;
  }

  function editInside(node, target) {
    for (var n = target; n; n = n.parentNode) {
      if (n === node) return true;
    }
    return false;
  }

  /* 전환 메뉴: 컷(기본) · 디졸브 0.25·0.5·0.75·1초. 두 클립 중 짧은 쪽의 45% 보다 긴 값은 막고 "클립이 짧아 쓸 수 없습니다".
     화면에 고정(position: fixed)해 타임라인 스크롤 상자에 잘리지 않게 하고, 손잡이 아래(넘치면 위)·화면 안으로 맞춘다. */
  function editTrOpen(ctx, index, anchor) {
    var a = ctx.clips[index];
    var b = ctx.clips[index + 1];
    if (!editAlive(ctx) || ctx.busy || !a || !b) return;
    editTrClose(ctx);
    var current = editDissolveOf(a);
    var name = "클립 " + (index + 1) + " → " + (index + 2) + " 전환";
    var menu = el("div", "vnew-edit-trmenu");
    menu.setAttribute("role", "menu");
    menu.setAttribute("aria-label", name);
    menu.appendChild(el("p", "vnew-edit-trmenu-title", name));
    var focus = null;
    [0].concat(EDIT_DISSOLVES).forEach(function (seconds) {
      var fits = !seconds || editDissolveFits(seconds, a, b);
      var on = seconds === current;
      var text = seconds ? "디졸브 " + seconds + "초" : "컷";
      // 막힌 버튼은 마우스 이벤트를 받지 않는 브라우저가 있어 안내(title)는 감싼 줄에도 둔다.
      var row = el("span", "vnew-edit-trmenu-row");
      var item = el("button", "vnew-edit-trmenu-item" + (on ? " is-on" : ""), text);
      item.type = "button";
      item.setAttribute("role", "menuitemradio");
      item.setAttribute("aria-checked", on ? "true" : "false");
      if (fits) {
        item.title = seconds ? "두 클립을 " + seconds + "초 겹쳐 바꿉니다 — 전체 길이가 " + seconds + "초 줄어듭니다" : "겹침 없이 바로 바꿉니다";
      } else {
        item.disabled = true;
        item.title = EDIT_TR_SHORT;
        row.title = EDIT_TR_SHORT;
        item.setAttribute("aria-label", text + " — " + EDIT_TR_SHORT);
      }
      item.addEventListener("click", function () {
        if (fits) editSetTransition(ctx, index, seconds);
      });
      if (on) focus = item;
      row.appendChild(item);
      menu.appendChild(row);
    });
    editTxTrRows(ctx, index, menu); // 작업 215b: 블랙/화이트로 사라졌다 나타나기
    ctx.root.appendChild(menu);
    var rect = anchor && anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : null;
    var vw = Number(window.innerWidth) || 0;
    var vh = Number(window.innerHeight) || 0;
    if (rect && vw > 0) {
      var mw = Number(menu.offsetWidth) || 180;
      var mh = Number(menu.offsetHeight) || 220;
      var left = Math.max(8, Math.min(vw - mw - 8, (Number(rect.left) + Number(rect.right)) / 2 - mw / 2));
      var top = Number(rect.bottom) + 6;
      if (vh > 0 && top + mh > vh - 8) top = Math.max(8, Number(rect.top) - 6 - mh);
      menu.style.left = Math.round(left) + "px";
      menu.style.top = Math.round(top) + "px";
    }
    var onDoc = function (event) {
      var target = event && event.target;
      if (editInside(menu, target) || editInside(anchor, target)) return;
      editTrClose(ctx);
    };
    var onMove = function (event) {
      if (event && editInside(menu, event.target)) return;
      editTrClose(ctx);
    };
    document.addEventListener("pointerdown", onDoc, true);
    if (typeof window.addEventListener === "function") {
      window.addEventListener("scroll", onMove, true); // 손잡이가 움직이면(페이지·타임라인 스크롤) 메뉴를 닫는다
      window.addEventListener("resize", onMove);
    }
    ctx.trMenu = { el: menu, index: index, anchor: anchor, onDoc: onDoc, onMove: onMove };
    if (anchor) {
      anchor.classList.add("is-open");
      anchor.setAttribute("aria-expanded", "true");
    }
    var first = focus || editTrFirst(menu);
    if (first && typeof first.focus === "function") {
      try {
        first.focus({ preventScroll: true });
      } catch (error) {
        /* 초점을 줄 수 없는 요소 */
      }
    }
  }

  function editTrFirst(menu) {
    var rows = menu.children || [];
    for (var i = 0; i < rows.length; i++) {
      var item = (rows[i].children || [])[0];
      if (item && !item.disabled) return item;
    }
    return null;
  }

  function editTrClose(ctx, refocus) {
    var menu = ctx && ctx.trMenu;
    if (!menu) return;
    ctx.trMenu = null;
    document.removeEventListener("pointerdown", menu.onDoc, true);
    if (typeof window.removeEventListener === "function") {
      window.removeEventListener("scroll", menu.onMove, true);
      window.removeEventListener("resize", menu.onMove);
    }
    if (menu.el.parentNode) menu.el.parentNode.removeChild(menu.el);
    if (menu.anchor) {
      menu.anchor.classList.remove("is-open");
      menu.anchor.setAttribute("aria-expanded", "false");
      if (refocus && typeof menu.anchor.focus === "function") {
        try {
          menu.anchor.focus();
        } catch (error) {
          /* 이미 떨어진 손잡이 */
        }
      }
    }
  }

  /* index 번째 클립 → 다음 클립 전환을 seconds(0 = 컷)로. 실행 취소 한 단계, 기본값(컷)이면 transition 키를 지운다. */
  function editSetTransition(ctx, index, seconds) {
    editTrClose(ctx);
    if (!editAlive(ctx) || ctx.busy) return;
    var a = ctx.clips[index];
    var b = ctx.clips[index + 1];
    if (!seconds && a && b && editTxDipOf(a).type) return editTxSetDip(ctx, index, "", 0); // 작업 215b: 블랙/화이트 → 컷
    if (!a || !b || editDissolveOf(a) === seconds) return;
    if (seconds && !editDissolveFits(seconds, a, b)) {
      editNote(ctx, EDIT_TR_SHORT + ".");
      return;
    }
    var clip = editCloneClip(a);
    if (seconds) clip.transition = { type: "dissolve", seconds: seconds };
    else delete clip.transition;
    var next = ctx.clips.slice();
    next[index] = clip;
    editCommit(ctx, next, {});
  }

  function editPlaceHead(ctx) {
    var d = ctx.dom;
    var total = editStarts(ctx.clips).total;
    if (d.head) d.head.style.left = Math.round(ctx.t * ctx.pps * 10) / 10 + "px";
    if (d.time) d.time.textContent = editTimeText(ctx.t) + " / " + editTimeText(total);
    editTxPreview(ctx); // 작업 215b: 자막 · 타이틀 · 색 · 전환 미리보기
  }

  function editSelect(ctx, index) {
    ctx.sel = index >= 0 && index < ctx.clips.length ? index : -1;
    ctx.multi = ctx.sel >= 0 ? [ctx.sel] : []; // 작업 192: 그냥 고르기 = 그 클립 하나(Shift 범위의 기준도 이 클립)
    ctx.anchor = ctx.sel;
    if (ctx.cutPlay && ctx.cutPlay.index !== ctx.sel) ctx.cutPlay = null; // 작업 188: 다른 클립을 고르면 이 컷 재생 표시 · 멈출 자리를 지운다
    if (ctx.mixSel) {
      ctx.mixSel = null; // 작업 215a: 클립(또는 빈 곳)을 고르면 고른 소리 아이템은 풀린다
      ctx.mixKey = null;
      editMixRender(ctx);
    }
    editSelChanged(ctx);
  }

  /* 작업 192: 고른 클립이 바뀐 뒤 — 클립 상자 표시 · 도구줄 · 재료 메뉴(작업 149) · 열린 AI로 고치기 패널(고른 컷에서 열었으면). */
  function editSelChanged(ctx) {
    ctx.clipEls.forEach(function (parts) {
      var method = editIsSel(ctx, parts.index) ? "add" : "remove";
      parts.box.classList[method]("is-selected");
      parts.abox.classList[method]("is-selected");
    });
    editRenderTools(ctx);
    if (editBinFollow(ctx)) editRenderBin(ctx); // 작업 149: 고른 클립의 테이크·컷을 재료 메뉴에
    editFixSelFollow(ctx);
  }

  /* 작업 192: 고른 클립 번호(오름차순, 중복 없음). ctx.multi 가 둘 이상이고 ctx.sel 을 품으면 그 목록, 아니면 [ctx.sel](없으면 []).
     ctx.sel 만 바꾸는 옛 길(editCommit sel · 검사)도 그대로 한 클립이다. */
  function editSelIndexes(ctx) {
    var n = (ctx.clips || []).length;
    var sel = ctx.sel;
    if (!(sel >= 0 && sel < n)) return [];
    var seen = {};
    var list = (Array.isArray(ctx.multi) ? ctx.multi : []).filter(function (i) {
      var ok = typeof i === "number" && i >= 0 && i < n && !seen[i];
      seen[i] = true;
      return ok;
    }).sort(function (a, b) {
      return a - b;
    });
    return list.length > 1 && list.indexOf(sel) >= 0 ? list : [sel];
  }

  function editMultiOn(ctx) {
    return editSelIndexes(ctx).length > 1;
  }

  function editIsSel(ctx, index) {
    return editSelIndexes(ctx).indexOf(index) >= 0;
  }

  /* 작업 192: 한 클립만 고른 때의 그 클립(여러 컷이면 null). 속도·길이·J/L·대사 타이밍처럼 한 클립에만 맞는 칸이 쓴다. */
  function editSingle(ctx) {
    return editMultiOn(ctx) ? null : editSelected(ctx);
  }

  /* 작업 192: Ctrl/Cmd+누르기 = 그 클립을 넣고 빼기, Shift+누르기 = 기준 클립부터 그 클립까지(기준은 그대로). 첫 번호가 ctx.sel. */
  function editSelectMore(ctx, index, range) {
    var n = ctx.clips.length;
    if (!(index >= 0 && index < n)) return;
    var list = editSelIndexes(ctx);
    if (range) {
      var anchor = ctx.anchor >= 0 && ctx.anchor < n ? ctx.anchor : list.length ? list[0] : index;
      list = [];
      for (var i = Math.min(anchor, index); i <= Math.max(anchor, index); i++) list.push(i);
      ctx.anchor = anchor;
    } else {
      var at = list.indexOf(index);
      if (at >= 0) list.splice(at, 1);
      else list.push(index);
      list.sort(function (a, b) {
        return a - b;
      });
      ctx.anchor = index;
    }
    ctx.multi = list;
    ctx.sel = list.length ? list[0] : -1;
    if (ctx.cutPlay && ctx.cutPlay.index !== ctx.sel) ctx.cutPlay = null; // 작업 188
    editSelChanged(ctx);
  }

  /* 작업 192: 누르기에 Ctrl/Cmd · Shift 가 있으면 여러 컷 고르기(editSelectMore)를 하고 true. 없으면 아무것도 하지 않고 false. */
  function editSelExtend(ctx, index, event) {
    var e = event || {};
    var mod = !!(e.ctrlKey || e.metaKey);
    if (!mod && !e.shiftKey) return false;
    editSelectMore(ctx, index, !!e.shiftKey && !mod);
    return true;
  }

  /* 작업 192: 도구줄의 여러 컷 고름 안내와 [고른 컷 AI로 고치기](AI로 고치기 켬일 때만 보인다 — editFixRenderSel). */
  function editMultiBox(ctx) {
    var box = el("div", "vnew-edit-selfix");
    var note = el("span", "vnew-hint vnew-edit-multi-note", EDIT_MULTI_NOTE);
    note.style.display = "none";
    box.appendChild(note);
    var btn = el("button", "outline-button vnew-edit-fix-tool", "고른 컷 AI로 고치기");
    btn.type = "button";
    btn.setAttribute("aria-haspopup", "dialog");
    btn.addEventListener("click", function () {
      editFixOpenSel(ctx);
    });
    box.appendChild(btn);
    ctx.dom.multiNote = note;
    ctx.dom.fixSel = btn;
    return box;
  }

  function editRenderMulti(ctx) {
    var note = ctx.dom.multiNote;
    if (note) note.style.display = editMultiOn(ctx) ? "" : "none";
  }

  function editShowDrop(ctx, index) {
    var drop = ctx.dom.drop;
    if (!drop) return;
    if (index < 0) {
      drop.style.display = "none";
      return;
    }
    var info = editStarts(ctx.clips);
    var at = index < info.starts.length ? info.starts[index] : info.total;
    drop.style.left = Math.round(at * ctx.pps * 10) / 10 + "px";
    drop.style.display = "";
  }

  /* 화면 x(포인터) → 타임라인 안 x(px). 내용 상자의 화면 위치가 스크롤을 반영한다. */
  function editContentX(ctx, event) {
    var content = ctx.dom.content;
    var rect = content && content.getBoundingClientRect ? content.getBoundingClientRect() : { left: 0 };
    return (Number(event.clientX) || 0) - (Number(rect.left) || 0);
  }

  /* 끄는 동안 포인터가 스크롤 영역 양 끝 32px 안이면 그쪽으로 조금씩 스크롤한다. */
  function editAutoScroll(ctx, event) {
    var sc = ctx.dom.scroller;
    if (!sc || !sc.getBoundingClientRect) return;
    var rect = sc.getBoundingClientRect();
    var x = Number(event.clientX) || 0;
    if (x < rect.left + 32) sc.scrollLeft = Math.max(0, (sc.scrollLeft || 0) - 16);
    else if (x > rect.right - 32) sc.scrollLeft = (sc.scrollLeft || 0) + 16;
  }

  function editFollowHead(ctx) {
    var sc = ctx.dom.scroller;
    if (!sc || !sc.clientWidth) return;
    var x = ctx.t * ctx.pps;
    var left = sc.scrollLeft || 0;
    if (x < left || x > left + sc.clientWidth - 24) sc.scrollLeft = Math.max(0, x - sc.clientWidth * 0.2);
  }

  function editOnScroll(ctx) {
    if (!editAlive(ctx) || ctx.scrollQueued) return;
    ctx.scrollQueued = true;
    editRaf(function () {
      ctx.scrollQueued = false;
      if (editAlive(ctx)) editQueueMedia(ctx);
    });
  }

  /* ---- 끌기(포인터 이벤트: 마우스·터치 같은 길) ---- */

  function editPointerOk(ctx, event) {
    if (!editAlive(ctx) || ctx.busy || ctx.drag) return false;
    return !(event.pointerType === "mouse" && event.button !== undefined && event.button !== 0);
  }

  /* 누른 요소에 포인터를 붙잡고(setPointerCapture) 움직임을 타임라인 x 차이(px, 자동 스크롤 포함)로 알린다.
     threshold px 넘게 움직여야 끌기(moved). 끝나면 end(dx, moved, cancelled). */
  function editTrack(ctx, target, event, handlers) {
    var start = editContentX(ctx, event);
    var pointerId = event.pointerId;
    var drag = { dx: 0, moved: false, cleanup: null };
    try {
      if (target.setPointerCapture && pointerId !== undefined) target.setPointerCapture(pointerId);
    } catch (error) {
      /* 붙잡기를 못 해도 같은 요소 위에서는 된다 */
    }
    function move(ev) {
      if (ev.preventDefault) ev.preventDefault();
      editAutoScroll(ctx, ev);
      drag.dx = editContentX(ctx, ev) - start;
      if (!drag.moved && Math.abs(drag.dx) >= (handlers.threshold || 0)) drag.moved = true;
      if (drag.moved && handlers.move) handlers.move(drag.dx, ev);
    }
    function finish(cancelled) {
      cleanup();
      if (drag.moved) ctx.dragEndAt = Date.now(); // 작업 188: 끌기 · 끝 다듬기를 끝낸 두 번 누르기는 재생하지 않는다(editClipDblClick)
      if (handlers.end) handlers.end(drag.dx, drag.moved && !cancelled, cancelled);
    }
    function up() {
      finish(false);
    }
    function cancel() {
      finish(true);
    }
    function cleanup() {
      target.removeEventListener("pointermove", move);
      target.removeEventListener("pointerup", up);
      target.removeEventListener("pointercancel", cancel);
      try {
        if (target.releasePointerCapture && pointerId !== undefined) target.releasePointerCapture(pointerId);
      } catch (error) {
        /* 이미 놓였다 */
      }
      if (ctx.drag === drag) ctx.drag = null;
    }
    drag.cleanup = cleanup;
    target.addEventListener("pointermove", move);
    target.addEventListener("pointerup", up);
    target.addEventListener("pointercancel", cancel);
    ctx.drag = drag;
    return drag;
  }

  /* 클립 몸통: 누르면 고르기, 끌면 순서 바꾸기(놓을 자리 세로선).
     작업 190: 움직이지 않고 떼면(끌기 문턱 안 · 취소 아님) 그 컷 시작으로 재생헤드(editSeekClip). 끌기 · 취소는 옮기지 않는다. */
  function editClipPointer(ctx, parts, event) {
    if (edit215cClipPointer(ctx, parts, event)) return; // 작업 215c: 자르기 · 밀어넣기 · 밀기 도구 · 트랙 잠금
    if (!editPointerOk(ctx, event)) return;
    if (event.stopPropagation) event.stopPropagation();
    if (event.preventDefault) event.preventDefault();
    // 작업 192: Ctrl/Cmd · Shift+누르기는 여러 컷 고르기(끌어 옮기지 않는다). 떼면 고른 첫 컷 시작으로(작업 190 과 같은 길).
    var extra = editSelExtend(ctx, parts.index, event);
    var from = extra ? ctx.sel : parts.index;
    var drop = -1;
    if (!extra) editSelect(ctx, from);
    editTrack(ctx, parts.box, event, {
      threshold: EDIT_DRAG_PX,
      move: function (dx, ev) {
        if (extra) return;
        if (ctx.playing) editPause(ctx);
        parts.box.classList.add("is-dragging");
        drop = editDropIndex(ctx.clips, editContentX(ctx, ev), ctx.pps);
        editShowDrop(ctx, drop);
      },
      end: function (dx, moved, cancelled) {
        editShowDrop(ctx, -1);
        parts.box.classList.remove("is-dragging");
        if (!moved && !cancelled) editSeekClip(ctx, from);
        if (!moved || drop < 0 || drop === from || drop === from + 1) return;
        editCommit(ctx, editMove(ctx.clips, from, drop), { sel: drop > from ? drop - 1 : drop });
      }
    });
  }

  /* 작업 190: 클립 한 번 누르기 — 그 컷의 타임라인 시작(editStarts(ctx.clips).starts[index])으로 재생헤드를 옮긴다.
     멈춰 있으면 멈춘 채(재생을 시작하지 않는다), 재생 중이면 그 자리부터 계속 재생한다(editSeek 이 이어 재생). */
  function editSeekClip(ctx, index) {
    if (!editAlive(ctx) || ctx.busy || index < 0 || index >= ctx.clips.length) return;
    editSeek(ctx, editStarts(ctx.clips).starts[index]);
  }

  /* 클립 끝 손잡이: in/out 조정(원본 안·0.04초 이상·원본 fps 프레임). 끄는 동안 미리보기가 그 끝 프레임을 보인다.
     줄이기와 함께 원본 처음(0)·끝(재료 길이)까지 늘이기도 된다. 속도가 있으면 원본 변화 = 끈 거리 ÷ px/초 × 속도이고,
     재료 길이를 모르면 끝은 지금 out 보다 늘리지 않는다(지시서 115). */
  function editEdgePointer(ctx, parts, edge, event) {
    if (edit215cEdgePointer(ctx, parts, edge, event)) return; // 작업 215c: 롤링 편집 · 트랙 잠금
    if (!editPointerOk(ctx, event)) return;
    if (event.stopPropagation) event.stopPropagation();
    if (event.preventDefault) event.preventDefault();
    var index = parts.index;
    var orig = editCloneClip(ctx.clips[index]);
    var before = editSnapshotOf(ctx.variant, ctx.clips);
    var mat = ctx.materials[orig.sourceKey] || {};
    var fps = editFpsOf(ctx, orig);
    var speed = editSpeedOf(orig);
    var dur = Number(mat.duration) > 0 ? Number(mat.duration) : edge === "out" ? orig.out : 0;
    var minSource = editMinSource(editMinLen(ctx), speed);
    editPause(ctx);
    editSelect(ctx, index);
    editTrack(ctx, edge === "in" ? parts.inEdge : parts.outEdge, event, {
      threshold: 1,
      move: function (dx) {
        var base = edge === "in" ? orig.in : orig.out;
        var next = editTrimClip(orig, edge, base + (dx * speed) / ctx.pps, dur, fps, minSource);
        ctx.clips[index] = next;
        editPaintClip(ctx, parts, next);
        ctx.dom.content.style.width = Math.ceil(editStarts(ctx.clips).total * ctx.pps) + EDIT_TAIL_PX + "px";
        editPlaceFx(ctx); // 작업 182: 전환 손잡이·겹침·J/L 막대 자리(새 요소는 만들지 않는다)
        editTrimPreview(ctx, next, edge, fps);
      },
      end: function (dx, moved, cancelled) {
        var cur = ctx.clips[index];
        if (cancelled || (cur.in === orig.in && cur.out === orig.out)) {
          ctx.clips[index] = orig;
          editPaintClip(ctx, parts, orig);
          editPlaceFx(ctx); // 작업 182
          editSeek(ctx, ctx.t);
          return;
        }
        editCommit(ctx, ctx.clips.slice(), { before: before, sel: index });
      }
    });
  }

  function editTrimPreview(ctx, clip, edge, fps) {
    var video = ctx.vids[ctx.cur];
    if (!video) return;
    editSetSrc(video, editUrlOf(ctx, clip.sourceKey));
    editSetTime(video, edge === "in" ? clip.in : Math.max(clip.in, clip.out - 1 / fps));
  }

  /* 눈금자 누르기·끌기: 그 시각으로 재생헤드(재생 중이면 계속 재생). */
  function editRulerPointer(ctx, event) {
    if (!editPointerOk(ctx, event)) return;
    if (event.stopPropagation) event.stopPropagation();
    if (event.preventDefault) event.preventDefault();
    var start = editContentX(ctx, event);
    editSeek(ctx, start / ctx.pps);
    editTrack(ctx, ctx.dom.ruler, event, {
      threshold: 0,
      move: function (dx) {
        editSeek(ctx, (start + dx) / ctx.pps);
      }
    });
  }

  /* 재생헤드 손잡이(가위): 끌면 옮기고, 움직이지 않고 떼면 그 자리에서 자르기. */
  function editHandlePointer(ctx, event) {
    if (!editPointerOk(ctx, event)) return;
    if (event.stopPropagation) event.stopPropagation();
    if (event.preventDefault) event.preventDefault();
    var startT = ctx.t;
    editTrack(ctx, ctx.dom.handle, event, {
      threshold: 4,
      move: function (dx) {
        editSeek(ctx, startT + dx / ctx.pps);
      },
      end: function (dx, moved, cancelled) {
        if (!moved && !cancelled) editDo(ctx, "split");
      }
    });
  }

  /* ---- 미리보기 재생(<video> 2개 번갈아, design §5.3 재생) ---- */

  function editSetSrc(video, url) {
    if (video._src === url) return;
    video._src = url;
    video.src = url;
  }

  function editSetTime(video, seconds) {
    try {
      video.currentTime = Math.max(0, Number(seconds) || 0);
    } catch (error) {
      /* 아직 불러오기 전 — 불러온 뒤 그 자리로 간다 */
    }
  }

  /* 클립 속도를 미리보기 <video> 에(지시서 115). 소스를 바꾸면 브라우저가 playbackRate 를 defaultPlaybackRate 로 되돌리므로
     둘 다 둔다. preservesPitch(브라우저 기본 true)로 소리 높이는 그대로다(내보내기 atempo 와 같다). */
  function editSetRate(video, clip) {
    var speed = editSpeedOf(clip);
    speed *= edit215cRate; // 작업 215c: J/K/L 셔틀 배속(평소 1)
    try {
      if (video.defaultPlaybackRate !== speed) video.defaultPlaybackRate = speed;
      if (video.playbackRate !== speed) video.playbackRate = speed;
      video.preservesPitch = true;
    } catch (error) {
      /* 이 속도를 못 내는 브라우저 — 1× 로 보인다(내보내기는 서버가 맞춘다) */
    }
  }

  function editPlayVideo(video) {
    try {
      var promise = video.play();
      if (promise && typeof promise.catch === "function") {
        promise.catch(function () {
          /* 자동 재생 거절·바꿔 끼우기로 끊김 — 다음 조작에서 다시 */
        });
      }
    } catch (error) {
      /* 재생할 수 없는 요소 */
    }
  }

  /* ---- 미리보기 원본 소리(지시서 118b) ----
     재료 audioFrom {sourceKey, mid, offset, scale, end}(서버 edit._audio_sources)이 있는 클립(소리 없는 다시 만든 버전)은
     숨은 소리 요소(ctx.audio)로 그 칸 v1 소리를 낸다. 시각은 내보내기(edit._clip_audio + stitcher atrim·atempo)와 같다:
     소리 시작 = max(0, offset + in × scale), 그 뒤로는 원본과 같은 빠르기로 흐른다 → 소리 시각 = 시작 + (원본 시각 − in).
     end 를 넘으면(내보내기는 그 뒤 무음) null. 소리 끈 클립·audioFrom 없음도 null. */
  function editAltAudioAt(from, clip, source) {
    if (!from || typeof from !== "object" || !clip || clip.muted === true) return null;
    var off = Number(from.offset) || 0;
    var sc = Number(from.scale) > 0 ? Number(from.scale) : 1;
    var cin = Number(clip.in) || 0;
    var at = Math.max(0, off + cin * sc) + Math.max(0, (Number(source) || 0) - cin);
    var end = Number(from.end);
    if (end > 0 && at >= end) return null;
    return Math.round(at * 1000) / 1000;
  }

  /* 작업 186: 대사 타이밍(audioSlip) 미리보기 — 화면 원본 시각 source 에서 들릴 소리 시각. 내보내기(edit)처럼 화면 t 에서 원본
     t − 대사 타이밍 × 속도를 듣는다. from(원본 소리 대체)이 있으면 그 소리 시각으로 옮긴다(editAltAudioAt). 소리 끈 클립이거나
     원본 밖(0 앞 · duration 끝 뒤)이면 null(무음). */
  function editSlipAudioAt(from, clip, source, duration) {
    if (!clip || clip.muted === true) return null;
    var cin = Number(clip.in) || 0;
    var heard = (Number(source) || 0) - editSlipOf(clip) * editSpeedOf(clip);
    var at = heard;
    if (from) {
      at = editAltAudioAt(from, clip, Math.max(cin, heard));
      if (at === null) return null;
      if (heard < cin) at -= cin - heard;
    }
    var end = Number(duration) || 0;
    if (at < 0 || (end > 0 && at >= end - 0.02)) return null;
    return Math.round(at * 1000) / 1000;
  }
  /* ---- 미리보기 원본 소리 끝 ---- */

  var EDIT_AUDIO_DRIFT_S = 0.15; // 소리 요소가 이만큼 넘게 어긋나면 다시 맞춘다(editTick)

  function editAltFrom(ctx, clip) {
    var from = clip ? (ctx.materials[clip.sourceKey] || {}).audioFrom : null;
    return from && typeof from === "object" && from.sourceKey ? from : null;
  }

  function editAudioStop(ctx) {
    var audio = ctx.audio;
    if (!audio) return;
    try {
      if (!audio.paused) audio.pause();
    } catch (error) {
      /* 무시 */
    }
  }

  /* 지금 클립의 원본 소리를 ctx.audio 에 맞춘다(source = 화면 <video> 의 원본 시각). force 면 시각을 꼭 다시 둔다.
     원본 소리를 내면 true(부르는 쪽이 화면 <video> 를 음소거), 아니면 소리 요소를 멈추고 false(지금 동작 그대로).
     작업 186: 대사 타이밍(audioSlip)이 있는 소리 켠 클립도 이 소리 요소가 그 원본(원본 소리 대체면 그 소리)을 옮긴 시각으로 낸다
     (editSlipAudioAt). 어긋남 허용은 EDIT_AUDIO_DRIFT_S 라 근사다(정확한 결과는 내보낸 영상, EDIT_FX_NOTE). */
  function editSyncAudio(ctx, clip, source, force) {
    var audio = ctx.audio;
    var from = editAltFrom(ctx, clip);
    var slip = clip && clip.muted !== true ? editSlipOf(clip) : 0;
    var key = from ? from.sourceKey : slip ? clip.sourceKey : "";
    var url = key ? editUrlOf(ctx, key) : "";
    var at = null;
    if (audio && url) at = slip ? editSlipAudioAt(from, clip, source, (ctx.materials[key] || {}).duration) : editAltAudioAt(from, clip, source);
    if (at === null) {
      editAudioStop(ctx);
      return !!(audio && url && clip && clip.muted !== true); // end 넘음: 화면은 계속 음소거(내보내기도 무음)
    }
    editSetSrc(audio, url);
    audio.muted = false;
    editSetRate(audio, clip);
    if (force || Math.abs((Number(audio.currentTime) || 0) - at) > EDIT_AUDIO_DRIFT_S) editSetTime(audio, at);
    if (ctx.playing) {
      if (audio.paused) editPlayVideo(audio);
    } else {
      editAudioStop(ctx);
    }
    return true;
  }

  function editContiguous(ctx, a, b) {
    if (editFxEdge(a, b)) return false; // 작업 182: 디졸브·J/L 컷 경계는 <video> 2개로 겹쳐 보인다(editFx*)
    return a.sourceKey === b.sourceKey && Math.abs(Number(b.in) - Number(a.out)) < 0.5 / editFpsOf(ctx, a);
  }

  /* ---- 작업 182: 미리보기 근사(디졸브·J/L 컷) ----
     <video> 2개 모델 그대로: 바꿔 끼운 뒤 앞 클립(이제 숨은 쪽)을 디졸브 + 이어 감 동안 더 재생하고(fxHold), 다음 클립(숨은 쪽)은
     앞당김만큼 먼저 소리를 낸다(fxLead). 디졸브는 들어오는 <video> 의 CSS opacity 를 타임라인 시각으로 0 → 1, 소리 크기는 내보내기
     (edit._audio_envelope)처럼 직선으로 바꾼다. 숨은 쪽 하나가 둘 다 맡을 수는 없어 겹치면 앞의 것이 끝난 뒤 시작한다. 재생을 멈추거나
     옮기면 모두 끝낸다(겹친 구간에 멈추면 뒤 클립만 보인다). 원본 소리 대체(지시서 118b) 클립의 앞당김·이어 감은 미리보기에 없다.
     작업 186: 대사 타이밍(audioSlip)은 숨은 소리 요소(ctx.audio, editSyncAudio)가 옮긴 시각으로 낸다. 그 클립의 앞당김·이어 감도 없다.
     정확한 결과는 내보낸 영상(EDIT_FX_NOTE). */

  function editFxVolume(video, value) {
    try {
      video.volume = Math.max(0, Math.min(1, Number(value) || 0));
    } catch (error) {
      /* 소리 크기를 바꿀 수 없는 브라우저(iOS) — 그대로 들린다 */
    }
  }

  /* 앞 클립 붙잡기를 끝낸다: 숨은 <video> 를 멈추고 덮었던 투명도·순서·소리 크기를 되돌린다. */
  function editFxHoldEnd(ctx) {
    var hold = ctx.fxHold;
    if (!hold) return;
    ctx.fxHold = null;
    try {
      hold.video.pause();
    } catch (error) {
      /* 무시 */
    }
    hold.video.style.opacity = "";
    editFxVolume(hold.video, 1);
    if (hold.incoming) {
      hold.incoming.style.opacity = "";
      hold.incoming.style.zIndex = "";
      editFxVolume(hold.incoming, 1);
    }
  }

  function editFxLeadEnd(ctx, keepPlaying) {
    var lead = ctx.fxLead;
    if (!lead) return;
    ctx.fxLead = null;
    if (!keepPlaying) {
      try {
        lead.video.pause();
      } catch (error) {
        /* 무시 */
      }
    }
    editFxVolume(lead.video, 1);
  }

  /* 재생을 멈추거나 옮길 때: 겹쳐 재생하던 것을 모두 끝낸다(없으면 아무것도 하지 않는다). */
  function editFxStop(ctx) {
    editFxHoldEnd(ctx);
    editFxLeadEnd(ctx, false);
  }

  /* 바꿔 끼울 때(editAdvance) 앞 클립 prev 를 더 재생할지: 디졸브(그 초 동안 화면 아래에 남는다) + 이어 감(소리만). */
  function editFxHoldStart(ctx, video, incoming, prev, at, leadOn) {
    var dissolve = editDissolveOf(prev);
    var tail = prev.muted === true ? 0 : editShiftOf(prev, "audioTail");
    if (!dissolve && !tail) return false;
    ctx.fxHold = { video: video, incoming: incoming, from: at, fade: dissolve, end: at + dissolve + tail, tail: tail, lead: !!leadOn };
    if (dissolve) {
      video.style.opacity = "1"; // 클래스(is-active)를 떼도 디졸브 동안 보인다
      incoming.style.zIndex = "1";
      incoming.style.opacity = "0";
      editFxVolume(incoming, leadOn ? EDIT_OFFSCREEN_GAIN : 0);
    }
    editFxVolume(video, dissolve ? 1 : EDIT_OFFSCREEN_GAIN);
    return true;
  }

  /* 재생 중 한 화면마다: 붙잡은 앞 클립의 투명도·소리 크기와 끝, 다음 클립 앞당김 시작·소리 크기. */
  function editFxTick(ctx, info) {
    var hold = ctx.fxHold;
    if (hold) {
      var t = ctx.t;
      if (t >= hold.end - 1e-6 || hold.video.ended) {
        editFxHoldEnd(ctx);
        editPreloadNext(ctx); // 붙잡는 동안 미뤄 둔 다음 클립 불러 두기
      } else if (hold.fade && t < hold.from + hold.fade) {
        var p = Math.max(0, Math.min(1, (t - hold.from) / hold.fade));
        hold.incoming.style.opacity = String(Math.round(p * 1000) / 1000);
        editFxVolume(hold.video, 1 - p * (1 - (hold.tail ? EDIT_OFFSCREEN_GAIN : 0)));
        editFxVolume(hold.incoming, (hold.lead ? EDIT_OFFSCREEN_GAIN : 0) + p * (1 - (hold.lead ? EDIT_OFFSCREEN_GAIN : 0)));
      } else {
        if (hold.fade && hold.incoming.style.opacity !== "") {
          hold.video.style.opacity = "";
          hold.incoming.style.opacity = "";
          hold.incoming.style.zIndex = "";
          editFxVolume(hold.incoming, 1);
        }
        var q = hold.tail ? Math.max(0, Math.min(1, (t - hold.from - hold.fade) / hold.tail)) : 1;
        editFxVolume(hold.video, EDIT_OFFSCREEN_GAIN * (1 - q));
      }
    }
    editFxLeadTick(ctx, info);
  }

  /* 다음 클립의 앞당김(J컷): 그 화면이 오기 앞당김 초 전부터 숨은 <video> 로 소리를 낸다(0 → 0.6). 원본 0초보다 앞은 기다린다. */
  function editFxLeadTick(ctx, info) {
    var index = ctx.curIndex + 1;
    var next = ctx.clips[index];
    var lead = ctx.fxLead;
    if (lead && lead.index !== index) {
      editFxLeadEnd(ctx, false);
      lead = null;
    }
    // 작업 186: 대사 타이밍이 있는 클립의 앞당김도 미리보기에 없다(소리 요소 하나가 지금 클립 소리를 맡는다)
    if (!next || next.muted === true || ctx.fxHold || editAltFrom(ctx, next) || editSlipOf(next)) return;
    var seconds = editShiftOf(next, "audioLead");
    var at = info.starts[index];
    if (!seconds || !(at > ctx.t)) return;
    if (!lead) {
      var speed = editSpeedOf(next);
      var source = Number(next.in) - (at - ctx.t) * speed;
      if (ctx.t < at - seconds - 1e-6 || source < 0) return;
      var other = ctx.vids[1 - ctx.cur];
      if (!other) return;
      editSetSrc(other, editUrlOf(ctx, next.sourceKey));
      editSetRate(other, next);
      editSetTime(other, source);
      other.muted = false;
      editFxVolume(other, 0);
      editPlayVideo(other);
      lead = ctx.fxLead = { index: index, video: other, at: at, seconds: seconds };
    }
    editFxVolume(lead.video, EDIT_OFFSCREEN_GAIN * Math.max(0, Math.min(1, 1 - (at - ctx.t) / seconds)));
  }
  /* ---- 작업 182 미리보기 근사 끝 ---- */

  /* 타임라인 t 로 옮긴다: 그 클립 원본을 지금 <video> 에 두고 원본 시각을 맞춘다. 끝(전체 길이)이면 마지막 프레임. */
  function editSeek(ctx, t) {
    if (ctx.fxHold || ctx.fxLead) editFxStop(ctx); // 작업 182: 옮기면 겹쳐 재생하던 것을 끝낸다
    if (ctx.cutPlay) {
      ctx.cutPlay = null; // 작업 188: 재생헤드를 옮기면 [이 컷만] 멈출 자리를 지운다(editPlayCut 은 editPlay 뒤에 다시 둔다)
      editRenderCutPlay(ctx);
    }
    var total = editStarts(ctx.clips).total;
    ctx.t = Math.max(0, Math.min(total, Number(t) || 0));
    editPlaceHead(ctx);
    var loc = editLocate(ctx.clips, ctx.t);
    if (ctx.dom.empty) ctx.dom.empty.style.display = loc ? "none" : "";
    if (!ctx.playing && editBinFollow(ctx)) editRenderBin(ctx); // 작업 149: 멈춘 재생헤드가 든 클립의 테이크·컷을 재료 메뉴에
    var video = ctx.vids[ctx.cur];
    if (!loc || !video) {
      ctx.curIndex = -1;
      return;
    }
    editSetSrc(video, editUrlOf(ctx, loc.clip.sourceKey));
    video.muted = loc.clip.muted === true;
    editSetRate(video, loc.clip);
    var at = loc.source;
    if (ctx.t >= total - 1e-6) at = Math.max(Number(loc.clip.in), Number(loc.clip.out) - 1 / editFpsOf(ctx, loc.clip));
    editSetTime(video, at);
    ctx.curIndex = loc.index;
    if (editSyncAudio(ctx, loc.clip, at, true)) video.muted = true; // 지시서 118b: 원본 소리는 ctx.audio 가 낸다
    editPreloadNext(ctx);
    if (ctx.playing) editPlayVideo(video);
  }

  /* 다음 클립이 다른 원본(또는 같은 원본의 떨어진 구간)이면 숨은 <video> 에 미리 불러 둔다. */
  function editPreloadNext(ctx) {
    var clip = ctx.clips[ctx.curIndex];
    var next = ctx.clips[ctx.curIndex + 1];
    var other = ctx.vids[1 - ctx.cur];
    if (!clip || !next || !other || editContiguous(ctx, clip, next)) return;
    if (ctx.fxHold || ctx.fxLead) return; // 작업 182: 숨은 <video> 가 앞 클립 붙잡기·앞당김에 쓰이는 동안은 미룬다
    editSetSrc(other, editUrlOf(ctx, next.sourceKey));
    other.muted = true;
    editSetRate(other, next);
    editSetTime(other, next.in);
  }

  function editAdvance(ctx, index) {
    var prev = ctx.clips[index - 1];
    var next = ctx.clips[index];
    var video = ctx.vids[ctx.cur];
    ctx.curIndex = index;
    ctx.t = editStarts(ctx.clips).starts[index];
    if (prev && editContiguous(ctx, prev, next)) {
      video.muted = next.muted === true;
      editSetRate(video, next); // 이어진 같은 원본이어도 속도가 다를 수 있다
      if (editSyncAudio(ctx, next, Number(video.currentTime) || Number(next.in), false)) video.muted = true; // 지시서 118b
    } else {
      var other = ctx.vids[1 - ctx.cur];
      // 작업 182: 숨은 <video> 가 그 전 경계의 붙잡기에 남아 있으면 끝내고, 이 클립 앞당김으로 이미 재생 중이면 그대로 잇는다.
      if (ctx.fxHold) editFxHoldEnd(ctx);
      var leading = !!ctx.fxLead && ctx.fxLead.index === index && ctx.fxLead.video === other;
      if (ctx.fxLead) editFxLeadEnd(ctx, leading);
      editSetSrc(other, editUrlOf(ctx, next.sourceKey));
      if (Math.abs((Number(other.currentTime) || 0) - Number(next.in)) > (leading ? 0.25 : 0.02)) editSetTime(other, next.in);
      other.muted = next.muted === true;
      editSetRate(other, next);
      if (editSyncAudio(ctx, next, Number(next.in), true)) other.muted = true; // 지시서 118b
      editPlayVideo(other);
      // 작업 182: 디졸브·이어 감이면 앞 클립을 그만큼 더 재생한다(editFxTick 이 투명도·소리 크기·끝을 맞춘다).
      if (!(prev && editFxHoldStart(ctx, video, other, prev, ctx.t, leading))) {
        try {
          video.pause();
        } catch (error) {
          /* 무시 */
        }
      }
      video.classList.remove("is-active");
      other.classList.add("is-active");
      ctx.cur = 1 - ctx.cur;
    }
    editPreloadNext(ctx);
    editPlaceHead(ctx);
  }

  function editTick(ctx) {
    ctx.raf = 0;
    if (!ctx.playing) return;
    if (!editAlive(ctx)) {
      editPause(ctx);
      return;
    }
    var clip = ctx.clips[ctx.curIndex];
    var video = ctx.vids[ctx.cur];
    if (!clip || !video) {
      editPause(ctx);
      return;
    }
    var now = Number(video.currentTime) || 0;
    // 작업 182: 다음 클립으로 디졸브하면 그 초(원본 = 초 × 속도)만큼 일찍 바꿔 끼운다(다음 클립 화면 시작 = editStarts).
    var hasNext = ctx.curIndex + 1 < ctx.clips.length;
    var endAt = Number(clip.out) - (hasNext ? editDissolveOf(clip) * editSpeedOf(clip) : 0);
    var reached = now >= endAt - 0.5 / editFpsOf(ctx, clip) || video.ended;
    // 작업 188: [이 컷만 재생]이면 그 컷 끝(다음 클립으로 넘어가는 자리)에서 넘어가지 않고 멈춘다(editCutPlayEnd).
    if (ctx.cutPlay && ctx.cutPlay.mode === "only" && (reached || ctx.curIndex !== ctx.cutPlay.index)) {
      editCutPlayEnd(ctx);
      return;
    }
    if (reached) {
      if (ctx.curIndex + 1 >= ctx.clips.length) {
        ctx.t = editStarts(ctx.clips).total;
        editPause(ctx);
        editPlaceHead(ctx);
        return;
      }
      editAdvance(ctx, ctx.curIndex + 1);
    } else {
      // 타임라인 시각 = 클립 시작 + (원본 시각 − in) ÷ 속도(지시서 115)
      ctx.t = editStarts(ctx.clips).starts[ctx.curIndex] + Math.max(0, now - Number(clip.in)) / editSpeedOf(clip);
      editPlaceHead(ctx);
      // 지시서 118b: 원본 소리를 0.15초 넘게 어긋나면 다시 맞추고, end 를 넘으면 멈춘다
      if (editSyncAudio(ctx, clip, now, false)) video.muted = true;
    }
    var after = ctx.clips[ctx.curIndex + 1];
    if (ctx.fxHold || ctx.fxLead || (after && editShiftOf(after, "audioLead"))) editFxTick(ctx, editStarts(ctx.clips)); // 작업 182
    editFollowHead(ctx);
    editMixSync(ctx); // 작업 215a: 오디오 트랙 미리듣기(WebAudio)를 재생헤드에 맞춘다
    ctx.raf = editRaf(function () {
      editTick(ctx);
    });
  }

  function editPlay(ctx) {
    if (!ctx.clips.length || !ctx.vids.length) return;
    if (ctx.t >= editStarts(ctx.clips).total - 0.01) ctx.t = 0;
    ctx.playing = true;
    editSeek(ctx, ctx.t);
    editRenderPlayButton(ctx);
    editCancelRaf(ctx.raf);
    ctx.raf = editRaf(function () {
      editTick(ctx);
    });
  }

  function editPause(ctx) {
    ctx.playing = false;
    edit215cShuttleStop(ctx); // 작업 215c: 셔틀 배속 · 뒤로 재생 끝
    ctx.cutPlay = null; // 작업 188: 멈추면 [이 컷만] 멈출 자리 · 이 컷 재생 표시를 지운다
    editCancelRaf(ctx.raf);
    ctx.raf = 0;
    ctx.vids.forEach(function (video) {
      try {
        video.pause();
      } catch (error) {
        /* 무시 */
      }
    });
    editAudioStop(ctx); // 지시서 118b: 원본 소리 요소도 함께
    editMixStop(ctx); // 작업 215a: 오디오 트랙 미리듣기도
    if (ctx.fxHold || ctx.fxLead) editFxStop(ctx); // 작업 182: 겹쳐 재생하던 앞·다음 클립도
    editRenderPlayButton(ctx);
  }

  function editStep(ctx, dir, seconds) {
    editPause(ctx);
    var loc = editLocate(ctx.clips, ctx.t);
    var step = seconds || 1 / (loc ? editFpsOf(ctx, loc.clip) : EDIT_DEFAULT_FPS);
    editSeek(ctx, ctx.t + dir * step);
  }

  /* 영상 오류(presigned 만료 등): GET 을 다시 불러 주소만 바꾸고 같은 시각에서 잇는다(15초에 한 번까지). */
  function editOnVideoError(ctx, slot) {
    var video = ctx.vids[slot];
    if (!editAlive(ctx) || !video || !video._src) return;
    var now = Date.now();
    if (now - ctx.urlRetryAt < EDIT_URL_RETRY_MS) return;
    ctx.urlRetryAt = now;
    api(editPath(ctx) + "?variant=" + encodeURIComponent(ctx.variant))
      .then(function (view) {
        if (!editAlive(ctx)) return;
        editAbsorbMaterials(ctx, view || {});
        ctx.vids.forEach(function (v) {
          v._src = "";
        });
        if (ctx.audio) ctx.audio._src = ""; // 지시서 118b: 원본 소리 주소도 새로
        editSeek(ctx, ctx.t);
      })
      .catch(function () {
        /* 다음 오류 때 다시 */
      });
  }

  /* ---- 확대/축소 ---- */

  /* 재생헤드의 화면 위치를 그대로 두고 확대를 바꾼다(design §5.3). fromSlider 면 슬라이더 값은 건드리지 않는다. */
  function editSetZoom(ctx, pps, fromSlider) {
    var next = editClampPps(pps);
    if (Math.abs(next - ctx.pps) < 0.05) return;
    var sc = ctx.dom.scroller;
    var anchor = ctx.t * ctx.pps - (sc ? sc.scrollLeft || 0 : 0);
    ctx.pps = next;
    if (!fromSlider && ctx.dom.zoom) ctx.dom.zoom.value = String(editSliderOf(next));
    editRenderTimeline(ctx);
    if (sc) sc.scrollLeft = Math.max(0, ctx.t * next - anchor);
  }

  /* 슬라이더를 끄는 동안은 한 화면 그리기에 한 번만 다시 그린다(requestAnimationFrame). */
  function editZoomFromSlider(ctx) {
    if (!editAlive(ctx) || !ctx.dom.zoom) return;
    ctx.zoomPending = editPpsOf(ctx.dom.zoom.value);
    if (ctx.zoomRaf) return;
    ctx.zoomRaf = editRaf(function () {
      ctx.zoomRaf = 0;
      if (editAlive(ctx)) editSetZoom(ctx, ctx.zoomPending, true);
    });
  }

  /* ---- 필름스트립·파형(하나씩 차례로, design §5.6) ---- */

  function editQueueMedia(ctx) {
    var sc = ctx.dom.scroller;
    var lo = sc ? (sc.scrollLeft || 0) / ctx.pps : 0;
    var hi = sc && sc.clientWidth ? lo + sc.clientWidth / ctx.pps : Infinity;
    var now = Date.now();
    var order = editMediaOrder(
      ctx.clips,
      function (key) {
        return String((ctx.materials[key] || {}).mid || "");
      },
      lo,
      hi
    );
    ctx.clips.forEach(function (clip) {
      var from = (ctx.materials[clip.sourceKey] || {}).audioFrom; // 지시서 116 보완 10: 대체 소리의 v1 파형도 받는다
      if (from && from.mid && order.indexOf(String(from.mid)) < 0) order.push(String(from.mid));
    });
    ctx.mediaQueue = order.filter(function (mid) {
      var item = ctx.media[mid];
      return !item || (item.status === "done" && now - (item.at || 0) > EDIT_MEDIA_TTL_MS);
    });
    editPumpMedia(ctx);
  }

  function editPumpMedia(ctx) {
    if (ctx.mediaBusy || !editAlive(ctx)) return;
    var mid = ctx.mediaQueue.shift();
    if (!mid) return;
    ctx.mediaBusy = true;
    editMediaLoad(ctx, mid).then(function () {
      ctx.mediaBusy = false;
      if (!editAlive(ctx)) return;
      editPumpMedia(ctx);
    });
  }

  /* 재료 하나의 필름스트립·파형(GET /edit/media/<mid>)을 받아 ctx.media 에 두고 그 재료 클립을 다시 칠한다. 실패해도 풀리는 약속.
     작업 186: 빈틈 다듬기도 이 길로 받는다(editTightenMedia) — 받는 중이면 그 약속을 같이 기다린다(같은 요청을 겹쳐 보내지 않는다). */
  function editMediaLoad(ctx, mid) {
    var keep = ctx.media[mid];
    if (keep && keep.status === "loading" && keep.wait) return keep.wait;
    var item = { status: "loading", data: keep ? keep.data : null, wait: null };
    ctx.media[mid] = item;
    item.wait = api(editPath(ctx) + "/media/" + encodeURIComponent(mid))
      .then(function (data) {
        ctx.media[mid] = { status: "done", data: data || {}, at: Date.now() };
        editMediaFacts(ctx, mid, data || {});
      })
      .catch(function () {
        ctx.media[mid] = { status: "failed", data: null };
      })
      .then(function () {
        if (!editAlive(ctx)) return;
        ctx.clipEls.forEach(function (parts) {
          var clip = ctx.clips[parts.index];
          var cm = clip ? ctx.materials[clip.sourceKey] || {} : {};
          if (clip && (cm.mid === mid || (cm.audioFrom && cm.audioFrom.mid === mid))) editPaintClip(ctx, parts, clip);
        });
      });
    return item.wait;
  }

  /* 필름 응답의 길이·소리를 그 재료 정보에 채운다(기본 밖 재료는 GET 이 길이를 아직 안 잰다). */
  function editMediaFacts(ctx, mid, data) {
    Object.keys(ctx.materials).forEach(function (key) {
      var mat = ctx.materials[key];
      if (mat.mid !== mid) return;
      if (!(Number(mat.duration) > 0) && Number(data.duration) > 0) mat.duration = Number(data.duration);
      if (typeof data.hasAudio === "boolean" && typeof mat.hasAudio !== "boolean") mat.hasAudio = data.hasAudio;
    });
  }

  /* ---- 편집 동작 ---- */

  function editDo(ctx, action) {
    if (!editAlive(ctx) || ctx.busy) return;
    if (editTxDo(ctx, action)) return; // 작업 215b: 고른 자막·타이틀(지우기 · 자르기)
    if (action === "split" && edit215cLocked(ctx)) return edit215cSplitLocked(ctx); // 작업 219b: V1 잠김 = 소리 아이템만 나눈다
    if (edit215cBlocked(ctx, action)) return; // 작업 215c: 영상 트랙 잠금
    if (action === "play") {
      if (ctx.playing) editPause(ctx);
      else editPlay(ctx);
    } else if (action === "frameBack") {
      editStep(ctx, -1, 0);
    } else if (action === "frameFwd") {
      editStep(ctx, 1, 0);
    } else if (action === "secBack") {
      editStep(ctx, -1, 1);
    } else if (action === "secFwd") {
      editStep(ctx, 1, 1);
    } else if (action === "home") {
      editPause(ctx);
      editSeek(ctx, 0);
    } else if (action === "end") {
      editPause(ctx);
      editSeek(ctx, editStarts(ctx.clips).total);
    } else if (action === "split") {
      editSplitHere(ctx);
    } else if (action === "delete") {
      if (editMixDelete(ctx)) return; // 작업 215a: 고른 소리 키 · 아이템 먼저
      var many = editSelIndexes(ctx);
      if (many.length > 1) editCommit(ctx, editRemoveMany(ctx.clips, many), { sel: -1 }); // 작업 192: 고른 컷 모두 = 실행 취소 한 단계
      else if (ctx.sel >= 0 && ctx.sel < ctx.clips.length) editCommit(ctx, editRemove(ctx.clips, ctx.sel), { sel: -1 });
    } else if (action === "mute") {
      if (editMixItemRef(ctx)) {
        editMixCommit(ctx, editMixToggle(ctx.mix, ctx.mixSel.t, "mute")); // 작업 215a: 고른 소리의 트랙 mute
      } else if (editMultiOn(ctx)) {
        editNote(ctx, EDIT_MULTI_NOTE); // 작업 192
      } else if (ctx.sel >= 0 && ctx.sel < ctx.clips.length) {
        var next = editCloneClips(ctx.clips);
        next[ctx.sel].muted = !next[ctx.sel].muted;
        editCommit(ctx, next, {});
      }
    } else if (action === "undo" || action === "redo") {
      editUndoRedo(ctx, action === "redo");
    } else if (action === "snap") {
      // 작업 219b: 붙기 켜기 · 끄기(Shift+N) — 215c 🧲 과 같은 상태
      editNote(ctx, edit215cSnapToggle(ctx) ? "붙기 켬 (Shift+N)" : "붙기 끔 (Shift+N)");
    } else if (action === "zoomIn") {
      editSetZoom(ctx, ctx.pps * 1.25);
    } else if (action === "zoomOut") {
      editSetZoom(ctx, ctx.pps / 1.25);
    } else if (action === "reset") {
      if ((ctx.defaults[ctx.variant] || []).length) editCommit(ctx, editCloneClips(ctx.defaults[ctx.variant]), { sel: -1 });
    } else if (Object.prototype.hasOwnProperty.call(EDIT_NUDGES, action)) {
      editNudgeSel(ctx, EDIT_NUDGES[action][0], EDIT_NUDGES[action][1]);
    }
  }

  function editOnKey(ctx, event) {
    if (!editAlive(ctx)) {
      if (editCtx === ctx) editLeave();
      else document.removeEventListener("keydown", ctx.onKey);
      return;
    }
    if (ctx.trMenu) {
      // 작업 182: 전환 메뉴가 열린 동안 ⑦ 단축키는 쓰지 않는다(Space·Enter 는 메뉴 버튼을 누른다, Esc 는 메뉴 닫기).
      if (event && event.key === "Escape") editTrClose(ctx, true);
      return;
    }
    if (mnotes.close) return; // 작업 199: 모델 에러 로그 창이 열린 동안도(Esc 는 창이 닫는다)
    if (ctx.fixPick) {
      // 지시서 119: 테이크·장면 고르기 창이 열린 동안도 ⑦ 단축키는 쓰지 않는다(Esc 는 창 닫기).
      if (event && event.key === "Escape") editFixPickClose(ctx);
      return;
    }
    if (ctx.fix && ctx.fix.open) {
      // 지시서 116: AI로 고치기 패널이 열린 동안 ⑦ 단축키는 쓰지 않는다(Esc 는 패널 닫기).
      if (event && event.key === "Escape") editFixClose(ctx);
      return;
    }
    if (ctx.tighten && ctx.tighten.open && event && event.key === "Escape") {
      editTightenClose(ctx, true); // 작업 186: Esc 는 빈틈 다듬기 확인 칸 닫기(다른 단축키는 그대로 — 편집하면 칸이 닫힌다)
      return;
    }
    if (edit215cOnKey(ctx, event)) return; // 작업 215c: 프리미어식 단축키(처리하지 않은 키는 아래 기존 길)
    if (!event || event.defaultPrevented || event.altKey) return;
    var target = event.target || {};
    if (editTypingTarget(target)) return;
    if (event.key === "Enter" && !event.ctrlKey && !event.metaKey) {
      // 작업 188: Enter = 고른 컷 처음부터 재생, Shift+Enter = 이 컷만. 버튼 · 링크의 Enter · ⑦ 밖 · 고른 클립 없음이면 건드리지 않는다.
      if (!editCutKeyOk(ctx, target)) return;
      if (event.preventDefault) event.preventDefault();
      if (!event.repeat) editPlayCut(ctx, !!event.shiftKey, false);
      return;
    }
    var action = editKeyAction(event.key, event.code, event.ctrlKey || event.metaKey, event.shiftKey);
    if (!action) return;
    if (event.preventDefault) event.preventDefault();
    editDo(ctx, action);
  }

  /* 끝난 변경 하나(design §5.5): 바뀌기 전 상태를 쌓고(다시 실행 스택은 비움) 다시 그린 뒤 저장을 건다. */
  function editCommit(ctx, clips, opts) {
    var o = opts || {};
    editHistPush(ctx.hist, o.before || editSnapshotOf(ctx.variant, ctx.clips));
    editMixTag(ctx); // 작업 215a: 오디오 트랙도 같은 단계에(빈 mix 면 215a 전 스냅샷 그대로)
    ctx.clips = editTidyFx(clips); // 작업 182: 옮기기·삭제·자르기·늘이기 뒤 J/L 컷·디졸브 자리 규칙 정리
    if (o.mix) ctx.mix = o.mix; // 작업 215a: 자르기(S)가 소리 아이템도 함께 나눈다
    ctx.clips = editTxTidy(ctx.clips); // 작업 215b: 블랙/화이트 전환 길이 규칙
    edit215cOnCommit(ctx); // 작업 215c: 빈틈 앞 디졸브 정리 · 쌓은 상태에 마커·트랙
    if (o.sel !== undefined) ctx.sel = o.sel;
    if (o.sel !== undefined) ctx.multi = o.sel >= 0 ? [o.sel] : []; // 작업 192: 고른 클립을 정해 주는 변경은 한 클립으로
    editChanged(ctx);
  }

  function editChanged(ctx) {
    ctx.rev += 1;
    ctx.cutPlay = null; // 작업 188: 편집하면 [이 컷만] 멈출 자리(편집 전 클립으로 잰 값)를 지운다 — 재생은 보통 재생으로 잇는다
    if (ctx.tighten) editTightenClose(ctx); // 작업 186: 편집이 바뀌면 받은(받는 중인) 빈틈 다듬기 제안은 버린다
    if (ctx.sel >= ctx.clips.length) ctx.sel = -1;
    ctx.multi = editSelIndexes(ctx); // 작업 192: 고른 컷 목록도 지금 클립 안으로
    editNote(ctx, "");
    editRenderVariants(ctx);
    editRenderTimeline(ctx);
    editRenderBin(ctx);
    if (ctx.fix && editFixAlive(ctx, ctx.fix)) {
      editFixPaintVersions(ctx, ctx.fix); // 지시서 116 보완 2: 타임라인 사용 중 표시
      editFixSelFollow(ctx); // 작업 192: 고른 컷이 바뀌었으면 준비한 프롬프트는 다시 준비
    }
    editSeek(ctx, ctx.t);
    editScheduleSave(ctx);
  }

  /* 작업 190: 스택은 그림체마다 따로라(ctx.hists) 되돌려도 그림체는 바뀌지 않는다. */
  function editUndoRedo(ctx, redo) {
    var current = editSnapshotOf(ctx.variant, ctx.clips);
    if (!editMixEmpty(ctx.mix)) current.mix = editMixClone(ctx.mix); // 작업 215a: 오디오 트랙도 함께 되돌린다
    edit215cSnapExtras(ctx, current); // 작업 215c
    var snap = redo ? editRedo(ctx.hist, current) : editUndo(ctx.hist, current);
    if (!snap) return;
    ctx.clips = editCloneClips(snap.clips);
    editMixRestore(ctx, snap);
    editTxRestore(ctx, snap); // 작업 215b
    edit215cRestore(ctx, snap); // 작업 215c: 마커·트랙도 되돌린다
    ctx.sel = -1;
    editChanged(ctx);
  }

  function editSplitHere(ctx) {
    var loc = editLocate(ctx.clips, ctx.t);
    var mixSplit = editMixSplitAll(ctx.mix, ctx.t); // 작업 215a: 재생헤드에 걸친 소리 아이템도 나눈다(같은 실행 취소 단계)
    if (!loc) {
      if (mixSplit) editMixCommit(ctx, mixSplit);
      return;
    }
    var max = editLimit(ctx, "maxClips", 200);
    if (ctx.clips.length >= max) {
      editNote(ctx, "클립은 " + max + "개까지 둘 수 있습니다.");
      return;
    }
    var result = editSplit(ctx.clips, ctx.t, editNewId(ctx.clips), editFpsOf(ctx, loc.clip),
      editMinSource(editMinLen(ctx), editSpeedOf(loc.clip)));
    if (!result) {
      if (mixSplit) {
        editMixCommit(ctx, mixSplit);
        return;
      }
      editNote(ctx, "클립 끝에 너무 가까워 자를 수 없습니다. 재생헤드를 조금 옮겨 주세요.");
      return;
    }
    editCommit(ctx, result.clips, mixSplit ? { sel: result.index, mix: mixSplit } : { sel: result.index });
  }

  /* 고른 클립을 next 로 바꾸면 전체 길이 한도(maxTotalSeconds) 안인가. 넘으면 안내하고 false(지시서 115). */
  function editFitsTotal(ctx, clip, next) {
    var max = editLimit(ctx, "maxTotalSeconds", 300);
    var list = ctx.clips.slice();
    var at = list.indexOf(clip);
    if (at >= 0) list[at] = next;
    var total = editStarts(editTidyFx(list)).total; // 작업 182: 디졸브가 줄거나 빠지면 그만큼 길어진다
    if (total <= max + 1e-6) return true;
    editNote(ctx, "타임라인 전체 길이는 " + max + "초까지입니다.");
    editRenderRetime(ctx, true);
    return false;
  }

  function editSelected(ctx) {
    return ctx.sel >= 0 && ctx.sel < ctx.clips.length ? ctx.clips[ctx.sel] : null;
  }

  function editReplaceSel(ctx, next) {
    var clips = ctx.clips.slice();
    clips[ctx.sel] = next;
    editCommit(ctx, clips, { sel: ctx.sel });
  }

  /* 도구줄 [속도](지시서 115): 고른 클립 속도를 바꾼다(in·out 그대로, 타임라인 길이 = 원본 구간 ÷ 속도). */
  function editApplySpeed(ctx, value) {
    var clip = editSingle(ctx); // 작업 192: 여러 컷이면 하지 않는다
    if (!editAlive(ctx) || ctx.busy || !clip) return;
    var speed = Number(value);
    var next = editSetSpeed(clip, speed, editMinLen(ctx));
    if (!next) {
      if (EDIT_SPEEDS.indexOf(speed) >= 0) editNote(ctx, "이 클립은 너무 짧아 " + speed + "×로 바꿀 수 없습니다. 먼저 늘려 주세요.");
      editRenderRetime(ctx, true);
      return;
    }
    if (editSpeedOf(next) === editSpeedOf(clip) || !editFitsTotal(ctx, clip, next)) return;
    editReplaceSel(ctx, next);
  }

  /* 도구줄 "길이(초)"(지시서 115, editRetime): 원본 안이면 끝을, 모자라면 속도를 낮춰 맞추고 안내 한 줄. 같은 결과면 저장하지 않는다. */
  function editApplyLength(ctx, raw) {
    var clip = editSingle(ctx); // 작업 192: 여러 컷이면 하지 않는다
    if (!editAlive(ctx) || ctx.busy || !clip) return;
    var text = String(raw === undefined || raw === null ? "" : raw).trim().replace(",", ".");
    var mat = ctx.materials[clip.sourceKey] || {};
    var value = text ? Number(text) : NaN;
    var number = isFinite(value) && value > 0;
    var result = number ? editRetime(clip, value, mat.duration, editFpsOf(ctx, clip), editMinLen(ctx)) : null;
    if (!result) {
      editNote(ctx, number ? "이 클립은 원본 범위를 벗어나 길이를 바꿀 수 없습니다." : "길이는 초 단위 숫자로 넣어 주세요(예: 2.5).");
      editRenderRetime(ctx, true);
      return;
    }
    var next = result.clip;
    if (next.in === clip.in && next.out === clip.out && editSpeedOf(next) === editSpeedOf(clip)) {
      editNote(ctx, result.note);
      editRenderRetime(ctx, true);
      return;
    }
    if (!editFitsTotal(ctx, clip, next)) return;
    editReplaceSel(ctx, next);
    editNote(ctx, result.note);
    editRenderRetime(ctx, true);
  }

  /* 단축키 [ ]·Shift+[ ](지시서 115): 고른 클립 끝·시작을 원본 fps 한 프레임. 미리보기는 바뀐 끝 프레임을 보인다. */
  function editNudgeSel(ctx, edge, dir) {
    if (editMultiOn(ctx)) {
      editNote(ctx, EDIT_MULTI_NOTE); // 작업 192
      return;
    }
    var clip = editSelected(ctx);
    if (!clip) {
      editNote(ctx, "먼저 클립을 골라 주세요.");
      return;
    }
    var mat = ctx.materials[clip.sourceKey] || {};
    var fps = editFpsOf(ctx, clip);
    var next = editNudge(clip, edge, dir, mat.duration, fps, editMinLen(ctx));
    if (next.in === clip.in && next.out === clip.out) {
      var longer = (edge === "out") === (dir > 0);
      editNote(ctx, longer ? (edge === "out" ? "원본 끝입니다." : "원본 처음입니다.") : "더 줄일 수 없습니다.");
      return;
    }
    if (!editFitsTotal(ctx, clip, next)) return;
    editPause(ctx);
    editReplaceSel(ctx, next);
    editTrimPreview(ctx, next, edge, fps);
  }

  /* 그림체 탭 바꾸기(작업 190). 탭 = 그 그림체의 편집본(서버 작업 189, 그림체마다 따로 저장) — 확인 창도, 지금 문서에 바꿔 넣기도 없다.
     ① 지금 그림체의 남은 저장을 보내고 끝나기를 기다린다(editSaveFlushed). 실패하면 이 탭에 남고 오류를 보인다(409 는 지금 그림체를
        다시 불러오는 editFlushSave 가 맡는다). ② GET ?variant= 로 그 그림체의 문서 · etag · 내보내기 · 기록을 받아 그대로 보인다.
     실행 취소 스택은 그림체마다(ctx.hists)라 이 화면 안에서 돌아오면 되살아난다. 고른 클립 · 고른 컷 재생(188) · 열린 패널은 지운다. */
  function editSwitchVariant(ctx, variant) {
    if (!editAlive(ctx) || ctx.busy || ctx.exporting || variant === ctx.variant || !EDIT_VARIANT_LABEL[variant]) return;
    var label = EDIT_VARIANT_LABEL[variant];
    editPause(ctx);
    editFixClose(ctx); // 지시서 116: AI로 고치기 패널
    editFixPickClose(ctx); // 지시서 119: 테이크·장면 고르기 창
    editTrClose(ctx); // 작업 182: 전환 메뉴
    editTightenClose(ctx); // 작업 186: 빈틈 다듬기 확인 칸
    if (ctx.exportTimer) window.clearTimeout(ctx.exportTimer); // 앞 그림체 내보내기 조회는 멈춘다(새 그림체 화면이 다시 건다)
    ctx.exportTimer = 0;
    ctx.busy = true;
    editShowError(ctx, "");
    editNote(ctx, label + " 편집본을 불러오는 중…");
    editSaveFlushed(ctx)
      .then(
        function () {
          return editFetchView(ctx, variant, function () {
            editNote(ctx, "영상 길이를 읽는 중…");
          });
        },
        function (err) {
          if (err && !err.handled) err.saveFailed = true;
          throw err;
        }
      )
      .then(function (view) {
        ctx.busy = false;
        if (!editAlive(ctx)) return;
        editApplyView(ctx, view, true);
        ctx.t = 0;
        ctx.notice = "";
        editBuild(ctx);
      })
      .catch(function (err) {
        if (err && err.handled) return; // 409 EditChanged: 지금 그림체를 다시 불러오는 editLoad 가 화면 · busy 를 맡는다
        ctx.busy = false;
        if (!editAlive(ctx) || (err && err.left)) return;
        editNote(ctx, "");
        editShowError(ctx, (err && err.saveFailed ? "저장하지 못해 그림체를 바꾸지 않았습니다: " : "그림체를 바꾸지 못했습니다: ") + errMessage(err));
        editScheduleExportPoll(ctx);
      });
  }

  /* 재료 넣기: 재생헤드가 있는 클립 뒤에(없으면 끝에) 그 영상 전체(기본 문서에 그 원본 클립이 하나면 그 구간)를 넣는다.
     작업 149: range({in, out, label}, 원본 시간 — 통 테이크 안 컷)가 있으면 그 구간을 넣는다.
     길이를 아직 안 잰 재료는 필름 요청이 먼저 잰다. */
  function editInsertMaterial(ctx, sourceKey, button, range) {
    if (!editAlive(ctx) || ctx.busy) return;
    var mat = ctx.materials[sourceKey];
    if (!mat) return;
    var variant = ctx.variant; // 작업 190: 길이를 재는 동안 탭을 바꾸면 넣지 않는다(재료는 그 그림체 문서에만)
    function place() {
      var duration = Number(mat.duration) || 0;
      var maxClips = editLimit(ctx, "maxClips", 200);
      var maxTotal = editLimit(ctx, "maxTotalSeconds", 300);
      if (!(duration > 0)) {
        editNote(ctx, "이 영상의 길이를 읽지 못했습니다. 잠시 뒤 다시 눌러 주세요.");
        return;
      }
      if (ctx.clips.length >= maxClips) {
        editNote(ctx, "클립은 " + maxClips + "개까지 둘 수 있습니다.");
        return;
      }
      var same = (ctx.defaults[ctx.variant] || []).filter(function (c) {
        return c.sourceKey === sourceKey;
      });
      var start = same.length === 1 ? Number(same[0].in) : 0;
      var end = same.length === 1 ? Number(same[0].out) : duration;
      if (same.length !== 1 && mat.group === "retake" && Number((mat.retake || {}).trimTo) > 0) end = Math.min(end, Number(mat.retake.trimTo)); // 지시서 116 보완 1: 자른 버전은 그 길이까지
      if (range) {
        start = Math.max(0, Number(range.in) || 0);
        end = Math.min(duration, Number(range.out) || 0);
      }
      end = Math.min(end, start + maxTotal - editStarts(ctx.clips).total);
      if (end - start < editMinLen(ctx)) {
        editNote(ctx, "타임라인 전체 길이는 " + maxTotal + "초까지입니다.");
        return;
      }
      var loc = editLocate(ctx.clips, ctx.t);
      var at = loc ? loc.index + 1 : ctx.clips.length;
      var clip = { id: editNewId(ctx.clips), sourceKey: sourceKey, in: editRound3(start), out: editRound3(end), muted: false };
      if (range && range.label) clip.label = String(range.label).slice(0, 40);
      else if (mat.label) clip.label = String(mat.label).slice(0, 40);
      var next = ctx.clips.slice();
      next.splice(at, 0, clip);
      ctx.t = editStarts(next).starts[at];
      editCommit(ctx, next, { sel: at });
    }
    if (Number(mat.duration) > 0) {
      place();
      return;
    }
    button.disabled = true;
    editNote(ctx, "영상 길이를 읽는 중…");
    api(editPath(ctx) + "/media/" + encodeURIComponent(String(mat.mid || "")))
      .then(function (data) {
        button.disabled = false;
        if (!editAlive(ctx)) return;
        ctx.media[mat.mid] = { status: "done", data: data || {}, at: Date.now() };
        editMediaFacts(ctx, mat.mid, data || {});
        if (ctx.busy || ctx.variant !== variant) return;
        editNote(ctx, "");
        place();
      })
      .catch(function (err) {
        button.disabled = false;
        if (editAlive(ctx)) editNote(ctx, "넣지 못했습니다: " + errMessage(err));
      });
  }

  /* 재료 한 줄 길이(초). AI로 고친 자른 버전은 그 길이까지(지시서 116 보완 1). 모르면 0. */
  function editBinSeconds(mat) {
    var duration = Number(mat.duration);
    if (mat.group === "retake" && Number((mat.retake || {}).trimTo) > 0 && duration > 0) duration = Math.min(duration, Number(mat.retake.trimTo));
    return duration > 0 ? duration : 0;
  }

  /* 테이크 만든 때(서버 takeAt ISO) → "11/3 14:05". 읽을 수 없으면 "". */
  function editTakeWhen(iso) {
    var d = iso ? new Date(String(iso)) : null;
    if (!d || isNaN(d.getTime())) return "";
    function pad(n) {
      return (n < 10 ? "0" : "") + n;
    }
    return d.getMonth() + 1 + "/" + d.getDate() + " " + pad(d.getHours()) + ":" + pad(d.getMinutes());
  }

  /* 작업 145: 재료 → 테이크 메뉴 항목. takes = 테이크(takeNo 오름차순 = 오래된 것부터) + 테이크가 아닌 통 영상(합친 영상),
     cutsOf(take) = 그 테이크의 컷 재료(takeOf). 테이크·통 영상이 하나도 없으면 cutsOf(null) = 재료 전부. */
  function editBinTakes(ctx) {
    var mats = (ctx.matList[ctx.variant] || []).map(function (item) {
      return ctx.materials[item.sourceKey] || item;
    });
    var takes = mats.filter(function (m) {
      return typeof m.takeNo === "number";
    }).sort(function (a, b) {
      return a.takeNo - b.takeNo;
    });
    var extras = mats.filter(function (m) {
      return typeof m.takeNo !== "number" && m.kind === "final" && typeof m.takeOf !== "number";
    });
    return {
      takes: takes.concat(extras),
      newest: takes.length ? takes[takes.length - 1] : extras[0] || null,
      cutsOf: function (take) {
        if (!take) return takes.length || extras.length ? [] : mats;
        if (typeof take.takeNo !== "number") return [];
        return mats.filter(function (m) {
          return m.takeOf === take.takeNo && m.sourceKey !== take.sourceKey;
        });
      }
    };
  }

  function editBinTakeText(ctx, mat) {
    var seconds = editBinSeconds(mat);
    var parts = [typeof mat.takeNo === "number" ? mat.takeLabel || "테이크 " + mat.takeNo : mat.label || "영상"];
    if (seconds > 0) parts.push(seconds.toFixed(1) + "초");
    if (typeof mat.takeNo === "number") parts.push(editTakeWhen(mat.takeAt) || (ctx.variant === "real" ? "실사" : "애니"));
    return parts.join(" · ");
  }

  function editBinSelect(className, label, mats, value, textOf) {
    var select = el("select", className);
    select.title = label;
    select.setAttribute("aria-label", label);
    mats.forEach(function (mat) {
      var option = el("option", "", textOf(mat));
      option.value = mat.sourceKey;
      select.appendChild(option);
    });
    select.value = value;
    return select;
  }

  /* 작업 145: 재료 = [테이크 고르기] 메뉴(가장 최근 테이크가 처음 값) + [＋ 넣기], 고른 테이크에 컷 재료가 있으면 [컷 고르기] + [＋ 컷 넣기].
     고른 값(sourceKey)은 ctx.binTake·ctx.binCut[그림체]에 두어 다시 그리기·폴링(재료 다시 받기)에도 남는다. 넣기는 editInsertMaterial. */
  function editRenderBin(ctx) {
    var bin = ctx.dom.bin;
    if (!bin) return;
    clear(bin);
    ctx.dom.binFixPaint = null; // 작업 192
    var list = ctx.matList[ctx.variant] || [];
    if (!list.length) return;
    editBinFollow(ctx); // 작업 149: 고른 타임라인 클립(또는 재생헤드 클립)이 바뀌었으면 메뉴를 그 테이크·컷으로
    var head = el("div", "vnew-edit-bin-head");
    head.appendChild(el("span", "vnew-edit-bin-title", "재료"));
    head.appendChild(el("span", "vnew-hint", "누르면 재생헤드가 있는 클립 뒤에 넣습니다."));
    if (editFixOn(ctx)) head.appendChild(editFixPickButton(ctx)); // 지시서 119: [AI로 고치기] 1개(테이크·장면 고르기 창)
    bin.appendChild(head);
    var info = editBinTakes(ctx);
    var picked = ctx.binTake[ctx.variant];
    var take = info.takes.filter(function (m) {
      return m.sourceKey === picked;
    })[0] || info.newest;
    if (take) {
      ctx.binTake[ctx.variant] = take.sourceKey;
      var row = el("div", "vnew-edit-bin-list");
      row.appendChild(el("span", "vnew-edit-bin-label", "테이크 고르기"));
      var takeSel = editBinSelect("vnew-edit-bin-take", "테이크 고르기", info.takes, take.sourceKey, function (mat) {
        return editBinTakeText(ctx, mat);
      });
      takeSel.addEventListener("change", function () {
        ctx.binTake[ctx.variant] = String(takeSel.value || "");
        editRenderBin(ctx);
      });
      row.appendChild(takeSel);
      var add = el("button", "outline-button vnew-edit-bin-add", "＋ 넣기");
      add.type = "button";
      add.title = "고른 테이크를 재생헤드가 있는 클립 뒤에 넣기";
      add.addEventListener("click", function () {
        editInsertMaterial(ctx, ctx.binTake[ctx.variant], add);
      });
      row.appendChild(add);
      bin.appendChild(row);
    }
    if (typeof sepMountBin === "function") sepMountBin(ctx, bin); // 작업 217: 섞인 소리 분리하기(재료마다)
    var cuts = info.cutsOf(take || null);
    var timeCuts = editBinTimeCuts(take);
    var options = timeCuts.map(function (c) {
      return { value: editBinCutValue(c), text: c.label + " · " + editFixSecs(c.seconds) + "초", cut: c, mat: null };
    }).concat(cuts.map(function (mat) {
      var seconds = editBinSeconds(mat);
      return { value: mat.sourceKey, text: String(mat.label || "영상").replace(/^실사 \d+회차 · /, "") + (seconds > 0 ? " · " + seconds.toFixed(1) + "초" : ""),
               cut: null, mat: mat };
    }));
    if (!options.length) return;
    var cutPicked = ctx.binCut[ctx.variant];
    var chosen = options.filter(function (o) {
      return o.value === cutPicked;
    })[0] || options[0];
    ctx.binCut[ctx.variant] = chosen.value;
    var cutRow = el("div", "vnew-edit-bin-list vnew-edit-bin-cuts");
    cutRow.appendChild(el("span", "vnew-edit-bin-label", take ? "컷" : "컷 고르기"));
    var cutSel = editBinSelect("vnew-edit-bin-cut", "컷 고르기", options.map(function (o) {
      return { sourceKey: o.value, text: o.text };
    }), chosen.value, function (o) {
      return o.text;
    });
    function current() {
      return options.filter(function (o) {
        return o.value === ctx.binCut[ctx.variant];
      })[0] || options[0];
    }
    cutRow.appendChild(cutSel);
    var addCut = el("button", "outline-button vnew-edit-bin-cut-add", "＋ 컷 넣기");
    addCut.type = "button";
    addCut.title = "고른 컷을 재생헤드가 있는 클립 뒤에 넣기";
    addCut.addEventListener("click", function () {
      var o = current();
      if (o.cut) editInsertMaterial(ctx, take.sourceKey, addCut, { in: o.cut.start, out: o.cut.end, label: o.cut.label });
      else editInsertMaterial(ctx, o.value, addCut);
    });
    cutRow.appendChild(addCut);
    var fixCut = null;
    if (editFixOn(ctx)) { // 작업 149: [이 컷 AI로 고치기] → 119 패널을 그 테이크·그 컷 구간(「이 구간만」)으로 바로 연다
      fixCut = el("button", "outline-button vnew-edit-bin-cut-fix", "이 컷 AI로 고치기");
      fixCut.type = "button";
      fixCut.setAttribute("aria-haspopup", "dialog");
      fixCut.addEventListener("click", function () {
        if (!editFixOpenSel(ctx)) editBinFixCut(ctx, take, current()); // 작업 192: 타임라인에서 고른 컷(하나 또는 여럿)이 있으면 그 컷들
      });
      cutRow.appendChild(fixCut);
    }
    function paintFix() {
      if (!fixCut) return;
      var target = editBinFixTarget(take, current());
      var sel = editFixSelTarget(ctx); // 작업 192
      fixCut.disabled = !(sel || target) || ctx.busy;
      fixCut.title = sel ? sel.error || editFixSelTitle(sel) :
        target ? "고른 컷만 AI로 고칩니다(비용은 다음 창에서 보입니다)" : "이 컷은 AI로 고칠 수 없습니다";
    }
    ctx.dom.binFixPaint = paintFix; // 작업 192: 고른 컷이 바뀌면 editFixRenderSel 이 다시 칠한다
    cutSel.addEventListener("change", function () {
      ctx.binCut[ctx.variant] = String(cutSel.value || "");
      paintFix();
    });
    paintFix();
    bin.appendChild(cutRow);
  }

  /* 작업 149: 통 테이크 재료의 컷(서버 cuts {cutId, no, label, start, end, seconds}, 원본 시간) — 시작 순. 테이크가 아니면 []. */
  function editBinTimeCuts(take) {
    return (take && Array.isArray(take.cuts) ? take.cuts : []).filter(function (c) {
      return c && isFinite(Number(c.start)) && isFinite(Number(c.end)) && Number(c.end) > Number(c.start);
    }).map(function (c, i) {
      var a = Number(c.start);
      var b = Number(c.end);
      return { cutId: String(c.cutId || ""), no: c.no, label: String(c.label || "장면 " + (i + 1)), start: a, end: b,
               seconds: Number(c.seconds) > 0 ? Number(c.seconds) : b - a };
    }).sort(function (x, y) {
      return x.start - y.start;
    });
  }

  function editBinCutValue(cut) {
    return "cut:" + cut.cutId;
  }

  /* 작업 149(순수): 클립 원본 구간 [a, b] → 컷 번호(cuts 는 시작 순). 클립이 거의 그 컷이면(서로 80% 넘게 겹침) 그 컷, 아니면 in 점이 든 컷
     (컷 끝 0.05초 안은 다음 컷), in 점이 끝보다 뒤면 마지막 컷. 컷이 없으면 -1. */
  function editBinCutAt(cuts, a, b) {
    var list = Array.isArray(cuts) ? cuts : [];
    var start = Number(a);
    var end = Number(b);
    if (!list.length || !isFinite(start)) return -1;
    var best = -1;
    var most = 0;
    list.forEach(function (c, i) {
      var overlap = Math.min(end, Number(c.end)) - Math.max(start, Number(c.start));
      if (overlap > most) {
        most = overlap;
        best = i;
      }
    });
    if (best >= 0 && end > start && most >= 0.8 * (end - start) && most >= 0.8 * (Number(list[best].end) - Number(list[best].start))) return best;
    for (var i = 0; i < list.length; i++) {
      if (start < Number(list[i].end) - 0.05) return i;
    }
    return list.length - 1;
  }

  /* 작업 149: 타임라인 클립 → 재료 메뉴 값 {take, cut}(테이크 sourceKey·컷 메뉴 값). 테이크 원본이면 그 안 컷(editBinCutAt), 테이크의 컷 재료면
     그 테이크와 그 재료. 메뉴에 없는 재료면 null. */
  function editBinPickFor(ctx, clip) {
    if (!clip) return null;
    var info = editBinTakes(ctx);
    var take = info.takes.filter(function (m) {
      return m.sourceKey === clip.sourceKey;
    })[0];
    if (take) {
      var cuts = editBinTimeCuts(take);
      var i = editBinCutAt(cuts, clip.in, clip.out);
      return { take: take.sourceKey, cut: i >= 0 ? editBinCutValue(cuts[i]) : "" };
    }
    var mat = ctx.materials[clip.sourceKey] || {};
    var owner = typeof mat.takeOf === "number" ? info.takes.filter(function (m) {
      return m.takeNo === mat.takeOf;
    })[0] : null;
    return owner ? { take: owner.sourceKey, cut: mat.sourceKey } : null;
  }

  /* 작업 149: 고른 타임라인 클립(없으면 멈춘 재생헤드가 있는 클립)이 바뀌었으면 재료 메뉴를 그 클립의 테이크·컷으로 맞춘다. 같은 클립이면
     그대로(사용자가 메뉴에서 고른 값이 다시 그리기에도 남는다). 처음 한 번은 기억만 한다(처음 값 = 가장 최근 테이크, 작업 145).
     메뉴 값이 바뀌었으면 true. */
  function editBinFollow(ctx) {
    if (!ctx || !ctx.matList || !ctx.materials || !ctx.binTake || !ctx.binCut || !Array.isArray(ctx.clips)) return false;
    if (!ctx.binFollow) ctx.binFollow = {};
    var v = ctx.variant;
    var clip = editSelected(ctx);
    var how = "s";
    if (!clip) {
      if (ctx.playing) return false;
      var loc = editLocate(ctx.clips, ctx.t);
      clip = loc ? loc.clip : null;
      how = "p";
    }
    var key = clip ? [how, clip.id, clip.sourceKey, clip.in, clip.out].join("|") : "";
    var first = !Object.prototype.hasOwnProperty.call(ctx.binFollow, v);
    if (!first && ctx.binFollow[v] === key) return false;
    ctx.binFollow[v] = key;
    if (first || !clip) return false;
    var pick = editBinPickFor(ctx, clip);
    if (!pick) return false;
    var changed = ctx.binTake[v] !== pick.take || (!!pick.cut && ctx.binCut[v] !== pick.cut);
    ctx.binTake[v] = pick.take;
    if (pick.cut) ctx.binCut[v] = pick.cut;
    return changed;
  }

  /* 작업 149: [이 컷 AI로 고치기] 대상 {mid, range}. 통 테이크 안 컷 = 그 테이크(retake.can) + 그 컷 구간, 컷 재료 = 그 재료 통째(range null).
     고칠 수 없으면 null. */
  function editBinFixTarget(take, option) {
    if (!option) return null;
    if (option.cut) {
      if (!take || !take.retake || take.retake.can !== true) return null;
      return { mid: String(take.mid || ""), sourceKey: take.sourceKey, range: { in: editRound3(option.cut.start), out: editRound3(option.cut.end) } };
    }
    var mat = option.mat;
    if (!mat || !mat.retake || mat.retake.can !== true) return null;
    return { mid: String(mat.mid || ""), sourceKey: mat.sourceKey, range: null };
  }

  /* 119 패널(editFixOpen)을 그 대상으로 연다. 고른 클립이 같은 원본이고 그 구간과 겹치면 [바꾸기]가 그 클립을 바꾼다. */
  function editBinFixCut(ctx, take, option) {
    var target = editBinFixTarget(take, option);
    if (!target || !target.mid || !editFixOn(ctx)) return;
    var clip = editSelected(ctx);
    var r = target.range;
    var same = clip && clip.sourceKey === target.sourceKey &&
      (!r || Math.min(Number(clip.out), r.out) - Math.max(Number(clip.in), r.in) > 0.05);
    editFixOpen(ctx, target.mid, same ? clip.id : "", r, { names: option && option.cut ? option.cut.label : "" }); // 작업 192: 머리 줄 컷 이름
  }

  /* ---- 저장(design §5.5: 0.8초 디바운스, 한 번에 하나, 409 다시 불러오기) ---- */

  function editScheduleSave(ctx) {
    if (ctx.saveTimer) window.clearTimeout(ctx.saveTimer);
    ctx.saveTimer = window.setTimeout(function () {
      ctx.saveTimer = 0;
      editFlushSave(ctx);
    }, EDIT_SAVE_MS);
    editRenderSave(ctx);
  }

  function editSettleSave(ctx, err) {
    var waiters = ctx.saveWaiters;
    ctx.saveWaiters = [];
    waiters.forEach(function (pair) {
      if (err) pair[1](err);
      else pair[0]();
    });
  }

  /* 남은 변경을 지금 PUT 한다. 보내는 중이면 끝난 뒤 마지막 상태로 한 번 더(saveAgain). 화면을 떠난 뒤에도 보낸다. */
  function editFlushSave(ctx) {
    if (ctx.saveTimer) {
      window.clearTimeout(ctx.saveTimer);
      ctx.saveTimer = 0;
    }
    if (ctx.saving) {
      ctx.saveAgain = true;
      return;
    }
    if (ctx.rev === ctx.savedRev && ctx.saved && !ctx.saveFailed) {
      editRenderSave(ctx);
      editSettleSave(ctx, null);
      return;
    }
    var rev = ctx.rev;
    var variant = ctx.variant; // 작업 190: 그 그림체 문서에 그 그림체 etag 로(탭 바꾸기는 이 저장이 끝나기를 기다린다)
    ctx.saving = true;
    ctx.saveAgain = false;
    editRenderSave(ctx);
    api(editPath(ctx), { method: "PUT", body: JSON.stringify(Object.assign(edit215cBody(ctx, editTxBody(ctx, editSaveBody(ctx.etag, variant, ctx.clips))), editMixSaveFields(ctx.mix))) }) // 작업 215a·215b·215c
      .then(function (res) {
        ctx.saving = false;
        ctx.saveFailed = false;
        ctx.etag = String((res && res.etag) || "");
        ctx.saved = true;
        ctx.savedRev = rev;
        if (ctx.savedVariants[variant] !== true) {
          ctx.savedVariants[variant] = true; // 작업 190: 탭에 편집됨 점
          editRenderVariants(ctx);
        }
        if (ctx.exportState && ctx.exportState.status && ctx.exportState.status !== "none" && ctx.rev > 0) {
          ctx.exportState.matchesDoc = false;
        }
        if (ctx.saveAgain || ctx.rev !== ctx.savedRev) {
          editFlushSave(ctx);
          return;
        }
        editRenderSave(ctx);
        editRenderExport(ctx);
        editSettleSave(ctx, null);
      })
      .catch(function (err) {
        ctx.saving = false;
        if (err && err.type === "EditChanged") {
          err.handled = true;
          editSettleSave(ctx, err);
          if (editAlive(ctx)) {
            editPause(ctx);
            editShowWait(ctx, "다른 곳에서 바뀐 편집을 다시 불러오는 중…");
            editLoad(ctx, variant, EDIT_CHANGED_TEXT); // 작업 190: 같은 그림체를 다시(인자 없으면 서버가 최근 그림체를 고른다)
          }
          return;
        }
        ctx.saveFailed = true;
        editRenderSave(ctx);
        if (editAlive(ctx)) editShowError(ctx, "저장하지 못했습니다: " + errMessage(err));
        editSettleSave(ctx, err);
      });
  }

  /* 저장이 다 끝나면(남은 변경 없음) 풀리는 약속. 저장 안 된 기본 문서면 그대로 PUT 한다(design §5.5 내보내기 앞). */
  function editEnsureSaved(ctx) {
    return new Promise(function (resolve, reject) {
      if (!ctx.saving && !ctx.saveTimer && ctx.rev === ctx.savedRev && ctx.saved && !ctx.saveFailed) {
        resolve();
        return;
      }
      ctx.saveWaiters.push([resolve, reject]);
      editFlushSave(ctx);
    });
  }

  /* 작업 190: 남은 변경(디바운스 · 보내는 중 · 보내지 않은 편집)이 있으면 지금 보내고 끝나기를 기다리는 약속(탭 바꾸기 앞).
     editEnsureSaved 와 달리 고친 것 없는 기본 문서는 PUT 하지 않는다(탭만 오가도 저장본이 생기지 않게). */
  function editSaveFlushed(ctx) {
    return new Promise(function (resolve, reject) {
      if (!ctx.saving && !ctx.saveTimer && ctx.rev === ctx.savedRev) {
        resolve();
        return;
      }
      ctx.saveWaiters.push([resolve, reject]);
      editFlushSave(ctx);
    });
  }

  /* ---- 내보내기 ---- */

  function editExport(ctx) {
    if (!editAlive(ctx) || ctx.busy || ctx.exporting || !ctx.clips.length) return;
    ctx.exporting = true;
    editRenderTools(ctx);
    editShowError(ctx, "");
    editPause(ctx);
    editEnsureSaved(ctx)
      .then(function () {
        // 작업 190: 지금 그림체 문서를 그 그림체 etag 로(서버 작업 189)
        if (ctx.mixStems) {
          // 작업 215a: 스템 zip(대사만 · 배경음 · 효과음 · mix.wav)도
          return api(editPath(ctx) + "/export", { method: "POST", body: JSON.stringify(editMixExportBody(ctx)) });
        }
        return api(editPath(ctx) + "/export", { method: "POST", body: JSON.stringify({ etag: ctx.etag, variant: ctx.variant }) });
      })
      .then(function (res) {
        ctx.exporting = false;
        if (!editAlive(ctx)) return;
        ctx.exportState = (res && res.export) || { status: "none" };
        editRenderTools(ctx);
        editRenderExport(ctx);
        editScheduleExportPoll(ctx);
      })
      .catch(function (err) {
        ctx.exporting = false;
        if (!editAlive(ctx) || (err && err.handled)) return;
        editRenderTools(ctx);
        if (err && err.type === "EditChanged") {
          editShowWait(ctx, "다른 곳에서 바뀐 편집을 다시 불러오는 중…");
          editLoad(ctx, ctx.variant, EDIT_CHANGED_TEXT); // 작업 190: 같은 그림체를 다시
          return;
        }
        if (err && err.type === "ExportRunning") {
          // 작업 190: 다른 그림체(또는 다른 문서) 내보내기가 진행 중 — 실패가 아니라 안내 한 줄(서버 문구, 끝난 뒤 다시 누르면 된다)
          editNote(ctx, errMessage(err));
          return;
        }
        editShowError(ctx, "내보내지 못했습니다: " + errMessage(err));
      });
  }

  /* 내보내기 queued·running 이면 3초마다 GET — 편집 화면은 다시 그리지 않고 아래 상태만 바꾼다(design §5.6).
     작업 190: 내보내기는 그림체마다 따로라, 보낸 뒤 탭을 바꿨으면 늦게 온 앞 그림체 응답은 버린다. */
  function editScheduleExportPoll(ctx) {
    if (ctx.exportTimer) window.clearTimeout(ctx.exportTimer);
    ctx.exportTimer = 0;
    var status = (ctx.exportState || {}).status;
    if (status !== "queued" && status !== "running") return;
    ctx.exportTimer = window.setTimeout(function () {
      ctx.exportTimer = 0;
      if (!editAlive(ctx)) return;
      var variant = ctx.variant;
      api(editPath(ctx) + "?variant=" + encodeURIComponent(variant))
        .then(function (view) {
          if (!editAlive(ctx) || ctx.variant !== variant || ctx.busy) return;
          ctx.exportState = (view && view.export) || { status: "none" };
          ctx.exportHistory = Array.isArray((view || {}).exportHistory) ? view.exportHistory : [];
          editRenderExport(ctx);
          editScheduleExportPoll(ctx);
        })
        .catch(function (err) {
          if (!editAlive(ctx) || ctx.variant !== variant || ctx.busy) return;
          // 재료가 없어졌으면(gen 다시 만들기 등) 같은 실패를 되풀이하지 않고 안내로 바꾼다(지시서 115).
          if (editGoneKind(err)) editShowGone(ctx, err);
          else editScheduleExportPoll(ctx);
        });
    }, EDIT_EXPORT_POLL_MS);
  }

  /* 내보내기 상태 서명: 바뀐 것이 없으면 다시 그리지 않는다(결과 영상 재생이 끊기지 않게). 주소는 ?·# 앞 경로만. */
  function editExportSig(ctx) {
    var e = ctx.exportState || {};
    function path(url) {
      return String(url || "").split("#")[0].split("?")[0];
    }
    return JSON.stringify([
      e.status || "none", e.n || 0, (e.progress || {}).done || 0, (e.progress || {}).total || 0, e.matchesDoc !== false,
      e.error || "", path(e.videoUrl), ctx.exportHistory.map(function (h) {
        return [(h || {}).n, (h || {}).status, path((h || {}).videoUrl)];
      })
    ]);
  }

  function editRenderExport(ctx) {
    var box = ctx.dom.exportBox;
    if (!box) return;
    var sig = editExportSig(ctx);
    if (sig === ctx.exportSig) return;
    ctx.exportSig = sig;
    clear(box);
    var ex = ctx.exportState || { status: "none" };
    var status = ex.status || "none";
    box.appendChild(el("h3", "vnew-edit-export-title", "내보내기"));
    box.appendChild(el("p", "vnew-edit-export-status is-" + status, editExportText(ex)));
    if (status === "queued" || status === "running") {
      var progress = ex.progress || {};
      var total = Number(progress.total) || 0;
      var bar = el("div", "vnew-edit-export-bar");
      var fill = el("div", "vnew-edit-export-fill");
      fill.style.width = (total > 0 ? Math.round(((Number(progress.done) || 0) / total) * 100) : 0) + "%";
      bar.appendChild(fill);
      box.appendChild(bar);
    }
    if (status !== "none" && status !== "failed" && ex.matchesDoc === false) {
      box.appendChild(el("p", "vnew-hint vnew-edit-export-stale", "편집이 바뀌었습니다 — 다시 내보내기"));
    }
    if (status === "done" && ex.videoUrl) {
      var video = el("video", "vnew-edit-export-video");
      video.src = ex.videoUrl;
      video.controls = true;
      video.preload = "metadata";
      video.setAttribute("playsinline", "");
      box.appendChild(video);
      var tools = el("div", "vnew-edit-export-tools");
      // 작업 216-fix1b: 새 내보내기는 zip(downloadName — 소리 없는 mp4 · 소리 mp3 · README.txt), 옛 내보내기는 예전 그대로 mp4
      tools.appendChild(downloadLink(ex.downloadUrl || ex.videoUrl, "내려받기 (" + (ex.downloadName || "edit-" + (ex.n || 1) + ".mp4") + ")"));
      if (ex.stemsUrl) tools.appendChild(downloadLink(ex.stemsUrl, "스템 내려받기 (대사만 · 배경음 · 효과음 · mix.wav)")); // 작업 215a
      box.appendChild(tools);
    }
    var history = ctx.exportHistory.filter(function (h) {
      return h && h.status === "done" && h.videoUrl;
    });
    if (history.length) {
      var details = el("details", "vnew-edit-export-history");
      details.appendChild(el("summary", null, "지난 결과 (" + history.length + ")"));
      history
        .slice()
        .reverse()
        .forEach(function (h) {
          var item = el("div", "vnew-edit-export-item");
          item.appendChild(el("span", "vnew-edit-export-item-name", h.n + "번 · " + editTimeText(h.seconds)));
          var play = el("a", "outline-button vnew-edit-export-link", "재생");
          play.href = h.videoUrl;
          play.setAttribute("target", "_blank");
          play.setAttribute("rel", "noopener");
          item.appendChild(play);
          item.appendChild(downloadLink(h.downloadUrl || h.videoUrl, "내려받기"));
          details.appendChild(item);
        });
      box.appendChild(details);
    }
    editTxExportTools(ctx, box); // 작업 215b: 자막 굽기 · SRT · 프리미어 내보내기
  }

  /* ==== 작업 215b 시작 ==== */
  /* ================================================================ ⑦ 자막 · 타이틀 · 전환 · 색 보정 · 프리미어 내보내기 · ⑦ 안 대사 고치기(작업 215b)

     서버 edit_text.py — 문서 키 captions · titles · fadeIn · fadeOut · captionBurnIn, 클립 transition {dipBlack|dipWhite, seconds} · color.
     POST /edit/captions/auto(⑥ 대사 줄 시간 → 지금 클립 타임라인, 작업 212 대사 고침이 대본보다 먼저) · POST /edit/captions/srt(SRT 글).
     작업 215d POST·GET /edit/premiere(XML + EDL + 소스 zip). 작업 212 PUT /sound/lines/override(같은 말하는 사람 · 목소리 · 말투 메모).
     상태: ctx.tx = {captions, titles, fadeIn, fadeOut, burnIn}(그림체마다 — editApplyView 가 문서에서 채운다). 실행 취소 스냅숏(editSnapshotOf)에
     tx 도 들어가 자막·타이틀·페이드 바꾸기도 실행 취소 한 단계다. 고른 자막·타이틀 = ctx.txSel {kind, id}(Delete = 지우기, S = 재생헤드에서 자르기).
     ⑦ 안 [대사 고치기 · 영상 다시 만들기]는 ⑥ 함수(lrdRowTools · lrdEditor · lrdSave · lrdSuggest · lrdRedo)를 그대로 쓴다 — ⑥ 쪽은
     soundAlive · lrdReload · soundSchedule 첫 줄이 ctx.inEdit 이면 이 구역으로 넘긴다. 글은 모두 textContent(el). */

  var EDIT_TX_DIPS = [0.25, 0.5, 0.75, 1, 1.5, 2]; // 서버 edit_text.DIP_SECONDS
  var EDIT_TX_DIP_SHARE = 0.9; // 서버 DIP_MAX_SHARE(경계 앞뒤 반씩 — 한쪽 45%)
  var EDIT_TX_EDGES = [0.25, 0.5, 1, 1.5, 2, 3]; // 서버 EDGE_FADE_SECONDS
  var EDIT_TX_DIP_NAME = { dipBlack: "블랙", dipWhite: "화이트" };
  var EDIT_TX_COLOR_KEYS = [["brightness", "밝기"], ["contrast", "대비"], ["saturation", "채도"], ["temperature", "색온도"], ["tint", "색조"]];
  var EDIT_TX_MIN_S = 0.1; // 서버 MIN_ITEM_S
  var EDIT_TX_STYLE = { size: "m", color: "#ffffff", back: "outline", pos: "bottom", italic: false };
  var EDIT_TX_PRESETS = [
    ["basic", "기본 (흰 글씨 · 테두리 · 아래)", { size: "m", color: "#ffffff", back: "outline", pos: "bottom" }],
    ["box", "상자 (흰 글씨 · 검은 상자)", { size: "m", color: "#ffffff", back: "box", pos: "bottom" }],
    ["yellow", "노랑 (노란 글씨 · 테두리)", { size: "m", color: "#ffe14d", back: "outline", pos: "bottom" }],
    ["top", "위 (흰 글씨 · 테두리 · 위)", { size: "m", color: "#ffffff", back: "outline", pos: "top" }],
    ["big", "크게 (큰 글씨 · 상자)", { size: "l", color: "#ffffff", back: "box", pos: "bottom" }]
  ];
  var EDIT_TX_SIZES = [["s", "작게"], ["m", "보통"], ["l", "크게"]];
  var EDIT_TX_TSIZES = [["s", "작게"], ["m", "보통"], ["l", "크게"], ["xl", "아주 크게"]];
  var EDIT_TX_CAP_RATIO = { s: 0.045, m: 0.055, l: 0.07 }; // 서버 CAPTION_SIZES(글자 높이 = 화면 높이 × 값)
  var EDIT_TX_TITLE_RATIO = { s: 0.05, m: 0.075, l: 0.1, xl: 0.14 };
  var EDIT_TX_SNAP_PX = 8; // 끌 때 재생헤드 · 클립 경계 · 다른 자막 끝에 붙는 거리
  var EDIT_TX_POLL_MS = 3000;
  var EDIT_TX_SOUND_TTL_MS = 30000;
  var EDIT_TX_FIX_LINE = "이 대사도 고치기";
  var EDIT_TX_LINE_MENU = "대사 고치기 · 영상 다시 만들기";
  var EDIT_TX_PREM_LABEL = "프리미어 프로젝트로 내보내기 (XML + EDL + 소스)";
  var EDIT_TX_PREM_404 = "서버에 프리미어 내보내기 경로(/edit/premiere)가 아직 연결되지 않았습니다.";
  var EDIT_TX_NOTE = "자막 · 타이틀 · 색 · 블랙/화이트 전환은 미리보기에서 비슷하게만 보입니다. 내보낸 영상에서 정확히 확인하세요.";

  /* ---- 순수 함수(DOM 없음) ---- */

  function editTxNum(value, fallback) {
    var n = Number(value);
    return isFinite(n) ? n : fallback;
  }

  /* 클립의 블랙/화이트 전환 {type, seconds}(없거나 틀리면 type ""). 디졸브는 editDissolveOf 가 본다. */
  function editTxDipOf(clip) {
    var tr = (clip || {}).transition;
    if (!tr || typeof tr !== "object" || !EDIT_TX_DIP_NAME[tr.type]) return { type: "", seconds: 0 };
    var seconds = Number(tr.seconds);
    return EDIT_TX_DIPS.indexOf(seconds) >= 0 ? { type: tr.type, seconds: seconds } : { type: "", seconds: 0 };
  }

  /* 클립 색 보정(−100~100 정수, 0 은 뺀다). 없으면 {}. */
  function editTxColorOf(clip) {
    var raw = (clip || {}).color;
    var out = {};
    if (!raw || typeof raw !== "object") return out;
    EDIT_TX_COLOR_KEYS.forEach(function (pair) {
      var v = Math.round(editTxNum(raw[pair[0]], 0));
      v = Math.max(-100, Math.min(100, v));
      if (v) out[pair[0]] = v;
    });
    return out;
  }

  function editTxHasColor(clip) {
    return Object.keys(editTxColorOf(clip)).length > 0;
  }

  /* editCloneClip 이 부른다: 블랙/화이트 전환 · 색 보정을 사본에 옮긴다(없으면 아무것도 넣지 않는다 — 옛 저장 바디 그대로). */
  function editTxCloneFx(clip, copy) {
    var dip = editTxDipOf(clip);
    if (dip.type) copy.transition = { type: dip.type, seconds: dip.seconds };
    var color = editTxColorOf(clip);
    if (Object.keys(color).length) copy.color = color;
    return copy;
  }

  function editTxDipFits(seconds, a, b) {
    if (!a || !b) return false;
    return Number(seconds) <= EDIT_TX_DIP_SHARE * Math.min(editClipLen(a), editClipLen(b)) + 1e-9;
  }

  /* editCommit 이 부른다(서버 fit_dips 와 같은 규칙): 안 맞는 블랙/화이트 전환은 맞는 가장 긴 값으로, 없으면 컷. 바꿀 것이 없으면 받은 목록. */
  function editTxTidy(clips) {
    var list = Array.isArray(clips) ? clips : [];
    var out = list.slice();
    var changed = false;
    for (var i = 0; i < list.length; i++) {
      var dip = editTxDipOf(list[i]);
      if (!dip.type) continue;
      if (i === list.length - 1) {
        out[i] = editCloneClip(list[i]);
        delete out[i].transition;
        changed = true;
        continue;
      }
      if (editTxDipFits(dip.seconds, list[i], list[i + 1])) continue;
      var fit = EDIT_TX_DIPS.filter(function (s) {
        return editTxDipFits(s, list[i], list[i + 1]);
      });
      out[i] = editCloneClip(list[i]);
      if (fit.length) out[i].transition = { type: dip.type, seconds: fit[fit.length - 1] };
      else delete out[i].transition;
      changed = true;
    }
    return changed ? out : list;
  }

  function editTxStyle(raw) {
    var s = raw && typeof raw === "object" ? raw : {};
    return {
      size: EDIT_TX_CAP_RATIO[s.size] ? s.size : EDIT_TX_STYLE.size,
      color: /^#[0-9a-fA-F]{6}$/.test(String(s.color || "")) ? String(s.color).toLowerCase() : EDIT_TX_STYLE.color,
      back: s.back === "box" ? "box" : "outline",
      pos: s.pos === "top" ? "top" : "bottom",
      italic: s.italic === true
    };
  }

  function editTxCap(raw) {
    var c = raw || {};
    var out = { id: String(c.id), start: editRound3(editTxNum(c.start, 0)), end: editRound3(editTxNum(c.end, 0)),
      text: String(c.text || ""), style: editTxStyle(c.style) };
    if (typeof c.lineId === "string" && c.lineId) out.lineId = c.lineId;
    return out;
  }

  function editTxTitle(raw) {
    var t = raw || {};
    var pos = Number(t.pos);
    return {
      id: String(t.id), start: editRound3(editTxNum(t.start, 0)), end: editRound3(editTxNum(t.end, 0)), text: String(t.text || ""),
      size: EDIT_TX_TITLE_RATIO[t.size] ? t.size : "l", pos: pos >= 1 && pos <= 9 && Math.round(pos) === pos ? pos : 5,
      fadeIn: Math.max(0, Math.min(3, editTxNum(t.fadeIn, 0.5))), fadeOut: Math.max(0, Math.min(3, editTxNum(t.fadeOut, 0.5))),
      box: t.box === true, color: /^#[0-9a-fA-F]{6}$/.test(String(t.color || "")) ? String(t.color).toLowerCase() : "#ffffff"
    };
  }

  /* 문서 → 화면 상태 ctx.tx(없는 키는 빈 목록 · 0 · 굽기 켬). */
  function editTxFromDoc(doc) {
    var d = doc || {};
    function ok(item) {
      return item && typeof item === "object" && typeof item.id === "string";
    }
    return {
      captions: (Array.isArray(d.captions) ? d.captions : []).filter(ok).map(editTxCap),
      titles: (Array.isArray(d.titles) ? d.titles : []).filter(ok).map(editTxTitle),
      fadeIn: EDIT_TX_EDGES.indexOf(Number(d.fadeIn)) >= 0 ? Number(d.fadeIn) : 0,
      fadeOut: EDIT_TX_EDGES.indexOf(Number(d.fadeOut)) >= 0 ? Number(d.fadeOut) : 0,
      burnIn: d.captionBurnIn !== false
    };
  }

  function editTxCloneTx(tx) {
    var t = tx || editTxFromDoc({});
    return { captions: t.captions.map(editTxCap), titles: t.titles.map(editTxTitle), fadeIn: t.fadeIn, fadeOut: t.fadeOut, burnIn: t.burnIn !== false };
  }

  /* PUT /edit 바디에 더한다(있고 기본값이 아닌 키만 — 안 쓴 문서의 저장 바디는 215b 전과 같다). */
  function editTxBody(ctx, body) {
    var tx = (ctx || {}).tx;
    if (!tx) return body;
    if (tx.captions.length) body.captions = tx.captions.map(editTxCap);
    if (tx.titles.length) body.titles = tx.titles.map(editTxTitle);
    if (tx.fadeIn) body.fadeIn = tx.fadeIn;
    if (tx.fadeOut) body.fadeOut = tx.fadeOut;
    if (tx.burnIn === false) body.captionBurnIn = false;
    return body;
  }

  function editTxNewId(prefix, lists) {
    var used = {};
    (lists || []).forEach(function (list) {
      (list || []).forEach(function (item) {
        used[item.id] = true;
      });
    });
    var id = "";
    do {
      id = prefix + Date.now().toString(36) + Math.floor(Math.random() * 46656).toString(36);
    } while (used[id]);
    return id;
  }

  /* 자막·타이틀 옮기기: 길이 그대로 start 를 [0, max − 길이] 안으로. */
  function editTxMoved(item, start, max) {
    var len = Number(item.end) - Number(item.start);
    var a = Math.max(0, Math.min(Math.max(0, Number(max) - len), Number(start)));
    var copy = JSON.parse(JSON.stringify(item));
    copy.start = editRound3(a);
    copy.end = editRound3(a + len);
    return copy;
  }

  /* 끝 다듬기: edge "in"|"out" 를 value 로(최소 길이 EDIT_TX_MIN_S, 0~max). */
  function editTxTrimmed(item, edge, value, max) {
    var copy = JSON.parse(JSON.stringify(item));
    var v = Number(value);
    if (!isFinite(v)) return copy;
    if (edge === "in") copy.start = editRound3(Math.max(0, Math.min(Number(item.end) - EDIT_TX_MIN_S, v)));
    else copy.end = editRound3(Math.min(Math.max(Number(item.start) + EDIT_TX_MIN_S, v), Math.max(Number(max), Number(item.start) + EDIT_TX_MIN_S)));
    return copy;
  }

  /* 재생헤드 t 에서 자르기(같은 글 두 개 — 레이저 도구). 양쪽 모두 최소 길이 이상이어야 한다. 못 자르면 null. */
  function editTxSplitAt(list, index, t, newId) {
    var item = list[index];
    if (!item) return null;
    var cut = editRound3(Number(t));
    if (cut - Number(item.start) < EDIT_TX_MIN_S - 1e-9 || Number(item.end) - cut < EDIT_TX_MIN_S - 1e-9) return null;
    var left = JSON.parse(JSON.stringify(item));
    var right = JSON.parse(JSON.stringify(item));
    left.end = cut;
    right.start = cut;
    right.id = String(newId);
    var next = list.slice();
    next.splice(index, 1, left, right);
    return next;
  }

  /* 붙기: points 중 t 와 tol 초 안에서 가장 가까운 값, 없으면 t. */
  function editTxSnap(t, points, tol) {
    var best = t;
    var gap = tol;
    (points || []).forEach(function (p) {
      var d = Math.abs(Number(p) - t);
      if (d <= gap) {
        gap = d;
        best = Number(p);
      }
    });
    return best;
  }

  /* 붙을 자리: 0 · 끝 · 재생헤드 · 클립 경계 · 다른 자막/타이틀 끝(skipId 빼고). */
  function editTxSnapPoints(clips, tx, t, skipId) {
    var info = editStarts(clips || []);
    var points = [0, info.total, Number(t) || 0];
    (clips || []).forEach(function (clip, i) {
      points.push(info.starts[i], info.starts[i] + editClipLen(clip));
    });
    [(tx || {}).captions || [], (tx || {}).titles || []].forEach(function (list) {
      list.forEach(function (item) {
        if (item.id !== skipId) points.push(Number(item.start), Number(item.end));
      });
    });
    return points;
  }

  function editTxActive(list, t) {
    return (list || []).filter(function (item) {
      return Number(item.start) <= t + 1e-6 && t < Number(item.end) - 1e-6;
    });
  }

  /* 미리보기 CSS filter(밝기·대비·채도 — 서버 eq 와 비슷하게). 색온도·색조는 editTxTintOf 겹침 층. */
  function editTxCssFilter(color) {
    var c = color || {};
    var parts = [];
    if (c.brightness) parts.push("brightness(" + (1 + (c.brightness / 100) * 0.4).toFixed(3) + ")");
    if (c.contrast) parts.push("contrast(" + (1 + (c.contrast / 100) * 0.5).toFixed(3) + ")");
    if (c.saturation) parts.push("saturate(" + (1 + c.saturation / 100).toFixed(3) + ")");
    return parts.join(" ");
  }

  /* 색온도(따뜻 = 주황, 차가움 = 파랑) · 색조(+ 자홍, − 초록) 겹침 층 [{color, alpha}](soft-light). */
  function editTxTintOf(color) {
    var c = color || {};
    var out = [];
    if (c.temperature) out.push({ color: c.temperature > 0 ? "rgb(255,140,30)" : "rgb(40,120,255)", alpha: Math.abs(c.temperature) / 100 * 0.45 });
    if (c.tint) out.push({ color: c.tint > 0 ? "rgb(255,40,200)" : "rgb(40,220,80)", alpha: Math.abs(c.tint) / 100 * 0.4 });
    return out;
  }

  /* 시각 t 의 블랙/화이트 전환 · 시작/끝 페이드 덮개 {tone, alpha}(alpha 0 = 없음). */
  function editTxFadeAt(clips, tx, t) {
    var info = editStarts(clips || []);
    var best = { tone: "black", alpha: 0 };
    for (var i = 0; i + 1 < (clips || []).length; i++) {
      var dip = editTxDipOf(clips[i]);
      if (!dip.type) continue;
      var half = dip.seconds / 2;
      var gap = Math.abs(t - info.starts[i + 1]);
      if (gap < half) {
        var a = 1 - gap / half;
        if (a > best.alpha) best = { tone: dip.type === "dipWhite" ? "white" : "black", alpha: a };
      }
    }
    var x = tx || {};
    if (x.fadeIn && t < x.fadeIn) best = { tone: "black", alpha: Math.max(best.alpha, 1 - t / x.fadeIn) };
    if (x.fadeOut && t > info.total - x.fadeOut) best = { tone: "black", alpha: Math.max(best.alpha, Math.min(1, (t - (info.total - x.fadeOut)) / x.fadeOut)) };
    return best;
  }

  function editTxTitleAlpha(title, t) {
    var a = 1;
    var fin = Number(title.fadeIn) || 0;
    var fout = Number(title.fadeOut) || 0;
    if (fin > 0) a = Math.min(a, (t - Number(title.start)) / fin);
    if (fout > 0) a = Math.min(a, (Number(title.end) - t) / fout);
    return Math.max(0, Math.min(1, a));
  }

  function editTxPremText(p) {
    var e = p || {};
    if (e.status === "queued") return "프리미어 프로젝트: 대기 중…";
    if (e.status === "running") return "프리미어 프로젝트: 소스를 묶는 중…";
    if (e.status === "done") return "프리미어 프로젝트 준비됨" + (e.matchesDoc === false ? " (편집이 바뀌었습니다 — 다시 내보내기)" : "");
    if (e.status === "failed") return "프리미어 프로젝트를 만들지 못했습니다: " + (e.error || "알 수 없는 오류");
    return "";
  }

  /* 시각 [a, b] 와 겹치는 클립 번호. */
  function editTxClipsIn(clips, a, b) {
    var info = editStarts(clips || []);
    var out = [];
    (clips || []).forEach(function (clip, i) {
      var s = info.starts[i];
      if (s < b - 1e-6 && s + editClipLen(clip) > a + 1e-6) out.push(i);
    });
    return out;
  }

  /* ---- 상태 · 실행 취소 ---- */

  /* editApplyView 가 부른다. */
  function editTxApply(ctx, view) {
    ctx.tx = editTxFromDoc((view || {}).doc);
    ctx.txInfo = (view || {}).textFx || {};
    ctx.txSel = null;
    ctx.txColorLive = null;
    ctx.txToolsSig = "";
    if (ctx.txPrem) {
      if (ctx.txPrem.timer) window.clearTimeout(ctx.txPrem.timer);
      ctx.txPrem.timer = 0;
      ctx.txPrem.state = null; // 그림체가 바뀌면 그 그림체 묶음을 다시 읽는다
      ctx.txPrem.loaded = false;
      ctx.txPrem.error = "";
    }
  }

  /* editSnapshotOf 가 부른다(지금 열린 ⑦ 의 tx 도 함께). */
  function editTxSnapOf(ctx, snap) {
    snap.tx = editTxCloneTx(ctx.tx);
    return snap;
  }

  /* editUndoRedo 가 부른다. */
  function editTxRestore(ctx, snap) {
    if (!snap || !snap.tx) return;
    ctx.tx = editTxCloneTx(snap.tx);
    if (ctx.txSel && !editTxFind(ctx, ctx.txSel.kind, ctx.txSel.id)) ctx.txSel = null;
  }

  function editTxList(ctx, kind) {
    return kind === "title" ? ctx.tx.titles : ctx.tx.captions;
  }

  function editTxFind(ctx, kind, id) {
    var list = editTxList(ctx, kind);
    for (var i = 0; i < list.length; i++) {
      if (list[i].id === id) return { index: i, item: list[i] };
    }
    return null;
  }

  /* tx 를 고치는 한 단계(실행 취소 한 단계 · 다시 그리기 · 저장). mutate(사본)가 false 를 주면 하지 않는다. */
  function editTxCommit(ctx, mutate) {
    if (!editAlive(ctx) || ctx.busy || !ctx.tx) return false;
    var before = editSnapshotOf(ctx.variant, ctx.clips);
    var next = editTxCloneTx(ctx.tx);
    if (mutate(next) === false) return false;
    next.captions.sort(function (a, b) {
      return a.start - b.start || a.end - b.end;
    });
    next.titles.sort(function (a, b) {
      return a.start - b.start || a.end - b.end;
    });
    ctx.tx = next;
    editCommit(ctx, editCloneClips(ctx.clips), { before: before });
    return true;
  }

  /* editDo 가 먼저 부른다: 자막·타이틀을 골랐으면 Delete = 지우기, S = 재생헤드에서 자르기. 처리했으면 true. */
  function editTxDo(ctx, action) {
    var sel = ctx.txSel;
    if (!sel || !ctx.tx || (action !== "delete" && action !== "split")) return false;
    var found = editTxFind(ctx, sel.kind, sel.id);
    if (!found) {
      ctx.txSel = null;
      return false;
    }
    if (action === "delete") {
      ctx.txSel = null;
      editTxCommit(ctx, function (tx) {
        editTxList({ tx: tx }, sel.kind).splice(found.index, 1);
      });
      return true;
    }
    var t = ctx.t;
    if (!(t > found.item.start && t < found.item.end)) return false; // 재생헤드가 밖이면 클립 자르기(원래 동작)
    var lists = [ctx.tx.captions, ctx.tx.titles];
    var newId = editTxNewId(sel.kind === "title" ? "t" : "s", lists);
    var ok = editTxCommit(ctx, function (tx) {
      var next = editTxSplitAt(editTxList({ tx: tx }, sel.kind), found.index, t, newId);
      if (!next) return false;
      if (sel.kind === "title") tx.titles = next;
      else tx.captions = next;
    });
    if (!ok) editNote(ctx, "자막이 너무 짧아 자를 수 없습니다.");
    return true;
  }

  function editTxSelect(ctx, kind, id) {
    ctx.txSel = kind ? { kind: kind, id: id } : null;
    if (kind && ctx.mixSel) {
      ctx.mixSel = null; // 작업 220: 고른 것은 하나 — 자막을 고르면 고른 소리 아이템은 풀린다(Delete 가 방금 고른 것을 지운다)
      ctx.mixKey = null;
      editMixRender(ctx);
    }
    (ctx.txEls || []).forEach(function (rec) {
      var on = !!ctx.txSel && rec.kind === ctx.txSel.kind && rec.id === ctx.txSel.id;
      rec.el.classList[on ? "add" : "remove"]("is-selected");
    });
    editTxRenderTools(ctx);
  }

  /* ---- 타임라인: 자막 · V2 타이틀 트랙 ---- */

  /* editBuild 가 부른다: 자막 줄 · V2 타이틀 줄과 왼쪽 이름표. 끝에 붙이고(앞 요소 자리는 그대로) 눈금자 바로 아래에 보이는 것은
     CSS order(.has-tx)가 맡는다. */
  function editTxTracks(ctx, labels) {
    var d = ctx.dom;
    d.txCapLane = el("div", "vnew-edit-track vnew-edit-txlane is-captions");
    d.txTitleLane = el("div", "vnew-edit-track vnew-edit-txlane is-titles");
    [d.txCapLane, d.txTitleLane].forEach(function (lane) {
      lane.addEventListener("pointerdown", function (event) {
        if (event && event.target === lane && editAlive(ctx)) editTxSelect(ctx, null);
      });
      d.content.appendChild(lane);
    });
    var titleLabel = el("span", "vnew-edit-label is-tx is-titles", "V2");
    titleLabel.title = "V2 타이틀(영상 위 글)";
    labels.appendChild(el("span", "vnew-edit-label is-tx is-captions", "자막"));
    labels.appendChild(titleLabel);
    labels.classList.add("has-tx");
    d.content.classList.add("has-tx");
  }

  /* editRenderTimeline 이 부른다: 자막·타이틀 상자 · 클립 오른쪽 누르기 메뉴 · 전환 손잡이 오른쪽 누르기(새로 그린 요소에만 한 번). */
  function editTxRender(ctx) {
    if (!ctx.tx || !ctx.dom.txCapLane) return;
    (ctx.clipEls || []).forEach(function (parts) {
      parts.box.addEventListener("pointerdown", function () {
        if (ctx.txSel) editTxSelect(ctx, null);
      });
      parts.box.addEventListener("contextmenu", function (event) {
        if (event && event.preventDefault) event.preventDefault();
        if (!editAlive(ctx) || ctx.busy) return;
        editSelect(ctx, parts.index);
        editTxClipMenu(ctx, parts.index, event);
      });
    });
    (ctx.trEls || []).forEach(function (item, i) {
      item.btn.addEventListener("contextmenu", function (event) {
        if (event && event.preventDefault) event.preventDefault();
        editTrOpen(ctx, i, item.btn); // 작업 215b: 오른쪽 누르기도 전환 메뉴
      });
    });
    editTxRenderItems(ctx);
    editTxPlaceFx(ctx);
    editTxRenderTools(ctx);
    editTxExportPaint(ctx);
  }

  /* 자막 줄 · V2 타이틀 줄의 상자들. */
  function editTxRenderItems(ctx) {
    var d = ctx.dom;
    if (!ctx.tx || !d.txCapLane) return;
    clear(d.txCapLane);
    clear(d.txTitleLane);
    ctx.txEls = [];
    ctx.tx.captions.forEach(function (cap) {
      d.txCapLane.appendChild(editTxItemEl(ctx, "caption", cap));
    });
    ctx.tx.titles.forEach(function (title) {
      d.txTitleLane.appendChild(editTxItemEl(ctx, "title", title));
    });
  }

  function editTxPx(ctx, seconds) {
    return Math.round(Number(seconds) * ctx.pps * 10) / 10 + "px";
  }

  function editTxItemEl(ctx, kind, item) {
    var node = el("div", "vnew-edit-txitem is-" + kind + (ctx.txSel && ctx.txSel.kind === kind && ctx.txSel.id === item.id ? " is-selected" : ""));
    var italic = kind === "caption" && item.style && item.style.italic;
    node.style.left = editTxPx(ctx, item.start);
    node.style.width = editTxPx(ctx, Math.max(0.01, item.end - item.start));
    var label = el("span", "vnew-edit-txitem-text" + (italic ? " is-italic" : ""), String(item.text || "").replace(/\n/g, " / "));
    node.appendChild(label);
    node.title = (kind === "title" ? "타이틀: " : "자막: ") + item.text + " · " + editTimeText(item.start, true) + "–" + editTimeText(item.end, true) +
      " — 끌어 옮기기 · 양 끝 끌어 다듬기 · 두 번 눌러 글 고치기 · 오른쪽 누르기 메뉴";
    var inEdge = el("div", "vnew-edit-txedge is-in");
    var outEdge = el("div", "vnew-edit-txedge is-out");
    node.appendChild(inEdge);
    node.appendChild(outEdge);
    var rec = { kind: kind, id: item.id, el: node };
    ctx.txEls.push(rec);
    node.addEventListener("pointerdown", function (event) {
      editTxPointer(ctx, kind, item.id, "move", node, event);
    });
    inEdge.addEventListener("pointerdown", function (event) {
      if (event && event.stopPropagation) event.stopPropagation();
      editTxPointer(ctx, kind, item.id, "in", node, event);
    });
    outEdge.addEventListener("pointerdown", function (event) {
      if (event && event.stopPropagation) event.stopPropagation();
      editTxPointer(ctx, kind, item.id, "out", node, event);
    });
    node.addEventListener("dblclick", function () {
      editTxEditText(ctx, kind, item.id, node);
    });
    node.addEventListener("contextmenu", function (event) {
      if (event && event.preventDefault) event.preventDefault();
      editTxSelect(ctx, kind, item.id);
      editTxItemMenu(ctx, kind, item.id, event);
    });
    return node;
  }

  /* 누르기 = 고르기, 끌기 = 옮기기(move) · 끝 다듬기(in/out). 재생헤드 · 클립 경계 · 다른 자막 끝에 붙는다(EDIT_TX_SNAP_PX). */
  function editTxPointer(ctx, kind, id, mode, node, event) {
    if (!editPointerOk(ctx, event)) return;
    var found = editTxFind(ctx, kind, id);
    if (!found) return;
    editTxSelect(ctx, kind, id);
    var item = found.item;
    var max = editStarts(ctx.clips).total;
    var points = editTxSnapPoints(ctx.clips, ctx.tx, ctx.t, id);
    var tol = EDIT_TX_SNAP_PX / (ctx.pps || 100);
    var next = item;
    editTrack(ctx, node, event, {
      threshold: 3,
      move: function (dx) {
        var shift = dx / (ctx.pps || 100);
        if (mode === "move") {
          var start = item.start + shift;
          var snapA = editTxSnap(start, points, tol);
          var snapB = editTxSnap(start + (item.end - item.start), points, tol) - (item.end - item.start);
          start = Math.abs(snapA - start) <= Math.abs(snapB - start) ? snapA : snapB;
          next = editTxMoved(item, start, max);
        } else {
          var base = mode === "in" ? item.start : item.end;
          next = editTxTrimmed(item, mode, editTxSnap(base + shift, points, tol), max);
        }
        node.style.left = editTxPx(ctx, next.start);
        node.style.width = editTxPx(ctx, Math.max(0.01, next.end - next.start));
      },
      end: function (dx, moved) {
        if (!moved || (next.start === item.start && next.end === item.end)) {
          editTxRenderItems(ctx);
          return;
        }
        editTxCommit(ctx, function (tx) {
          var list = editTxList({ tx: tx }, kind);
          var at = editTxFind({ tx: tx }, kind, id);
          if (!at) return false;
          list[at.index] = kind === "title" ? editTxTitle(next) : editTxCap(next);
        });
      }
    });
  }

  /* 글 고치기(자막 위에 뜨는 입력 칸). 자막이 ⑥ 대사 줄이면 [이 대사도 고치기](기본 켬) — ⑥ 대사 고침(작업 212)으로도 저장한다. */
  function editTxEditText(ctx, kind, id, node) {
    if (!editAlive(ctx) || ctx.busy) return;
    var found = editTxFind(ctx, kind, id);
    if (!found) return;
    editTxCloseEditor(ctx);
    var item = found.item;
    var box = el("div", "vnew-edit-txeditor");
    box.setAttribute("role", "dialog");
    box.setAttribute("aria-label", kind === "title" ? "타이틀 글 고치기" : "자막 글 고치기");
    var input = el("textarea", "vnew-edit-txeditor-text");
    input.value = item.text;
    input.rows = 2;
    input.maxLength = kind === "title" ? 120 : 200;
    box.appendChild(input);
    var check = null;
    if (kind === "caption" && item.lineId) {
      var label = el("label", "vnew-edit-txeditor-check");
      check = el("input", "vnew-edit-txeditor-line");
      check.type = "checkbox";
      check.checked = true;
      label.appendChild(check);
      label.appendChild(el("span", "", EDIT_TX_FIX_LINE));
      label.title = "⑥ 사운드의 그 대사(작업 212 대사 고침)도 같은 글로 저장합니다 — 말하는 사람 · 목소리 · 말투 메모는 그대로";
      box.appendChild(label);
    }
    var tools = el("div", "vnew-edit-txeditor-tools");
    var ok = el("button", "primary-button vnew-edit-txeditor-ok", "확인");
    ok.type = "button";
    var cancel = el("button", "outline-button vnew-edit-txeditor-cancel", "취소");
    cancel.type = "button";
    tools.appendChild(ok);
    tools.appendChild(cancel);
    box.appendChild(tools);
    function done(save) {
      if (ctx.txEditor !== rec) return;
      editTxCloseEditor(ctx);
      if (save) editTxSetText(ctx, kind, id, input.value, !!(check && check.checked));
    }
    var rec = { el: box, done: done };
    ok.addEventListener("click", function () {
      done(true);
    });
    cancel.addEventListener("click", function () {
      done(false);
    });
    input.addEventListener("keydown", function (event) {
      if (event && event.key === "Enter" && !event.shiftKey) {
        if (event.preventDefault) event.preventDefault();
        done(true);
      } else if (event && event.key === "Escape") {
        done(false);
      }
    });
    ctx.txEditor = rec;
    ctx.root.appendChild(box);
    var rect = node && node.getBoundingClientRect ? node.getBoundingClientRect() : null;
    if (rect) {
      box.style.left = Math.max(8, Math.round(Number(rect.left) || 0)) + "px";
      box.style.top = Math.max(8, Math.round((Number(rect.bottom) || 0) + 4)) + "px";
    }
    if (typeof input.focus === "function") {
      try {
        input.focus({ preventScroll: true });
      } catch (error) {
        /* 초점을 줄 수 없다 */
      }
    }
  }

  function editTxCloseEditor(ctx) {
    var rec = ctx.txEditor;
    if (!rec) return;
    ctx.txEditor = null;
    if (rec.el.parentNode) rec.el.parentNode.removeChild(rec.el);
  }

  /* 글을 바꾼다(실행 취소 한 단계). alsoLine 이면 ⑥ 대사 고침도 저장(editTxSaveLine). */
  function editTxSetText(ctx, kind, id, raw, alsoLine) {
    var text = String(raw || "").replace(/\r\n?/g, "\n").split("\n").map(function (s) {
      return s.trim();
    }).filter(Boolean).slice(0, 3).join("\n");
    if (!text) {
      editNote(ctx, "글이 비어 있으면 바꾸지 않습니다. 지우려면 Delete 를 누르세요.");
      return;
    }
    var found = editTxFind(ctx, kind, id);
    if (!found) return;
    if (found.item.text !== text) {
      editTxCommit(ctx, function (tx) {
        var at = editTxFind({ tx: tx }, kind, id);
        if (!at) return false;
        editTxList({ tx: tx }, kind)[at.index].text = text;
      });
    }
    if (kind === "caption" && alsoLine && found.item.lineId) editTxSaveLine(ctx, found.item.lineId, text);
  }

  function editTxSoundView(ctx, fresh) {
    var cache = ctx.txSound;
    if (!fresh && cache && cache.variant === ctx.variant && Date.now() - cache.at < EDIT_TX_SOUND_TTL_MS) return Promise.resolve(cache.view);
    return api(soundPath() + "?variant=" + encodeURIComponent(ctx.variant)).then(function (view) {
      ctx.txSound = { variant: ctx.variant, at: Date.now(), view: view || {} };
      return view || {};
    });
  }

  /* 작업 212 대사 고침 바디: 새 글만 바꾸고 말하는 사람 · 목소리(속마음) · 말투 메모는 지금 고침(없으면 대본 줄) 그대로. 순수 함수. */
  function editTxOverrideBody(view, variant, lineId, text) {
    var line = ((view || {}).lines || []).filter(function (l) {
      return l && l.id === lineId;
    })[0] || {};
    var entry = ((((view || {}).lineRedo || {}).lines) || {})[lineId] || {};
    var ov = entry.override || null;
    return {
      variant: variant, lineId: lineId, textKo: text,
      speaker: ov ? String(ov.speaker || "") : String(line.speaker || ""),
      thought: ov ? ov.thought === true : line.voice === "thought",
      deliveryKo: ov ? String(ov.deliveryKo || "") : ""
    };
  }

  function editTxSaveLine(ctx, lineId, text) {
    var variant = ctx.variant;
    editNote(ctx, "⑥ 대사도 고치는 중…");
    return editTxSoundView(ctx, false)
      .then(function (view) {
        return api(soundPath() + "/lines/override", { method: "PUT", body: JSON.stringify(editTxOverrideBody(view, variant, lineId, text)) });
      })
      .then(function (view) {
        ctx.txSound = { variant: variant, at: Date.now(), view: view || {} };
        if (editAlive(ctx)) editNote(ctx, "⑥ 대사 목록도 새 글로 고쳤습니다.");
      })
      .catch(function (err) {
        if (editAlive(ctx)) editShowError(ctx, "⑥ 대사는 고치지 못했습니다: " + lrdErr(err));
      });
  }

  /* ---- 오른쪽 누르기 메뉴 ---- */

  /* 화면에 고정한 작은 메뉴(전환 메뉴와 같은 모양). items [{text, title?, disabled?, run()}]. 바깥 누르기 · Esc · 스크롤이면 닫는다. */
  function editTxMenu(ctx, event, title, items) {
    editTxMenuClose(ctx);
    editTrClose(ctx);
    var menu = el("div", "vnew-edit-trmenu vnew-edit-txmenu");
    menu.setAttribute("role", "menu");
    menu.appendChild(el("p", "vnew-edit-trmenu-title", title));
    items.forEach(function (it) {
      var row = el("span", "vnew-edit-trmenu-row");
      var btn = el("button", "vnew-edit-trmenu-item", it.text);
      btn.type = "button";
      btn.setAttribute("role", "menuitem");
      if (it.title) btn.title = it.title;
      btn.disabled = !!it.disabled;
      btn.addEventListener("click", function () {
        editTxMenuClose(ctx);
        it.run();
      });
      row.appendChild(btn);
      menu.appendChild(row);
    });
    ctx.root.appendChild(menu);
    var vw = Number(window.innerWidth) || 0;
    var vh = Number(window.innerHeight) || 0;
    var x = Number((event || {}).clientX) || 0;
    var y = Number((event || {}).clientY) || 0;
    var mw = Number(menu.offsetWidth) || 220;
    var mh = Number(menu.offsetHeight) || 160;
    menu.style.left = Math.round(vw ? Math.max(8, Math.min(vw - mw - 8, x)) : x) + "px";
    menu.style.top = Math.round(vh ? Math.max(8, Math.min(vh - mh - 8, y)) : y) + "px";
    var onDoc = function (ev) {
      if (!editInside(menu, ev && ev.target)) editTxMenuClose(ctx);
    };
    var onKey = function (ev) {
      if (ev && ev.key === "Escape") editTxMenuClose(ctx);
    };
    document.addEventListener("pointerdown", onDoc, true);
    document.addEventListener("keydown", onKey, true);
    ctx.txMenu = { el: menu, onDoc: onDoc, onKey: onKey };
    return menu;
  }

  function editTxMenuClose(ctx) {
    var m = ctx && ctx.txMenu;
    if (!m) return;
    ctx.txMenu = null;
    document.removeEventListener("pointerdown", m.onDoc, true);
    document.removeEventListener("keydown", m.onKey, true);
    if (m.el.parentNode) m.el.parentNode.removeChild(m.el);
  }

  function editTxQuote(text) {
    var t = String(text || "").replace(/\s+/g, " ");
    return "“" + (t.length > 24 ? t.slice(0, 23) + "…" : t) + "”";
  }

  /* 자막 메뉴: 글 고치기 · 대사 고치기 · 영상 다시 만들기(⑥ 줄일 때) · 재생헤드에서 자르기 · 지우기. */
  function editTxItemMenu(ctx, kind, id, event) {
    var found = editTxFind(ctx, kind, id);
    if (!found) return;
    var item = found.item;
    var items = [{ text: "글 고치기", run: function () {
      var rec = (ctx.txEls || []).filter(function (r) {
        return r.kind === kind && r.id === id;
      })[0];
      editTxEditText(ctx, kind, id, rec ? rec.el : null);
    } }];
    if (kind === "caption" && item.lineId) {
      items.push({ text: EDIT_TX_LINE_MENU + " — " + editTxQuote(item.text), run: function () {
        var ids = editTxClipsIn(ctx.clips, item.start, item.end).map(function (i) {
          return ctx.clips[i].id;
        });
        editTxLineOpen(ctx, item.lineId, ids);
      } });
    }
    items.push({ text: "재생헤드에서 자르기 (S)", disabled: !(ctx.t > item.start && ctx.t < item.end), run: function () {
      editTxSelect(ctx, kind, id);
      editTxDo(ctx, "split");
    } });
    items.push({ text: "지우기 (Delete)", run: function () {
      editTxSelect(ctx, kind, id);
      editTxDo(ctx, "delete");
    } });
    editTxMenu(ctx, event, kind === "title" ? "타이틀" : "자막", items);
  }

  /* 클립 메뉴: 그 클립 안 대사마다 [대사 고치기 · 영상 다시 만들기] · 색 보정 원래대로 · 같은 테이크 모두에 적용. 자막이 없으면 서버에 대사 자리를 묻는다. */
  function editTxClipMenu(ctx, index, event) {
    var clip = ctx.clips[index];
    if (!clip) return;
    var info = editStarts(ctx.clips);
    var a = info.starts[index];
    var b = a + editClipLen(clip);
    function lineItems(caps) {
      var seen = {};
      return caps.filter(function (c) {
        if (!c.lineId || seen[c.lineId] || !(c.start < b - 1e-6 && c.end > a + 1e-6)) return false;
        seen[c.lineId] = true;
        return true;
      }).map(function (c) {
        return { text: EDIT_TX_LINE_MENU + " — " + editTxQuote(c.text), run: function () {
          editTxLineOpen(ctx, c.lineId, [clip.id]);
        } };
      });
    }
    function show(lines, note) {
      var items = lines.slice();
      if (!items.length) items.push({ text: note || "이 클립에서 찾은 대사가 없습니다", disabled: true, run: function () {} });
      items.push({ text: "색 보정 원래대로", disabled: !editTxHasColor(clip), run: function () {
        editTxColorReset(ctx);
      } });
      items.push({ text: "같은 테이크 모두에 이 색 적용", disabled: !editTxHasColor(clip), run: function () {
        editTxColorSameTake(ctx);
      } });
      editTxMenu(ctx, event, "클립 " + (index + 1), items);
    }
    var mine = lineItems(ctx.tx.captions);
    if (mine.length) {
      show(mine);
      return;
    }
    show([], "대사 찾는 중…");
    api(editPath(ctx) + "/captions/auto", { method: "POST", body: JSON.stringify({ variant: ctx.variant, clips: editCloneClips(ctx.clips) }) })
      .then(function (res) {
        if (!editAlive(ctx) || !ctx.txMenu) return;
        show(lineItems(((res || {}).captions || []).map(editTxCap)));
      })
      .catch(function (err) {
        if (editAlive(ctx) && ctx.txMenu) show([], "대사를 찾지 못했습니다: " + errMessage(err));
      });
  }

  /* ---- 전환: 블랙/화이트(전환 메뉴 아래 줄) · 손잡이 표시 ---- */

  /* editTrOpen 이 부른다: [블랙으로 0.5초] … [화이트로 …] 줄. 지금 값은 is-on, 맞지 않는 길이는 막는다. */
  function editTxTrRows(ctx, index, menu) {
    var a = ctx.clips[index];
    var b = ctx.clips[index + 1];
    if (!a || !b) return;
    var now = editTxDipOf(a);
    if (now.type) {
      Array.prototype.slice.call(menu.children || []).forEach(function (row) {
        var first = (row.children || [])[0];
        if (first && first.classList && !editDissolveOf(a)) {
          first.classList.remove("is-on");
          first.setAttribute("aria-checked", "false");
        }
      });
    }
    ["dipBlack", "dipWhite"].forEach(function (type) {
      var row = el("div", "vnew-edit-trmenu-row vnew-edit-txdip");
      row.appendChild(el("span", "vnew-edit-txdip-name", EDIT_TX_DIP_NAME[type] + "로 사라졌다 나타나기"));
      EDIT_TX_DIPS.forEach(function (seconds) {
        var fits = editTxDipFits(seconds, a, b);
        var on = now.type === type && now.seconds === seconds;
        var item = el("button", "vnew-edit-trmenu-item vnew-edit-txdip-item" + (on ? " is-on" : ""), seconds + "초");
        item.type = "button";
        item.setAttribute("role", "menuitemradio");
        item.setAttribute("aria-checked", on ? "true" : "false");
        item.setAttribute("aria-label", EDIT_TX_DIP_NAME[type] + " 전환 " + seconds + "초");
        item.disabled = !fits;
        item.title = fits ? "경계 앞 " + seconds / 2 + "초는 " + EDIT_TX_DIP_NAME[type] + "로 사라지고 뒤 " + seconds / 2 + "초는 나타납니다 — 길이는 그대로" : EDIT_TR_SHORT;
        item.addEventListener("click", function () {
          if (fits) editTxSetDip(ctx, index, type, seconds);
        });
        row.appendChild(item);
      });
      menu.appendChild(row);
    });
  }

  /* index 번째 클립 → 다음 클립 전환을 블랙/화이트(type "" = 컷)로. 실행 취소 한 단계. */
  function editTxSetDip(ctx, index, type, seconds) {
    editTrClose(ctx);
    if (!editAlive(ctx) || ctx.busy) return;
    var a = ctx.clips[index];
    var b = ctx.clips[index + 1];
    if (!a || !b) return;
    if (type && !editTxDipFits(seconds, a, b)) {
      editNote(ctx, EDIT_TR_SHORT + ".");
      return;
    }
    var clip = editCloneClip(a);
    if (type) clip.transition = { type: type, seconds: seconds };
    else delete clip.transition;
    var next = ctx.clips.slice();
    next[index] = clip;
    editCommit(ctx, next, {});
  }

  /* editPlaceFx 가 부른다: 블랙/화이트 전환 손잡이 색 · 이름. */
  function editTxPlaceFx(ctx) {
    (ctx.trEls || []).forEach(function (item, i) {
      var dip = editTxDipOf(ctx.clips[i]);
      item.btn.classList.remove("is-dip-black");
      item.btn.classList.remove("is-dip-white");
      if (!dip.type) return;
      item.btn.classList.add(dip.type === "dipWhite" ? "is-dip-white" : "is-dip-black");
      var title = "클립 " + (i + 1) + " → " + (i + 2) + " 전환: " + EDIT_TX_DIP_NAME[dip.type] + " " + dip.seconds + "초 — 눌러 바꾸기";
      if (item.btn.title !== title) {
        item.btn.title = title;
        item.btn.setAttribute("aria-label", title);
      }
    });
  }

  /* ---- 미리보기(재생 화면 위) ---- */

  /* editBuild 가 부른다: 색온도·색조 층 · 블랙/화이트 덮개 · 타이틀 · 자막 층. */
  function editTxScreen(ctx, screen) {
    var d = ctx.dom;
    d.txScreen = screen;
    d.txWarm = el("div", "vnew-edit-txlayer is-tint");
    d.txTint = el("div", "vnew-edit-txlayer is-tint");
    d.txFade = el("div", "vnew-edit-txlayer is-fade");
    d.txTitles = el("div", "vnew-edit-txlayer is-titles");
    d.txCap = el("div", "vnew-edit-txcap");
    [d.txWarm, d.txTint, d.txFade, d.txTitles, d.txCap].forEach(function (n) {
      n.setAttribute("aria-hidden", "true");
      screen.appendChild(n);
    });
  }

  function editTxSetStyle(node, key, value) {
    if (node && node.style[key] !== value) node.style[key] = value;
  }

  /* editPlaceHead 가 부른다(재생헤드가 움직일 때마다): 색 · 전환 덮개 · 자막 · 타이틀. 바뀐 값만 쓴다. */
  function editTxPreview(ctx) {
    var d = ctx.dom;
    if (!d.txScreen || !ctx.tx) return;
    var t = ctx.t;
    var loc = editLocate(ctx.clips, t);
    var live = ctx.txColorLive;
    var color = loc ? (live && live.index === loc.index ? live.color : editTxColorOf(loc.clip)) : {};
    var filter = editTxCssFilter(color);
    (ctx.vids || []).forEach(function (video) {
      editTxSetStyle(video, "filter", filter);
    });
    var tints = editTxTintOf(color);
    [d.txWarm, d.txTint].forEach(function (layer, i) {
      var tint = color && (i === 0 ? (color.temperature ? tints[0] : null) : (color.tint ? tints[tints.length - 1] : null));
      editTxSetStyle(layer, "background", tint ? tint.color : "transparent");
      editTxSetStyle(layer, "opacity", tint ? String(Math.round(tint.alpha * 1000) / 1000) : "0");
    });
    var fade = editTxFadeAt(ctx.clips, ctx.tx, t);
    editTxSetStyle(d.txFade, "background", fade.tone === "white" ? "#ffffff" : "#000000");
    editTxSetStyle(d.txFade, "opacity", String(Math.round(fade.alpha * 1000) / 1000));
    var h = Number(d.txScreen.clientHeight) || 270;
    var caps = editTxActive(ctx.tx.captions, t);
    var capSig = caps.map(function (c) {
      return c.id + "|" + c.text + "|" + JSON.stringify(c.style);
    }).join("\n") + "|" + h;
    if (capSig !== ctx.txCapSig) {
      ctx.txCapSig = capSig;
      clear(d.txCap);
      caps.forEach(function (cap) {
        var line = el("span", "vnew-edit-txcap-line is-" + cap.style.back + (cap.style.italic ? " is-italic" : ""), cap.text);
        line.style.color = cap.style.color;
        line.style.fontSize = Math.max(8, Math.round(h * EDIT_TX_CAP_RATIO[cap.style.size])) + "px";
        d.txCap.appendChild(line);
      });
      d.txCap.className = "vnew-edit-txcap is-" + ((caps[0] || {}).style || {}).pos;
    }
    var titles = editTxActive(ctx.tx.titles, t);
    var titleSig = titles.map(function (x) {
      return JSON.stringify(x);
    }).join("\n") + "|" + h;
    if (titleSig !== ctx.txTitleSig) {
      ctx.txTitleSig = titleSig;
      clear(d.txTitles);
      ctx.txTitleEls = titles.map(function (title) {
        var node = el("span", "vnew-edit-txtitle is-pos" + title.pos + (title.box ? " is-box" : ""), title.text);
        node.style.color = title.color;
        node.style.fontSize = Math.max(8, Math.round(h * EDIT_TX_TITLE_RATIO[title.size])) + "px";
        d.txTitles.appendChild(node);
        return { el: node, title: title };
      });
    }
    (ctx.txTitleEls || []).forEach(function (rec) {
      editTxSetStyle(rec.el, "opacity", String(Math.round(editTxTitleAlpha(rec.title, t) * 1000) / 1000));
    });
  }

  /* ---- 도구 칸(자막 · 타이틀 · 색 · 시작/끝 페이드) ---- */

  function editTxSelectEl(className, options, value, onChange, label) {
    var select = el("select", className);
    options.forEach(function (pair) {
      var option = el("option", "", pair[1]);
      option.value = String(pair[0]);
      select.appendChild(option);
    });
    select.value = String(value);
    if (label) select.setAttribute("aria-label", label);
    select.addEventListener("change", function () {
      onChange(select.value);
    });
    return select;
  }

  function editTxField(text, field) {
    var label = el("label", "vnew-edit-txfield");
    label.appendChild(el("span", "vnew-edit-txfield-label", text));
    label.appendChild(field);
    return label;
  }

  /* editBuild 가 도구줄에 붙인다. 내용은 editTxRenderTools 가 고른 것에 맞춰 다시 그린다. */
  function editTxToolsBox(ctx) {
    var d = ctx.dom;
    d.txBox = el("div", "vnew-edit-tx");
    return d.txBox;
  }

  function editTxButton(className, text, title, onClick) {
    var btn = el("button", "outline-button vnew-edit-txbtn " + className, text);
    btn.type = "button";
    if (title) btn.title = title;
    btn.addEventListener("click", onClick);
    return btn;
  }

  /* editRenderTools · editTxRender 가 부른다. 그릴 내용(고른 것 · 값)이 같으면 다시 그리지 않는다(입력 중인 칸이 끊기지 않게). */
  function editTxRenderTools(ctx) {
    var box = ctx.dom.txBox;
    if (!box || !ctx.tx) return;
    var sel = ctx.txSel ? editTxFind(ctx, ctx.txSel.kind, ctx.txSel.id) : null;
    var clipSel = !sel && ctx.sel >= 0 && ctx.sel < ctx.clips.length && !editMultiOn(ctx) ? ctx.sel : -1;
    var sig = JSON.stringify([ctx.txSel, sel && sel.item, clipSel, clipSel >= 0 ? editTxColorOf(ctx.clips[clipSel]) : null,
      ctx.tx.fadeIn, ctx.tx.fadeOut, ctx.clips.length]);
    if (sig === ctx.txToolsSig && box.firstChild) return;
    ctx.txToolsSig = sig;
    clear(box);
    var row = el("div", "vnew-edit-txrow");
    row.appendChild(editTxButton("is-auto", "자막 자동 만들기", "⑥ 사운드의 대사 시간으로 자막을 만듭니다(⑥에서 고친 대사가 먼저). 실행 취소로 되돌릴 수 있습니다", function () {
      editTxAutoCaptions(ctx);
    }));
    row.appendChild(editTxButton("is-add-cap", "+ 자막", "재생헤드에 2초 자막을 넣습니다", function () {
      editTxAdd(ctx, "caption");
    }));
    row.appendChild(editTxButton("is-add-title", "+ 타이틀", "재생헤드에 3초 타이틀(V2)을 넣습니다", function () {
      editTxAdd(ctx, "title");
    }));
    var edges = [[0, "없음"]].concat(EDIT_TX_EDGES.map(function (s) {
      return [s, s + "초"];
    }));
    row.appendChild(editTxField("처음: 검은 화면에서", editTxSelectEl("vnew-edit-txfade-in", edges, ctx.tx.fadeIn, function (v) {
      editTxCommit(ctx, function (tx) {
        tx.fadeIn = Number(v) || 0;
      });
    }, "처음 페이드")));
    row.appendChild(editTxField("끝: 검은 화면으로", editTxSelectEl("vnew-edit-txfade-out", edges, ctx.tx.fadeOut, function (v) {
      editTxCommit(ctx, function (tx) {
        tx.fadeOut = Number(v) || 0;
      });
    }, "끝 페이드")));
    box.appendChild(row);
    if (sel && ctx.txSel.kind === "caption") box.appendChild(editTxCaptionTools(ctx, sel.item));
    else if (sel && ctx.txSel.kind === "title") box.appendChild(editTxTitleTools(ctx, sel.item));
    else if (clipSel >= 0) box.appendChild(editTxColorTools(ctx));
    box.appendChild(el("p", "vnew-hint vnew-edit-txnote", EDIT_TX_NOTE));
  }

  function editTxCaptionTools(ctx, cap) {
    var wrap = el("div", "vnew-edit-txrow is-caption");
    wrap.appendChild(el("span", "vnew-edit-txrow-title", "자막 모양"));
    function apply(patch, all) {
      editTxCommit(ctx, function (tx) {
        tx.captions.forEach(function (c) {
          if (all || c.id === cap.id) Object.keys(patch).forEach(function (k) {
            c.style[k] = patch[k];
          });
        });
      });
    }
    wrap.appendChild(editTxField("모음", editTxSelectEl("vnew-edit-txpreset", [["", "고르기…"]].concat(EDIT_TX_PRESETS.map(function (p) {
      return [p[0], p[1]];
    })), "", function (v) {
      var preset = EDIT_TX_PRESETS.filter(function (p) {
        return p[0] === v;
      })[0];
      if (preset) apply(preset[2], false);
    }, "자막 모양 모음")));
    wrap.appendChild(editTxField("크기", editTxSelectEl("vnew-edit-txsize", EDIT_TX_SIZES, cap.style.size, function (v) {
      apply({ size: v }, false);
    })));
    var color = el("input", "vnew-edit-txcolor");
    color.type = "color";
    color.value = cap.style.color;
    color.addEventListener("change", function () {
      apply({ color: String(color.value).toLowerCase() }, false);
    });
    wrap.appendChild(editTxField("색", color));
    wrap.appendChild(editTxField("배경", editTxSelectEl("vnew-edit-txback", [["outline", "테두리"], ["box", "상자"]], cap.style.back, function (v) {
      apply({ back: v }, false);
    })));
    wrap.appendChild(editTxField("자리", editTxSelectEl("vnew-edit-txpos", [["bottom", "아래"], ["top", "위"]], cap.style.pos, function (v) {
      apply({ pos: v }, false);
    })));
    var italic = el("input", "vnew-edit-txitalic");
    italic.type = "checkbox";
    italic.checked = cap.style.italic === true;
    italic.addEventListener("change", function () {
      apply({ italic: !!italic.checked }, false);
    });
    wrap.appendChild(editTxField("기울임(속마음)", italic));
    wrap.appendChild(editTxButton("is-all", "모든 자막에 이 모양", "크기 · 색 · 배경 · 자리를 모든 자막에 적용합니다(기울임은 그대로)", function () {
      apply({ size: cap.style.size, color: cap.style.color, back: cap.style.back, pos: cap.style.pos }, true);
    }));
    return wrap;
  }

  function editTxTitleTools(ctx, title) {
    var wrap = el("div", "vnew-edit-txrow is-title");
    wrap.appendChild(el("span", "vnew-edit-txrow-title", "타이틀"));
    function apply(patch) {
      editTxCommit(ctx, function (tx) {
        var at = editTxFind({ tx: tx }, "title", title.id);
        if (!at) return false;
        Object.keys(patch).forEach(function (k) {
          tx.titles[at.index][k] = patch[k];
        });
      });
    }
    wrap.appendChild(editTxField("크기", editTxSelectEl("vnew-edit-txtsize", EDIT_TX_TSIZES, title.size, function (v) {
      apply({ size: v });
    })));
    var grid = el("div", "vnew-edit-txgrid");
    grid.setAttribute("role", "radiogroup");
    grid.setAttribute("aria-label", "타이틀 자리(9칸)");
    for (var p = 1; p <= 9; p++) {
      (function (pos) {
        var cell = el("button", "vnew-edit-txgrid-cell" + (pos === title.pos ? " is-on" : ""), "");
        cell.type = "button";
        cell.setAttribute("role", "radio");
        cell.setAttribute("aria-checked", pos === title.pos ? "true" : "false");
        cell.setAttribute("aria-label", "자리 " + pos);
        cell.addEventListener("click", function () {
          if (pos !== title.pos) apply({ pos: pos });
        });
        grid.appendChild(cell);
      })(p);
    }
    wrap.appendChild(editTxField("자리", grid));
    ["fadeIn", "fadeOut"].forEach(function (key) {
      var input = el("input", "vnew-edit-txtfade");
      input.type = "number";
      input.min = "0";
      input.max = "3";
      input.step = "0.1";
      input.value = String(title[key]);
      input.addEventListener("change", function () {
        var v = Math.max(0, Math.min(3, Math.round(editTxNum(input.value, 0) * 20) / 20));
        if (v !== title[key]) apply(key === "fadeIn" ? { fadeIn: v } : { fadeOut: v });
      });
      wrap.appendChild(editTxField(key === "fadeIn" ? "나타나기(초)" : "사라지기(초)", input));
    });
    var box = el("input", "vnew-edit-txtbox");
    box.type = "checkbox";
    box.checked = title.box === true;
    box.addEventListener("change", function () {
      apply({ box: !!box.checked });
    });
    wrap.appendChild(editTxField("배경 상자", box));
    var color = el("input", "vnew-edit-txcolor");
    color.type = "color";
    color.value = title.color;
    color.addEventListener("change", function () {
      apply({ color: String(color.value).toLowerCase() });
    });
    wrap.appendChild(editTxField("색", color));
    return wrap;
  }

  /* 고른 클립 색 보정(Lumetri-lite): 슬라이더 5개(−100~100) · 원래대로 · 같은 테이크 모두에 적용. 끄는 동안은 미리보기만(input),
     놓으면 실행 취소 한 단계(change). */
  function editTxColorTools(ctx) {
    var wrap = el("div", "vnew-edit-txrow is-color");
    wrap.appendChild(el("span", "vnew-edit-txrow-title", "색 보정 · 클립 " + (ctx.sel + 1)));
    var index = ctx.sel;
    var clip = ctx.clips[index];
    var now = editTxColorOf(clip);
    EDIT_TX_COLOR_KEYS.forEach(function (pair) {
      var key = pair[0];
      var range = el("input", "vnew-edit-txslider is-" + key);
      range.type = "range";
      range.min = "-100";
      range.max = "100";
      range.step = "1";
      range.value = String(now[key] || 0);
      range.setAttribute("aria-label", pair[1]);
      var value = el("span", "vnew-edit-txslider-value", String(now[key] || 0));
      range.addEventListener("input", function () {
        var color = editTxColorOf(clip);
        color[key] = Math.round(Number(range.value) || 0);
        ctx.txColorLive = { index: index, color: editTxColorOf({ color: color }) };
        value.textContent = String(color[key]);
        editTxPreview(ctx);
      });
      range.addEventListener("change", function () {
        ctx.txColorLive = null;
        editTxColorSet(ctx, index, key, Math.round(Number(range.value) || 0));
      });
      var field = editTxField(pair[1], range);
      field.appendChild(value);
      wrap.appendChild(field);
    });
    wrap.appendChild(editTxButton("is-reset", "원래대로", "이 클립 색 보정을 모두 0 으로", function () {
      editTxColorReset(ctx);
    }));
    wrap.appendChild(editTxButton("is-same", "같은 테이크 모두에 적용", "같은 원본 영상을 쓰는 클립 모두에 이 색 보정을 넣습니다", function () {
      editTxColorSameTake(ctx);
    }));
    return wrap;
  }

  function editTxColorSet(ctx, index, key, value) {
    var clip = ctx.clips[index];
    if (!clip || !editAlive(ctx) || ctx.busy) return;
    var color = editTxColorOf(clip);
    var v = Math.max(-100, Math.min(100, Math.round(Number(value) || 0)));
    if ((color[key] || 0) === v) return;
    color[key] = v;
    var next = ctx.clips.slice();
    next[index] = editCloneClip(clip);
    next[index].color = editTxColorOf({ color: color });
    if (!Object.keys(next[index].color).length) delete next[index].color;
    editCommit(ctx, next, {});
  }

  function editTxColorReset(ctx) {
    var clip = ctx.clips[ctx.sel];
    if (!clip || !editTxHasColor(clip) || !editAlive(ctx) || ctx.busy) return;
    var next = ctx.clips.slice();
    next[ctx.sel] = editCloneClip(clip);
    delete next[ctx.sel].color;
    editCommit(ctx, next, {});
  }

  /* 같은 원본(sourceKey)의 클립 모두에 고른 클립 색 보정(실행 취소 한 단계). 반환 바꾼 클립 수. */
  function editTxColorSameTake(ctx) {
    var clip = ctx.clips[ctx.sel];
    if (!clip || !editAlive(ctx) || ctx.busy) return 0;
    var color = editTxColorOf(clip);
    var count = 0;
    var next = ctx.clips.map(function (c) {
      if (c.sourceKey !== clip.sourceKey || c === clip) return c;
      if (JSON.stringify(editTxColorOf(c)) === JSON.stringify(color)) return c;
      var copy = editCloneClip(c);
      if (Object.keys(color).length) copy.color = JSON.parse(JSON.stringify(color));
      else delete copy.color;
      count += 1;
      return copy;
    });
    if (!count) {
      editNote(ctx, "같은 테이크의 다른 클립이 없거나 이미 같은 색입니다.");
      return 0;
    }
    editCommit(ctx, next, {});
    editNote(ctx, "같은 테이크 클립 " + count + "개에 색 보정을 넣었습니다.");
    return count;
  }

  /* [+ 자막] · [+ 타이틀]: 재생헤드에서 2초(타이틀 3초), 끝을 넘으면 끝에 맞춘다. */
  function editTxAdd(ctx, kind) {
    var total = editStarts(ctx.clips).total;
    if (!(total > EDIT_TX_MIN_S)) return;
    var len = kind === "title" ? 3 : 2;
    var start = Math.max(0, Math.min(ctx.t, total - Math.min(len, total)));
    var id = editTxNewId(kind === "title" ? "t" : "s", [ctx.tx.captions, ctx.tx.titles]);
    var ok = editTxCommit(ctx, function (tx) {
      if (kind === "title") tx.titles.push(editTxTitle({ id: id, start: start, end: Math.min(total, start + len), text: "타이틀" }));
      else tx.captions.push(editTxCap({ id: id, start: start, end: Math.min(total, start + len), text: "자막", style: EDIT_TX_STYLE }));
    });
    if (ok) editTxSelect(ctx, kind, id);
  }

  /* [자막 자동 만들기]: 서버가 ⑥ 대사 시간으로 만든 자막을 넣는다(있던 자막이 있으면 바꿀지 묻는다 — 실행 취소 한 단계). */
  function editTxAutoCaptions(ctx) {
    if (!editAlive(ctx) || ctx.busy || !ctx.clips.length) return;
    editNote(ctx, "⑥ 대사 시간으로 자막을 만드는 중…");
    var variant = ctx.variant;
    return api(editPath(ctx) + "/captions/auto", { method: "POST", body: JSON.stringify({ variant: variant, clips: editCloneClips(ctx.clips) }) })
      .then(function (res) {
        if (!editAlive(ctx) || ctx.variant !== variant) return;
        var caps = ((res || {}).captions || []).map(editTxCap);
        if (!caps.length) {
          editNote(ctx, "자막으로 넣을 대사를 찾지 못했습니다. ⑥ 사운드에서 대사 시간을 확인해 주세요.");
          return;
        }
        if (ctx.tx.captions.length && !editConfirm("지금 자막 " + ctx.tx.captions.length + "개를 새로 만든 자막 " + caps.length + "개로 바꿀까요? (실행 취소로 되돌릴 수 있습니다)")) return;
        editTxCommit(ctx, function (tx) {
          tx.captions = caps;
        });
        editNote(ctx, "자막 " + caps.length + "개를 만들었습니다." + ((res || {}).skipped ? " 편집에 없는 대사 " + res.skipped + "줄은 뺐습니다." : ""));
      })
      .catch(function (err) {
        if (editAlive(ctx)) editShowError(ctx, "자막을 만들지 못했습니다: " + errMessage(err));
      });
  }

  /* ---- 내보내기 칸: 자막 굽기 · SRT · 프리미어 ---- */

  /* editRenderExport 가 부른다(그 칸을 다시 그릴 때마다 — 같은 요소를 다시 붙인다). 내용은 editTxExportPaint. */
  function editTxExportTools(ctx, box) {
    if (!ctx.tx) return;
    if (!ctx.dom.txExport) ctx.dom.txExport = el("div", "vnew-edit-txexport");
    box.appendChild(ctx.dom.txExport);
    if (EDIT_PREMIERE_UI) {
      // 작업 219b: 꺼져 있으면 프리미어 묶음 칸을 만들지 않는다(ctx.txPrem 이 없으니 /edit/premiere 도 부르지 않는다)
      if (!ctx.txPrem) ctx.txPrem = { box: null, state: null, busy: false, timer: 0, loaded: false, error: "" };
      if (!ctx.dom.txPrem) ctx.dom.txPrem = el("div", "vnew-edit-txprem");
      ctx.txPrem.box = ctx.dom.txPrem;
      box.appendChild(ctx.dom.txPrem);
    }
    editTxExportPaint(ctx);
    editTxPremPaint(ctx); // 상태는 누른 뒤에만 읽는다(열 때 GET 을 더하지 않는다)
  }

  /* 자막 굽기 · 글꼴 안내 · [SRT 내려받기](자막 수 · 굽기 값이 바뀔 때마다 — editTxRender 도 부른다). */
  function editTxExportPaint(ctx) {
    var wrap = ctx.dom.txExport;
    if (!wrap || !ctx.tx) return;
    var info = (ctx.txInfo || {}).burnIn || {};
    clear(wrap);
    var burnLabel = el("label", "vnew-edit-txexport-burn");
    var burn = el("input", "vnew-edit-txburn");
    burn.type = "checkbox";
    burn.checked = ctx.tx.burnIn !== false;
    burn.addEventListener("change", function () {
      editTxCommit(ctx, function (tx) {
        tx.burnIn = !!burn.checked;
      });
    });
    burnLabel.appendChild(burn);
    burnLabel.appendChild(el("span", "", "자막을 영상에 넣기(굽기)"));
    wrap.appendChild(burnLabel);
    if (info.available === false) wrap.appendChild(el("p", "vnew-hint is-bad vnew-edit-txexport-font", info.reason || "이 서버에서는 자막을 영상에 넣을 수 없습니다."));
    else if (info.font) wrap.appendChild(el("span", "vnew-hint vnew-edit-txexport-font", "글꼴: " + info.font));
    var srt = el("button", "outline-button vnew-edit-txsrt", "SRT 내려받기");
    srt.type = "button";
    srt.disabled = !ctx.tx.captions.length;
    srt.title = ctx.tx.captions.length ? "지금 자막을 SRT 파일로 내려받습니다(프리미어 · 유튜브에서 자막 트랙으로 쓸 수 있습니다)" : "자막이 없습니다";
    srt.addEventListener("click", function () {
      editTxSrt(ctx);
    });
    wrap.appendChild(srt);
  }

  function editTxSrt(ctx) {
    var caps = ctx.tx.captions.map(editTxCap);
    if (!caps.length) return null;
    return api(editPath(ctx) + "/captions/srt", { method: "POST", body: JSON.stringify({ variant: ctx.variant, captions: caps }) })
      .then(function (res) {
        var text = String((res || {}).text || "");
        var name = String((res || {}).fileName || "captions.srt");
        if (typeof Blob === "function" && window.URL && window.URL.createObjectURL) {
          var url = window.URL.createObjectURL(new Blob(["\ufeff" + text], { type: "application/x-subrip;charset=utf-8" }));
          var a = el("a", "vnew-edit-txsrt-link", "");
          a.href = url;
          a.setAttribute("download", name);
          document.body.appendChild(a);
          if (typeof a.click === "function") a.click();
          document.body.removeChild(a);
          window.setTimeout(function () {
            window.URL.revokeObjectURL(url);
          }, 1000);
        }
        if (editAlive(ctx)) editNote(ctx, "SRT 를 내려받았습니다(자막 " + ((res || {}).count || caps.length) + "개).");
        return text;
      })
      .catch(function (err) {
        if (editAlive(ctx)) editShowError(ctx, "SRT 를 만들지 못했습니다: " + errMessage(err));
      });
  }

  function editTxPremPaint(ctx) {
    var p = ctx.txPrem;
    if (!p || !p.box) return;
    clear(p.box);
    var btn = el("button", "outline-button vnew-edit-txprem-btn", EDIT_TX_PREM_LABEL);
    btn.type = "button";
    var live = p.state && (p.state.status === "queued" || p.state.status === "running");
    btn.disabled = p.busy || live || !ctx.clips.length;
    btn.title = "저장한 편집을 Premiere Pro 가 여는 XML(FCP7) · EDL · 쓴 영상 소스 zip 으로 묶습니다(유료 호출 없음)";
    btn.addEventListener("click", function () {
      editTxPremiere(ctx);
    });
    p.box.appendChild(btn);
    var text = p.error || editTxPremText(p.state);
    if (text) p.box.appendChild(el("p", "vnew-hint vnew-edit-txprem-status" + (p.error || (p.state || {}).status === "failed" ? " is-bad" : ""), text));
    if (live) {
      var bar = el("div", "vnew-edit-export-bar");
      var fill = el("div", "vnew-edit-export-fill");
      var pr = (p.state || {}).progress || {};
      fill.style.width = (Number(pr.total) > 0 ? Math.round((Number(pr.done) || 0) / Number(pr.total) * 100) : 15) + "%";
      bar.appendChild(fill);
      p.box.appendChild(bar);
    }
    if (p.state && p.state.status === "done" && p.state.url) {
      p.box.appendChild(downloadLink(p.state.url, "프리미어 프로젝트 내려받기 (" + (p.state.fileName || "premiere.zip") + ")"));
    }
  }

  /* [프리미어 프로젝트로 내보내기]: 저장 → POST /edit/premiere(작업 215d) → 끝날 때까지 GET. 라우트가 없으면(404) 안내. */
  function editTxPremiere(ctx) {
    var p = ctx.txPrem;
    if (!p || p.busy || !editAlive(ctx)) return null;
    p.busy = true;
    p.error = "";
    editTxPremPaint(ctx);
    var variant = ctx.variant;
    return editEnsureSaved(ctx)
      .then(function () {
        return api(editPath(ctx) + "/premiere", { method: "POST", body: JSON.stringify({ variant: variant }) });
      })
      .then(function (res) {
        p.busy = false;
        p.state = (res || {}).premiere || { status: "queued" };
        if (!editAlive(ctx)) return;
        editTxPremPaint(ctx);
        editTxPremPoll(ctx, false);
      })
      .catch(function (err) {
        p.busy = false;
        p.error = err && err.status === 404 && err.type !== "EditDisabled" ? EDIT_TX_PREM_404 : "프리미어 프로젝트를 시작하지 못했습니다: " + errMessage(err);
        if (editAlive(ctx)) editTxPremPaint(ctx);
      });
  }

  /* GET /edit/premiere — now 면 바로(처음 열 때 지난 결과), 아니면 진행 중일 때만 EDIT_TX_POLL_MS 뒤. 404 는 조용히 멈춘다. */
  function editTxPremPoll(ctx, now) {
    var p = ctx.txPrem;
    if (!p) return;
    if (p.timer) window.clearTimeout(p.timer);
    p.timer = 0;
    var live = p.state && (p.state.status === "queued" || p.state.status === "running");
    if (!now && !live) return;
    var variant = ctx.variant;
    function run() {
      p.timer = 0;
      if (!editAlive(ctx) || ctx.variant !== variant) return;
      api(editPath(ctx) + "/premiere?variant=" + encodeURIComponent(variant))
        .then(function (res) {
          if (!editAlive(ctx) || ctx.variant !== variant) return;
          var next = (res || {}).premiere || null;
          p.state = next && next.status !== "none" ? next : p.state;
          editTxPremPaint(ctx);
          editTxPremPoll(ctx, false);
        })
        .catch(function (err) {
          if (!editAlive(ctx) || (err && err.status === 404)) return;
          editTxPremPoll(ctx, false);
        });
    }
    if (now) run();
    else p.timer = window.setTimeout(run, EDIT_TX_POLL_MS);
  }

  /* ---- ⑦ 안 대사 고치기 · 영상 다시 만들기(⑥ 함수 재사용) ---- */

  /* ⑥ soundAlive 첫 줄이 부른다(ctx.inEdit). */
  function editTxLrdAlive(lctx) {
    var ctx = lctx.edit;
    return !!ctx && editAlive(ctx) && ctx.txLine === lctx;
  }

  /* [대사 고치기 · 영상 다시 만들기]: ⑥ 보기(GET /sound)를 받아 ⑥ 줄 도구(lrdRowTools)와 편집 창(lrdEditor)을 ⑦ 창 안에 연다. */
  function editTxLineOpen(ctx, lineId, clipIds) {
    if (!editAlive(ctx) || ctx.busy) return null;
    editPause(ctx);
    editTxLineClose(ctx);
    var panel = el("div", "vnew-edit-txline");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", EDIT_TX_LINE_MENU);
    var head = el("div", "vnew-edit-txline-head");
    head.appendChild(el("h3", "vnew-edit-txline-title", EDIT_TX_LINE_MENU));
    var close = el("button", "vnew-edit-help-close", "×");
    close.type = "button";
    close.setAttribute("aria-label", "닫기");
    close.addEventListener("click", function () {
      editTxLineClose(ctx);
    });
    head.appendChild(close);
    panel.appendChild(head);
    var body = el("div", "vnew-edit-txline-body");
    panel.appendChild(body);
    var note = el("p", "vnew-hint vnew-edit-txline-note", "⑥ 대사 정보를 불러오는 중…");
    note.setAttribute("role", "status");
    panel.appendChild(note);
    var swap = el("div", "vnew-edit-txline-swap");
    panel.appendChild(swap);
    var lctx = { inEdit: true, edit: ctx, variant: ctx.variant, view: null, lrd: null, lineId: lineId, clipIds: (clipIds || []).slice(),
      root: body, panel: panel, note: note, swap: swap, timer: 0, doneAt: "" };
    ctx.txLine = lctx;
    ctx.root.appendChild(panel);
    return api(soundPath() + "?variant=" + encodeURIComponent(ctx.variant))
      .then(function (view) {
        if (!editTxLrdAlive(lctx)) return;
        lctx.view = view || {};
        editTxLinePaint(lctx);
        if (lrdBusy(lctx.view)) editTxLrdPoll(lctx);
      })
      .catch(function (err) {
        if (editTxLrdAlive(lctx)) note.textContent = "⑥ 대사 정보를 불러오지 못했습니다: " + soundErrText(err);
      });
  }

  function editTxLineClose(ctx) {
    var lctx = ctx && ctx.txLine;
    if (!lctx) return;
    ctx.txLine = null;
    if (lctx.timer) window.clearTimeout(lctx.timer);
    lctx.timer = 0;
    if (lctx.panel.parentNode) lctx.panel.parentNode.removeChild(lctx.panel);
  }

  /* 창 안을 다시 그린다: ⑥ 줄 한 줄(lrdRowTools)을 만들고 편집 창을 바로 연다. 다시 만들기가 끝났으면 [새 영상으로 바꾸기]. */
  function editTxLinePaint(lctx) {
    var view = lctx.view || {};
    clear(lctx.root);
    lctx.lrd = null;
    var line = (view.lines || []).filter(function (l) {
      return l && l.id === lctx.lineId;
    })[0];
    if (!lrdInfo(lctx)) {
      lctx.note.textContent = "대사 고치기 기능이 꺼져 있습니다.";
      return;
    }
    if (!line) {
      lctx.note.textContent = "⑥ 사운드에서 이 대사 줄을 찾지 못했습니다. ⑥ 을 한 번 연 뒤 다시 눌러 주세요.";
      return;
    }
    lctx.note.textContent = "";
    var row = el("div", "vnew-sound-row vnew-edit-txline-row");
    row.appendChild(el("span", "vnew-sound-cell vnew-sound-speaker", line.speaker || "(이름 없음)"));
    var lineCell = el("span", "vnew-sound-cell vnew-sound-line", line.lineKo || "");
    row.appendChild(lineCell);
    var cell = el("span", "vnew-sound-cell");
    row.appendChild(cell);
    lctx.root.appendChild(row);
    lrdRowTools(lctx, line, row, lineCell, cell); // ⑥ 과 같은 줄 도구 · 편집 창(새 대사 · 말하는 사람 · 실제 말/속마음 · 말투 · AI로 다시 쓰기 · 다시 만들기)
    var rec = lctx.lrd && lctx.lrd.rows[lctx.lrd.rows.length - 1];
    if (rec && !rec.editor && rec.open && typeof rec.open.click === "function") rec.open.click();
    editTxPaintSwap(lctx);
  }

  /* ⑥ lrdReload 첫 줄이 부른다(저장 · 되돌리기 뒤 새 보기): ⑦ 자막을 새 대사로 맞추고 창을 다시 그린다. */
  function editTxLrdReload(lctx, view) {
    var ctx = lctx.edit;
    lctx.view = view || {};
    ctx.txSound = { variant: lctx.variant, at: Date.now(), view: lctx.view };
    if (editTxLrdAlive(lctx)) {
      editTxSyncCaptions(ctx, lctx.view);
      editTxLinePaint(lctx);
    }
    if (lrdBusy(lctx.view)) editTxLrdPoll(lctx);
  }

  /* 고친 대사(override)와 글이 다른 자막(lineId 같음)을 새 글 · 속마음 기울임으로(실행 취소 한 단계). 반환 바꾼 수. */
  function editTxSyncCaptions(ctx, view) {
    var lines = (((view || {}).lineRedo || {}).lines) || {};
    var count = 0;
    ctx.tx.captions.forEach(function (cap) {
      var ov = cap.lineId && (lines[cap.lineId] || {}).override;
      if (ov && ov.textKo && (ov.textKo !== cap.text || (ov.thought === true) !== cap.style.italic)) count += 1;
    });
    if (!count) return 0;
    editTxCommit(ctx, function (tx) {
      tx.captions.forEach(function (cap) {
        var ov = cap.lineId && (lines[cap.lineId] || {}).override;
        if (ov && ov.textKo) {
          cap.text = String(ov.textKo);
          cap.style.italic = ov.thought === true;
        }
      });
    });
    editNote(ctx, "자막 " + count + "개도 고친 대사로 바꿨습니다.");
    return count;
  }

  /* ⑥ soundSchedule 첫 줄이 부른다: 다시 만드는 중이면 EDIT_TX_POLL_MS 마다 GET /sound, 끝나면(lrdChanged) 다시 그린다. */
  function editTxLrdPoll(lctx) {
    if (lctx.timer) window.clearTimeout(lctx.timer);
    lctx.timer = 0;
    if (!lrdBusy(lctx.view) || !editTxLrdAlive(lctx)) return;
    lctx.timer = window.setTimeout(function () {
      lctx.timer = 0;
      if (!editTxLrdAlive(lctx)) return;
      api(soundPath() + "?variant=" + encodeURIComponent(lctx.variant))
        .then(function (view) {
          if (!editTxLrdAlive(lctx)) return;
          if (lrdChanged(lctx.view, view)) {
            editTxLrdReload(lctx, view);
            return;
          }
          if ((view || {}).lineRedo) lctx.view.lineRedo = view.lineRedo;
          lrdPaintRows(lctx);
          editTxLrdPoll(lctx);
        })
        .catch(function () {
          editTxLrdPoll(lctx);
        });
    }, EDIT_TX_POLL_MS);
  }

  /* 다시 만들기가 끝났으면 [새 영상으로 바꾸기](클립 in/out · 편집은 그대로 — ⑦ AI로 고치기의 [바꾸기]와 같은 규칙). */
  function editTxPaintSwap(lctx) {
    clear(lctx.swap);
    var job = (lrdEntry(lctx, lctx.lineId) || {}).job || {};
    if (job.status !== "done") return;
    var ctx = lctx.edit;
    lctx.swap.appendChild(el("p", "vnew-hint", "대사를 고친 새 영상이 준비됐습니다. ⑦ 클립도 새 영상으로 바꿀 수 있습니다(자르기 · 속도 · 색 · 전환은 그대로)."));
    var btn = el("button", "primary-button vnew-edit-txline-swapbtn", "새 영상으로 바꾸기");
    btn.type = "button";
    btn.disabled = !ctx.retakeOn;
    if (!ctx.retakeOn) btn.title = "⑦ AI로 고치기가 꺼져 있어 새 영상을 재료로 쓸 수 없습니다";
    btn.addEventListener("click", function () {
      editTxSwapRedo(ctx, lctx, btn);
    });
    lctx.swap.appendChild(btn);
    lctx.swapNote = el("p", "vnew-hint vnew-edit-txline-swapnote", "");
    lctx.swapNote.style.display = "none";
    lctx.swap.appendChild(lctx.swapNote);
  }

  /* 새 영상으로 바꾸기: 재료를 다시 받고(새 버전이 재료가 된다) 그 클립 재료의 AI로 고치기 보기에서 가장 새 완성 버전을 골라
     ⑦ editFixSwap(지시서 116 · 작업 192 · 194 규칙 — in/out 을 새 버전 시간으로 옮기고 속도 · 소리 끔 · 색 · 전환은 그대로)으로 바꾼다. */
  function editTxSwapRedo(ctx, lctx, button) {
    var ids = (lctx.clipIds || []).filter(function (id) {
      return ctx.clips.some(function (c) {
        return c.id === id;
      });
    });
    var clip = ctx.clips.filter(function (c) {
      return c.id === ids[0];
    })[0];
    var fix = { mid: "", clipId: ids[0] || "", clipIds: ids, view: null, open: true, dom: { note: lctx.swapNote } };
    if (!clip) {
      editFixNote(fix, "바꿀 클립이 타임라인에 없습니다.");
      return null;
    }
    fix.mid = String((ctx.materials[clip.sourceKey] || {}).mid || "");
    if (button) button.disabled = true;
    editFixNote(fix, "새 영상을 찾는 중…");
    return api(editPath(ctx) + "?variant=" + encodeURIComponent(ctx.variant))
      .then(function (view) {
        if (view && Array.isArray(view.materials)) {
          editAbsorbMaterials(ctx, view);
          editRenderBin(ctx);
        }
        return api(editPath(ctx) + "/retake/" + encodeURIComponent(fix.mid));
      })
      .then(function (fv) {
        if (button) button.disabled = false;
        if (!editAlive(ctx)) return;
        fix.view = fv || {};
        var vers = (fix.view.versions || []).filter(function (v) {
          return v && !v.original && v.status === "completed" && v.sourceKey && ctx.materials[v.sourceKey];
        });
        var ver = vers.sort(function (a, b) {
          return (Number(b.n) || 0) - (Number(a.n) || 0);
        })[0];
        if (!ver) {
          editFixNote(fix, "새 영상을 ⑦ 재료에서 찾지 못했습니다. 잠시 뒤 다시 눌러 주세요.");
          return;
        }
        editFixSwap(ctx, fix, ver, button || el("button"));
      })
      .catch(function (err) {
        if (button) button.disabled = false;
        editFixNote(fix, "새 영상으로 바꾸지 못했습니다: " + errMessage(err));
      });
  }

  /* editLeave 가 부른다. */
  function editTxLeave(ctx) {
    editTxMenuClose(ctx);
    editTxCloseEditor(ctx);
    editTxLineClose(ctx);
    if (ctx.txPrem && ctx.txPrem.timer) window.clearTimeout(ctx.txPrem.timer);
    if (ctx.txPrem) ctx.txPrem.timer = 0;
  }
  /* ==== 작업 215b 끝 ==== */

  /* ================================================================ 작업 215a: ⑦ 오디오 트랙(A2~A5 내 소리) 시작
     편집 문서 audioTracks · a1(서버 edit_audio.py 와 같은 규칙). 트랙 아이템 = 사용자가 올린 소리(내 소리) 구간. 실행 취소는 몸짓마다
     한 단계(editMixCommit — 클립 스냅샷에 mix 를 함께 둔다, 빈 mix 는 넣지 않는다). 미리듣기는 WebAudio(이득 · 페이드 · 키 · 덕킹),
     A1 은 지금처럼 <video> 소리(트랙 이득은 volume, mute · solo 는 muted). 이 파일들은 AI 모델에 보내지 않는다. */
  var EDIT_MIX_MAX_TRACKS = 4;
  var EDIT_MIX_NAMES = ["A2 배경음", "A3 효과음", "A4 효과음 2", "A5 소리"];
  var EDIT_MIX_GAIN_MIN = -60;
  var EDIT_MIX_GAIN_MAX = 12;
  var EDIT_MIX_DUCK_DB = 8;
  var EDIT_MIX_ATTACK_S = 0.15;
  var EDIT_MIX_RELEASE_S = 0.15;
  var EDIT_MIX_MIN_S = 0.04;
  var EDIT_MIX_SNAP_PX = 8;
  var EDIT_MIX_LANE_PX = 44;
  var EDIT_MIX_DRIFT_S = 0.15;
  var EDIT_MIX_CURVE_HZ = 100;
  var EDIT_MIX_ACCEPT = ".mp3,.wav,.m4a,.aac,.ogg,audio/mpeg,audio/wav,audio/mp4,audio/aac,audio/ogg";

  function editMixDefaultA1() {
    return { gainDb: 0, mute: false, solo: false, duck: false, duckDb: EDIT_MIX_DUCK_DB };
  }

  function editMixA1Default(a1) {
    var d = editMixDefaultA1();
    var a = a1 || d;
    return Number(a.gainDb) === d.gainDb && !a.mute && !a.solo && !a.duck && Number(a.duckDb) === d.duckDb;
  }

  function editMixClamp(value, lo, hi) {
    var v = Number(value);
    if (!isFinite(v)) v = 0;
    return Math.max(lo, Math.min(hi, v));
  }

  function editMixR3(value) {
    return Math.round(Number(value) * 1000) / 1000;
  }

  function editMixCloneItem(item) {
    var i = item || {};
    return {
      id: String(i.id || ""), assetId: String(i.assetId || ""), start: Number(i.start) || 0, in: Number(i.in) || 0,
      out: Number(i.out) || 0, gainDb: Number(i.gainDb) || 0, fadeIn: Number(i.fadeIn) || 0, fadeOut: Number(i.fadeOut) || 0,
      keys: (Array.isArray(i.keys) ? i.keys : []).map(function (k) {
        return { t: Number(k.t) || 0, db: Number(k.db) || 0 };
      })
    };
  }

  /* 문서 → mix {tracks, a1}(꼴만 맞춘 사본). 옛 문서(audioTracks · a1 없음)는 빈 트랙 · 기본 A1. */
  function editMixNorm(doc) {
    var d = doc || {};
    var a = d.a1 && typeof d.a1 === "object" ? d.a1 : {};
    var a1 = editMixDefaultA1();
    a1.gainDb = Number(a.gainDb) || 0;
    a1.mute = a.mute === true;
    a1.solo = a.solo === true;
    a1.duck = a.duck === true;
    a1.duckDb = a.duckDb === undefined || a.duckDb === null ? EDIT_MIX_DUCK_DB : Number(a.duckDb) || 0;
    var tracks = (Array.isArray(d.audioTracks) ? d.audioTracks : []).slice(0, EDIT_MIX_MAX_TRACKS).map(function (t, index) {
      var tr = t || {};
      return {
        id: String(tr.id || "a" + (index + 2)), name: String(tr.name || EDIT_MIX_NAMES[index]), mute: tr.mute === true,
        solo: tr.solo === true, gainDb: Number(tr.gainDb) || 0,
        items: (Array.isArray(tr.items) ? tr.items : []).map(editMixCloneItem)
      };
    });
    return { tracks: tracks, a1: a1 };
  }

  function editMixClone(mix) {
    return editMixNorm({ audioTracks: (mix || {}).tracks, a1: (mix || {}).a1 });
  }

  function editMixEmpty(mix) {
    return !mix || (!(mix.tracks || []).length && editMixA1Default(mix.a1));
  }

  /* PUT 바디에 더할 값. 빈 mix 면 {}(215a 전과 같은 바디). */
  function editMixSaveFields(mix) {
    if (editMixEmpty(mix)) return {};
    var out = { audioTracks: editMixClone(mix).tracks };
    if (!editMixA1Default(mix.a1)) out.a1 = editMixClone(mix).a1;
    return out;
  }

  function editMixItemLen(item) {
    return Math.max(0, Number(item.out) - Number(item.in));
  }

  function editMixNewId(mix, prefix) {
    var used = {};
    (mix.tracks || []).forEach(function (t) {
      used[t.id] = true;
      t.items.forEach(function (i) {
        used[i.id] = true;
      });
    });
    for (var n = 1; n < 100000; n++) {
      if (!used[prefix + n]) return prefix + n;
    }
    return prefix + Date.now();
  }

  function editMixAddTrack(mix) {
    var next = editMixClone(mix);
    var index = next.tracks.length;
    if (index >= EDIT_MIX_MAX_TRACKS) return null;
    var id = "a" + (index + 2);
    if (next.tracks.some(function (t) { return t.id === id; })) id = editMixNewId(next, "tr");
    next.tracks.push({ id: id, name: EDIT_MIX_NAMES[index], mute: false, solo: false, gainDb: 0, items: [] });
    return next;
  }

  function editMixRemoveTrack(mix, t) {
    var next = editMixClone(mix);
    if (!next.tracks[t]) return null;
    next.tracks.splice(t, 1);
    return next;
  }

  /* 내 소리 asset 을 트랙 t 의 start(타임라인 초)에 통째로 넣는다(길이는 타임라인 한도 안으로). */
  function editMixAddItem(mix, t, asset, start, maxTotal) {
    var next = editMixClone(mix);
    var track = next.tracks[t];
    var dur = Number((asset || {}).duration) || 0;
    if (!track || !(dur > 0)) return null;
    var at = Math.max(0, editMixR3(start));
    var len = Math.min(dur, (Number(maxTotal) || 300) - at);
    if (!(len >= EDIT_MIX_MIN_S)) return null;
    track.items.push({ id: editMixNewId(next, "s"), assetId: String(asset.assetId), start: at, in: 0, out: editMixR3(len), gainDb: 0,
      fadeIn: 0, fadeOut: 0, keys: [] });
    return next;
  }

  /* 끝 다듬기. edge "in": 왼쪽 끝을 타임라인 x 초로(start · in 함께), "out": 오른쪽 끝(out). 원본 0 · 길이 · 최소 길이로 묶는다. */
  function editMixTrimItem(item, edge, x, assetDur) {
    var it = editMixCloneItem(item);
    var dur = Number(assetDur) > 0 ? Number(assetDur) : Number(it.out);
    if (edge === "in") {
      var delta = Number(x) - it.start;
      delta = Math.max(-it.in, Math.min(it.out - it.in - EDIT_MIX_MIN_S, delta));
      delta = Math.max(delta, -it.start);
      it.start = editMixR3(it.start + delta);
      it.in = editMixR3(it.in + delta);
      it.keys = it.keys.map(function (k) { return { t: editMixR3(k.t - delta), db: k.db }; })
        .filter(function (k) { return k.t >= 0; });
    } else {
      var end = Math.min(dur, Math.max(it.in + EDIT_MIX_MIN_S, it.in + (Number(x) - it.start)));
      it.out = editMixR3(end);
    }
    var len = editMixItemLen(it);
    it.keys = it.keys.filter(function (k) { return k.t <= len + 1e-6; });
    if (it.fadeIn + it.fadeOut > len) {
      var scale = len / (it.fadeIn + it.fadeOut);
      it.fadeIn = editMixR3(it.fadeIn * scale);
      it.fadeOut = editMixR3(it.fadeOut * scale);
    }
    return it;
  }

  function editMixSetFade(item, which, seconds) {
    var it = editMixCloneItem(item);
    var len = editMixItemLen(it);
    var other = which === "in" ? it.fadeOut : it.fadeIn;
    var v = editMixR3(editMixClamp(seconds, 0, Math.max(0, len - other)));
    if (which === "in") it.fadeIn = v;
    else it.fadeOut = v;
    return it;
  }

  /* 키프레임 볼륨 꺾은선(dB, 아이템 시각): 아이템 이득 + 키(첫 키 앞 · 마지막 키 뒤는 그 값). 서버 key_points 와 같다. */
  function editMixKeyDb(item, t) {
    var keys = (item.keys || []).slice().sort(function (a, b) { return a.t - b.t; });
    var g = Number(item.gainDb) || 0;
    if (!keys.length) return g;
    if (t <= keys[0].t) return g + keys[0].db;
    for (var i = 1; i < keys.length; i++) {
      if (t <= keys[i].t) {
        var a = keys[i - 1];
        var b = keys[i];
        return g + (b.t <= a.t ? a.db : a.db + (b.db - a.db) * (t - a.t) / (b.t - a.t));
      }
    }
    return g + keys[keys.length - 1].db;
  }

  function editMixFade(item, t) {
    var len = editMixItemLen(item);
    var g = 1;
    if (item.fadeIn > 0) g = Math.min(g, Math.max(0, t / item.fadeIn));
    if (item.fadeOut > 0) g = Math.min(g, Math.max(0, (len - t) / item.fadeOut));
    return g;
  }

  /* 아이템 시각 t 의 선형 이득(서버 item_gain 과 같다). */
  function editMixGainAt(item, t) {
    return Math.pow(10, editMixKeyDb(item, t) / 20) * editMixFade(item, t);
  }

  /* Ctrl+누르기: 그 시각 지금 꺾은선 값으로 키 하나(키 db 는 아이템 이득을 뺀 값). 반환 [새 아이템, 키 번호]. */
  function editMixKeyAdd(item, t, db) {
    var it = editMixCloneItem(item);
    var at = editMixR3(editMixClamp(t, 0, editMixItemLen(it)));
    var value = db === undefined ? editMixKeyDb(it, at) - it.gainDb : Number(db);
    it.keys.push({ t: at, db: Math.round(editMixClamp(value, EDIT_MIX_GAIN_MIN, EDIT_MIX_GAIN_MAX) * 100) / 100 });
    it.keys.sort(function (a, b) { return a.t - b.t; });
    for (var k = 0; k < it.keys.length; k++) if (it.keys[k].t === at) return [it, k];
    return [it, it.keys.length - 1];
  }

  function editMixKeyMove(item, k, t, db) {
    var it = editMixCloneItem(item);
    if (!it.keys[k]) return it;
    var lo = k > 0 ? it.keys[k - 1].t : 0;
    var hi = k + 1 < it.keys.length ? it.keys[k + 1].t : editMixItemLen(it);
    it.keys[k] = { t: editMixR3(editMixClamp(t, lo, hi)), db: Math.round(editMixClamp(db, EDIT_MIX_GAIN_MIN, EDIT_MIX_GAIN_MAX) * 100) / 100 };
    return it;
  }

  function editMixKeyRemove(item, k) {
    var it = editMixCloneItem(item);
    it.keys.splice(k, 1);
    return it;
  }

  /* 재생헤드 at 에서 모든 트랙의 걸친 아이템을 둘로(키 · 페이드는 나눠 갖는다). 바뀐 것이 없으면 null. */
  function editMixSplitAll(mix, at) {
    var next = editMixClone(mix);
    var changed = false;
    next.tracks.forEach(function (track) {
      var out = [];
      track.items.forEach(function (it) {
        var cut = Number(at) - it.start;
        var len = editMixItemLen(it);
        if (cut < EDIT_MIX_MIN_S || cut > len - EDIT_MIX_MIN_S) {
          out.push(it);
          return;
        }
        changed = true;
        var left = editMixCloneItem(it);
        var right = editMixCloneItem(it);
        left.out = editMixR3(it.in + cut);
        left.fadeIn = Math.min(it.fadeIn, cut);
        left.fadeOut = 0;
        left.keys = it.keys.filter(function (k) { return k.t <= cut; });
        right.id = editMixNewId({ tracks: [{ id: "", items: out.concat([left]).concat(track.items) }].concat(next.tracks) }, "s");
        right.start = editMixR3(it.start + cut);
        right.in = left.out;
        right.fadeIn = 0;
        right.fadeOut = Math.min(it.fadeOut, len - cut);
        right.keys = it.keys.filter(function (k) { return k.t >= cut; }).map(function (k) {
          return { t: editMixR3(k.t - cut), db: k.db };
        });
        var level = editMixKeyDb(it, cut) - it.gainDb;
        if (it.keys.length) {
          left.keys.push({ t: editMixR3(cut), db: Math.round(level * 100) / 100 });
          right.keys.unshift({ t: 0, db: Math.round(level * 100) / 100 });
        }
        out.push(left, right);
      });
      track.items = out;
    });
    return changed ? next : null;
  }

  /* 붙기(Shift+N): 가장 가까운 점이 tol 초 안이면 그 점, 아니면 value. */
  function editMixSnap(value, points, tol) {
    var best = value;
    var gap = tol;
    (points || []).forEach(function (p) {
      var d = Math.abs(p - value);
      if (d <= gap) {
        gap = d;
        best = p;
      }
    });
    return best;
  }

  /* 붙을 점: 클립 경계 · 재생헤드 · 다른 아이템 양 끝(skipId 는 뺀다). */
  function editMixSnapPoints(clips, mix, playhead, skipId) {
    var info = editStarts(clips || []);
    var points = info.starts.slice();
    points.push(info.total, Number(playhead) || 0, 0);
    (mix.tracks || []).forEach(function (track) {
      track.items.forEach(function (it) {
        if (it.id === skipId) return;
        points.push(it.start, it.start + editMixItemLen(it));
      });
    });
    return points;
  }

  /* mute · solo(서버 audible 과 같다): solo 가 하나라도 있으면 solo 인 것만. */
  function editMixAudible(mix) {
    var tracks = mix.tracks || [];
    var a1 = mix.a1 || editMixDefaultA1();
    var anySolo = a1.solo || tracks.some(function (t) { return t.solo; });
    return {
      a1: anySolo ? !!a1.solo : !a1.mute,
      tracks: tracks.map(function (t) { return anySolo ? !!t.solo : !t.mute; })
    };
  }

  function editMixToggle(mix, t, key) {
    var next = editMixClone(mix);
    var target = t < 0 ? next.a1 : next.tracks[t];
    if (!target) return null;
    target[key] = !target[key];
    return next;
  }

  /* 덕킹 꺾은선 [[초, dB]](서버 duck_points 와 같다). */
  function editMixDuckPoints(spans, depth) {
    var gapJoin = EDIT_MIX_ATTACK_S + EDIT_MIX_RELEASE_S;
    var list = (spans || []).filter(function (s) { return s[1] > s[0]; }).map(function (s) { return [Number(s[0]), Number(s[1])]; })
      .sort(function (a, b) { return a[0] - b[0]; });
    var merged = [];
    list.forEach(function (s) {
      var last = merged[merged.length - 1];
      if (last && s[0] <= last[1] + gapJoin + 1e-9) last[1] = Math.max(last[1], s[1]);
      else merged.push(s.slice());
    });
    if (!(Number(depth) > 0) || !merged.length) return [];
    var pts = [];
    merged.forEach(function (s) {
      var down = Math.max(0, s[0] - EDIT_MIX_ATTACK_S);
      if (pts.length && down <= pts[pts.length - 1][0]) down = pts[pts.length - 1][0];
      if (!pts.length || down > pts[pts.length - 1][0] || pts[pts.length - 1][1] !== 0) pts.push([down, 0]);
      pts.push([Math.max(s[0], down), -depth], [s[1], -depth], [s[1] + EDIT_MIX_RELEASE_S, 0]);
    });
    return pts;
  }

  function editMixPointsAt(pts, t) {
    if (!pts.length) return 0;
    if (t <= pts[0][0]) return pts[0][1];
    for (var i = 1; i < pts.length; i++) {
      if (t <= pts[i][0]) {
        var a = pts[i - 1];
        var b = pts[i];
        return b[0] <= a[0] ? a[1] : a[1] + (b[1] - a[1]) * (t - a[0]) / (b[0] - a[0]);
      }
    }
    return pts[pts.length - 1][1];
  }

  /* 대사 구간(타임라인 초): 클립마다 재료 소리 구간(작업 185 soundRanges, 원본 초)을 화면 구간 안으로 옮긴다. */
  function editMixDialogueSpans(clips, rangesOf) {
    var info = editStarts(clips || []);
    var out = [];
    (clips || []).forEach(function (clip, index) {
      if (clip.muted) return;
      var speed = editSpeedOf(clip);
      var shift = editSlipOf(clip) * speed;
      (rangesOf(clip) || []).forEach(function (r) {
        var a = Math.max(Number(clip.in), Number(r[0]) + shift);
        var b = Math.min(Number(clip.out), Number(r[1]) + shift);
        if (b > a) out.push([info.starts[index] + (a - Number(clip.in)) / speed, info.starts[index] + (b - Number(clip.in)) / speed]);
      });
    });
    return out;
  }

  /* ---- 상태 · 실행 취소 ---- */

  function editMixSnapshot(ctx) {
    var snap = editSnapshotOf(ctx.variant, ctx.clips);
    if (!editMixEmpty(ctx.mix)) snap.mix = editMixClone(ctx.mix);
    return snap;
  }

  /* 클립 몸짓(editCommit)이 막 쌓은 스냅샷에 지금 mix 를 붙인다(빈 mix 면 붙이지 않는다). */
  function editMixTag(ctx) {
    var last = ctx.hist.undo[ctx.hist.undo.length - 1];
    if (last && last.mix === undefined && !editMixEmpty(ctx.mix)) last.mix = editMixClone(ctx.mix);
  }

  /* 오디오 몸짓 하나 = 실행 취소 한 단계. */
  function editMixCommit(ctx, next, sel) {
    if (!next) return;
    editHistPush(ctx.hist, editMixSnapshot(ctx));
    ctx.mix = next;
    if (sel !== undefined) ctx.mixSel = sel;
    ctx.mixKey = null;
    editChanged(ctx);
  }

  function editMixRestore(ctx, snap) {
    ctx.mix = snap && snap.mix ? editMixClone(snap.mix) : editMixNorm({});
    ctx.mixSel = null;
    ctx.mixKey = null;
  }

  function editMixItemRef(ctx) {
    var s = ctx.mixSel;
    var track = s && ctx.mix.tracks[s.t];
    var item = track && track.items[s.i];
    return item ? { track: track, item: item, t: s.t, i: s.i } : null;
  }

  function editMixReplaceItem(ctx, t, i, item) {
    var next = editMixClone(ctx.mix);
    next.tracks[t].items[i] = item;
    return next;
  }

  /* Delete: 고른 키 → 고른 아이템 순. 지웠으면 true(클립 삭제로 넘어가지 않는다). */
  function editMixDelete(ctx) {
    var ref = editMixItemRef(ctx);
    if (!ref) return false;
    if (ctx.mixKey && ctx.mixKey.t === ref.t && ctx.mixKey.i === ref.i && ref.item.keys[ctx.mixKey.k]) {
      editMixCommit(ctx, editMixReplaceItem(ctx, ref.t, ref.i, editMixKeyRemove(ref.item, ctx.mixKey.k)));
      return true;
    }
    var next = editMixClone(ctx.mix);
    next.tracks[ref.t].items.splice(ref.i, 1);
    editMixCommit(ctx, next, null);
    return true;
  }

  function editMixSnapTol(ctx) {
    return edit215cSnapOn(ctx) ? EDIT_MIX_SNAP_PX / Math.max(1, ctx.pps || 1) : 0; // 작업 219b: 215c 와 같은 붙기 상태
  }

  /* ---- 그리기 ---- */

  function editMixRender(ctx) {
    var d = ctx.dom;
    if (!d.mixLanes) return;
    clear(d.mixLanes);
    (d.mixLabelEls || []).forEach(function (node) {
      if (node.parentNode) node.parentNode.removeChild(node);
    });
    d.mixLabelEls = [];
    var tracks = ctx.mix.tracks;
    var aud = editMixAudible(ctx.mix);
    if (d.labels) {
      if (tracks.length) d.labels.classList.add("has-mix");
      else d.labels.classList.remove("has-mix");
    }
    tracks.forEach(function (track, t) {
      var label = el("span", "vnew-edit-label is-mix" + (aud.tracks[t] ? "" : " is-off"), track.name.split(" ")[0]);
      label.title = track.name;
      d.labels.appendChild(label);
      d.mixLabelEls.push(label);
      var lane = el("div", "vnew-edit-track is-mix" + (aud.tracks[t] ? "" : " is-off"));
      lane.setAttribute("data-track", String(t));
      track.items.forEach(function (item, i) {
        lane.appendChild(editMixItemBox(ctx, t, i, item));
      });
      d.mixLanes.appendChild(lane);
    });
    editMixRenderMixer(ctx);
    editMixQueuePeaks(ctx);
  }

  function editMixItemBox(ctx, t, i, item) {
    var pps = ctx.pps || 1;
    var len = editMixItemLen(item);
    var sel = ctx.mixSel && ctx.mixSel.t === t && ctx.mixSel.i === i;
    var box = el("div", "vnew-edit-mix-item" + (sel ? " is-selected" : ""));
    box.style.left = Math.round(item.start * pps * 10) / 10 + "px";
    box.style.width = Math.max(4, Math.round(len * pps * 10) / 10) + "px";
    var asset = (ctx.sounds.byId || {})[item.assetId] || {};
    box.title = (asset.name || "소리") + " — 끌어 옮기기 · Alt+끌기 복제 · Ctrl+누르기 키 · Delete 지우기";
    var peaks = ctx.sounds.peaks[item.assetId];
    var path = peaks ? editPeakPath({ in: item.in, out: item.out }, { perSecond: peaks.perSecond, values: peaks.values }, 0) : null;
    if (path && typeof document.createElementNS === "function") {
      var svg = document.createElementNS(EDIT_SVG_NS, "svg");
      svg.setAttribute("class", "vnew-edit-wave");
      svg.setAttribute("viewBox", "0 0 " + path.n + " 100");
      svg.setAttribute("preserveAspectRatio", "none");
      svg.setAttribute("aria-hidden", "true");
      var line = document.createElementNS(EDIT_SVG_NS, "path");
      line.setAttribute("d", path.d);
      svg.appendChild(line);
      box.appendChild(svg);
    }
    box.appendChild(el("span", "vnew-edit-mix-name", asset.name || "소리"));
    // 고무줄(볼륨): 위 = +12dB, 아래 = −60dB. 키는 점.
    var band = el("div", "vnew-edit-mix-band");
    var points = [[0, editMixKeyDb(item, 0)]].concat(item.keys.map(function (k) { return [k.t, editMixKeyDb(item, k.t)]; }))
      .concat([[len, editMixKeyDb(item, len)]]);
    if (typeof document.createElementNS === "function") {
      var bsvg = document.createElementNS(EDIT_SVG_NS, "svg");
      bsvg.setAttribute("class", "vnew-edit-mix-rubber");
      bsvg.setAttribute("viewBox", "0 0 1000 100");
      bsvg.setAttribute("preserveAspectRatio", "none");
      bsvg.setAttribute("aria-hidden", "true");
      var poly = document.createElementNS(EDIT_SVG_NS, "polyline");
      poly.setAttribute("points", points.map(function (p) {
        return Math.round((len ? p[0] / len : 0) * 1000) + "," + Math.round(editMixDbY(p[1]) * 100);
      }).join(" "));
      bsvg.appendChild(poly);
      band.appendChild(bsvg);
    }
    item.keys.forEach(function (k, n) {
      var dot = el("span", "vnew-edit-mix-key" + (ctx.mixKey && ctx.mixKey.t === t && ctx.mixKey.i === i && ctx.mixKey.k === n ? " is-selected" : ""));
      dot.style.left = Math.round(k.t * pps * 10) / 10 + "px";
      dot.style.top = Math.round(editMixDbY(item.gainDb + k.db) * 1000) / 10 + "%";
      dot.title = "키 " + (item.gainDb + k.db).toFixed(1) + " dB — 끌어 옮기기 · Delete 지우기";
      dot.addEventListener("pointerdown", function (event) {
        editMixKeyPointer(ctx, t, i, n, event);
      });
      band.appendChild(dot);
    });
    box.appendChild(band);
    ["in", "out"].forEach(function (which) {
      var fade = el("span", "vnew-edit-mix-fade is-" + which);
      var secs = which === "in" ? item.fadeIn : item.fadeOut;
      fade.style.width = Math.max(10, Math.round(secs * pps)) + "px";
      fade.title = (which === "in" ? "페이드 인 " : "페이드 아웃 ") + secs.toFixed(2) + "초 — 끌어 바꾸기";
      fade.addEventListener("pointerdown", function (event) {
        editMixFadePointer(ctx, t, i, which, event);
      });
      box.appendChild(fade);
      var edge = el("span", "vnew-edit-mix-edge is-" + which);
      edge.title = which === "in" ? "시작 다듬기" : "끝 다듬기";
      edge.addEventListener("pointerdown", function (event) {
        editMixEdgePointer(ctx, t, i, which, event);
      });
      box.appendChild(edge);
    });
    box.addEventListener("pointerdown", function (event) {
      editMixItemPointer(ctx, t, i, event);
    });
    return box;
  }

  function editMixDbY(db) {
    return (EDIT_MIX_GAIN_MAX - editMixClamp(db, EDIT_MIX_GAIN_MIN, EDIT_MIX_GAIN_MAX)) / (EDIT_MIX_GAIN_MAX - EDIT_MIX_GAIN_MIN);
  }

  function editMixYDb(frac) {
    return EDIT_MIX_GAIN_MAX - editMixClamp(frac, 0, 1) * (EDIT_MIX_GAIN_MAX - EDIT_MIX_GAIN_MIN);
  }

  function editMixPick(ctx, t, i) {
    ctx.mixSel = t === null ? null : { t: t, i: i };
    ctx.mixKey = null;
    if (t !== null && ctx.txSel) editTxSelect(ctx, null); // 작업 220: 고른 것은 하나 — Delete 가 방금 고른 소리 아이템을 지운다
    if (t !== null && ctx.sel >= 0) {
      ctx.sel = -1;
      ctx.multi = [];
      editRenderTimeline(ctx);
      return;
    }
    editMixRender(ctx);
  }

  /* ---- 끌기(편집 포인터 도우미 editTrack 을 쓴다) ---- */

  function editMixItemPointer(ctx, t, i, event) {
    if (!editPointerOk(ctx, event)) return;
    if (event.stopPropagation) event.stopPropagation();
    var track = ctx.mix.tracks[t];
    var item = track && track.items[i];
    if (!item) return;
    if (event.ctrlKey || event.metaKey) {
      // Ctrl+누르기: 고무줄 키 더하기(그 시각 지금 값)
      var x = editContentX(ctx, event) / (ctx.pps || 1) - item.start;
      var made = editMixKeyAdd(item, x);
      editMixCommit(ctx, editMixReplaceItem(ctx, t, i, made[0]), { t: t, i: i });
      ctx.mixKey = { t: t, i: i, k: made[1] };
      editMixRender(ctx);
      return;
    }
    var copy = !!event.altKey;
    var before = editMixSnapshot(ctx);
    editMixPick(ctx, t, i);
    var box = event.currentTarget || event.target;
    editTrack(ctx, box, event, {
      threshold: 3,
      move: function (dx) {
        var at = Math.max(0, item.start + dx / (ctx.pps || 1));
        var len = editMixItemLen(item);
        var pts = editMixSnapPoints(ctx.clips, ctx.mix, ctx.t, copy ? "" : item.id);
        var tol = editMixSnapTol(ctx);
        var a = editMixSnap(at, pts, tol);
        var b = editMixSnap(at + len, pts, tol);
        at = Math.abs(a - at) <= Math.abs(b - at - len) ? a : b - len;
        box.style.left = Math.round(Math.max(0, at) * (ctx.pps || 1) * 10) / 10 + "px";
        box._mixAt = Math.max(0, editMixR3(at));
      },
      end: function (_dx, moved) {
        if (!moved || box._mixAt === undefined) {
          editMixRender(ctx);
          return;
        }
        var next = editMixClone(ctx.mix);
        var moving = editMixCloneItem(item);
        moving.start = box._mixAt;
        var sel;
        if (copy) {
          moving.id = editMixNewId(next, "s");
          next.tracks[t].items.push(moving);
          sel = { t: t, i: next.tracks[t].items.length - 1 };
        } else {
          next.tracks[t].items[i] = moving;
          sel = { t: t, i: i };
        }
        editHistPush(ctx.hist, before);
        ctx.mix = next;
        ctx.mixSel = sel;
        editChanged(ctx);
      }
    });
  }

  function editMixEdgePointer(ctx, t, i, edge, event) {
    if (!editPointerOk(ctx, event)) return;
    if (event.stopPropagation) event.stopPropagation();
    var item = ctx.mix.tracks[t].items[i];
    var dur = Number(((ctx.sounds.byId || {})[item.assetId] || {}).duration) || item.out;
    var x0 = edge === "in" ? item.start : item.start + editMixItemLen(item);
    var target = event.currentTarget || event.target;
    var box = target.parentNode;
    var draft = null;
    editTrack(ctx, target, event, {
      threshold: 1,
      move: function (dx) {
        var x = editMixSnap(x0 + dx / (ctx.pps || 1), editMixSnapPoints(ctx.clips, ctx.mix, ctx.t, item.id), editMixSnapTol(ctx));
        draft = editMixTrimItem(item, edge, x, dur);
        if (box && box.style) {
          box.style.left = Math.round(draft.start * (ctx.pps || 1) * 10) / 10 + "px";
          box.style.width = Math.max(4, Math.round(editMixItemLen(draft) * (ctx.pps || 1) * 10) / 10) + "px";
        }
      },
      end: function (_dx, moved) {
        if (moved && draft) editMixCommit(ctx, editMixReplaceItem(ctx, t, i, draft), { t: t, i: i });
        else editMixRender(ctx);
      }
    });
  }

  function editMixFadePointer(ctx, t, i, which, event) {
    if (!editPointerOk(ctx, event)) return;
    if (event.stopPropagation) event.stopPropagation();
    var item = ctx.mix.tracks[t].items[i];
    var start = which === "in" ? item.fadeIn : item.fadeOut;
    var target = event.currentTarget || event.target;
    var draft = null;
    editTrack(ctx, target, event, {
      threshold: 1,
      move: function (dx) {
        draft = editMixSetFade(item, which, start + (which === "in" ? dx : -dx) / (ctx.pps || 1));
        if (target.style) target.style.width = Math.max(10, Math.round((which === "in" ? draft.fadeIn : draft.fadeOut) * (ctx.pps || 1))) + "px";
      },
      end: function (_dx, moved) {
        if (moved && draft) editMixCommit(ctx, editMixReplaceItem(ctx, t, i, draft), { t: t, i: i });
        else editMixRender(ctx);
      }
    });
  }

  function editMixKeyPointer(ctx, t, i, k, event) {
    if (!editPointerOk(ctx, event)) return;
    if (event.stopPropagation) event.stopPropagation();
    var item = ctx.mix.tracks[t].items[i];
    var key = item.keys[k];
    var target = event.currentTarget || event.target;
    var band = target.parentNode;
    var rect = band && band.getBoundingClientRect ? band.getBoundingClientRect() : { top: 0, height: 0 };
    var height = Number(rect.height) || 1;
    var y0 = Number(event.clientY) || 0;
    var draft = null;
    ctx.mixSel = { t: t, i: i };
    ctx.mixKey = { t: t, i: i, k: k };
    editTrack(ctx, target, event, {
      threshold: 1,
      move: function (dx, ev) {
        var frac = editMixDbY(item.gainDb + key.db) + ((Number(ev.clientY) || 0) - y0) / height;
        draft = editMixKeyMove(item, k, key.t + dx / (ctx.pps || 1), editMixYDb(frac) - item.gainDb);
        if (target.style) {
          target.style.left = Math.round(draft.keys[k].t * (ctx.pps || 1) * 10) / 10 + "px";
          target.style.top = Math.round(editMixDbY(item.gainDb + draft.keys[k].db) * 1000) / 10 + "%";
        }
      },
      end: function (_dx, moved) {
        if (moved && draft) {
          editMixCommit(ctx, editMixReplaceItem(ctx, t, i, draft), { t: t, i: i });
          ctx.mixKey = { t: t, i: i, k: k };
        }
        editMixRender(ctx);
      }
    });
  }

  /* ---- 믹서(트랙 머리: 이름 · M · S · 이득, A1 덕킹) · 고른 아이템 ---- */

  function editMixButton(text, title, on, onClick) {
    var b = el("button", "vnew-edit-mix-btn" + (on ? " is-on" : ""), text);
    b.type = "button";
    b.title = title;
    b.setAttribute("aria-pressed", on ? "true" : "false");
    b.addEventListener("click", onClick);
    return b;
  }

  function editMixGainInput(value, title, onLive, onDone) {
    var input = el("input", "vnew-edit-mix-gain");
    input.type = "range";
    input.min = String(EDIT_MIX_GAIN_MIN);
    input.max = String(EDIT_MIX_GAIN_MAX);
    input.step = "0.5";
    input.value = String(value);
    input.title = title;
    input.setAttribute("aria-label", title);
    input.addEventListener("input", function () {
      onLive(Number(input.value));
    });
    input.addEventListener("change", function () {
      onDone(Number(input.value)); // 몸짓 하나(놓을 때) = 실행 취소 한 단계
    });
    return input;
  }

  function editMixRow(ctx, t, label, target, aud) {
    var row = el("div", "vnew-edit-mix-row" + (aud ? "" : " is-off"));
    row.appendChild(el("span", "vnew-edit-mix-title", label));
    row.appendChild(editMixButton("M", label + " 소리 끄기(M)", !!target.mute, function () {
      editMixCommit(ctx, editMixToggle(ctx.mix, t, "mute"));
      if (t < 0) editSeek(ctx, ctx.t);
    }));
    row.appendChild(editMixButton("S", label + " 이 트랙만 듣기(solo)", !!target.solo, function () {
      editMixCommit(ctx, editMixToggle(ctx.mix, t, "solo"));
      if (t < 0) editSeek(ctx, ctx.t);
    }));
    var shown = el("span", "vnew-edit-mix-db", Number(target.gainDb).toFixed(1) + " dB");
    row.appendChild(editMixGainInput(target.gainDb, label + " 트랙 이득(dB)", function (v) {
      shown.textContent = v.toFixed(1) + " dB";
    }, function (v) {
      var next = editMixClone(ctx.mix);
      (t < 0 ? next.a1 : next.tracks[t]).gainDb = v;
      editMixCommit(ctx, next);
    }));
    row.appendChild(shown);
    return row;
  }

  function editMixRenderMixer(ctx) {
    var box = ctx.dom.mixer;
    if (!box) return;
    clear(box);
    var aud = editMixAudible(ctx.mix);
    var a1 = ctx.mix.a1;
    var r1 = editMixRow(ctx, -1, "A1 대사", a1, aud.a1);
    var duck = el("label", "vnew-edit-mix-duck");
    var check = el("input");
    check.type = "checkbox";
    check.checked = !!a1.duck;
    check.addEventListener("change", function () {
      editMixCommit(ctx, editMixToggle(ctx.mix, -1, "duck"));
    });
    duck.appendChild(check);
    duck.appendChild(el("span", "", "자동 덕킹"));
    var depth = el("input", "vnew-edit-mix-duckdb");
    depth.type = "number";
    depth.min = "0";
    depth.max = "40";
    depth.step = "1";
    depth.value = String(a1.duckDb);
    depth.title = "대사가 나오는 동안 배경음 · 효과음을 낮출 dB(공격 · 놓기 0.15초)";
    depth.addEventListener("change", function () {
      var next = editMixClone(ctx.mix);
      next.a1.duckDb = editMixClamp(depth.value, 0, 40);
      editMixCommit(ctx, next);
    });
    duck.appendChild(depth);
    duck.appendChild(el("span", "", "dB"));
    r1.appendChild(duck);
    box.appendChild(r1);
    ctx.mix.tracks.forEach(function (track, t) {
      var row = editMixRow(ctx, t, track.name, track, aud.tracks[t]);
      row.appendChild(editMixButton("✕", track.name + " 트랙 지우기", false, function () {
        if (track.items.length && !editConfirm(track.name + " 트랙과 소리 " + track.items.length + "개를 지울까요?")) return;
        editMixCommit(ctx, editMixRemoveTrack(ctx.mix, t), null);
      }));
      box.appendChild(row);
    });
    var bar = el("div", "vnew-edit-mix-bar");
    var add = editMixButton("+ 오디오 트랙", "배경음 · 효과음 트랙 더하기(4개까지)", false, function () {
      editMixCommit(ctx, editMixAddTrack(ctx.mix));
    });
    add.disabled = ctx.mix.tracks.length >= EDIT_MIX_MAX_TRACKS;
    bar.appendChild(add);
    bar.appendChild(editMixButton("🧲 붙기", "클립 경계 · 재생헤드 · 다른 소리에 붙기 (Shift+N)", edit215cSnapOn(ctx), function () {
      editDo(ctx, "snap");
    }));
    bar.appendChild(editMixButton("스템도 함께", "내보낼 때 대사만 · 배경음 · 효과음 · mix.wav 를 zip 으로도(Premiere 마무리용)", !!ctx.mixStems, function () {
      ctx.mixStems = !ctx.mixStems;
      editMixRenderMixer(ctx);
    }));
    var ref = editMixItemRef(ctx);
    if (ref) {
      var it = ref.item;
      var info = el("span", "vnew-edit-mix-sel", "고른 소리 " + editMixItemLen(it).toFixed(2) + "초");
      bar.appendChild(info);
      var gshown = el("span", "vnew-edit-mix-db", it.gainDb.toFixed(1) + " dB");
      bar.appendChild(editMixGainInput(it.gainDb, "고른 소리 이득(dB)", function (v) {
        gshown.textContent = v.toFixed(1) + " dB";
      }, function (v) {
        var item = editMixCloneItem(it);
        item.gainDb = v;
        editMixCommit(ctx, editMixReplaceItem(ctx, ref.t, ref.i, item), { t: ref.t, i: ref.i });
      }));
      bar.appendChild(gshown);
    }
    box.appendChild(bar);
  }

  /* POST /edit/export 바디(스템을 고른 때만 — 그 밖은 190 바디 그대로). */
  function editMixExportBody(ctx) {
    return { etag: ctx.etag, variant: ctx.variant, stems: true };
  }

  /* ---- 내 소리(올리기 · 목록 · 넣기 · 지우기) ---- */

  function editMixSoundsPath(ctx) {
    return editPath(ctx) + "/audio-assets";
  }

  function editMixLoadSounds(ctx) {
    ctx.sounds.loaded = true;
    return api(editMixSoundsPath(ctx)).then(function (res) {
      if (!editAlive(ctx)) return;
      editMixAbsorbSounds(ctx, res);
    }, function (err) {
      ctx.sounds.error = errMessage(err);
      editMixRenderSounds(ctx);
    });
  }

  function editMixAbsorbSounds(ctx, res) {
    ctx.sounds.list = Array.isArray((res || {}).assets) ? res.assets : [];
    ctx.sounds.byId = {};
    ctx.sounds.list.forEach(function (a) {
      ctx.sounds.byId[a.assetId] = a;
    });
    ctx.sounds.error = "";
    editMixRenderSounds(ctx);
    editMixRender(ctx);
  }

  function editMixRenderSounds(ctx) {
    var box = ctx.dom.sounds;
    if (!box) return;
    clear(box);
    var head = el("button", "vnew-edit-mix-btn vnew-edit-sounds-title", (ctx.sounds.open ? "▾ " : "▸ ") + "내 소리 (배경음 · 효과음)");
    head.type = "button";
    head.setAttribute("aria-expanded", ctx.sounds.open ? "true" : "false");
    head.addEventListener("click", function () {
      ctx.sounds.open = !ctx.sounds.open;
      if (ctx.sounds.open && !ctx.sounds.loaded) editMixLoadSounds(ctx);
      editMixRenderSounds(ctx);
    });
    box.appendChild(head);
    if (!ctx.sounds.open) return;
    box.appendChild(el("p", "vnew-hint", "배경음 · 효과음 파일(mp3 · wav · m4a · aac · ogg, 50MB · 10분까지)을 올려 오디오 트랙에 넣습니다. AI 로 보내지 않습니다."));
    var file = el("input", "vnew-edit-sounds-file");
    file.type = "file";
    file.accept = EDIT_MIX_ACCEPT;
    file.addEventListener("change", function () {
      var f = file.files && file.files[0];
      if (f) editMixUpload(ctx, f);
    });
    box.appendChild(file);
    if (ctx.sounds.busy) box.appendChild(el("span", "vnew-hint", "올리는 중…"));
    if (ctx.sounds.error) box.appendChild(el("p", "vnew-error", ctx.sounds.error));
    var list = el("ul", "vnew-edit-sounds-list");
    ctx.sounds.list.forEach(function (a) {
      var row = el("li", "vnew-edit-sounds-item");
      row.appendChild(el("span", "vnew-edit-sounds-name", a.name + " · " + editTimeText(Number(a.duration) || 0, false)));
      row.appendChild(editMixButton("넣기", "재생헤드 자리에 넣기(고른 소리의 트랙, 없으면 첫 트랙)", false, function () {
        editMixInsert(ctx, a);
      }));
      var del = editMixButton("지우기", a.used ? "타임라인에서 쓰는 소리는 지울 수 없습니다" : "내 소리에서 지우기", false, function () {
        editMixDeleteSound(ctx, a);
      });
      del.disabled = a.used === true;
      row.appendChild(del);
      list.appendChild(row);
    });
    box.appendChild(list);
  }

  function editMixUpload(ctx, f) {
    var form = new FormData();
    form.append("file", f, f.name);
    ctx.sounds.busy = true;
    ctx.sounds.error = "";
    editMixRenderSounds(ctx);
    api(editMixSoundsPath(ctx), { method: "POST", body: form }).then(function (res) {
      ctx.sounds.busy = false;
      if (editAlive(ctx)) editMixAbsorbSounds(ctx, res);
    }, function (err) {
      ctx.sounds.busy = false;
      ctx.sounds.error = "올리지 못했습니다: " + errMessage(err);
      editMixRenderSounds(ctx);
    });
  }

  function editMixInsert(ctx, asset) {
    var next = ctx.mix.tracks.length ? editMixClone(ctx.mix) : editMixAddTrack(ctx.mix);
    var t = ctx.mixSel && next.tracks[ctx.mixSel.t] ? ctx.mixSel.t : 0;
    var made = editMixAddItem(next, t, asset, ctx.t, editLimit(ctx, "maxTotalSeconds", 300));
    if (!made) {
      editNote(ctx, "재생헤드 자리에 넣을 수 없습니다(타임라인 한도).");
      return;
    }
    editMixCommit(ctx, made, { t: t, i: made.tracks[t].items.length - 1 });
  }

  function editMixDeleteSound(ctx, asset) {
    if (!editConfirm("‘" + asset.name + "’ 소리를 지울까요?")) return;
    api(editMixSoundsPath(ctx) + "/" + encodeURIComponent(asset.assetId), { method: "DELETE" }).then(function (res) {
      if (editAlive(ctx)) editMixAbsorbSounds(ctx, res);
    }, function (err) {
      ctx.sounds.error = errMessage(err);
      editMixRenderSounds(ctx);
    });
  }

  /* 쓰는 소리의 파형 피크를 하나씩 받는다. */
  function editMixQueuePeaks(ctx) {
    if (ctx.sounds.peakBusy) return;
    var want = null;
    ctx.mix.tracks.forEach(function (track) {
      track.items.forEach(function (it) {
        if (!want && !ctx.sounds.peaks[it.assetId] && !ctx.sounds.peakFail[it.assetId]) want = it.assetId;
      });
    });
    if (!want) return;
    ctx.sounds.peakBusy = true;
    api(editMixSoundsPath(ctx) + "/" + encodeURIComponent(want)).then(function (res) {
      ctx.sounds.peakBusy = false;
      ctx.sounds.peaks[want] = (res && res.peaks) || { perSecond: 20, values: [] };
      if (res && res.asset) ctx.sounds.byId[want] = res.asset;
      if (editAlive(ctx)) editMixRender(ctx);
    }, function () {
      ctx.sounds.peakBusy = false;
      ctx.sounds.peakFail[want] = true;
      if (editAlive(ctx)) editMixQueuePeaks(ctx);
    });
  }

  /* ---- 미리듣기(WebAudio) ---- */

  function editMixBuffer(ctx, ac, assetId) {
    var cache = ctx.sounds.buffers;
    if (cache[assetId]) return cache[assetId];
    cache[assetId] = fetch(editMixSoundsPath(ctx) + "/" + encodeURIComponent(assetId) + "/file", {
      headers: { Authorization: "Bearer " + token() }
    }).then(function (response) {
      if (!response.ok) throw new Error("audio " + response.status);
      return response.arrayBuffer();
    }).then(function (data) {
      return new Promise(function (resolve, reject) {
        var p = ac.decodeAudioData(data, resolve, reject);
        if (p && p.then) p.then(resolve, reject);
      });
    });
    cache[assetId].catch(function () {
      delete cache[assetId];
    });
    return cache[assetId];
  }

  function editMixCurve(fn, from, dur) {
    var n = Math.max(2, Math.min(200000, Math.ceil(dur * EDIT_MIX_CURVE_HZ) + 1));
    var curve = new Float32Array(n);
    for (var k = 0; k < n; k++) curve[k] = Math.max(0, fn(from + dur * k / (n - 1)));
    return curve;
  }

  function editMixStop(ctx) {
    var live = ctx.mixLive;
    ctx.mixLive = null;
    if (!live) return;
    live.nodes.forEach(function (node) {
      try {
        if (node.stop) node.stop();
        node.disconnect();
      } catch (error) {
        /* 이미 멈췄다 */
      }
    });
  }

  /* 지금 재생헤드부터 모든 들리는 트랙을 예약한다(A1 은 <video>). */
  function editMixStart(ctx) {
    editMixStop(ctx);
    var aud = editMixAudible(ctx.mix);
    var has = ctx.mix.tracks.some(function (t, n) { return aud.tracks[n] && t.items.length; });
    var Ctor = window.AudioContext || window.webkitAudioContext;
    if (!has || !Ctor) return;
    var ac = ctx.mixAc || (ctx.mixAc = new Ctor());
    if (ac.resume) ac.resume();
    var t0 = ctx.t;
    var when = ac.currentTime + 0.05;
    var live = { ac: ac, nodes: [], startAt: when, startT: t0 };
    ctx.mixLive = live;
    var duck = ctx.mix.a1.duck && aud.a1 ? editMixDuckPoints(editMixDialogueSpans(ctx.clips, function (clip) {
      var mid = (ctx.materials[clip.sourceKey] || {}).mid;
      var m = ctx.media[mid];
      return m && m.data ? m.data.soundRanges : [];
    }), Number(ctx.mix.a1.duckDb) || 0) : [];
    var total = editStarts(ctx.clips).total;
    ctx.mix.tracks.forEach(function (track, n) {
      if (!aud.tracks[n]) return;
      var bus = ac.createGain();
      var tg = Math.pow(10, track.gainDb / 20);
      var rest = Math.max(0.05, total - t0);
      if (duck.length) {
        bus.gain.setValueCurveAtTime(editMixCurve(function (x) { return tg * Math.pow(10, editMixPointsAt(duck, x) / 20); }, t0, rest), when, rest);
      } else {
        bus.gain.value = tg;
      }
      bus.connect(ac.destination);
      live.nodes.push(bus);
      track.items.forEach(function (it) {
        var len = editMixItemLen(it);
        if (it.start + len <= t0) return;
        editMixBuffer(ctx, ac, it.assetId).then(function (buffer) {
          if (ctx.mixLive !== live) return;
          var now = live.ac.currentTime;
          var tl = live.startT + Math.max(0, now - live.startAt); // 받는 동안 흐른 시간
          var from = Math.max(0, tl - it.start);
          if (from >= len) return;
          var at = live.startAt + Math.max(0, it.start - live.startT);
          if (at < now) at = now + 0.01;
          var src = ac.createBufferSource();
          src.buffer = buffer;
          var g = ac.createGain();
          var dur = len - from;
          g.gain.setValueCurveAtTime(editMixCurve(function (x) { return editMixGainAt(it, x); }, from, dur), at, dur);
          src.connect(g);
          g.connect(bus);
          src.start(at, it.in + from, dur);
          live.nodes.push(src, g);
        }, function () {
          /* 못 받은 소리는 미리듣기에서 빠진다(내보내기는 서버가 섞는다) */
        });
      });
    });
  }

  /* 재생 중 매 틱: A1 이득 · mute/solo 를 <video> 에, WebAudio 가 재생헤드와 0.15초 넘게 어긋나면 다시 예약. */
  function editMixSync(ctx) {
    var aud = editMixAudible(ctx.mix);
    var vol = Math.min(1, Math.pow(10, (Number(ctx.mix.a1.gainDb) || 0) / 20));
    ctx.vids.concat(ctx.audio ? [ctx.audio] : []).forEach(function (v) {
      try {
        v.volume = vol;
        if (!aud.a1) v.muted = true;
      } catch (error) {
        /* 무시 */
      }
    });
    if (!ctx.playing) {
      editMixStop(ctx);
      return;
    }
    var live = ctx.mixLive;
    var expect = live ? live.startT + Math.max(0, live.ac.currentTime - live.startAt) : null;
    if (!live || Math.abs(expect - ctx.t) > EDIT_MIX_DRIFT_S) editMixStart(ctx);
  }

  /* ================================================================ 작업 215a: ⑦ 오디오 트랙 끝 */

  /* ==== 작업 217 시작 ==== */
  /* 섞인 소리 분리하기(⑥ 사운드 원본 · ⑦ 재료 하나). GET /audio-separate?scope=sound|edit&variant= 가 원본마다 상태 · 견적을 준다.
     [섞인 소리 분리하기 (AI, $x)] → 견적 다시(dryRun) → 확인창(금액) → POST(confirmUsd). 409 CostMismatch 면 보기를 다시 읽어
     단추에 새 금액을 보이고 보내지 않는다(다시 눌러야 한다 — 작업 206). 자동으로 다시 보내지 않는다. 진행 중이면 SEP_POLL_MS 마다 GET.
     끝나면 [분리된 목소리 쓰기](⑥ 반영 · ⑦ 내보내기 A1) · [배경을 A2에](⑦ 내보내기, 배경음 트랙) 체크 — PUT. 소리만 보낸다(서버가 뽑는다). */
  var SEP_POLL_MS = 5000;
  var SEP_FRESH_MS = 20000;
  var SEP_BUTTON = "섞인 소리 분리하기";

  function sepPath() {
    return "/api/vnew/p/" + encodeURIComponent(state.projectId) + "/audio-separate";
  }

  function sepButtonText(item) {
    var est = (item && item.estimate) || null;
    var st = ((item && item.state) || {}).status;
    var money = est && est.label ? est.label : "가격 확인 불가";
    return (st === "done" ? "다시 분리하기" : SEP_BUTTON) + " (AI, " + money + ")";
  }

  function sepConfirmText(item, est) {
    var e = est || {};
    var secs = typeof e.seconds === "number" ? e.seconds.toFixed(1) + "초" : "";
    return "이 소리(" + (item.label || "원본") + (secs ? " · " + secs : "") + ")를 AI(" + (e.modelLabel || "분리 모델") +
      ")로 목소리와 배경으로 나눕니다. 영상 그림은 보내지 않고 소리만 보냅니다.\n비용: " +
      (e.priceKnown ? e.label : "가격 확인 불가") + "\n계속할까요?";
  }

  function sepOf(ctx, scope) {
    if (!ctx.sep || ctx.sep.variant !== ctx.variant) {
      ctx.sep = { scope: scope, variant: ctx.variant, view: null, at: 0, busy: false, msg: "", bad: false, pick: "", timer: 0,
                  loading: false, box: null, paint: null, alive: null };
    }
    return ctx.sep;
  }

  function sepItem(sep) {
    var items = ((sep.view || {}).items) || [];
    if (sep.scope === "sound") return items[0] || null;
    return items.filter(function (i) { return i.sourceKey === sep.pick; })[0] || null;
  }

  function sepLoad(sep) {
    if (sep.loading || !sep.alive()) return;
    sep.loading = true;
    api(sepPath() + "?scope=" + sep.scope + "&variant=" + encodeURIComponent(sep.variant || ""))
      .then(function (view) {
        sep.loading = false;
        sep.view = view || {};
        sep.at = Date.now();
        if (!sep.alive()) return;
        sep.paint();
        var running = (sep.view.items || []).some(function (i) {
          var st = (i.state || {}).status;
          return st === "running" || st === "starting";
        });
        if (running && !sep.timer) {
          sep.timer = window.setTimeout(function () {
            sep.timer = 0;
            sepLoad(sep);
          }, SEP_POLL_MS);
        }
      })
      .catch(function (err) {
        sep.loading = false;
        if (err && err.status === 404) {
          sep.view = { items: [] }; // 기능 꺼짐 — 그리지 않는다
          if (sep.alive()) sep.paint();
          return;
        }
        sep.msg = "분리 정보를 읽지 못했습니다: " + errMessage(err);
        sep.bad = true;
        if (sep.alive()) sep.paint();
      });
  }

  function sepBody(sep, item, extra) {
    var body = { scope: sep.scope, variant: sep.variant };
    if (sep.scope === "edit") body.sourceKey = item.sourceKey;
    Object.keys(extra || {}).forEach(function (k) { body[k] = extra[k]; });
    return JSON.stringify(body);
  }

  function sepSay(sep, text, bad) {
    sep.msg = text;
    sep.bad = !!bad;
    if (sep.alive()) sep.paint();
  }

  function sepRun(sep, item) {
    if (sep.busy || !item) return;
    sep.busy = true;
    sepSay(sep, "금액을 확인하는 중…", false);
    api(sepPath(), { method: "POST", body: sepBody(sep, item, { dryRun: true }) })
      .then(function (result) {
        var est = (result || {}).estimate || {};
        if (!sep.alive()) return null;
        if (!window.confirm(sepConfirmText(item, est))) {
          sep.busy = false;
          sepSay(sep, "", false);
          return null;
        }
        sepSay(sep, "소리를 보내는 중…", false);
        return api(sepPath(), { method: "POST", body: sepBody(sep, item, { confirmUsd: est.priceKnown ? est.usd : 0 }) });
      })
      .then(function (result) {
        if (!result) return;
        sep.busy = false;
        sepSay(sep, "분리를 시작했습니다. 1~3분쯤 걸립니다.", false);
        sepLoad(sep);
      })
      .catch(function (err) {
        sep.busy = false;
        if (isCostMismatch(err)) {
          sepSay(sep, "금액이 바뀌었습니다. 단추의 새 금액을 확인한 뒤 다시 눌러 주세요.", true);
          sepLoad(sep); // 새 견적으로 단추 글을 바꾼다(보내지 않는다)
          return;
        }
        sepSay(sep, "분리하지 못했습니다: " + errMessage(err), true);
      });
  }

  function sepUse(sep, item, field, value) {
    if (sep.busy || !item) return;
    sep.busy = true;
    var extra = {};
    extra[field] = value;
    api(sepPath(), { method: "PUT", body: sepBody(sep, item, extra) })
      .then(function (result) {
        sep.busy = false;
        item.state = (result || {}).state || item.state;
        sepSay(sep, field === "useVoice" ? (value ? "분리된 목소리를 씁니다." : "원래 소리를 씁니다.") :
          (value ? "배경을 A2(배경음)에 놓습니다." : "배경은 넣지 않습니다."), false);
      })
      .catch(function (err) {
        sep.busy = false;
        sepSay(sep, "바꾸지 못했습니다: " + errMessage(err), true);
      });
  }

  function sepCheck(text, checked, disabled, onChange) {
    var label = el("label", "vnew-sep-check");
    var box = document.createElement("input");
    box.type = "checkbox";
    box.checked = !!checked;
    box.disabled = !!disabled;
    box.addEventListener("change", function () { onChange(box.checked); });
    label.appendChild(box);
    label.appendChild(document.createTextNode(" " + text));
    return label;
  }

  function sepPaintBox(sep) {
    var box = sep.box;
    if (!box) return;
    clear(box);
    var items = ((sep.view || {}).items) || [];
    if (!sep.view) { // 처음에는 서버를 부르지 않는다 — 열면 상태 · 금액을 읽는다
      box.hidden = false;
      var open = el("button", "outline-button vnew-sep-open", sep.loading ? "금액을 읽는 중…" : SEP_BUTTON + " (AI, 금액 보기)");
      open.type = "button";
      open.disabled = sep.loading;
      open.title = "대사와 배경이 섞인 옛 영상의 소리를 AI로 나눕니다. 누르면 상태와 금액을 먼저 보입니다";
      open.addEventListener("click", function () {
        sepLoad(sep);
        sep.paint();
      });
      box.appendChild(open);
      return;
    }
    if (!items.length) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    var row = el("div", "vnew-sep-row");
    row.appendChild(el("span", "vnew-sep-title", "섞인 소리"));
    if (sep.scope === "edit") {
      var sel = document.createElement("select");
      sel.className = "vnew-sep-pick";
      sel.setAttribute("aria-label", "분리할 재료");
      items.forEach(function (i) {
        var opt = document.createElement("option");
        opt.value = i.sourceKey;
        opt.textContent = (i.label || "영상") + ((i.state || {}).status === "done" ? " · 분리됨" : "");
        sel.appendChild(opt);
      });
      if (!sepItem(sep)) sep.pick = items[0].sourceKey;
      sel.value = sep.pick;
      sel.addEventListener("change", function () {
        sep.pick = String(sel.value || "");
        sep.msg = "";
        sep.paint();
      });
      row.appendChild(sel);
    }
    var item = sepItem(sep);
    var st = ((item && item.state) || {});
    var live = st.status === "running" || st.status === "starting";
    var btn = el("button", "outline-button vnew-sep-run", live ? "분리하는 중…" : sepButtonText(item));
    btn.type = "button";
    btn.disabled = sep.busy || live || !item || item.hasAudio === false;
    btn.title = item && item.hasAudio === false ? "이 영상에는 소리가 없습니다" :
      "대사와 배경(환경음 · 음악)이 섞인 소리를 AI로 목소리 · 배경으로 나눕니다(누르면 금액을 먼저 보입니다)";
    btn.addEventListener("click", function () { sepRun(sep, sepItem(sep)); });
    row.appendChild(btn);
    box.appendChild(row);
    if (st.status === "done") {
      var use = el("div", "vnew-sep-row vnew-sep-use");
      use.appendChild(sepCheck("분리된 목소리 쓰기", st.useVoice, sep.busy, function (v) { sepUse(sep, item, "useVoice", v); }));
      if (sep.scope === "edit") {
        use.appendChild(sepCheck("배경을 A2(배경음)에 놓기", st.bgOnA2, sep.busy || !st.useVoice,
          function (v) { sepUse(sep, item, "bgOnA2", v); }));
      } else {
        use.appendChild(el("span", "vnew-hint", "⑥ 반영 영상에는 목소리만 들어갑니다. 배경은 ⑦ A2에 놓을 수 있습니다."));
      }
      [["목소리", st.voiceUrl], ["배경", st.bgUrl]].forEach(function (pair) {
        if (!pair[1]) return;
        var audio = document.createElement("audio");
        audio.className = "vnew-sep-audio";
        audio.controls = true;
        audio.preload = "none";
        audio.src = pair[1];
        audio.setAttribute("aria-label", pair[0] + " 듣기");
        use.appendChild(el("span", "vnew-sep-alabel", pair[0]));
        use.appendChild(audio);
      });
      box.appendChild(use);
    }
    var text = sep.msg || (st.status === "failed" ? "분리하지 못했습니다: " + (st.error || "") : live ? "소리를 분리하는 중입니다…" : "");
    if (text) {
      var line = el("p", "vnew-hint vnew-sep-status" + (sep.bad || (!sep.msg && st.status === "failed") ? " is-bad" : ""), text);
      line.setAttribute("role", "status");
      box.appendChild(line);
    }
  }

  function sepMount(sep, host, alive) {
    sep.box = el("div", "vnew-sep");
    sep.box.hidden = true;
    sep.alive = alive;
    sep.paint = function () { sepPaintBox(sep); };
    host.appendChild(sep.box);
    sep.paint();
    if (sep.view && Date.now() - sep.at > SEP_FRESH_MS) sepLoad(sep); // 연 적이 있을 때만 다시 읽는다
  }

  /* ⑥ 사운드: 원본 플레이어 아래. */
  function sepMountSound(ctx, host) {
    var sep = sepOf(ctx, "sound");
    sepMount(sep, host, function () { return soundAlive(ctx); });
  }

  /* ⑦ 재료: 테이크 고르기 줄 아래(재료 하나씩 고른다 — 기본은 고른 테이크). */
  function sepMountBin(ctx, host) {
    var sep = sepOf(ctx, "edit");
    if (!sep.pick && ctx.binTake) sep.pick = String(ctx.binTake[ctx.variant] || "");
    sepMount(sep, host, function () { return editAlive(ctx); });
  }
  /* ==== 작업 217 끝 ==== */

  /* ---------------------------------------------------------------- ⑦ AI로 고치기(지시서 116)

     재료(테이크·컷) 하나를 이전 프롬프트 + 결과 영상 + 사용자 수정 요청으로 고쳐 같은 모델로 새 버전(v2…)을 만든다. 서버
     /api/vnew/p/<pid>/edit/retake/<mid>(retake.py). 켬(GET /edit 의 retake.enabled)일 때만 재료 줄에 [AI로 고치기]·머리 줄에
     [고른 클립 AI로 고치기]가 생긴다. 패널은 ⑦ 루트 안 고정 위치 대화상자이고, 처음 열 때 뼈대(영상·입력칸)를 한 번 만든 뒤 조회 때
     바뀌는 칸만 다시 그린다(입력 중인 글·재생이 끊기지 않게). 새 프롬프트 만들기·다시 만들기는 202 + 조회(초안 2초·버전 5초).
     [바꾸기]는 실행 취소 한 단계(editCommit)이고 v1 [바꾸기]가 되돌리기다. 패널이 열린 동안 ⑦ 단축키는 쓰지 않는다(Esc 는 닫기).
     글은 모두 textContent·value. 검사는 check_vpoc rtk-j. */

  var EDIT_FIX_DRAFT_POLL_MS = 2000; // 새 프롬프트 만드는 중 다시 읽기
  var EDIT_FIX_RUN_POLL_MS = 5000; // 다시 만드는 중 다시 읽기(서버 조회 간격과 같다)
  var EDIT_FIX_SOURCE = {
    log: "기록",
    reconstructed: "재구성 — 기록이 없던 영상이라 칸 상태의 마지막 프롬프트를 보입니다",
    retake: "AI로 고친 버전의 프롬프트"
  };
  var EDIT_FIX_STATUS = {
    queued: "대기 중",
    preparing: "준비 중",
    submitted: "만드는 중",
    completed: "완성",
    failed: "실패",
    running: "만드는 중",
    done: "완성"
  };

  /* ---- 순수 함수(DOM 없음) ---- */

  /* UTF-8 바이트 수(서버 상한과 같은 단위). */
  function editFixBytes(text) {
    var s = String(text || "");
    var n = 0;
    for (var i = 0; i < s.length; i++) {
      var c = s.charCodeAt(i);
      if (c < 0x80) n += 1;
      else if (c < 0x800) n += 2;
      else if (c >= 0xd800 && c <= 0xdbff && i + 1 < s.length) {
        n += 4;
        i += 1;
      } else n += 3;
    }
    return n;
  }

  /* 1234567 → "1,234,567"(바이트 계기·문구). */
  function editFixComma(n) {
    return String(Math.round(Number(n) || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  }

  function editFixMoney(usd) {
    return "$" + (Number(usd) || 0).toFixed(2);
  }

  /* 버전 이름(v1 원본·v2…)과 조사(로·를 — 원본은 받침이 있어 으로·을). */
  function editFixVerName(version) {
    var v = version || {};
    return v.original ? "v1 원본" : "v" + (v.n || "?");
  }

  function editFixVerTo(version) {
    return editFixVerName(version) + ((version || {}).original ? "으로" : "로");
  }

  function editFixVerObj(version) {
    return editFixVerName(version) + ((version || {}).original ? "을" : "를");
  }

  /* 버전 하나의 진행 줄 글. */
  function editFixStatusText(version) {
    var v = version || {};
    var name = editFixVerName(v);
    if (v.status === "failed") return name + " 실패 — " + (v.error || "다시 만들지 못했습니다.");
    if (v.status === "completed") return name + " 완성 — [바꾸기]·[재료에 추가]로 타임라인에 쓸 수 있습니다.";
    return name + " " + (EDIT_FIX_STATUS[v.status] || v.status || "") + (Number(v.attempt) > 1 ? " (" + v.attempt + "번째 시도)" : "") + "…" +
      (v.contentNote ? " " + v.contentNote : ""); // 작업 195: 콘텐츠 검사 거절 뒤 순화해 한 번 더 내는 중
  }

  /* 이전 시도 한 줄 머리(출처·시각·모델·상태·참고 수). */
  function editFixAttemptMeta(attempt) {
    var a = attempt || {};
    var parts = [];
    if (a.source === "retake") parts.push("v" + (a.version || "?") + " 다시 만들기" + (Number(a.attempt) > 1 ? " · " + a.attempt + "번째 시도" : ""));
    else if (a.source === "reconstructed") parts.push("재구성(기록 없음)");
    else parts.push((a.run ? "실사 " + a.run + "회차" : "⑤ 영상") + (a.attempt ? " · " + a.attempt + "번째 시도" : ""));
    if (a.at) parts.push(String(a.at).replace("T", " ").replace("Z", ""));
    if (a.model) parts.push(String(a.model));
    if (a.status) parts.push(EDIT_FIX_STATUS[a.status] || String(a.status));
    parts.push(a.refCount === null || a.refCount === undefined ? "참고 이미지 기록 없음" : "참고 이미지 " + a.refCount + "장");
    if (a.truncated) parts.push("8KB 에서 잘림");
    return parts.join(" · ");
  }

  /* 다음 다시 만들기 번호: 마지막 버전이 실패면 그 번호(같은 번호로 다시), 아니면 가장 큰 번호 + 1. */
  function editFixNextN(view) {
    var list = ((view || {}).versions || []).filter(function (v) {
      return v && !v.original;
    });
    var last = list[list.length - 1];
    if (last && last.status === "failed") return Number(last.n) || 2;
    var top = 1;
    list.forEach(function (v) {
      if (Number(v.n) > top) top = Number(v.n);
    });
    return top + 1;
  }

  /* 타임라인 클립 원본을 같은 칸의 다른 버전(newKey)으로 바꾼다. 대상 = 원본이 keys(그 칸의 버전 원본들)에 든 클립, ids 가 있으면 그 중
     그 클립만. in/out 은 새 길이(duration) 안으로 줄이고(속도 반영 최소 길이), 속도·소리 끔·id 는 그대로, 이름은 비었거나 oldLabels 에 든
     이름(재료 이름)일 때만 newLabel. 이미 newKey 인 클립은 그대로. 반환 {clips, changed, skipped}(skipped = 길이 안에 못 넣은 클립). */
  function editSwapSource(clips, ids, keys, newKey, duration, minLen, newLabel, oldLabels) {
    var keySet = {};
    (keys || []).forEach(function (k) {
      if (k) keySet[k] = true;
    });
    var idSet = null;
    if (ids && ids.length) {
      idSet = {};
      ids.forEach(function (id) {
        idSet[id] = true;
      });
    }
    var dur = Number(duration) || 0;
    var changed = 0;
    var skipped = 0;
    var out = editCloneClips(clips).map(function (clip) {
      if (!keySet[clip.sourceKey] || (idSet && !idSet[clip.id]) || clip.sourceKey === newKey) return clip;
      var min = editMinSource(minLen, editSpeedOf(clip));
      var end = Math.min(Number(clip.out), dur);
      var start = Math.min(Number(clip.in), Math.max(0, end - min));
      if (!(dur > 0) || end - start < min - 1e-9) {
        skipped += 1;
        return clip;
      }
      var next = editCloneClip(clip);
      next.sourceKey = newKey;
      next.in = editRound3(start);
      next.out = editRound3(end);
      if (newLabel && (!clip.label || (oldLabels || []).indexOf(clip.label) >= 0)) next.label = String(newLabel).slice(0, 40);
      changed += 1;
      return next;
    });
    return { clips: out, changed: changed, skipped: skipped };
  }

  /* ---- 재료 줄 · 머리 줄 버튼 ---- */

  function editFixOn(ctx) {
    return !!ctx && ctx.retakeOn === true;
  }

  /* 고른 클립의 재료(AI로 고칠 수 있을 때만). */
  function editFixSelMat(ctx) {
    var clip = editSelected(ctx);
    if (!clip) return null;
    var mat = ctx.materials[clip.sourceKey];
    return mat && mat.retake && mat.retake.can === true ? mat : null;
  }

  /* [고른 클립 AI로 고치기]: disabled·title 만 바꾼다(editRenderTools 가 부른다 — 노드를 만들지 않는다).
     작업 192: 도구줄 [고른 컷 AI로 고치기](editMultiBox)와 재료 줄 [이 컷 AI로 고치기]가 고른 컷(하나 또는 여럿)을 따른다. */
  function editFixRenderSel(ctx) {
    var btn = ctx.dom.fixSel;
    if (btn) {
      var on = editFixOn(ctx);
      var target = on ? editFixSelTarget(ctx) : null;
      btn.style.display = on ? "" : "none";
      btn.disabled = !target || ctx.busy;
      btn.title = target ? target.error || editFixSelTitle(target) : "타임라인에서 AI로 고칠 컷을 고르세요(Ctrl/Cmd · Shift+누르기로 여러 개)";
      btn.setAttribute("aria-label", btn.title);
    }
    if (ctx.dom.binFixPaint) ctx.dom.binFixPaint();
  }

  /* ---- 작업 192: 고른 컷(하나 또는 여럿)으로 AI로 고치기 ---- */

  /* 고른 클립들 이름 한 줄: 모두 "컷 N" 이면 "컷 a" · "컷 a–b"(가장 작은 번호–가장 큰 번호), 아니면 이름(재료 이름)을 · 로(4개 넘으면 "외 n개"). */
  function editFixSelNames(clips, matLabel) {
    var names = (clips || []).map(function (c) {
      return String((c && c.label) || matLabel || "클립");
    });
    var nums = names.map(function (name) {
      var m = /^컷 (\d+)$/.exec(name);
      return m ? Number(m[1]) : NaN;
    });
    if (nums.length && nums.every(isFinite)) {
      var lo = Math.min.apply(null, nums);
      var hi = Math.max.apply(null, nums);
      return lo === hi ? "컷 " + lo : "컷 " + lo + "–" + hi;
    }
    var uniq = names.filter(function (name, i) {
      return names.indexOf(name) === i;
    });
    return uniq.slice(0, 4).join(" · ") + (uniq.length > 4 ? " 외 " + (uniq.length - 4) + "개" : "");
  }

  /* 타임라인에서 고른 컷 → {mid, sourceKey, ids, range: {in, out}(원본 초 — 가장 이른 in ~ 가장 늦은 out), names}. 고른 클립이 없으면 null.
     원본(sourceKey)이 서로 다르면 {error: EDIT_FIX_MIXED}, 그 재료를 AI로 고칠 수 없으면 {error: 이유}. 순수에 가깝다(ctx 만 읽는다).
     작업 194: 원본이 섞여도 모두 한 칸(같은 retake cellKey — 원본과 그 버전)이거나, 한 원본이라도 원본 초가 이어지지 않으면 segments 길:
     {mid: 그 칸 원본(v1) 재료, segments [{sourceKey, in, out}](타임라인 순서), total(합 초), range {in 0, out 합(보기 GET 용)}}.
     다른 칸이 섞이면 EDIT_FIX_MIXED, 속도 1이 아니거나 고른 컷 사이 디졸브 · 8개 넘음은 그 안내. */
  function editFixSelTarget(ctx) {
    var indexes = editSelIndexes(ctx);
    if (!indexes.length) return null;
    var clips = indexes.map(function (i) {
      return ctx.clips[i];
    });
    var key = clips[0].sourceKey;
    if (editFixNeedsSegments(clips)) return editFixSegTarget(ctx, clips, indexes); // 작업 194
    var mat = ctx.materials[key];
    if (!mat || !mat.retake || mat.retake.can !== true) {
      return { error: (mat && mat.retake && mat.retake.reason) || "이 컷은 AI로 고칠 수 없습니다." };
    }
    var a = Math.min.apply(null, clips.map(function (c) {
      return Number(c.in);
    }));
    var b = Math.max.apply(null, clips.map(function (c) {
      return Number(c.out);
    }));
    return { mid: String(mat.mid || ""), sourceKey: key, ids: clips.map(function (c) {
      return String(c.id);
    }), range: { in: editRound3(a), out: editRound3(b) }, names: editFixSelNames(clips, mat.label) };
  }

  function editFixSelTitle(target) {
    var t = target || {};
    var r = t.range || {};
    var what = (t.names ? t.names + " · " : "") + (t.segments ? "합 " + editFixSecs(t.total) : editFixSecs(r.in) + "–" + editFixSecs(r.out)) + "초";
    return (t.ids && t.ids.length > 1 ? "고른 컷 " + t.ids.length + "개(" + what + ")를" : "고른 컷(" + what + ")을") + " AI로 고칩니다";
  }

  /* ---- 작업 194: 같은 칸(원본 + 그 버전)의 여러 클립 = segments ---- */

  /* 고른 클립(타임라인 순서)을 segments 로 보내야 하는가(순수): 원본이 섞이거나, 한 원본이라도 원본 초가 이어지지 않는다(앞 out ≠ 다음 in). */
  function editFixNeedsSegments(clips) {
    for (var i = 1; i < (clips || []).length; i++) {
      if (clips[i].sourceKey !== clips[0].sourceKey) return true;
      if (Math.abs(Number(clips[i].in) - Number(clips[i - 1].out)) > EDIT_FIX_SEG_EPS) return true;
    }
    return false;
  }

  /* 고른 클립 재료가 모두 한 칸(재료 retake.cellKey 가 같고 비지 않음 — 원본과 그 버전)이면 그 cellKey, 아니면 "". */
  function editFixCellOf(ctx, clips) {
    var cell = "";
    for (var i = 0; i < clips.length; i++) {
      var mat = ctx.materials[clips[i].sourceKey];
      var key = mat && mat.retake ? String(mat.retake.cellKey || "") : "";
      if (!key || (cell && key !== cell)) return "";
      cell = key;
    }
    return cell;
  }

  /* 그 칸 원본(v1 — retake.n 1) 재료. 없으면 null. */
  function editFixCellBase(ctx, cellKey) {
    var keys = Object.keys(ctx.materials || {});
    for (var i = 0; i < keys.length; i++) {
      var mat = ctx.materials[keys[i]];
      if (mat && mat.retake && String(mat.retake.cellKey || "") === cellKey && Number(mat.retake.n) === 1) return mat;
    }
    return null;
  }

  /* 고른 클립(타임라인 순서) → {segments: [{sourceKey, in, out}]} 또는 {error}(순수). 8개 넘음 · 속도 1이 아닌 클립 · 이웃한 두 고른 클립
     사이 디졸브(indexes = 타임라인 번호)는 거절. 각 클립 자기 파일 · 자기 in·out 그대로다. */
  function editFixSegments(clips, indexes) {
    if (clips.length > EDIT_FIX_SEG_MAX) return { error: EDIT_FIX_SEG_MANY };
    if (clips.some(function (c) {
      return editSpeedOf(c) !== 1;
    })) return { error: EDIT_FIX_SEG_SPEED };
    for (var k = 0; k + 1 < clips.length; k++) {
      if (indexes[k + 1] === indexes[k] + 1 && editDissolveOf(clips[k])) return { error: EDIT_FIX_SEG_DISSOLVE };
    }
    return { segments: clips.map(function (c) {
      return { sourceKey: String(c.sourceKey), in: editRound3(c.in), out: editRound3(c.out) };
    }) };
  }

  /* segments 길의 고른 컷 대상: 패널 재료 = 그 칸 원본(v1), range = {in 0, out 합}(보기 GET 이 모델 목록을 주도록 — 원본 길이 안), total = 합. */
  function editFixSegTarget(ctx, clips, indexes) {
    var cellKey = editFixCellOf(ctx, clips);
    if (!cellKey) return { error: EDIT_FIX_MIXED };
    var segs = editFixSegments(clips, indexes);
    if (segs.error) return { error: segs.error };
    var base = editFixCellBase(ctx, cellKey) || ctx.materials[clips[0].sourceKey];
    if (!base.retake || base.retake.can !== true) return { error: base.retake.reason || "이 컷은 AI로 고칠 수 없습니다." };
    var total = editRound3(segs.segments.reduce(function (sum, s) {
      return sum + (s.out - s.in);
    }, 0));
    var len = editBinSeconds(base);
    return { mid: String(base.mid || ""), sourceKey: String(clips[0].sourceKey), ids: clips.map(function (c) {
      return String(c.id);
    }), range: { in: 0, out: editRound3(len > 0 ? Math.min(total, len) : total) }, names: editFixSelNames(clips, base.label),
      segments: segs.segments, total: total };
  }

  /* 고른 컷으로 패널을 연다. 고른 컷이 있으면 true(원본이 다르거나 고칠 수 없으면 안내 한 줄만 — 패널을 열지 않는다), 없으면 false. */
  function editFixOpenSel(ctx) {
    if (!editAlive(ctx) || ctx.busy || !editFixOn(ctx)) return false;
    var target = editFixSelTarget(ctx);
    if (!target) return false;
    if (target.error) {
      editNote(ctx, target.error);
      return true;
    }
    editFixOpen(ctx, target.mid, target.ids[0], target.range, { ids: target.ids, names: target.names, fromSel: true, segments: target.segments,
                                                                 total: target.total });
    return true;
  }

  /* 고른 컷 표시(초안이 고른 컷에 묶인다): 고른 클립마다 [id, 원본, in, out]. */
  function editFixSelKey(ctx) {
    return JSON.stringify(editSelIndexes(ctx).map(function (i) {
      var c = ctx.clips[i];
      return [c.id, c.sourceKey, c.in, c.out];
    }));
  }

  /* 고른 컷에서 연 compose 패널: 고른 컷이 바뀌면(고르기 · 편집) 같은 재료면 범위를 따라가고(보기를 다시 받는다), 아니면 그대로 둔다.
     어느 쪽이든 준비한 프롬프트는 「다시 준비해 주세요」가 된다(editFixComposeSig 에 고른 컷 표시가 든다). */
  function editFixSelFollow(ctx) {
    var fix = ctx.fix;
    if (!fix || !fix.compose || !fix.fromSel || !editFixAlive(ctx, fix)) return;
    var key = editFixSelKey(ctx);
    if (key === fix.selNow) return;
    fix.selNow = key;
    var target = editFixSelTarget(ctx);
    if (target && !target.error && target.mid === fix.mid) {
      var moved = !fix.clip || target.range.in !== fix.clip.in || target.range.out !== fix.clip.out ||
        JSON.stringify(target.segments || null) !== JSON.stringify(fix.segments || null); // 작업 196: 보기가 조각을 따르므로 조각이 바뀌어도 다시 받는다
      fix.clip = target.range;
      fix.clipIds = target.ids.slice();
      fix.clipId = target.ids[0];
      fix.names = target.names;
      fix.segments = target.segments || null; // 작업 194
      fix.total = target.segments ? target.total : 0;
      if (moved) {
        fix.rangeWait = true;
        editFixPaint(ctx, fix, fix.view);
        editFixLoad(ctx, fix);
        return;
      }
    }
    editFixPaint(ctx, fix, fix.view);
  }

  function editFixSelButton(ctx) {
    var btn = el("button", "outline-button vnew-edit-fix-sel", "고른 클립 AI로 고치기");
    btn.type = "button";
    btn.addEventListener("click", function () {
      var mat = editFixSelMat(ctx);
      var clip = editSelected(ctx);
      if (mat && clip) editFixOpen(ctx, String(mat.mid || ""), clip.id);
    });
    ctx.dom.fixSel = btn;
    editFixRenderSel(ctx);
    return btn;
  }

  /* 재료 하나의 [AI로 고치기]. 합친 영상·옛 구간·지난 실사 회차(서버 retake.n 없음)는 버튼이 없고, 기록이 없으면 흐림 + 이유. */
  function editFixBinButton(ctx, item, mat) {
    var info = (mat || {}).retake;
    if (!info || !info.n) return null;
    var name = mat.label || "영상";
    var btn = el("button", "outline-button vnew-edit-fix-open", "AI로 고치기");
    btn.type = "button";
    btn.disabled = info.can !== true;
    btn.title = info.can === true ? name + " — AI로 고쳐 새 버전 만들기" + (info.running ? " (지금 다시 만드는 중)" : "") : info.reason || "AI로 고칠 수 없습니다";
    btn.setAttribute("aria-label", "AI로 고치기 — " + name);
    btn.addEventListener("click", function () {
      editFixOpen(ctx, String(mat.mid || item.mid || ""), "");
    });
    return btn;
  }

  /* ---- 패널 ---- */

  function editFixPath(ctx, fix) {
    return editPath(ctx) + "/retake/" + encodeURIComponent(fix.mid);
  }

  /* 이 패널이 아직 보이는가(⑦ 이 살아 있고 이 패널이 열려 있고 ⑦ 루트에 붙어 있다 — 다시 그리면 떨어져 조회가 멈춘다). */
  function editFixAlive(ctx, fix) {
    return editAlive(ctx) && ctx.fix === fix && fix.open && !!fix.dom.root && fix.dom.root.parentNode === ctx.root;
  }

  function editFixNote(fix, message) {
    var node = fix.dom.note;
    if (!node) return;
    node.textContent = message || "";
    node.style.display = message ? "" : "none";
  }

  /* 조회 타이머·영상을 멈춘다(패널은 남긴다 — editLeave 가 부른다). */
  function editFixStop(ctx) {
    var fix = ctx && ctx.fix;
    if (!fix) return;
    fix.open = false;
    if (fix.timer) window.clearTimeout(fix.timer);
    fix.timer = 0;
    var video = fix.dom.video;
    if (video) {
      try {
        video.pause();
        video.removeAttribute("src");
        video.load();
      } catch (error) {
        /* 이미 떨어진 요소 */
      }
    }
  }

  function editFixClose(ctx) {
    var fix = ctx && ctx.fix;
    if (!fix) return;
    editFixStop(ctx);
    if (fix.dom.root && fix.dom.root.parentNode) fix.dom.root.parentNode.removeChild(fix.dom.root);
    ctx.fix = null;
  }

  /* 지시서 119: range({in, out}, 원본 시간 — 고르기 창에서 고른 장면)가 있으면 그것이 "이 구간만" 범위다(없으면 clipId 의 클립 구간).
     작업 192: sel {ids, names, fromSel} — 고른 컷들(editFixOpenSel · 재료 줄 컷). [바꾸기]는 그 클립들만 바꾸고, 머리 줄에 컷 이름 · 구간. */
  function editFixOpen(ctx, mid, clipId, range, sel) {
    if (!editAlive(ctx) || ctx.busy || !mid) return;
    editPause(ctx);
    editFixPickClose(ctx);
    editFixClose(ctx);
    var fix = { mid: mid, clipId: clipId || "", view: null, timer: 0, busy: false, draftShown: "", refreshing: false, open: true, dom: {} };
    fix.model = ""; // 지시서 116 보완 1: 고른 영상 모델(보기가 오면 원래 모델·초안 모델로 채운다)·범위·고른 클립 구간(원본 일부일 때만)
    var s = sel || {};
    fix.clipIds = Array.isArray(s.ids) && s.ids.length ? s.ids.slice() : clipId ? [String(clipId)] : null; // 작업 192
    fix.names = String(s.names || "");
    fix.fromSel = s.fromSel === true;
    fix.selNow = fix.fromSel ? editFixSelKey(ctx) : "";
    // 작업 194: 같은 칸 여러 클립 — compose · 제출 바디는 in·out 대신 segments(타임라인 순서), 머리 줄은 컷 이름 · 합 초
    fix.segments = Array.isArray(s.segments) && s.segments.length ? s.segments.map(function (x) {
      return { sourceKey: String(x.sourceKey), in: Number(x.in), out: Number(x.out) };
    }) : null;
    fix.total = fix.segments ? Number(s.total) || 0 : 0;
    fix.compose = false; // 작업 192: 보기 fullScopeAllowed false 면 true(editFixApply) — 프롬프트 준비(compose) 길
    fix.draftId = ""; // 작업 192: 이 패널이 준비한 compose 초안 id · 그때 입력 표시(editFixComposeSig)
    fix.draftSig = "";
    var a = range ? Number(range.in) : NaN;
    var b = range ? Number(range.out) : NaN;
    fix.clip = isFinite(a) && isFinite(b) && b > a && a >= 0 ? { in: editRound3(a), out: editRound3(b) } : editFixClipFor(ctx, fix.clipId);
    fix.scope = range && fix.clip ? "range" : ""; // 지시서 119: 고르기 창에서 컷을 골랐으면 「이 구간만」이 기본(서버가 구간을 못 주면 editFixAdopt 가 full 로)
    ctx.fix = fix;
    editFixBuild(ctx, fix);
    editFixNote(fix, "불러오는 중…");
    editFixLoad(ctx, fix);
  }

  function editFixLabeled(text, field) {
    var label = el("label", "vnew-edit-fix-field");
    label.appendChild(el("span", "vnew-edit-fix-label", text));
    label.appendChild(field);
    return label;
  }

  /* 뼈대를 한 번 만든다(머리·안내·영상·버전·이전 프롬프트·입력·초안·진행 줄). 바뀌는 칸은 editFixPaint 가 채운다. */
  function editFixBuild(ctx, fix) {
    var d = fix.dom;
    d.root = el("div", "vnew-edit-fix");
    d.root.setAttribute("role", "dialog");
    d.root.setAttribute("aria-modal", "true");
    d.root.setAttribute("aria-label", "AI로 고치기");
    var box = el("div", "vnew-edit-fix-box");
    var head = el("div", "vnew-edit-fix-head");
    d.title = el("h3", "vnew-edit-fix-title", "AI로 고치기");
    head.appendChild(d.title);
    var close = el("button", "vnew-edit-fix-close", "×");
    close.type = "button";
    close.title = "닫기 (Esc)";
    close.setAttribute("aria-label", close.title);
    close.addEventListener("click", function () {
      editFixClose(ctx);
    });
    head.appendChild(close);
    box.appendChild(head);
    d.selInfo = el("p", "vnew-edit-fix-selinfo", ""); // 작업 192: 고칠 컷 이름 · 구간(compose 길에서만 글이 있다)
    d.selInfo.style.display = "none";
    box.appendChild(d.selInfo);
    d.note = el("p", "vnew-hint vnew-edit-fix-note", "");
    box.appendChild(d.note);
    d.reload = el("button", "outline-button vnew-edit-fix-reload", "다시 불러오기");
    d.reload.type = "button";
    d.reload.style.display = "none";
    d.reload.addEventListener("click", function () {
      d.reload.style.display = "none";
      editFixNote(fix, "불러오는 중…");
      editFixLoad(ctx, fix);
    });
    box.appendChild(d.reload);

    var body = el("div", "vnew-edit-fix-body");
    var left = el("div", "vnew-edit-fix-col");
    d.video = el("video", "vnew-edit-fix-video");
    d.video.controls = true;
    d.video.preload = "metadata";
    d.video.setAttribute("playsinline", "");
    left.appendChild(d.video);
    left.appendChild(el("h4", "vnew-edit-fix-label", "버전"));
    d.versions = el("div", "vnew-edit-fix-versions");
    left.appendChild(d.versions);
    body.appendChild(left);

    var right = el("div", "vnew-edit-fix-col");
    right.appendChild(el("h4", "vnew-edit-fix-label", "이전 프롬프트"));
    d.prev = el("div", "vnew-edit-fix-prev");
    right.appendChild(d.prev);
    editFixBuildSetup(ctx, fix, right); // 지시서 116 보완 1: 범위·영상 모델
    d.request = el("textarea", "vnew-edit-fix-input");
    d.request.rows = 3;
    d.request.setAttribute("maxlength", "500");
    d.request.setAttribute("placeholder", "예: 2초쯤 남자가 웃지 않게, 손은 주머니에 넣은 채로");
    right.appendChild(editFixLabeled("수정 사항(필수, 500자)", d.request));
    d.extra = el("textarea", "vnew-edit-fix-input");
    d.extra.rows = 2;
    d.extra.setAttribute("maxlength", "500");
    d.extra.setAttribute("placeholder", "예: 창밖 빛을 조금 더 따뜻하게");
    right.appendChild(editFixLabeled("추가 프롬프트(선택, 500자)", d.extra));
    [d.request, d.extra].forEach(function (field) {
      field.addEventListener("input", function () {
        if (fix.compose) editFixPaintDraft(fix); // 작업 192: 고치면 준비한 프롬프트는 「다시 준비해 주세요」
      });
    });
    d.make = el("button", "primary-button vnew-edit-fix-make", "프롬프트 준비");
    d.make.type = "button";
    d.make.disabled = true; // 보기가 오면 editFixPaint 가 글·막기를 정한다(작업 192: 기본 = 프롬프트 준비, 옛 길 = 새 프롬프트 만들기)
    d.make.title = "이전 프롬프트·결과 영상 프레임·수정 사항으로 AI가 새 프롬프트를 씁니다(아직 다시 만들지 않습니다)";
    d.make.addEventListener("click", function () {
      if (fix.compose) editFixCompose(ctx, fix);
      else editFixPreview(ctx, fix);
    });
    right.appendChild(d.make);

    d.draft = el("div", "vnew-edit-fix-draft");
    d.draft.style.display = "none";
    d.draftStatus = el("p", "vnew-edit-fix-status", "");
    d.result = el("p", "vnew-edit-fix-result", "");
    d.changes = el("ul", "vnew-edit-fix-changes");
    d.newPrompt = el("textarea", "vnew-edit-fix-input vnew-edit-fix-new");
    d.newPrompt.rows = 10;
    d.newPrompt.addEventListener("input", function () {
      editFixMeter(fix);
    });
    d.meter = el("span", "vnew-edit-fix-meter", "");
    d.problems = el("ul", "vnew-edit-fix-problems");
    d.cost = el("p", "vnew-edit-fix-cost", "");
    d.submit = el("button", "primary-button vnew-edit-fix-submit", "다시 만들기");
    d.submit.type = "button";
    d.submit.addEventListener("click", function () {
      editFixSubmit(ctx, fix);
    });
    d.newField = editFixLabeled("새 프롬프트(제출 전에 고칠 수 있습니다)", d.newPrompt);
    d.composed = el("pre", "vnew-edit-fix-prompt vnew-edit-fix-composed", ""); // 작업 192: 준비한 프롬프트(읽기만, 수정 줄 강조)
    d.composed.style.display = "none";
    [d.draftStatus, d.result, d.changes, d.newField, d.composed, d.meter, d.problems, d.cost, d.submit].forEach(function (node) {
      d.draft.appendChild(node);
    });
    right.appendChild(d.draft);
    d.run = el("p", "vnew-edit-fix-status vnew-edit-fix-run", "");
    d.run.style.display = "none";
    right.appendChild(d.run);
    body.appendChild(right);
    box.appendChild(body);
    d.root.appendChild(box);
    ctx.root.appendChild(d.root);
  }

  function editFixLoad(ctx, fix) {
    api(editFixViewPath(ctx, fix))
      .then(function (view) {
        if (!editFixAlive(ctx, fix)) return;
        editFixApply(ctx, fix, view || {});
      })
      .catch(function (err) {
        if (!editFixAlive(ctx, fix)) return;
        editFixNote(fix, "불러오지 못했습니다: " + errMessage(err));
        fix.dom.reload.style.display = "";
      });
  }

  function editFixApply(ctx, fix, view) {
    var before = fix.view;
    fix.view = view;
    if (before && before.live && !view.live) mnotesRefresh(ctx.pid); // 작업 199: 버전이 끝났으면 모델 에러 로그 건수 한 번 더
    // 작업 192: 원본 칸 전체를 고를 수 없는 서버(fullScopeAllowed false) = 프롬프트 준비(compose) 길. 범위는 늘 고른 컷 구간이고, 범위 없이
    // 연 패널(테이크 통째)은 그 재료 처음~끝을 구간으로 보기를 한 번 더 받는다.
    fix.compose = view.fullScopeAllowed === false;
    if (fix.compose && !fix.clip) {
      fix.clip = editFixWhole(ctx, fix);
      if (fix.clip) {
        fix.scope = "range";
        fix.rangeWait = true;
        editFixPaint(ctx, fix, before);
        editFixLoad(ctx, fix);
        return;
      }
    }
    editFixAdopt(fix); // 지시서 116 보완 1: 범위·모델 기본값(구간을 못 받았으면 원본 칸 전체로)
    editFixPaint(ctx, fix, before);
    editFixSchedule(ctx, fix);
  }

  /* 작업 192: 패널 재료(mid)의 처음~끝 {in, out}(원본 초 — 자른 버전은 그 길이까지). 길이를 모르면 null. */
  function editFixWhole(ctx, fix) {
    var keys = Object.keys(ctx.materials || {});
    for (var i = 0; i < keys.length; i++) {
      var mat = ctx.materials[keys[i]];
      if (mat && String(mat.mid || "") === fix.mid) {
        var len = editBinSeconds(mat);
        return len > 0 ? { in: 0, out: editRound3(len) } : null;
      }
    }
    return null;
  }

  /* 초안 running 이면 2초, 버전 진행 중이면 5초 뒤 다시 GET(실패면 같은 간격으로 다시 건다). 둘 다 아니면 멈춘다. */
  function editFixSchedule(ctx, fix) {
    if (fix.timer) window.clearTimeout(fix.timer);
    fix.timer = 0;
    var v = fix.view || {};
    var ms = v.draft && v.draft.status === "running" ? EDIT_FIX_DRAFT_POLL_MS : v.live ? EDIT_FIX_RUN_POLL_MS : 0;
    if (!ms) return;
    fix.timer = window.setTimeout(function () {
      fix.timer = 0;
      if (!editFixAlive(ctx, fix)) return;
      api(editFixViewPath(ctx, fix))
        .then(function (view) {
          if (editFixAlive(ctx, fix)) editFixApply(ctx, fix, view || {});
        })
        .catch(function () {
          if (editFixAlive(ctx, fix)) editFixSchedule(ctx, fix);
        });
    }, ms);
  }

  /* 새 버전이 재료 목록에 아직 없으면 GET /edit?variant= 로 재료만 다시 받는다(편집 문서·실행 취소는 그대로). 완성된 버전을 그릴 때
     editFixPaintVersions 가 부른다(한 번에 하나). */
  function editFixRefreshMaterials(ctx, fix) {
    if (fix.refreshing) return;
    fix.refreshing = true;
    api(editPath(ctx) + "?variant=" + encodeURIComponent(ctx.variant))
      .then(function (view) {
        fix.refreshing = false;
        if (!editAlive(ctx)) return;
        if (view && Array.isArray(view.materials)) {
          editAbsorbMaterials(ctx, view);
          ctx.retakeOn = !!(view.retake && view.retake.enabled === true);
          editRenderBin(ctx);
        }
        if (editFixAlive(ctx, fix)) editFixPaintVersions(ctx, fix);
      })
      .catch(function () {
        fix.refreshing = false;
      });
  }

  function editFixPaint(ctx, fix, before) {
    var v = fix.view || {};
    var d = fix.dom;
    d.title.textContent = "AI로 고치기 — " + (v.label || "영상");
    if (!before && v.videoUrl) {
      d.video.src = String(v.videoUrl);
      fix.previewMid = String(v.mid || ""); // 지시서 116 보완 2: 처음 미리보기 = 패널을 연 기준 버전
    }
    editFixNote(fix, v.canRetake === false ? v.block || "이 칸은 AI로 고칠 수 없습니다." : "");
    editFixPaintVersions(ctx, fix);
    editFixPaintPrev(fix);
    editFixPaintSetup(ctx, fix); // 지시서 116 보완 1
    editFixPaintDraft(fix);
    editFixPaintRun(fix);
    var drafting = !!(v.draft && v.draft.status === "running");
    if (fix.compose) {
      // 작업 192: [프롬프트 준비] = 이전 프롬프트 + 수정 사항(LLM 다시 쓰기 없음, POST …/compose)
      d.make.textContent = fix.busy ? "프롬프트 준비 중…" : "프롬프트 준비";
      d.make.title = "이전 프롬프트에 수정 사항을 붙여 고른 컷 구간만 고칠 프롬프트를 만듭니다(아직 다시 만들지 않습니다)";
      d.make.disabled = v.canRetake !== true || fix.busy || drafting || !!fix.rangeWait || !v.range || !fix.clip || !fix.model;
      d.selInfo.textContent = editFixSelInfo(fix);
      d.selInfo.style.display = "";
    } else {
      d.make.disabled = v.canRetake !== true || fix.busy || drafting;
      d.make.textContent = drafting ? "새 프롬프트를 만드는 중…" : v.draft ? "새 프롬프트 다시 만들기" : "새 프롬프트 만들기";
      d.make.title = "이전 프롬프트·결과 영상 프레임·수정 사항으로 AI가 새 프롬프트를 씁니다(아직 다시 만들지 않습니다)";
    }
    d.request.disabled = v.canRetake !== true;
    d.extra.disabled = v.canRetake !== true;
  }

  /* 작업 192: 머리 줄 아래 한 줄 — 고칠 컷 이름(컷 a–b · 클립 이름) · 원본 구간 초. 작업 194: segments 면 컷 이름 · 클립 수 · 합 초. */
  function editFixSelInfo(fix) {
    var c = fix.clip;
    if (fix.segments) {
      return "고칠 컷: " + (fix.names ? fix.names + " · " : "") + "클립 " + fix.segments.length + "개 · 합 " + editFixSecs(fix.total) + "초";
    }
    var span = c ? editFixSecs(c.in) + "–" + editFixSecs(c.out) + "초" : "구간을 알 수 없습니다";
    var count = fix.clipIds && fix.clipIds.length > 1 ? " · 클립 " + fix.clipIds.length + "개" : "";
    return "고칠 구간: " + (fix.names ? fix.names + " · " : "") + span + count;
  }

  function editFixPaintPrev(fix) {
    var v = fix.view || {};
    var d = fix.dom;
    clear(d.prev);
    var base = v.base;
    d.prev.appendChild(el("span", "vnew-edit-fix-src", base ? EDIT_FIX_SOURCE[base.source] || String(base.source || "") : "기록 없음"));
    d.prev.appendChild(el("pre", "vnew-edit-fix-prompt", base ? String(base.prompt || "") : "이 칸은 프롬프트 기록이 없습니다."));
    var attempts = Array.isArray(v.attempts) ? v.attempts : [];
    if (!attempts.length) return;
    var details = el("details", "vnew-edit-fix-attempts");
    details.appendChild(el("summary", null, "이전 시도 (" + attempts.length + ")"));
    attempts.forEach(function (a) {
      var item = el("div", "vnew-edit-fix-attempt");
      item.appendChild(el("span", "vnew-edit-fix-meta", editFixAttemptMeta(a)));
      item.appendChild(el("pre", "vnew-edit-fix-prompt", String(a.prompt || "")));
      details.appendChild(item);
    });
    d.prev.appendChild(details);
  }

  function editFixPaintVersions(ctx, fix) {
    var v = fix.view || {};
    var d = fix.dom;
    clear(d.versions);
    d.picks = {};
    var list = Array.isArray(v.versions) ? v.versions : [];
    var use = editFixInUse(ctx.clips, list); // 지시서 116 보완 2: 지금 편집 문서가 쓰는 버전
    list.forEach(function (ver) {
      var row = el("div", "vnew-edit-fix-version" + editFixRowState(v, ver, use, fix.previewMid));
      var name = el("span", "vnew-edit-fix-version-name", editFixVerName(ver) + " · " + (EDIT_FIX_STATUS[ver.status] || ver.status || "없음"));
      row.appendChild(name);
      editFixPickable(ctx, fix, ver, name); // 지시서 116 보완 2: 이름을 누르면(Enter·Space) 미리보기
      var badge = editFixUseBadge(use, ver);
      if (badge) row.appendChild(badge);
      var meta = editFixVersionMeta(v, ver); // 지시서 116 보완 1: 다른 모델·구간 버전
      if (meta) row.appendChild(el("span", "vnew-edit-fix-version-meta", meta));
      if (ver.status === "completed" && ver.sourceKey) {
        var known = !!ctx.materials[ver.sourceKey];
        var swap = el("button", "outline-button vnew-edit-fix-swap", "바꾸기");
        swap.type = "button";
        swap.disabled = !known;
        swap.title = ver.range ? editFixSwapTitle(fix, ver) :
          (fix.clipId ? "고른 클립" : "타임라인에서 이 칸의 클립") + "을 " + editFixVerTo(ver) + " 바꿉니다(구간·속도는 길이 안에서 유지)";
        swap.addEventListener("click", function () {
          editFixSwap(ctx, fix, ver, swap);
        });
        var add = el("button", "outline-button vnew-edit-fix-add", "재료에 추가");
        add.type = "button";
        add.disabled = !known;
        add.title = editFixVerObj(ver) + " 재생헤드가 있는 클립 뒤에 넣습니다";
        add.addEventListener("click", function () {
          if (ctx.materials[ver.sourceKey]) editInsertMaterial(ctx, ver.sourceKey, add);
        });
        row.appendChild(swap);
        row.appendChild(add);
        if (!known) editFixRefreshMaterials(ctx, fix);
      } else if (ver.status === "failed" && ver.recovering) {
        // 지시서 116 보완 4: 시간 초과 버전 — 서버가 패널을 열 때마다 결과를 다시 묻는다(새 제출 없음)
        row.appendChild(el("span", "vnew-edit-fix-error", "결과를 다시 확인하는 중입니다. 잠시 뒤 [다시 불러오기]를 눌러 주세요."));
        d.reload.style.display = "";
      } else if (ver.status === "failed" && ver.error) {
        row.appendChild(el("span", "vnew-edit-fix-error", ver.error));
      } else if (ver.contentNote && ver.status !== "completed" && ver.status !== "failed") {
        row.appendChild(el("span", "vnew-hint", ver.contentNote)); // 작업 195: 콘텐츠 검사 거절 뒤 순화해 한 번 더 내는 중
      }
      d.versions.appendChild(row);
    });
  }

  function editFixPaintDraft(fix) {
    if (fix.compose) {
      editFixPaintCompose(fix); // 작업 192
      return;
    }
    var v = fix.view || {};
    var d = fix.dom;
    var dr = v.draft;
    d.composed.style.display = "none";
    if (!dr) {
      d.draft.style.display = "none";
      return;
    }
    d.draft.style.display = "";
    var done = dr.status === "done";
    [d.result, d.changes, d.newField, d.meter, d.problems, d.cost, d.submit].forEach(function (node) {
      node.style.display = done ? "" : "none";
    });
    if (dr.status === "running") {
      d.draftStatus.textContent = "새 프롬프트를 만드는 중… (결과 영상 프레임과 이전 프롬프트를 보고 고칩니다)";
      return;
    }
    if (!done) {
      d.draftStatus.textContent = "실패 — " + (dr.error || "새 프롬프트를 만들지 못했습니다.");
      return;
    }
    d.draftStatus.textContent = "새 프롬프트 — v" + (dr.baseN || 1) + "에서 고침 · " +
      (Number(dr.framesUsed) > 0 ? "결과 프레임 " + dr.framesUsed + "장을 봤습니다" : "결과 프레임을 읽지 못해 이전 프롬프트·콘티만 봤습니다") +
      editFixDraftSuffix(dr) + // 지시서 116 보완 1: 다른 모델·구간이면 그 모델·구간
      (dr.shrunk ? " · 길이 상한에 맞게 한 번 줄였습니다" : ""); // 지시서 116 보완 3: 서버가 바이트 초과 초안을 한 번 다시 줄임
    d.result.textContent = dr.resultKo ? "지금 결과: " + dr.resultKo : "";
    clear(d.changes);
    (Array.isArray(dr.changesKo) ? dr.changesKo : []).forEach(function (text) {
      d.changes.appendChild(el("li", null, String(text)));
    });
    if (fix.draftShown !== dr.id) {
      d.newPrompt.value = String(dr.prompt || "");
      fix.draftShown = dr.id;
    }
    var cur = editFixPlanOf(v, fix.model, fix.scope); // 지시서 116 보완 1: 고른 모델·범위의 견적(원래 모델·원본 칸 전체면 116 글자 그대로)
    d.cost.textContent = editFixCostText(v, cur.row, cur.plan);
    editFixMeter(fix);
  }

  /* 바이트 계기·문제 목록·제출 버튼. 초안 그대로면 서버 문제 목록, 고쳤으면 길이만 미리 보고 나머지는 제출 때 서버가 다시 검사한다. */
  function editFixMeter(fix) {
    var v = fix.view || {};
    var d = fix.dom;
    var dr = v.draft || {};
    var max = editFixMaxBytes(fix); // 지시서 116 보완 1: 고른 모델의 상한(같은 경로면 116 limits)
    var text = String(d.newPrompt.value || "");
    var bytes = editFixBytes(text);
    var over = max > 0 && bytes > max;
    d.meter.textContent = editFixComma(bytes) + " / " + editFixComma(max) + "바이트";
    d.meter.className = "vnew-edit-fix-meter" + (over ? " is-over" : "");
    clear(d.problems);
    var edited = text !== String(dr.prompt || "");
    var list = edited ? (over ? ["새 프롬프트가 " + editFixComma(bytes) + "바이트입니다. " + editFixComma(max) + "바이트 안으로 줄여 주세요."] : []) :
      (Array.isArray(dr.problems) ? dr.problems : []);
    list.forEach(function (problem) {
      d.problems.appendChild(el("li", null, String(problem)));
    });
    if (edited) d.problems.appendChild(el("li", "vnew-edit-fix-edited", "직접 고친 프롬프트는 제출할 때 서버가 참고 표시·항목·길이를 다시 검사합니다."));
    var matches = dr.status !== "done" || editFixDraftMatches(v, fix); // 지시서 116 보완 1: 초안은 모델·범위에 묶인다
    if (!matches) d.problems.appendChild(el("li", "vnew-edit-fix-mismatch", "모델이나 범위가 새 프롬프트를 만들 때와 다릅니다. [새 프롬프트 다시 만들기]를 눌러 주세요."));
    d.submit.textContent = (editFixNextN(v) === Number(((v.versions || []).slice(-1)[0] || {}).n) ? "다시 시도" : "다시 만들기") +
      " (" + editFixMoney(editFixUsd(fix)) + ")";
    d.submit.disabled = fix.busy || !!v.live || dr.status !== "done" || !text.trim() || over || !matches;
  }

  function editFixPaintRun(fix) {
    var v = fix.view || {};
    var d = fix.dom;
    var list = (Array.isArray(v.versions) ? v.versions : []).filter(function (x) {
      return x && !x.original;
    });
    var target = v.live ? list.filter(function (x) {
      return x.n === v.live.n;
    })[0] || v.live : list[list.length - 1];
    if (!target) {
      d.run.style.display = "none";
      return;
    }
    d.run.style.display = "";
    d.run.className = "vnew-edit-fix-status vnew-edit-fix-run is-" + String(target.status || "");
    d.run.textContent = editFixStatusText(target);
  }

  function editFixPreview(ctx, fix) {
    if (!editFixAlive(ctx, fix) || fix.busy) return;
    var d = fix.dom;
    var request = String(d.request.value || "").trim();
    if (!request) {
      editFixNote(fix, "수정 사항을 적어 주세요.");
      return;
    }
    fix.busy = true;
    d.make.disabled = true;
    editFixNote(fix, "");
    api(editFixPath(ctx, fix) + "/preview", { method: "POST", body: JSON.stringify(editFixBody(fix, fix.view, { requestKo: request, extraKo: String(d.extra.value || "").trim() })) })
      .then(function (res) {
        fix.busy = false;
        if (editFixAlive(ctx, fix)) editFixApply(ctx, fix, (res && res.retake) || fix.view || {});
      })
      .catch(function (err) {
        fix.busy = false;
        if (!editFixAlive(ctx, fix)) return;
        editFixPaint(ctx, fix, fix.view);
        editFixNote(fix, "새 프롬프트를 만들지 못했습니다: " + errMessage(err));
      });
  }

  function editFixSubmit(ctx, fix) {
    if (!editFixAlive(ctx, fix) || fix.busy) return;
    if (fix.compose) {
      editFixSubmitCompose(ctx, fix); // 작업 192
      return;
    }
    var v = fix.view || {};
    var dr = v.draft || {};
    var cur = editFixPlanOf(v, fix.model, fix.scope); // 지시서 116 보완 1: 고른 모델·범위(같은 경로면 116 확인창·바디 그대로)
    var prompt = String(fix.dom.newPrompt.value || "").trim();
    if (dr.status !== "done" || !prompt || !editFixDraftMatches(v, fix)) return;
    var next = editFixNextN(v);
    if (!editConfirm(editFixConfirmText(v, cur.row, cur.plan, next, fix.scope === "range" ? v.range : null))) return;
    fix.busy = true;
    fix.dom.submit.disabled = true;
    editFixNote(fix, "");
    api(editFixPath(ctx, fix), {
      method: "POST",
      body: JSON.stringify(editFixBody(fix, v, { prompt: prompt, requestKo: String(dr.requestKo || ""), extraKo: String(dr.extraKo || ""), confirmUsd: Number(editFixUsd(fix)) || 0, draftId: String(dr.id || "") }))
    })
      .then(function (res) {
        fix.busy = false;
        if (!editFixAlive(ctx, fix)) return;
        editFixApply(ctx, fix, (res && res.retake) || v);
        editFixNote(fix, "v" + next + "를 만들기 시작했습니다. 끝나면 [바꾸기]·[재료에 추가]가 생깁니다.");
      })
      .catch(function (err) {
        fix.busy = false;
        if (!editFixAlive(ctx, fix)) return;
        editFixPaint(ctx, fix, fix.view);
        editFixNote(fix, "다시 만들지 못했습니다: " + errMessage(err));
      });
  }

  /* [바꾸기]: 그 칸의 클립(패널을 고른 클립에서 열었으면 그 클립만) 원본을 이 버전으로. 길이를 모르면 /media 로 먼저 잰다. */
  function editFixSwap(ctx, fix, ver, button) {
    if (!editAlive(ctx) || ctx.busy) return;
    var mat = ctx.materials[ver.sourceKey];
    if (!mat) return;
    function apply() {
      var v = fix.view || {};
      var versions = Array.isArray(v.versions) ? v.versions : [];
      var keys = versions.map(function (x) {
        return x.sourceKey;
      });
      var labels = versions.map(function (x) {
        return x.label;
      });
      var ranged = editFixRanged(ctx, versions, ver); // 지시서 116 보완 1: 구간 버전이 끼면 녹화 시간으로 옮긴다
      var ids = editFixIds(fix); // 작업 192: 고른 컷들(여럿이면 모두)
      var oldLabels = labels.concat(versions.map(function (x) { return (ctx.materials[x.sourceKey] || {}).label; }));
      // 작업 192: 결과 영상 버전(videoSource result)이 끼면 재료 초를 그대로 옮긴다(editFixResultSwap — 같은 자리 · 같은 길이)
      // 작업 194: 여러 클립 조각 버전이 끼면 고른 클립마다 그 조각(editFixSegSwap — 같은 자리 · 같은 길이)
      var segs = editFixSegUses(ctx.clips, versions, ver);
      var result = !segs && editFixResultUses(ctx.clips, versions, ver);
      var res = segs ? editFixSegSwap(ctx.clips, ids, versions, ver, editFixDurations(ctx, versions), editMinLen(ctx), mat.label || ver.label, oldLabels) :
        result ? editFixResultSwap(ctx.clips, ids, versions, ver, editFixDurations(ctx, versions), editMinLen(ctx), mat.label || ver.label, oldLabels) :
        ranged ? editFixRangeSwap(ctx.clips, ids, versions, v.kind, v.cellSeconds, editFixDurations(ctx, versions), ver,
        editMinLen(ctx), mat.label || ver.label, oldLabels) :
        editSwapSource(ctx.clips, ids, keys, ver.sourceKey, Number(mat.duration), editMinLen(ctx),
        mat.label || ver.label, labels);
      var span = ver.sourceRange ? editFixSecs(ver.sourceRange.in) + "–" + editFixSecs(ver.sourceRange.out) :
        editFixSecs((ver.range || {}).start) + "–" + editFixSecs((ver.range || {}).end);
      if (!res.changed) {
        editFixNote(fix, segs && res.outside ? "고른 클립이 " + editFixVerName(ver) + "를 만들 때 고른 컷과 달라 바꾸지 않았습니다." :
          res.outside ? "고른 클립이 " + editFixVerName(ver) + " 구간(" + span +
          "초) 밖이라 바꾸지 않았습니다." : res.skipped ? "이 버전 길이 안에 들어가지 않는 클립이라 바꾸지 않았습니다." :
          "타임라인에 바꿀 이 칸의 클립이 없습니다. [재료에 추가]로 넣어 주세요.");
        return;
      }
      editCommit(ctx, res.clips, {});
      editFixNote(fix, "타임라인 클립 " + res.changed + "개를 " + editFixVerTo(ver) + " 바꿨습니다." +
        (res.skipped ? " 길이가 맞지 않는 " + res.skipped + "개는 그대로 두었습니다." : "") +
        ((result || segs) && res.outside ? " 구간 밖 " + res.outside + "개는 그대로 두었습니다." : "") + " 다른 버전의 [바꾸기]로 되돌릴 수 있습니다.");
    }
    if (Number(mat.duration) > 0) {
      apply();
      return;
    }
    button.disabled = true;
    editFixNote(fix, "영상 길이를 읽는 중…");
    api(editPath(ctx) + "/media/" + encodeURIComponent(String(mat.mid || "")))
      .then(function (data) {
        button.disabled = false;
        if (!editAlive(ctx)) return;
        ctx.media[mat.mid] = { status: "done", data: data || {}, at: Date.now() };
        editMediaFacts(ctx, mat.mid, data || {});
        apply();
      })
      .catch(function (err) {
        button.disabled = false;
        if (editFixAlive(ctx, fix)) editFixNote(fix, "영상 길이를 읽지 못했습니다: " + errMessage(err));
      });
  }

  /* ---------------------------------------------------------------- ⑦ AI로 고치기 — 모델·구간(지시서 116 보완 1)

     패널 「설정」: 범위(원본 칸 전체 | 이 구간만 — 고른 클립이 원본의 일부일 때만)와 영상 모델(서버 보기 models 중 그 범위에서 쓸 수 있는 것,
     기본 = 초안의 모델 또는 원래 모델). 모델을 바꾸면 서버가 미리 넣은 모델 × 범위 견적으로 비용·길이·상한을 다시 그린다(서버를 부르지 않는다).
     구간을 처음 고르면 ?in=&out=(원본 시간)으로 보기를 다시 받고, 그 뒤 조회도 같은 주소다. 원래 모델·원본 칸 전체면 116 글자·바디 그대로다.
     구간 버전이 낀 [바꾸기]는 editFixRangeSwap(녹화 시간으로 옮김), 그 밖은 116 editSwapSource. 검사는 check_vpoc rtk-s. */

  var EDIT_FIX_FORMATS = {
    reference: "참고 영상·이미지(@Video1·@Image N)",
    elements: "인물 요소(@Video1·@Element N)",
    images: "이미지 목록(@Video1·Image N)",
    none: "참고 이미지 없음"
  };

  /* 초 글자(소수 한 자리): 7.5 → "7.5", 15 → "15.0". */
  function editFixSecs(value) {
    return (Math.round((Number(value) || 0) * 10) / 10).toFixed(1);
  }

  /* 고른 클립이 원본의 일부면 {in, out}(원본 시간), 아니면 null. 원본 길이 = 잰 길이와 자를 길이(trimTo) 중 짧은 것. */
  function editFixClipPart(clip, duration, trimTo) {
    if (!clip) return null;
    var a = Number(clip.in) || 0;
    var b = Number(clip.out) || 0;
    var dur = Number(duration) || 0;
    var trim = Number(trimTo) || 0;
    var extent = trim > 0 && dur > 0 ? Math.min(trim, dur) : trim > 0 ? trim : dur;
    if (!(b > a) || !(extent > 0)) return null;
    if (a <= 0.1 && b >= extent - 0.1) return null;
    return { in: editRound3(a), out: editRound3(Math.min(b, extent)) };
  }

  /* 보기의 모델 줄과 그 범위의 견적. 없으면 {row: null, plan: null}. */
  function editFixPlanOf(view, model, scope) {
    var rows = view && Array.isArray(view.models) ? view.models : [];
    for (var i = 0; i < rows.length; i++) {
      if (rows[i] && rows[i].id === model) return { row: rows[i], plan: rows[i][scope] || null };
    }
    return { row: null, plan: null };
  }

  /* 원본 시간 → 칸 녹화 시간 {o, s, e}(서버 retake._time_map 과 같은 규칙): 구간 버전 (구간 시작, 1, 자를 길이) · 자를 길이가 있는 원본
     (0, 1, 자를 길이) · 통 테이크 v1·자르지 않은 버전 (0, 칸 길이 ÷ 결과 길이, 결과 길이) · 그 밖 (0, 1, 결과 길이). */
  function editFixTimeMap(ver, kind, cellSeconds, duration) {
    var v = ver || {};
    var dur = Number(duration) || 0;
    var trim = Number(v.trimTo) || 0;
    var r = v.range;
    if (r && Number(r.end) > Number(r.start)) return { o: Number(r.start), s: 1, e: trim > 0 ? trim : Number(r.end) - Number(r.start) };
    if (trim > 0) return { o: 0, s: 1, e: dur > 0 ? Math.min(trim, dur) : trim };
    var cell = Number(cellSeconds) || 0;
    if (kind === "take" && dur > 0 && cell > 0) return { o: 0, s: cell / dur, e: dur };
    return { o: 0, s: 1, e: dur > 0 ? dur : cell };
  }

  /* 구간 버전이 낀 [바꾸기](순수). 대상 = 원본이 versions(그 칸 v1·버전)의 원본인 클립(ids 가 있으면 그 중). 클립 in·out 을 녹화 시간으로
     옮긴 뒤 새 버전 시간으로 바꾼다. 새 버전이 구간이면 클립의 녹화 시간이 그 구간 안(±0.06초)일 때만 바꾸고 밖이면 outside 로 센다.
     속도·소리 끔·id 는 그대로, 이름은 116 규칙(비었거나 oldLabels 에 든 이름일 때만 newLabel). 반환 {clips, changed, skipped, outside}. */
  function editFixRangeSwap(clips, ids, versions, kind, cellSeconds, durations, newVer, minLen, newLabel, oldLabels) {
    var byKey = {};
    (versions || []).forEach(function (x) {
      if (x && x.sourceKey) byKey[x.sourceKey] = x;
    });
    var idSet = null;
    if (ids && ids.length) {
      idSet = {};
      ids.forEach(function (id) {
        idSet[id] = true;
      });
    }
    var nv = newVer || {};
    var durs = durations || {};
    var nm = editFixTimeMap(nv, kind, cellSeconds, durs[nv.sourceKey]);
    var lo = nm.o;
    var hi = nm.o + nm.e * nm.s;
    var changed = 0;
    var skipped = 0;
    var outside = 0;
    var out = editCloneClips(clips).map(function (clip) {
      var src = byKey[clip.sourceKey];
      if (!src || (idSet && !idSet[clip.id]) || clip.sourceKey === nv.sourceKey) return clip;
      var sm = editFixTimeMap(src, kind, cellSeconds, durs[clip.sourceKey]);
      var r0 = sm.o + Number(clip.in) * sm.s;
      var r1 = sm.o + Number(clip.out) * sm.s;
      if (nv.range && (r0 < lo - 0.06 || r1 > hi + 0.06)) {
        outside += 1;
        return clip;
      }
      var a = Math.max(0, Math.min(nm.e, (r0 - nm.o) / nm.s));
      var b = Math.max(0, Math.min(nm.e, (r1 - nm.o) / nm.s));
      if (!(nm.e > 0) || b - a < editMinSource(minLen, editSpeedOf(clip)) - 1e-9) {
        skipped += 1;
        return clip;
      }
      var next = editCloneClip(clip);
      next.sourceKey = nv.sourceKey;
      next.in = editRound3(a);
      next.out = editRound3(b);
      if (newLabel && (!clip.label || (oldLabels || []).indexOf(clip.label) >= 0)) next.label = String(newLabel).slice(0, 40);
      changed += 1;
      return next;
    });
    return { clips: out, changed: changed, skipped: skipped, outside: outside };
  }

  /* 모델 상자 한 줄: "이름 · 원래 모델 · $x"(단가 정보가 없어 기본 단가로 계산했으면 " · 기본 단가"). */
  function editFixModelOption(row, plan) {
    var r = row || {};
    var p = plan || {};
    return String(r.label || r.id || "영상 모델") + (r.original ? " · 원래 모델" : "") + " · " + editFixMoney(p.usd) + (p.priceKnown === false ? " · 기본 단가" : "");
  }

  /* 모델 길이 선택지 글자: 이어진 4~30 은 "4~30", 그 밖은 "4·8·12". */
  function editFixDurText(list) {
    var d = Array.isArray(list) ? list : [];
    if (d.length > 3 && Number(d[d.length - 1]) - Number(d[0]) + 1 === d.length) return d[0] + "~" + d[d.length - 1];
    return d.join("·");
  }

  /* 정보 줄: 형식 · 길이(모델 길이 선택지 → 보낼 길이·자를 길이) · 해상도 · 예상 금액 · 프롬프트 상한 · 기본 단가 안내 · 형식이 바뀌면 옮긴다는 안내. */
  function editFixPlanText(view, row, plan) {
    var r = row || {};
    var p = plan || {};
    var parts = ["형식: " + (EDIT_FIX_FORMATS[r.format] || String(r.format || ""))];
    if (p.same) parts.push("원래와 같은 길이 " + editFixSecs(p.seconds) + "초");
    else {
      var durs = Array.isArray(p.durations) && p.durations.length ? "이 모델 길이 " + editFixDurText(p.durations) + "초 중 " : "";
      var cut = Number(p.trimTo) > 0 && Number(p.sentSeconds) > Number(p.trimTo) + 0.05;
      parts.push(durs + editFixSecs(p.sentSeconds) + "초로 만들고 " + (cut ? editFixSecs(p.trimTo) + "초로 잘라 씀" : "그대로 씀"));
    }
    if (p.resolution) parts.push(String(p.resolution));
    parts.push("예상 " + editFixMoney(p.usd));
    parts.push("프롬프트 " + editFixComma(p.maxBytes) + "바이트까지");
    if (p.priceKnown === false) parts.push("단가 정보가 없어 기본 단가로 계산");
    var orig = editFixPlanOf(view, (view || {}).defaultModel, "full").row;
    if (!p.same && orig && r.format !== orig.format) parts.push("원래 모델과 형식이 달라 새 프롬프트를 이 모델 형식으로 옮깁니다");
    return parts.join(" · ");
  }

  /* 비용 줄. 같은 경로(원래 모델·원본 칸 전체)는 116 글자 그대로. */
  function editFixCostText(view, row, plan) {
    var v = view || {};
    var r = row || {};
    var p = plan;
    if (!p || p.same) {
      var est = v.estimate || {};
      return "예상 비용 " + editFixMoney(est.usd) + " — 같은 모델(" + (est.model || "") + ")·같은 길이 " + (Number(est.seconds) || 0) + "초" +
        (est.resolution ? " · " + est.resolution : "") + ". 원래 결과는 그대로 남습니다.";
    }
    var cut = Number(p.trimTo) > 0 && Number(p.sentSeconds) > Number(p.trimTo) + 0.05;
    return "예상 비용 " + editFixMoney(p.usd) + " — " + (r.label || r.id || "") + "(" + (r.id || "") + ") · " + editFixSecs(p.sentSeconds) + "초" +
      (cut ? "(" + editFixSecs(p.trimTo) + "초로 잘라 씀)" : "") + (p.resolution ? " · " + p.resolution : "") + ". 원래 결과는 그대로 남습니다.";
  }

  /* 제출 확인창. 같은 경로는 116 글자 그대로. */
  function editFixConfirmText(view, row, plan, next, range) {
    var v = view || {};
    var r = row || {};
    var p = plan;
    if (!p || p.same) {
      var est = v.estimate || {};
      return "v" + next + "를 같은 모델(" + (est.model || "") + ")·같은 길이로 다시 만듭니다. 예상 비용 " + editFixMoney(est.usd) + ". 원래 결과는 그대로 남습니다.";
    }
    var what = range ? "구간 " + editFixSecs(range.start) + "–" + editFixSecs(range.end) + "초만" : "원본 칸 전체를";
    return "v" + next + "를 " + (r.label || r.id || "") + "(으)로 " + what + " 다시 만듭니다. 예상 비용 " + editFixMoney(p.usd) + ". 원래 결과는 그대로 남습니다.";
  }

  /* 고른 견적의 금액·바이트 상한(견적이 없거나 같은 경로면 116 estimate·limits). */
  function editFixUsd(fix) {
    var v = (fix || {}).view || {};
    var p = editFixPlanOf(v, fix.model, fix.scope).plan;
    return p && p.ok ? p.usd : (v.estimate || {}).usd;
  }

  function editFixMaxBytes(fix) {
    var v = (fix || {}).view || {};
    var p = editFixPlanOf(v, fix.model, fix.scope).plan;
    return p && p.ok && !p.same ? Number(p.maxBytes) || 0 : Number((v.limits || {}).promptMaxBytes) || 0;
  }

  /* 초안이 지금 고른 모델·범위(구간 밀리초까지)로 만든 것인가(116 초안은 원래 모델·원본 칸 전체). */
  function editFixDraftMatches(view, fix) {
    var v = view || {};
    var dr = v.draft || {};
    if ((dr.model || v.defaultModel || "") !== (fix.model || v.defaultModel || "") || (dr.scope || "full") !== (fix.scope || "full")) return false;
    if ((dr.scope || "full") !== "range") return true;
    var a = dr.range || {};
    var b = v.range || {};
    return Math.abs((Number(a.start) || 0) - (Number(b.start) || 0)) < 0.0005 && Math.abs((Number(a.end) || 0) - (Number(b.end) || 0)) < 0.0005;
  }

  /* 미리보기·제출 바디: base + 고른 모델(원래와 다를 때)·범위(구간이면 scope·in·out). 원래 모델·원본 칸 전체는 116 바디 그대로. */
  function editFixBody(fix, view, base) {
    var v = view || {};
    var body = {};
    Object.keys(base || {}).forEach(function (k) {
      body[k] = base[k];
    });
    if (fix.model && fix.model !== v.defaultModel) body.model = fix.model;
    if (fix.scope === "range") {
      body.scope = "range";
      if (fix.clip) {
        body.in = fix.clip.in;
        body.out = fix.clip.out;
      }
    }
    return body;
  }

  /* 초안 상태 줄 꼬리: 새 길 초안이면 " · 모델 이름 · 구간 a–b초". */
  function editFixDraftSuffix(dr) {
    var d = dr || {};
    if (d.format !== "template") return "";
    return " · " + (d.modelLabel || d.model || "") + (d.range ? " · 구간 " + editFixSecs(d.range.start) + "–" + editFixSecs(d.range.end) + "초" : "");
  }

  /* 버전 줄 덧붙임: 원래 모델이 아니면 모델 이름, 구간 버전이면 구간. 둘 다 아니면 "". */
  function editFixVersionMeta(view, ver) {
    var v = ver || {};
    var parts = [];
    if (v.model && v.model !== (view || {}).defaultModel) parts.push(String(v.modelLabel || v.model));
    if (v.range) parts.push("구간 " + editFixSecs(v.range.start) + "–" + editFixSecs(v.range.end) + "초");
    return parts.join(" · ");
  }

  function editFixSwapTitle(fix, ver) {
    var r = (ver || {}).range || {};
    var src = (ver || {}).sourceRange; // 작업 192: 결과 영상 버전 — 고른 컷만, 같은 자리 · 같은 길이
    var pieces = editFixSegInfo(ver); // 작업 194: 여러 클립 조각 버전 — 고른 클립마다 그 조각
    if (pieces) {
      return "고른 컷 " + pieces.length + "개를 " + editFixVerTo(ver) + " 바꿉니다(클립마다 이어진 조각, 같은 자리 · 같은 길이 — 이웃 클립은 그대로)";
    }
    if (src && (ver || {}).videoSource === "result") {
      return (editFixIds(fix) ? "고른 컷" : "타임라인에서 이 구간 안의 클립") + "을 " + editFixVerTo(ver) + " 바꿉니다(원본 " + editFixSecs(src.in) + "–" +
        editFixSecs(src.out) + "초 자리, 같은 자리 · 같은 길이 — 이웃 클립은 그대로)";
    }
    return (fix.clipId ? "고른 클립" : "타임라인에서 이 구간 안의 클립") + "을 " + editFixVerTo(ver) + " 바꿉니다(녹화 " + editFixSecs(r.start) + "–" +
      editFixSecs(r.end) + "초 자리, 속도는 그대로)";
  }

  /* 구간 버전이 끼는가: 새 버전이 구간이거나, 타임라인에 그 칸 구간 버전 클립이 있다(되돌리기). */
  function editFixRanged(ctx, versions, ver) {
    if (ver && ver.range) return true;
    return (versions || []).some(function (x) {
      return !!(x && x.range) && ctx.clips.some(function (c) {
        return c.sourceKey === x.sourceKey;
      });
    });
  }

  function editFixDurations(ctx, versions) {
    var out = {};
    (versions || []).forEach(function (x) {
      if (x && x.sourceKey) out[x.sourceKey] = Number((ctx.materials[x.sourceKey] || {}).duration) || 0;
    });
    return out;
  }

  /* 패널을 고른 클립에서 열었으면 그 클립이 원본의 일부인지(일부면 {in, out}). */
  function editFixClipFor(ctx, clipId) {
    if (!clipId) return null;
    var clip = ctx.clips.filter(function (c) {
      return c.id === clipId;
    })[0];
    var mat = clip ? ctx.materials[clip.sourceKey] || {} : {};
    return clip ? editFixClipPart(clip, mat.duration, (mat.retake || {}).trimTo) : null;
  }

  function editFixViewPath(ctx, fix) {
    var path = editFixPath(ctx, fix);
    if (fix.segments) return path + "?segments=" + encodeURIComponent(editFixSegQuery(ctx, fix.segments)); // 작업 196: 고른 조각 그대로
    if (fix.scope === "range" && fix.clip) path += "?in=" + encodeURIComponent(String(fix.clip.in)) + "&out=" + encodeURIComponent(String(fix.clip.out));
    return path;
  }

  /* 작업 196: 보기 GET 의 segments 쿼리 "mid:in-out,…"(타임라인 순서 · 조각마다 그 재료 원본 초 — 서버 retake._query_segments). 재료 mid 를
     모르면 mid 없이(서버가 보기 재료로 본다). */
  function editFixSegQuery(ctx, segments) {
    return (segments || []).map(function (s) {
      var mat = ((ctx || {}).materials || {})[s.sourceKey];
      var mid = mat && mat.mid ? String(mat.mid) : "";
      return (mid ? mid + ":" : "") + String(editRound3(Number(s.in))) + "-" + String(editRound3(Number(s.out)));
    }).join(",");
  }

  /* 보기가 올 때 범위·모델 기본값. 구간을 기다리던 중 보기에 구간이 없으면(서버가 거절) 원본 칸 전체로 돌리고 이유를 남긴다. 고른 모델을 그
     범위에서 쓸 수 없으면 원래 모델, 그것도 안 되면 쓸 수 있는 첫 모델. */
  function editFixAdopt(fix) {
    var v = fix.view || {};
    if (fix.compose) {
      editFixAdoptCompose(fix); // 작업 192
      return;
    }
    var scopes = Array.isArray(v.scopes) && v.scopes.length ? v.scopes : ["full"];
    if (fix.rangeWait) {
      fix.rangeWait = false;
      fix.scopeNote = v.range ? "" : v.rangeNote || "이 클립 구간은 따로 고칠 수 없습니다.";
    }
    if (!fix.scope || scopes.indexOf(fix.scope) < 0) fix.scope = scopes.indexOf("full") >= 0 ? "full" : "range";
    var dr = v.draft || null;
    if (!fix.model) fix.model = (dr && dr.model) || v.defaultModel || "";
    var cur = editFixPlanOf(v, fix.model, fix.scope);
    if (cur.plan && cur.plan.ok) return;
    var usable = (Array.isArray(v.models) ? v.models : []).filter(function (m) {
      return m && m[fix.scope] && m[fix.scope].ok;
    });
    var orig = usable.filter(function (m) {
      return m.id === v.defaultModel;
    })[0];
    if (orig || usable[0]) fix.model = (orig || usable[0]).id;
  }

  /* 범위 바꾸기: 구간을 처음 고르면 ?in=&out= 로 보기를 다시 받는다(그동안 정보 줄에 "불러오는 중"). */
  function editFixSetScope(ctx, fix, scope) {
    if (!editFixAlive(ctx, fix) || fix.scope === scope) return;
    fix.scope = scope;
    fix.scopeNote = "";
    if (scope === "range" && !(fix.view || {}).range) {
      fix.rangeWait = true;
      editFixPaintSetup(ctx, fix);
      editFixLoad(ctx, fix);
      return;
    }
    editFixAdopt(fix);
    editFixPaint(ctx, fix, fix.view);
  }

  function editFixScopeItem(ctx, fix, parent, value, text) {
    var label = el("label", "vnew-edit-fix-scope-item");
    var input = el("input", null);
    input.type = "radio";
    input.name = "vnew-edit-fix-scope";
    input.value = value;
    input.addEventListener("change", function () {
      if (input.checked) editFixSetScope(ctx, fix, value);
    });
    var span = el("span", null, text);
    label.appendChild(input);
    label.appendChild(span);
    label.radio = input;
    label.text = span;
    if (parent) parent.appendChild(label); // 작업 193: 뼈대에서는 붙이지 않는다(editFixScopeAttach — 옛 길 보기가 온 뒤)
    return label;
  }

  /* 작업 193: 범위 라디오 두 개를 범위 상자에 붙인다(옛 길 보기가 온 뒤 처음 그릴 때만). 보기 전 뼈대·compose 길에는 라디오가 없다. */
  function editFixScopeAttach(d) {
    if (!d.scopeFull.parentNode) d.scope.appendChild(d.scopeFull);
    if (!d.scopeRange.parentNode) d.scope.appendChild(d.scopeRange);
  }

  /* 고정 범위 한 줄(compose 길 · 타임라인에서 고른 컷으로 연 패널). */
  function editFixRangeText(fix) {
    var c = fix.clip;
    if (fix.segments) return "선택한 컷만 고칩니다 (클립 " + fix.segments.length + "개 · 합 " + editFixSecs(fix.total) + "초)"; // 작업 194
    return "선택한 컷 구간만 고칩니다 (" + (c ? editFixSecs(c.in) + "–" + editFixSecs(c.out) + "초" : "구간 모름") + ")";
  }

  /* 「설정」 뼈대(한 번): 범위 상자(빈 채 숨김 — 라디오는 보기가 온 뒤 editFixScopeAttach)·고정 범위 한 줄·영상 모델 상자·정보 줄·뺀 모델 한 줄.
     작업 193: 보기가 오기 전에는 「원본 칸 전체 / 이 구간만 다시 만들기」를 그리지 않는다. 고른 컷으로 열었으면 처음부터 고정 한 줄. */
  function editFixBuildSetup(ctx, fix, parent) {
    var d = fix.dom;
    d.setup = el("div", "vnew-edit-fix-setup");
    d.scope = el("div", "vnew-edit-fix-scope");
    d.scope.setAttribute("role", "radiogroup");
    d.scope.setAttribute("aria-label", "다시 만들 범위");
    d.scope.style.display = "none";
    d.scopeFull = editFixScopeItem(ctx, fix, null, "full", "원본 칸 전체");
    d.scopeRange = editFixScopeItem(ctx, fix, null, "range", "이 구간만 다시 만들기");
    d.setup.appendChild(d.scope);
    d.rangeLine = el("p", "vnew-edit-fix-range-line", ""); // 작업 192: compose 길 = 범위 라디오 대신 고정 한 줄(editFixPaintSetup)
    d.rangeLine.style.display = "none";
    if (fix.fromSel && fix.clip) {
      d.rangeLine.textContent = editFixRangeText(fix);
      d.rangeLine.style.display = "";
    }
    d.setup.appendChild(d.rangeLine);
    d.scopeNote = el("p", "vnew-hint vnew-edit-fix-scope-note", "");
    d.scopeNote.style.display = "none";
    d.setup.appendChild(d.scopeNote);
    d.model = el("select", "vnew-edit-fix-input vnew-edit-fix-model");
    d.model.setAttribute("aria-label", "영상 모델");
    d.model.addEventListener("change", function () {
      fix.model = String(d.model.value || "");
      editFixPaintSetup(ctx, fix);
      editFixPaintDraft(fix);
    });
    d.setup.appendChild(editFixLabeled("영상 모델", d.model));
    d.modelInfo = el("p", "vnew-edit-fix-model-info", "");
    d.setup.appendChild(d.modelInfo);
    d.modelHidden = el("p", "vnew-edit-fix-hidden", "");
    d.modelHidden.style.display = "none";
    d.setup.appendChild(d.modelHidden);
    parent.appendChild(d.setup);
  }

  function editFixPaintSetup(ctx, fix) {
    var v = fix.view || {};
    var d = fix.dom;
    if (!d.setup) return;
    if (fix.compose) {
      editFixPaintComposeSetup(fix); // 작업 192
      return;
    }
    var scopes = Array.isArray(v.scopes) && v.scopes.length ? v.scopes : ["full"];
    var hasRange = !!fix.clip || scopes.indexOf("range") >= 0;
    // 작업 193: 타임라인에서 고른 컷으로 열었고 구간을 받았으면 라디오 없이 고정 한 줄(옛 길 보기여도). 아니면 라디오를 이때 붙인다.
    var fixed = fix.fromSel && fix.scope === "range" && !!v.range;
    d.rangeLine.textContent = fixed ? editFixRangeText(fix) : "";
    d.rangeLine.style.display = fixed ? "" : "none";
    if (!fixed) editFixScopeAttach(d);
    d.scope.style.display = hasRange && !fixed ? "" : "none";
    d.scopeFull.style.display = scopes.indexOf("full") >= 0 ? "" : "none";
    d.scopeFull.radio.checked = fix.scope !== "range";
    d.scopeRange.radio.checked = fix.scope === "range";
    d.scopeFull.text.textContent = "원본 칸 전체" + (Number(v.cellSeconds) > 0 ? " (0.0–" + editFixSecs(v.cellSeconds) + "초)" : "");
    var r = v.range;
    var nos = r && Array.isArray(r.cuts) ? r.cuts.map(function (c) {
      return c.no;
    }).filter(function (n) {
      return n !== null && n !== undefined;
    }) : [];
    d.scopeRange.text.textContent = "이 구간만 다시 만들기" + (r ? " (" + editFixSecs(r.start) + "–" + editFixSecs(r.end) + "초" +
      (nos.length ? " · 컷 " + (nos.length > 1 ? nos[0] + "–" + nos[nos.length - 1] : nos[0]) : "") + ")" : fix.clip ? " (클립 " + editFixSecs(fix.clip.in) + "–" +
      editFixSecs(fix.clip.out) + "초)" : "");
    d.scopeNote.textContent = fix.scopeNote || "";
    d.scopeNote.style.display = fix.scopeNote ? "" : "none";
    clear(d.model);
    var rows = (Array.isArray(v.models) ? v.models : []).filter(function (m) {
      return m && m[fix.scope] && m[fix.scope].ok;
    });
    rows.forEach(function (m) {
      var option = el("option", null, editFixModelOption(m, m[fix.scope]));
      option.value = m.id;
      if (m.id === fix.model) option.selected = true;
      d.model.appendChild(option);
    });
    d.model.value = fix.model || "";
    d.model.disabled = rows.length < 2 || v.canRetake !== true || !!fix.rangeWait;
    var cur = editFixPlanOf(v, fix.model, fix.scope);
    d.modelInfo.textContent = fix.rangeWait ? "구간 정보를 불러오는 중…" : cur.plan && cur.plan.ok ? editFixPlanText(v, cur.row, cur.plan) : "";
    var hidden = Array.isArray(v.modelsHidden) ? v.modelsHidden : [];
    d.modelHidden.style.display = hidden.length ? "" : "none";
    d.modelHidden.textContent = hidden.length ? "이 칸에 쓸 수 없는 모델 " + hidden.length + "개는 목록에서 뺐습니다." : "";
    d.modelHidden.title = hidden.map(function (h) {
      return String(h.label || h.id || "") + ": " + String(h.reason || "");
    }).join("\n");
  }

  /* ---------------------------------------------------------------- ⑦ AI로 고치기 — 프롬프트 준비 · 고른 컷만(작업 192)

     서버가 원본 칸 전체를 막으면(보기 fullScopeAllowed false, 작업 191) 패널은 이 길이다: 범위 라디오 대신 「선택한 컷 구간만 고칩니다 (a–b초)」
     한 줄, [새 프롬프트 만들기](LLM 다시 쓰기) 대신 [프롬프트 준비](POST …/compose {requestKo, extraKo?, model, in, out} — 이전 프롬프트 + 수정 사항),
     모델 상자는 보기 composeModels(고른 구간을 영상→영상으로 고칠 수 있는 모델). 준비한 프롬프트는 읽기만(수정 줄 강조)·견적·비용 줄이고,
     [다시 만들기]는 기존 제출 {prompt, requestKo, extraKo, confirmUsd = draft.plan.usd, draftId, model, scope: "range", in, out}. 수정 사항·추가
     프롬프트·모델·고른 컷이 준비할 때와 다르면 「다시 준비해 주세요」이고 제출은 막힌다(editFixComposeSig).
     결과 영상 버전(videoSource result — @Video1 = 고친 재료 파일의 in–out)의 [바꾸기]는 editFixResultSwap: 고른 컷만, 클립마다 새 in·out =
     옛 in·out − 구간 in(속도·소리 설정 그대로) → 같은 자리 · 같은 길이, 이웃 클립은 그대로, 실행 취소 한 단계. 검사는 check_vpoc ms192. */

  var EDIT_FIX_STALE = "다시 준비해 주세요 — 수정 사항·추가 프롬프트·모델·고른 컷이 프롬프트를 준비할 때와 다릅니다.";
  var EDIT_FIX_NO_V2V = "이 구간을 영상→영상으로 고칠 수 있는 모델이 없습니다.";
  var EDIT_FIX_EDGE_S = 0.06; // 결과 영상 버전 구간 끝 허용(클립 in·out 이 구간 안인가)

  /* [바꾸기] 대상 클립 id 목록(고른 컷에서 열었으면 그 컷들, 고른 클립 하나면 그것). 없으면 null(그 칸 클립 모두). */
  function editFixIds(fix) {
    var ids = (fix || {}).clipIds;
    if (Array.isArray(ids) && ids.length) return ids.slice();
    return fix && fix.clipId ? [fix.clipId] : null;
  }

  /* compose 모델 줄: 보기 models 중 composeModels 에 든 것(그 순서는 models 순서 — 원래 모델 첫 줄). */
  function editFixComposeRows(view) {
    var v = view || {};
    var ids = Array.isArray(v.composeModels) ? v.composeModels : [];
    return (Array.isArray(v.models) ? v.models : []).filter(function (m) {
      return m && ids.indexOf(m.id) >= 0;
    });
  }

  /* 보기가 올 때(compose 길): 범위는 늘 range, 구간을 못 받았으면 이유, 고른 모델이 compose 목록 밖이면 composeDefaultModel(없으면 첫 줄). */
  function editFixAdoptCompose(fix) {
    var v = fix.view || {};
    var rows = editFixComposeRows(v);
    fix.scope = "range";
    fix.rangeWait = false;
    fix.scopeNote = !v.range ? v.rangeNote || "이 구간은 따로 고칠 수 없습니다." : !rows.length ? EDIT_FIX_NO_V2V : "";
    if (rows.some(function (m) {
      return m.id === fix.model;
    })) return;
    var def = rows.filter(function (m) {
      return m.id === v.composeDefaultModel;
    })[0];
    fix.model = (def || rows[0] || {}).id || "";
  }

  /* 「설정」(compose 길): 범위 라디오를 떼고 고정 한 줄 · 모델 상자(compose 목록) · 형식 줄 · 뺀 모델 한 줄. */
  function editFixPaintComposeSetup(fix) {
    var v = fix.view || {};
    var d = fix.dom;
    if (d.scope.parentNode) d.scope.parentNode.removeChild(d.scope); // 「원본 칸 전체」 라디오는 없다
    d.rangeLine.textContent = editFixRangeText(fix);
    d.rangeLine.style.display = "";
    d.scopeNote.textContent = fix.scopeNote || "";
    d.scopeNote.style.display = fix.scopeNote ? "" : "none";
    clear(d.model);
    var rows = editFixComposeRows(v);
    rows.forEach(function (m) {
      var option = el("option", null, String(m.label || m.id) + (m.original ? " · 원래 모델" : ""));
      option.value = m.id;
      if (m.id === fix.model) option.selected = true;
      d.model.appendChild(option);
    });
    d.model.value = fix.model || "";
    d.model.disabled = rows.length < 2 || v.canRetake !== true || !!fix.rangeWait;
    var row = rows.filter(function (m) {
      return m.id === fix.model;
    })[0];
    d.modelInfo.textContent = fix.rangeWait ? "구간 정보를 불러오는 중…" : row ? "형식: " + (EDIT_FIX_FORMATS[row.format] || String(row.format || "")) +
      " · 고른 컷 구간을 영상→영상으로 고칩니다(금액은 [프롬프트 준비] 뒤에 보입니다)" : "";
    var hidden = Array.isArray(v.modelsHidden) ? v.modelsHidden : [];
    d.modelHidden.style.display = hidden.length ? "" : "none";
    d.modelHidden.textContent = hidden.length ? "이 칸에 쓸 수 없는 모델 " + hidden.length + "개는 목록에서 뺐습니다." : "";
    d.modelHidden.title = hidden.map(function (h) {
      return String(h.label || h.id || "") + ": " + String(h.reason || "");
    }).join("\n");
  }

  /* 준비한 프롬프트가 묶이는 입력: 수정 사항 · 추가 프롬프트(앞뒤 공백 뺌) · 모델 · 구간 · 고른 컷 표시. */
  function editFixComposeSig(fix) {
    var d = fix.dom;
    var c = fix.clip || {};
    return JSON.stringify([String(d.request.value || "").trim(), String(d.extra.value || "").trim(), fix.model || "", c.in, c.out, fix.selNow || ""]);
  }

  /* "none"(이 패널에서 아직 준비하지 않음) · "stale"(입력이 바뀜 · 서버 초안이 바뀜) · "ready". */
  function editFixComposeState(fix) {
    if (!fix.draftId) return "none";
    var dr = (fix.view || {}).draft || {};
    if (dr.id !== fix.draftId || dr.kind !== "compose" || dr.status !== "done") return "stale";
    return fix.draftSig === editFixComposeSig(fix) ? "ready" : "stale";
  }

  /* 준비한 프롬프트에서 수정 줄 번호(번역 한 문장, 없으면 한국어 수정 사항이 든 첫 줄). 못 찾으면 −1(강조 없음). */
  function editFixChangeLine(prompt, draft) {
    var lines = String(prompt || "").split("\n");
    var dr = draft || {};
    var keys = [String(dr.changeEn || "").trim(), String(dr.requestKo || "").replace(/\s+/g, " ").trim()].filter(Boolean);
    for (var k = 0; k < keys.length; k++) {
      for (var i = 0; i < lines.length; i++) {
        if (lines[i].indexOf(keys[k]) >= 0) return i;
      }
    }
    return -1;
  }

  /* 견적 한 줄(draft.plan): 고른 구간 길이 · 더 길게 만들면 그 까닭(작업 196 sentReason — min 모델 최소 길이 · step 모델 길이 단위)과 그 길이 ·
     잘라 쓰는 길이 · 해상도 · 예상 금액. 까닭이 없으면(옛 초안 포함) 만드는 길이만. */
  function editFixComposeEstimate(plan) {
    var p = plan || {};
    var len = Number(p.trimTo) > 0 ? Number(p.trimTo) : Number(p.seconds) || 0;
    var sent = Number(p.sentSeconds) || len;
    var longer = sent > len + 0.05;
    var parts = ["견적: 고른 구간 " + editFixSecs(len) + "초"];
    if (longer && p.sentReason === "min") {
      parts.push("모델 최소 길이(" + editFixSecs(sent) + "초)에 맞춰 " + editFixSecs(sent) + "초로 만들고 " + editFixSecs(len) + "초만 씀");
    } else if (longer && p.sentReason === "step") {
      parts.push("모델이 만드는 길이 단위에 맞춰 " + editFixSecs(sent) + "초로 만들고 " + editFixSecs(len) + "초만 씀");
    } else {
      parts.push(editFixSecs(sent) + "초로 만듦");
    }
    if (p.resolution) parts.push(String(p.resolution));
    parts.push("예상 " + editFixMoney(p.usd));
    return parts.join(" · ");
  }

  /* 초안 칸(compose 길): 준비 전이면 숨김, 입력이 바뀌었으면 「다시 준비해 주세요」+ 제출 막힘, 아니면 상태 · 준비한 프롬프트(읽기만) · 견적 ·
     바이트 · 서버 문제 · 비용 · [다시 만들기 ($x)]. 서버 문제(check_prompt)가 있으면 제출할 수 없다(프롬프트는 고칠 수 없다 — 다시 준비). */
  function editFixPaintCompose(fix) {
    var v = fix.view || {};
    var d = fix.dom;
    var dr = v.draft || {};
    var state = editFixComposeState(fix);
    d.changes.style.display = "none";
    d.newField.style.display = "none";
    if (state === "none") {
      d.draft.style.display = "none";
      return;
    }
    d.draft.style.display = "";
    var ready = state === "ready";
    [d.result, d.composed, d.meter, d.problems, d.cost].forEach(function (node) {
      node.style.display = ready ? "" : "none";
    });
    d.submit.style.display = "";
    d.draftStatus.className = "vnew-edit-fix-status" + (ready ? "" : " is-stale");
    var plan = dr.plan || {};
    var again = editFixNextN(v) === Number(((v.versions || []).slice(-1)[0] || {}).n);
    d.submit.textContent = (again ? "다시 시도" : "다시 만들기") + (ready ? " (" + editFixMoney(plan.usd) + ")" : "");
    if (!ready) {
      d.draftStatus.textContent = EDIT_FIX_STALE;
      d.submit.disabled = true;
      return;
    }
    var row = editFixPlanOf(v, dr.model, "range").row || { id: dr.model, label: dr.modelLabel };
    var c = fix.clip || {};
    var where = fix.segments ? "고른 컷 " + fix.segments.length + "개 · 합 " + editFixSecs(fix.total) + "초" : "구간 " + editFixSecs(c.in) + "–" +
      editFixSecs(c.out) + "초"; // 작업 194
    d.draftStatus.textContent = "고칠 프롬프트 — 이전 프롬프트 + 수정 사항 · " + String(row.label || row.id || "") + " · " + where +
      (dr.translated === false ? " · 수정 사항을 번역하지 못해 한국어 그대로 넣었습니다" : "");
    var prompt = String(dr.prompt || "");
    var at = editFixChangeLine(prompt, dr);
    clear(d.composed);
    prompt.split("\n").forEach(function (line, i) {
      d.composed.appendChild(el("span", i === at ? "vnew-edit-fix-change" : null, line + "\n"));
    });
    d.result.textContent = editFixComposeEstimate(plan);
    var max = Number(plan.maxBytes) || Number(dr.maxBytes) || 0;
    var bytes = editFixBytes(prompt);
    d.meter.textContent = editFixComma(bytes) + " / " + editFixComma(max) + "바이트";
    d.meter.className = "vnew-edit-fix-meter" + (max > 0 && bytes > max ? " is-over" : "");
    clear(d.problems);
    var problems = Array.isArray(dr.problems) ? dr.problems : [];
    problems.forEach(function (problem) {
      d.problems.appendChild(el("li", null, String(problem)));
    });
    if (problems.length) d.problems.appendChild(el("li", "vnew-edit-fix-edited", "수정 사항·추가 프롬프트를 줄이거나 다른 모델을 고른 뒤 다시 준비해 주세요."));
    d.cost.textContent = editFixCostText(v, row, plan);
    d.submit.disabled = fix.busy || !!v.live || !prompt.trim() || problems.length > 0;
  }

  /* [프롬프트 준비]: POST …/compose. 응답 보기의 초안(kind compose · done)을 이 패널 초안으로 두고, 누른 때 입력을 묶는다. */
  function editFixCompose(ctx, fix) {
    if (!editFixAlive(ctx, fix) || fix.busy || !fix.clip) return;
    var d = fix.dom;
    var request = String(d.request.value || "").trim();
    if (!request) {
      editFixNote(fix, "수정 사항을 적어 주세요.");
      return;
    }
    var extra = String(d.extra.value || "").trim();
    // 작업 194: 같은 칸 여러 클립이면 in·out 대신 segments(타임라인 순서)
    var body = fix.segments ? { requestKo: request, segments: editFixSegCopy(fix.segments) } : { requestKo: request, in: fix.clip.in, out: fix.clip.out };
    if (extra) body.extraKo = extra;
    if (fix.model) body.model = fix.model;
    var sig = editFixComposeSig(fix);
    fix.busy = true;
    editFixNote(fix, "");
    editFixPaint(ctx, fix, fix.view);
    api(editFixPath(ctx, fix) + "/compose", { method: "POST", body: JSON.stringify(body) })
      .then(function (res) {
        fix.busy = false;
        if (!editFixAlive(ctx, fix)) return;
        var view = (res && res.retake) || fix.view || {};
        var dr = view.draft || {};
        if (dr.kind === "compose" && dr.status === "done" && dr.id) {
          fix.draftId = String(dr.id);
          fix.draftSig = sig;
        }
        editFixApply(ctx, fix, view);
      })
      .catch(function (err) {
        fix.busy = false;
        if (!editFixAlive(ctx, fix)) return;
        editFixPaint(ctx, fix, fix.view);
        editFixNote(fix, "프롬프트를 준비하지 못했습니다: " + errMessage(err));
      });
  }

  /* [다시 만들기](compose 길): 확인창 → 기존 제출(초안 그대로, scope range · 같은 in·out · 초안 모델 · confirmUsd = draft.plan.usd). */
  function editFixSubmitCompose(ctx, fix) {
    var v = fix.view || {};
    var dr = v.draft || {};
    if (editFixComposeState(fix) !== "ready" || v.live || !fix.clip) return;
    var plan = dr.plan || {};
    var usd = Number(plan.usd) || 0;
    var next = editFixNextN(v);
    var row = editFixPlanOf(v, dr.model, "range").row || { id: dr.model, label: dr.modelLabel };
    var span = editFixSecs(fix.clip.in) + "–" + editFixSecs(fix.clip.out) + "초";
    var what = fix.segments ? "고른 컷 " + fix.segments.length + "개(합 " + editFixSecs(fix.total) + "초)" : "선택한 컷 구간(" + span + ")"; // 작업 194
    if (!editConfirm("v" + next + "를 " + String(row.label || row.id || "") + "(으)로 " + what + "만 다시 만듭니다. 예상 비용 " +
      editFixMoney(usd) + ". 원래 결과는 그대로 남습니다.")) return;
    var body = { prompt: String(dr.prompt || ""), requestKo: String(dr.requestKo || ""), extraKo: String(dr.extraKo || ""), confirmUsd: usd,
                 draftId: String(dr.id || ""), model: String(dr.model || fix.model || ""), scope: "range" };
    if (fix.segments) {
      body.segments = editFixSegCopy(fix.segments);
    } else {
      body.in = fix.clip.in;
      body.out = fix.clip.out;
    }
    fix.busy = true;
    fix.dom.submit.disabled = true;
    editFixNote(fix, "");
    api(editFixPath(ctx, fix), { method: "POST", body: JSON.stringify(body) })
      .then(function (res) {
        fix.busy = false;
        if (!editFixAlive(ctx, fix)) return;
        fix.draftId = ""; // 낸 초안은 다 썼다(다시 내려면 다시 준비)
        fix.draftSig = "";
        editFixApply(ctx, fix, (res && res.retake) || v);
        editFixNote(fix, "v" + next + "를 만들기 시작했습니다. 끝나면 [바꾸기]·[재료에 추가]가 생깁니다.");
      })
      .catch(function (err) {
        fix.busy = false;
        if (!editFixAlive(ctx, fix)) return;
        editFixPaint(ctx, fix, fix.view);
        editFixNote(fix, "다시 만들지 못했습니다: " + errMessage(err));
      });
  }

  /* 결과 영상 버전 줄 → {key(그 버전 파일), fromKey(고친 재료 파일 = fromN 버전의 원본), in, out(fromKey 초), len(쓸 길이)}. 아니면 null(순수). */
  function editFixResultInfo(versions, ver) {
    var x = ver || {};
    var r = x.sourceRange || {};
    var a = Number(r.in);
    var b = Number(r.out);
    if (x.videoSource !== "result" || !x.sourceKey || r.in === null || r.out === null || !isFinite(a) || !isFinite(b) || !(b > a)) return null;
    if (Array.isArray(x.segments) && x.segments.length) return null; // 작업 194: 여러 클립 조각 버전은 editFixSegSwap
    var from = (versions || []).filter(function (y) {
      return y && y.n === x.fromN && y.sourceKey;
    })[0];
    if (!from) return null;
    var trim = Number(x.trimTo) > 0 ? Number(x.trimTo) : Number(x.seconds) > 0 ? Number(x.seconds) : b - a;
    return { key: String(x.sourceKey), fromKey: String(from.sourceKey), in: a, out: b, len: Math.min(trim, b - a) };
  }

  /* [바꾸기]에 결과 영상 버전이 끼는가: 새 버전이 결과 영상 버전이거나, 타임라인에 새 버전 파일을 고친 결과 영상 버전 클립이 있다(되돌리기). */
  function editFixResultUses(clips, versions, ver) {
    if (editFixResultInfo(versions, ver)) return true;
    var key = (ver || {}).sourceKey;
    return (versions || []).some(function (x) {
      var info = editFixResultInfo(versions, x);
      return !!info && info.fromKey === key && (clips || []).some(function (c) {
        return c.sourceKey === info.key;
      });
    });
  }

  /* 결과 영상 버전 [바꾸기](순수). 앞으로: 원본이 그 버전의 fromKey 이고 in·out 이 구간 [in, out] 안(±EDIT_FIX_EDGE_S)인 클립(ids 가 있으면 그 중)을
     새 in = 옛 in − 구간 in · 새 out = 옛 out − 구간 in(쓸 길이 · 새 재료 길이 안)으로. 되돌리기: 결과 영상 버전 클립을 그 fromKey(= 새 버전 파일)로
     새 in = 옛 in + 구간 in. 속도 · 소리 끔 · J/L · 대사 타이밍 · 전환 · id 는 그대로라 타임라인 자리와 길이가 같고 이웃 클립은 그대로다. 이름은 116 규칙.
     ids 안인데 구간 밖이면 outside, 최소 길이보다 짧아지면 skipped. 반환 {clips, changed, skipped, outside}. */
  function editFixResultSwap(clips, ids, versions, newVer, durations, minLen, newLabel, oldLabels) {
    var infos = {};
    (versions || []).forEach(function (x) {
      var info = editFixResultInfo(versions, x);
      if (info) infos[info.key] = info;
    });
    var nv = newVer || {};
    var newKey = String(nv.sourceKey || "");
    var fwd = infos[newKey] || null;
    var dur = Number((durations || {})[newKey]) || 0;
    var idSet = null;
    if (ids && ids.length) {
      idSet = {};
      ids.forEach(function (id) {
        idSet[id] = true;
      });
    }
    var changed = 0;
    var skipped = 0;
    var outside = 0;
    var out = editCloneClips(clips).map(function (clip) {
      if ((idSet && !idSet[clip.id]) || clip.sourceKey === newKey) return clip;
      var a = Number(clip.in);
      var b = Number(clip.out);
      var shift = 0;
      var extent = Infinity;
      if (fwd && clip.sourceKey === fwd.fromKey) {
        if (a < fwd.in - EDIT_FIX_EDGE_S || b > fwd.out + EDIT_FIX_EDGE_S) {
          if (idSet) outside += 1;
          return clip;
        }
        shift = -fwd.in;
        extent = fwd.len;
      } else {
        var back = infos[clip.sourceKey];
        if (!back || back.fromKey !== newKey) return clip;
        shift = back.in;
      }
      if (dur > 0) extent = Math.min(extent, dur);
      var na = Math.max(0, a + shift);
      var nb = Math.min(extent, b + shift);
      if (!(nb - na >= editMinSource(minLen, editSpeedOf(clip)) - 1e-9)) {
        skipped += 1;
        return clip;
      }
      var next = editCloneClip(clip);
      next.sourceKey = newKey;
      next.in = editRound3(na);
      next.out = editRound3(nb);
      if (newLabel && (!clip.label || (oldLabels || []).indexOf(clip.label) >= 0)) next.label = String(newLabel).slice(0, 40);
      changed += 1;
      return next;
    });
    return { clips: out, changed: changed, skipped: skipped, outside: outside };
  }

  /* ---- 작업 194: 여러 클립 조각(segments) 버전 [바꾸기] ---- */

  function editFixSegCopy(list) {
    return (list || []).map(function (s) {
      return { sourceKey: String(s.sourceKey), in: Number(s.in), out: Number(s.out) };
    });
  }

  /* 조각 버전 줄 → [{sourceKey, in, out, at, len}](at = 새 버전 파일 안 시작 = 앞 조각 길이 합). 조각 버전이 아니면 null(순수). */
  function editFixSegInfo(ver) {
    var x = ver || {};
    if (x.videoSource !== "result" || !x.sourceKey || !Array.isArray(x.segments) || !x.segments.length) return null;
    var at = 0;
    var out = [];
    for (var i = 0; i < x.segments.length; i++) {
      var s = x.segments[i] || {};
      var a = Number(s.in);
      var b = Number(s.out);
      if (!s.sourceKey || !isFinite(a) || !isFinite(b) || !(b > a)) return null;
      out.push({ sourceKey: String(s.sourceKey), in: a, out: b, at: editRound3(at), len: editRound3(b - a) });
      at += b - a;
    }
    return out;
  }

  /* [바꾸기]에 조각 버전이 끼는가: 새 버전이 조각 버전이거나, 타임라인에 새 버전 파일을 조각으로 쓴 조각 버전 클립이 있다(되돌리기). */
  function editFixSegUses(clips, versions, ver) {
    if (editFixSegInfo(ver)) return true;
    var key = (ver || {}).sourceKey;
    return (versions || []).some(function (x) {
      var pieces = editFixSegInfo(x);
      return !!pieces && pieces.some(function (p) {
        return p.sourceKey === key;
      }) && (clips || []).some(function (c) {
        return c.sourceKey === x.sourceKey;
      });
    });
  }

  /* 조각 버전 [바꾸기](순수). 앞으로: 조각 k 마다 원본 · in · out 이 그 조각과 같은(±EDIT_FIX_SEG_EPS, 속도 1) 클립 하나(ids 가 있으면 그 중)를
     새 버전 파일의 [at_k, at_k + 길이_k] 로 — 고른 클립마다 이어진 조각이라 같은 자리 · 같은 길이, 이웃 클립은 그대로. 짝이 없는 조각은 outside.
     되돌리기: 조각 버전 클립 중 한 조각 안에 드는 클립을 그 조각 원본이 새 버전이면 원본 in = 조각 in + (클립 in − at_k)로. 소리 끔 · J/L · 대사
     타이밍 · 전환 · id 는 그대로, 이름은 116 규칙. 반환 {clips, changed, skipped, outside}. */
  function editFixSegSwap(clips, ids, versions, newVer, durations, minLen, newLabel, oldLabels) {
    var nv = newVer || {};
    var newKey = String(nv.sourceKey || "");
    var fwd = editFixSegInfo(nv);
    var dur = Number((durations || {})[newKey]) || 0;
    var idSet = null;
    if (ids && ids.length) {
      idSet = {};
      ids.forEach(function (id) {
        idSet[id] = true;
      });
    }
    var out = editCloneClips(clips);
    var changed = 0;
    var skipped = 0;
    var outside = 0;
    function put(index, na, nb) {
      var clip = out[index];
      if (!(nb - na >= editMinSource(minLen, editSpeedOf(clip)) - 1e-9)) {
        skipped += 1;
        return;
      }
      var next = editCloneClip(clip);
      next.sourceKey = newKey;
      next.in = editRound3(na);
      next.out = editRound3(nb);
      if (newLabel && (!clip.label || (oldLabels || []).indexOf(clip.label) >= 0)) next.label = String(newLabel).slice(0, 40);
      out[index] = next;
      changed += 1;
    }
    if (fwd) {
      var used = {};
      fwd.forEach(function (p) {
        var found = -1;
        for (var i = 0; i < out.length && found < 0; i++) {
          var c = out[i];
          if (used[i] || (idSet && !idSet[c.id]) || c.sourceKey !== p.sourceKey || editSpeedOf(c) !== 1) continue;
          if (Math.abs(Number(c.in) - p.in) <= EDIT_FIX_SEG_EPS && Math.abs(Number(c.out) - p.out) <= EDIT_FIX_SEG_EPS) found = i;
        }
        if (found < 0) {
          outside += 1;
          return;
        }
        used[found] = true;
        put(found, p.at, dur > 0 ? Math.min(dur, p.at + p.len) : p.at + p.len);
      });
      return { clips: out, changed: changed, skipped: skipped, outside: outside };
    }
    var infos = {};
    (versions || []).forEach(function (x) {
      var pieces = editFixSegInfo(x);
      if (pieces) infos[x.sourceKey] = pieces;
    });
    out.forEach(function (c, i) {
      var pieces = infos[c.sourceKey];
      if (!pieces || (idSet && !idSet[c.id])) return;
      var a = Number(c.in);
      var b = Number(c.out);
      var p = pieces.filter(function (q) {
        return a >= q.at - EDIT_FIX_SEG_EPS && b <= q.at + q.len + EDIT_FIX_SEG_EPS;
      })[0];
      if (!p || p.sourceKey !== newKey) return;
      put(i, Math.max(p.in, p.in + (a - p.at)), Math.min(p.out, p.in + (b - p.at)));
    });
    return { clips: out, changed: changed, skipped: skipped, outside: outside };
  }

  /* ---------------------------------------------------------------- ⑦ AI로 고치기 — 버전 미리보기·타임라인 사용 표시(지시서 116 보완 2)

     버전 줄 이름을 누르면(Enter·Space 도) 그 버전 영상을 패널 미리보기에서 재생하고 그 줄에 is-previewing 을 단다. 완성된 버전만 누를 수 있고
     (진행 중·실패는 상태 글만), 주소는 패널을 연 버전이면 보기 videoUrl, 그 밖은 재료 videoUrl(GET /edit 이 이미 준다 — 서버 변경 없음).
     is-current 는 "지금 편집 문서(타임라인)가 쓰는 버전"이고 배지 「타임라인 사용 중」을 붙인다(여러 버전에 걸치면 각각). 패널을 연 기준 버전은
     is-base 다. 사용 중 표시는 editChanged(바꾸기·되돌리기·실행 취소/다시 실행)와 패널 다시 그리기(다시 불러오기·조회) 때 다시 센다.
     검사는 check_vpoc rtk-v. */

  /* 그 칸 버전 원본 → 지금 클립 중 그 원본을 쓰는 클립 수(순수). 버전 원본이 아닌 클립은 세지 않는다. */
  function editFixInUse(clips, versions) {
    var use = {};
    (versions || []).forEach(function (x) {
      if (x && x.sourceKey) use[x.sourceKey] = 0;
    });
    (clips || []).forEach(function (c) {
      if (c && Object.prototype.hasOwnProperty.call(use, c.sourceKey)) use[c.sourceKey] += 1;
    });
    return use;
  }

  /* 버전 줄 상태 클래스(순수): 타임라인 사용 중 is-current · 패널을 연 기준 버전 is-base · 미리보기 중 is-previewing. */
  function editFixRowState(view, ver, use, previewMid) {
    var x = ver || {};
    var out = "";
    if (x.sourceKey && (use || {})[x.sourceKey] > 0) out += " is-current";
    if (x.n !== undefined && x.n !== null && x.n === (view || {}).n) out += " is-base";
    if (previewMid && x.mid === previewMid) out += " is-previewing";
    return out;
  }

  /* 미리보기 주소: 완성된 버전만. 패널을 연 버전은 보기 videoUrl, 그 밖은 재료 videoUrl(재료를 아직 못 받았으면 ""). */
  function editFixVersionUrl(ctx, view, ver) {
    var x = ver || {};
    var v = view || {};
    if (x.status !== "completed" || !x.sourceKey) return "";
    if (x.mid && x.mid === v.mid && v.videoUrl) return String(v.videoUrl);
    return String(((ctx.materials || {})[x.sourceKey] || {}).videoUrl || "");
  }

  function editFixUseBadge(use, ver) {
    var count = (use || {})[(ver || {}).sourceKey] || 0;
    if (!(count > 0)) return null;
    var badge = el("span", "vnew-badge", "타임라인 사용 중" + (count > 1 ? " · 클립 " + count + "개" : ""));
    badge.title = "지금 타임라인에서 이 버전을 쓰는 클립 " + count + "개";
    return badge;
  }

  /* 이름 칸을 누를 수 있게(미리보기 주소가 있을 때만): role=button·tabindex·aria-pressed, 클릭·Enter·Space. */
  function editFixPickable(ctx, fix, ver, name) {
    var base = ver.n === (fix.view || {}).n ? " (이 패널을 연 기준 버전)" : "";
    var url = editFixVersionUrl(ctx, fix.view, ver);
    if (!url) {
      if (base) name.title = editFixVerName(ver) + base;
      return;
    }
    var on = !!fix.previewMid && ver.mid === fix.previewMid;
    name.setAttribute("role", "button");
    name.setAttribute("tabindex", "0");
    name.setAttribute("aria-pressed", on ? "true" : "false");
    name.title = (on ? "미리보기 중" : "눌러서 미리보기") + " — " + editFixVerName(ver) + base;
    name.addEventListener("click", function () {
      editFixShowVersion(ctx, fix, ver);
    });
    name.addEventListener("keydown", function (event) {
      var k = event && event.key;
      if (k !== "Enter" && k !== " " && k !== "Spacebar") return;
      if (event.preventDefault) event.preventDefault();
      editFixShowVersion(ctx, fix, ver);
    });
    if (ver.mid) fix.dom.picks[ver.mid] = name;
  }

  /* 고른 버전을 미리보기에서 재생할 준비(주소가 바뀔 때만 src 를 바꾼다)·줄 다시 그리기·키보드 초점 되돌리기. */
  function editFixShowVersion(ctx, fix, ver) {
    if (!editFixAlive(ctx, fix)) return;
    var url = editFixVersionUrl(ctx, fix.view, ver);
    if (!url) return;
    var video = fix.dom.video;
    if (fix.previewMid !== ver.mid || video.src !== url) {
      try {
        video.pause();
      } catch (error) {
        /* 이미 떨어진 요소 */
      }
      video.src = url;
    }
    fix.previewMid = String(ver.mid || "");
    editFixPaintVersions(ctx, fix);
    var again = (fix.dom.picks || {})[fix.previewMid];
    if (again && typeof again.focus === "function") again.focus();
  }

  /* ---------------------------------------------------------------- ⑦ AI로 고치기 — 테이크·장면 고르기(지시서 119)
     머리 줄 [AI로 고치기] 하나 → 고르기 창(테이크 라디오 · 그 테이크의 장면(컷) 체크박스) → 116 패널(editFixOpen). 장면 경계는 서버
     재료 표식 retake.cuts(원본 시간). 서버 구간은 하나라 떨어진 장면을 고르면 첫 장면 시작~마지막 장면 끝 한 구간이다. */

  /* 고른 장면들 → 한 구간 {in, out}(첫 시작~마지막 끝, 원본 시간) 또는 null(고른 것 없음 = 통째). 순서는 상관없다. */
  function editFixCutsRange(cuts) {
    var list = (Array.isArray(cuts) ? cuts : []).filter(function (c) {
      return c && isFinite(Number(c.in)) && isFinite(Number(c.out)) && Number(c.out) > Number(c.in);
    });
    if (!list.length) return null;
    var a = Math.min.apply(null, list.map(function (c) {
      return Number(c.in);
    }));
    var b = Math.max.apply(null, list.map(function (c) {
      return Number(c.out);
    }));
    return { in: editRound3(a), out: editRound3(b) };
  }

  /* 장면 이름: 컷 번호가 있으면 "컷 3", 없으면 "장면 2", 고른 클립 구간(장면 경계를 모를 때)이면 그 말. */
  function editFixPickCutName(cut, index) {
    if (cut && cut.clip) return "타임라인에서 고른 클립 구간";
    return cut && cut.no !== null && cut.no !== undefined ? "컷 " + cut.no : "장면 " + (index + 1);
  }

  /* 창이 패널에 넘길 구간: 고른 것 없음·장면을 모두 고름(고른 클립 구간 하나는 빼고) → null(통째), 아니면 editFixCutsRange. */
  function editFixPickRange(cuts, picked) {
    var list = Array.isArray(cuts) ? cuts : [];
    var chosen = list.filter(function (c, i) {
      return !!(picked || [])[i];
    });
    if (!chosen.length || (chosen.length === list.length && !list[0].clip)) return null;
    return editFixCutsRange(chosen);
  }

  /* 고르기 창 안내 한 줄(통째·한 구간·떨어진 장면이면 사이 장면 이름). */
  function editFixPickNote(cuts, picked) {
    var list = Array.isArray(cuts) ? cuts : [];
    if (!list.length) return "이 테이크는 장면 경계를 알 수 없어 통째로 고칩니다.";
    var r = editFixPickRange(list, picked);
    var n = list.filter(function (c, i) {
      return !!(picked || [])[i];
    }).length;
    if (!r) return n ? "모든 장면을 골라 테이크 통째로 고칩니다." : "아무 장면도 고르지 않으면 테이크 통째로 고칩니다.";
    var span = editFixSecs(r.in) + "–" + editFixSecs(r.out) + "초";
    var between = [];
    list.forEach(function (c, i) {
      if (!(picked || [])[i] && Number(c.in) >= r.in - 0.001 && Number(c.out) <= r.out + 0.001) between.push(editFixPickCutName(c, i));
    });
    if (between.length) {
      return "고른 장면이 떨어져 있어 첫 장면 시작부터 마지막 장면 끝까지(" + span + ") 한 구간으로 고칩니다. 사이 장면(" + between.join("·") +
        ")도 함께 다시 만들어집니다.";
    }
    return "고른 구간(" + span + ")만 고칩니다. 패널의 「이 구간만 다시 만들기」가 이 구간입니다.";
  }

  /* 고를 수 있는 재료(테이크·컷·v2…, 서버 retake.n 이 있는 것 — 합친 영상·지난 실사 회차는 없다). 이름 = 재료 줄과 같은 이름·길이. */
  function editFixPickMats(ctx) {
    var out = [];
    (ctx.matList[ctx.variant] || []).forEach(function (item) {
      var mat = ctx.materials[item.sourceKey] || item;
      var info = mat.retake;
      if (!info || !info.n) return;
      var duration = Number(mat.duration);
      if (mat.group === "retake" && Number(info.trimTo) > 0 && duration > 0) duration = Math.min(duration, Number(info.trimTo));
      out.push({ mid: String(mat.mid || item.mid || ""), mat: mat, can: info.can === true, reason: info.reason || "AI로 고칠 수 없습니다",
                 name: (mat.label || "영상") + (duration > 0 ? " · " + duration.toFixed(1) + "초" : "") + (info.running ? " · 다시 만드는 중" : "") });
    });
    return out;
  }

  /* 머리 줄 [AI로 고치기](켬일 때만 editRenderBin 이 붙인다). 고칠 수 있는 재료가 없으면 흐림. */
  function editFixPickButton(ctx) {
    var btn = el("button", "outline-button vnew-edit-fix-pick", "AI로 고치기");
    btn.type = "button";
    var any = editFixPickMats(ctx).some(function (m) {
      return m.can;
    });
    btn.disabled = !any;
    btn.title = any ? "고칠 테이크와 장면을 골라 AI로 고칩니다" : "AI로 고칠 수 있는 테이크·컷이 없습니다";
    btn.setAttribute("aria-haspopup", "dialog");
    btn.addEventListener("click", function () {
      editFixPickOpen(ctx);
    });
    return btn;
  }

  function editFixPickClose(ctx) {
    var pick = ctx && ctx.fixPick;
    if (!pick) return;
    if (pick.dom.root && pick.dom.root.parentNode) pick.dom.root.parentNode.removeChild(pick.dom.root);
    ctx.fixPick = null;
  }

  /* 창 열기: 처음 테이크 = 타임라인에서 고른 클립의 재료(고칠 수 있으면), 아니면 첫 항목. */
  function editFixPickOpen(ctx) {
    if (!editAlive(ctx) || ctx.busy || !editFixOn(ctx)) return;
    var mats = editFixPickMats(ctx);
    var usable = mats.filter(function (m) {
      return m.can;
    });
    if (!usable.length) return;
    editPause(ctx);
    editFixClose(ctx);
    editFixPickClose(ctx);
    var sel = editFixSelMat(ctx);
    var selMid = sel ? String(sel.mid || "") : "";
    var start = usable.some(function (m) {
      return m.mid === selMid;
    }) ? selMid : usable[0].mid;
    var pick = { mid: "", mats: mats, cuts: [], picked: [], dom: {} };
    ctx.fixPick = pick;
    editFixPickBuild(ctx, pick);
    ctx.root.appendChild(pick.dom.root);
    editFixPickChoose(ctx, pick, start);
    var radio = pick.dom.radios[start];
    if (radio && typeof radio.focus === "function") radio.focus();
  }

  /* 뼈대(한 번): 머리 · 테이크 라디오 · 장면 체크박스 칸 · 안내 · [닫기]·[AI로 고치기]. */
  function editFixPickBuild(ctx, pick) {
    var d = pick.dom;
    d.root = el("div", "vnew-edit-pick");
    d.root.setAttribute("role", "dialog");
    d.root.setAttribute("aria-modal", "true");
    d.root.setAttribute("aria-label", "AI로 고칠 테이크·장면 고르기");
    var box = el("div", "vnew-edit-fix-box vnew-edit-pick-box");
    var head = el("div", "vnew-edit-fix-head");
    head.appendChild(el("h3", "vnew-edit-fix-title", "AI로 고치기 — 테이크·장면 고르기"));
    var close = el("button", "vnew-edit-fix-close", "×");
    close.type = "button";
    close.title = "닫기 (Esc)";
    close.setAttribute("aria-label", close.title);
    close.addEventListener("click", function () {
      editFixPickClose(ctx);
    });
    head.appendChild(close);
    box.appendChild(head);
    var takes = el("fieldset", "vnew-edit-pick-list");
    takes.appendChild(el("legend", "vnew-edit-fix-label", "테이크"));
    d.radios = {};
    pick.mats.forEach(function (m) {
      var label = el("label", "vnew-edit-pick-item" + (m.can ? "" : " is-off"));
      var input = el("input", null);
      input.type = "radio";
      input.name = "vnew-pick-take";
      input.value = m.mid;
      input.disabled = !m.can;
      input.addEventListener("change", function () {
        if (input.checked) editFixPickChoose(ctx, pick, m.mid);
      });
      if (!m.can) label.title = m.reason;
      label.appendChild(input);
      label.appendChild(el("span", null, m.name));
      d.radios[m.mid] = input;
      takes.appendChild(label);
    });
    box.appendChild(takes);
    var cuts = el("fieldset", "vnew-edit-pick-list");
    cuts.appendChild(el("legend", "vnew-edit-fix-label", "장면(컷) — 여러 개 고를 수 있습니다"));
    var tools = el("div", "vnew-edit-pick-tools");
    d.all = el("button", "outline-button", "전체");
    d.none = el("button", "outline-button", "모두 해제");
    [d.all, d.none].forEach(function (btn, i) {
      btn.type = "button";
      btn.addEventListener("click", function () {
        pick.picked = pick.cuts.map(function () {
          return i === 0;
        });
        (d.boxes || []).forEach(function (input, j) {
          input.checked = !!pick.picked[j];
        });
        editFixPickPaintNote(pick);
      });
      tools.appendChild(btn);
    });
    cuts.appendChild(tools);
    d.cutList = el("div", "vnew-edit-pick-cuts");
    cuts.appendChild(d.cutList);
    box.appendChild(cuts);
    d.note = el("p", "vnew-hint vnew-edit-pick-note", "");
    d.note.setAttribute("aria-live", "polite");
    box.appendChild(d.note);
    var actions = el("div", "vnew-edit-pick-actions");
    var cancel = el("button", "outline-button", "닫기");
    cancel.type = "button";
    cancel.addEventListener("click", function () {
      editFixPickClose(ctx);
    });
    d.go = el("button", "primary-button vnew-edit-pick-go", "AI로 고치기");
    d.go.type = "button";
    d.go.addEventListener("click", function () {
      if (ctx.fixPick !== pick || !pick.mid) return;
      var mid = pick.mid;
      var range = editFixPickRange(pick.cuts, pick.picked);
      editFixPickClose(ctx);
      editFixOpen(ctx, mid, "", range);
    });
    actions.appendChild(cancel);
    actions.appendChild(d.go);
    box.appendChild(actions);
    d.root.appendChild(box);
  }

  /* 테이크 고르기: 그 재료의 장면 목록을 다시 그린다. 처음에는 아무것도 고르지 않는다 — 타임라인에서 고른 클립이 이 재료의 일부면 그
     구간과 겹치는 장면을 고른다(장면 경계를 모르면 그 클립 구간 한 줄). */
  function editFixPickChoose(ctx, pick, mid) {
    pick.mid = mid;
    var entry = pick.mats.filter(function (m) {
      return m.mid === mid;
    })[0];
    var info = entry ? entry.mat.retake || {} : {};
    var cuts = (Array.isArray(info.cuts) ? info.cuts : []).filter(function (c) {
      return c && Number(c.out) > Number(c.in);
    }).slice().sort(function (x, y) {
      return Number(x.in) - Number(y.in);
    });
    var sel = editFixSelMat(ctx);
    var clip = editSelected(ctx);
    var part = sel && clip && String(sel.mid || "") === mid ? editFixClipPart(clip, sel.duration, (sel.retake || {}).trimTo) : null;
    if (!cuts.length && part) cuts = [{ cutId: "", no: null, in: part.in, out: part.out, clip: true }];
    pick.cuts = cuts;
    pick.picked = cuts.map(function (c) {
      return !!part && Math.min(Number(c.out), part.out) - Math.max(Number(c.in), part.in) > 0.05;
    });
    var d = pick.dom;
    Object.keys(d.radios).forEach(function (key) {
      d.radios[key].checked = key === mid;
    });
    clear(d.cutList);
    d.boxes = cuts.map(function (c, i) {
      var label = el("label", "vnew-edit-pick-item");
      var input = el("input", null);
      input.type = "checkbox";
      input.value = String(c.cutId || i);
      input.checked = !!pick.picked[i];
      input.addEventListener("change", function () {
        pick.picked[i] = !!input.checked;
        editFixPickPaintNote(pick);
      });
      var secs = c.clip ? editFixSecs(c.in) + "–" + editFixSecs(c.out) + "초" : editFixSecs(Number(c.out) - Number(c.in)) + "초";
      label.appendChild(input);
      label.appendChild(el("span", null, editFixPickCutName(c, i) + " · " + secs));
      d.cutList.appendChild(label);
      return input;
    });
    d.all.disabled = !cuts.length;
    d.none.disabled = !cuts.length;
    d.go.disabled = !entry || !entry.can;
    editFixPickPaintNote(pick);
  }

  function editFixPickPaintNote(pick) {
    pick.dom.note.textContent = editFixPickNote(pick.cuts, pick.picked);
  }

  /* ================================================================ 모델 에러 로그(작업 199)

     GET /model-notes?gen=&variant=(작업 198) 를 ⑤ 영상 결과 상태 띠와 ⑦ 편집 머리의 [모델 에러 로그 보기] 로 보인다.
     건수(전체 그림체)는 단계를 열 때 한 번, 영상 생성이 끝날 때(⑤ running → 아님)·AI로 고치기 버전이 끝날 때(⑦) 한 번 더 받는다.
     폴링하지 않는다. 404(기능 꺼짐)면 버튼을 숨긴다. 노트 글은 모두 textContent 로만 넣는다. */

  var MNOTES_BUTTON = "모델 에러 로그 보기";
  var MNOTES_TITLE = "모델 에러 로그";
  var MNOTES_EMPTY = "기록된 모델 오류가 없습니다.";
  var MNOTES_RESTORED = "예전 기록에서 복원 — 시도 번호·원문이 없을 수 있음";
  var MNOTES_RAW_SHOW = "모델 원문 보기";
  var MNOTES_RAW_HIDE = "모델 원문 숨기기";
  var MNOTES_FILTERS = [["all", "전체"], ["webtoon", "애니"], ["real", "실사"]];
  var MNOTES_KINDS = ["contentFlag", "promptSize", "timeout", "badRequest", "provider", "qc", "other"];
  var MNOTES_KIND_LABEL = {
    contentFlag: "콘텐츠 검사",
    promptSize: "길이 한도",
    timeout: "시간 초과",
    badRequest: "요청 오류",
    provider: "모델 오류",
    qc: "품질 검사",
    other: "기타"
  };
  var MNOTES_ACTION_LABEL = {
    softRetry: "대사 순화 후 다시 시도",
    dropRefs: "참고 그림 빼고 다시 시도",
    dialogueRewrite: "대사 다시 쓰기",
    gaveUp: "포기",
    retry: "다시 시도"
  };
  var MNOTES_STAGE_LABEL = { still: "시작 그림", opening: "시작 장면", costume: "장면 옷 편집" };

  // 건수 캐시(프로젝트·gen 하나): key "pid|genId", count, off(404), running(⑤ 마지막으로 본 진행 중), seq(늦게 온 응답 버리기),
  // btns(지금 그린 버튼 — "gen"·"edit" 자리마다 하나), close(열린 창 닫기, 없으면 null).
  var mnotes = { key: "", count: 0, off: false, running: false, seq: 0, btns: {}, close: null };

  function mnotesGenId() {
    return String(((state.plan || {}).gen || {}).genId || "");
  }

  function mnotesPath(pid, genId, variant) {
    return "/api/vnew/p/" + encodeURIComponent(pid) + "/model-notes?variant=" + encodeURIComponent(variant || "all") +
      (genId ? "&gen=" + encodeURIComponent(genId) : "");
  }

  /* 프로젝트·gen 이 바뀌었으면 캐시를 비운다. 지금 key. */
  function mnotesUseKey(pid) {
    var key = String(pid || "") + "|" + mnotesGenId();
    if (mnotes.key !== key) {
      mnotes.key = key;
      mnotes.count = 0;
      mnotes.off = false;
      mnotes.running = false;
    }
    return key;
  }

  /* 버튼 글·숨김: 노트가 있으면 "(n)", 404 면 숨김. */
  function mnotesPaint(btn) {
    if (!btn) return;
    btn.style.display = mnotes.off ? "none" : "";
    btn.textContent = mnotes.count > 0 ? MNOTES_BUTTON + " (" + mnotes.count + ")" : MNOTES_BUTTON;
  }

  function mnotesPaintAll() {
    Object.keys(mnotes.btns).forEach(function (place) {
      mnotesPaint(mnotes.btns[place]);
    });
  }

  /* 받은 응답(전체 그림체)으로 건수를 맞춘다. 404 면 꺼짐. */
  function mnotesAbsorb(key, view, err) {
    if (mnotes.key !== key) return;
    if (err) {
      if (err.status === 404) mnotes.off = true;
    } else {
      mnotes.off = false;
      mnotes.count = Array.isArray((view || {}).notes) ? view.notes.length : 0;
    }
    mnotesPaintAll();
  }

  /* 건수 한 번 받기(variant all). 실패(404 말고)는 조용히 — 버튼은 건수 없이 그대로. */
  function mnotesRefresh(pid) {
    var key = mnotesUseKey(pid);
    mnotes.seq += 1;
    var seq = mnotes.seq;
    api(mnotesPath(pid, mnotesGenId(), "all"))
      .then(function (view) {
        if (seq === mnotes.seq) mnotesAbsorb(key, view, null);
      })
      .catch(function (err) {
        if (seq === mnotes.seq) mnotesAbsorb(key, null, err || {});
      });
  }

  /* ⑤ 를 그릴 때: 단계를 처음 열었거나(fresh) gen 이 바뀌었거나 생성이 막 끝났으면(running → 아님) 건수를 다시 받는다. */
  function mnotesGenWatch(fresh, running) {
    var before = mnotes.key;
    var key = mnotesUseKey(state.projectId);
    var finished = before === key && mnotes.running && !running;
    var changed = before !== key;
    mnotes.running = !!running;
    if (fresh || changed || finished) mnotesRefresh(state.projectId);
  }

  /* [모델 에러 로그 보기] 버튼(place "gen"|"edit"). opts {pid, variant(창 처음 거르기 — 값 또는 함수), onPick(note) → 처리했으면 true,
     pickable(note)}. */
  function mnotesButton(place, opts) {
    var btn = el("button", "outline-button vnew-mnotes-btn", MNOTES_BUTTON);
    btn.type = "button";
    btn.title = "영상·그림 모델이 거절하거나 실패한 기록을 봅니다";
    btn.addEventListener("click", function () {
      mnotesOpen(opts || {});
    });
    mnotes.btns[place] = btn;
    mnotesPaint(btn);
    return btn;
  }

  function mnotesPad(n) {
    return (n < 10 ? "0" : "") + n;
  }

  /* 노트 시각(UTC ISO) → 이 컴퓨터 시각 "MM-DD HH:mm". 읽지 못하면 받은 글 그대로. */
  function mnotesTime(at) {
    var date = new Date(String(at || ""));
    if (!at || isNaN(date.getTime())) return String(at || "");
    return mnotesPad(date.getMonth() + 1) + "-" + mnotesPad(date.getDate()) + " " + mnotesPad(date.getHours()) + ":" +
      mnotesPad(date.getMinutes());
  }

  /* 모델 짧은 이름: 계획의 모델 목록(plan.models label)에 있으면 그 이름, 없으면 endpoint id. */
  function mnotesModelLabel(model) {
    var id = String(model || "");
    if (!id || id === "-") return "모델 모름";
    var models = (state.plan || {}).models || [];
    for (var i = 0; i < models.length; i++) {
      var m = models[i] || {};
      if ((m.id === id || m.id === "fal:" + id || m.endpointId === id) && m.label) return String(m.label);
    }
    return id;
  }

  /* 콘티 컷 id → 번호(plan.conti.cuts 의 정수 no). 모르면 NaN. */
  function mnotesCutNo(cutId) {
    var cuts = ((state.plan || {}).conti || {}).cuts || [];
    for (var i = 0; i < cuts.length; i++) {
      var c = cuts[i] || {};
      if (String(c.id) === String(cutId) && typeof c.no === "number" && isFinite(c.no)) return c.no;
    }
    return NaN;
  }

  /* "컷 8" · "컷 8–9"(가장 작은–가장 큰 번호). 번호를 하나도 모르면 "컷 n개", 컷이 없으면 "". */
  function mnotesCutText(cutIds) {
    var ids = Array.isArray(cutIds) ? cutIds : [];
    if (!ids.length) return "";
    var nos = ids.map(mnotesCutNo).filter(function (n) {
      return isFinite(n);
    });
    if (!nos.length) return "컷 " + ids.length + "개";
    var lo = Math.min.apply(null, nos);
    var hi = Math.max.apply(null, nos);
    return lo === hi ? "컷 " + lo : "컷 " + lo + "–" + hi;
  }

  /* takeId → "테이크 n"(⑤ gen.units 차례, 없으면 id 의 숫자, 그것도 없으면 id). */
  function mnotesTakeText(takeId) {
    var id = String(takeId || "");
    var units = ((state.plan || {}).gen || {}).units || [];
    for (var i = 0; i < units.length; i++) {
      if (String((units[i] || {}).id || "") === id) return "테이크 " + (i + 1);
    }
    var m = /^take(\d+)$/.exec(id);
    return "테이크 " + (m ? Number(m[1]) : id);
  }

  /* 어디서: "테이크 2 · 조각 1 · 컷 8–9" · "AI로 고치기 · 컷 8 · 버전 6" · "시작 그림 · 컷 3". */
  function mnotesWhere(note) {
    var t = (note && note.target) || {};
    var stage = String((note && note.stage) || "");
    var parts = [];
    var cuts = mnotesCutText(t.cutIds);
    if (stage === "retake" || t.retakeCell) {
      parts.push("AI로 고치기");
      if (cuts) parts.push(cuts);
      if (t.version) parts.push("버전 " + t.version);
      return parts.join(" · ");
    }
    if (MNOTES_STAGE_LABEL[stage]) {
      parts.push(MNOTES_STAGE_LABEL[stage]);
    } else {
      if (t.takeId) parts.push(mnotesTakeText(t.takeId));
      if (t.partNo) parts.push("조각 " + t.partNo);
    }
    if (cuts) parts.push(cuts);
    return parts.length ? parts.join(" · ") : "영상";
  }

  /* 그 뒤에 한 일(action) 한국어. 모르면 "". */
  function mnotesAction(action) {
    var text = String(action || "");
    if (text.indexOf("fallbackModel:") === 0) return "다른 모델로 대체 (" + mnotesModelLabel(text.slice(14)) + ")";
    return MNOTES_ACTION_LABEL[text] || "";
  }

  function mnotesKindLabel(kind) {
    return MNOTES_KIND_LABEL[kind] || MNOTES_KIND_LABEL.other;
  }

  /* 맨 위 한 줄: "Wan 3.0 2건 · Seedance 2.5 1건 | 콘텐츠 검사 2 · 길이 한도 1"(많은 차례, 같으면 받은 차례 · 종류 차례).
     모르는 kind 는 기타에 더한다. 노트가 없으면 "". */
  function mnotesSummary(counts) {
    var byModel = (counts && counts.byModel) || {};
    var rawKind = (counts && counts.byKind) || {};
    var byKind = {};
    Object.keys(rawKind).forEach(function (k) {
      var key = MNOTES_KIND_LABEL[k] ? k : "other";
      byKind[key] = (byKind[key] || 0) + (Number(rawKind[k]) || 0);
    });
    function order(obj, keys) {
      return keys.filter(function (k) {
        return Number(obj[k]) > 0;
      }).sort(function (a, b) {
        return Number(obj[b]) - Number(obj[a]) || keys.indexOf(a) - keys.indexOf(b);
      });
    }
    var models = order(byModel, Object.keys(byModel)).map(function (k) {
      return mnotesModelLabel(k) + " " + Number(byModel[k]) + "건";
    });
    var kinds = order(byKind, MNOTES_KINDS).map(function (k) {
      return mnotesKindLabel(k) + " " + Number(byKind[k]);
    });
    if (!models.length && !kinds.length) return "";
    return models.join(" · ") + " | " + kinds.join(" · ");
  }

  /* 노트 한 줄. pick(note) 이 있으면 줄을 눌러 그 컷으로 간다(⑦). */
  function mnotesRow(note, pick) {
    var row = el("div", "vnew-mnotes-row");
    var meta = el("div", "vnew-mnotes-meta");
    meta.appendChild(el("span", "vnew-mnotes-time", mnotesTime(note.at)));
    meta.appendChild(el("span", "vnew-mnotes-where", mnotesWhere(note)));
    if (note.variant === "webtoon" || note.variant === "real") {
      meta.appendChild(el("span", "vnew-mnotes-variant", note.variant === "real" ? "실사" : "애니"));
    }
    meta.appendChild(el("span", "vnew-mnotes-model", mnotesModelLabel(note.model)));
    if (Number(note.attempt) > 0) meta.appendChild(el("span", "vnew-mnotes-attempt", Number(note.attempt) + "번째 시도"));
    var kind = MNOTES_KIND_LABEL[note.kind] ? note.kind : "other";
    meta.appendChild(el("span", "vnew-badge vnew-mnotes-kind is-" + kind, mnotesKindLabel(kind)));
    if (note.resolved === true) meta.appendChild(el("span", "vnew-badge vnew-mnotes-resolved", "해결됨"));
    row.appendChild(meta);
    row.appendChild(el("p", "vnew-mnotes-reason", String(note.reasonKo || "")));
    var action = mnotesAction(note.action);
    if (action) row.appendChild(el("p", "vnew-mnotes-action", action));
    if (note.source === "state") row.appendChild(el("p", "vnew-hint vnew-mnotes-restored", MNOTES_RESTORED));
    var raw = String(note.providerMessage || "");
    if (raw) {
      var toggle = el("button", "text-action vnew-mnotes-raw-toggle", MNOTES_RAW_SHOW);
      toggle.type = "button";
      toggle.setAttribute("aria-expanded", "false");
      var pre = el("pre", "vnew-mnotes-raw", raw);
      pre.style.display = "none";
      toggle.addEventListener("click", function (event) {
        if (event && event.stopPropagation) event.stopPropagation();
        var open = pre.style.display === "none";
        pre.style.display = open ? "" : "none";
        toggle.textContent = open ? MNOTES_RAW_HIDE : MNOTES_RAW_SHOW;
        toggle.setAttribute("aria-expanded", open ? "true" : "false");
      });
      row.appendChild(toggle);
      row.appendChild(pre);
    }
    if (pick) {
      row.classList.add("is-pickable");
      row.title = "타임라인에서 이 컷으로 갑니다";
      row.addEventListener("click", function () {
        pick(note);
      });
    }
    return row;
  }

  function mnotesClose() {
    if (mnotes.close) mnotes.close();
  }

  /* 창 열기(기존 vnew-modal 꼴). 거르기 칩 전체·애니·실사(처음 = opts.variant 또는 전체), 고를 때마다 그 variant 로 다시 받는다. */
  function mnotesOpen(opts) {
    mnotesClose();
    var pid = opts.pid || state.projectId;
    var genId = mnotesGenId();
    var key = mnotesUseKey(pid);
    var first = typeof opts.variant === "function" ? opts.variant() : opts.variant;
    var variant = first === "webtoon" || first === "real" ? first : "all";
    var seq = 0;
    var scrim = el("div", "vnew-modal-scrim");
    var modal = el("div", "vnew-modal vnew-mnotes-modal");
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-label", MNOTES_TITLE);
    var head = el("div", "vnew-modal-head");
    head.appendChild(el("h3", null, MNOTES_TITLE));
    var close = el("button", "icon-button", "×");
    close.type = "button";
    close.title = "닫기 (Esc)";
    close.setAttribute("aria-label", close.title);
    close.addEventListener("click", closeModal);
    head.appendChild(close);
    modal.appendChild(head);
    var body = el("div", "vnew-modal-body vnew-mnotes-body");
    var chips = el("div", "vnew-mnotes-chips");
    var chipEls = {};
    MNOTES_FILTERS.forEach(function (f) {
      var chip = el("button", "vnew-mnotes-chip", f[1]);
      chip.type = "button";
      chip.addEventListener("click", function () {
        if (variant === f[0]) return;
        variant = f[0];
        load();
      });
      chipEls[f[0]] = chip;
      chips.appendChild(chip);
    });
    body.appendChild(chips);
    var summary = el("p", "vnew-hint vnew-mnotes-summary", "");
    body.appendChild(summary);
    var error = planError("");
    body.appendChild(error);
    var list = el("div", "vnew-mnotes-list");
    body.appendChild(list);
    modal.appendChild(body);
    scrim.appendChild(modal);
    scrim.addEventListener("click", function (event) {
      if (event.target === scrim) closeModal();
    });
    function onKey(event) {
      if (event && event.key === "Escape") closeModal();
    }
    document.addEventListener("keydown", onKey);
    document.body.appendChild(scrim);
    mnotes.close = closeModal;

    function closeModal() {
      seq += 1;
      document.removeEventListener("keydown", onKey);
      if (scrim.parentNode) scrim.parentNode.removeChild(scrim);
      if (mnotes.close === closeModal) mnotes.close = null;
    }

    function paintChips() {
      Object.keys(chipEls).forEach(function (k) {
        chipEls[k].classList[k === variant ? "add" : "remove"]("is-on");
        chipEls[k].setAttribute("aria-pressed", k === variant ? "true" : "false");
      });
    }

    function load() {
      seq += 1;
      var mine = seq;
      paintChips();
      error.style.display = "none";
      error.textContent = "";
      summary.textContent = "";
      clear(list);
      list.appendChild(el("p", "vnew-boot", "불러오는 중…"));
      api(mnotesPath(pid, genId, variant))
        .then(function (view) {
          if (mine !== seq) return;
          if (variant === "all") mnotesAbsorb(key, view, null);
          var notes = Array.isArray((view || {}).notes) ? view.notes : [];
          clear(list);
          summary.textContent = mnotesSummary((view || {}).counts);
          if (!notes.length) {
            list.appendChild(el("p", "vnew-hint vnew-mnotes-empty", MNOTES_EMPTY));
            return;
          }
          notes.forEach(function (note) {
            if (!note || typeof note !== "object") return;
            var can = opts.onPick && (!opts.pickable || opts.pickable(note));
            list.appendChild(mnotesRow(note, can ? function (n) {
              if (opts.onPick(n)) closeModal();
            } : null));
          });
        })
        .catch(function (err) {
          if (mine !== seq) return;
          clear(list);
          if (err && err.status === 404) {
            mnotesAbsorb(key, null, err);
            closeModal();
            return;
          }
          error.style.display = "";
          error.textContent = "모델 에러 로그를 불러오지 못했습니다: " + errMessage(err);
        });
    }

    load();
  }

  /* ⑦ 노트의 컷이 지금 타임라인에 있으면 그 클립 번호(라벨 "컷 n" — edit.py _default_clips 와 같은 번호), 없으면 -1. */
  function editMnotesClip(ctx, note) {
    var ids = (((note || {}).target || {}).cutIds) || [];
    for (var i = 0; i < ids.length; i++) {
      var no = mnotesCutNo(ids[i]);
      if (!isFinite(no)) continue;
      for (var j = 0; j < (ctx.clips || []).length; j++) {
        if (String((ctx.clips[j] || {}).label || "") === "컷 " + no) return j;
      }
    }
    return -1;
  }

  /* ⑦ 머리 버튼: 처음 거르기 = 지금 그림체 탭, 줄을 누르면 그 컷을 고르고 그 시작으로 간다. */
  function editMnotesButton(ctx) {
    return mnotesButton("edit", {
      pid: ctx.pid,
      variant: function () {
        return ctx.variant;
      },
      pickable: function (note) {
        return editMnotesClip(ctx, note) >= 0;
      },
      onPick: function (note) {
        var index = editMnotesClip(ctx, note);
        if (!editAlive(ctx) || index < 0) return false;
        editSelect(ctx, index);
        editSeekClip(ctx, index);
        return true;
      }
    });
  }

  /* ---------------------------------------------------------------- 전역 */

  window.vnew = {
    mount: function (projectId) {
      state.mounted = true;
      state.projectId = projectId || "";
      state.plan = null;
      state.planStep = 0; // loadPlan 뒤 decidePlanStep 가 정한다
      state.drawnStep = 0; // 작업 199: 처음 그리는 ⑤ 는 새로 연 것
      stopPlanPolling();
      loadPlan(true);
    },
    mountCharacters: function () {
      state.mounted = true;
      state.view = "list";
      state.charId = "";
      state.detail = null;
      stopPolling();
      revokeAllGlb();
      renderList(byId(CHARACTERS_ROOT_ID));
    },
    unmount: function () {
      editLeave(); // ⑦ 편집: 남은 저장을 보내고 재생·타이머·키를 멈춘다(state.mounted 를 내리기 전에)
      state.mounted = false;
      stopPolling();
      stopPlanPolling();
      stopProgressTicker();
      if (state.orderSaveTimer) {
        window.clearTimeout(state.orderSaveTimer);
        state.orderSaveTimer = 0;
      }
      revokeAllGlb();
      destroyPreviz();
      previzRecording = null;
      state.view = "list";
      state.charId = "";
      state.detail = null;
      state.plan = null;
      clear(byId(ROOT_ID));
      clear(byId(CHARACTERS_ROOT_ID));
    },
    // 다음 단계(영상 기능)에서 ready 인 캐릭터를 고르는 데 쓴다.
    // Promise<[{id,name,status,...}]> — status === "ready" 인 캐릭터만.
    listCharacters: function () {
      return api("/api/vnew/characters")
        .then(function (payload) {
          var rows = (payload && payload.characters) || [];
          return rows.filter(function (row) {
            return row && row.status === "ready";
          });
        })
        .catch(function () {
          return [];
        });
    }
  };
})();
