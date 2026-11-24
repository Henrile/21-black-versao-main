create schema if not exists extensions;
create extension if not exists pgcrypto with schema extensions;

create table if not exists public.blackjack_player_profiles (
    player_id uuid primary key,
    token_hash text not null unique,
    display_name text not null,
    wallet numeric(12,2) not null default 50.00,
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

alter table public.blackjack_player_profiles
    add column if not exists wallet numeric(12,2) not null default 50.00;

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

create or replace function public._bj_mask_dealer(p_state jsonb)
returns jsonb
language plpgsql
immutable
set search_path = public, extensions, pg_temp
as $$
begin
    if p_state->>'over' = 'true' or p_state#>>'{hands,2,1}' is null then
        return p_state;
    end if;
    return jsonb_set(p_state, '{hands,2,1}', '{"rank":"?","suit":"?"}'::jsonb);
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

drop function if exists public.create_blackjack_room(text, text, integer, text, text);
create or replace function public.create_blackjack_room(
    p_name text,
    p_rules text default 'Blackjack',
    p_best_of integer default 1,
    p_theme text default 'Cassino',
    p_profile_token text default null,
    p_bet numeric default 5
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
    hands jsonb := '[[],[],[]]'::jsonb;
    card jsonb;
    player_index integer;
    deal_index integer;
    player_wallet numeric(12,2);
begin
    if coalesce(length(trim(p_name)), 0) not between 1 and 18 then
        raise exception 'O nome deve ter entre 1 e 18 caracteres.';
    end if;
    if p_rules is distinct from 'Blackjack'
       or p_best_of is null or p_best_of not in (1, 3, 5)
       or p_theme is null or p_theme not in ('Velho Oeste', 'Cassino', 'Neon', 'Clássico')
       or p_bet is null or p_bet < 1 or p_bet <> trunc(p_bet) then
        raise exception 'Configuração de partida inválida.';
    end if;

    player_profile := public._bj_get_or_create_profile(p_profile_token, p_name);
    player_one_id := (player_profile->>'player_id')::uuid;
    select wallet into player_wallet
    from public.blackjack_player_profiles
    where player_id = player_one_id
    for update;
    if p_bet > player_wallet then
        raise exception 'Saldo insuficiente para essa aposta.';
    end if;

    loop
        room_code := upper(substr(encode(extensions.gen_random_bytes(5), 'hex'), 1, 6));
        exit when not exists (select 1 from public.blackjack_rooms where code = room_code);
    end loop;

    cards := public._bj_new_deck();
    for deal_index in 1..2
    loop
        for player_index in 0..2
        loop
            card := cards->(jsonb_array_length(cards) - 1);
            cards := cards - (jsonb_array_length(cards) - 1);
            hands := jsonb_set(hands, array[player_index::text], (hands->player_index) || jsonb_build_array(card));
        end loop;
    end loop;

    game_state := jsonb_build_object(
        'player_count', 2,
        'names', jsonb_build_array(trim(p_name), 'Aguardando jogador'),
        'rules', p_rules,
        'theme', p_theme,
        'bets', jsonb_build_array(p_bet, 0),
        'wallets', jsonb_build_array(player_wallet, 0),
        'best_of', p_best_of,
        'wins_needed', p_best_of / 2 + 1,
        'round_number', 1,
        'match_wins', '[0,0]'::jsonb,
        'hands', hands,
        'finished', '[false,false,false]'::jsonb,
        'current_player', 0,
        'history', '[]'::jsonb,
        'dealer_playing', false,
        'over', false,
        'match_over', false,
        'result', ''
    );

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
        'state', public._bj_mask_dealer(game_state)
    );
end;
$$;

drop function if exists public.join_blackjack_room(text, text, text, text);
create or replace function public.join_blackjack_room(
    p_code text,
    p_token text,
    p_name text,
    p_profile_token text default null,
    p_bet numeric default 5
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
    joined_player_id uuid;
    player_two_token text := encode(extensions.gen_random_bytes(24), 'hex');
    player_one_wallet numeric(12,2);
    player_two_wallet numeric(12,2);
    player_one_bet numeric(12,2);
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
    if p_bet is null or p_bet < 1 or p_bet <> trunc(p_bet) then
        raise exception 'A aposta precisa ser de pelo menos $1.';
    end if;
    player_profile := public._bj_get_or_create_profile(p_profile_token, p_name);
    joined_player_id := (player_profile->>'player_id')::uuid;
    if joined_player_id = room.player_one_id then
        raise exception 'Use um perfil diferente para cada jogador.';
    end if;
    player_one_bet := (room.state#>>'{bets,0}')::numeric;
    select wallet into player_one_wallet
    from public.blackjack_player_profiles
    where player_id = room.player_one_id
    for update;
    select wallet into player_two_wallet
    from public.blackjack_player_profiles
    where public.blackjack_player_profiles.player_id = joined_player_id
    for update;
    if player_one_wallet < player_one_bet or player_two_wallet < p_bet then
        raise exception 'Um dos jogadores não tem saldo suficiente para a aposta.';
    end if;
    update public.blackjack_player_profiles
    set wallet = wallet - player_one_bet
    where player_id = room.player_one_id;
    update public.blackjack_player_profiles
    set wallet = wallet - p_bet
    where public.blackjack_player_profiles.player_id = joined_player_id;

    game_state := room.state;
    game_state := jsonb_set(game_state, '{names,1}', to_jsonb(trim(p_name)));
    game_state := jsonb_set(game_state, '{bets,1}', to_jsonb(p_bet));
    game_state := jsonb_set(game_state, '{wallets,0}', to_jsonb(player_one_wallet - player_one_bet));
    game_state := jsonb_set(game_state, '{wallets,1}', to_jsonb(player_two_wallet - p_bet));
    update public.blackjack_rooms
    set state = game_state,
        player_two_id = joined_player_id,
        player_two_token_hash = encode(extensions.digest(player_two_token, 'sha256'), 'hex'),
        updated_at = now()
    where code = room.code;
    return jsonb_build_object(
        'code', room.code,
        'player_token', player_two_token,
        'state', public._bj_mask_dealer(game_state),
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
    response_state jsonb;
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
            'draws', coalesce(one_stats.draws, 0),
            'wallet', one_profile.wallet
        ),
        jsonb_build_object(
            'games_played', coalesce(two_stats.games_played, 0),
            'wins', coalesce(two_stats.wins, 0),
            'losses', coalesce(two_stats.losses, 0),
            'draws', coalesce(two_stats.draws, 0),
            'wallet', two_profile.wallet
        )
    ) into player_stats
    from (select 1) seed
    left join public.blackjack_player_stats one_stats on one_stats.player_id = room.player_one_id
    left join public.blackjack_player_stats two_stats on two_stats.player_id = room.player_two_id
    left join public.blackjack_player_profiles one_profile on one_profile.player_id = room.player_one_id
    left join public.blackjack_player_profiles two_profile on two_profile.player_id = room.player_two_id;

    response_state := public._bj_mask_dealer(room.state);

    return jsonb_build_object(
        'code', room.code,
        'state', response_state,
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
    match_wins jsonb;
    match_over boolean;
    result_text text;
    score_after_draw integer;
    dealer_score integer;
    player_score integer;
    bet_amount numeric(12,2);
    payout numeric(12,2);
    player_wallet numeric(12,2);
    player_natural boolean;
    dealer_natural boolean;
    outcome text;
    round_results text[] := array[]::text[];
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
        if public._bj_score(hands->player_seat, 'Blackjack') >= 21 then
            raise exception 'Você não pode pedir carta com 21 ou mais pontos.';
        end if;
        if jsonb_array_length(cards) = 0 then
            cards := public._bj_new_deck();
        end if;
        card := cards->(jsonb_array_length(cards) - 1);
        cards := cards - (jsonb_array_length(cards) - 1);
        hands := jsonb_set(hands, array[player_seat::text], (hands->player_seat) || jsonb_build_array(card));
        score_after_draw := public._bj_score(hands->player_seat, 'Blackjack');
        result_text := (game_state#>>array['names', player_seat::text]) || ' comprou '
            || (card->>'rank') || (card->>'suit') || ' ('
            || score_after_draw::text || ' pontos).';
        history := history || jsonb_build_array(result_text);
        if score_after_draw >= 21 then
            finished := jsonb_set(finished, array[player_seat::text], 'true'::jsonb);
            if score_after_draw > 21 then
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
        dealer_score := public._bj_score(hands->2, 'Blackjack');
        while dealer_score < 17
        loop
            if jsonb_array_length(cards) = 0 then
                cards := public._bj_new_deck();
            end if;
            card := cards->(jsonb_array_length(cards) - 1);
            cards := cards - (jsonb_array_length(cards) - 1);
            hands := jsonb_set(hands, '{2}', (hands->2) || jsonb_build_array(card));
            history := history || jsonb_build_array(
                'Dealer comprou ' || (card->>'rank') || (card->>'suit') || '.'
            );
            dealer_score := public._bj_score(hands->2, 'Blackjack');
        end loop;
        finished := jsonb_set(finished, '{2}', 'true'::jsonb);
        dealer_natural := jsonb_array_length(hands->2) = 2 and dealer_score = 21;
        for player_index in 0..1
        loop
            player_score := public._bj_score(hands->player_index, 'Blackjack');
            player_natural := jsonb_array_length(hands->player_index) = 2 and player_score = 21;
            bet_amount := (game_state#>>array['bets', player_index::text])::numeric;
            if player_score > 21 or (dealer_natural and not player_natural) then
                payout := 0;
                outcome := 'perdeu';
            elsif player_natural and dealer_natural then
                payout := bet_amount;
                outcome := 'empatou';
            elsif player_natural then
                payout := bet_amount * 2.5;
                outcome := 'venceu com Blackjack natural';
            elsif dealer_score > 21 or player_score > dealer_score then
                payout := bet_amount * 2;
                outcome := 'venceu';
            elsif player_score = dealer_score then
                payout := bet_amount;
                outcome := 'empatou';
            else
                payout := 0;
                outcome := 'perdeu';
            end if;
            update public.blackjack_player_profiles
            set wallet = wallet + payout
            where player_id = case when player_index = 0 then room.player_one_id else room.player_two_id end
            returning wallet into player_wallet;
            game_state := jsonb_set(
                game_state, array['wallets', player_index::text], to_jsonb(player_wallet)
            );
            update public.blackjack_player_stats
            set games_played = games_played + 1,
                wins = wins + case when outcome like 'venceu%' then 1 else 0 end,
                losses = losses + case when outcome = 'perdeu' then 1 else 0 end,
                draws = draws + case when outcome = 'empatou' then 1 else 0 end,
                updated_at = now()
            where player_id = case when player_index = 0 then room.player_one_id else room.player_two_id end;
            if outcome like 'venceu%' then
                game_state := jsonb_set(
                    game_state, array['match_wins', player_index::text],
                    to_jsonb((game_state#>>array['match_wins', player_index::text])::integer + 1)
                );
            end if;
            round_results := array_append(
                round_results,
                (game_state#>>array['names', player_index::text]) || ' ' || outcome
                || ' (' || player_score::text || ' contra ' || dealer_score::text
                || '); carteira: $' || to_char(player_wallet, 'FM999999990.00')
            );
        end loop;
        match_wins := game_state->'match_wins';
        match_over := greatest((match_wins->>0)::integer, (match_wins->>1)::integer)
            >= (game_state->>'wins_needed')::integer;
        result_text := array_to_string(round_results, ' | ');
        game_state := jsonb_set(game_state, '{match_over}', to_jsonb(match_over));
        game_state := jsonb_set(game_state, '{over}', 'true'::jsonb);
        game_state := jsonb_set(game_state, '{result}', to_jsonb(result_text));
    end if;
    game_state := jsonb_set(game_state, '{hands}', hands);
    game_state := jsonb_set(game_state, '{finished}', finished);
    game_state := jsonb_set(game_state, '{history}', history);

    update public.blackjack_rooms
    set state = game_state, deck = cards, updated_at = now()
    where code = room.code;
    return jsonb_build_object('state', public._bj_mask_dealer(game_state));
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
    hands jsonb := '[[],[],[]]'::jsonb;
    card jsonb;
    player_index integer;
    deal_index integer;
    new_round integer;
    bet_amount numeric(12,2);
    player_wallet numeric(12,2);
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

    for player_index in 0..1
    loop
        bet_amount := (game_state#>>array['bets', player_index::text])::numeric;
        update public.blackjack_player_profiles
        set wallet = wallet - bet_amount
        where player_id = case when player_index = 0 then room.player_one_id else room.player_two_id end
          and wallet >= bet_amount
        returning wallet into player_wallet;
        if not found then
            raise exception 'Saldo insuficiente para % continuar apostando.',
                game_state#>>array['names', player_index::text];
        end if;
        game_state := jsonb_set(
            game_state, array['wallets', player_index::text], to_jsonb(player_wallet)
        );
    end loop;

    cards := public._bj_new_deck();
    for deal_index in 1..2
    loop
        for player_index in 0..2
        loop
            card := cards->(jsonb_array_length(cards) - 1);
            cards := cards - (jsonb_array_length(cards) - 1);
            hands := jsonb_set(hands, array[player_index::text], (hands->player_index) || jsonb_build_array(card));
        end loop;
    end loop;
    new_round := case when game_state->>'match_over' = 'true' then 1 else (game_state->>'round_number')::integer + 1 end;
    game_state := jsonb_set(game_state, '{round_number}', to_jsonb(new_round));
    game_state := jsonb_set(game_state, '{match_wins}', case when game_state->>'match_over' = 'true' then '[0,0]'::jsonb else game_state->'match_wins' end);
    game_state := jsonb_set(game_state, '{hands}', hands);
    game_state := jsonb_set(game_state, '{finished}', '[false,false,false]'::jsonb);
    game_state := jsonb_set(game_state, '{current_player}', '0'::jsonb);
    game_state := jsonb_set(game_state, '{history}', '[]'::jsonb);
    game_state := jsonb_set(game_state, '{over}', 'false'::jsonb);
    game_state := jsonb_set(game_state, '{match_over}', 'false'::jsonb);
    game_state := jsonb_set(game_state, '{result}', '""'::jsonb);

    update public.blackjack_rooms
    set state = game_state, deck = cards, updated_at = now()
    where code = room.code;
    return jsonb_build_object('state', public._bj_mask_dealer(game_state));
end;
$$;

revoke all on function public._bj_new_deck() from public, anon, authenticated;
revoke all on function public._bj_score(jsonb, text) from public, anon, authenticated;
revoke all on function public._bj_mask_dealer(jsonb) from public, anon, authenticated;
revoke all on function public._bj_get_or_create_profile(text, text) from public, anon, authenticated;
revoke all on function public.create_blackjack_room(text, text, integer, text, text, numeric) from public;
revoke all on function public.join_blackjack_room(text, text, text, text, numeric) from public;
revoke all on function public.get_blackjack_room(text, text) from public;
revoke all on function public.play_blackjack_turn(text, text, text) from public;
revoke all on function public.next_blackjack_round(text, text) from public;
grant execute on function public.create_blackjack_room(text, text, integer, text, text, numeric) to anon, authenticated;
grant execute on function public.join_blackjack_room(text, text, text, text, numeric) to anon, authenticated;
grant execute on function public.get_blackjack_room(text, text) to anon, authenticated;
grant execute on function public.play_blackjack_turn(text, text, text) to anon, authenticated;
grant execute on function public.next_blackjack_round(text, text) to anon, authenticated;

notify pgrst, 'reload schema';
