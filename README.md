# Streamlit + Supabase MCP Tool Catalog

로그인한 팀원이 MCP 도구의 Operation, Resource Type, Public Injection Point 여부와 Output keys를 공동 분류하는 앱이다. 관리자 이메일로 로그인하면 앱 안에서 서버 이름, `tools.json`, `tools-outputschema.json`을 올려 카탈로그를 바로 생성하거나 갱신할 수 있다.

## 제공 기능

- Supabase 이메일/비밀번호 로그인
- 암호화 쿠키를 사용한 30일 로그인 유지
- 서버별 도구 검색과 진행률 표시
- `Read`, `Write`, `Modify` 드롭다운
- `Private`, `Open-public`, `Targeted-access` 드롭다운
- Public Injection Point `True`, `False` 드롭다운
- Output keys 텍스트 편집
- Input/Output Schema 상세 보기
- 동시 수정 충돌 감지
- 관리자 전용 JSON 업로드 및 미리보기
- 동일한 서버/tool name 재업로드 시 기존 사람 분류값 보존

## 1. Supabase 준비

Supabase 프로젝트의 SQL Editor에서 `supabase_schema.sql`을 실행한다. **이 SQL은 기존 `tool_catalog` 테이블과 모든 분류값을 삭제하고 새로 생성한다.** 일반 로그인 사용자는 조회와 네 개의 사람 입력 열만 수정할 수 있다. 행 생성과 스키마 갱신은 서버 측 Secret Key를 사용하는 관리자 업로드 기능만 수행한다.

`Authentication → Users`에서 이메일과 비밀번호가 설정되고 이메일이 확인된 사용자를 만든다. 초대 메일 방식을 사용하려면 별도의 초대 콜백과 최초 비밀번호 설정 화면이 필요하므로, 이 앱의 기본 구성에서는 관리자가 사용자를 직접 생성하는 방식을 권장한다.

## 2. 로컬 설정

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

예시 설정을 복사한다.

```bash
cp .streamlit/secrets.toml.example .streamlit/secrets.toml
```

`.streamlit/secrets.toml`에 실제 프로젝트 URL, Publishable Key, Secret Key, 쿠키 암호화 비밀번호, 관리자 이메일을 입력한다. 쿠키 비밀번호는 다음 명령으로 별도 생성한다.

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

```toml
[supabase]
url = "https://PROJECT_ID.supabase.co"
publishable_key = "sb_publishable_..."
secret_key = "sb_secret_..."

[cookies]
password = "위 명령으로 생성한 32자 이상의 임의 문자열"

[app]
admin_emails = ["admin@example.com"]
```

Secret Key는 RLS를 우회하는 높은 권한 키다. 이 파일은 `.gitignore`에 포함되어 있으며 GitHub, 브라우저 코드, 채팅에 올리면 안 된다. 앱은 Supabase가 검증한 로그인 이메일이 `admin_emails`에 포함된 경우에만 관리자 업로드 UI에서 이 키를 사용한다.

로그인 세션은 브라우저의 암호화 쿠키에 저장되며 로그인한 시점부터 정확히 30일 동안 유지된다. 페이지를 다시 열 때 Supabase refresh token으로 세션을 갱신하더라도 최초 만료 시각은 연장되지 않는다. 로그아웃하면 Supabase 세션을 종료하고 쿠키도 삭제한다. `cookies.password`를 변경하면 기존 로그인 쿠키를 해독할 수 없어 모든 사용자가 다시 로그인해야 한다.

## 3. 실행

```bash
streamlit run streamlit_app.py
```

관리자로 로그인한 뒤 `JSON 업로드` 탭에서 다음을 입력한다.

1. 서버 이름
2. 원본 `tools.json`
3. 이름별 output schema JSON
4. 검증 미리보기 확인
5. `생성/갱신` 클릭

새 도구의 Output keys는 빈 텍스트로 생성하며 사람이 수정할 수 있다. 기존 서버를 같은 이름으로 다시 올리면 Description, Input Schema, Output Schema는 최신 파일로 갱신하고 Operation, Resource Type, Public Injection Point, 사람이 수정한 Output keys는 그대로 유지한다. 파일에 사라진 기존 도구는 자동 삭제하지 않는다.

## 4. Streamlit Community Cloud 배포

1. `secrets.toml`을 제외한 파일을 GitHub 저장소에 올린다.
2. Streamlit Community Cloud에서 `streamlit_app.py`를 엔트리포인트로 배포한다.
3. 앱의 Secrets 설정에 로컬 `secrets.toml` 내용을 입력한다.
4. 가능하면 Streamlit 앱 자체도 비공개로 설정하고 팀원에게 URL을 공유한다.

## 5. 테스트

```bash
python3 -m unittest -v \
  test_notion_tool_catalog.py \
  test_tool_catalog_service.py \
  test_persistent_login.py
```

의존성은 `streamlit==1.59.2`, `streamlit-cookies-manager-v2==0.3.1`, `supabase==2.31.0`으로 고정했다. Snyk 패키지 상태 조회는 현재 개발 환경의 Snyk 인증이 없어 완료하지 못했으므로, 실제 배포 환경에서는 설치 후 전체 의존성 트리에 대한 SCA 검사를 추가하는 것을 권장한다.
