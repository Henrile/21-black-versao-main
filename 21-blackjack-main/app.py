import html
import json
import random
import time
from io import BytesIO
from urllib.parse import urlencode

import qrcode
import streamlit as st
from streamlit.errors import StreamlitSecretNotFoundError


TARGET_SCORE = 21
COMPUTER_DRAW_DELAY = 0.8
SUITS = ["♠", "♥", "♦", "♣"]
RANKS = ["A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K"]
THEMES = {
    "Velho Oeste": {"felt": "#473126", "accent": "#dcae67", "red": "#a94335", "background": "#17110e"},
    "Cassino": {"felt": "#17463e", "accent": "#f3bd62", "red": "#f05a50", "background": "#111722"},
    "Neon": {"felt": "#30265d", "accent": "#f0d94c", "red": "#ff5679", "background": "#11121e"},
    "Clássico": {"felt": "#244c70", "accent": "#f2ca78", "red": "#bc4545", "background": "#17202b"},
}


def make_new_deck() -> list[dict]:
    deck = [{"rank": rank, "suit": suit} for suit in SUITS for rank in RANKS]
    random.shuffle(deck)
    return deck


def create_game(
    player_count: int = 1,
    names: list[str] | None = None,
    rules: str = "Blackjack",
    best_of: int = 1,
    theme: str = "Velho Oeste",
    bet: int = 5,
    wallets: list[float] | None = None,
) -> dict:
    if names is None:
        names = ["Você"] if player_count == 1 else ["Jogador 1", "Jogador 2"]
    if bet < 1 or bet > 50:
        raise ValueError("A aposta precisa estar entre 1 e 50.")
    if wallets is None:
        wallets = [50.0, 50.0]
    if any(wallets[player] < bet for player in range(player_count)):
        raise ValueError("A aposta não pode ser maior que o saldo disponível.")
    game = {
        "player_count": player_count,
        "names": names,
        "rules": rules,
        "theme": theme,
        "wallets": wallets.copy(),
        "bets": [bet, bet],
        "best_of": best_of,
        "wins_needed": best_of // 2 + 1,
        "round_number": 1,
        "match_wins": [0, 0],
        "deck": make_new_deck(),
        "hands": [[] for _ in range(player_count + 1)],
        "finished": [False for _ in range(player_count + 1)],
        "current_player": 0,
        "history": [],
        "dealer_playing": False,
        "over": False,
        "match_over": False,
        "result": "",
    }
    for player in range(player_count + 1):
        if player < player_count:
            game["wallets"][player] -= bet
    for _ in range(2):
        for player in range(player_count + 1):
            draw_card(game, player)
    dealer = player_count
    for player in range(player_count):
        if hand_score(game["hands"][player], rules) == TARGET_SCORE:
            game["finished"][player] = True
            game["history"].append(f'{names[player]} recebeu Blackjack natural.')
    game["finished"][dealer] = False
    if hand_score(game["hands"][dealer], rules) == TARGET_SCORE:
        game["dealer_playing"] = False
        finish_game(game)
    elif all(game["finished"][:player_count]):
        game["dealer_playing"] = True
    else:
        game["current_player"] = next(
            index for index, is_finished in enumerate(game["finished"][:player_count]) if not is_finished
        )
    return game


def card_points(card: dict, rules: str) -> int:
    if card["rank"] == "A":
        return 11 if rules == "Blackjack" else 1
    if card["rank"] in {"J", "Q", "K"}:
        return 10
    return int(card["rank"])


def hand_score(hand: list[dict], rules: str) -> int:
    score = sum(card_points(card, rules) for card in hand)
    if rules == "Blackjack":
        aces = sum(card["rank"] == "A" for card in hand)
        while score > TARGET_SCORE and aces:
            score -= 10
            aces -= 1
    return score


def draw_card(game: dict, player: int) -> dict:
    if not game["deck"]:
        game["deck"] = make_new_deck()
    card = game["deck"].pop()
    game["hands"][player].append(card)
    return card


def format_card(card: dict) -> str:
    return f"{card['rank']}{card['suit']}"


def pixel_character_markup(
    role: str,
    name: str,
    balance: float | None = None,
    bet: float | None = None,
) -> str:
    sprites = {
        "player": (
            "JOGADOR",
            ["..HHHH..", ".HHHHHH.", ".SSSSSS.", ".SE.S.E.", "..SSSS..",
             "..TTTT..", ".TTTTTT.", ".T.TT.T.", ".LL..LL.", ".LL..LL."],
        ),
        "dealer": (
            "DEALER 8-BIT",
            ["..CCCC..", ".CCCCCC.", ".SSSSSS.", ".SE.S.E.", "..SSSS..",
             "..BBBB..", ".BBBBBB.", ".B.BB.B.", "..PPPP..", "..P..P.."],
        ),
        "rival": (
            "RIVAL",
            ["..HHHH..", ".HHHHHH.", ".SSSSSS.", ".SE.S.E.", "..SSSS..",
             "..VVVV..", ".VVVVVV.", ".V.VV.V.", "..LL.LL.", "..LL.LL."],
        ),
    }
    label, rows = sprites[role]
    cells = "".join(
        f'<span class="pixel-cell pixel-{pixel.lower()}"></span>'
        for row in rows
        for pixel in row
    )
    balance_markup = (
        f'<small>CARTEIRA ${balance:.2f} · APOSTA ${bet:.2f}</small>'
        if balance is not None
        else ""
    )
    return (
        f'<div class="table-character {role}" aria-label="{label}: {html.escape(name)}">'
        f'<div class="pixel-sprite" aria-hidden="true">{cells}</div>'
        f'<div class="character-caption"><span>{label}</span>'
        f'<strong>{html.escape(name)}</strong>{balance_markup}</div></div>'
    )


