-- Run once per environment (replace the password; never commit the real one).
-- Creates the login role used by the Flask server. It can only work inside the agrocast schema.
create role agrocast_app login password 'CHANGE-ME-strong-password';
grant usage on schema agrocast to agrocast_app;
grant select, insert, update, delete on all tables in schema agrocast to agrocast_app;
grant usage, select on all sequences in schema agrocast to agrocast_app;
alter default privileges in schema agrocast grant select, insert, update, delete on tables to agrocast_app;
alter default privileges in schema agrocast grant usage, select on sequences to agrocast_app;

-- RLS is enabled on every table; the server role is the only one with a policy.
do $$
declare t text;
begin
  for t in select tablename from pg_tables where schemaname = 'agrocast' loop
    execute format('create policy server_access on agrocast.%I for all to agrocast_app using (true) with check (true)', t);
  end loop;
end $$;
