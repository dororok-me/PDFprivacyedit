# PDF개인정보삭제기 (PDFprivacyedit)

고등학교 입학 지원서(생활기록부 등) PDF에서 개인정보를 찾아 검은 박스로 덮고,
밑의 텍스트·이미지 데이터까지 완전히 지운 뒤 새 PDF로 저장합니다. 전부 로컬에서만 동작합니다.

## 파일
- app_web.py + ui/index.html : 메인 앱 화면 (pywebview)
- app.py     : 예비용 단순 화면 (Tkinter)
- engine.py  : 탐지·삭제 엔진
- ocr.py     : 스캔 PDF용 OCR (Tesseract)
- batch.py   : 터미널용 일괄 처리
- .github/workflows/build.yml : GitHub에 올리면 윈도우 exe / 맥 app 자동 빌드

## exe / 맥 앱 받기 (GitHub Actions)
1. 이 폴더를 GitHub 저장소(main 브랜치)에 push
2. 저장소 → Actions 탭 → "build" 실행 완료 후 Artifacts 에서 다운로드
   - PDF개인정보삭제기-windows : exe 하나 (Tesseract 한국어 OCR 포함, 설치 불필요)
   - PDF개인정보삭제기-mac : .app (OCR은 `brew install tesseract tesseract-lang` 필요)
3. 윈도우 첫 실행 시 SmartScreen 경고 → "추가 정보" → "실행". 맥은 우클릭 → 열기.

## 파이썬으로 바로 실행 (테스트용)
윈도우: run_windows.bat 더블클릭   /   맥: run_mac.command 더블클릭 (Python 3.10+ 필요)

## 삭제 항목
성명 / 주민등록번호 / 생년월일 / 전화번호 / 이메일 / 주소 / 사진 / 반·번호 / 대장번호 / 학교명 / 출력정보(IP·출력자)
전체 기본값을 정한 뒤, 파일 목록에서 파일을 클릭해 개별로 바꿀 수 있습니다(★ 표시).
저장 폴더의 _report.json 에 파일별로 무엇을 어디서 지웠는지 기록됩니다.
