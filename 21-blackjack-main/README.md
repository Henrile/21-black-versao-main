# 21 | Blackjack

Aplicativo Streamlit de Blackjack com modo local e salas online de dois jogadores.
Cada rodada começa com uma carta por jogador.

## Salas online com Supabase

1. Crie um projeto Supabase e execute [`supabase/schema.sql`](./supabase/schema.sql) no SQL Editor do projeto.
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

As estatísticas são persistidas no Supabase por perfil. O token de perfil é criado na primeira sala; guarde-o e reutilize-o ao criar ou entrar em outras salas para continuar o mesmo histórico. Os tokens de perfil e de sala são credenciais privadas: não os publique nem os envie a terceiros.

## Efeitos sonoros

Os sons são sintetizados pelo navegador. Clique em **Ativar efeitos sonoros** uma vez; depois disso, o app tenta reproduzir um efeito automaticamente quando uma jogada nova aparece. O navegador pode bloquear áudio até essa interação ou conforme suas permissões.

## Modo local

O modo local continua disponível sem credenciais do Supabase. Ele mantém o estado apenas na sessão atual do Streamlit; para salas online e estatísticas persistentes, configure o Supabase conforme acima.
