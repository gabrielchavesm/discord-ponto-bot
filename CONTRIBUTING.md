# Guia de Continuous Integration (CI) e Docker

Este documento explica como está configurada a pipeline de Integração Contínua (CI) do projeto **Discord Ponto Bot**, como podemos executar os testes localmente, como funcionam os workflows do GitHub Actions e como usar o Docker Compose para correr o bot em produção.

---

## Estrutura do Projeto

Os ficheiros relevantes para a CI e testes são:

- `.github/workflows/cicd.yml` – Workflow principal do GitHub Actions
- `.github/dependabot.yml` – Configuração do Dependabot para atualizações automáticas
- `tests/` – Pasta com todos os testes unitários (pytest)
- `requirements-dev.txt` – Dependências para desenvolvimento e testes
- `docker-compose.yml` – Orquestração dos containers (bot + PostgreSQL)
- `CONTRIBUTING.md` – Este documento

---

## Executar Testes Localmente

Para garantir que o código funciona antes de fazer *push*, devemos correr os testes na tua máquina.

### Pré‑requisitos

- Python 3.11 ou superior
- `pip` (gestor de pacotes)
- Docker

### Passos

1. **Criar um ambiente virtual** (recomendado):

   ```bash
   python -m venv venv
   source venv/bin/activate      # Linux/macOS
   venv\Scripts\activate         # Windows
   ```

2. **Instalar as dependências de desenvolvimento**:

   ```bash
   pip install -r requirements-dev.txt
   ```

   O ficheiro `requirements-dev.txt` inclui:
   - `pytest` e plugins (`pytest-asyncio`, `pytest-cov`, `pytest-mock`)
   - Ferramentas de qualidade de código: `black`, `flake8`, `mypy`
   - A própria biblioteca `discord.py` (já listada em `requirements.txt`, que é incluída via `-r requirements.txt`)

3. **Executar os testes**:

   ```bash
   pytest tests/ -v --cov=bot --cov=database --cov-report=term-missing
   ```

   - `-v` : modo verboso
   - `--cov=bot --cov=database` : mede a cobertura dos módulos `bot.py` e `database.py`
   - `--cov-report=term-missing` : mostra no terminal as linhas não cobertas

   Os testes estão organizados em:
   - `tests/test_bot.py` – testa toda a lógica do bot (comandos, views, permissões, etc.)
   - `tests/test_database.py` – testa as funções da base de dados (registos, locks, relatórios, etc.)

4. **Verificar a qualidade do código** (opcional):

   ```bash
   black .           # formata o código automaticamente
   flake8            # verifica erros de estilo
   mypy .            # verifica tipos (type hints)
   ```

---

## GitHub Actions – Workflow de CI

O ficheiro `.github/workflows/cicd.yml` define uma pipeline automática que é executada sempre que há um *push* ou *pull request* em qualquer ramo.

### O que faz o workflow?

