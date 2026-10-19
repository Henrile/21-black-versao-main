create schema if not exists extensions;
create extension if not exists pgcrypto with schema extensions;

create table if not exists public.blackjack_player_profiles (
    player_id uuid primary key,
    token_hash text not null unique,
    display_name text not null,
    created_at timestamptz not null default now()
);

create table if not exists public.blackjack_player_stats (
    player_id uuid primary key references public.blackjack_player_profiles(player_id),
    display_name text not null default 'Jogador',
    games_played integer not null default 0,
    wins integer not null default 0,
    losses integer not null default 0,
    draws integer not null default 0,
    updated_at timestamptz not null default now()
);

create table if not exists public.blackjack_rooms (
    code text primary key,
    state jsonb not null,
    deck jsonb not null,
    player_one_id uuid not null references public.blackjack_player_profiles(player_id),
    player_two_id uuid references public.blackjack_player_profiles(player_id),
    player_one_token_hash text not null,
    player_two_token_hash text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

insert into public.blackjack_player_profiles(player_id, token_hash, display_name)
select
    player_id,
    encode(extensions.digest(extensions.gen_random_bytes(24), 'sha256'), 'hex'),
    display_name
from public.blackjack_player_stats
on conflict (player_id) do nothing;

alter table public.blackjack_rooms alter column player_two_id drop not null;
alter table public.blackjack_rooms drop constraint if exists blackjack_rooms_player_one_id_fkey;
alter table public.blackjack_rooms drop constraint if exists blackjack_rooms_player_two_id_fkey;
alter table public.blackjack_player_stats drop constraint if exists blackjack_player_stats_player_id_fkey;
alter table public.blackjack_player_stats
    add constraint blackjack_player_stats_player_id_fkey
    foreign key (player_id) references public.blackjack_player_profiles(player_id);
alter table public.blackjack_rooms
    add constraint blackjack_rooms_player_one_id_fkey
    foreign key (player_one_id) references public.blackjack_player_profiles(player_id);
alter table public.blackjack_rooms
    add constraint blackjack_rooms_player_two_id_fkey
    foreign key (player_two_id) references public.blackjack_player_profiles(player_id);

alter table public.blackjack_rooms enable row level security;
alter table public.blackjack_player_profiles enable row level security;
alter table public.blackjack_player_stats enable row level security;
revoke all on public.blackjack_rooms, public.blackjack_player_profiles, public.blackjack_player_stats
    from public, anon, authenticated;

create or replace function public._bj_new_deck()
returns jsonb
language sql
volatile
set search_path = public, extensions, pg_temp
as $$
    select jsonb_agg(jsonb_build_object('rank', ranks.rank, 'suit', suits.suit) order by random())
    from unnest(array['A','2','3','4','5','6','7','8','9','10','J','Q','K']) as ranks(rank)
    cross join unnest(array['♠','♥','♦','♣']) as suits(suit);
$$;

create or replace function public._bj_score(p_hand jsonb, p_rules text)
returns integer
language plpgsql
immutable
set search_path = public, extensions, pg_temp
as $$
declare
    card jsonb;
    score integer := 0;
    aces integer := 0;
    rank text;
begin
    for card in select value from jsonb_array_elements(p_hand)
    loop
        rank := card->>'rank';
        if rank = 'A' then
            aces := aces + 1;
            score := score + case when p_rules = 'Blackjack' then 11 else 1 end;
        elsif rank in ('J', 'Q', 'K') then
            score := score + 10;
        else
            score := score + rank::integer;
        end if;
    end loop;
    while p_rules = 'Blackjack' and score > 21 and aces > 0
    loop
        score := score - 10;
        aces := aces - 1;
    end loop;
    return score;
end;
$$;

create or replace function public._bj_get_or_create_profile(p_token text, p_name text)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions, pg_temp
as $$
declare
    profile_id uuid;
    profile_token text := p_token;
begin
    if coalesce(length(trim(p_name)), 0) not between 1 and 18 then
        raise exception 'O nome deve ter entre 1 e 18 caracteres.';
    end if;
    if coalesce(trim(p_token), '') = '' then
        profile_token := encode(extensions.gen_random_bytes(24), 'hex');
        profile_id := gen_random_uuid();
        insert into public.blackjack_player_profiles(player_id, token_hash, display_name)
        values (profile_id, encode(extensions.digest(profile_token, 'sha256'), 'hex'), trim(p_name));
        insert into public.blackjack_player_stats(player_id, display_name)
        values (profile_id, trim(p_name));
    else
        select player_id into profile_id
        from public.blackjack_player_profiles
        where token_hash = encode(extensions.digest(p_token, 'sha256'), 'hex')
        for update;
        if not found then
            raise exception 'Perfil persistente inválido.';
        end if;
        update public.blackjack_player_profiles
        set display_name = trim(p_name)
        where player_id = profile_id;
        update public.blackjack_player_stats
        set display_name = trim(p_name), updated_at = now()
        where player_id = profile_id;
    end if;
    return jsonb_build_object('player_id', profile_id, 'profile_token', profile_token);
end;
$$;

create or replace function public.create_blackjack_room(
    p_name text,
    p_rules text default 'Blackjack',
    p_best_of integer default 1,
    p_theme text default 'Cassino',
    p_profile_token text default null
)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions, pg_temp
as $$
declare
    room_code text;
    player_one_token text := encode(extensions.gen_random_bytes(24), 'hex');
    player_two_token text := encode(extensions.gen_random_bytes(24), 'hex');
    player_profile jsonb;
    player_one_id uuid;
    player_two_id uuid;
    game_state jsonb;
    cards jsonb;
    hands jsonb := '[[],[]]'::jsonb;
    card jsonb;
    player_index integer;
    deal_index integer;
begin
    if coalesce(length(trim(p_name)), 0) not between 1 and 18 then
        raise exception 'O nome deve ter entre 1 e 18 caracteres.';
    end if;
    if p_rules is null or p_rules not in ('Blackjack', '21 simples')
       or p_best_of is null or p_best_of not in (1, 3, 5)
       or p_theme is null or p_theme not in ('Cassino', 'Neon', 'Clássico') then
        raise exception 'Configuração de partida inválida.';
    end if;

    loop
        room_code := upper(substr(encode(extensions.gen_random_bytes(5), 'hex'), 1, 6));
        exit when not exists (select 1 from public.blackjack_rooms where code = room_code);
    end loop;

    cards := public._bj_new_deck();
    for deal_index in 1..2
    loop
        player_index := (deal_index - 1) % 2;
        card := cards->(jsonb_array_length(cards) - 1);
        cards := cards - (jsonb_array_length(cards) - 1);
        hands := jsonb_set(hands, array[player_index::text], (hands->player_index) || jsonb_build_array(card));
    end loop;

    game_state := jsonb_build_object(
        'player_count', 2,
        'names', jsonb_build_array(trim(p_name), 'Aguardando jogador'),
        'rules', p_rules,
        'theme', p_theme,
        'best_of', p_best_of,
        'wins_needed', p_best_of / 2 + 1,
        'round_number', 1,
        'match_wins', '[0,0]'::jsonb,
        'hands', hands,
        'finished', '[false,false]'::jsonb,
        'current_player', 0,
        'history', '[]'::jsonb,
        'dealer_playing', false,
        'over', false,
        'match_over', false,
        'result', ''
    );

    player_profile := public._bj_get_or_create_profile(p_profile_token, p_name);
    player_one_id := (player_profile->>'player_id')::uuid;
    insert into public.blackjack_rooms(
        code, state, deck, player_one_id, player_two_id,
        player_one_token_hash, player_two_token_hash
    )
    values (
        room_code, game_state, cards, player_one_id, player_two_id,
        encode(extensions.digest(player_one_token, 'sha256'), 'hex'),
        encode(extensions.digest(player_two_token, 'sha256'), 'hex')
    );

    return jsonb_build_object(
        'code', room_code,
        'player_token', player_one_token,
        'guest_token', player_two_token,
        'profile_token', player_profile->>'profile_token',
        'state', game_state
    );
end;
$$;

create or replace function public.join_blackjack_room(
    p_code text,
    p_token text,
    p_name text,
    p_profile_token text default null
)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions, pg_temp
as $$
declare
    room public.blackjack_rooms%rowtype;
    game_state jsonb;
    player_profile jsonb;
    player_id uuid;
begin
    if coalesce(length(trim(p_name)), 0) not between 1 and 18 then
        raise exception 'O nome deve ter entre 1 e 18 caracteres.';
    end if;
    select * into room from public.blackjack_rooms where code = upper(trim(p_code)) for update;
    if not found then
        raise exception 'Sala não encontrada.';
    end if;
    if encode(extensions.digest(coalesce(p_token, ''), 'sha256'), 'hex') <> room.player_two_token_hash then
        raise exception 'Convite inválido.';
    end if;
    if room.player_two_id is not null then
        raise exception 'O segundo assento já foi ocupado.';
    end if;
    player_profile := public._bj_get_or_create_profile(p_profile_token, p_name);
    player_id := (player_profile->>'player_id')::uuid;
    game_state := room.state;
    game_state := jsonb_set(game_state, '{names,1}', to_jsonb(trim(p_name)));
    update public.blackjack_rooms
    set state = game_state, player_two_id = player_id, updated_at = now()
    where code = room.code;
    return jsonb_build_object(
        'code', room.code,
        'state', game_state,
        'profile_token', player_profile->>'profile_token'
    );
end;
$$;

create or replace function public.get_blackjack_room(p_code text, p_token text)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions, pg_temp
as $$
declare
    room public.blackjack_rooms%rowtype;
    player_seat integer;
    player_stats jsonb;
begin
    select * into room from public.blackjack_rooms where code = upper(trim(p_code));
    if not found then
        raise exception 'Sala não encontrada.';
    end if;
    if encode(extensions.digest(coalesce(p_token, ''), 'sha256'), 'hex') = room.player_one_token_hash then
        player_seat := 0;
    elsif encode(extensions.digest(coalesce(p_token, ''), 'sha256'), 'hex') = room.player_two_token_hash then
        player_seat := 1;
    else
        raise exception 'Credencial de jogador inválida.';
    end if;

    select jsonb_build_array(
        jsonb_build_object(
            'games_played', coalesce(one_stats.games_played, 0),
            'wins', coalesce(one_stats.wins, 0),
            'losses', coalesce(one_stats.losses, 0),
            'draws', coalesce(one_stats.draws, 0)
        ),
        jsonb_build_object(
            'games_played', coalesce(two_stats.games_played, 0),
            'wins', coalesce(two_stats.wins, 0),
            'losses', coalesce(two_stats.losses, 0),
            'draws', coalesce(two_stats.draws, 0)
        )
    ) into player_stats
    from (select 1) seed
    left join public.blackjack_player_stats one_stats on one_stats.player_id = room.player_one_id
    left join public.blackjack_player_stats two_stats on two_stats.player_id = room.player_two_id;

    return jsonb_build_object(
        'code', room.code,
        'state', room.state,
        'my_seat', player_seat,
        'stats', coalesce(player_stats, '[]'::jsonb),
        'updated_at', room.updated_at
    );
end;
$$;

create or replace function public.play_blackjack_turn(p_code text, p_token text, p_action text)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions, pg_temp
as $$
declare
    room public.blackjack_rooms%rowtype;
    player_seat integer;
    game_state jsonb;
    cards jsonb;
    card jsonb;
    player_index integer;
    next_player integer;
    hands jsonb;
    finished jsonb;
    history jsonb;
    scores integer[];
    winners integer[];
    best_score integer;
    winner_index integer;
    match_wins jsonb;
    match_over boolean;
    result_text text;
    score_after_draw integer;
begin
    if p_action is null or p_action not in ('buy', 'stand') then
        raise exception 'Jogada inválida.';
    end if;
    select * into room from public.blackjack_rooms where code = upper(trim(p_code)) for update;
    if not found then
        raise exception 'Sala não encontrada.';
    end if;
    if encode(extensions.digest(coalesce(p_token, ''), 'sha256'), 'hex') = room.player_one_token_hash then
        player_seat := 0;
    elsif encode(extensions.digest(coalesce(p_token, ''), 'sha256'), 'hex') = room.player_two_token_hash then
        player_seat := 1;
    else
        raise exception 'Credencial de jogador inválida.';
    end if;

    game_state := room.state;
    if game_state->>'over' = 'true' or game_state->>'current_player' <> player_seat::text
       or game_state#>>'{names,1}' = 'Aguardando jogador' then
        raise exception 'Não é a sua vez ou a partida ainda não começou.';
    end if;

    hands := game_state->'hands';
    finished := game_state->'finished';
    history := game_state->'history';
    cards := room.deck;
    if p_action = 'buy' then
        if jsonb_array_length(cards) = 0 then
            cards := public._bj_new_deck();
        end if;
        card := cards->(jsonb_array_length(cards) - 1);
        cards := cards - (jsonb_array_length(cards) - 1);
        hands := jsonb_set(hands, array[player_seat::text], (hands->player_seat) || jsonb_build_array(card));
        score_after_draw := public._bj_score(hands->player_seat, game_state->>'rules');
        result_text := (game_state#>>array['names', player_seat::text]) || ' comprou '
            || (card->>'rank') || (card->>'suit') || ' ('
            || score_after_draw::text || ' pontos).';
        history := history || jsonb_build_array(result_text);
        if score_after_draw >= 21 then
            finished := jsonb_set(finished, array[player_seat::text], 'true'::jsonb);
            if score_after_draw = 21 then
                history := history || jsonb_build_array((game_state#>>array['names', player_seat::text]) || ' chegou a 21!');
            else
                history := history || jsonb_build_array((game_state#>>array['names', player_seat::text]) || ' estourou 21.');
            end if;
        end if;
    else
        finished := jsonb_set(finished, array[player_seat::text], 'true'::jsonb);
        history := history || jsonb_build_array((game_state#>>array['names', player_seat::text]) || ' parou.');
    end if;

    next_player := player_seat;
    if finished->>player_seat = 'true' then
        next_player := null;
        for player_index in 0..1
        loop
            if finished->>player_index = 'false' then
                next_player := player_index;
                exit;
            end if;
        end loop;
    end if;
    game_state := jsonb_set(game_state, '{hands}', hands);
    game_state := jsonb_set(game_state, '{finished}', finished);
    game_state := jsonb_set(game_state, '{history}', history);

    if next_player is not null then
        game_state := jsonb_set(game_state, '{current_player}', to_jsonb(next_player));
    else
        scores := array[
            public._bj_score(hands->0, game_state->>'rules'),
            public._bj_score(hands->1, game_state->>'rules')
        ];
        winners := array[]::integer[];
        best_score := -1;
        for player_index in 0..1
        loop
            if scores[player_index + 1] <= 21 then
                if scores[player_index + 1] > best_score then
                    best_score := scores[player_index + 1];
                    winners := array[player_index];
                elsif scores[player_index + 1] = best_score then
                    winners := array_append(winners, player_index);
                end if;
            end if;
        end loop;

        match_wins := game_state->'match_wins';
        if cardinality(winners) = 1 then
            winner_index := winners[1];
            match_wins := jsonb_set(
                match_wins, array[winner_index::text],
                to_jsonb((match_wins->>winner_index)::integer + 1)
            );
            result_text := (game_state#>>array['names', winner_index::text])
                || ' venceu a rodada com ' || best_score::text || ' pontos!';
        elsif cardinality(winners) > 1 then
            result_text := 'Empate!';
        else
            result_text := 'Todos estouraram. Ninguém venceu desta vez.';
        end if;

        for player_index in 0..1
        loop
            update public.blackjack_player_stats
            set games_played = games_played + 1,
                wins = wins + case when cardinality(winners) = 1 and winners[1] = player_index then 1 else 0 end,
                losses = losses + case when cardinality(winners) = 1 and winners[1] <> player_index then 1 else 0 end,
                draws = draws + case when cardinality(winners) <> 1 then 1 else 0 end,
                updated_at = now()
            where player_id = case when player_index = 0 then room.player_one_id else room.player_two_id end;
        end loop;

        match_over := greatest((match_wins->>0)::integer, (match_wins->>1)::integer)
            >= (game_state->>'wins_needed')::integer;
        if match_over then
            winner_index := case when (match_wins->>0)::integer > (match_wins->>1)::integer then 0 else 1 end;
            result_text := (game_state#>>array['names', winner_index::text]) || ' venceu a série!';
        end if;
        game_state := jsonb_set(game_state, '{match_wins}', match_wins);
        game_state := jsonb_set(game_state, '{match_over}', to_jsonb(match_over));
        game_state := jsonb_set(game_state, '{over}', 'true'::jsonb);
        game_state := jsonb_set(game_state, '{result}', to_jsonb(result_text));
    end if;

    update public.blackjack_rooms
    set state = game_state, deck = cards, updated_at = now()
    where code = room.code;
    return jsonb_build_object('state', game_state);
end;
$$;

create or replace function public.next_blackjack_round(p_code text, p_token text)
returns jsonb
language plpgsql
security definer
set search_path = public, extensions, pg_temp
as $$
declare
    room public.blackjack_rooms%rowtype;
    player_seat integer;
    game_state jsonb;
    cards jsonb;
    hands jsonb := '[[],[]]'::jsonb;
    card jsonb;
    player_index integer;
    deal_index integer;
    new_round integer;
begin
    select * into room from public.blackjack_rooms where code = upper(trim(p_code)) for update;
    if not found then
        raise exception 'Sala não encontrada.';
    end if;
    if encode(extensions.digest(coalesce(p_token, ''), 'sha256'), 'hex') = room.player_one_token_hash then
        player_seat := 0;
    elsif encode(extensions.digest(coalesce(p_token, ''), 'sha256'), 'hex') = room.player_two_token_hash then
        player_seat := 1;
    else
        raise exception 'Credencial de jogador inválida.';
    end if;
    game_state := room.state;
    if game_state->>'over' <> 'true' or game_state#>>'{names,1}' = 'Aguardando jogador' then
        raise exception 'A rodada ainda não terminou.';
    end if;

    cards := public._bj_new_deck();
    for deal_index in 1..2
    loop
        player_index := (deal_index - 1) % 2;
        card := cards->(jsonb_array_length(cards) - 1);
        cards := cards - (jsonb_array_length(cards) - 1);
        hands := jsonb_set(hands, array[player_index::text], (hands->player_index) || jsonb_build_array(card));
    end loop;
    new_round := case when game_state->>'match_over' = 'true' then 1 else (game_state->>'round_number')::integer + 1 end;
    game_state := jsonb_set(game_state, '{round_number}', to_jsonb(new_round));
    game_state := jsonb_set(game_state, '{match_wins}', case when game_state->>'match_over' = 'true' then '[0,0]'::jsonb else game_state->'match_wins' end);
    game_state := jsonb_set(game_state, '{hands}', hands);
    game_state := jsonb_set(game_state, '{finished}', '[false,false]'::jsonb);
    game_state := jsonb_set(game_state, '{current_player}', '0'::jsonb);
    game_state := jsonb_set(game_state, '{history}', '[]'::jsonb);
    game_state := jsonb_set(game_state, '{over}', 'false'::jsonb);
    game_state := jsonb_set(game_state, '{match_over}', 'false'::jsonb);
    game_state := jsonb_set(game_state, '{result}', '""'::jsonb);

    update public.blackjack_rooms
    set state = game_state, deck = cards, updated_at = now()
    where code = room.code;
    return jsonb_build_object('state', game_state);
end;
$$;

revoke all on function public._bj_new_deck() from public, anon, authenticated;
revoke all on function public._bj_score(jsonb, text) from public, anon, authenticated;
revoke all on function public._bj_get_or_create_profile(text, text) from public, anon, authenticated;
revoke all on function public.create_blackjack_room(text, text, integer, text, text) from public;
revoke all on function public.join_blackjack_room(text, text, text, text) from public;
revoke all on function public.get_blackjack_room(text, text) from public;
revoke all on function public.play_blackjack_turn(text, text, text) from public;
revoke all on function public.next_blackjack_round(text, text) from public;
grant execute on function public.create_blackjack_room(text, text, integer, text, text) to anon, authenticated;
grant execute on function public.join_blackjack_room(text, text, text, text) to anon, authenticated;
grant execute on function public.get_blackjack_room(text, text) to anon, authenticated;
grant execute on function public.play_blackjack_turn(text, text, text) to anon, authenticated;
grant execute on function public.next_blackjack_round(text, text) to anon, authenticated;
