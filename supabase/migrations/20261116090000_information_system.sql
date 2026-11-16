-- AGROCAST web information system (Activity 3).
-- Business data lives in its own schema. It is not exposed through the Data API: only the Flask
-- server reaches it, using a dedicated login role with the minimum privileges it needs.

create schema if not exists agrocast;
revoke all on schema agrocast from public, anon, authenticated;

create table agrocast.app_user (
  id bigint generated always as identity primary key,
  full_name text not null check (char_length(full_name) between 3 and 80),
  email text not null check (email = lower(email) and char_length(email) <= 120),
  password_hash text not null,
  role text not null default 'producer' check (role in ('admin', 'analyst', 'producer')),
  department text check (char_length(department) <= 60),
  is_active boolean not null default true,
  created_at timestamptz not null default now(),
  last_login_at timestamptz,
  constraint app_user_email_key unique (email)
);

create table agrocast.model_version (
  id bigint generated always as identity primary key,
  version text not null unique,
  algorithm text not null,
  artifact text not null,
  data_cutoff date not null,
  trained_at timestamptz not null,
  metrics jsonb not null default '{}'::jsonb,
  notes text,
  is_active boolean not null default false,
  created_at timestamptz not null default now()
);
-- Only one model version can serve predictions at a time.
create unique index model_version_one_active on agrocast.model_version (is_active) where is_active;

