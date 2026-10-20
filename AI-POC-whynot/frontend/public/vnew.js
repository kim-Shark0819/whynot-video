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
    glbLoading: {} // variant → true (중복 요청 방지)
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
    if (analysis.descriptionKo || analysis.model) {
      var card = el("div", "vnew-analysis");
      if (analysis.descriptionKo) card.appendChild(el("p", "vnew-analysis-desc", analysis.descriptionKo));
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

    btn.addEventListener("click", function () {
      if (madeBefore) {
        var okConfirm = false;
        try {
          okConfirm = window.confirm("다시 만들면 새 버전이 생기고 비용이 듭니다. 계속할까요?");
        } catch (e) {
          okConfirm = true;
        }
        if (!okConfirm) return;
      }
      btn.disabled = true;
      api("/api/vnew/characters/" + encodeURIComponent(detail.id) + "/build", { method: "POST" })
        .then(function () {
          refreshDetail(host, false);
        })
        .catch(function (err) {
          btn.disabled = false;
          error.style.display = "";
          if (err && err.status === 409) {
            error.textContent = "이미 만들고 있습니다.";
          } else {
            error.textContent = "만들기를 시작하지 못했습니다: " + errMessage(err);
          }
        });
    });

    section.appendChild(btn);
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
      var retry = el("button", "outline-action", "다시 만들기");
      retry.type = "button";
      retry.addEventListener("click", function () {
        var okConfirm = false;
        try {
          okConfirm = window.confirm("다시 만들면 새 버전이 생기고 비용이 듭니다. 계속할까요?");
        } catch (e) {
          okConfirm = true;
        }
        if (!okConfirm) return;
        retry.disabled = true;
        api("/api/vnew/characters/" + encodeURIComponent(detail.id) + "/build", { method: "POST" })
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

  /* ---------------------------------------------------------------- 전역 */

  window.vnew = {
    mount: function (projectId) {
      state.mounted = true;
      state.projectId = projectId || "";
      renderReadyInto(
        byId(ROOT_ID),
        "영상만들기_신규 — 준비 중",
        "웹툰 이미지만 넣으면 콘티·영상까지 만드는 새 기능입니다. 아래 단계 순서로 열립니다.",
        true
      );
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
      revokeAllGlb();
      state.view = "list";
      state.charId = "";
      state.detail = null;
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
