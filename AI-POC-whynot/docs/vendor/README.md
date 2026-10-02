# 벤더 공식 문서 (1차 출처)

`Seedance영상고품질화_계획.md`의 원칙: **1차 출처만 근거로 쓴다.** 서드파티
리셀러(Kie·Magic Hour·Vercel·runware.ai 등) 문서는 파라미터명·상한이 다를 수 있어
설계 근거로 쓰지 않는다.

`docs.byteplus.com`은 JavaScript 렌더링 SPA라 자동 fetch로 본문을 가져올 수 없다
(실측: 네비게이션 메뉴만 560바이트, 본문 텍스트 0). 브라우저에서 열어 각 페이지의
"Copy Page" 버튼으로 Markdown을 복사해 이 디렉터리에 저장한다.

## 받을 파일 목록

| 파일명 | 문서 | ID |
|---|---|---|
| `seedance-2.0-tutorial.md` | Seedance 2.0 series tutorial | ModelArk/2291680 |
| `seedance-2.0-prompt-guide.md` | Seedance 2.0 series prompt guide | ModelArk/2222480 |
| `modelark-regions.md` | Region availability | ModelArk/2191806 |
| `modelark-auth.md` | Signature authentication | ModelArk/1465834 |
| `modelark-api.md` | APIs (API reference) | ModelArk/1511946 |
| `seedance-enterprise-use-case.md` | Dreamina-Seedance-2.0 Enterprise Use Case Guide (larkoffice, Marketplace 리스팅 첨부) | — |

위 6개 파일이 이 디렉터리에 채워지면 `seedance-video-quality` spec의 태스크 0.3이
완료되고, R-03.3(프롬프트 규칙)·R-04.2(레퍼런스 상한)·R-06.2(토큰 계산)를 이
파일들 기준으로 확정할 수 있다.
