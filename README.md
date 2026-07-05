# epub-factory

`epub-factory`는 `pdf2htmlEX`로 PDF를 변환한 PDFHTML 파일을 EPUB 제작에 적합한 XHTML로 정제한 뒤, EPUB로 패키징하는 도구입니다.

PDFHTML은 원본 PDF의 시각적 배치를 그대로 재현하는 데 초점이 맞춰져 있어, 절대 좌표로 배치된 글자 단위 `<div>`, 페이지 전체 크기의 배경 래스터 이미지, 뷰어 전용 스크립트/스타일, 커닝 보정용 빈 `<span>` 등이 뒤섞여 있습니다. `epub-factory`는 이 노이즈를 제거하고 폰트 크기·글머리기호 등을 단서로 문단과 제목을 재구성(reflow)하여, EPUB 리더에서 자유롭게 리플로우되는 구조적 XHTML 문서를 만듭니다.

## 파이프라인

```text
input.html ──(1) pdfhtml2xhtml.py──▶ input/*.xhtml + input/index.yaml ──(2) xhtml2epub.py──▶ *.epub
                                                  ▲
                                     (사용자가 index.yaml을 직접 편집해
                                      목차 구조·제목·저자를 조정 가능)

input.html ──────────────────── html2epub.py (위 두 단계를 한 번에 실행) ────────────────────▶ *.epub
```

변환은 두 단계로 나뉘어 있습니다. 목차(챕터) 구성을 손볼 필요가 없다면 `html2epub.py`로 한 번에 처리하면 되고, 챕터를 합치거나 나누거나 목차 계층(레벨)·제목·저자를 조정하고 싶다면 `pdfhtml2xhtml.py`로 XHTML과 `index.yaml`을 먼저 만든 뒤 `index.yaml`을 손으로 고치고 `xhtml2epub.py`로 패키징합니다.

## 빠른 시작

`bin\` 폴더의 배치 스크립트를 실행하면 되며, `python`이나 가상환경 활성화 없이 어느 위치에서든 바로 실행할 수 있습니다.

```powershell
# 1. 한 번에 변환 (목차 구성을 바꿀 필요가 없는 경우)
.\bin\html2epub.bat data\sample.html
```

```powershell
# 2. 목차를 조정하고 싶은 경우: 두 단계로 실행
.\bin\pdfhtml2xhtml.bat data\sample.html
# data\sample\index.yaml 을 열어 level/label/file, title, author를 조정한다
.\bin\xhtml2epub.bat data\sample\index.yaml
```

## 스크립트

### `pdfhtml2xhtml`

pdf2htmlEX HTML을 리플로우된 XHTML 챕터들로 분할합니다.

```text
bin\pdfhtml2xhtml.bat <input.html>
```

- 입력 `<dir>/<stem>.html` 기준으로 `<dir>/<stem>/` 폴더를 만들어 그 안에 결과를 씁니다.
- PDF 북마크(목차) 항목 하나당 XHTML 파일 하나(`001-<slug>.xhtml`, `002-<slug>.xhtml`, ...)를 생성합니다. 북마크 순서를 그대로 따르므로, 북마크가 평평한 목록이면 결과도 평평합니다.
- 같은 폴더에 `index.yaml`을 함께 생성합니다.
- 처리 과정:
  1. `<style>`에 자동 생성된 pdf2htmlEX 클래스(위치 `x`/`y`, 크기 `fs`, 배율 `m`)를 파싱해 각 텍스트 줄의 실제 화면 좌표와 글자 크기를 복원합니다(`@media print` 재정의는 화면용 px 값을 덮어쓰므로 무시합니다).
  2. 같은 줄에 걸친 텍스트 조각을 하나로 합치고, 페이지 읽기 순서(위→아래, 왼쪽→오른쪽)로 정렬합니다.
  3. 문서 전체에서 가장 많이 쓰인 글자 크기를 본문 크기로 간주하고, 그보다 큰 크기 또는 글머리기호(○, -, (01번), ①…)로 시작하는 줄을 새 블록의 시작으로 판단해 문단/제목 블록으로 묶습니다.
  4. 제목 블록들을 크기별로 군집화해 `<h1>`~`<h6>` 레벨을 배정합니다.
  5. 북마크의 페이지+스크롤 좌표(`data-dest-detail`)를 기준으로 각 줄을 해당 북마크(챕터)에 배정합니다.
  6. 배경 이미지(`<img class="bi">`)는 완전히 버립니다. 표의 테두리, 장식 요소 등은 사라지지만, **PDF에서 텍스트가 아니라 그래픽(스타일이 입혀진 로고체 문구 등)으로 그려진 글자도 함께 사라집니다** — 자세한 내용은 [알려진 한계](#알려진-한계) 참고.
  7. 페이지 번호만 있는 줄(`- 6 -` 등)은 버립니다.

### `xhtml2epub`

`index.yaml`을 읽어 EPUB로 패키징합니다.

```text
bin\xhtml2epub.bat <index.yaml> [-o OUTPUT_DIR]
```

- `chapters` 목록의 순서를 그대로 EPUB 읽기 순서(spine)로 사용합니다. 즉 `index.yaml`에서 항목을 재배열하면 책의 순서가 바뀝니다.
- 각 항목의 `level`로 목차(EPUB3 `nav.xhtml` + 구버전 호환용 `toc.ncx`)를 중첩 구조로 만듭니다. `level` 값을 고치면 목차 계층이 바뀝니다.
- `-o`를 생략하면 `index.yaml`과 같은 폴더에 저장하고, 지정하면 그 폴더(없으면 새로 생성)에 저장합니다.
- 파일명은 `"<title> by <author>.epub"`이며, `title`/`author`가 비어 있으면 각각 `"unknown"`으로 대체합니다.

### `html2epub`

위 두 스크립트를 이어 붙인 편의 스크립트입니다.

```text
bin\html2epub.bat <input.html> [-o OUTPUT_DIR]
```

- XHTML/`index.yaml` 생성 경로는 `pdfhtml2xhtml`의 규칙을 그대로 따릅니다(`<dir>/<stem>/`).
- EPUB 저장 경로는 `-o`를 생략하면 **입력 HTML 파일이 있는 폴더**가 기본값입니다(`xhtml2epub`를 단독 실행할 때의 기본값인 `index.yaml` 폴더와 다릅니다).

각 `.bat`은 프로젝트의 `.venv` 파이썬으로 `scripts\` 아래의 동일한 이름의 `.py` 스크립트를 실행하는 얇은 래퍼이며, 실행 위치(현재 작업 폴더)와 무관하게 항상 올바른 가상환경과 스크립트를 찾습니다. `python`을 직접 호출하거나 가상환경을 활성화할 필요가 없습니다.

## `index.yaml` 형식

`pdfhtml2xhtml.py`가 생성하고 `xhtml2epub.py`가 읽는 중간 산출물로, EPUB의 제목·저자·목차 구조를 사람이 직접 조정할 수 있게 하기 위한 파일입니다.

```yaml
title: 제목
author: 저자
chapters:
  - level: 2
    label: 목차
    file: 001-목차.xhtml
  - level: 2
    label: 개요
    file: 002-개요.xhtml
