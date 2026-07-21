-- WARNING: 이 스크립트를 실행하면 기존 카탈로그와 분류값을 모두 삭제합니다.
drop table if exists public.tool_catalog;

create table public.tool_catalog (
    id bigint generated always as identity primary key,
    server_name text not null,
    tool_name text not null,
    description text not null default '',
    operation text check (operation in ('Read', 'Write', 'Modify')),
    resource_type text check (
        resource_type in ('Private', 'Open-public', 'Targeted-access')
    ),
    public_injection_point boolean,
    output_keys text not null default '',
    input_schema jsonb,
    output_schema jsonb,
    updated_at timestamptz not null default now(),
    updated_by uuid references auth.users(id) on delete set null,
    unique (server_name, tool_name)
);

create index if not exists tool_catalog_server_idx
    on public.tool_catalog(server_name);

alter table public.tool_catalog enable row level security;

revoke all on public.tool_catalog from anon, authenticated;
grant usage on schema public to authenticated;
grant select on public.tool_catalog to authenticated;
grant update (
    operation,
    resource_type,
    public_injection_point,
    output_keys
)
    on public.tool_catalog to authenticated;

drop policy if exists "authenticated users can read tools"
    on public.tool_catalog;
create policy "authenticated users can read tools"
    on public.tool_catalog
    for select
    to authenticated
    using (true);

drop policy if exists "authenticated users can classify tools"
    on public.tool_catalog;
create policy "authenticated users can classify tools"
    on public.tool_catalog
    for update
    to authenticated
    using (true)
    with check (true);

create or replace function public.set_tool_catalog_audit()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
    new.updated_at = now();
    new.updated_by = auth.uid();
    return new;
end;
$$;

drop trigger if exists tool_catalog_audit on public.tool_catalog;
create trigger tool_catalog_audit
before update on public.tool_catalog
for each row
execute function public.set_tool_catalog_audit();