def finish_game(game: dict) -> None:
    dealer_index = game["player_count"]
    dealer_score = hand_score(game["hands"][dealer_index], game["rules"])
    dealer_natural = len(game["hands"][dealer_index]) == 2 and dealer_score == TARGET_SCORE
    dealer_bust = dealer_score > TARGET_SCORE
    outcomes = []
    for player in range(game["player_count"]):
        score = hand_score(game["hands"][player], game["rules"])
        natural = len(game["hands"][player]) == 2 and score == TARGET_SCORE
        bet = game["bets"][player]
        if score > TARGET_SCORE or (dealer_natural and not natural):
            outcome, payout = "perdeu", 0
        elif natural and dealer_natural:
            outcome, payout = "empatou", bet
        elif natural:
            outcome, payout = "venceu com Blackjack natural", bet * 2.5
        elif dealer_bust or score > dealer_score:
            outcome, payout = "venceu", bet * 2
        elif score == dealer_score:
            outcome, payout = "empatou", bet
        else:
            outcome, payout = "perdeu", 0
        game["wallets"][player] += payout
        if outcome.startswith("venceu"):
            game["match_wins"][player] += 1
        outcomes.append(
            f'{game["names"][player]} {outcome} ({score} contra {dealer_score}); '
            f'carteira: ${game["wallets"][player]:.2f}'
        )
    game["round_outcomes"] = outcomes
    game["result"] = " | ".join(outcomes)
    game["match_over"] = max(game["match_wins"]) >= game["wins_needed"]
    game["over"] = True


def advance_turn(game: dict) -> None:
    next_player = next(
        (
            index for index, is_finished in enumerate(game["finished"][:game["player_count"]])
            if not is_finished
        ),
        None,
    )
    if next_player is None:
        game["dealer_playing"] = True
    else:
        game["current_player"] = next_player


def take_turn(action: str) -> None:
    game = st.session_state.game
    player = game["current_player"]

    if action == "buy":
        card = draw_card(game, player)
        score = hand_score(game["hands"][player], game["rules"])
        game["history"].append(f"{game['names'][player]} comprou {format_card(card)} ({score} pontos).")
        if score >= TARGET_SCORE:
            game["finished"][player] = True
            if score == TARGET_SCORE:
                game["history"].append(f"{game['names'][player]} chegou a 21!")
            else:
                game["history"].append(f"{game['names'][player]} estourou 21.")
            advance_turn(game)
    else:
        game["finished"][player] = True
        game["history"].append(f"{game['names'][player]} parou.")
        advance_turn(game)


def start_next_round(game: dict, bet: int | None = None) -> None:
    if bet is not None:
        if bet < 1:
            raise ValueError("A aposta precisa ser de pelo menos $1.")
        for player in range(game["player_count"]):
            if game["wallets"][player] < bet:
                raise ValueError(f'{game["names"][player]} não tem saldo suficiente para essa aposta.')
        game["bets"] = [bet, bet]
    for player in range(game["player_count"]):
        if game["wallets"][player] < game["bets"][player]:
            raise ValueError(f'{game["names"][player]} não tem saldo suficiente para a aposta.')
    for player in range(game["player_count"]):
        game["wallets"][player] -= game["bets"][player]
    game["round_number"] += 1
    game["deck"] = make_new_deck()
    game["hands"] = [[] for _ in range(game["player_count"] + 1)]
    game["finished"] = [False for _ in range(game["player_count"] + 1)]
    game["current_player"] = 0
    game["history"] = []
    game["dealer_playing"] = False
    game["over"] = False
    game["result"] = ""
    for _ in range(2):
        for player in range(game["player_count"] + 1):
            draw_card(game, player)
    for player in range(game["player_count"]):
        if hand_score(game["hands"][player], game["rules"]) == TARGET_SCORE:
            game["finished"][player] = True
            game["history"].append(f'{game["names"][player]} recebeu Blackjack natural.')
    dealer = game["player_count"]
    if hand_score(game["hands"][dealer], game["rules"]) == TARGET_SCORE or all(
        game["finished"][:game["player_count"]]
    ):
        game["dealer_playing"] = True
    else:
        game["current_player"] = next(
            index for index, is_finished in enumerate(game["finished"][:game["player_count"]]) if not is_finished
        )


def dealer_should_hit(game: dict) -> bool:
    dealer_score = hand_score(game["hands"][game["player_count"]], game["rules"])
    return dealer_score < 17


def get_supabase_client(url: str, anon_key: str):
    from supabase import create_client

    return create_client(url, anon_key)


def call_supabase(client, function_name: str, parameters: dict) -> dict:
    response = client.rpc(function_name, parameters).execute()
    if not isinstance(response.data, dict):
        raise ValueError(f"Resposta inválida recebida de {function_name}.")
    return response.data