```

- `title`/`author`: 문서에 별도 메타데이터가 없으므로, 문서에서 감지된 첫 두 개의 제목(heading)을 각각 제목/저자로 추정해 채워 넣은 값입니다. 정확하지 않을 수 있으니 직접 확인하고 고치는 것을 권장합니다.
- `chapters[].level`: 목차 중첩 깊이(최상위 1). PDF 북마크 트리의 실제 중첩 깊이를 그대로 반영한 초기값이며, 북마크가 평평한 문서라면 대부분 같은 레벨로 채워집니다. EPUB 목차를 원하는 계층으로 만들려면 이 값을 직접 조정하세요.
- `chapters[].label`: 목차와 XHTML `<title>`에 쓰이는 챕터 이름. 자유롭게 수정 가능합니다.
- `chapters[].file`: 같은 폴더에 있는 XHTML 파일명. 목록에서 항목을 지우면 해당 챕터는 EPUB에서 빠집니다. 순서를 바꾸면 읽기 순서가 바뀝니다.

## 알려진 한계

- **배경 이미지에 그려진 텍스트는 복원 불가**: 이 문서는 표지·챕터 구분 페이지 등에서 "AI" 같은 문구를 일반 텍스트가 아니라 그래픽(로고체 효과가 들어간 그림)으로 그려 넣은 경우가 있습니다. pdf2htmlEX는 이런 요소를 배경 PNG에 래스터화하고, 진짜 선택 가능한 글자만 HTML 텍스트로 남깁니다. `epub-factory`는 배경 이미지를 통째로 버리므로, 그 안에 있던 문구는 결과 XHTML에서 사라집니다(예: `전략분야AI 고속도로 구축` → `전략분야 고속도로 구축`).
- **북마크가 평평하면 목차도 평평합니다**: PDF 북마크 트리에 실제 중첩(하위 `<ul>`)이 없으면 `level`이 대부분 같은 값으로 채워집니다. 항목 순서가 북마크 자체의 순서를 그대로 따르므로, 원본 PDF의 북마크가 뒤섞여 있으면(예: 부록 각주 번호가 뒤죽박죽인 경우) 챕터 순서도 그대로 뒤섞입니다. `index.yaml`을 손으로 정리해서 바로잡아야 합니다.
- **문단/제목 구분은 휴리스틱**: 글자 크기·글머리기호 패턴에 의존하는 근사치이므로, 디자인이 복잡한 페이지(다단 배치, 표 등)에서는 문장이 부자연스럽게 합쳐지거나 나뉠 수 있습니다.
- **표·이미지·다단 레이아웃은 지원하지 않음**: 결과는 순수 텍스트(`<h1>`~`<h6>`, `<p>`)만 포함합니다.

## 개발 환경

Python 3.13 이상, [uv](https://docs.astral.sh/uv/)로 의존성을 관리합니다.

```powershell
uv sync
```

주요 의존성(`pyproject.toml` 참고):

- `beautifulsoup4`, `lxml`: HTML 파싱
- `pyyaml`: `index.yaml` 읽기/쓰기

세 스크립트는 `bin\`의 배치 파일로 실행하는 것이 기본이지만(위 [스크립트](#스크립트) 참고), 다른 파이썬 코드를 임시로 시험해 보고 싶다면 프로젝트 루트의 `python.cmd`로 가상환경 Python을 바로 실행할 수 있습니다.

```powershell
.\python.cmd scripts\html2epub.py data\sample.html
```

## 저장소 구조

```text
.
├── bin/
│   ├── pdfhtml2xhtml.bat    # scripts\pdfhtml2xhtml.py 실행 래퍼
│   ├── xhtml2epub.bat       # scripts\xhtml2epub.py 실행 래퍼
│   └── html2epub.bat        # scripts\html2epub.py 실행 래퍼
├── data/                    # 입력 HTML 및 변환 결과 (git 추적 제외)
├── scripts/
│   ├── pdfhtml2xhtml.py     # HTML → XHTML 챕터 + index.yaml
│   ├── xhtml2epub.py        # index.yaml → EPUB
│   └── html2epub.py         # 위 두 단계를 한 번에 실행
├── main.py
├── pyproject.toml
└── README.md
```

## 라이선스

MIT
