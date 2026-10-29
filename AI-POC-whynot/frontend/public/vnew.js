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
    ["④", "영상(실사·웹툰풍)"],
    ["⑤", "사운드(베타)"]
  ];

  // variant 표시 정보. 서버 variant 키 → 한국어 제목.
  var VARIANTS = [
    { key: "real", title: "실사" },
    { key: "webtoon", title: "웹툰 그림체" }
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
     409 등 상태 코드가 필요한 곳을 위해 status 를 error 에 실어 준다. */
  function api(path, options) {
    var init = options || {};
    init.headers = init.headers || {};
    init.headers["Authorization"] = "Bearer " + token();
    if (init.body && !(init.body instanceof FormData)) {
      init.headers["Content-Type"] = "application/json";
    }
    var httpStatus = 0;
    return fetch(path, init).then(function (response) {
      httpStatus = response.status;
      return response
        .json()
        .catch(function () {
          return null;
        })
        .then(function (payload) {
          if (response.ok) {
            return payload || {};
          }
          var err = (payload && payload.error) || { message: "서버 오류가 발생했습니다." };
          if (typeof err === "object" && err && err.status === undefined) {
            err.status = httpStatus;
          }
          throw err;
        });
    });
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
    if (!building) return;
    state.pollTimer = window.setTimeout(function () {
      // 화면을 떠났으면 멈춘다(schedulePoll/ stopPolling 가 보장).
      if (state.view !== "detail") return;
      refreshDetail(host, false);
    }, POLL_MS);
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

    if (status !== "failed") {
      // ready — 별도 액션 없음(3D 재시도는 viewer3d 가 처리).
      return row;
    }

    // 실패·중단: [이 그림체만 다시 만들기] + 추가 요청 창(재사용).
    var error = el("p", "login-error vnew-variant-rebuild-error");
    error.setAttribute("role", "alert");
    error.style.display = "none";

    var btn = el("button", "outline-action vnew-variant-rebuild-btn", "이 그림체만 다시 만들기");
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
    panel.appendChild(el("p", "vnew-rebuild-cost", "이 그림체만 새로 그립니다. 비용이 듭니다."));
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
    [
      ["frontUrl", "정면"],
      ["backUrl", "후면"]
    ].forEach(function (pair) {
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
        "기본 복장 위에 더 입히는 옷차림(예: 정장 자켓)을 따로 만듭니다. 3D 는 기본 복장 하나만 만듭니다."
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
    if (outfit.source === "auto") {
      top.appendChild(el("span", "vnew-outfit-badge", "후보"));
    }
    card.appendChild(top);
    if (outfit.promptEn) {
      card.appendChild(el("p", "vnew-outfit-prompt", outfit.promptEn));
    }

    var variants = outfit.variants || {};
    // 상태: 하나라도 images 면 만드는 중, 하나라도 ready 면 결과 있음.
    var anyBuilding = false;
    var anyReady = false;
    VARIANTS.forEach(function (meta) {
      var ov = variants[meta.key] || {};
      if (ov.status === "images" || ov.status === "queued") anyBuilding = true;
      if (ov.status === "ready") anyReady = true;
    });

    var error = el("p", "login-error vnew-outfit-error");
    error.setAttribute("role", "alert");
    error.style.display = "none";

    // 결과: 두 그림체 정면·후면 나란히
    if (anyReady) {
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
        } else if (ov.status === "images" || ov.status === "queued") {
          col.appendChild(el("p", "vnew-hint", "만드는 중…"));
        }
        grid.appendChild(col);
      });
      card.appendChild(grid);
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
     ⑥ 사운드는 gen.selected 가 있으면 열린다(설계자 2026-10-21). */
  function maxReachableStep() {
    var plan = state.plan || {};
    var conti = plan.conti || {};
    var previz = plan.previz || {};
    var gen = plan.gen || {};
    if (gen.selected) return 6;
    if (previz.recordedAll) return 5;
    if (conti.status === "ready" && (conti.cuts || []).length) return 4;
    return 3;
  }

  function planStepBar() {
    var bar = el("nav", "vnew-plan-steps");
    var maxStep = maxReachableStep();
    PLAN_STEPS.forEach(function (step, index) {
      var stepNo = index + 1;
      var item = el("button", "vnew-plan-step");
      item.type = "button";
      if (stepNo === state.planStep) item.classList.add("is-current");
      if (stepNo < state.planStep) item.classList.add("is-done");
      item.appendChild(el("span", "vnew-plan-step-mark", step[0]));
      item.appendChild(el("span", "vnew-plan-step-text", step[1]));
      // 갈 수 있는 단계까지만 활성화. 그 뒤 단계는 아직 안내만(is-soon).
      var canGo = stepNo <= maxStep;
      if (!canGo) item.classList.add("is-soon");
      item.addEventListener("click", function () {
        if (stepNo === state.planStep) return;
        if (stepNo <= maxStep) {
          state.planStep = stepNo;
          renderPlan();
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

  function loadPlan(first) {
    var host = planHost();
    if (!host) return;
    if (first) {
      clear(host);
      host.appendChild(el("p", "vnew-boot", "불러오는 중…"));
    }
    var pid = state.projectId;
    api("/api/vnew/p/" + encodeURIComponent(pid) + "/plan")
      .then(function (plan) {
        if (!state.mounted || state.projectId !== pid) return;
        state.plan = plan;
        decidePlanStep();
        renderPlan();
      })
      .catch(function (err) {
        if (!state.mounted || state.projectId !== pid) return;
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

  /* 처음 열 때(또는 task 진행 중) 알맞은 단계를 고른다. 사용자가 단계 막대로 옮긴 뒤에는
     그 선택을 존중하되, 진행 중인 작업이 있으면 그 단계를 보여 준다. */
  function decidePlanStep() {
    var plan = state.plan || {};
    var task = plan.task || {};
    var previz = plan.previz || {};
    var gen = plan.gen || {};
    // 영상 생성이 진행 중(running)이면 ⑤를 보여 준다.
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
      state.planStep = task.name === "conti" ? 3 : 2;
      return;
    }
    // 사용자가 이미 단계를 골랐으면(1~6) 그대로 둔다(단, 갈 수 없는 단계면 되돌린다).
    if (state.planStep >= 1 && state.planStep <= 6) {
      var maxStep = maxReachableStep();
      if (state.planStep > maxStep) state.planStep = maxStep;
      return;
    }
    // 처음 열 때: gen.selected 면 ⑥, gen 이 있으면 ⑤, 프리비즈가 있으면 ④, 아니면 ①.
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

  /* 진행 중(task.running 또는 gen.running)이면 다시 불러온다. 영상 생성은 이미지·제출 단계에서
     3초, 그 밖에는 5초 간격(설계자 2026-10-20). */
  function schedulePlanPoll() {
    stopPlanPolling();
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
      loadPlan(false);
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

    var head = el("div", "vnew-plan-head");
    head.appendChild(el("h1", "vnew-h1", "영상만들기_신규"));
    head.appendChild(
      el("p", "vnew-lead", "웹툰 이미지를 넣으면 대본·상황을 읽고 콘티까지 만듭니다.")
    );
    host.appendChild(head);
    host.appendChild(planStepBar());

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

    if (hasScript) {
      var contiBtn = el("button", "primary-button", "콘티 만들기");
      contiBtn.type = "button";
      contiBtn.addEventListener("click", function () {
        contiBtn.disabled = true;
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
            state.planStep = 3;
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
    if (isTake) {
      var takeSecs = 0;
      (est.takes || []).forEach(function (t) { takeSecs += Number(t.seconds) || 0; });
      takeSecs = Math.round(takeSecs);
      var takeLead = el("p", null, "통 테이크(베타): 승인한 프리비즈 녹화를 통째로 넣어 ");
      takeLead.appendChild(el("span", null, "웹툰 그림체로 " + takeSecs + "초를 한 번에 만듭니다 (480p 초안)."));
      bodyWrap.appendChild(takeLead);
      bodyWrap.appendChild(
        el("p", "vnew-hint", "테이크 " + ((est.takes || []).length) + "개로 만듭니다. 실사는 건너뜁니다 — 기본 모델로 따로 만들 수 있습니다.")
      );
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
    // 금액 숫자는 보이지 않는다(설계자 2026-10-20) — 문구만.
    bodyWrap.appendChild(el("p", "vnew-gen-cost", "예상 비용이 듭니다."));

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
      api("/api/vnew/p/" + encodeURIComponent(state.projectId) + "/generate", {
        method: "POST",
        body: JSON.stringify({ confirmUsd: confirmUsd })
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

    // 참고 웹툰 컷 썸네일
    var thumb = panelThumb((cut.panels || [])[0]);
    if (thumb && thumb.url) {
      var img = el("img", "vnew-cut-thumb");
      img.src = thumb.url;
      img.alt = "참고 컷";
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

    // 인물 위치·동작
    var who = cut.characters || [];
    if (who.length) {
      var whoWrap = el("div", "vnew-entry-who");
      who.forEach(function (person, pIndex) {
        var row = el("div", "vnew-entry-person");
        row.appendChild(el("span", "vnew-entry-person-name", person.name || "인물 " + (pIndex + 1)));
        row.appendChild(textField("위치 (영어)", person, "positionEn", false));
        row.appendChild(textField("동작 (영어)", person, "actionEn", false));
        whoWrap.appendChild(row);
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
      doApprove(false);
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
        el("p", "vnew-hint", "웹툰 그림체로 " + Math.round(takeSecs0) + "초를 한 번에 만듭니다 (480p 초안). 실사는 건너뜁니다.")
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
    VARIANTS.forEach(function (meta) {
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
    statusBar.appendChild(el("p", "vnew-gen-status-text", statusText[gen.status] || gen.status));
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
        el("p", "vnew-hint", "웹툰 그림체로 " + takeSecs + "초를 한 번에 만듭니다 (480p 초안). 실사는 건너뜁니다 — 기본 모델로 따로 만들 수 있습니다.")
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
      host.appendChild(takeTimeline(gen.units || [], showError));
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
     프리비즈 미리보기(테이크 녹화)·웹툰 결과 칸·실사 칸("건너뜀"). 보낸 프롬프트 접이식. */
  function takeTimeline(units, showError) {
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
      list.appendChild(takeRow(unit, index, showError));
    });
    section.appendChild(list);
    return section;
  }

  function takeRow(unit, index, showError) {
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
    cells.appendChild(cutCell(unit, { key: "webtoon", title: "웹툰 그림체" }, showError));
    // 실사 칸은 "건너뜀" 안내만.
    var realCell = el("div", "vnew-seg-cell");
    var realHead = el("div", "vnew-seg-cell-head");
    realHead.appendChild(el("span", "vnew-seg-cell-title", "실사"));
    realHead.appendChild(el("span", "vnew-badge-gen is-skipped", "건너뜀"));
    realCell.appendChild(realHead);
    var note = (unit.real || {}).note || "통 테이크(베타)는 웹툰만 만듭니다 — 실사는 기본 모델로 따로 만들 수 있습니다.";
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
    VARIANTS.forEach(function (variant) {
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
    var bothReady =
      (gen.finalReal || {}).status === "done" && (gen.finalWebtoon || {}).status === "done";

    VARIANTS.forEach(function (meta) {
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
        }
      } else if (final.status === "failed") {
        col.appendChild(el("p", "login-error", final.error || "이어 붙이기에 실패했습니다."));
      } else if (final.status === "skipped") {
        var skip = el("div", "vnew-final-pending");
        skip.appendChild(
          el("p", "vnew-hint", final.note || "통 테이크(베타)는 웹툰만 만듭니다 — 실사는 기본 모델로 따로 만들 수 있습니다.")
        );
        col.appendChild(skip);
      } else {
        var place = el("div", "vnew-final-pending");
        place.appendChild(el("p", "vnew-hint", "모든 구간이 끝나면 자동으로 이어 붙입니다."));
        col.appendChild(place);
      }
      section.appendChild(col);
    });
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
    VARIANTS.forEach(function (variant) {
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

  /* ================================================================ ⑤ 사운드 (베타)

     고른 영상(gen.selected) 플레이어는 고정. 대사 목록은 읽기 전용. 소리 토글은 화면 안에서만
     muted 를 바꾼다(저장 안 함). [다른 영상 고르기] → ④. */

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
    var meta = selected === "real" ? VARIANTS[0] : VARIANTS[1];
    var final = (selected === "real" ? gen.finalReal : gen.finalWebtoon) || {};

    // --- 머리글 + BETA 배지 ---
    var head = el("section", "vnew-sound-head");
    var titleWrap = el("div");
    var title = el("h2", "vnew-sec-title", "사운드");
    title.appendChild(el("span", "vnew-badge vnew-beta-badge", "BETA"));
    titleWrap.appendChild(title);
    titleWrap.appendChild(el("p", "vnew-hint", "고른 영상(" + meta.title + ")에 소리를 입히는 단계입니다."));
    head.appendChild(titleWrap);
    host.appendChild(head);

    // --- 고른 영상 플레이어(고정) + [다른 영상 고르기] ---
    var playerSec = el("section", "panel vnew-sound-player-sec");
    var player = null;
    if (final.status === "done" && final.videoUrl) {
      player = el("video", "vnew-final-player vnew-sound-player");
      player.src = final.videoUrl;
      player.controls = true;
      player.setAttribute("playsinline", "");
      playerSec.appendChild(player);
    } else {
      playerSec.appendChild(el("p", "vnew-hint", "고른 영상을 불러오지 못했습니다."));
    }

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
    playerSec.appendChild(playerTools);
    host.appendChild(playerSec);

    // --- 소리 설정(muted 토글, 저장 안 함) ---
    var soundSec = el("section", "panel vnew-sound-settings");
    soundSec.appendChild(el("h2", "vnew-sec-title", "소리 설정"));
    soundSec.appendChild(
      el("p", "vnew-hint", "지금은 이 화면에서만 바뀝니다(저장하지 않습니다).")
    );
    var toggle = el("div", "vnew-sound-toggle");
    var onBtn = el("button", "outline-button is-on", "모델 원음 유지");
    var muteBtn = el("button", "outline-button", "무음");
    onBtn.type = "button";
    muteBtn.type = "button";
    function setMuted(muted) {
      if (player) player.muted = muted;
      if (muted) {
        muteBtn.classList.add("is-on");
        onBtn.classList.remove("is-on");
      } else {
        onBtn.classList.add("is-on");
        muteBtn.classList.remove("is-on");
      }
    }
    onBtn.addEventListener("click", function () {
      setMuted(false);
    });
    muteBtn.addEventListener("click", function () {
      setMuted(true);
    });
    toggle.appendChild(onBtn);
    toggle.appendChild(muteBtn);
    soundSec.appendChild(toggle);
    host.appendChild(soundSec);

    // --- 대사 목록(읽기 전용) ---
    host.appendChild(dialogueList());
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
