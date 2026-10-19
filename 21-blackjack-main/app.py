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
    theme: str = "Cassino",
) -> dict:
    if names is None:
        names = ["Você", "Computador"] if player_count == 1 else ["Jogador 1", "Jogador 2"]
    game = {
        "player_count": player_count,
        "names": names,
        "rules": rules,
        "theme": theme,
        "best_of": best_of,
        "wins_needed": best_of // 2 + 1,
        "round_number": 1,
        "match_wins": [0, 0],
        "deck": make_new_deck(),
        "hands": [[], []],
        "finished": [False, False],
        "current_player": 0,
        "history": [],
        "dealer_playing": False,
        "over": False,
        "match_over": False,
        "result": "",
    }
    for player in range(2):
        draw_card(game, player)
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


def finish_game(game: dict) -> None:
    scores = [hand_score(hand, game["rules"]) for hand in game["hands"]]
    eligible = [
        index
        for index, score in enumerate(scores)
        if score <= TARGET_SCORE
    ]
    if not eligible:
        game["result"] = "Todos estouraram. Ninguém venceu desta vez."
        winners = []
    else:
        best_score = max(scores[index] for index in eligible)
        winners = [index for index in eligible if scores[index] == best_score]
        if len(winners) > 1:
            game["result"] = "Empate!"
        else:
            game["result"] = f"{game['names'][winners[0]]} venceu a rodada com {best_score} pontos!"

    if len(winners) == 1:
        game["match_wins"][winners[0]] += 1
    game["match_over"] = max(game["match_wins"]) >= game["wins_needed"]
    if game["match_over"]:
        match_winner = game["match_wins"].index(max(game["match_wins"]))
        game["result"] = f"{game['names'][match_winner]} venceu a série!"
    game["over"] = True


def advance_turn(game: dict) -> None:
    if game["player_count"] == 1:
        if hand_score(game["hands"][0], game["rules"]) > TARGET_SCORE:
            finish_game(game)
            return
        game["dealer_playing"] = True
        return

    next_player = next(
        (index for index, is_finished in enumerate(game["finished"]) if not is_finished),
        None,
    )
    if next_player is None:
        finish_game(game)
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


def start_next_round(game: dict) -> None:
    game["round_number"] += 1
    game["deck"] = make_new_deck()
    game["hands"] = [[], []]
    game["finished"] = [False, False]
    game["current_player"] = 0
    game["history"] = []
    game["dealer_playing"] = False
    game["over"] = False
    game["result"] = ""
    for player in range(2):
        draw_card(game, player)


