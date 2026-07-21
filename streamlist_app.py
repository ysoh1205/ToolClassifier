# streamlit_app.py
import streamlit as st
from supabase import create_client


OPERATIONS = ["Read", "Write", "Modify"]
RESOURCE_TYPES = ["Private", "Open-public", "Targeted-access"]


st.set_page_config(
    page_title="Tool Catalog",
    page_icon="🧰",
    layout="wide",
)


def clear_login():
    for key in ("access_token", "refresh_token"):
        st.session_state.pop(key, None)


client = create_client(
    st.secrets["supabase"]["url"],
    st.secrets["supabase"]["publishable_key"],
)

# 이전 로그인 세션 복원
if "access_token" in st.session_state:
    try:
        auth = client.auth.set_session(
            st.session_state["access_token"],
            st.session_state["refresh_token"],
        )
        st.session_state["access_token"] = auth.session.access_token
        st.session_state["refresh_token"] = auth.session.refresh_token
    except Exception:
        clear_login()

# 로그인 화면
if "access_token" not in st.session_state:
    st.title("Tool Catalog 로그인")

    with st.form("login"):
        email = st.text_input("이메일")
        password = st.text_input("비밀번호", type="password")
        submitted = st.form_submit_button("로그인")

    if submitted:
        try:
            auth = client.auth.sign_in_with_password(
                {
                    "email": email.strip(),
                    "password": password,
                }
            )

            if auth.session is None:
                st.error("세션이 생성되지 않았습니다.")
                st.stop()

            st.session_state["access_token"] = (
                auth.session.access_token
            )
            st.session_state["refresh_token"] = (
                auth.session.refresh_token
            )

            st.rerun()

        except Exception as exc:
            # 개발 중에만 상세 오류 표시
            st.error(
                f"{type(exc).__name__}: {exc}"
            )

    st.stop()


user = client.auth.get_user().user

st.sidebar.write(user.email)

if st.sidebar.button("로그아웃"):
    try:
        client.auth.sign_out()
    finally:
        clear_login()
        st.rerun()


# 서버 목록
server_rows = (
    client.table("tool_catalog")
    .select("server_name")
    .execute()
    .data
)

server_names = sorted({row["server_name"] for row in server_rows})

if not server_names:
    st.info("등록된 서버가 없습니다.")
    st.stop()

server_name = st.sidebar.selectbox("서버", server_names)

# 선택된 서버의 데이터
rows = (
    client.table("tool_catalog")
    .select("*")
    .eq("server_name", server_name)
    .order("tool_name")
    .execute()
    .data
)

st.title(f"{server_name} Tool Catalog")

query = st.text_input("도구 검색").strip().lower()

visible_rows = [
    row
    for row in rows
    if not query
    or query in row["tool_name"].lower()
    or query in row["description"].lower()
]

grid_rows = [
    {
        "id": row["id"],
        "tool_name": row["tool_name"],
        "description": row["description"],
        "operation": row["operation"],
        "resource_type": row["resource_type"],
    }
    for row in visible_rows
]

edited_rows = st.data_editor(
    grid_rows,
    width="stretch",
    hide_index=True,
    num_rows="fixed",
    column_order=(
        "tool_name",
        "description",
        "operation",
        "resource_type",
    ),
    disabled=("tool_name", "description"),
    column_config={
        "tool_name": st.column_config.TextColumn(
            "Tool Name",
            width="medium",
        ),
        "description": st.column_config.TextColumn(
            "Description",
            width="large",
        ),
        "operation": st.column_config.SelectboxColumn(
            "Operation",
            options=OPERATIONS,
            required=False,
        ),
        "resource_type": st.column_config.SelectboxColumn(
            "Resource Type",
            options=RESOURCE_TYPES,
            required=False,
        ),
    },
)

if st.button("변경 사항 저장", type="primary"):
    saved = 0
    conflicts = 0

    for original, edited in zip(visible_rows, edited_rows, strict=True):
        changes = {}

        for field in ("operation", "resource_type"):
            if edited.get(field) != original.get(field):
                changes[field] = edited.get(field)

        if not changes:
            continue

        # updated_at을 조건에 포함해 동시 수정 충돌 방지
        response = (
            client.table("tool_catalog")
            .update(changes)
            .eq("id", original["id"])
            .eq("updated_at", original["updated_at"])
            .select("id")
            .execute()
        )

        if response.data:
            saved += 1
        else:
            conflicts += 1

    if conflicts:
        st.warning(
            f"{conflicts}개 행은 다른 사용자가 먼저 수정했습니다. "
            "새로고침 후 다시 확인하세요."
        )
    else:
        st.success(f"{saved}개 행을 저장했습니다.")
        st.rerun()


# 상세 스키마
if visible_rows:
    st.divider()
    selected_name = st.selectbox(
        "상세 스키마를 볼 도구",
        [row["tool_name"] for row in visible_rows],
    )

    selected = next(
        row for row in visible_rows
        if row["tool_name"] == selected_name
    )

    st.subheader(selected["tool_name"])
    st.write(selected["description"])

    input_tab, output_tab = st.tabs(
        ["Input Schema", "Output Schema"]
    )

    with input_tab:
        st.json(selected["input_schema"] or {})

    with output_tab:
        st.json(selected["output_schema"] or {})