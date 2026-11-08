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
    var genRunning = gen.status === "running";
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
    if (isTake) {
      var takeSecs = 0;
      (est.takes || []).forEach(function (t) { takeSecs += Number(t.seconds) || 0; });
      takeSecs = Math.round(takeSecs);
      var takeLead = el("p", null, "통 테이크(베타): 승인한 프리비즈 녹화를 통째로 넣어 ");
      takeLead.appendChild(el("span", null, GEN_WEBTOON_TITLE + "로 " + takeSecs + "초를 한 번에 만듭니다 (480p 초안)."));
      bodyWrap.appendChild(takeLead);
      bodyWrap.appendChild(
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
    if (opts.hadResult) {
      bodyWrap.appendChild(
        el("p", "vnew-hint vnew-remake-note", "지금 결과는 '이전 영상'으로만 남고 새로 만듭니다.")
      );
    }
    // 금액 숫자는 보이지 않는다(설계자 2026-10-20) — 문구만. 작업 123: 통 테이크는 고른 것의 합을 숫자로 보인다.
    var cost = el("p", "vnew-gen-cost", styles ? genStylesCostText(est, styles.real()) : "예상 비용이 듭니다.");
    bodyWrap.appendChild(cost);
    function updateCost() {
      if (styles) cost.textContent = genStylesCostText(est, styles.real());
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
      var confirmUsd = Number((gen.estimate || {}).totalUsd) || 0;
      var genBody = { confirmUsd: confirmUsd };
      if (styles) {
        var withReal = styles.real();
        genBody.styles = withReal ? ["webtoon", "real"] : ["webtoon"];
        if (withReal) genBody.confirmUsd = takeUsdSum(est.totalUsd, est.realUsd);
      }
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
          okBtn.disabled = false;
          cancelBtn.disabled = false;
          var isMissing = err && err.type === "CharacterRealMissing";
          var msg = errMessage(err);
          if (isMissing) {
            msg = msg + " 캐릭터 만들기에서 실사 버전을 완성해 주세요.";
          }
          showModalError(msg, isMissing);
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
    ["cover_face", "얼굴 가리기"]
  ];

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

    // 타임라인(컷 경계 눈금·컷 번호·대사 말풍선)
    var flat = flattenCuts(scenes);
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

    // --- 우측 편집 패널: 현재 컷의 카메라·인물 ---
    var isEditingPanel = false;

    function renderCutPanel() {
      clear(right);
      var item = flat[previzCutIndex];
      if (!item) {
        right.appendChild(el("p", "vnew-hint", "컷을 고르세요."));
        return;
      }
      var scene = item.scene;
      var cut = item.cut;
      right.appendChild(el("h3", "vnew-previz-panel-title", "컷 " + (previzCutIndex + 1) + " 편집"));

      // 웹툰 패널 썸네일 + 시점 일치도 배지 + 패널 분석(읽기 전용)
      right.appendChild(previzPanelMatchCard(cut, previz));

      // 카메라
      var camCard = el("div", "vnew-previz-edit-card");
      camCard.appendChild(el("strong", "vnew-previz-edit-head", "카메라"));
      var cam = cut.camera || (cut.camera = { pos: [0, 1.5, 4], target: [0, 1.2, 0], fov: 50 });
      camCard.appendChild(vec3Field("위치 (x,y,z)", cam, "pos", scene, cut));
      camCard.appendChild(vec3Field("바라보는 곳 (x,y,z)", cam, "target", scene, cut));
      camCard.appendChild(numField("화각(fov)", cam, "fov", 20, 90, scene, cut));
      right.appendChild(camCard);

      // 인물 목록(색 칩·이름·pose·위치)
      var actorsById = {};
      (scene.actors || []).forEach(function (a) {
        actorsById[a.id] = a;
      });
      (cut.actors || []).forEach(function (ca) {
        var meta = actorsById[ca.id] || {};
        var card = el("div", "vnew-previz-edit-card");
        var headRow = el("div", "vnew-previz-actor-head");
        var chip = el("i", "vnew-previz-dot");
        chip.style.background = meta.color || "#ffffff";
        headRow.appendChild(chip);
        headRow.appendChild(el("strong", "vnew-previz-edit-head", meta.name || ca.id));
        card.appendChild(headRow);
        card.appendChild(selectField("자세", ca, "pose", POSE_LABELS, scene, cut));
        card.appendChild(selectField("동작", ca, "gesture", GESTURE_LABELS, scene, cut));
        card.appendChild(numField("방향(도)", ca, "facingDeg", -360, 360, scene, cut));
        card.appendChild(vec3Field("위치 (x,y,z)", ca, "pos", scene, cut));
        var speakWrap = el("label", "vnew-previz-check");
        var speak = el("input");
        speak.type = "checkbox";
        speak.checked = !!ca.speaking;
        speak.addEventListener("change", function () {
          ca.speaking = speak.checked;
          applyEdit(scene, cut);
        });
        speakWrap.appendChild(speak);
        speakWrap.appendChild(el("span", null, "말하는 중(머리 끄덕임)"));
        card.appendChild(speakWrap);
        right.appendChild(card);
      });

      // 저장 버튼
      var saveBtn = el("button", "primary-button vnew-previz-save", "저장");
      saveBtn.type = "button";
      saveBtn.addEventListener("click", function () {
        saveBtn.disabled = true;
        api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/previz/scene", {
          method: "PUT",
          body: JSON.stringify({ scenes: scenes })
        })
          .then(function (plan) {
            state.plan = plan;
            loadPlan(false);
          })
          .catch(function (err) {
            saveBtn.disabled = false;
            showError("저장하지 못했습니다: " + errMessage(err));
          });
      });
      right.appendChild(saveBtn);
    }

    // 편집값을 즉시 뷰포트에 반영: 컨트롤러를 그 컷 시작 시각으로 다시 그린다.
    function applyEdit(scene, cut) {
      isEditingPanel = true;
      if (previzCtl) {
        previzCtl.pause();
        previzCtl.seek(Number(cut.start) || 0);
      }
      window.setTimeout(function () {
        isEditingPanel = false;
      }, 50);
    }

    // 숫자 벡터 3칸 입력
    function vec3Field(label, obj, key, scene, cut) {
      var wrap = el("label", "vnew-field vnew-previz-vec");
      wrap.appendChild(el("span", "vnew-field-label", label));
      var row = el("div", "vnew-previz-vec-row");
      var arr = Array.isArray(obj[key]) ? obj[key] : (obj[key] = [0, 0, 0]);
      [0, 1, 2].forEach(function (i) {
        var input = el("input", "vnew-field-input");
        input.type = "number";
        input.step = "0.1";
        input.value = String(Number(arr[i]) || 0);
        input.addEventListener("input", function () {
          arr[i] = Number(input.value) || 0;
          applyEdit(scene, cut);
        });
        row.appendChild(input);
      });
      wrap.appendChild(row);
      return wrap;
    }

    function numField(label, obj, key, min, max, scene, cut) {
      var wrap = el("label", "vnew-field");
      wrap.appendChild(el("span", "vnew-field-label", label));
      var input = el("input", "vnew-field-input");
      input.type = "number";
      if (min !== undefined) input.min = String(min);
      if (max !== undefined) input.max = String(max);
      input.value = String(Number(obj[key]) || 0);
      input.addEventListener("input", function () {
        obj[key] = Number(input.value) || 0;
        applyEdit(scene, cut);
      });
      wrap.appendChild(input);
      return wrap;
    }

    function selectField(label, obj, key, options, scene, cut) {
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
    function doApprove(force) {
      btn.disabled = true;
      var body = force ? JSON.stringify({ force: true }) : JSON.stringify({});
      api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/previz/approve", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body
      })
        .then(function (plan) {
          state.plan = plan;
          var segs = (((state.plan || {}).previz || {}).segments) || segments;
          return recordSegments(segs, showError);
        })
        .catch(function (err) {
          btn.disabled = false;
          if (err && err.status === 409) {
            showError(errMessage(err) || "시점 일치도가 낮은 컷이 있습니다.");
          } else {
            showError("승인하지 못했습니다: " + errMessage(err));
          }
        });
    }

    if (lowCuts.length && !alreadyApproved) {
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
    } else {
      btn.disabled = busy;
    }

    btn.addEventListener("click", function () {
      // 이미 [그래도 승인]으로 승인한 장면을 다시 녹화할 때는 같은 force 로 보낸다(아니면 409 LowMatch).
      doApprove(!!(alreadyApproved && previz.approvedForced));
    });
    host.appendChild(btn);

    // 모두 녹화 완료 안내 + 다음 단계로
    if (previz.recordedAll) {
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
  function renderGenStart(host, showError, staleGen) {
    var plan = state.plan || {};
    var previz = plan.previz || {};

    if (staleGen) {
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
      var startBtn = el("button", "primary-button", "영상 만들기");
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

  /* 실사 열 상태: none(회차 없음) · running(만드는 중·이어 붙이는 중) · done · failed · cancelled. */
  function takeRealState(gen) {
    var g = gen || {};
    if (!g.realRun) return "none";
    var final = g.finalReal || {};
    if (final.status === "done") return "done";
    if (final.status === "failed") return "failed";
    var counts = takeRealCounts(g);
    if (g.status === "running" && (counts.active > 0 || counts.completed === counts.total)) return "running";
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
      el("p", "vnew-hint", "다음 업데이트에서 목소리·효과음·배경음을 넣을 수 있습니다.")
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
    players.appendChild(sourceBox);
    ctx.resultBox = el("div", "vnew-sound-pbox");
    players.appendChild(ctx.resultBox);
    ctx.root.appendChild(players);
    ctx.versionSig = "";
    soundRenderResult(ctx);
    if (!source.hasAudio) ctx.root.appendChild(el("p", "vnew-hint vnew-sound-noaudio", SOUND_NO_AUDIO));

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
      return [v.n, v.currentSource];
    })]);
    if (sig === ctx.versionSig) return;
    ctx.versionSig = sig;
    clear(box);
    box.appendChild(el("h3", "vnew-sound-ptitle", "반영한 소리"));
    if (!versions.length) {
      box.appendChild(el("p", "vnew-hint", "아직 반영한 소리가 없습니다. 아래에서 고친 뒤 [소리에 반영]을 누르세요."));
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
      var label = "v" + v.n + " · 속마음 " + (v.thoughtIds || []).length + "줄" + (v.currentSource ? "" : " · 이전 원본");
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
    if (chosen.videoUrl) tools.appendChild(downloadLink(chosen.videoUrl, "반영본 내려받기"));
    box.appendChild(tools);
  }

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
    row.appendChild(el("span", "vnew-sound-cell vnew-sound-line", line.lineKo || ""));
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
    if (ctx.timer) window.clearTimeout(ctx.timer);
    ctx.timer = 0;
    if (((ctx.view || {}).job || {}).status !== "running") return;
    ctx.timer = window.setTimeout(function () {
      ctx.timer = 0;
      if (!soundAlive(ctx)) return;
      api(soundPath() + "?variant=" + encodeURIComponent(ctx.variant))
        .then(function (view) {
          if (!soundAlive(ctx)) return;
          ctx.view.job = (view || {}).job || {};
          ctx.view.versions = (view || {}).versions || [];
          if (ctx.view.job.status !== "running") ctx.pickN = null; // 끝나면 최신 버전을 보인다
          soundRenderResult(ctx);
          soundRenderJob(ctx);
          soundSchedule(ctx);
        })
        .catch(function () {
          if (soundAlive(ctx)) soundSchedule(ctx);
        });
    }, SOUND_POLL_MS);
  }

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
    return "아직 내보낸 영상이 없습니다. [내보내기]를 누르면 지금 편집대로 mp4 하나를 만듭니다.";
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
  }

  /* ⑦ 을 떠날 때(단계 이동·unmount·다시 그리기): 재생·타이머·키를 멈추고, 남은 저장이 있으면 한 번 보낸다(design §5.5). */
  function editLeave() {
    var ctx = editCtx;
    if (!ctx) return;
    editCtx = null;
    ctx.alive = false;
    editTrClose(ctx); // 작업 182: 열린 전환 메뉴
    ctx.tighten = null; // 작업 186: 빈틈 다듬기 제안(받는 중이면 응답을 버린다)
    editFixStop(ctx); // 지시서 116: AI로 고치기 패널 조회 타이머·영상
    editPause(ctx);
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
    ctx.cutPlay = null; // 작업 190: 작업 188 고른 컷 재생 표시도 새 문서에서는 없다
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
    d.exportBtn = el("button", "primary-button vnew-edit-export-btn", "내보내기");
    d.exportBtn.type = "button";
    d.exportBtn.title = "지금 편집대로 mp4 하나를 만듭니다(저장이 남았으면 먼저 저장)";
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
    d.scroller.appendChild(d.content);
    tl.appendChild(d.scroller);
    root.appendChild(tl);
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
    var clip = editSelected(ctx);
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
    var clip = editSelected(ctx);
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
    var clip = editSelected(ctx);
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
    var clip = editSelected(ctx);
    var parts = clip ? ctx.clipEls[ctx.sel] : null;
    if (!parts || parts.index !== ctx.sel) return;
    var preview = editCloneClip(clip);
    if (value) preview.audioSlip = value;
    else delete preview.audioSlip;
    editPaintClip(ctx, parts, preview);
  }

  /* 고른 클립의 대사 타이밍을 바꾼다(0 이면 키를 지운다 — 저장 바디에 없다). 실행 취소 한 단계. 소리 끈 클립은 받지 않는다. */
  function editApplySlip(ctx, raw) {
    var clip = editSelected(ctx);
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
      var clip = editSelected(ctx);
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
    d.btnUndo.disabled = !ctx.hist.undo.length;
    d.btnRedo.disabled = !ctx.hist.redo.length;
    d.btnDelete.disabled = !has;
    d.btnMute.disabled = !has;
    d.btnSplit.disabled = !ctx.clips.length;
    d.btnReset.disabled = !(ctx.defaults[ctx.variant] || []).length;
    d.btnMute._label.textContent = muted ? "소리 켜기" : "소리 끄기";
    editToolTitle(d.btnMute, muted ? "고른 클립 소리 켜기" : "고른 클립 소리 끄기", "M");
    d.exportBtn.disabled = ctx.exporting || !ctx.clips.length;
    editRenderRetime(ctx, false);
    editRenderAudioFx(ctx); // 작업 182
    editRenderDialogue(ctx); // 작업 186: 대사 타이밍 · 빈틈 다듬기 버튼
    editRenderPlayButton(ctx);
    editFixRenderSel(ctx); // 지시서 116: [고른 클립 AI로 고치기] disabled·title 만(노드를 만들지 않는다)
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
    editRenderFx(ctx);
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
    return parts;
  }

  /* 클립 상자 내용을 지금 값(clip)으로 다시 칠한다(끄는 동안·필름을 받은 뒤에도 같은 요소를 고친다). */
  function editPaintClip(ctx, parts, clip) {
    var mat = ctx.materials[clip.sourceKey] || {};
    var len = editClipLen(clip);
    var width = Math.max(1, len * ctx.pps);
    var invalid = ctx.invalidIds[clip.id];
    var selected = parts.index === ctx.sel;
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
  }

  function editSelect(ctx, index) {
    ctx.sel = index >= 0 && index < ctx.clips.length ? index : -1;
    if (ctx.cutPlay && ctx.cutPlay.index !== ctx.sel) ctx.cutPlay = null; // 작업 188: 다른 클립을 고르면 이 컷 재생 표시 · 멈출 자리를 지운다
    ctx.clipEls.forEach(function (parts) {
      var method = parts.index === ctx.sel ? "add" : "remove";
      parts.box.classList[method]("is-selected");
      parts.abox.classList[method]("is-selected");
    });
    editRenderTools(ctx);
    if (editBinFollow(ctx)) editRenderBin(ctx); // 작업 149: 고른 클립의 테이크·컷을 재료 메뉴에
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
    if (!editPointerOk(ctx, event)) return;
    if (event.stopPropagation) event.stopPropagation();
    if (event.preventDefault) event.preventDefault();
    var from = parts.index;
    var drop = -1;
    editSelect(ctx, from);
    editTrack(ctx, parts.box, event, {
      threshold: EDIT_DRAG_PX,
      move: function (dx, ev) {
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
      if (ctx.sel >= 0 && ctx.sel < ctx.clips.length) editCommit(ctx, editRemove(ctx.clips, ctx.sel), { sel: -1 });
    } else if (action === "mute") {
      if (ctx.sel >= 0 && ctx.sel < ctx.clips.length) {
        var next = editCloneClips(ctx.clips);
        next[ctx.sel].muted = !next[ctx.sel].muted;
        editCommit(ctx, next, {});
      }
    } else if (action === "undo" || action === "redo") {
      editUndoRedo(ctx, action === "redo");
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
    ctx.clips = editTidyFx(clips); // 작업 182: 옮기기·삭제·자르기·늘이기 뒤 J/L 컷·디졸브 자리 규칙 정리
    if (o.sel !== undefined) ctx.sel = o.sel;
    editChanged(ctx);
  }

  function editChanged(ctx) {
    ctx.rev += 1;
    ctx.cutPlay = null; // 작업 188: 편집하면 [이 컷만] 멈출 자리(편집 전 클립으로 잰 값)를 지운다 — 재생은 보통 재생으로 잇는다
    if (ctx.tighten) editTightenClose(ctx); // 작업 186: 편집이 바뀌면 받은(받는 중인) 빈틈 다듬기 제안은 버린다
    if (ctx.sel >= ctx.clips.length) ctx.sel = -1;
    editNote(ctx, "");
    editRenderVariants(ctx);
    editRenderTimeline(ctx);
    editRenderBin(ctx);
    if (ctx.fix && editFixAlive(ctx, ctx.fix)) editFixPaintVersions(ctx, ctx.fix); // 지시서 116 보완 2: 타임라인 사용 중 표시
    editSeek(ctx, ctx.t);
    editScheduleSave(ctx);
  }

  /* 작업 190: 스택은 그림체마다 따로라(ctx.hists) 되돌려도 그림체는 바뀌지 않는다. */
  function editUndoRedo(ctx, redo) {
    var current = editSnapshotOf(ctx.variant, ctx.clips);
    var snap = redo ? editRedo(ctx.hist, current) : editUndo(ctx.hist, current);
    if (!snap) return;
    ctx.clips = editCloneClips(snap.clips);
    ctx.sel = -1;
    editChanged(ctx);
  }

  function editSplitHere(ctx) {
    var loc = editLocate(ctx.clips, ctx.t);
    if (!loc) return;
    var max = editLimit(ctx, "maxClips", 200);
    if (ctx.clips.length >= max) {
      editNote(ctx, "클립은 " + max + "개까지 둘 수 있습니다.");
      return;
    }
    var result = editSplit(ctx.clips, ctx.t, editNewId(ctx.clips), editFpsOf(ctx, loc.clip),
      editMinSource(editMinLen(ctx), editSpeedOf(loc.clip)));
    if (!result) {
      editNote(ctx, "클립 끝에 너무 가까워 자를 수 없습니다. 재생헤드를 조금 옮겨 주세요.");
      return;
    }
    editCommit(ctx, result.clips, { sel: result.index });
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
    var clip = editSelected(ctx);
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
    var clip = editSelected(ctx);
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
        editBinFixCut(ctx, take, current());
      });
      cutRow.appendChild(fixCut);
    }
    function paintFix() {
      if (!fixCut) return;
      var target = editBinFixTarget(take, current());
      fixCut.disabled = !target || ctx.busy;
      fixCut.title = target ? "고른 컷만 AI로 고칩니다(비용은 다음 창에서 보입니다)" : "이 컷은 AI로 고칠 수 없습니다";
    }
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
    editFixOpen(ctx, target.mid, same ? clip.id : "", r);
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
    api(editPath(ctx), { method: "PUT", body: JSON.stringify(editSaveBody(ctx.etag, variant, ctx.clips)) })
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
      tools.appendChild(downloadLink(ex.downloadUrl || ex.videoUrl, "내려받기 (edit-" + (ex.n || 1) + ".mp4)"));
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
  }

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
    return name + " " + (EDIT_FIX_STATUS[v.status] || v.status || "") + (Number(v.attempt) > 1 ? " (" + v.attempt + "번째 시도)" : "") + "…";
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

  /* [고른 클립 AI로 고치기]: disabled·title 만 바꾼다(editRenderTools 가 부른다 — 노드를 만들지 않는다). */
  function editFixRenderSel(ctx) {
    var btn = ctx.dom.fixSel;
    if (!btn) return;
    var mat = editFixSelMat(ctx);
    btn.disabled = !mat || ctx.busy;
    btn.title = mat ? "고른 클립(" + (mat.label || "영상") + ")을 AI로 고칩니다" : "타임라인에서 AI로 고칠 수 있는 테이크·컷 클립을 고르세요";
    btn.setAttribute("aria-label", btn.title);
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

  /* 지시서 119: range({in, out}, 원본 시간 — 고르기 창에서 고른 장면)가 있으면 그것이 "이 구간만" 범위다(없으면 clipId 의 클립 구간). */
  function editFixOpen(ctx, mid, clipId, range) {
    if (!editAlive(ctx) || ctx.busy || !mid) return;
    editPause(ctx);
    editFixPickClose(ctx);
    editFixClose(ctx);
    var fix = { mid: mid, clipId: clipId || "", view: null, timer: 0, busy: false, draftShown: "", refreshing: false, open: true, dom: {} };
    fix.model = ""; // 지시서 116 보완 1: 고른 영상 모델(보기가 오면 원래 모델·초안 모델로 채운다)·범위·고른 클립 구간(원본 일부일 때만)
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
    d.make = el("button", "primary-button vnew-edit-fix-make", "새 프롬프트 만들기");
    d.make.type = "button";
    d.make.title = "이전 프롬프트·결과 영상 프레임·수정 사항으로 AI가 새 프롬프트를 씁니다(아직 다시 만들지 않습니다)";
    d.make.addEventListener("click", function () {
      editFixPreview(ctx, fix);
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
    [d.draftStatus, d.result, d.changes, d.newField, d.meter, d.problems, d.cost, d.submit].forEach(function (node) {
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
    editFixAdopt(fix); // 지시서 116 보완 1: 범위·모델 기본값(구간을 못 받았으면 원본 칸 전체로)
    editFixPaint(ctx, fix, before);
    editFixSchedule(ctx, fix);
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
    d.make.disabled = v.canRetake !== true || fix.busy || drafting;
    d.make.textContent = drafting ? "새 프롬프트를 만드는 중…" : v.draft ? "새 프롬프트 다시 만들기" : "새 프롬프트 만들기";
    d.request.disabled = v.canRetake !== true;
    d.extra.disabled = v.canRetake !== true;
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
      }
      d.versions.appendChild(row);
    });
  }

  function editFixPaintDraft(fix) {
    var v = fix.view || {};
    var d = fix.dom;
    var dr = v.draft;
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
      var res = ranged ? editFixRangeSwap(ctx.clips, fix.clipId ? [fix.clipId] : null, versions, v.kind, v.cellSeconds, editFixDurations(ctx, versions), ver,
        editMinLen(ctx), mat.label || ver.label, labels.concat(versions.map(function (x) { return (ctx.materials[x.sourceKey] || {}).label; }))) :
        editSwapSource(ctx.clips, fix.clipId ? [fix.clipId] : null, keys, ver.sourceKey, Number(mat.duration), editMinLen(ctx),
        mat.label || ver.label, labels);
      if (!res.changed) {
        editFixNote(fix, res.outside ? "고른 클립이 " + editFixVerName(ver) + " 구간(" + editFixSecs((ver.range || {}).start) + "–" + editFixSecs((ver.range || {}).end) +
          "초) 밖이라 바꾸지 않았습니다." : res.skipped ? "이 버전 길이 안에 들어가지 않는 클립이라 바꾸지 않았습니다." :
          "타임라인에 바꿀 이 칸의 클립이 없습니다. [재료에 추가]로 넣어 주세요.");
        return;
      }
      editCommit(ctx, res.clips, {});
      editFixNote(fix, "타임라인 클립 " + res.changed + "개를 " + editFixVerTo(ver) + " 바꿨습니다." +
        (res.skipped ? " 길이가 맞지 않는 " + res.skipped + "개는 그대로 두었습니다." : "") + " 다른 버전의 [바꾸기]로 되돌릴 수 있습니다.");
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
    if (fix.scope === "range" && fix.clip) path += "?in=" + encodeURIComponent(String(fix.clip.in)) + "&out=" + encodeURIComponent(String(fix.clip.out));
    return path;
  }

  /* 보기가 올 때 범위·모델 기본값. 구간을 기다리던 중 보기에 구간이 없으면(서버가 거절) 원본 칸 전체로 돌리고 이유를 남긴다. 고른 모델을 그
     범위에서 쓸 수 없으면 원래 모델, 그것도 안 되면 쓸 수 있는 첫 모델. */
  function editFixAdopt(fix) {
    var v = fix.view || {};
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
    parent.appendChild(label);
    return label;
  }

  /* 「설정」 뼈대(한 번): 범위 라디오 두 개·영상 모델 상자·정보 줄·뺀 모델 한 줄. */
  function editFixBuildSetup(ctx, fix, parent) {
    var d = fix.dom;
    d.setup = el("div", "vnew-edit-fix-setup");
    d.scope = el("div", "vnew-edit-fix-scope");
    d.scope.setAttribute("role", "radiogroup");
    d.scope.setAttribute("aria-label", "다시 만들 범위");
    d.scopeFull = editFixScopeItem(ctx, fix, d.scope, "full", "원본 칸 전체");
    d.scopeRange = editFixScopeItem(ctx, fix, d.scope, "range", "이 구간만 다시 만들기");
    d.setup.appendChild(d.scope);
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
    var scopes = Array.isArray(v.scopes) && v.scopes.length ? v.scopes : ["full"];
    var hasRange = !!fix.clip || scopes.indexOf("range") >= 0;
    d.scope.style.display = hasRange ? "" : "none";
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

  /* ---------------------------------------------------------------- 전역 */

  window.vnew = {
    mount: function (projectId) {
      state.mounted = true;
      state.projectId = projectId || "";
      state.plan = null;
      state.planStep = 0; // loadPlan 뒤 decidePlanStep 가 정한다
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