def computer_should_hit(game: dict) -> bool:
    computer_score = hand_score(game["hands"][1], game["rules"])
    player_score = hand_score(game["hands"][0], game["rules"])
    if computer_score < 17:
        return True
    return player_score <= TARGET_SCORE and computer_score < player_score < TARGET_SCORE


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
        <button id="sound-toggle" type="button" aria-label="Ativar som" title="Ativar som">
            <span id="sound-icon" aria-hidden="true">🔇</span>
        </button>
        <span id="sound-status" aria-live="polite"></span>
        <style>
        body {{ margin: 0; font-family: sans-serif; }}
        #sound-toggle {{
            display: grid; place-items: center; width: 40px; height: 40px;
            padding: 0; border: 1px solid #66747c; border-radius: 8px;
            background: #202a35; color: #fff2d5; font-size: 20px; cursor: pointer;
        }}
        #sound-toggle:hover {{ background: #344452; }}
        </style>
        <script>
        const eventKey = {safe_event_key};
        const enabledKey = "blackjack-sounds-enabled";
        const previousKey = "blackjack-sounds-last-event";
        const button = document.getElementById("sound-toggle");
        const icon = document.getElementById("sound-icon");
        const status = document.getElementById("sound-status");
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;

        function updateSoundButton(enabled) {{
            icon.textContent = enabled ? "🔊" : "🔇";
            const label = enabled ? "Desativar som" : "Ativar som";
            button.setAttribute("aria-label", label);
            button.title = label;
        }}

        function playTone(frequency) {{
            if (!AudioContextClass) {{
                status.textContent = " Áudio não suportado neste navegador.";
                return;
            }}
            const context = new AudioContextClass();
            const oscillator = context.createOscillator();
            const gain = context.createGain();
            oscillator.frequency.value = frequency;
            gain.gain.setValueAtTime(0.12, context.currentTime);
            gain.gain.exponentialRampToValueAtTime(0.001, context.currentTime + 0.16);
            oscillator.connect(gain);
            gain.connect(context.destination);
            oscillator.start();
            oscillator.stop(context.currentTime + 0.16);
            oscillator.onended = () => context.close();
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
        const enabled = localStorage.getItem(enabledKey) === "true";
        updateSoundButton(enabled);
        if (enabled && localStorage.getItem(previousKey) !== eventKey) {{
            localStorage.setItem(previousKey, eventKey);
            playTone(440);
        }}
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
                    linear-gradient(145deg, {palette['felt']}, #123b36 120%);
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
        stats_columns = st.columns(2)
        for index, column in enumerate(stats_columns):
            player_stats = stats[index]
            column.caption(
                f'{game["names"][index]} — {player_stats["games_played"]} partidas, '
                f'{player_stats["wins"]} vitórias, {player_stats["losses"]} derrotas, '
                f'{player_stats["draws"]} empates'
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
            score_columns = st.columns(2)
            for index, column in enumerate(score_columns):
                score = hand_score(game["hands"][index], game["rules"])
                status = (
                    "Estourou" if score > TARGET_SCORE else
                    "Parou" if game["finished"][index] else
                    "Finalizado" if game["over"] else
                    "Na vez" if index == game["current_player"] else "Aguardando"
                )
                suit_color = "#db4c4c" if index == 1 else "#1d4243"
                hand_markup = "".join(
                    f'<div class="playing-card {"red-suit" if card["suit"] in {"♥", "♦"} else ""}">'
                    f'<span>{html.escape(card["rank"])}</span><span>{html.escape(card["suit"])}</span></div>'
                    for card in game["hands"][index]
                )
                column.markdown(
                    f"""
                    <div class="score-panel" style="--suit: {suit_color}">
                        <div class="card-corner"><span>♠</span><span>{score:02d}</span></div>
                        <div class="card-value">{score}</div>
                        <div class="score-name">{html.escape(game["names"][index])}</div>
                        <div class="score-state">{status}</div>
                    </div>
                    <div class="playing-hand">{hand_markup}</div>
                    """,
                    unsafe_allow_html=True,
                )

            if game["over"]:
                if game["result"].startswith(("Empate", "Todos")):
                    st.info(game["result"])
                else:
                    st.success(game["result"])
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
                    if buy_column.button("Comprar", type="primary", use_container_width=True):
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
    .card-corner, .card-bottom { color: var(--suit); font-family: 'DM Mono', monospace; font-weight: 500; }
    .card-corner { display: flex; gap: .45rem; font-size: .9rem; }
    .card-value { color: #17252a; font-family: 'Bungee', Impact, sans-serif; font-size: 3.5rem; line-height: 1; }
    .score-name { color: #34494b; font-family: 'DM Mono', monospace; font-size: .85rem; text-transform: uppercase; }
    .score-state { color: var(--suit); font-family: 'DM Mono', monospace; font-size: .75rem; font-weight: 500; text-transform: uppercase; }
    .card-bottom { position: absolute; right: 1rem; bottom: .7rem; font-size: 2.8rem; opacity: .8; }
    .playing-hand { display: flex; flex-wrap: wrap; gap: .55rem; margin: .9rem 0 1.1rem; min-height: 72px; }
    .playing-card {
        display: grid; place-content: space-between; width: 55px; height: 76px; padding: 6px;
        border-radius: 6px; background: #fff2d5; color: #203238; font: 500 .9rem 'DM Mono', monospace;
        box-shadow: 0 3px 0 rgba(4,18,20,.45); animation: card-deal .24s ease-out both;
    }
    .playing-card.red-suit { color: #d54450; }
    .playing-card span:last-child { justify-self: end; font-size: 1.25rem; }
    @keyframes card-deal { from { opacity: 0; transform: translateY(-12px) rotate(-5deg); } to { opacity: 1; transform: translateY(0) rotate(0); } }
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
    </style>
    """,
    unsafe_allow_html=True,
)

if "game" not in st.session_state:
    st.session_state.game = create_game()

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
            linear-gradient(145deg, {palette['felt']}, #123b36 120%);
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
    st.markdown('<div class="eyebrow">Cassino de bolso &nbsp; / &nbsp; Blackjack simplificado</div>', unsafe_allow_html=True)
    st.title("Vinte e um")
    st.markdown('<p class="tagline">COMPRE. SEGURE. NÃO ESTOURE.</p>', unsafe_allow_html=True)
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
                room_name = st.text_input("Seu nome", value="Jogador 1", max_chars=18)
                profile_token = st.text_input(
                    "Token de perfil existente (opcional)",
                    value=st.session_state.get("online_profile_token", ""),
                    type="password",
                    help="Deixe vazio para criar um perfil persistente novo.",
                )
                room_rules = st.selectbox("Regra", ["Blackjack", "21 simples"])
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

with st.expander("Configurar partida", expanded=False):
    settings_columns = st.columns(3)
    with settings_columns[0]:
        player_count = st.radio(
            "Modo",
            options=[1, 2],
            format_func=lambda count: "Contra computador" if count == 1 else "Dois jogadores",
            key="setup_player_count",
        )
    with settings_columns[1]:
        rules = st.selectbox("Regra", ["Blackjack", "21 simples"], key="setup_rules")
        best_of = st.selectbox(
            "Série",
            [1, 3, 5],
            format_func=lambda count: "Uma rodada" if count == 1 else f"Melhor de {count}",
            key="setup_best_of",
        )
    with settings_columns[2]:
        theme = st.selectbox("Tema", list(THEMES), key="setup_theme")
        if player_count == 1:
            first_name = st.text_input("Seu nome", value="Você", max_chars=18, key="setup_name_one")
            second_name = "Computador"
        else:
            first_name = st.text_input("Nome do jogador 1", value="Jogador 1", max_chars=18, key="setup_name_first")
            second_name = st.text_input("Nome do jogador 2", value="Jogador 2", max_chars=18, key="setup_name_second")
    if st.button("Aplicar e iniciar", type="primary", use_container_width=True):
        names = [first_name.strip() or "Jogador 1", second_name.strip() or "Jogador 2"]
        st.session_state.game = create_game(player_count, names, rules, best_of, theme)
        st.rerun()

game = st.session_state.game
render_sound_control(
    f'local:{game["round_number"]}:{len(game["history"])}:'
    f'{game["history"][-1] if game["history"] else "início"}'
)
st.markdown('<div class="eyebrow">Cassino de bolso &nbsp; / &nbsp; Blackjack simplificado</div>', unsafe_allow_html=True)
st.title("Vinte e um")
st.markdown('<p class="tagline">COMPRE. SEGURE. NÃO ESTOURE.</p>', unsafe_allow_html=True)
st.markdown(
    f'<div class="match-score">RODADA {game["round_number"]} &nbsp; · &nbsp; SÉRIE {game["match_wins"][0]} — {game["match_wins"][1]} &nbsp; · &nbsp; {game["rules"].upper()}</div>',
    unsafe_allow_html=True,
)

with st.container(border=True):
    st.markdown(f'<div class="eyebrow">Mesa 01 &nbsp; / &nbsp; Alvo: {TARGET_SCORE}</div>', unsafe_allow_html=True)
    score_columns = st.columns(2)
    for index, column in enumerate(score_columns):
        score = hand_score(game["hands"][index], game["rules"])
        status = "Estourou" if score > TARGET_SCORE else "Parou" if game["finished"][index] else "Finalizado" if game["over"] else "Na vez" if index == game["current_player"] else "Aguardando"
        suit_color = "#db4c4c" if index == 1 else "#1d4243"
        hand_markup = "".join(
            f'<div class="playing-card {"red-suit" if card["suit"] in {"♥", "♦"} else ""}"><span>{card["rank"]}</span><span>{card["suit"]}</span></div>'
            for card in game["hands"][index]
        )
        player_name = html.escape(game["names"][index])
        column.markdown(
            f"""
            <div class="score-panel" style="--suit: {suit_color}">
                <div class="card-corner"><span>♠</span><span>{score:02d}</span></div>
                <div class="card-value">{score}</div>
                <div class="score-name">{player_name}</div>
                <div class="score-state">{status}</div>
            </div>
            <div class="playing-hand">{hand_markup}</div>
            """,
            unsafe_allow_html=True,
        )

    computer_update = st.empty()
    if game["over"]:
        if game["result"].startswith("Empate") or game["result"].startswith("Todos"):
            st.info(game["result"])
        else:
            st.success(game["result"])
        if game["match_over"]:
            if st.button("Nova série", type="primary"):
                st.session_state.game = create_game(
                    game["player_count"], game["names"], game["rules"], game["best_of"], game["theme"]
                )
                st.rerun()
        elif st.button("Próxima rodada", type="primary"):
            start_next_round(game)
            st.rerun()
    elif game["dealer_playing"]:
        st.subheader("Vez do Computador")
        computer_update.info("O computador está escolhendo uma carta...")
    elif game["current_player"] < game["player_count"]:
        player_name = game["names"][game["current_player"]]
        st.subheader(f"Vez de {player_name}")
        buy_column, stand_column = st.columns(2)
        if buy_column.button("Comprar", type="primary", use_container_width=True):
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
    card = draw_card(game, 1)
    score = hand_score(game["hands"][1], game["rules"])
    game["history"].append(f"Computador comprou {format_card(card)} ({score} pontos).")
    computer_update.info(
        f"Computador comprou {format_card(card)}. Total: {score} pontos."
    )
    if not computer_should_hit(game):
        game["dealer_playing"] = False
        game["finished"][1] = True
        finish_game(game)
    time.sleep(COMPUTER_DRAW_DELAY)
    st.rerun()
