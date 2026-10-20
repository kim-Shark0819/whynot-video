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

  function variantColumn(host, detail, meta) {
    var v = (detail.variants || {})[meta.key] || {};
    var model3d = v.model3d || {};
    var column = el("div", "vnew-variant-col");
    column.appendChild(el("h2", "vnew-variant-title", meta.title));

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
    ["④", "영상"],
    ["⑤", "사운드"]
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
     ④는 gen 이 있으면, ⑤는 gen.selected 가 있으면 열린다(설계자 2026-10-20). */
  function maxReachableStep() {
    var plan = state.plan || {};
    var gen = plan.gen || {};
    var hasGen = gen && gen.status && gen.status !== "none";
    var hasSelected = !!gen.selected;
    if (hasSelected) return 5;
    if (hasGen) return 4;
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
    var gen = plan.gen || {};
    // 영상 생성이 진행 중(running)이면 ④를 보여 준다.
    if (gen.status === "running" || gen.status === "partial") {
      state.planStep = 4;
      return;
    }
    if (task.status === "running") {
      state.planStep = task.name === "conti" ? 3 : 2;
      return;
    }
    // 사용자가 이미 단계를 골랐으면(1~5) 그대로 둔다(단, 갈 수 없는 단계면 되돌린다).
    if (state.planStep >= 1 && state.planStep <= 5) {
      var maxStep = maxReachableStep();
      if (state.planStep > maxStep) state.planStep = maxStep;
      return;
    }
    // 처음 열 때: gen 이 있으면 ④, 아니면 ①.
    if (gen.status && gen.status !== "none") {
      state.planStep = gen.selected ? 5 : 4;
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
    var gen = plan.gen || {};
    var genRunning = gen.status === "running";
    if (task.status !== "running" && !genRunning) return;
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

  /* opening 이 아직이거나 어떤 구간이 preparing/queued/waiting(제출 직전)이면 빠른 폴링. */
  function genBusyFast(gen) {
    var opening = gen.opening || {};
    if (opening.status && opening.status !== "done" && opening.status !== "failed") return true;
    var segments = gen.segments || [];
    for (var i = 0; i < segments.length; i++) {
      var r = (segments[i].real || {}).status;
      var w = (segments[i].webtoon || {}).status;
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

    // 모델
    var modelField = el("div", "vnew-field");
    modelField.appendChild(el("label", "vnew-field-label", "영상 모델"));
    var select = el("select", "vnew-field-input");
    if (!models.length) {
      var opt = el("option", null, inp.videoModel || "기본 모델");
      opt.value = inp.videoModel || "";
      select.appendChild(opt);
      select.disabled = true;
    } else {
      models.forEach(function (model) {
        var option = el("option", null, model.label || model.id);
        option.value = model.id;
        if (model.id === inp.videoModel) option.selected = true;
        select.appendChild(option);
      });
    }
    // 실사 시작 이미지를 받지 않는 모델(Seedance 등) 경고.
    var realWarn = el(
      "p",
      "vnew-field-warn",
      "이 모델은 실사 인물 시작 이미지를 받지 않아 신규 영상 기능에서 실패할 수 있습니다. Kling 등 fal 모델을 권장합니다."
    );
    function updateRealWarn(modelId) {
      var chosen = null;
      for (var i = 0; i < models.length; i++) {
        if (models[i] && models[i].id === modelId) {
          chosen = models[i];
          break;
        }
      }
      var unsupported = chosen && chosen.realStartImage === false;
      realWarn.style.display = unsupported ? "" : "none";
    }
    select.addEventListener("change", function () {
      updateRealWarn(select.value);
      putInput({ videoModel: select.value })
        .then(function () {
          renderPlan(); // 최대 길이 안내가 바뀌니 다시 그린다
        })
        .catch(function (err) {
          showError("모델을 저장하지 못했습니다: " + errMessage(err));
        });
    });
    modelField.appendChild(select);
    modelField.appendChild(realWarn);
    updateRealWarn(select.value);
    grid.appendChild(modelField);

    section.appendChild(grid);
    return section;
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

    // 영상 만들기: 합계가 목표와 맞아야(콘티가 서버 초 맞춤을 거친 상태) 누를 수 있다.
    var makeBtn = el("button", "primary-button", "영상 만들기");
    makeBtn.type = "button";
    var lengthMismatch = totalSeconds !== sumSeconds;
    makeBtn.disabled = lengthMismatch;
    if (lengthMismatch) {
      makeBtn.title = "먼저 길이를 맞춰 주세요";
    }
    makeBtn.addEventListener("click", function () {
      openGenerateConfirm(showError);
    });
    footer.appendChild(makeBtn);
    if (lengthMismatch) {
      footer.appendChild(el("span", "vnew-hint", "합계를 목표에 맞춘 뒤 영상을 만들 수 있습니다."));
    }
    host.appendChild(footer);
  }

  /* ③ → ④ : 영상 만들기 확인창. 구간 수·'예상 비용이 듭니다'·설명을 보이고,
     확인하면 POST /generate {confirmUsd: estimate.totalUsd} → ④로. 409 메시지는 그대로 보인다
     (CharacterRealMissing 이면 캐릭터 만들기 링크를 함께 보인다). */
  function openGenerateConfirm(showError) {
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
    bodyWrap.appendChild(
      el("p", null, "실사 영상을 구간별로 이어 만든 뒤 웹툰 그림체로도 변환합니다.")
    );
    bodyWrap.appendChild(el("p", "vnew-hint", "구간 " + segCount + "개를 순서대로 만듭니다."));
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
          state.planStep = 4;
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

  /* ================================================================ ④ 영상 생성

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
    cancelled: "취소됨"
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
      host.appendChild(el("p", "vnew-hint", "아직 영상 생성을 시작하지 않았습니다. ③ 콘티에서 [영상 만들기] 를 눌러 주세요."));
      var backBtn = el("button", "outline-button", "← 콘티로");
      backBtn.type = "button";
      backBtn.addEventListener("click", function () {
        state.planStep = 3;
        renderPlan();
      });
      host.appendChild(backBtn);
      return;
    }

    var running = gen.status === "running";

    // --- 진행 상태 띠 + 취소 ---
    var statusBar = el("section", "panel vnew-gen-statusbar");
    var statusText = {
      running: "영상을 만들고 있습니다…",
      done: "두 영상이 모두 준비됐습니다. 아래에서 하나를 골라 사운드로 넘어가세요.",
      partial: "일부 구간이 실패했습니다. 실패한 칸에서 다시 시도할 수 있습니다.",
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
    host.appendChild(statusBar);

    // --- 맨 위 결과: 실사 / 웹툰 두 열 ---
    host.appendChild(finalResults(gen, showError));

    // --- 시작 장면(opening) ---
    host.appendChild(openingCard(gen.opening || {}));

    // --- 구간 타임라인 ---
    host.appendChild(segmentTimeline(gen.segments || [], showError));
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
                state.planStep = 5;
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

  /* 구간 타임라인: 구간마다 한 줄 — 번호·초·컷, 실사 칸·웹툰 칸(배지+영상+실패 시 재시도). */
  function segmentTimeline(segments, showError) {
    var section = el("section", "panel vnew-seg-timeline");
    section.appendChild(el("h2", "vnew-sec-title", "구간별 진행"));
    if (!segments.length) {
      section.appendChild(el("p", "vnew-hint", "구간이 없습니다."));
      return section;
    }
    var list = el("div", "vnew-seg-rows");
    segments.forEach(function (seg, index) {
      list.appendChild(segmentRow(seg, index, showError));
    });
    section.appendChild(list);
    return section;
  }

  function segmentRow(seg, index, showError) {
    var row = el("div", "vnew-seg-row");

    var meta = el("div", "vnew-seg-meta");
    meta.appendChild(el("span", "vnew-seg-no", "구간 " + (index + 1)));
    meta.appendChild(el("span", "vnew-seg-seconds", (seg.seconds || 0) + "초"));
    meta.appendChild(el("span", "vnew-seg-cuts", "컷 " + ((seg.cutIds || []).join(", ") || "-")));
    row.appendChild(meta);

    var cells = el("div", "vnew-seg-cells");
    VARIANTS.forEach(function (variant) {
      cells.appendChild(segmentCell(seg, variant, showError));
    });
    row.appendChild(cells);
    return row;
  }

  function segmentCell(seg, variant, showError) {
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
    } else if (v.status === "failed") {
      cell.appendChild(el("p", "login-error vnew-seg-error", v.error || "실패했습니다."));
      var retryLabel = variant.key === "real" ? "이 구간부터 다시" : "변환 다시";
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
      host.appendChild(el("p", "vnew-hint", "먼저 ④ 영상에서 결과 하나를 골라 주세요."));
      var goBtn = el("button", "outline-button", "← 영상으로");
      goBtn.type = "button";
      goBtn.addEventListener("click", function () {
        state.planStep = 4;
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
      state.planStep = 4;
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
      if (state.orderSaveTimer) {
        window.clearTimeout(state.orderSaveTimer);
        state.orderSaveTimer = 0;
      }
      revokeAllGlb();
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
