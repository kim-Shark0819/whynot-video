/* 영상만들기_신규(vnew, 설계자 2026-10-20) 화면 — 지금은 틀만이다.

   #vnew-root(영상만들기_신규)와 #characters-root(캐릭터 만들기) 안의 DOM 을 전부 직접 만든다.
   기존 마크업을 찾지 않고, 이벤트는 자기가 만든 요소에만 단다. 어떤 요소가 없어도 예외로
   멈추지 않는다. CSS 클래스는 vnew- 접두사.

   서버 연결 확인: GET /api/vnew/health → {ok:true}. 인증은 기존 블루프린트들과 같은 방식
   (sessionStorage 의 토큰을 Bearer 로 보낸다 — vpoc.js 와 같은 토큰 키).

   전역: window.vnew = { mount(projectId), mountCharacters(), unmount() } */
(function () {
  "use strict";

  var TOKEN_KEY = "toon-change-ai-pj:token";
  var ROOT_ID = "vnew-root";
  var CHARACTERS_ROOT_ID = "characters-root";

  // 앞으로 붙일 단계(설계자 2026-10-20). 지금은 목록으로만 보인다.
  var STEPS = [
    ["①", "웹툰 이미지 올리기"],
    ["②", "대본·상황 정리"],
    ["③", "콘티"],
    ["④", "영상(실사·웹툰풍)"],
    ["⑤", "사운드(베타)"]
  ];

  var state = {
    mounted: false,
    projectId: ""
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

  /* ---------------------------------------------------------------- 통신 */

  function api(path, options) {
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

  /* ---------------------------------------------------------------- 조각 */

  /* 서버 연결 확인 줄. /api/vnew/health 를 불러 상태를 보인다. */
  function healthRow() {
    var row = el("p", "vnew-health", "서버 연결 확인 중…");
    api("/api/vnew/health")
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

  /* 준비 중 카드. 제목과 안내 문구, 선택적으로 단계 목록을 담는다. */
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

  function renderInto(host, title, lead, withSteps) {
    if (!host) return;
    clear(host);
    host.appendChild(readyCard(title, lead, withSteps));
  }

  /* ---------------------------------------------------------------- 전역 */

  window.vnew = {
    mount: function (projectId) {
      state.mounted = true;
      state.projectId = projectId || "";
      renderInto(
        byId(ROOT_ID),
        "영상만들기_신규 — 준비 중",
        "웹툰 이미지만 넣으면 콘티·영상까지 만드는 새 기능입니다. 아래 단계 순서로 열립니다.",
        true
      );
    },
    mountCharacters: function () {
      state.mounted = true;
      renderInto(
        byId(CHARACTERS_ROOT_ID),
        "캐릭터 만들기 — 준비 중",
        "영상만들기_신규에서 쓸 캐릭터를 만드는 화면입니다. 곧 열립니다.",
        false
      );
    },
    unmount: function () {
      state.mounted = false;
      clear(byId(ROOT_ID));
      clear(byId(CHARACTERS_ROOT_ID));
    }
  };
})();
