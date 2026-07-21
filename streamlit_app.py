"""Shared Streamlit UI for classifying MCP tools stored in Supabase."""

from __future__ import annotations

from typing import Any

import streamlit as st
from streamlit_cookies_manager import EncryptedCookieManager
from supabase import create_client

from notion_tool_catalog import CatalogError
from persistent_login import (
    COOKIE_NAME,
    COOKIE_PREFIX,
    PersistentSession,
    create_persistent_session,
    deserialize_persistent_session,
    serialize_persistent_session,
    set_cookie_expiry,
)
from tool_catalog_service import parse_catalog_upload, upsert_catalog


OPERATIONS = ["Read", "Write", "Modify"]
RESOURCE_TYPES = ["Private", "Open-public", "Targeted-access"]
PUBLIC_INJECTION_CHOICES = ["True", "False"]


st.set_page_config(
    page_title="MCP Tool Catalog",
    page_icon="🧰",
    layout="wide",
)


def clear_login() -> None:
    for key in ("access_token", "refresh_token", "login_expires_at"):
        st.session_state.pop(key, None)


def delete_login_cookie(cookies: Any) -> None:
    if cookies.get(COOKIE_NAME) is not None:
        del cookies[COOKIE_NAME]
        cookies.save()


def save_login_cookie(cookies: Any, session: PersistentSession) -> None:
    serialized = serialize_persistent_session(session)
    if cookies.get(COOKIE_NAME) != serialized:
        set_cookie_expiry(cookies, expires_at=session.expires_at)
        cookies[COOKIE_NAME] = serialized
        cookies.save()


def restore_login(client: Any, cookies: Any) -> None:
    stored_value = cookies.get(COOKIE_NAME)
    persistent = deserialize_persistent_session(stored_value)

    state_has_access = "access_token" in st.session_state
    state_has_refresh = "refresh_token" in st.session_state
    if state_has_access != state_has_refresh:
        clear_login()
        state_has_access = False

    if not state_has_access:
        if persistent is None:
            if stored_value is not None:
                delete_login_cookie(cookies)
            return
        st.session_state["access_token"] = persistent.access_token
        st.session_state["refresh_token"] = persistent.refresh_token
        st.session_state["login_expires_at"] = persistent.expires_at
    else:
        current = deserialize_persistent_session(
            serialize_persistent_session(
                PersistentSession(
                    access_token=st.session_state["access_token"],
                    refresh_token=st.session_state["refresh_token"],
                    expires_at=st.session_state.get("login_expires_at", 0),
                )
            )
        )
        if current is None:
            clear_login()
            delete_login_cookie(cookies)
            return
        persistent = current

    if persistent is None:
        return
    try:
        auth = client.auth.set_session(
            st.session_state["access_token"],
            st.session_state["refresh_token"],
        )
        if auth.session is None:
            clear_login()
            delete_login_cookie(cookies)
            return
        refreshed = PersistentSession(
            access_token=auth.session.access_token,
            refresh_token=auth.session.refresh_token,
            expires_at=persistent.expires_at,
        )
        st.session_state["access_token"] = refreshed.access_token
        st.session_state["refresh_token"] = refreshed.refresh_token
        st.session_state["login_expires_at"] = refreshed.expires_at
        save_login_cookie(cookies, refreshed)
    except Exception:
        clear_login()
        delete_login_cookie(cookies)


def render_login(client: Any, cookies: Any) -> None:
    st.title("MCP Tool Catalog")
    st.caption("Supabase에 등록된 팀 계정으로 로그인하세요.")
    with st.form("login_form"):
        email = st.text_input("이메일")
        password = st.text_input("비밀번호", type="password")
        submitted = st.form_submit_button("로그인", type="primary")

    if submitted:
        try:
            auth = client.auth.sign_in_with_password(
                {"email": email.strip(), "password": password}
            )
            if auth.session is None:
                st.error("로그인 세션이 생성되지 않았습니다.")
                return
            persistent = create_persistent_session(
                auth.session.access_token,
                auth.session.refresh_token,
            )
            st.session_state["access_token"] = persistent.access_token
            st.session_state["refresh_token"] = persistent.refresh_token
            st.session_state["login_expires_at"] = persistent.expires_at
            save_login_cookie(cookies, persistent)
            st.rerun()
        except Exception as exc:
            st.error(f"로그인 실패: {exc}")


def fetch_rows(client: Any, server_name: str) -> list[dict[str, Any]]:
    response = (
        client.table("tool_catalog")
        .select("*")
        .eq("server_name", server_name)
        .order("tool_name")
        .execute()
    )
    return [dict(row) for row in response.data]


