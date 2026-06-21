---
name: notletters-api
description: Чтение писем и OTP через API NotLetters (api.notletters.com) для КОНКРЕТНОГО ящика email:password, который дал пользователь. НЕ логиниться в веб-админку notletters.com. Используй для «получи код с почты» / «жду письмо от OpenAI» / «проверь inbox ящика X».
triggers:
  - notletters
  - notletters-api
  - получи код с почты
  - проверь письма
  - жду письмо
  - OTP с почты
  - верификация по почте
  - код из письма
---

# NotLetters API skill

CLI-обёртка над **`https://api.notletters.com`**. Читает **конкретный почтовый ящик** (`user@domain.com:password`), который пользователь указал для регистрации.

## Критически важно — два разных «аккаунта»

| Что | Для чего | Где лежит | Когда использовать |
|---|---|---|---|
| **API-ключ** (`NOTLETTERS_API_KEY`) | Авторизация запросов к API от имени владельца NotLetters (админ) | `/etc/cloak/manager.env` или `/etc/notletters.env` | Только в HTTP-заголовке CLI — **никогда не логин в UI** |
| **Ящик** `email:password` | Inbox конкретной почты на твоём домене | **Даёт пользователь** в задаче («регай на `foo@example.com`, пароль `...`») | Команды `letters`, `codes`, `wait`, `watch` |

**`balance` показывает username админа (например `my_admin`) — это НЕ ящик для OTP.** Не путай с mailbox.

### Запрещено

- **НЕ открывай** `https://notletters.com` в браузере — это **админ-панель** для создания/управления почтами, не inbox.
- **НЕ используй** IMAP (`imap.notletters.com`) и **НЕ логинься** в веб, если задача — «получить код с почты X». Только API CLI ниже.
- **НЕ подставляй** admin username / API key как пароль ящика.
- **НЕ вызывай** `python3 notletters.py` — в системном Python нет `httpx`. Только через Hermes venv (см. ниже).

### Разрешено

- Только **`notletters.py`** через Hermes venv для чтения **того `email:password`, который указал пользователь**.

## Как запускать CLI (обязательный префикс)

```bash
NL=/root/.hermes/skills/notletters-api/notletters.py
PY=/usr/local/lib/hermes-agent/venv/bin/python

# smoke — ключ и API живы (покажет admin username, это нормально)
$PY $NL balance --json

# читать КОНКРЕТНЫЙ ящик пользователя (подставь email:pass из задачи!)
$PY $NL letters user@example.com:mailbox_password --json
$PY $NL wait user@example.com:mailbox_password \
    --sender openai --digits 6 --timeout 180 --since-now
```

Если `which hermes` находит venv иначе — используй `$(dirname $(which hermes))/../venv/bin/python` или тот путь, что подскажет ошибка скрипта.

## Три способа доступа NotLetters (используем только первый)

| Способ | Адрес | Для агента |
|---|---|---|
| **API** | `api.notletters.com` | **Да** — через `notletters.py` |
| IMAP | `imap.notletters.com` | **Нет** — не нужен для OTP |
| Web | `notletters.com` | **Нет** — только админка, не inbox |

## Конфигурация API-ключа (на сервере)

Ключ читается в порядке:

1. `$NOTLETTERS_API_KEY` / `$NL_KEY` в env
2. `/etc/cloak/manager.env` (часто уже есть после install патча)
3. `/etc/notletters.env`
4. `~/nl_key.txt`

Писать ключ через `write_file` в `/etc/*` **запрещено** Hermes — если ключа нет, попроси пользователя добавить в `manager.env` и перезапустить gateway.

## Команды

Все команды — только с **`$PY $NL`** и **`email:password` ящика из задачи пользователя**.

```bash
# Admin info (username/balance) — НЕ inbox
$PY $NL balance
$PY $NL balance --json

# Письма конкретного ящика (последние ~50)
$PY $NL letters user@example.com:mailbox_password
$PY $NL letters user@example.com:mailbox_password --search "OpenAI"
$PY $NL letters user@example.com:mailbox_password --json

# Только письма с числовыми кодами
$PY $NL codes user@example.com:mailbox_password

# Дождаться OTP после submit на сайте регистрации
$PY $NL wait user@example.com:mailbox_password \
    --sender openai \
    --digits 6 \
    --timeout 180 \
    --since-now
# OK: {"code": "123456", "letter_id": "...", "subject": "..."}
# Fail: {"code": null, "error": "timeout", "elapsed": 178}

# Live-мониторинг (редко нужен)
$PY $NL watch user@example.com:mailbox_password --interval 5
```

## Типовой сценарий: регистрация + email OTP

Пользователь дал: `newuser@mydomain.com` + пароль ящика + целевой сайт.

```
1. cloak_set_active / browser_navigate → форма регистрации
2. browser_type(email_field, "newuser@mydomain.com")
3. browser_click(submit)
4. $PY $NL wait newuser@mydomain.com:MAILBOX_PASS \
       --sender openai --digits 6 --timeout 180 --since-now
5. → {"code": "123456", ...}
6. browser_snapshot  → свежие refs
7. browser_type(otp_field, "123456")
8. browser_click(verify)
```

Если `wait` вернул timeout:

1. Проверь, что **email:password — именно ящик**, а не admin.
2. Проверь `--sender` (подстрока в From: `openai`, `microsoft`, `noreply@...`).
3. `$PY $NL letters user@domain:pass --json` — есть ли письмо уже?
4. Если письмо есть, но код не матчится — ослабь `--digits 0` или возьми код из `letters` вручную.

## Эндпоинты API

| Что | URL | В скрипте |
|---|---|---|
| Профиль админа / баланс | `GET /v1/me` | `balance` |
| Письма ящика | `POST /v1/letters` body `{email, password}` | `letters`, `codes`, `wait`, `watch` |
| Смена пароля ящика | `POST /v1/letters/password` | `change-password --payload '{...}'` |
| Покупка почт | `POST /v1/emails/buy` | `buy --payload '{...}'` |

## Безопасность

- API-ключ и пароли ящиков **не печатай в чат**.
- В чат — только `code`, `subject`, статус (`got code` / `timeout`).
- Rate limit ~10 req/s; скрипт делает backoff на 429.

## Связи

- **`cloak-account-registration`** — основной потребитель `wait` после submit формы.
- Покупка/создание новых ящиков на домене — **вне scope этого скилла** (админка NotLetters / `buy` stub). Если пользователь не дал готовый `email:password` — **спроси**, не лезь в notletters.com сам.
