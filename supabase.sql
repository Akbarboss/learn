create table if not exists public.learner_progress (
  name text primary key check (name in ('Akbar', 'Abror', 'Muhammadali')),
  learned jsonb not null default '[]'::jsonb,
  log jsonb not null default '[]'::jsonb,
  days jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

alter table public.learner_progress enable row level security;

create policy "wordbook read progress"
  on public.learner_progress for select to anon using (true);

create policy "wordbook insert progress"
  on public.learner_progress for insert to anon with check (true);

create policy "wordbook update progress"
  on public.learner_progress for update to anon using (true) with check (true);
