# 21 | Blackjack

Aplicativo Streamlit de Blackjack com modo local e salas online de dois jogadores. Cada mão começa com duas cartas para cada jogador e duas para o dealer; a carta fechada do dealer só é revelada no fim da rodada.

Ao abrir o app, o jogador informa o nome antes de entrar na mesa. O jogo usa o tema **Velho Oeste**, com avatares pixel art 8-bit. Cada carteira começa com $50 e o jogador escolhe a aposta inicial. No modo local, a aposta pode ser ajustada antes de cada rodada; online, a aposta escolhida na sala se repete nas rodadas seguintes. O dealer pede carta abaixo de 17; vitória normal paga 1:1, Blackjack natural paga 3:2 e empate devolve a aposta. Dobrar, dividir e seguro não fazem parte desta versão.

Na mesa local, **Reiniciar jogo** redistribui as cartas, zera a série e restaura as carteiras para $50.

## Salas online com Supabase

1. Crie um projeto Supabase e execute [`supabase/schema.sql`](./supabase/schema.sql) no SQL Editor do projeto. Se já tinha uma instalação, execute o script novamente para adicionar carteiras e atualizar as funções online.
2. Instale as dependências com `pip install -r requirements.txt`.
3. Configure os segredos do Streamlit localmente em `.streamlit/secrets.toml` ou na seção **Secrets** do serviço de hospedagem:

   ```toml
   [supabase]
   url = "https://SEU-PROJETO.supabase.co"
   anon_key = "SUA_CHAVE_ANON"
   ```

   Use apenas a chave `anon`/publishable no app. Nunca configure ou publique a chave `service_role`.
4. Publique `app.py` em um host que execute Streamlit, como o Streamlit Community Cloud. A aplicação só oferece salas online quando os segredos do Supabase estão configurados.

Para iniciar uma partida, o primeiro jogador cria uma sala e compartilha o código e o convite privado do segundo assento. O convite só autoriza ocupar esse assento; cada jogador também recebe um segredo privado de sala para reconectar. As jogadas e a troca de rodada são autorizadas e aplicadas atomicamente pelas funções SQL, e o baralho permanece no banco, não na resposta enviada ao navegador. A interface consulta o estado compartilhado a cada segundo para atualizar as duas sessões e retomar a sincronização após uma interrupção.

As estatísticas e carteiras online são persistidas por perfil. O token de perfil é criado na primeira sala; guarde-o e reutilize-o ao criar ou entrar em outras salas para continuar o mesmo histórico e saldo. Cada jogador escolhe sua própria aposta e joga contra o dealer. Os tokens de perfil e de sala são credenciais privadas: não os publique nem os envie a terceiros.

## Efeitos sonoros

Os sons são sintetizados pelo navegador. Clique em **Ativar efeitos** uma vez; depois disso, o app tenta reproduzir um efeito automaticamente quando uma jogada nova aparece. O navegador pode bloquear áudio até essa interação ou conforme suas permissões.

## Música de fundo

Use o botão de nota musical (🎵) para ativar ou desativar uma trilha 8-bit sintetizada no navegador. A preferência é salva no navegador e não exige arquivos externos.

## Modo local

O modo local continua disponível sem credenciais do Supabase. A carteira local vale apenas para a sessão atual do Streamlit; para salas online e carteiras persistentes, configure o Supabase conforme acima.

## APK Android offline

O projeto em `android/` inclui uma versão Android offline do Blackjack, com uma mesa local contra o dealer. A interface e as regras ficam dentro do APK; não é necessário iniciar o Streamlit, conectar à internet ou configurar um servidor. O app Android é independente da interface Streamlit e, nesta versão, não inclui salas online/Supabase.

No Android Studio, abra a pasta `android/`, aguarde a sincronização do Gradle, selecione um emulador ou dispositivo conectado e pressione **Run**. Para gerar o APK, selecione **Build > Build Bundle(s) / APK(s) > Build APK(s)**. O arquivo fica em `app/build/outputs/apk/debug/app-debug.apk`.

Também é possível compilar pelo terminal dentro de `android/` com `.\gradlew.bat assembleDebug`. O Gradle 8.13 deste projeto requer JDK 17; no Android Studio, selecione JDK 17 em **Settings > Build Tools > Gradle > Gradle JDK**.