def render_sound_control(event_key: str) -> None:
    safe_event_key = (
        json.dumps(event_key)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    st.iframe(
        f"""
        <div class="sound-controls">
            <button id="sound-toggle" type="button" aria-label="Ativar efeitos" title="Ativar efeitos">
                <span id="sound-icon" aria-hidden="true">🔇</span>
            </button>
            <button id="music-toggle" type="button" aria-label="Ativar música 8-bit" title="Ativar música 8-bit">
                <span aria-hidden="true">🎵</span>
            </button>
        </div>
        <span id="sound-status" aria-live="polite"></span>
        <style>
        body {{ margin: 0; font-family: sans-serif; }}
        .sound-controls {{ display: flex; gap: 6px; }}
        #sound-toggle {{
            display: grid; place-items: center; width: 40px; height: 40px;
            padding: 0; border: 1px solid #66747c; border-radius: 8px;
            background: #202a35; color: #fff2d5; font-size: 20px; cursor: pointer;
        }}
        #music-toggle {{
            display: grid; place-items: center; width: 40px; height: 40px;
            padding: 0; border: 1px solid #66747c; border-radius: 8px;
            background: #202a35; color: #fff2d5; font-size: 17px; cursor: pointer;
        }}
        #sound-toggle:hover, #music-toggle:hover {{ background: #344452; }}
        #music-toggle.active {{ background: #17463e; border-color: #f3bd62; box-shadow: 0 0 12px rgba(243,189,98,.45); }}
        </style>
        <script>
        const eventKey = {safe_event_key};
        const enabledKey = "blackjack-sounds-enabled";
        const previousKey = "blackjack-sounds-last-event";
        const musicKey = "blackjack-music-enabled";
        const button = document.getElementById("sound-toggle");
        const icon = document.getElementById("sound-icon");
        const musicButton = document.getElementById("music-toggle");
        const status = document.getElementById("sound-status");
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        let context = null;
        let musicTimer = null;
        let musicStep = 0;

        function updateSoundButton(enabled) {{
            icon.textContent = enabled ? "🔊" : "🔇";
            const label = enabled ? "Desativar efeitos" : "Ativar efeitos";
            button.setAttribute("aria-label", label);
            button.title = label;
        }}

        function getContext() {{
            if (!AudioContextClass) {{
                status.textContent = " Áudio não suportado neste navegador.";
                return null;
            }}
            if (!context) context = new AudioContextClass();
            if (context.state === "suspended") context.resume();
            return context;
        }}

        function playTone(frequency, duration = 0.16, volume = 0.12) {{
            const audio = getContext();
            if (!audio) return;
            const oscillator = audio.createOscillator();
            const gain = audio.createGain();
            oscillator.type = "square";
            oscillator.frequency.value = frequency;
            gain.gain.setValueAtTime(volume, audio.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.001, audio.currentTime + duration);
            oscillator.connect(gain);
            gain.connect(audio.destination);
            oscillator.start();
            oscillator.stop(audio.currentTime + duration);
        }}

        button.addEventListener("click", () => {{
            const enabled = localStorage.getItem(enabledKey) !== "true";
            localStorage.setItem(enabledKey, String(enabled));
            updateSoundButton(enabled);
            if (enabled) {{
                localStorage.setItem(previousKey, eventKey);
                playTone(660);
            }}
            status.textContent = "";
        }});

        function playMusicNote() {{
            const melody = [261.63, 329.63, 392.00, 523.25, 392.00, 329.63, 293.66, 392.00];
            const bass = [130.81, 130.81, 146.83, 146.83];
            playTone(melody[musicStep % melody.length], 0.11, 0.018);
            if (musicStep % 4 === 0) playTone(bass[Math.floor(musicStep / 4) % bass.length], 0.18, 0.012);
            musicStep += 1;
        }}

        function setMusic(enabled) {{
            musicButton.classList.toggle("active", enabled);
            musicButton.setAttribute("aria-label", enabled ? "Desativar música 8-bit" : "Ativar música 8-bit");
            musicButton.title = enabled ? "Desativar música 8-bit" : "Ativar música 8-bit";
            if (enabled) {{
                getContext();
                playMusicNote();
                if (!musicTimer) musicTimer = setInterval(playMusicNote, 230);
            }} else if (musicTimer) {{
                clearInterval(musicTimer);
                musicTimer = null;
            }}
        }}

        musicButton.addEventListener("click", () => {{
            const enabled = localStorage.getItem(musicKey) !== "true";
            localStorage.setItem(musicKey, String(enabled));
            setMusic(enabled);
        }});

        const enabled = localStorage.getItem(enabledKey) === "true";
        updateSoundButton(enabled);
        if (enabled && localStorage.getItem(previousKey) !== eventKey) {{
            localStorage.setItem(previousKey, eventKey);
            playTone(440);
        }}
        setMusic(localStorage.getItem(musicKey) === "true");
        </script>
        """,
        height=42,
    )


def render_online_room(client) -> None:
    @st.fragment(run_every="1s")
    def room_view() -> None:
        from postgrest.exceptions import APIError
        import httpx

        room_code = st.session_state.online_room_code
        player_token = st.session_state.online_player_token
        try:
            response = call_supabase(
                client,
                "get_blackjack_room",
                {"p_code": room_code, "p_token": player_token},
            )
        except (APIError, httpx.HTTPError) as error:
            st.error(f"Conexão com a sala interrompida. Tentando novamente: {error}")
            return

        game = response["state"]
        if len(game.get("hands", [])) != 3 or "bets" not in game:
            st.warning("Esta sala foi criada antes das regras de apostas. Crie uma nova sala para jogar.")
            return
        seat = response["my_seat"]
        palette = THEMES[game["theme"]]
        st.markdown(
            f"""
            <style>
            :root {{ --felt: {palette['felt']}; --red: {palette['red']}; }}
            [data-testid="stAppViewContainer"] {{
                background-color: {palette['background']};
                background-image: repeating-linear-gradient(0deg, rgba(255,255,255,.018) 0 1px, transparent 0 4px),
                    linear-gradient(135deg, {palette['background']} 0%, #10141d 55%, {palette['felt']} 180%);
            }}
            div[data-testid="stVerticalBlockBorderWrapper"] {{
                background: repeating-linear-gradient(135deg, rgba(255,255,255,.018) 0 2px, transparent 0 8px),
                    linear-gradient(145deg, {palette['felt']}, {palette['background']} 120%);
                border-color: {palette['accent']} !important;
            }}
            .eyebrow {{ color: {palette['accent']}; }}
            </style>
            """,
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<div class="eyebrow">Sala {html.escape(room_code)} &nbsp; / &nbsp; '
            f'Jogador {seat + 1}</div>',
            unsafe_allow_html=True,
        )
        if st.button("Desconectar desta sala", key=f"disconnect-{room_code}"):
            st.session_state.pop("online_room_code", None)
            st.session_state.pop("online_player_token", None)
            st.session_state.pop("online_guest_token", None)
            st.rerun()
        invite_token = st.session_state.get("online_guest_token")
        profile_token = st.session_state.get("online_profile_token")
        if profile_token:
            with st.expander("Seu perfil e estatísticas persistentes"):
                st.caption("Guarde este segredo para manter suas estatísticas em outras salas.")
                st.code(profile_token)
        if invite_token and game["names"][1] == "Aguardando jogador":
            invite_url = (
                f"{st.session_state.online_public_app_url}?"
                f"{urlencode({'r': room_code, 'i': invite_token})}"
            )
            qr_image = qrcode.make(invite_url)
            qr_buffer = BytesIO()
            qr_image.save(qr_buffer, format="PNG")
            st.markdown("### Convide o segundo jogador")
            st.image(qr_buffer.getvalue(), width=220, caption="Escaneie para abrir o convite")
            st.code(invite_url, language=None)
            st.caption(
                "Envie este link ou QR apenas ao jogador convidado. O convite é de uso único; "
                "depois que ele entrar, o segredo da sala muda."
            )
            with st.expander("Credencial privada para reconectar"):
                st.caption("Guarde este segredo. Não o compartilhe com o outro jogador.")
                st.code(player_token)
        if game["names"][1] == "Aguardando jogador":
            st.info("Aguardando o segundo jogador entrar na sala.")

        stats = response["stats"]
        game["wallets"] = [float(player_stats["wallet"] or 0) for player_stats in stats]
        stats_columns = st.columns(2)
        for index, column in enumerate(stats_columns):
            player_stats = stats[index]
            column.caption(
                f'{game["names"][index]} — {player_stats["games_played"]} partidas, '
                f'{player_stats["wins"]} vitórias, {player_stats["losses"]} derrotas, '
                f'{player_stats["draws"]} empates · carteira ${game["wallets"][index]:.2f}'
            )

        st.markdown(
            f'<div class="match-score">RODADA {game["round_number"]} &nbsp; · &nbsp; '
            f'SÉRIE {game["match_wins"][0]} — {game["match_wins"][1]} &nbsp; · &nbsp; '
            f'{html.escape(game["rules"].upper())}</div>',
            unsafe_allow_html=True,
        )
        render_sound_control(
            f'{room_code}:{game["round_number"]}:{len(game["history"])}:'
            f'{game["history"][-1] if game["history"] else "início"}'
        )
        with st.container(border=True):
            st.markdown(f'<div class="eyebrow">Mesa online &nbsp; / &nbsp; Alvo: {TARGET_SCORE}</div>', unsafe_allow_html=True)
            score_columns = st.columns(3)
            for index, column in enumerate(score_columns):
                is_dealer = index == 2
                hidden_hole_card = is_dealer and not game["over"]
                visible_hand = game["hands"][index][:1] if hidden_hole_card else game["hands"][index]
                score = hand_score(visible_hand, game["rules"])
                status = (
                    "Dealer" if is_dealer else
                    "Estourou" if score > TARGET_SCORE else
                    "Parou" if game["finished"][index] else
                    "Na vez" if index == game["current_player"] else "Aguardando"
                )
                suit_color = "#db4c4c" if index else "#1d4243"
                hand_markup = "".join(
                    '<div class="playing-card card-back" aria-label="Carta fechada"></div>'
                    if hidden_hole_card and card_index == 1
                    else f'<div class="playing-card {"red-suit" if card["suit"] in {"♥", "♦"} else ""}" style="--card-index:{card_index}">'
                    f'<span>{html.escape(card["rank"])}</span><span>{html.escape(card["suit"])}</span></div>'
                    for card_index, card in enumerate(game["hands"][index])
                )
                character_role = "dealer" if is_dealer else "player" if index == 0 else "rival"
                name = "Dealer" if is_dealer else game["names"][index]
                balance = None if is_dealer else game["wallets"][index]
                bet = None if is_dealer else game["bets"][index]
                panel_score = "?" if hidden_hole_card else f"{score:02d}"
                column.markdown(
                    f"""
                    <div class="score-panel" style="--suit: {suit_color}">
                        {pixel_character_markup(character_role, name, balance, bet)}
                        <div class="card-corner"><span>♠</span><span>{panel_score}</span></div>
                        <div class="card-value">{panel_score}</div>
                        <div class="score-state">{status}</div>
                    </div>
                    <div class="playing-hand">{hand_markup}</div>
                    """,
                    unsafe_allow_html=True,
                )

            if game["over"]:
                if "venceu" in game["result"]:
                    st.success(game["result"])
                else:
                    st.info(game["result"])
                if st.button("Nova rodada", type="primary"):
                    try:
                        call_supabase(
                            client,
                            "next_blackjack_round",
                            {"p_code": room_code, "p_token": player_token},
                        )
                    except (APIError, httpx.HTTPError) as error:
                        st.error(f"Não foi possível iniciar a próxima rodada: {error}")
                    else:
                        st.rerun(scope="fragment")
            elif game["names"][1] != "Aguardando jogador":
                if seat == game["current_player"]:
                    buy_column, stand_column = st.columns(2)
                    current_score = hand_score(game["hands"][seat], game["rules"])
                    if buy_column.button(
                        "Pedir carta", type="primary", use_container_width=True, disabled=current_score >= TARGET_SCORE
                    ):
                        try:
                            call_supabase(
                                client,
                                "play_blackjack_turn",
                                {"p_code": room_code, "p_token": player_token, "p_action": "buy"},
                            )
                        except (APIError, httpx.HTTPError) as error:
                            st.error(f"Não foi possível comprar uma carta: {error}")
                        else:
                            st.rerun(scope="fragment")
                    if stand_column.button("Parar", use_container_width=True):
                        try:
                            call_supabase(
                                client,
                                "play_blackjack_turn",
                                {"p_code": room_code, "p_token": player_token, "p_action": "stand"},
                            )
                        except (APIError, httpx.HTTPError) as error:
                            st.error(f"Não foi possível encerrar sua vez: {error}")
                        else:
                            st.rerun(scope="fragment")
                else:
                    st.info(f"Aguardando a jogada de {game['names'][game['current_player']]}.")

            if game["history"]:
                with st.expander("Últimas jogadas", expanded=True):
                    for event in reversed(game["history"]):
                        st.write(f"- {event}")

    room_view()


st.set_page_config(page_title="21 | Blackjack", page_icon="♠", layout="wide")
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Bungee&family=DM+Mono:wght@400;500&display=swap');
    :root {
        --felt: #17463e;
        --red: #f05a50;
        --cream: #fff2d5;
        --ink: #f8f0df;
        --muted: #b8c9bd;
    }
    [data-testid="stAppViewContainer"] {
        background-color: #111722;
        background-image: repeating-linear-gradient(0deg, rgba(255,255,255,.018) 0 1px, transparent 1px 4px),
            linear-gradient(135deg, #161d29 0%, #10141d 55%, #241923 100%);
        color: var(--ink);
    }
    [data-testid="stHeader"] { background: transparent; }
    [data-testid="stMainBlockContainer"] { max-width: 1080px; padding-top: 2.2rem; }
    h1, h2, h3 { color: var(--cream); }
    h1 {
        font-family: 'Bungee', Impact, sans-serif !important;
        font-size: 4rem !important; line-height: 1.05 !important;
        text-shadow: 3px 3px 0 var(--red), -2px -2px 0 #36b8a2;
        margin-bottom: .25rem !important;
    }
    h1 [data-testid="stHeadingLink"] { display: none; }
    h3 { font-family: 'Bungee', Impact, sans-serif !important; font-size: 1.15rem !important; }
    .eyebrow {
        color: #f3bd62; font-family: 'DM Mono', monospace; font-size: .75rem;
        font-weight: 500; letter-spacing: .12em; text-transform: uppercase;
    }
    .tagline { color: var(--muted); font-family: 'DM Mono', monospace; margin: 0 0 1.5rem; }
    div[data-testid="stVerticalBlockBorderWrapper"] {
        background: repeating-linear-gradient(135deg, rgba(255,255,255,.018) 0 2px, transparent 2px 8px),
            linear-gradient(145deg, #1c5147, #123b36 75%);
        border: 2px solid #e0ac58 !important; border-radius: 12px !important;
        box-shadow: 0 9px 0 #091018, 0 13px 35px rgba(0,0,0,.38);
        padding: 1.1rem 1.35rem 1.35rem;
    }
    .score-panel {
        position: relative; overflow: hidden; display: flex; flex-direction: column;
        justify-content: space-between; background: var(--cream); color: #18252b;
        border: 3px solid #f9e7bd; border-radius: 9px; min-height: 205px;
        padding: 1rem 1.15rem; box-shadow: 0 5px 0 rgba(4,18,20,.48);
    }
    .score-panel::after {
        content: ''; position: absolute; right: -14px; bottom: -30px; width: 110px; height: 110px;
        border: 13px solid rgba(29,55,58,.07); border-radius: 50%;
    }
    .table-character {
        position: relative; z-index: 1; display: flex; align-items: center; gap: .65rem;
        width: fit-content; margin-bottom: .35rem; animation: character-idle 2.4s ease-in-out infinite;
    }
    .pixel-sprite {
        display: grid; grid-template-columns: repeat(8, 6px); grid-auto-rows: 6px;
        image-rendering: pixelated; filter: drop-shadow(2px 3px 0 rgba(4,18,20,.22));
    }
    .pixel-cell { width: 6px; height: 6px; }
    .pixel-h { background: #4b2d1f; }
    .pixel-s { background: #efb583; }
    .pixel-e { background: #17252a; }
    .pixel-t { background: #d94f4a; }
    .pixel-l { background: #3154a4; }
    .pixel-c { background: #f3bd62; }
    .pixel-b { background: #26384a; }
    .pixel-p { background: #26384a; }
    .pixel-v { background: #7748a8; }
    .character-caption { display: grid; gap: .18rem; }
    .character-caption span {
        color: #638078; font: 500 .59rem 'DM Mono', monospace; letter-spacing: .1em;
    }
    .character-caption strong {
        max-width: 180px; overflow: hidden; color: #18252b;
        font: 500 .75rem 'DM Mono', monospace; text-overflow: ellipsis; white-space: nowrap;
    }
    .character-caption small { color: #638078; font: 500 .62rem 'DM Mono', monospace; }
    @keyframes character-idle { 0%, 100% { transform: translateY(0); } 50% { transform: translateY(-2px); } }
    .card-corner, .card-bottom { color: var(--suit); font-family: 'DM Mono', monospace; font-weight: 500; }
    .card-corner { display: flex; gap: .45rem; font-size: .9rem; }
    .card-value { color: #17252a; font-family: 'Bungee', Impact, sans-serif; font-size: 3.5rem; line-height: 1; }
    .score-state { color: var(--suit); font-family: 'DM Mono', monospace; font-size: .75rem; font-weight: 500; text-transform: uppercase; }
    .card-bottom { position: absolute; right: 1rem; bottom: .7rem; font-size: 2.8rem; opacity: .8; }
    .playing-hand {
        display: flex; flex-wrap: wrap; gap: .55rem; margin: .9rem 0 1.1rem;
        min-height: 76px; perspective: 800px;
    }
    .playing-card {
        display: grid; place-content: space-between; width: 55px; height: 76px; padding: 6px;
        border-radius: 6px; background: #fff2d5; color: #203238; font: 500 .9rem 'DM Mono', monospace;
        border: 1px solid rgba(255,255,255,.75);
        box-shadow: 0 4px 0 rgba(4,18,20,.45), 0 9px 18px rgba(0,0,0,.18);
        transform-origin: 50% 120%;
        animation: card-deal .48s cubic-bezier(.2,.75,.25,1.25) both;
        animation-delay: calc(var(--card-index, 0) * 90ms);
        transition: transform .18s ease, box-shadow .18s ease;
        position: relative; overflow: hidden;
    }
    .playing-card::after {
        content: ''; position: absolute; inset: -45% -80%;
        background: linear-gradient(120deg, transparent 42%, rgba(255,255,255,.65) 50%, transparent 58%);
        transform: translateX(-70%) rotate(8deg);
        animation: card-shine 1.1s ease-out both;
        animation-delay: calc(160ms + var(--card-index, 0) * 90ms);
    }
    .playing-card:hover { transform: translateY(-7px) rotate(-1deg); box-shadow: 0 8px 0 rgba(4,18,20,.42), 0 15px 22px rgba(0,0,0,.25); }
    .playing-card.red-suit { color: #d54450; }
    .playing-card.card-back {
        border: 2px solid #fff2d5;
        background: repeating-linear-gradient(45deg, #193c70 0 4px, #315c91 4px 7px);
    }
    .playing-card span:last-child { justify-self: end; font-size: 1.25rem; }
    @keyframes card-deal {
        0% { opacity: 0; transform: translate(-70px,-95px) rotateY(180deg) rotateZ(-13deg) scale(.72); }
        55% { opacity: 1; transform: translate(8px,-9px) rotateY(-12deg) rotateZ(3deg) scale(1.05); }
        100% { opacity: 1; transform: translate(0,0) rotateY(0) rotateZ(0) scale(1); }
    }
    @keyframes card-shine { 0% { opacity: 0; transform: translateX(-70%) rotate(8deg); } 30% { opacity: .7; } 100% { opacity: 0; transform: translateX(70%) rotate(8deg); } }
    .match-score { color: #f3bd62; font: 500 .8rem 'DM Mono', monospace; margin-top: .7rem; }
    div[data-testid="stRadio"] label p, div[data-testid="stRadio"] legend {
        color: var(--cream) !important; font-family: 'DM Mono', monospace !important;
    }
    div[data-testid="stRadio"] [role="radiogroup"] { gap: .5rem; }
    div.stButton > button[kind="primary"] {
        background: var(--red); border: 0; color: #fff5dd; font-weight: 700;
        box-shadow: 0 4px 0 #a93239; text-transform: uppercase;
    }
    div.stButton > button[kind="primary"]:hover {
        background: #ff7063; color: #fff5dd; border: 0; transform: translateY(-1px);
    }
    div.stButton > button:not([kind="primary"]) {
        background: #202a35; border: 1px solid #66747c; color: var(--cream);
        font-family: 'DM Mono', monospace;
    }
    div[data-testid="stExpander"] { border-color: #455563; background: rgba(17,23,34,.72); }
    @media (max-width: 640px) {
        [data-testid="stMainBlockContainer"] { padding: 1.2rem .8rem 2rem; }
        h1 { font-size: 2.8rem !important; }
        div[data-testid="stVerticalBlockBorderWrapper"] { padding: .8rem; }
        .score-panel { min-height: 170px; padding: .8rem; }
        .card-value { font-size: 2.7rem; }
    }
    @media (prefers-reduced-motion: reduce) {
        .table-character { animation: none; }
        .playing-card, .playing-card::after { animation-duration: .01ms; animation-iteration-count: 1; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

if not st.session_state.get("welcome_complete"):
    welcome_column = st.columns([1, 2, 1])[1]
    with welcome_column:
        st.markdown('<div class="eyebrow">Cassino de bolso &nbsp; / &nbsp; Blackjack</div>', unsafe_allow_html=True)
        st.title("Entre no salão")
        st.markdown('<p class="tagline">ESCOLHA SEU NOME PARA ENTRAR NA MESA.</p>', unsafe_allow_html=True)
        with st.container(border=True):
            with st.form("welcome_screen"):
                entered_name = st.text_input(
                    "Nome do jogador",
                    max_chars=18,
                    placeholder="Digite seu nome",
                    key="welcome_player_name",
                )
                submitted = st.form_submit_button(
                    "Entrar na mesa",
                    type="primary",
                    use_container_width=True,
                )
            if submitted:
                player_name = entered_name.strip()
                if not player_name:
                    st.error("Digite seu nome para continuar.")
                else:
                    st.session_state.player_name = player_name
                    st.session_state.welcome_complete = True
                    st.session_state.setup_name_one = player_name
                    st.session_state.setup_name_first = player_name
                    st.session_state.online_create_name = player_name
                    st.session_state.game = create_game(names=[player_name])
                    st.rerun()
    st.stop()

if (
    "game" not in st.session_state
    or "wallets" not in st.session_state.game
    or len(st.session_state.game["hands"]) != st.session_state.game["player_count"] + 1
):
    st.session_state.game = create_game(names=[st.session_state.get("player_name", "Você")])

game = st.session_state.game
palette = THEMES[game["theme"]]
st.markdown(
    f"""
    <style>
    :root {{ --felt: {palette['felt']}; --red: {palette['red']}; }}
    [data-testid="stAppViewContainer"] {{
        background-color: {palette['background']};
        background-image: repeating-linear-gradient(0deg, rgba(255,255,255,.018) 0 1px, transparent 1px 4px),
            linear-gradient(135deg, {palette['background']} 0%, #10141d 55%, {palette['felt']} 180%);
    }}
    div[data-testid="stVerticalBlockBorderWrapper"] {{
        background: repeating-linear-gradient(135deg, rgba(255,255,255,.018) 0 2px, transparent 2px 8px),
            linear-gradient(145deg, {palette['felt']}, {palette['background']} 120%);
        border-color: {palette['accent']} !important;
    }}
    .eyebrow {{ color: {palette['accent']}; }}
    </style>
    """,
    unsafe_allow_html=True,
)

if st.query_params.get("r") and st.query_params.get("i"):
    st.session_state.play_mode = "Sala online"

play_mode = st.radio(
    "Modo de jogo",
    ["Local", "Sala online"],
    horizontal=True,
    key="play_mode",
)
if play_mode == "Sala online":
    st.markdown('<div class="eyebrow">Cassino de bolso &nbsp; / &nbsp; Blackjack</div>', unsafe_allow_html=True)
    st.title("Vinte e um")
    st.markdown('<p class="tagline">APOSTE. PEÇA. PARE. VENÇA O DEALER.</p>', unsafe_allow_html=True)
    try:
        from postgrest.exceptions import APIError
        import httpx
    except ImportError:
        st.error("Instale as dependências do projeto para habilitar salas online.")
        st.stop()

    try:
        supabase_config = st.secrets.get("supabase", {})
    except StreamlitSecretNotFoundError:
        supabase_config = {}
    supabase_url = supabase_config.get("url")
    supabase_anon_key = supabase_config.get("anon_key")
    if not supabase_url or not supabase_anon_key:
        st.error(
            "Salas online indisponíveis: configure `supabase.url` e `supabase.anon_key` "
            "nos segredos do Streamlit."
        )
        st.stop()
    st.session_state.online_public_app_url = supabase_config.get(
        "public_app_url",
        "https://21-black-versao-main-f3leeeayx9nksixb9syhbx.streamlit.app",
    ).rstrip("/")
    invite_code = st.query_params.get("r")
    invite_token = st.query_params.get("i")
    if invite_code and invite_token:
        st.session_state.pending_invite_code = str(invite_code).upper()
        st.session_state.pending_invite_token = str(invite_token)
        st.query_params.clear()

    try:
        supabase = get_supabase_client(supabase_url, supabase_anon_key)
    except ImportError:
        st.error("Instale as dependências do projeto para habilitar salas online.")
        st.stop()

    if not st.session_state.get("online_room_code") or not st.session_state.get("online_player_token"):
        if st.session_state.get("pending_invite_code"):
            join_tab, create_tab, reconnect_tab = st.tabs(
                ["Entrar com convite", "Criar sala", "Reconectar"]
            )
        else:
            create_tab, join_tab, reconnect_tab = st.tabs(
                ["Criar sala", "Entrar com convite", "Reconectar"]
            )
        with create_tab:
            with st.form("create_online_room"):
                room_name = st.text_input(
                    "Seu nome",
                    max_chars=18,
                    key="online_create_name",
                )
                profile_token = st.text_input(
                    "Token de perfil existente (opcional)",
                    value=st.session_state.get("online_profile_token", ""),
                    type="password",
                    help="Deixe vazio para criar um perfil persistente novo.",
                )
                room_rules = st.selectbox("Regra", ["Blackjack"])
                room_bet = st.number_input("Aposta inicial", min_value=1, value=5, step=1)
                room_best_of = st.selectbox(
                    "Série", [1, 3, 5],
                    format_func=lambda count: "Uma rodada" if count == 1 else f"Melhor de {count}",
                )
                room_theme = st.selectbox("Tema", list(THEMES))
                create_submitted = st.form_submit_button("Criar sala", type="primary")
            if create_submitted:
                try:
                    created = call_supabase(
                        supabase,
                        "create_blackjack_room",
                        {
                            "p_name": room_name.strip(),
                            "p_rules": room_rules,
                            "p_best_of": room_best_of,
                            "p_theme": room_theme,
                            "p_profile_token": profile_token.strip() or None,
                            "p_bet": room_bet,
                        },
                    )
                except (APIError, httpx.HTTPError) as error:
                    st.error(f"Não foi possível criar a sala: {error}")
                else:
                    st.session_state.online_room_code = created["code"]
                    st.session_state.online_player_token = created["player_token"]
                    st.session_state.online_guest_token = created["guest_token"]
                    st.session_state.online_profile_token = created["profile_token"]
                    st.rerun()
        with join_tab:
            with st.form("join_online_room"):
                join_code = st.text_input(
                    "Código da sala",
                    value=st.session_state.get("pending_invite_code", ""),
                    max_chars=6,
                ).strip().upper()
                join_token = st.text_input(
                    "Convite privado do jogador 2",
                    value=st.session_state.get("pending_invite_token", ""),
                    type="password",
                )
                join_name = st.text_input("Seu nome", value="Jogador 2", max_chars=18)
                join_bet = st.number_input("Sua aposta inicial", min_value=1, value=5, step=1)
                join_profile_token = st.text_input(
                    "Token de perfil existente (opcional)",
                    value=st.session_state.get("online_profile_token", ""),
                    type="password",
                    help="Deixe vazio para criar um perfil persistente novo.",
                )
                join_submitted = st.form_submit_button("Entrar na sala", type="primary")
            if join_submitted:
                try:
                    joined = call_supabase(
                        supabase,
                        "join_blackjack_room",
                        {
                            "p_code": join_code,
                            "p_token": join_token,
                            "p_name": join_name.strip(),
                            "p_profile_token": join_profile_token.strip() or None,
                            "p_bet": join_bet,
                        },
                    )
                except (APIError, httpx.HTTPError) as error:
                    st.error(f"Não foi possível entrar na sala: {error}")
                else:
                    st.session_state.online_room_code = joined["code"]
                    st.session_state.online_player_token = joined["player_token"]
                    st.session_state.online_guest_token = ""
                    st.session_state.online_profile_token = joined["profile_token"]
                    st.session_state.pop("pending_invite_code", None)
                    st.session_state.pop("pending_invite_token", None)
                    st.rerun()
        with reconnect_tab:
            with st.form("reconnect_online_room"):
                reconnect_code = st.text_input("Código da sala", max_chars=6).strip().upper()
                reconnect_token = st.text_input("Seu segredo privado de jogador", type="password")
                reconnect_submitted = st.form_submit_button("Reconectar", type="primary")
            if reconnect_submitted:
                try:
                    call_supabase(
                        supabase,
                        "get_blackjack_room",
                        {"p_code": reconnect_code, "p_token": reconnect_token},
                    )
                except (APIError, httpx.HTTPError) as error:
                    st.error(f"Não foi possível reconectar: {error}")
                else:
                    st.session_state.online_room_code = reconnect_code
                    st.session_state.online_player_token = reconnect_token
                    st.session_state.online_guest_token = ""
                    st.rerun()
    else:
        render_online_room(supabase)
    st.stop()

with st.expander("Configurar partida", expanded=True):
    settings_columns = st.columns(3)
    with settings_columns[0]:
        player_count = st.radio(
            "Modo",
            options=[1, 2],
            format_func=lambda count: "Contra dealer" if count == 1 else "Dois jogadores",
            key="setup_player_count",
        )
    with settings_columns[1]:
        rules = st.selectbox("Regra", ["Blackjack"], key="setup_rules")
        best_of = st.selectbox(
            "Série",
            [1, 3, 5],
            format_func=lambda count: "Uma rodada" if count == 1 else f"Melhor de {count}",
            key="setup_best_of",
        )
    with settings_columns[2]:
        theme = st.selectbox("Tema", list(THEMES), key="setup_theme")
        if player_count == 1:
            first_name = st.text_input(
                "Seu nome",
                max_chars=18,
                key="setup_name_one",
            )
            second_name = "Dealer"
        else:
            first_name = st.text_input(
                "Nome do jogador 1",
                max_chars=18,
                key="setup_name_first",
            )
            second_name = st.text_input("Nome do jogador 2", value="Jogador 2", max_chars=18, key="setup_name_second")
    bet_amount = st.number_input(
        "Aposta inicial por jogador",
        min_value=1,
        max_value=50,
        value=5,
        step=1,
        help="Cada jogador começa com $50. O valor apostado é descontado da carteira.",
        key="setup_bet",
    )
    if st.button("Aplicar e iniciar", type="primary", use_container_width=True):
        names = [first_name.strip() or "Jogador 1"]
        if player_count == 2:
            names.append(second_name.strip() or "Jogador 2")
        st.session_state.game = create_game(
            player_count, names, rules, best_of, theme, int(bet_amount)
        )
        st.rerun()

game = st.session_state.game
if st.button(
    "Reiniciar jogo",
    help="Começa uma nova série, restaura as carteiras para $50 e distribui duas cartas novamente.",
    key="reset_local_game",
):
    st.session_state.game = create_game(
        game["player_count"],
        game["names"],
        game["rules"],
        game["best_of"],
        game["theme"],
        game["bets"][0],
    )
    st.rerun()

render_sound_control(
    f'local:{game["round_number"]}:{len(game["history"])}:'
    f'{game["history"][-1] if game["history"] else "início"}'
)
st.markdown('<div class="eyebrow">Cassino de bolso &nbsp; / &nbsp; Blackjack</div>', unsafe_allow_html=True)
st.title("Vinte e um")
st.markdown('<p class="tagline">APOSTE. PEÇA. PARE. VENÇA O DEALER.</p>', unsafe_allow_html=True)
st.markdown(
    f'<div class="match-score">RODADA {game["round_number"]} &nbsp; · &nbsp; SÉRIE {game["match_wins"][0]} — {game["match_wins"][1]} &nbsp; · &nbsp; {game["rules"].upper()}</div>',
    unsafe_allow_html=True,
)

with st.container(border=True):
    st.markdown(f'<div class="eyebrow">Mesa 01 &nbsp; / &nbsp; Alvo: {TARGET_SCORE}</div>', unsafe_allow_html=True)
    score_columns = st.columns(game["player_count"] + 1)
    for index, column in enumerate(score_columns):
        is_dealer = index == game["player_count"]
        score = hand_score(game["hands"][index], game["rules"])
        hidden_hole_card = is_dealer and not game["over"]
        status = (
            "Dealer" if is_dealer else
            "Estourou" if score > TARGET_SCORE else
            "Parou" if game["finished"][index] else
            "Na vez" if index == game["current_player"] else "Aguardando"
        )
        suit_color = "#db4c4c" if index else "#1d4243"
        hand_markup = "".join(
            '<div class="playing-card card-back" aria-label="Carta fechada"></div>'
            if hidden_hole_card and card_index == 1
            else f'<div class="playing-card {"red-suit" if card["suit"] in {"♥", "♦"} else ""}" style="--card-index:{card_index}"><span>{card["rank"]}</span><span>{card["suit"]}</span></div>'
            for card_index, card in enumerate(game["hands"][index])
        )
        character_role = "dealer" if is_dealer else "player" if index == 0 else "rival"
        name = "Dealer" if is_dealer else game["names"][index]
        balance = None if is_dealer else game["wallets"][index]
        bet = None if is_dealer else game["bets"][index]
        panel_score = "?" if hidden_hole_card else f"{score:02d}"
        column.markdown(
            f"""
            <div class="score-panel" style="--suit: {suit_color}">
                {pixel_character_markup(character_role, name, balance, bet)}
                <div class="card-corner"><span>♠</span><span>{panel_score}</span></div>
                <div class="card-value">{panel_score}</div>
                <div class="score-state">{status}</div>
            </div>
            <div class="playing-hand">{hand_markup}</div>
            """,
            unsafe_allow_html=True,
        )

    computer_update = st.empty()
    if game["over"]:
        if any("venceu" in outcome for outcome in game["round_outcomes"]):
            st.success(game["result"])
        else:
            st.info(game["result"])
        available_bet = min(game["wallets"][:game["player_count"]])
        if available_bet < 1:
            st.info("Saldo insuficiente para outra rodada.")
            next_bet = 1
        else:
            next_bet = int(
                st.number_input(
                    "Aposta da próxima rodada por jogador",
                    min_value=1,
                    max_value=int(available_bet),
                    value=min(int(game["bets"][0]), int(available_bet)),
                    step=1,
                    key=f"next_bet_{game['round_number']}",
                )
            )
        if game["match_over"]:
            if st.button("Nova série", type="primary", disabled=available_bet < 1):
                st.session_state.game = create_game(
                    game["player_count"], game["names"], game["rules"], game["best_of"],
                    game["theme"], next_bet, game["wallets"],
                )
                st.rerun()
        elif st.button("Próxima rodada", type="primary", disabled=available_bet < 1):
            try:
                start_next_round(game, next_bet)
            except ValueError as error:
                st.error(str(error))
            else:
                st.rerun()
    elif game["dealer_playing"]:
        st.subheader("Vez do Dealer")
        computer_update.info("O dealer está jogando...")
    elif game["current_player"] < game["player_count"]:
        player_name = game["names"][game["current_player"]]
        st.subheader(f"Vez de {player_name}")
        buy_column, stand_column = st.columns(2)
        if buy_column.button("Pedir carta", type="primary", use_container_width=True, disabled=hand_score(game["hands"][game["current_player"]], game["rules"]) >= TARGET_SCORE):
            take_turn("buy")
            st.rerun()
        if stand_column.button("Parar", use_container_width=True):
            take_turn("stand")
            st.rerun()

if game["history"]:
    with st.expander("Últimas jogadas", expanded=True):
        for event in reversed(game["history"]):
            st.write(f"- {event}")

if game["dealer_playing"]:
    dealer = game["player_count"]
    if dealer_should_hit(game):
        card = draw_card(game, dealer)
        score = hand_score(game["hands"][dealer], game["rules"])
        game["history"].append(f"Dealer pediu {format_card(card)} ({score} pontos).")
        computer_update.info(f"Dealer pediu {format_card(card)}. Total: {score} pontos.")
        time.sleep(COMPUTER_DRAW_DELAY)
        st.rerun()
    else:
        game["dealer_playing"] = False
        game["finished"][dealer] = True
        finish_game(game)
        st.rerun()