create table agrocast.index_observation (
  series text not null check (series in ('total', 'fertilizantes', 'plaguicidas')),
  month date not null check (extract(day from month) = 1),
  value numeric(10,4) not null check (value > 0),
  source text not null default 'UPRA' check (char_length(source) <= 60),
  entered_by bigint references agrocast.app_user(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (series, month)
);
create index index_observation_entered_by_idx on agrocast.index_observation (entered_by);

create table agrocast.product_price (
  id bigint generated always as identity primary key,
  category text not null check (category in ('Fertilizantes', 'Fungicidas', 'Herbicidas', 'Insecticidas')),
  product text not null check (char_length(product) between 1 and 120),
  presentation text not null check (char_length(presentation) between 1 and 60),
  price_month date not null,
  price numeric(14,2) not null check (price > 0),
  unique (category, product, presentation)
);

create table agrocast.farm (
  id bigint generated always as identity primary key,
  owner_id bigint not null references agrocast.app_user(id) on delete cascade,
  name text not null check (char_length(name) between 2 and 80),
  department text not null check (char_length(department) between 2 and 60),
  municipality text check (char_length(municipality) <= 60),
  main_crop text not null check (char_length(main_crop) between 2 and 60),
  area_ha numeric(10,2) not null check (area_ha > 0 and area_ha <= 100000),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (owner_id, name)
);
create index farm_owner_id_idx on agrocast.farm (owner_id);

create table agrocast.purchase_plan (
  id bigint generated always as identity primary key,
  owner_id bigint not null references agrocast.app_user(id) on delete cascade,
  farm_id bigint not null references agrocast.farm(id) on delete restrict,
  title text not null check (char_length(title) between 3 and 100),
  category text not null check (category in ('Fertilizantes', 'Fungicidas', 'Herbicidas', 'Insecticidas')),
  planned_month date not null check (extract(day from planned_month) = 1),
  status text not null default 'draft'
    check (status in ('draft', 'evaluated', 'approved', 'deferred', 'purchased', 'cancelled')),
  recommendation text check (recommendation in ('buy_now', 'defer', 'as_planned')),
  decision text check (decision in ('buy_now', 'defer', 'as_planned')),
  decision_note text check (char_length(decision_note) <= 400),
  last_inference_id bigint,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  closed_at timestamptz
);
create index purchase_plan_owner_id_idx on agrocast.purchase_plan (owner_id, created_at desc);
create index purchase_plan_farm_id_idx on agrocast.purchase_plan (farm_id);
create index purchase_plan_status_idx on agrocast.purchase_plan (status);

create table agrocast.plan_item (
  id bigint generated always as identity primary key,
  plan_id bigint not null references agrocast.purchase_plan(id) on delete cascade,
  product_price_id bigint not null references agrocast.product_price(id) on delete restrict,
  quantity numeric(10,2) not null check (quantity > 0 and quantity <= 100000),
  unit_price numeric(14,2) not null check (unit_price > 0),
  actual_unit_price numeric(14,2) check (actual_unit_price > 0),
  unique (plan_id, product_price_id)
);
create index plan_item_product_price_id_idx on agrocast.plan_item (product_price_id);

create table agrocast.inference (
  id bigint generated always as identity primary key,
  user_id bigint references agrocast.app_user(id) on delete set null,
  plan_id bigint references agrocast.purchase_plan(id) on delete set null,
  model_version_id bigint not null references agrocast.model_version(id) on delete restrict,
  series text not null check (series in ('total', 'fertilizantes', 'plaguicidas')),
  horizon smallint not null check (horizon between 1 and 6),
  input_source text not null check (input_source in ('system_records', 'manual_entry')),
  input_payload jsonb not null,
  output_payload jsonb not null,
  origin_month date not null,
  target_month date not null,
  predicted_value numeric(10,3) not null,
  change_pct numeric(8,2) not null,
  recommendation text not null check (recommendation in ('buy_now', 'defer', 'as_planned')),
  response_ms integer not null check (response_ms >= 0),
  created_at timestamptz not null default now()
);
create index inference_user_id_idx on agrocast.inference (user_id, created_at desc);
create index inference_plan_id_idx on agrocast.inference (plan_id);
create index inference_model_version_id_idx on agrocast.inference (model_version_id);
create index inference_target_idx on agrocast.inference (series, target_month);

alter table agrocast.purchase_plan
  add constraint purchase_plan_last_inference_fk
  foreign key (last_inference_id) references agrocast.inference(id) on delete set null;
create index purchase_plan_last_inference_id_idx on agrocast.purchase_plan (last_inference_id);

create table agrocast.drift_check (
  id bigint generated always as identity primary key,
  run_by bigint references agrocast.app_user(id) on delete set null,
  model_version_id bigint not null references agrocast.model_version(id) on delete cascade,
  series text not null,
  status text not null check (status in ('ok', 'warning', 'alert')),
  z_score numeric(8,3) not null,
  volatility_ratio numeric(8,3) not null,
  mae_recent numeric(10,3),
  detail jsonb not null,
  created_at timestamptz not null default now()
);
create index drift_check_run_by_idx on agrocast.drift_check (run_by);
create index drift_check_model_version_id_idx on agrocast.drift_check (model_version_id, created_at desc);

create table agrocast.incident (
  id bigint generated always as identity primary key,
  reporter_id bigint not null references agrocast.app_user(id) on delete cascade,
  assigned_to bigint references agrocast.app_user(id) on delete set null,
  kind text not null check (kind in ('bug', 'data_issue', 'model_issue', 'access', 'feedback')),
  severity text not null default 'medium' check (severity in ('low', 'medium', 'high')),
  title text not null check (char_length(title) between 5 and 120),
  description text not null check (char_length(description) between 10 and 2000),
  status text not null default 'open' check (status in ('open', 'in_progress', 'resolved', 'closed')),
  resolution text check (char_length(resolution) <= 2000),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index incident_reporter_id_idx on agrocast.incident (reporter_id);
create index incident_assigned_to_idx on agrocast.incident (assigned_to);
create index incident_status_idx on agrocast.incident (status, created_at desc);

create table agrocast.event_log (
  id bigint generated always as identity primary key,
  level text not null check (level in ('info', 'warning', 'error')),
  event text not null,
  user_id bigint references agrocast.app_user(id) on delete set null,
  method text,
  path text,
  status_code smallint,
  duration_ms integer,
  detail jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index event_log_created_at_idx on agrocast.event_log (created_at desc);
create index event_log_event_idx on agrocast.event_log (event, created_at desc);
create index event_log_user_id_idx on agrocast.event_log (user_id);

-- Row level security is enabled as defence in depth; the Data API roles get no policy at all.
alter table agrocast.app_user enable row level security;
alter table agrocast.model_version enable row level security;
alter table agrocast.index_observation enable row level security;
alter table agrocast.product_price enable row level security;
alter table agrocast.farm enable row level security;
alter table agrocast.purchase_plan enable row level security;
alter table agrocast.plan_item enable row level security;
alter table agrocast.inference enable row level security;
alter table agrocast.drift_check enable row level security;
alter table agrocast.incident enable row level security;
alter table agrocast.event_log enable row level security;
revoke all on all tables in schema agrocast from public, anon, authenticated;