def render_catalog(client: Any) -> None:
    if message := st.session_state.pop("catalog_message", None):
        st.success(message)

    server_response = client.table("tool_catalog").select("server_name").execute()
    server_names = sorted({row["server_name"] for row in server_response.data})
    if not server_names:
        st.info("등록된 서버가 없습니다. 관리자가 JSON 업로드 탭에서 생성해야 합니다.")
        return

    header_left, header_right = st.columns([4, 1])
    with header_left:
        server_name = st.selectbox("서버", server_names)
    with header_right:
        st.write("")
        st.write("")
        st.button("새로고침", width="stretch")

    rows = fetch_rows(client, server_name)
    classified = sum(
        bool(
            row.get("operation")
            and row.get("resource_type")
            and row.get("public_injection_point") is not None
        )
        for row in rows
    )
    st.progress(
        classified / len(rows) if rows else 0,
        text=f"분류 완료: {classified}/{len(rows)}",
    )

    query = st.text_input("도구 이름 또는 설명 검색").strip().casefold()
    visible_rows = [
        row
        for row in rows
        if not query
        or query in row["tool_name"].casefold()
        or query in (row.get("description") or "").casefold()
    ]
    if not visible_rows:
        st.warning("검색 결과가 없습니다.")
        return

    grid_rows = [
        {
            "id": row["id"],
            "tool_name": row["tool_name"],
            "description": row.get("description") or "",
            "operation": row.get("operation"),
            "resource_type": row.get("resource_type"),
            "public_injection_point": (
                None
                if row.get("public_injection_point") is None
                else str(bool(row["public_injection_point"]))
            ),
            "output_keys": row.get("output_keys") or "",
        }
        for row in visible_rows
    ]
    edited_rows = st.data_editor(
        grid_rows,
        width="stretch",
        height=520,
        hide_index=True,
        num_rows="fixed",
        column_order=(
            "tool_name",
            "description",
            "operation",
            "resource_type",
            "public_injection_point",
            "output_keys",
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
                width="medium",
            ),
            "resource_type": st.column_config.SelectboxColumn(
                "Resource Type",
                options=RESOURCE_TYPES,
                required=False,
                width="medium",
            ),
            "public_injection_point": st.column_config.SelectboxColumn(
                "Public Injection Point",
                options=PUBLIC_INJECTION_CHOICES,
                required=False,
                width="medium",
            ),
            "output_keys": st.column_config.TextColumn(
                "Output keys",
                width="large",
            ),
        },
        key=f"catalog_editor_{server_name}_{query}",
    )

    if st.button("변경 사항 저장", type="primary"):
        saved = 0
        conflicts = 0
        for original, edited in zip(visible_rows, edited_rows, strict=True):
            changes: dict[str, Any] = {
                field: edited.get(field)
                for field in ("operation", "resource_type", "output_keys")
                if edited.get(field) != original.get(field)
            }
            public_value = edited.get("public_injection_point")
            public_injection_point = (
                None if public_value in (None, "") else public_value == "True"
            )
            if public_injection_point != original.get("public_injection_point"):
                changes["public_injection_point"] = public_injection_point
            if not changes:
                continue

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
                "새로고침 후 다시 선택하세요."
            )
        else:
            st.session_state["catalog_message"] = f"{saved}개 행을 저장했습니다."
            st.rerun()

    st.divider()
    selected_name = st.selectbox(
        "Input/Output Schema를 볼 도구",
        [row["tool_name"] for row in visible_rows],
    )
    selected = next(
        row for row in visible_rows if row["tool_name"] == selected_name
    )
    st.subheader(selected["tool_name"])
    st.write(selected.get("description") or "설명이 없습니다.")
    st.write(
        "**Public Injection Point:** "
        + (
            "미분류"
            if selected.get("public_injection_point") is None
            else str(bool(selected["public_injection_point"]))
        )
    )
    st.write(f"**Output keys:** {selected.get('output_keys') or '(없음)'}")
    input_tab, output_tab = st.tabs(["Input Schema", "Output Schema"])
    with input_tab:
        st.json(selected.get("input_schema") or {})
    with output_tab:
        st.json(selected.get("output_schema") or {})