1. **Job `test`** (Ubuntu latest)
   - Configura Python 3.11
   - Instala as dependências (`requirements-dev.txt`)
   - Corre todos os testes com `pytest` e gera relatório de cobertura (formato XML)
   - Envia o relatório para o [Codecov](https://codecov.io/) (opcional)

2. **Job `build-and-push`** (depende do job `test`)
   - Constrói uma imagem Docker multi‑arquitetura (linux/amd64 e linux/arm64)
   - Publica a imagem no **GitHub Container Registry (GHCR)** com duas tags:
     - `ghcr.io/gabrielchavesm/discord-ponto-bot:<sha-do-commit>`
     - `ghcr.io/gabrielchavesm/discord-ponto-bot:latest`
   - Para tal, usa as permissões `contents: read` e `packages: write`

3. **Job `security-scan`** (depende do job `build-and-push`)
   - Faz login no GHCR e puxa a imagem acabada de construir
   - Executa o **Trivy** (scanner de vulnerabilidades) sobre a imagem
   - Envia os resultados para o separador *Security* do repositório (formato SARIF)

### Permissões necessárias

O workflow utiliza o `GITHUB_TOKEN` (gerado automaticamente pelo GitHub) para autenticar no GHCR. As permissões são definidas no próprio ficheiro:

```yaml
permissions:
  contents: read
  packages: write
  security-events: write   # para o job de segurança
```

---

## Dependabot – Atualizações Automáticas

O ficheiro `.github/dependabot.yml` configura o Dependabot para manter as **GitHub Actions** atualizadas.

- **Ecosystem**: `github-actions`
- **Schedule**: semanalmente
- **Commit message**: `chore(ci): automatically update pinned GitHub Actions versions`
- **Ignore**: a versão `0.23.0` do `aquasecurity/trivy-action` é ignorada (podemos ajustar conforme necessário)

Desta forma, quando houver novas versões das actions usadas no workflow, o Dependabot criará automaticamente um Pull Request com a atualização.

---

## Docker Compose – Executar o Bot em Produção

O ficheiro `docker-compose.yml` orquestra dois serviços:

- **`db`**: container PostgreSQL 18.1
- **`discord-bot`**: container com o bot, usando a imagem publicada no GHCR

### Configurações do ficheiro

- **Imagem pré‑construída**: É usado `image: ghcr.io/gabrielchavesm/discord-ponto-bot:latest`. Isto acelera o arranque porque não é necessário compilar a imagem localmente.
- **Healthcheck na base de dados**: o serviço `db` tem uma verificação de saúde (`pg_isready`) para garantir que o PostgreSQL está pronto antes de o bot tentar ligar-se.
- **Dependência condicional**:

  ```yaml
  depends_on:
    db:
      condition: service_healthy
  ```

  Isto faz com que o bot só inicie depois de a base de dados estar realmente operacional.

### Como correr localmente com Docker Compose

1. **Preparar o ficheiro `.env`** com as variáveis necessárias. Exemplo mínimo:

   ```
   DISCORD_TOKEN=o_teu_token
   GUILD_ID=o_id_do_servidor
   POSTGRES_DB=ponto
   POSTGRES_USER=postgres
   POSTGRES_PASSWORD=uma_password_segura
   ```

2. **Iniciar os containers**:

   ```bash
   docker-compose up -d
   ```

3. **Verificar os logs**:

   ```bash
   docker-compose logs -f discord-bot
   ```

4. **Parar os containers**:

   ```bash
   docker-compose down
   ```

---

## Autenticação no GitHub Container Registry (GHCR)

Para fazer *pull* ou *push* de imagens para o GHCR, precisamos de um token de acesso pessoal.

### Como gerar o token

1. Acede a [GitHub Settings > Tokens](https://github.com/settings/tokens)
2. Clica em **Generate new token (classic)**
3. Dá um nome (ex: “Docker GHCR Access”)
4. Escolhe uma data de expiração (recomenda‑se 30 ou 90 dias)
5. Seleciona os **scopes** necessários:
   - `read:packages` – para puxar imagens
   - `write:packages` – para enviar imagens
   - `delete:packages` – opcional, se precisarmos de apagar
   - `repo` – necessário apenas se o repositório for **privado**
6. Gere o token e **o copie imediatamente** (não voltará a ser mostrado).

### Fazer login com o token

```bash
docker login ghcr.io
Username: o_seu_nome_de_utilizador_github
Password: o_token_que_foi_criado
```

Após o login bem‑sucedido, podes usar `docker-compose up -d` normalmente – a imagem será puxada do GHCR.

## Deploy de Updates para Produção

O processo de deploy (atualização do bot) é totalmente automatizado via **GitHub Actions**. Quando uma nova **tag** é criada no repositório (ex.: `v1.2.3`), a pipeline CI/CD executa os seguintes passos:

1. **Testes unitários** – com `pytest` e relatório de cobertura.
2. **Build da imagem Docker** multi‑arquitetura (linux/amd64 e linux/arm64).
3. **Push da imagem** para o **GitHub Container Registry (GHCR)** com as tags:
   - `ghcr.io/gabrielchavesm/discord-ponto-bot:<sha-do-commit>`
   - `ghcr.io/gabrielchavesm/discord-ponto-bot:latest`
4. **Job `deploy`** – conecta‑se via SSH à VPS (usando as secrets `VPS_HOST`, `VPS_USER` e `VPS_SSH_KEY`) e executa o script `start.sh`.
---
### O script start.sh
O script `start.sh` (presente no diretório do projeto na VPS) é responsável por:

- Criar um **snapshot da imagem atual** como alvo de rollback (`:rollback`).

- Garantir que a base de dados está operacional (`docker compose up -d db`).

- Puxar a nova imagem (`docker compose pull discord-bot`).

- Recriar o container do bot com a imagem mais recente (`docker compose up -d --force-recreate --no-deps discord-bot`).

- Executar um **health check** (máx. 60s) à procura da mensagem `"INFO:database:Database schema ready"` nos logs.

- Se o health check falhar, faz **rollback automático**: restaura a imagem anterior (`:rollback`) e recria o container.

O script também configura automaticamente o **backup semanal** (via cron) – detalhado na secção seguinte.

```bash
#!/bin/bash
set -e

IMAGE="ghcr.io/gabrielchavesm/discord-ponto-bot"
SERVICE="discord-bot"
CONTAINER="discord-ponto-bot"

# ... (configuração de backup e cron) ...

# 1. Snapshot da imagem atual como rollback target
docker tag $IMAGE:latest $IMAGE:rollback 2>/dev/null || echo "No existing image to snapshot"

# 2. Assegurar que a base de dados está up
docker compose up -d db

# 3. Puxar nova imagem e recriar o bot
docker compose pull $SERVICE
docker compose up -d --force-recreate --no-deps $SERVICE

# 4. Health check
TIMEOUT=60
INTERVAL=5
SUCCESS=0
for i in $(seq 1 $((TIMEOUT / INTERVAL))); do
    if docker logs $CONTAINER 2>&1 | grep -q "INFO:database:Database schema ready"; then
        SUCCESS=1
        break
    fi
    sleep $INTERVAL
done

# 5. Rollback se falhar
if [ $SUCCESS -eq 0 ]; then
    echo "Health check failed! Initiating rollback..."
    if docker image inspect $IMAGE:rollback &>/dev/null; then
        docker tag $IMAGE:rollback $IMAGE:latest
        docker compose up -d --force-recreate --no-deps $SERVICE
        echo "Rollback complete."
    else
        echo "No rollback snapshot available."
    fi
    exit 1
fi

echo "Deploy complete!"
```
---
### Backup e Restauração
Para garantir a integridade dos dados, o sistema inclui scripts automáticos de backup e restauração.


O script `backup.sh` é executado semanalmente (aos domingos às 2h da manhã) via cron, configurado pelo `start.sh`. As suas funções:
- Cria um diretório `~/discord-ponto-bot/backups` se não existir.
- Gera um ficheiro de backup com timestamp: `backup_AAAAMMDD_HHMMSS.sql`.
- Executa `pg_dump` dentro do container PostgreSQL para criar um dump SQL da base de dados `ponto`.
- Comprime o ficheiro com `gzip`.
- Remove backups com mais de 30 dias.

```bash
#!/bin/bash
set -e

BACKUP_DIR=~/discord-ponto-bot/backups
mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/backup_$TIMESTAMP.sql"

echo "[$(date)] Creating backup: $BACKUP_FILE"
docker exec -t discord-ponto-db pg_dump -U postgres ponto > "$BACKUP_FILE"

gzip "$BACKUP_FILE"
find "$BACKUP_DIR" -name "backup_*.sql.gz" -mtime +30 -delete

echo "[$(date)] Backup complete."
```
---
### Restauração – restore_latest.sh
O script `restore_latest.sh` permite restaurar a base de dados a partir do backup mais recente. Passos:
- Localiza o ficheiro de backup mais recente (com ou sem compressão `.gz`).
- Para o container do bot para evitar escrita durante a restauração.
- Elimina e recria a base de dados `ponto`.
- Restaura o dump (descomprimindo temporariamente se necessário).
- Inicia novamente o bot.
```bash
#!/bin/bash
set -e

BACKUP_DIR=~/discord-ponto-bot/backups

# Encontrar o backup mais recente
LATEST_BACKUP=$(ls -t "$BACKUP_DIR"/backup_*.sql.gz 2>/dev/null | head -n1)
if [ -z "$LATEST_BACKUP" ]; then
    LATEST_BACKUP=$(ls -t "$BACKUP_DIR"/backup_*.sql 2>/dev/null | head -n1)
fi

if [ -z "$LATEST_BACKUP" ]; then
    echo "ERROR: No backup found in $BACKUP_DIR"
    exit 1
fi

echo "Latest backup: $LATEST_BACKUP"

# Descomprime se necessário
if [[ "$LATEST_BACKUP" == *.gz ]]; then
    gunzip -c "$LATEST_BACKUP" > /tmp/restore_temp.sql
    RESTORE_FILE=/tmp/restore_temp.sql
else
    RESTORE_FILE="$LATEST_BACKUP"
fi

cd ~/discord-ponto-bot

echo "Stopping bot (prevents writes during restore)..."
docker compose stop discord-bot

echo "Deleting and recreating the database..."
docker exec -i discord-ponto-db psql -U postgres -c "DROP DATABASE IF EXISTS ponto;"
docker exec -i discord-ponto-db psql -U postgres -c "CREATE DATABASE ponto;"

echo "Restoring backup..."
cat "$RESTORE_FILE" | docker exec -i discord-ponto-db psql -U postgres -d ponto

# Remove ficheiro temporário
if [[ "$LATEST_BACKUP" == *.gz ]]; then
    rm /tmp/restore_temp.sql
fi

echo "Starting the bot..."
docker compose start discord-bot

echo "Restoration successfully completed!"
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