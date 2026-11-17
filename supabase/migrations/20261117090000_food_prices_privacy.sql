-- Food wholesale prices (DANE SIPSA) refreshed daily, shopping lists and consent to the privacy policy.

alter table agrocast.app_user
  add column consent_at timestamptz,
  add column consent_version text check (char_length(consent_version) <= 20);

create table agrocast.food_product (
  id bigint generated always as identity primary key,
  name text not null unique check (char_length(name) between 2 and 120),
  food_group text not null check (char_length(food_group) <= 60),
  unit text not null default '1 kilogramo' check (char_length(unit) <= 30),
  has_daily boolean not null default false
);

create table agrocast.food_price (
  id bigint generated always as identity primary key,
  product_id bigint not null references agrocast.food_product(id) on delete cascade,
  frequency text not null check (frequency in ('daily', 'monthly', 'national')),
  price_date date not null,
  market text not null check (char_length(market) <= 120),
  city text check (char_length(city) <= 80),
  department text check (char_length(department) <= 80),
  price numeric(14, 2) not null check (price > 0),
  constraint food_price_unique unique (product_id, frequency, price_date, market)
);
create index food_price_product_freq_date_idx on agrocast.food_price (product_id, frequency, price_date desc);
create index food_price_freq_date_idx on agrocast.food_price (frequency, price_date desc);

create table agrocast.food_forecast (
  id bigint generated always as identity primary key,
  product_id bigint not null references agrocast.food_product(id) on delete cascade,
  horizon smallint not null check (horizon between 1 and 12),
  month date not null,
  value numeric(14, 2) not null,
  lo80 numeric(14, 2) not null,
  hi80 numeric(14, 2) not null,
  constraint food_forecast_unique unique (product_id, horizon)
);

create table agrocast.food_outlook (
  product_id bigint primary key references agrocast.food_product(id) on delete cascade,
  last_month date not null,
  last_price numeric(14, 2) not null,
  min_24m numeric(14, 2) not null,
  max_24m numeric(14, 2) not null,
  position_24m numeric(6, 3) not null,
  change_12m_pct numeric(8, 2),
  change_3m_pct numeric(8, 2) not null,
  model text not null check (model in ('naive', 'ets')),
  mae_model numeric(14, 2) not null,
  mae_naive numeric(14, 2) not null,
  recommendation text not null check (recommendation in ('buy', 'wait', 'normal')),
  reason text not null
);

create table agrocast.data_refresh (
  id bigint generated always as identity primary key,
  source text not null,
  status text not null check (status in ('ok', 'error')),
  detail jsonb not null default '{}'::jsonb,
  note text,
  refreshed_at timestamptz not null default now()
);
create index data_refresh_source_idx on agrocast.data_refresh (source, refreshed_at desc);

create table agrocast.shopping_list (
  id bigint generated always as identity primary key,
  owner_id bigint not null references agrocast.app_user(id) on delete cascade,
  name text not null check (char_length(name) between 2 and 80),
  city text check (char_length(city) <= 80),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index shopping_list_owner_id_idx on agrocast.shopping_list (owner_id);

create table agrocast.shopping_item (
  id bigint generated always as identity primary key,
  list_id bigint not null references agrocast.shopping_list(id) on delete cascade,
  product_id bigint not null references agrocast.food_product(id) on delete restrict,
  quantity numeric(10, 2) not null check (quantity > 0 and quantity <= 100000),
  constraint shopping_item_unique unique (list_id, product_id)
);
create index shopping_item_list_id_idx on agrocast.shopping_item (list_id);
create index shopping_item_product_id_idx on agrocast.shopping_item (product_id);

-- Same protection as the rest of the schema: RLS on, no Data API access, only the server role has a policy.
do $$
declare t text;
begin
  foreach t in array array['food_product', 'food_price', 'food_forecast', 'food_outlook', 'data_refresh',
                           'shopping_list', 'shopping_item'] loop
    execute format('alter table agrocast.%I enable row level security', t);
    execute format('revoke all on agrocast.%I from public, anon, authenticated', t);
    if exists (select 1 from pg_roles where rolname = 'agrocast_app') then
      execute format('grant select, insert, update, delete on agrocast.%I to agrocast_app', t);
      execute format('create policy server_access on agrocast.%I for all to agrocast_app using (true) with check (true)', t);
    end if;
  end loop;
  if exists (select 1 from pg_roles where rolname = 'agrocast_app') then
    grant usage, select on all sequences in schema agrocast to agrocast_app;
  end if;
end $$;
