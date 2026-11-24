# 21 | Blackjack

Blackjack em Streamlit com jogo local contra o dealer e salas online para dois jogadores. Inclui também um app Android offline para partidas locais.

## Aplicativo

O código do jogo, as instruções de execução, a integração opcional com Supabase e o projeto Android estão em [`21-blackjack-main/`](./21-blackjack-main/). Consulte o [README do aplicativo](./21-blackjack-main/README.md) para detalhes.

## Início rápido

```powershell
cd 21-blackjack-main
pip install -r requirements.txt
py -3 -m streamlit run app.py
```

O APK Android funciona offline e pode ser compilado abrindo [`21-blackjack-main/android/`](./21-blackjack-main/android/) no Android Studio.