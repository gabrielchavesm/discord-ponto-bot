# Discord Ponto Bot - Multi-Departamento com Sistema ON/BREAK/FINISH

Sistema completo de **registro de ponto por departamento** com suporte a múltiplos canais, **lock automático de utilizadores**, administração granular, **transferência entre departamentos** e exportação de dados. Utiliza sistema moderno de estados **ON/BREAK/FINISH** para controlo preciso de jornada de trabalho.

---

## Índice

- [Visão Geral](#1-visão-geral)
- [Estrutura do Projeto](#2-estrutura-do-projeto)
- [Sistema ON/BREAK/FINISH](#3-sistema-onbreakfinish)
- [Como Usar](#4-como-usar)
- [Comandos Disponíveis](#5-comandos-disponíveis)
- [Permissões e Hierarquia](#6-permissões-e-hierarquia)
- [Sistema de Bloqueio](#7-sistema-de-bloqueio)
- [Execução e Deploy](#8-execução-e-deploy)
- [Detalhes Técnicos](#9-detalhes-técnicos)
- [Testes Automatizados](#10-testes-automatizados)
- [Observações Importantes](#11-observações-importantes)
- [Exemplos de Uso](#12-exemplos-de-uso)
- [Variáveis de Ambiente](#13-variáveis-de-ambiente)
- [Dockerfile](#14-dockerfile)

---

## 1. Visão Geral

Este bot permite que diferentes departamentos/canais giram seus próprios sistemas de ponto de forma **independente e isolada**, com **bloqueio automático** de utilizadores para garantir que cada pessoa só possa registrar ponto em um departamento por vez.

### Funcionalidades Principais

- Sistema **ON/BREAK/FINISH**
- Painel de registro próprio por canal com os 3 botões persistentes
- Status em tempo real dos utilizadores (A TRABALHAR/PAUSA/AUSENTE)
- Cálculo automático de tempo trabalhado e tempo de pausa
- **Limite automático de 6h30min** de trabalho por dia
- Sistema de administradores específicos por departamento
- **Bloqueio automático**: cada utilizador fica vinculado ao primeiro departamento onde fizer ponto
- **Transferência de utilizadores** entre departamentos (apenas Super Admins)
- **Relatórios detalhados** com médias e estatísticas
- **Exportação** de dados em **CSV**
- **Detecção automática** de **dispositivo** (desktop/mobile)
- **Persistência de views** após reinicialização do bot
- **Atualização automática** de painéis de status
- **Sistema de Super Admins gerível**: Super Admins podem ser adicionados/removidos via comandos específico.

**Importante:** Quando um utilizador faz seu primeiro registro, ele fica **automaticamente bloqueado a aquele departamento**. Para mudar de departamento, é necessária uma **transferência manual** por um Super Admin.

---

## 2. Estrutura do Projeto

```
discord-ponto-bot/
├── bot.py                      # Script principal do bot
├── database.py                 # Camada de dados PostgreSQL
├── Dockerfile                  # Imagem Docker do bot
├── docker-compose.yml          # Orquestração (bot + PostgreSQL)
├── requirements.txt            # Dependências Python
├── .env                        # Variáveis de ambiente
├── data/
│   └── postgres/               # Dados persistentes do PostgreSQL
└── tests/                      # Testes automatizados (85 testes)
    ├── test_check_admin.py
    ├── test_config_admins_*.py
    ├── test_record_action.py
    └── ...
```

### Arquivos Principais

#### `bot.py`
- Configuração do bot Discord
- Definição de comandos slash (app_commands)
- Views interativas (botões ON/BREAK/FINISH)
- Lógica de permissões e hierarquia
- Sistema de atualização de painéis
- Detecção de dispositivo

#### `database.py`
- **Arquitetura async nativa** com psycopg AsyncConnectionPool
- **Pool de conexões assíncronas** (5-25 conexões concorrentes)
- **Sistema de retry automático** com gestão resiliente de falhas
- **Row-level locking** (SELECT FOR UPDATE) para prevenir race conditions
- Funções de registro de ações (ON/BREAK/FINISH)
- Sistema de bloqueio de canal
- Gestão de permissões
- Funções de relatórios e estatísticas
- Exportação de dados
- **Zero threads** - 100% non-blocking I/O

#### `docker-compose.yml`
- Serviço do bot Discord
- Serviço PostgreSQL 18.1
- Configuração de volumes persistentes
- Rede interna entre containers

---

## 3. Sistema ON/BREAK/FINISH

### Como Funciona o Sistema de Estados

O sistema utiliza **3 botões** para controlar a jornada de trabalho:

#### 🟢 Botão ON
- **Primeira vez do dia**: Registra entrada inicial (início da jornada)
- **Durante pausa**: Retorna ao trabalho (finaliza pausa)
- **Calcula**: Tempo de pausa quando retorna
- **Armazena**: `first_on` (timestamp da primeira entrada)
- **Atualiza**: `last_transition` e `last_action`

#### 🟡 Botão BREAK
- **Quando estiver a trabalhar**: Inicia pausa (almoço, intervalo, etc)
- **Calcula**: Tempo trabalhado até o momento da pausa
- **Acumula**: Tempo total de pausas do dia em `break_sum`
- **Verifica**: Se atingiu 6h30min de trabalho (fecha automaticamente)
- **Atualiza**: Estado para `is_break = TRUE`

#### 🔴 Botão FINISH
- **Quando estiver a trabalhar**: Finaliza o dia de trabalho
- **Calcula**: Tempo trabalhado da última sessão
- **Totaliza**: Horas trabalhadas e pausas do dia completo
- **Bloqueia**: Não permite mais registros neste dia (`is_finished = TRUE`)
- **Verifica**: Se excedeu 6h30min (limita automaticamente)

### Fluxo Completo de um Dia

```
09:00 → ON      [TRABALHANDO]  "🟢 Primeiro ON do dia registrado! Bom trabalho!"
12:30 → BREAK   [PAUSA]        "🟡 Pausa iniciada! Sessão de trabalho: 03:30:00"
13:30 → ON      [TRABALHANDO]  "🟢 Retorno da pausa registrado! Pausa durou: 01:00:00"
18:00 → FINISH  [AUSENTE]      "🔴 Dia finalizado!"
```

### Estados Visuais no Painel

O painel de status mostra em tempo real:

- 🟢 **TRABALHANDO** - Utilizador está ativo desde [hora]
- 🟡 **PAUSA** - Utilizador em pausa desde [hora]
- 🔴 **AUSENTE** - Utilizador finalizou o dia às [hora] on [data]

### Cálculos Automáticos

1. **Tempo de Trabalho** (`work_sum`)
   - Soma de todos os períodos entre ON e BREAK/FINISH
   - Exemplo: 3h30 (manhã) + 4h30 (tarde) = 8h00
   - **Limite máximo**: 6h30min por dia

2. **Tempo de Pausa** (`break_sum`)
   - Soma de todos os períodos entre BREAK e ON
   - Exemplo: 1h00 (almoço) = 1h00

3. **Primeiro ON do Dia** (`first_on`)
   - Timestamp exato da primeira entrada
   - Usado para médias de horário de entrada

4. **Último FINISH** (`finish`)
   - Timestamp exato da saída final
   - Usado para médias de horário de saída

5. **Detecção de Dispositivo** (`mobile_first_on`)
   - Registra se o primeiro ON foi via mobile
   - Usado para relatório de registros móveis

### Limite de 6h30min

O sistema possui um **limite automático de 6 horas e 30 minutos** de trabalho por dia, definido pela constante `MAX_WORK_HOURS` em `database.py`:

```python
MAX_WORK_HOURS = timedelta(hours=6, minutes=30)
```

#### Comportamento no BREAK

Se ao clicar BREAK o `work_sum` atingir/exceder 6h30min:
- Sistema **fecha automaticamente** o dia
- Limita `work_sum` a **06:30:00**
- Define `is_finished = TRUE`
- Informa o tempo excedente ignorado

**Exemplo:**
```
08:00 → ON
14:35 → BREAK (6h35min trabalhadas)
→ "🔴 Dia automaticamente finalizado (6h30min atingidas)!"
→ "⏱️ Trabalho total: 06:30:00"
→ "☕ Pausas totais: 00:00:00"
→ "ℹ️ Tempo excedente ignorado: 00:05:00"
```

#### Comportamento no FINISH

Se ao clicar FINISH o `work_sum` exceder 6h30min:
- Limita `work_sum` a **06:30:00**
- Define `is_finished = TRUE`
- Informa apenas que o dia foi finalizado (mensagem simplificada)

**Exemplo:**
```
08:00 → ON
12:00 → BREAK (4h trabalhadas)
13:00 → ON
19:00 → FINISH (4h + 6h = 10h trabalhadas)
→ "🔴 Dia finalizado!"
→ work_sum armazenado: 06:30:00 (limite aplicado)
→ tempo excedente de 3h30min ignorado
```

### Persistência de Views

O sistema utiliza `custom_id` nos botões para garantir persistência:

```python
@discord.ui.button(label="ON", style=discord.ButtonStyle.success, custom_id="on_point")
@discord.ui.button(label="BREAK", style=discord.ButtonStyle.secondary, custom_id="break_point")
@discord.ui.button(label="FINISH", style=discord.ButtonStyle.danger, custom_id="finish_point")
```

**Vantagens:**
- Botões funcionam após reinicialização do bot
- Não necessita recriar painéis
- Configurado via `setup_hook()` no bot

---

## 4. Como Usar

### Configuração Inicial (Super Admin)

1. **Adicionar Super Admin via comando**
   
   Um Super Admin existente pode adicionar novos Super Admins usando:
   ```
   /super_admin_add @user
   ```
   **Importante:** Apenas Super Admins existentes ou o proprietário do servidor podem usar este comando inicialmente.

2. **Criar Painel de Ponto no Canal**
   
   No canal do departamento, execute:
   ```
   /config_department_setup
   ```
   
   Isso criará um painel com os 3 botões: **ON**, **BREAK** e **FINISH**.

3. **Criar Painel de Status**
   
   ```
   /config_status_panel
   ```
   
   Cria um painel que atualiza automaticamente mostrando o estado de cada utilizador.

4. **Adicionar Admins do Departamento**
   
   ```
   /config_admins_add @utilizador
   ```
   
   O utilizador mencionado poderá administrar este departamento específico.

### Uso Diário (Utilizadores)

#### Fluxo Normal de Trabalho

**Entrada Manhã:**
```
09:00 - Clica ON
🟢 "Primeiro ON do dia registrado! Bom trabalho!"
Status: TRABALHANDO desde 09:00:00
```

**Pausa Almoço:**
```
12:30 - Clica BREAK
🟡 "Pausa iniciada! Sessão de trabalho: 03:30:00"
Status: PAUSA desde 12:30:00
```

**Retorno Almoço:**
```
13:30 - Clica ON
🟢 "Retorno da pausa registrado! Pausa durou: 01:00:00"
Status: TRABALHANDO desde 13:30:00
```

**Saída Final:**
```
18:00 - Clica FINISH
🔴 "Dia finalizado!"
Status: AUSENTE desde 18:00:00 on 21/01/2026
```

#### ⚠️ Situações Especiais

**Tentativa de ON quando já está ON:**
```
❌ "Você já está trabalhando!"
```

**Tentativa de BREAK sem estar trabalhando:**
```
❌ "Você precisa estar trabalhando para iniciar uma pausa."
```

**Tentativa de FINISH durante pausa:**
```
❌ "Você precisa estar trabalhando para finalizar o dia."
```

**Tentativa de registrar após FINISH:**
```
❌ "Dia já finalizado. Não é possível registrar mais ações."
```

**Atingindo limite de 6h30min no BREAK:**
```
🔴 "Dia automaticamente finalizado (6h30min atingidas)!"
⏱️ "Trabalho total: 06:30:00"
☕ "Pausas totais: 01:00:00"
ℹ️ "Tempo excedente ignorado: 00:05:00"
```

#### Bloqueio Automático

**⚠️ IMPORTANTE:** No primeiro registro, o utilizador será **automaticamente bloqueado naquele departamento**.

**Tentativa em outro departamento:**
```
⚠️ Você está registrado no departamento #recursos-humanos. 
Contacte um Super Admin para transferência.
```

### Transferência de Utilizadores (Super Admins)

#### Transferir um utilizador entre departamentos:

```
/admin_transfer_user @utilizador canal_destino:#novo-departamento
```

O sistema irá:
1. Mostrar confirmação com origem e destino
2. Ao confirmar, mover **todos os registros** do utilizador
3. Atualizar o bloqueio para o novo departamento
4. Atualizar painéis de status de ambos os canais

#### Ver todos os bloqueios ativos:

```
/admin_view_locks
```

Mostra lista de todos os utilizadores e seus departamentos atuais.

### Consulta de Dados (Admins)

#### Ver relatório detalhado de um utilizador:
```
/report_user_detailed @utilizador
```

Mostra:
- Média de horário de entrada
- Média de horário de saída
- Média de horas trabalhadas por dia
- Total de horas trabalhadas no período
- Média de pausas diárias
- Total de pausas no período
- Dias úteis considerados (ignora fins de semana)

Opcionalmente com período específico:
```
/report_user_detailed @utilizador data_inicio:01/01/2026 data_fim:31/01/2026
```

**Nota:** Se não informar datas, o sistema usa automaticamente o período entre o primeiro e último registro do utilizador no departamento.

#### Ver registros feitos via telemóvel:
```
/report_user_mobile @utilizador data_inicio:01/01/2026 data_fim:31/01/2026
```

Lista todos os dias em que o primeiro ON foi feito via dispositivo móvel.

#### Exportar todos os dados do departamento:
```
/report_export_csv
```

Gera arquivo CSV com todos os registros do canal, incluindo:
- channel_id, user_id, data
- first_on, finish
- work_sum, break_sum
- is_finished, mobile_first_on

---

## 5. Comandos Disponíveis

### Comandos de Configuração

| Comando | Permissão | Descrição |
|---------|-----------|-----------|
| `/config_department_setup` | Super Admin ou Admin do Canal | Cria painel de registro (ON/BREAK/FINISH) no canal |
| `/config_status_panel` | Super Admin ou Admin do Canal | Cria painel de status em tempo real |
| `/config_admins_add @user` | **Apenas Super Admin** | Adiciona administrador ao canal |
| `/config_admins_remove @user` | **Apenas Super Admin** | Remove administrador do canal |
| `/super_admin_add @user` | **Apenas Super Admin** | Promove um utilizador a SUPER ADMIN |
| `/super_admin_remove @user` | **Apenas Super Admin** | Remove privilégios de SUPER ADMIN |
| `/super_admin_list` | **Apenas Super Admin** | Lista todos os SUPER ADMINS do sistema |
| `/config_admins_list` | Super Admin ou Admin do Canal | Lista admins do canal |

### Comandos de Relatórios

| Comando | Permissão | Descrição |
|---------|-----------|-----------|
| `/report_user_detailed @user [data_inicio] [data_fim]` | Super Admin ou Admin do Canal | Relatório completo com médias e totais |
| `/report_user_mobile @user [data_inicio] [data_fim]` | Super Admin ou Admin do Canal | Lista dias com registro via mobile |
| `/report_export_csv` | Super Admin ou Admin do Canal | Exporta CSV com todos dados do canal |

### Comandos Administrativos

| Comando | Permissão | Descrição |
|---------|-----------|-----------|
| `/admin_delete_data @user` | Super Admin ou Admin do Canal | Deleta registros de um utilizador no canal |
| `/admin_transfer_user @user #destino` | **Apenas Super Admin** | Transfere utilizador entre departamentos |
| `/admin_view_locks` | **Apenas Super Admin** | Lista todos os bloqueios ativos |

### Interação por Botões

| Botão | Disponível Para | Ação |
|-------|----------------|------|
| 🟢 **ON** | Todos os utilizadores | Registra entrada ou retorno de pausa |
| 🟡 **BREAK** | Todos os utilizadores | Registra início de pausa |
| 🔴 **FINISH** | Todos os utilizadores | Finaliza o dia de trabalho |

**Nota:** Todos os comandos utilizam respostas efêmeras (`ephemeral=True`) para manter a privacidade dos dados.

---

## 6. Permissões e Hierarquia

### Modelo de Permissões

```
Proprietário do Servidor (owner_id)
    ↓
Administradores Discord (permissão administrador)
    ↓
Super Admins (via base de dados - adicionados por /super_admin_add)
    ↓ controle total global + transferências + adicionar admins
    ├─ Canal #rh
    │   ├─ Admin A (via /config_admins_add)
    │   └─ Admin B (via /config_admins_add)
    │       → podem gerenciar apenas #rh
    │       → NÃO podem transferir utilizadores
    │       → NÃO podem adicionar outros admins
    │
    ├─ Canal #ti
    │   └─ Admin C (via /config_admins_add)
    └─ Canal #vendas
        └─ (sem admins além dos Super Admins)
```

### Sistema de Super Admins Dinâmico

O sistema possui Super Admins gerenciáveis via base de dados:

```python
def check_super_admin(interaction: discord.Interaction) -> bool:
    # 1. Proprietário do servidor
    if interaction.user.id == interaction.guild.owner_id:
        return True

    # 2. Permissão de Administrador no Discord
    member = interaction.guild.get_member(interaction.user.id)
    if member and member.guild_permissions.administrator:
        return True

    # 3. SUPER ADMIN na base de dados
    if database.is_super_admin(str(interaction.user.id)):
        return True

    return False
```

### Limitações Importantes

1. **Admins de Canal NÃO podem:**
   - Ver ou modificar dados de outros canais
   - Adicionar ou remover administradores
   - **Transferir utilizadores entre departamentos**
   - Executar comandos em canais onde não são admins
   - Ver bloqueios globais do sistema

2. **Super Admins podem:**
   - Executar qualquer comando em qualquer canal
   - **Adicionar/remover administradores de qualquer canal**
   - **Transferir utilizadores entre departamentos**
   - Ver todos os bloqueios ativos
   - Ver dados de qualquer departamento

3. **Dados são isolados:**
   - Não existe "relatório global" de todos os departamentos
   - CSV exporta apenas dados do canal onde foi executado
   - Status mostra apenas utilizadores do canal atual
   - Transferências movem os dados, mantendo histórico completo

### Como Verificar Permissões

**Admins do canal atual:**
```
/config_admins_list
```

**Todos os bloqueios (Super Admin):**
```
/admin_view_locks
```

**Nota:**
Super Admins são **geríveis via comandos:**
   - Super Admins existentes podem adicionar/remover outros Super Admins
   - Comandos: `/super_admin_add`, `/super_admin_remove`, `/super_admin_list`
   - O proprietário do servidor e administradores Discord são automaticamente Super Admins
   - Não é necessário editar código para adicionar Super Admins

---

## 7. Sistema de Bloqueio

### Como Funciona

1. **Primeiro Registro:**
   - Utilizador clica em ON/BREAK/FINISH pela primeira vez
   - Função `record_action()` verifica se já existe bloqueio
   - Se não existir, chama `lock_user_to_channel()`
   - Registro é inserido em `permissao_canal` com `tipo='locked_user'`
   - Mensagem de confirmação é exibida

2. **Tentativa em Outro Canal:**
   - Utilizador tenta registrar em canal diferente
   - Sistema detecta bloqueio existente via `get_user_locked_channel()`
   - Compara `locked_channel` com `current_channel`
   - Se diferente, bloqueia ação e retorna mensagem de erro
   - Registro não é processado

3. **Transferência (Super Admin):**
   - Super Admin executa `/admin_transfer_user`
   - Função `transfer_user_to_channel()` é chamada
   - Atualiza `channel_id` em todas as tabelas:
     - `registros_diarios` (move histórico completo)
     - `permissao_canal` (atualiza bloqueio)
   - Painéis de status são atualizados
   - Confirmação é exibida

### Funções do Sistema de Bloqueio

```python
def get_user_locked_channel(user_id):
    """Retorna o channel_id onde o utilizador está bloqueado, ou None"""
    
def lock_user_to_channel(user_id, channel_id):
    """Bloqueia um utilizador a um canal específico"""
    
def unlock_user(user_id):
    """Remove o bloqueio de canal de um utilizador"""
    # Nota: Não utilizada atualmente, mas disponível
    
def transfer_user_to_channel(user_id, origem_channel_id, destino_channel_id):
    """Transfere todos os dados de um utilizador de um canal para outro"""
```

### Cenários de Uso

#### Cenário 1: Novo Funcionário

```
1. João entra no canal #rh
2. Clica em ON → bloqueado em #rh automaticamente
3. Trabalha normalmente em #rh
4. Não consegue registrar em outros canais
```

#### Cenário 2: Mudança de Departamento

```
1. Maria está registrada em #vendas
2. É transferida para #ti
3. Super Admin executa: /admin_transfer_user @Maria #ti
4. Todos os 156 registros são movidos
5. Maria agora registra ponto em #ti
6. Bloqueio atualizado para #ti
```

#### Cenário 3: Funcionário em Múltiplos Departamentos (Não Permitido)

```
1. Pedro está em #ti
2. Tenta dar ponto em #vendas
3. Sistema bloqueia: "Você está registrado em #ti"
4. Pedro precisa de transferência formal
5. Super Admin pode executar transferência
```

### Vantagens do Sistema

- **Controlo total** de alocação de pessoal  
- **Evita erros** de registro duplicado  
- **Auditoria clara** de movimentações  
- **Integridade de dados** garantida  
- **Gestão centralizada** via Super Admins  
- **Histórico preservado** nas transferências

### View de Confirmação de Transferência

```python
class ConfirmTransferView(View):
    """Confirmação visual antes de transferir"""
    def __init__(self, user_id, origem_channel_id, destino_channel_id):
        super().__init__(timeout=60)
        # Mostra origem, destino e número de registros
        # Botões: [Confirmar Transferência] [Cancelar]
```

---

## 8. Execução e Deploy

### Pré-requisitos

- Docker
- Docker Compose
- Arquivo `.env` configurado

### Configuração do `.env`

```env
# Token do bot Discord
DISCORD_TOKEN=seu_token_aqui

# ID do servidor Discord
GUILD_ID=seu_guild_id_aqui

# PostgreSQL
POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_DB=ponto
POSTGRES_USER=postgres
POSTGRES_PASSWORD=senha_segura_aqui
```

**⚠️ SEGURANÇA:** Nunca compartilhe seu `DISCORD_TOKEN`

### Estrutura do Docker Compose

```yaml
services:
  discord-bot:
    build: .
    container_name: discord-ponto-bot
    restart: always
    volumes:
      - ./data:/app/data
    env_file:
      - .env
    depends_on:
      - db

  db:
    image: postgres:18.1
    container_name: discord-ponto-db
    restart: always
    env_file:
      - .env
    volumes:
      - ./data/postgres:/var/lib/postgresql/data
```

### Iniciar o Sistema

```bash
# Build e inicialização
docker-compose up -d --build

# Verificar logs
docker-compose logs -f discord-bot

# Parar o sistema
docker-compose down

# Parar e remover dados (⚠️ CUIDADO)
docker-compose down -v
```

### Verificar Status

```bash
# Status dos containers
docker-compose ps

# Logs do bot
docker-compose logs discord-bot

# Logs do PostgreSQL
docker-compose logs db

# Logs em tempo real
docker-compose logs -f
```

### Acesso ao PostgreSQL

```bash
# Entrar no container do PostgreSQL
docker exec -it discord-ponto-db psql -U postgres -d ponto

# Verificar tabelas
\dt

# Ver registros
SELECT * FROM registros_diarios LIMIT 10;

# Ver bloqueios
SELECT * FROM permissao_canal WHERE tipo = 'locked_user';

# Sair
\q
```

### Backup e Restore

```bash
# Backup da base de dados
docker exec discord-ponto-db pg_dump -U postgres ponto > backup.sql

# Restore da base de dados
cat backup.sql | docker exec -i discord-ponto-db psql -U postgres ponto

# Backup da pasta data
tar -czf backup-data.tar.gz data/

# Restore da pasta data
tar -xzf backup-data.tar.gz
```

### Performance e Escalabilidade

#### Configuração do Pool de Conexões

O pool async está otimizado para alta concorrência em `database.py`:

```python
POOL_MIN = 20        # Conexões warm (sempre disponíveis)
POOL_MAX = 80       # Limite máximo de conexões concorrentes
POOL_TIMEOUT = 15   # Segundos para aguardar conexão disponível
```

**Para ajustar baseado no tráfego:**

```python
# Servidores pequenos (< 50 utilizadores ativos)
POOL_MIN = 2
POOL_MAX = 10

# Servidores médios (50-200 utilizadores ativos)  ← configuração atual
POOL_MIN = 5
POOL_MAX = 25

# Servidores grandes (200+ utilizadores ativos)
POOL_MIN = 10
POOL_MAX = 50
```

**⚠️ Importante:** `POOL_MAX` não pode exceder `max_connections` do PostgreSQL (default: 100)

#### Métricas de Performance

**Throughput esperado:**
- **Operações simples** (status check): ~5000 ops/segundo
- **Operações write** (record_action): ~2500 ops/segundo
- **Relatórios complexos**: ~500 ops/segundo

**Latência típica:**
- Registro de ação (ON/BREAK/FINISH): < 50ms
- Consulta de status: < 10ms
- Relatório de período: 50-200ms (depende do range)

**Concorrência:**
- **200 utilizadores simultâneos** → utilizadores diferentes = zero contenção
- **2 clicks do mesmo utilizador** → segundo aguarda ~10-20ms (row lock)
- **Pool de 25 conexões** → máximo 25 queries PostgreSQL paralelas

#### Monitorização

**Logs do pool (em database.py):**
```bash
# Ver criação e fechamento do pool
docker-compose logs discord-bot | grep "pool"

# Exemplo de logs saudáveis:
# "Creating async PostgreSQL connection pool"
# "Database initialized successfully"
# "Connection pool closed"
```

**Diagnosticar pool exhaustion:**
```bash
# Se aparecer este erro:
# "PoolTimeout: couldn't get a connection after 15.0 sec"

# Solução 1: Aumentar POOL_MAX
# Solução 2: Aumentar POOL_TIMEOUT
# Solução 3: Investigar queries lentas
```

**Verificar conexões PostgreSQL:**
```sql
-- No PostgreSQL container
docker exec -it discord-ponto-db psql -U postgres ponto

-- Ver conexões ativas
SELECT COUNT(*) FROM pg_stat_activity WHERE datname = 'ponto';

-- Detalhes das conexões
SELECT pid, state, query_start, query 
FROM pg_stat_activity 
WHERE datname = 'ponto';
```

#### Tuning PostgreSQL

**Para melhorar performance, editar** `docker-compose.yml`:

```yaml
db:
  image: postgres:18.1
  environment:
    # Conexões máximas (deve ser >= POOL_MAX do bot)
    POSTGRES_MAX_CONNECTIONS: 100
    
    # Memória compartilhada (25% da RAM disponível)
    POSTGRES_SHARED_BUFFERS: 256MB
    
    # Working memory por query
    POSTGRES_WORK_MEM: 4MB
  command:
    - postgres
    - -c
    - max_connections=100
    - -c
    - shared_buffers=256MB
```

#### Índices de Performance

O sistema cria automaticamente índices otimizados em `init_database()`:

```sql
-- Hot path: lookup de registro diário
CREATE INDEX idx_registros_lookup 
ON registros_diarios (channel_id, user_id, data);

-- Channel lock lookup (every record_action)
CREATE INDEX idx_permissao_locked 
ON permissao_canal (user_id, tipo) 
WHERE tipo = 'locked_user';
```

**Para verificar uso dos índices:**
```sql
-- Ver plano de execução
EXPLAIN ANALYZE 
SELECT * FROM registros_diarios 
WHERE channel_id = '123' AND user_id = '456' AND data = CURRENT_DATE;

-- Deve mostrar "Index Scan using idx_registros_lookup"
```

#### Limites e Capacidade

| Métrica | Limite Recomendado | Limite Absoluto |
|---------|-------------------|-----------------|
| Utilizadores ativos simultâneos | 200 | 500+ (com tuning) |
| Departamentos/canais | 50 | Ilimitado |
| Registros por utilizador/ano | 260 (dias úteis) | Ilimitado |
| Registros totais na DB | 100K | Milhões |
| Tamanho típico da DB | 50-100 MB/ano | N/A |
| Queries/segundo | 2000-3000 | 5000+ (peak) |

**Sinais de que precisa escalar:**
- Pool timeout errors frequentes
- Latência > 100ms em operações simples
- CPU do PostgreSQL > 80% constante
- Conexões ativas sempre = POOL_MAX

**Opções de scaling:**
1. **Vertical**: Aumentar RAM/CPU do container PostgreSQL
2. **Pool**: Aumentar POOL_MAX (até 50-75)
3. **Read replicas**: Para relatórios (não implementado)
4. **Sharding**: Por guild_id (requer mudança arquitetural)

---

## 9. Detalhes Técnicos

### Base de Dados (PostgreSQL)

#### Tabela `registros_diarios`

Armazena os registros de ponto com sistema de estados:

| Coluna | Tipo | Descrição |
|--------|------|-----------|
| `id` | SERIAL | Primary key auto-incremento |
| `channel_id` | TEXT | ID do canal/departamento |
| `user_id` | TEXT | ID do utilizador Discord |
| `data` | DATE | Data do registro (YYYY-MM-DD) |
| `first_on` | TIMESTAMP | Primeiro ON do dia |
| `finish` | TIMESTAMP | Horário do FINISH |
| `work_sum` | INTERVAL | Tempo total trabalhado (máx 6h30min) |
| `break_sum` | INTERVAL | Tempo total de pausas |
| `is_on` | BOOLEAN | Estado: trabalhando? |
| `is_break` | BOOLEAN | Estado: em pausa? |
| `is_finished` | BOOLEAN | Estado: dia finalizado? |
| `mobile_first_on` | BOOLEAN | Primeiro ON foi via mobile? |
| `last_transition` | TIMESTAMP | Última mudança de estado |
| `last_action` | TEXT | Última ação (ON/BREAK/FINISH) |

**Constraint:** `UNIQUE(channel_id, user_id, data)`

**Estados possíveis:**
- `is_on=TRUE, is_break=FALSE, is_finished=FALSE` → TRABALHANDO
- `is_on=FALSE, is_break=TRUE, is_finished=FALSE` → PAUSA
- `is_on=FALSE, is_break=FALSE, is_finished=TRUE` → AUSENTE (dia finalizado)
- `is_on=FALSE, is_break=FALSE, is_finished=FALSE` → NÃO INICIADO

#### Tabela `permissao_canal`

Controla permissões e bloqueios:

| Coluna | Tipo | Descrição |
|--------|------|-----------|
| `channel_id` | TEXT | ID do canal |
| `user_id` | TEXT | ID do utilizador/admin |
| `tipo` | TEXT | 'admin' ou 'locked_user' |
| `locked_at` | TIMESTAMP | Data/hora do bloqueio |

**Primary Key:** `(channel_id, user_id, tipo)`

**Tipos:**
- `admin` → Administrador do canal
- `locked_user` → Utilizador bloqueado no canal

**Check Constraint:** `tipo IN ('admin', 'locked_user')`

#### Tabela `channel_status_config`

Configurações de painéis de status:

| Coluna | Tipo | Descrição |
|--------|------|-----------|
| `channel_id` | TEXT | ID do canal (Primary Key) |
| `status_message_id` | TEXT | ID da mensagem de status |
| `updated_at` | TIMESTAMP | Última atualização |

**Uso:**
- Armazena ID da mensagem de status de cada canal
- Permite atualização automática dos painéis
- Restaurado na inicialização do bot via `restaurar_status_paineis()`

### Arquitetura Async de Base de Dados

#### Pool de Conexões Assíncronas

O sistema utiliza **psycopg3 AsyncConnectionPool** para gestão eficiente de conexões:

```python
# Configuração do pool em database.py
POOL_MIN = 20        # Conexões warm prontas para uso
POOL_MAX = 80       # Limite máximo de conexões concorrentes
POOL_TIMEOUT = 15   # Segundos para aguardar conexão disponível
POOL_MAX_WAITING = 100     # Máximo de coroutines em fila
POOL_MAX_LIFETIME = 3600   # Recicla conexões a cada 1h
POOL_RECONNECT_TIMEOUT = 300  # Auto-recuperação de falhas
```

**Características:**
- **Zero threads**: 100% non-blocking I/O via asyncio
- **Alta concorrência**: Suporta 200+ utilizadores simultâneos
- **Auto-recuperação**: Reconnect automático em falhas transientes
- **Pool resiliente**: Proteção contra pool exhaustion
- **Lock-free reads**: Queries SELECT não bloqueantes

#### Modelo de Concorrência

**Pattern de retry com pool:**
```python
async def _run(operation, retries=3):
    for attempt in range(retries):
        try:
            async with pool.connection() as conn:
                return await operation(conn)
        except OperationalError:
            await _replace_pool()  # Substitui pool corrompido
```

**Benefícios:**
- Conexão sempre retornada ao pool (mesmo com exceções)
- Python garante `__aexit__` via `try/finally` interno
- Sem manual `__aenter__`/`__aexit__` → zero leaks
- CancelledError, KeyboardInterrupt → pool intacto

#### Row-Level Locking (Prevenção de Race Conditions)

**SELECT FOR UPDATE** serializa modificações concorrentes:

```python
async with conn.transaction():
    await cursor.execute("""
        SELECT * FROM registros_diarios
        WHERE channel_id = %s AND user_id = %s AND data = %s
        FOR UPDATE  -- Lock exclusivo até COMMIT
    """, (channel_id, user_id, data))
    # Modificações in-memory...
    await _persist_record(conn, registro)  # UPDATE atômico
# COMMIT automático → lock liberado
```

**Cenário de concorrência:**
```
Thread A: User clica BREAK (200 concurrent users)
Thread B: User clica BREAK (double-click acidental)
         │
         ▼
A: SELECT FOR UPDATE → obtém lock na row
B: SELECT FOR UPDATE → AGUARDA lock de A
         │
A: calcula work_sum, UPDATE, COMMIT → libera lock
B: lê work_sum ATUALIZADO, detecta is_break=TRUE, rejeita
         │
Resultado: Zero lost updates, estado consistente
```

**Performance:**
- 200 utilizadores diferentes → 200 locks diferentes = zero contenção
- 2 requests do MESMO utilizador → segundo aguarda ~10ms
- Pool de 25 conexões → throughput de 2500+ ops/segundo

#### Gestão de Pool Lifecycle

**Inicialização (bot startup):**
```python
# Em bot.py setup_hook():
await database.init_database()
# 1. Cria pool com open=False
# 2. Aguarda pool.open() → min_size conexões prontas
# 3. Cria schema se não existir
```

**Operações normais:**
```python
# Cada função pública usa _run():
await _run(lambda conn: <operação dentro de transaction>)
# Pool automaticamente aloca/retorna conexões
# Retry em OperationalError (server restart, network drop)
```

**Shutdown gracioso (bot close):**
```python
# Em bot.py on_close():
await database.close_pool()
# 1. Adquire _pool_lock (serializa shutdown)
# 2. Drena conexões ativas (5s timeout)
# 3. Fecha pool
# 4. Marca _pool = None
```

#### Proteções de Segurança

**1. Pool Lock (_pool_lock):**
- Previne criação concorrente de múltiplos pools
- Serializa `create_pool()`, `_replace_pool()`, `close_pool()`
- Usa `asyncio.Lock()` para coordenação async

**2. Pool Replacement (não reset):**
- `_replace_pool()` drena pool antigo antes de criar novo
- Janela de 5s para operações in-flight completarem
- Novo pool só assume após estar 100% aberto

**3. Isolamento de Transações:**
- Todas operações write em `async with conn.transaction()`
- Rollback automático em exceções
- MVCC do PostgreSQL previne dirty reads

#### Requisitos de Dependências

```txt
# requirements.txt
psycopg[binary]>=3.1.4  # Driver async nativo
psycopg[pool]>=3.0.0    # AsyncConnectionPool
discord.py==2.3.2       # Framework async Discord
```

**Importante:** `psycopg[pool]` fornece `AsyncConnectionPool` — diferente do `ConnectionPool` sync (descontinuado para este caso de uso).

### Fluxo de Dados e Arquitetura de Rede

A comunicação entre o bot e a base de dados é feita de forma **assíncrona e escalável**, permitindo que dezenas ou centenas de utilizadores interajam simultaneamente sem bloqueios. Eis o fluxo técnico:

- **O bot é um processo único** (Python + `discord.py`) que corre num servidor (VPS).
- **Cada interação** (clique num botão, comando slash) gera um evento assíncrono.
- **Pool de conexões** – O bot mantém um conjunto de ligações à base de dados PostgreSQL (entre 20 e 80, configurável). Sempre que precisa de aceder à base, obtém uma ligação do *pool* e, após a operação, devolve‑a. Isto evita criar uma nova ligação para cada pedido, o que seria lento.
- **Concorrência** – Vários utilizadores podem clicar ao mesmo tempo. O *pool* distribui as ligações disponíveis; se não houver, os pedidos aguardam em fila (até 300 em espera).
- **Atomicidade** – Para evitar que dois cliques do mesmo utilizador causem dados inconsistentes (ex.: dois `BREAK` ao mesmo tempo), o bot usa **bloqueio ao nível da linha** (`SELECT ... FOR UPDATE` dentro de uma transação). A primeira transação bloqueia o registo; a segunda espera até a primeira terminar e depois vê o estado atualizado, recusando a ação se já não for válida.
- **Persistência** – A base de dados corre num contentor Docker separado, mas os ficheiros estão num volume montado no disco do servidor. Quando o contentor da base é recriado (ex.: após um *update*), os dados mantêm‑se porque o volume é reutilizado.
- **Comunicação rede** – O bot e a base estão na mesma máquina, ligados pela rede interna do Docker (ou `localhost`). A variável `POSTGRES_HOST=db` no `.env` aponta para o nome do serviço no `docker-compose.yml`.

#### Exemplo de um clique no botão ON:

1. O bot recebe o evento `interaction`.
2. Obtém uma ligação do *pool*.
3. Inicia uma transação.
4. Verifica se o utilizador já está bloqueado noutro canal.
5. Obtém o registo do dia (ou cria‑o) com `SELECT ... FOR UPDATE`.
6. Atualiza o registo em memória e executa um `UPDATE`.
7. Dá `COMMIT` (fim da transação).
8. Devolve a ligação ao *pool*.
9. Envia a resposta ao Discord.

Graças a esta arquitetura, o sistema suporta **centenas de utilizadores ativos** com latência baixa e sem perda de dados.

---

### Porque a base de dados não precisa estar exposta?

A base de dados PostgreSQL **nunca é exposta à internet** por duas razões principais:

- **Segurança:** Se a base estivesse acessível publicamente, qualquer pessoa poderia tentar ataques de força bruta, explorar vulnerabilidades ou até mesmo apagar dados. Manter a base isolada na rede interna do servidor reduz drasticamente a superfície de ataque.
- **Arquitetura:** O único cliente que precisa aceder à base é o próprio bot. Como ambos correm no mesmo servidor (ou na mesma rede Docker), a comunicação pode ser feita através de `localhost` ou do nome do serviço (`db` no Docker Compose), sem necessidade de expor portas.

---

### Resumo do fluxo de dados
```
[ Utilizador Discord ]
⇩ (clique no botão)
[ API do Discord ]
⇩ (evento HTTPS/WebSocket)
[ Bot (container) na VPS ]
⇩ (consulta SQL via localhost/rede interna)
[ PostgreSQL (container) na mesma VPS ]
⇩ (resposta)
[ Bot ]
⇩ (resposta à API)
[ Discord ]
⇩ (mensagem para o utilizador)
```


A base de dados **nunca** é contactada diretamente pelo utilizador ou pela internet – apenas o bot, que está na mesma máquina, tem acesso. Isto garante isolamento total e segurança dos dados registados.

---

### Fluxo de Ações

#### Fluxo do ON

```
1. Utilizador clica ON
   ↓
2. Detecta dispositivo (desktop/mobile/web)
   ↓
3. Verifica bloqueio de canal
   - get_user_locked_channel(user_id)
   - Se não bloqueado: lock_user_to_channel()
   - Se bloqueado em outro canal: retorna erro
   ↓
4a. Se primeiro ON do dia:
    - Registra first_on = datetime.now()
    - Define mobile_first_on = (dispositivo == "mobile")
    - Define is_on = TRUE
    - Salva last_transition e last_action
   ↓
4b. Se retorno de pausa:
    - Calcula duracao_pausa = now - last_transition
    - Adiciona a break_sum
    - Define is_on = TRUE, is_break = FALSE
    - Salva last_transition e last_action
   ↓
5. Atualiza painel de status via update_status()
   ↓
6. Retorna mensagem de sucesso
```

#### Fluxo do BREAK

```
1. Utilizador clica BREAK
   ↓
2. Verifica se está trabalhando (is_on = TRUE)
   - Se não: retorna erro
   ↓
3. Calcula tempo desde last_transition
   duracao_trabalho = now - last_transition
   ↓
4. Adiciona a work_sum
   novo_work_sum = work_sum + duracao_trabalho
   ↓
5. Verifica limite de 6h30min
   ↓
5a. Se novo_work_sum >= MAX_WORK_HOURS:
    - Limita work_sum = MAX_WORK_HOURS
    - Calcula tempo_excedente
    - Define is_finished = TRUE
    - Retorna mensagem de dia finalizado
   ↓
5b. Se novo_work_sum < MAX_WORK_HOURS:
    - Atualiza work_sum = novo_work_sum
    - Define is_on = FALSE, is_break = TRUE
    - Salva last_transition e last_action
   ↓
6. Atualiza painel de status
   ↓
7. Retorna mensagem apropriada
```

#### Fluxo do FINISH

```
1. Utilizador clica FINISH
   ↓
2. Verifica se está trabalhando (is_on = TRUE)
   - Se não: retorna erro
   ↓
3. Calcula tempo desde last_transition
   duracao_trabalho = now - last_transition
   ↓
4. Adiciona a work_sum
   novo_work_sum = work_sum + duracao_trabalho
   ↓
5. Verifica limite de 6h30min
   ↓
5a. Se novo_work_sum >= MAX_WORK_HOURS:
    - Limita work_sum = MAX_WORK_HOURS
    - (tempo excedente é silenciosamente ignorado)
   ↓
5b. Se novo_work_sum < MAX_WORK_HOURS:
    - Atualiza work_sum = novo_work_sum
   ↓
6. Registra finish = now
   ↓
7. Define is_on = FALSE, is_finished = TRUE
   ↓
8. Salva last_transition e last_action
   ↓
9. Atualiza painel de status
   ↓
10. Retorna mensagem "Dia finalizado!"
```

### Fluxo de Transferência

```
1. Super Admin executa /admin_transfer_user
   ↓
2. Sistema busca canal de origem
   locked_channel = get_user_locked_channel(user_id)
   ↓
3. Validações:
   - Utilizador existe e está bloqueado?
   - Já está no destino?
   - Canais são diferentes?
   ↓
4. Cria ConfirmTransferView com:
   - user_id
   - origem_channel_id
   - destino_channel_id
   ↓
5. Exibe confirmação com informações
   ↓
6. Ao confirmar:
   a. UPDATE registros_diarios 
      SET channel_id = destino 
      WHERE channel_id = origem AND user_id = user
   b. UPDATE permissao_canal 
      SET channel_id = destino 
      WHERE user_id = user AND tipo = 'locked_user'
   c. Conta registros_transferidos (cursor.rowcount)
   d. commit()
   ↓
7. Atualiza painéis de status:
   - update_status(origem_channel_id)
   - update_status(destino_channel_id)
   ↓
8. Retorna mensagem de sucesso com número de registros
```

### Sistema de Atualização de Status

```python
async def update_status(channel_id: int):
    """Atualiza o painel de status de um canal"""
    # 1. Busca message_id do painel
    message_id = get_status_message(str(channel_id))
    
    # 2. Busca status atual de todos utilizadores
    status = status_atual_users(str(channel_id))
    
    # 3. Constrói texto formatado com emojis
    # 🟢 TRABALHANDO
    # 🟡 PAUSA
    # 🔴 AUSENTE
    
    # 4. Atualiza mensagem via message.edit()
```

**Chamado em:**
- Após cada ação (ON/BREAK/FINISH)
- Após transferências
- Na inicialização do bot (restaurar_status_paineis)

### Cálculo de Médias no Relatório Detalhado

O comando `/report_user_detailed` calcula estatísticas do período:

```python
def media_hora(lista):
    """Calcula média de horários em segundos"""
    return sum(h.hour * 3600 + h.minute * 60 + h.second 
               for h in lista) / len(lista)

# 1. Busca registros do período
registros = period_report(channel_id, user_id, data_inicio, data_fim)

# 2. Filtra apenas dias úteis (segunda a sexta)
# Ignora: data.weekday() >= 5

# 3. Calcula médias
entradas = [r[1] for r in registros if r[1]]  # first_on
saidas = [r[2] for r in registros if r[2]]    # finish

media_entrada = media_hora(entradas)  # segundos
media_saida = media_hora(saidas)      # segundos

# 4. Calcula totais
total_trabalho = sum(r[3] for r in registros)  # work_sum
total_break = sum(r[4] for r in registros)     # break_sum

# 5. Calcula médias diárias
media_trabalho = total_trabalho / dias_uteis
media_break = total_break / dias_uteis
```

**Importante:** 
- Apenas dias com `is_finished = TRUE` são considerados
- Fins de semana (sábado e domingo) são ignorados
- Se não informar datas, usa `first_last_record()`

### Detecção de Dispositivo

```python
def detect_device(interaction: discord.Interaction) -> str:
    """Detecta o dispositivo do utilizador"""
    member = interaction.guild.get_member(interaction.user.id)
    if not member:
        return "unknown"

    if member.desktop_status != discord.Status.offline:
        return "desktop"
    elif member.mobile_status != discord.Status.offline:
        return "mobile"
    elif member.web_status != discord.Status.offline:
        return "web"
    
    return "unknown"
```

**Uso:**
- Chamado em cada ação (ON/BREAK/FINISH)
- Armazenado apenas no primeiro ON (`mobile_first_on`)
- Usado em `/report_user_mobile`

### Formatação de Tempo

```python
def format_timedelta(td):
    """Formata timedelta para HH:MM:SS"""
    if not td:
        return "00:00:00"
    total_seconds = int(td.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
```

**Exemplo:**
- Input: `timedelta(hours=8, minutes=30, seconds=45)`
- Output: `"08:30:45"`

### Persistência de Dados

- Base de dados PostgreSQL armazenada em `./data/postgres/`
- Dados persistem mesmo após restart do container
- **Bloqueios são preservados** entre restarts
- **Estados dos utilizadores são preservados** (is_on, is_break, is_finished)
- **Configurações de painéis são preservadas** (channel_status_config)
- Backups devem incluir a pasta `./data/`

### Formato de Exportação CSV

```csv
channel_id,user_id,data,first_on,finish,work_sum,break_sum,is_finished,mobile_first_on
1234567890,9876543210,2026-01-21,2026-01-21 09:00:00,2026-01-21 18:00:00,06:30:00,01:00:00,True,False
```

**Colunas:**
- `channel_id` - ID do canal Discord
- `user_id` - ID do utilizador Discord
- `data` - Data do registro (YYYY-MM-DD)
- `first_on` - Timestamp do primeiro ON (ou vazio)
- `finish` - Timestamp do FINISH (ou vazio)
- `work_sum` - Tempo trabalhado formatado (HH:MM:SS)
- `break_sum` - Tempo de pausas formatado (HH:MM:SS)
- `is_finished` - True/False (dia completo?)
- `mobile_first_on` - True/False (primeiro ON via mobile?)

### Conexão com PostgreSQL

O sistema utiliza **psycopg3 AsyncConnectionPool** para gestão eficiente e assíncrona de conexões:

```python
from psycopg_pool import AsyncConnectionPool
from psycopg import AsyncConnection

# Criação do pool (em database.py)
pool = AsyncConnectionPool(
    conninfo="host=db port=5432 dbname=ponto user=postgres password=...",
    min_size=5,        # Conexões warm
    max_size=25,       # Limite de conexões
    timeout=15,        # Timeout para obter conexão
    open=False         # Abertura manual com await
)
await pool.open(wait=True)  # Aguarda min_size conexões prontas

# Uso em operações (pattern interno via _run())
async with pool.connection() as conn:
    async with conn.transaction():
        async with conn.cursor() as cursor:
            await cursor.execute("SELECT ...")
            result = await cursor.fetchone()
```

**Características da arquitetura async:**
- **Pool de conexões** - 5-25 conexões geridas automaticamente
- **Non-blocking I/O** - Todas operações DB são async (await)
- **Retry automático** - Recuperação de falhas transientes (OperationalError)
- **Transaction safety** - Commit/rollback automático via context managers
- **Zero threads** - 100% asyncio, sem bloqueio do event loop
- **Row-level locking** - SELECT FOR UPDATE previne race conditions
- **Pool lifecycle** - Criação, substituição e shutdown coordenados via locks

**Diferenças vs. abordagem síncrona:**
| Aspecto | Sync (antigo) | Async (atual) |
|---------|---------------|---------------|
| Conexões | 1 global compartilhada | Pool 5-25 isoladas |
| Concorrência | Serial (1 op por vez) | Paralela (25 ops simultâneas) |
| Bloqueio | Bloqueia event loop | Non-blocking |
| Throughput | ~10-20 ops/seg | ~2500 ops/seg |
| Race conditions | Possíveis | Protegidas (FOR UPDATE) |
| Recuperação | Manual | Automática |

### Gestão de Timezone (UTC)

O sistema trabalha exclusivamente com UTC (timezone-aware):

```python
def utcnow():
    """Returns the current datetime in UTC (timezone-aware)."""
    return datetime.now(timezone.utc)

def ensure_utc(dt):
    """Guarantees that datetime is timezone-aware in UTC"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
```

**Vantagens:**
- Consistência em todas as operações
- Evita problemas com fusos horários
- Discord aceita timestamps UTC
- Comparações de tempo precisas

**Uso nos registros:**
- Todos os timestamps armazenados são UTC
- `last_transition` sempre em UTC
- Discord Timestamps: `<t:unix_timestamp:format>`
- Conversão automática para timezone do utilizador no Discord

---

## 10. Testes Automatizados

Para informações detalhadas sobre a execução de testes, estrutura do projeto de testes e configuração da integração contínua, consulte o **[Guia de Integração Contínua (CI)](CONTRIBUTING.md)**.

Em resumo, o projeto possui uma suíte abrangente de **testes unitários** que cobre tanto a lógica do bot (`bot.py`) quanto as operações da base de dados (`database.py`). Os testes estão organizados em:

- `tests/test_bot.py` – testa comandos, views, permissões e fluxos completos do bot.
- `tests/test_database.py` – testa todas as funções da base de dados (registos, bloqueios, relatórios, etc.).

### Executar testes localmente

Com as dependências de desenvolvimento instaladas (`pip install -r requirements-dev.txt`), basta executar:

```bash
pytest tests/ -v --cov=bot --cov=database --cov-report=term-missing
```

Este comando apresenta um resumo detalhado da cobertura de código, mostrando as linhas não cobertas.

### Integração Contínua

Sempre que há um *push* ou *pull request*, o GitHub Actions executa automaticamente toda a suíte de testes, gera relatórios de cobertura e, se os testes passarem, constrói e publica uma nova imagem Docker no GitHub Container Registry (GHCR). Todo este processo está documentado no **[Guia de Integração Contínua (CI)](CONTRIBUTING.md)**.

Para mais informações – como executar testes isolados, interpretar relatórios de cobertura ou compreender o pipeline de CI – consulte o guia dedicado.

---

## 11. Observações Importantes

### Limitações

1. **Um departamento por utilizador:**
   - Cada utilizador só pode estar ativo em um departamento
   - Mudanças requerem transferência formal por Super Admin
   - Transferências preservam todo o histórico
   - Não existe "multi-departamento simultâneo"

2. **Sistema de 3 estados (ON/BREAK/FINISH):**
   - Não permite múltiplas sessões de trabalho após FINISH
   - Um FINISH por dia
   - Para novo dia, novo conjunto de registros
   - Estados são mutuamente exclusivos

3. **Limite de 6h30min é fixo:**
   - Definido em `MAX_WORK_HOURS` no `database.py`
   - Aplicado automaticamente em BREAK e FINISH
   - Tempo excedente é ignorado (não armazenado)
   - Para alterar, modificar código e rebuild

4. **Painel de status requer bot online:**
   - Se o bot reiniciar, o painel não atualiza automaticamente até próxima interação
   - Estados são preservados na base de dados
   - Bloqueios são preservados mesmo com bot offline
   - Restauração automática via `restaurar_status_paineis()` no `on_ready`

5. **Exclusões são irreversíveis:**
   - `/admin_delete_data` apaga permanentemente os dados **do canal**
   - Não afeta o bloqueio global do utilizador
   - Sempre confirme com confirmação dupla antes de prosseguir
   - Não há "lixeira" ou backup automático

6. **Super Admins são definidos em código:**
   - Não há comando para adicionar Super Admins
   - Requer edição do `bot.py` e rebuild
   - Necessário para segurança do sistema
   - Lista em `SUPER_ADMINS = [...]`

7. **Apenas Super Admins podem adicionar administradores:**
   - Admins de canal NÃO podem adicionar outros admins
   - Centralização do controle de permissões
   - Evita escalação não autorizada de privilégios
   - Validado via `check_super_admin()`

8. **Fins de semana são considerados:**
   - Permitido registrar em qualquer dia
   - Relatórios ignoram sábados e domingos nas médias
   - Verificado via `data.weekday() >= 5`
   - Total de horas inclui todos os dias

### Regras de Negócio

1. **Primeiro ON:**
   - Deve ser o primeiro registro do dia
   - Define `first_on` e `mobile_first_on`
   - Inicia estado TRABALHANDO (`is_on = TRUE`)
   - Bloqueia utilizador no canal (se ainda não bloqueado)

2. **BREAK:**
   - Só pode ser acionado quando TRABALHANDO
   - Calcula e acumula tempo trabalhado em `work_sum`
   - Muda estado para PAUSA (`is_break = TRUE`)
   - Se atingir 6h30min, fecha automaticamente o dia

3. **ON após BREAK:**
   - Só pode ser acionado quando em PAUSA
   - Calcula e acumula tempo de pausa em `break_sum`
   - Retorna ao estado TRABALHANDO
   - `last_transition` atualizado para momento do ON

4. **FINISH:**
   - Só pode ser acionado quando TRABALHANDO
   - Calcula tempo final trabalhado
   - Bloqueia novos registros no dia (`is_finished = TRUE`)
   - Estado final: AUSENTE
   - Registra `finish` timestamp

5. **Finais de Semana:**
   - Permitido registrar em qualquer dia
   - Relatórios ignoram sábados e domingos nas médias
   - Dias úteis = segunda a sexta-feira
   - Total de horas inclui todos os dias

6. **Transferências:**
   - Apenas Super Admins podem executar
   - Move **todos** os registros históricos
   - Atualiza bloqueio para novo canal
   - Não cria duplicatas
   - Transação atômica (tudo ou nada)

### Troubleshooting

**Utilizador não consegue dar ponto em nenhum canal:**
```bash
# Super Admin deve verificar bloqueios
/admin_view_locks

# Verificar na base de dados
docker exec -it discord-ponto-db psql -U postgres ponto
SELECT * FROM permissao_canal WHERE user_id = 'ID_AQUI' AND tipo = 'locked_user';

# Se necessário, desbloquear manualmente
DELETE FROM permissao_canal WHERE user_id = 'ID_AQUI' AND tipo = 'locked_user';

# Ou transferir para canal correto
/admin_transfer_user @utilizador #canal-correto
```

**Bot não inicia:**
```bash
# Verificar se PostgreSQL está pronto
docker-compose logs db

# Verificar variáveis de ambiente
cat .env

# Recriar containers
docker-compose down
docker-compose up -d --build

# Ver logs em tempo real
docker-compose logs -f discord-bot
```

**Comandos não aparecem:**
```bash
# Verificar GUILD_ID no .env
grep GUILD_ID .env

# Verificar se o bot está online
docker-compose ps

# Aguardar até 1 hora para sincronização
# Ou forçar dessincronização e ressincronização no código

# Verificar logs
docker-compose logs discord-bot | grep "Synchronized commands"
```

**Dados não persistem:**
```bash
# Verificar se a pasta data/postgres existe
ls -la data/postgres

# Verificar permissões
sudo chown -R 999:999 data/postgres

# Verificar volume no docker-compose.yml
docker-compose config | grep volumes

# Verificar se o volume está montado
docker inspect discord-ponto-db | grep Mounts
```

**Transferência não funciona:**
```bash
# Verificar se o utilizador tem registros
/report_user_detailed @utilizador

# Verificar bloqueio atual
/admin_view_locks

# Verificar no banco
docker exec -it discord-ponto-db psql -U postgres ponto
SELECT COUNT(*) FROM registros_diarios WHERE user_id = 'ID_AQUI';

# Logs do sistema
docker-compose logs discord-bot | grep transfer
```

**Painel de status não atualiza:**
```bash
# Forçar atualização fazendo qualquer registro
# Ou recriar o painel:
/config_status_panel

# Verificar se a mensagem existe
docker exec -it discord-ponto-db psql -U postgres ponto
SELECT * FROM channel_status_config;

# Ver logs
docker-compose logs discord-bot | grep "update_status"
```

**Estados inconsistentes:**
```bash
# Verificar último registro no banco
docker exec -it discord-ponto-db psql -U postgres ponto
SELECT * FROM registros_diarios 
WHERE user_id = 'ID_AQUI' AND data = CURRENT_DATE;

# Estados são baseados em:
# - is_on, is_break, is_finished
# - last_transition e last_action

# Se necessário, corrigir manualmente
UPDATE registros_diarios 
SET is_on = FALSE, is_break = FALSE, is_finished = TRUE
WHERE user_id = 'ID_AQUI' AND data = CURRENT_DATE;

# Ou deletar o registro do dia e recomeçar
DELETE FROM registros_diarios 
WHERE user_id = 'ID_AQUI' AND data = CURRENT_DATE;
```

**Limite de 6h30min não funciona:**
```bash
# Verificar constante no código
grep "MAX_WORK_HOURS" database.py

# Deve mostrar:
# MAX_WORK_HOURS = timedelta(hours=6, minutes=30)

# Se alterou, rebuild obrigatório
docker-compose down
docker-compose up -d --build
```

**Erro de conexão PostgreSQL:**
```bash
# Verificar se o serviço está rodando
docker-compose ps

# Verificar logs do PostgreSQL
docker-compose logs db

# Testar conexão manual
docker exec -it discord-ponto-db psql -U postgres ponto

# Verificar variáveis de ambiente
docker exec discord-bot env | grep POSTGRES
```

---

## 12. Exemplos de Uso

### Exemplo 1: Dia Normal de Trabalho

```
09:00 - João clica ON
→ "🟢 Primeiro ON do dia registrado! Bom trabalho!"
→ Status: 🟢 João - Working since 09:00:00

12:30 - João clica BREAK
→ "🟡 Pausa iniciada! Sessão de trabalho: 03:30:00"
→ Status: 🟡 João - On break since 12:30:00

13:30 - João clica ON
→ "🟢 Retorno da pausa registrado! Pausa durou: 01:00:00"
→ Status: 🟢 João - Working since 13:30:00

18:00 - João clica FINISH
→ "🔴 Dia finalizado!"
→ Status: 🔴 João - Absent since 18:00:00 on 21/01/2026

Dados armazenados:
- first_on: 09:00:00
- finish: 18:00:00
- work_sum: 06:30:00 (limite aplicado: 3h30 + 4h30 = 8h → limitado a 6h30)
- break_sum: 01:00:00
- is_finished: TRUE
- mobile_first_on: FALSE
```

### Exemplo 2: Dia com Limite Atingido no BREAK

```
08:00 - Maria clica ON
→ "🟢 Primeiro ON do dia registrado! Bom trabalho!"

14:35 - Maria clica BREAK (6h35min trabalhadas)
→ "🔴 Dia automaticamente finalizado (6h30min atingidas)!"
→ "⏱️ Trabalho total: 06:30:00"
→ "☕ Pausas totais: 00:00:00"
→ "ℹ️ Tempo excedente ignorado: 00:05:00"
→ Status: 🔴 Maria - Absent since 14:35:00 on 21/01/2026

Dados armazenados:
- first_on: 08:00:00
- finish: 14:35:00
- work_sum: 06:30:00 (limitado automaticamente)
- break_sum: 00:00:00
- is_finished: TRUE
- mobile_first_on: FALSE
```

### Exemplo 3: Relatório Mensal Completo

```
Admin executa:
/report_user_detailed @João data_inicio:01/01/2026 data_fim:31/01/2026

Resultado:
📊 Detailed Records - João
📅 Period: 01/01/2026 → 31/01/2026

🟢 Average start time: 09:05:32
🔴 Average finish time: 18:12:45

⏱️ Average daily hours worked: 06:15:23
🧮 Total hours worked: 131:15:00
☕ Average daily breaks: 01:02:15
📌 Total breaks during the period: 21:47:00
📆 Working days considered: 21

Explicação:
- 21 dias úteis (segunda a sexta)
- Fins de semana ignorados nos cálculos
- Média de entrada: ~09:05
- Média de saída: ~18:12
- Média diária: 6h15min (abaixo do limite de 6h30min)
- Total de pausas: 21h47min no mês
```

### Exemplo 4: Transferência de Departamento

```
Situação inicial:
- Maria está em #marketing
- Tem 156 registros históricos
- Bloqueada em #marketing

Super Admin executa:
/admin_transfer_user @Maria canal_destino:#vendas

Sistema mostra:
⚠️ Transfer Confirmation

👤 User: @Maria
📤 From: #marketing
📥 To: #vendas

All records will be moved. Do you wish to continue?
[Confirmar Transferência] [Cancelar]

Admin clica [Confirmar Transferência]:

Resultado:
✅ User transferred successfully!
📊 156 record(s) moved
📤 From: #marketing
📥 To: #vendas

Efeitos:
- 156 registros movidos para #vendas
- Bloqueio atualizado para #vendas
- Painéis de status de ambos canais atualizados
- Maria agora registra ponto apenas em #vendas
```

### Exemplo 5: Registros via Mobile

```
Admin executa:
/report_user_mobile @Pedro data_inicio:01/01/2026 data_fim:31/01/2026

Resultado:
📱 Records via mobile phone - Pedro
📅 Period: 01/01/2026 → 31/01/2026

• 03/01/2026 - First ON: 09:15:00
• 10/01/2026 - First ON: 09:20:00
• 17/01/2026 - First ON: 09:25:00
• 24/01/2026 - First ON: 09:30:00
• 31/01/2026 - First ON: 09:35:00

Total: 5 day(s)

Interpretação:
- Pedro usou mobile em 5 dias
- Possível padrão de atraso quando usa mobile
- Útil para análise de pontualidade
```

### Exemplo 6: Exportação CSV

```
Admin executa (em #recursos-humanos):
/report_export_csv

Sistema gera:
📄 CSV generated for recursos-humanos!
[Download: registros_recursos-humanos_20260127.csv]

Conteúdo do CSV:
channel_id,user_id,data,first_on,finish,work_sum,break_sum,is_finished,mobile_first_on
1458825338313244767,1421228145482268715,2026-01-27,2026-01-27 09:00:00,2026-01-27 18:00:00,06:30:00,01:00:00,True,False
1458825338313244767,9876543210987654321,2026-01-27,2026-01-27 09:15:00,2026-01-27 18:15:00,06:30:00,01:15:00,True,True
1458825338313244767,1111111111111111111,2026-01-27,2026-01-27 08:45:00,,,00:00:00,00:00:00,False,False

Uso:
- Importar no Excel/Google Sheets
- Análises personalizadas
- Integração com sistemas de RH
- Backup de dados
```

### Exemplo 7: Gestão de Administradores

```
Super Admin em #ti:

1. Adicionar admin:
/config_admins_add @Carlos
→ "✅ Carlos now is the administrator of ti."

2. Listar admins:
/config_admins_list
→ "📋 Administrators of ti:
   • Carlos (@Carlos)
   • Ana (@Ana)"

3. Remover admin:
/config_admins_remove @Ana
→ "✅ Ana removed as administrator of ti."

4. Verificar novamente:
/config_admins_list
→ "📋 Administrators of ti:
   • Carlos (@Carlos)"

Resultado:
- Apenas Carlos pode gerenciar #ti
- Super Admins continuam com acesso total
- Ana não pode mais executar comandos em #ti
```

### Exemplo 8: Ver Bloqueios Ativos

```
Super Admin executa:
/admin_view_locks

Resultado:
📋 Registered Users by Department

• João → #recursos-humanos
• Maria → #vendas
• Pedro → #ti
• Carlos → #marketing
• Ana → #recursos-humanos
• Roberto → #ti
• Juliana → #vendas

Informações úteis:
- 7 utilizadores registrados
- 4 departamentos ativos
- #recursos-humanos: 2 pessoas
- #vendas: 2 pessoas
- #ti: 2 pessoas
- #marketing: 1 pessoa
```

### Exemplo 9: Tentativa de Registro em Canal Bloqueado

```
Situação:
- Pedro está bloqueado em #ti
- Tenta registrar em #vendas

Pedro clica ON em #vendas:
→ "⚠️ Você está registrado no departamento #ti. 
   Contacte um Super Admin para transferência."

Pedro solicita a um Super Admin:
"Olá, preciso ser transferido de #ti para #vendas"

Super Admin executa:
/admin_transfer_user @Pedro canal_destino:#vendas
[Confirma a transferência]

Resultado:
→ "✅ User transferred successfully!
   📊 89 record(s) moved
   📤 From: #ti
   📥 To: #vendas"

Agora Pedro pode registrar em #vendas
```

### Exemplo 10: Deletar Registros de Utilizador

```
Admin em #marketing:
/admin_delete_data @Roberto

Sistema mostra:
⚠️ Are you sure you want to delete all records from Roberto 
in the department marketing?

This action is irreversible.
[Confirmar Delete] [Cancelar]

Admin clica [Confirmar Delete]:

Resultado:
✅ User records successfully deleted in this department.

Efeitos:
- Todos os registros de Roberto em #marketing são apagados
- Bloqueio de Roberto permanece (ainda está vinculado a #marketing)
- Painel de status atualizado
- Histórico perdido permanentemente
- Roberto pode recomeçar do zero em #marketing
```

---

## 13. Variáveis de Ambiente

O sistema utiliza variáveis de ambiente definidas no arquivo `.env` para configuração. Abaixo está a descrição completa de cada variável:

### Configuração do Discord

```env
DISCORD_TOKEN=...
```
- **Descrição**: Token de autenticação do bot Discord
- **Obtenção**: 
  1. Acesse https://discord.com/developers/applications
  2. Selecione ou crie sua aplicação
  3. Vá em "Bot" no menu lateral
  4. Copie o token em "TOKEN"
- **Segurança**: NUNCA compartilhe este token publicamente

```env
GUILD_ID=...
```
- **Descrição**: ID do servidor Discord onde o bot operará
- **Obtenção**:
  1. Ative o "Modo Desenvolvedor" no Discord (Configurações > Avançado)
  2. Clique com botão direito no servidor
  3. Selecione "Copiar ID"
- **Uso**: Define onde os comandos slash serão registrados

### Configuração do PostgreSQL

```env
POSTGRES_HOST=db
```
- **Descrição**: Hostname do servidor PostgreSQL
- **Valor padrão**: `db` (nome do serviço no docker-compose)
- **Nota**: Não altere se estiver usando Docker Compose

```env
POSTGRES_PORT=5432
```
- **Descrição**: Porta do servidor PostgreSQL
- **Valor padrão**: `5432` (porta padrão do PostgreSQL)
- **Uso**: Porta interna entre containers

```env
POSTGRES_DB=ponto
```
- **Descrição**: Nome da base de dados a ser criado/utilizado
- **Valor padrão**: `ponto`
- **Nota**: Pode ser alterado conforme preferência

```env
POSTGRES_USER=postgres
```
- **Descrição**: Nome de usuário do PostgreSQL
- **Valor padrão**: `postgres` (superusuário padrão)
- **Nota**: Para produção, considere criar um usuário específico

```env
POSTGRES_PASSWORD=senha123
```
- **Descrição**: Senha do usuário PostgreSQL
- **Segurança**: **ALTERE IMEDIATAMENTE EM PRODUÇÃO**
- **Recomendações**:
  - Use senha forte (mínimo 16 caracteres)
  - Misture letras, números e símbolos
  - Não use senhas óbvias ou dicionário

### Exemplo de arquivo `.env` completo para produção:

```env
# Discord Configuration
DISCORD_TOKEN=seu_token_real_aqui
GUILD_ID=seu_guild_id_aqui

# PostgreSQL Configuration
POSTGRES_HOST=db
POSTGRES_PORT=5432
POSTGRES_DB=ponto
POSTGRES_USER=postgres
POSTGRES_PASSWORD=SuaSenhaForteMuitoSegura123!@#
```

### Variáveis no Código

Além das variáveis de ambiente, há configurações importantes no código:

#### Em `bot.py`:

```python
# Guild ID (também pode vir do .env)
GUILD_ID = 1458825338313244767
```

#### Em `database.py`:

```python
# Limite de horas trabalhadas por dia
MAX_WORK_HOURS = timedelta(hours=6, minutes=30)
```

### Troubleshooting de Variáveis

**Erro: "discord.errors.LoginFailure: Improper token has been passed"**
```bash
# Verificar se o token está correto
cat .env | grep DISCORD_TOKEN

# Token deve começar com MTQ... ou similar
# Se estiver errado, regenere no Discord Developer Portal
```

**Erro: "psycopg.OperationalError: connection failed"**
```bash
# Verificar todas as variáveis PostgreSQL
cat .env | grep POSTGRES

# Testar conexão manual
docker exec -it discord-ponto-db psql -U postgres -d ponto

# Se falhar, verificar se o serviço está rodando
docker-compose logs db
```

**Comandos não sincronizam:**
```bash
# Verificar GUILD_ID
cat .env | grep GUILD_ID

# Deve ser numérico e corresponder ao seu servidor
# Verificar no Discord (Modo Desenvolvedor ativado)
```

---

## 14. Dockerfile

O projeto inclui um Dockerfile para containerização do bot Discord. Abaixo está a explicação detalhada de cada seção:

### Estrutura do Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py database.py ./

CMD ["python", "-u", "bot.py"]
```

### Explicação Linha por Linha

#### Base Image
```dockerfile
FROM python:3.11-slim
```
- **Descrição**: Usa a imagem oficial Python 3.11 na versão slim
- **Vantagens**:
  - Imagem leve (~120MB vs ~900MB da versão completa)
  - Contém apenas essenciais do Python
  - Adequada para aplicações simples

#### Working Directory
```dockerfile
WORKDIR /app
```
- **Descrição**: Define `/app` como diretório de trabalho
- **Efeito**: Todos os comandos subsequentes são executados neste diretório
- **Criação**: Se não existir, será criado automaticamente

#### Copy Requirements
```dockerfile
COPY requirements.txt .
```
- **Descrição**: Copia arquivo de dependências para o container
- **Otimização**: Feito antes do código para aproveitar cache do Docker
- **Cache**: Se `requirements.txt` não mudar, a camada é reutilizada

#### Install Dependencies
```dockerfile
RUN pip install --no-cache-dir -r requirements.txt
```
- **Descrição**: Instala dependências Python
- **Flags**:
  - `--no-cache-dir`: Não salva cache do pip (economiza espaço)
- **Dependências instaladas**:
  ```
  discord.py==2.3.2
  psycopg[binary]>=3.1.4
  ```

#### Copy Application Code
```dockerfile
COPY bot.py database.py ./
```
- **Descrição**: Copia arquivos Python do bot para o container
- **Localização**: Copiados para `/app/` (definido em WORKDIR)
- **Nota**: `.env` NÃO é copiado (injetado via docker-compose)

#### Run Command
```dockerfile
CMD ["python", "-u", "bot.py"]
```
- **Descrição**: Comando executado quando o container inicia
- **Flags**:
  - `-u`: Unbuffered output (logs aparecem imediatamente)
- **Formato**: Exec form (preferível a shell form)

### Integração com Docker Compose

O Dockerfile é referenciado no `docker-compose.yml`:

```yaml
services:
  discord-bot:
    build: .  # ← Usa o Dockerfile no diretório atual
    container_name: discord-ponto-bot
    restart: always
    volumes:
      - ./data:/app/data
    env_file:
      - .env
    depends_on:
      - db
```

### Troubleshooting do Dockerfile

**Erro: "ModuleNotFoundError: No module named 'discord'"**
```bash
# Verificar se requirements.txt está correto
cat requirements.txt

# Rebuild forçado sem cache
docker-compose build --no-cache discord-bot

# Verificar logs de instalação
docker-compose logs discord-bot | grep "Successfully installed"
```

**Erro: "PermissionError: [Errno 13] Permission denied"**
```bash
# Adicionar usuário não-root ao Dockerfile
# Ou ajustar permissões dos volumes
chmod -R 755 ./data
```

**Imagem muito grande:**
```bash
# Verificar tamanho da imagem
docker images | grep discord-ponto-bot

# Usar imagem alpine (mais leve)
FROM python:3.11-alpine

# Ou implementar multi-stage build
```

**Build lento:**
```bash
# Usar BuildKit (build paralelo)
DOCKER_BUILDKIT=1 docker-compose build

# Ou configurar permanentemente
echo 'export DOCKER_BUILDKIT=1' >> ~/.bashrc
```

### Melhores Práticas

1. **Use versões específicas**
   ```dockerfile
   FROM python:3.11.7-slim  # ← Versão exata
   ```

2. **Minimize camadas**
   ```dockerfile
   # ❌ Ruim - 3 camadas
   RUN apt-get update
   RUN apt-get install -y git
   RUN apt-get clean
   
   # ✅ Bom - 1 camada
   RUN apt-get update && \
       apt-get install -y git && \
       apt-get clean && \
       rm -rf /var/lib/apt/lists/*
   ```

3. **Use .dockerignore**
   ```
   .env
   .git
   __pycache__
   *.pyc
   data/
   logs/
   tests/
   ```

4. **Labels para metadados**
   ```dockerfile
   LABEL maintainer="seu@email.com"
   LABEL version="1.0"
   LABEL description="Discord Bot para Registro de Ponto"
   ```

## Suporte

Para reportar problemas ou sugerir melhorias:

- **Desenvolvedor:** Gabriel
- **Discord.py Docs:** https://discordpy.readthedocs.io/
- **PostgreSQL Docs:** https://www.postgresql.org/docs/
- **Docker Docs:** https://docs.docker.com/
- **Psycopg Docs:** https://www.psycopg.org/psycopg3/docs/

### Recursos Adicionais

- **Discord Developer Portal:** https://discord.com/developers/applications
- **Docker Compose Docs:** https://docs.docker.com/compose/
- **Python Discord.py Examples:** https://github.com/Rapptz/discord.py/tree/master/examples
- **Pytest Documentation:** https://docs.pytest.org/

---

**Última atualização:** Fevereiro 2026