def render_upload(
    *,
    project_url: str,
    secret_key: str | None,
) -> None:
    st.subheader("서버 도구 JSON 업로드")
    st.write(
        "서버 이름과 두 JSON 파일을 올리면 새 도구는 생성하고, 같은 이름의 "
        "도구는 설명과 스키마를 갱신합니다. 기존 사람 입력 필드는 보존합니다."
    )
    if message := st.session_state.pop("upload_message", None):
        st.success(message)

    server_name = st.text_input(
        "서버 이름",
        placeholder="예: atlassian_jira",
        key="upload_server_name",
    )
    left, right = st.columns(2)
    with left:
        tools_file = st.file_uploader(
            "tools.json",
            type=("json",),
            key="tools_json_upload",
        )
    with right:
        output_file = st.file_uploader(
            "tools-outputschema.json",
            type=("json",),
            key="output_json_upload",
        )

    if not server_name.strip() or tools_file is None or output_file is None:
        st.info("서버 이름과 JSON 파일 두 개를 모두 입력하세요.")
        return

    try:
        catalog = parse_catalog_upload(
            tools_file.getvalue(),
            output_file.getvalue(),
            server_name,
        )
    except CatalogError as exc:
        st.error(str(exc))
        return

    st.success(f"검증 완료: {len(catalog.records)}개 도구")
    st.dataframe(
        [
            {
                "Tool Name": record["tool_name"],
                "Description": record["description"],
                "Output keys": record["output_keys"],
            }
            for record in catalog.records
        ],
        width="stretch",
        hide_index=True,
        height=360,
    )
    if catalog.warnings:
        with st.expander(f"경고 {len(catalog.warnings)}개"):
            for warning in catalog.warnings:
                st.warning(warning)

    if not secret_key:
        st.error(
            "관리자 업로드 키가 없습니다. Streamlit Secrets의 "
            "supabase.secret_key를 설정하세요."
        )
        return

    if st.button(
        f"{catalog.server_name} 생성/갱신",
        type="primary",
        key="upload_catalog_button",
    ):
        try:
            admin_client = create_client(project_url, secret_key)
            result = upsert_catalog(admin_client, catalog)
            st.session_state["upload_message"] = (
                f"완료: 신규 {result.created_count}개, "
                f"갱신 {result.updated_count}개, 총 {result.total_count}개"
            )
            st.rerun()
        except Exception as exc:
            st.error(f"Supabase 반영 실패: {exc}")


def main() -> None:
    try:
        supabase_config = st.secrets["supabase"]
    except (FileNotFoundError, KeyError):
        st.error("Streamlit Secrets에 [supabase] 설정이 없습니다.")
        st.stop()

    missing_keys = [
        key for key in ("url", "publishable_key") if key not in supabase_config
    ]
    if missing_keys:
        st.error(
            "Streamlit Secrets의 [supabase]에 다음 값이 없습니다: "
            + ", ".join(missing_keys)
        )
        st.stop()

    try:
        cookie_config = st.secrets["cookies"]
        cookie_password = str(cookie_config["password"])
    except (FileNotFoundError, KeyError):
        st.error("Streamlit Secrets에 [cookies].password 설정이 없습니다.")
        st.stop()
    if len(cookie_password) < 32:
        st.error("[cookies].password는 32자 이상의 임의 문자열이어야 합니다.")
        st.stop()

    cookies = EncryptedCookieManager(
        prefix=COOKIE_PREFIX,
        password=cookie_password,
    )
    set_cookie_expiry(cookies)
    if not cookies.ready():
        st.stop()

    project_url = str(supabase_config["url"])
    publishable_key = str(supabase_config["publishable_key"])
    secret_key = supabase_config.get("secret_key")

    auth_client = create_client(project_url, publishable_key)
    restore_login(auth_client, cookies)
    if "access_token" not in st.session_state:
        render_login(auth_client, cookies)
        return

    try:
        user = auth_client.auth.get_user().user
    except Exception:
        clear_login()
        delete_login_cookie(cookies)
        st.rerun()

    app_config = st.secrets["app"] if "app" in st.secrets else {}
    admin_emails = {
        str(email).strip().casefold()
        for email in app_config.get("admin_emails", [])
    }
    user_email = (user.email or "").casefold()
    is_admin = user_email in admin_emails

    with st.sidebar:
        st.write(f"로그인: `{user.email}`")
        st.caption("관리자" if is_admin else "분류 사용자")
        if st.button("로그아웃", width="stretch"):
            try:
                auth_client.auth.sign_out()
            finally:
                clear_login()
                delete_login_cookie(cookies)
                st.rerun()

    labels = ["도구 분류"]
    if is_admin:
        labels.append("JSON 업로드")
    tabs = st.tabs(labels)
    with tabs[0]:
        render_catalog(auth_client)
    if is_admin:
        with tabs[1]:
            render_upload(project_url=project_url, secret_key=secret_key)


if __name__ == "__main__":
    main()
