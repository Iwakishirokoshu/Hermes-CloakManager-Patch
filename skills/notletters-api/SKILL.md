---
name: notletters-api
description: Работа с ящиками сервиса NotLetters (api.notletters.com) — баланс, чтение писем, ожидание OTP/верификационных кодов, мониторинг новых писем в реальном времени. Поддерживает кастомные домены пользователя. Используй для любых задач "получи код с почты" / "проверь письма" / "жду письмо от X" / "купи почту".
triggers:
  - notletters
  - notletters-api
  - почта notletters
  - получи код с почты
  - проверь письма
  - жду письмо
  - OTP с почты
  - верификация по почте
  - example.com
---

# NotLetters API skill

Тонкая обёртка над `https://api.notletters.com` для работы с ящиками сервиса (включая кастомные домены типа `example.com`). Все скрипты лежат рядом со SKILL.md, API-ключ — в `/etc/cloak/manager.env` или `/etc/notletters.env` (chmod 600), не в коде.

## Конфигурация (на сервере)

```
/etc/notletters.env  (chmod 600, owner root)
  NOTLETTERS_API_KEY=<ключ из ЛК notletters>
```

CLI-хелпер читает в таком порядке: `$NOTLETTERS_API_KEY` → `$NL_KEY` → `/etc/notletters.env` → `~/nl_key.txt`. Если ничего нет — error.

## Команды CLI

`/root/.hermes/skills/notletters-api/notletters.py` — единственный исполняемый файл. Все команды поддерживают `--json` для машинного вывода.

```bash
# Баланс / лимит / username аккаунта NotLetters
python3 notletters.py balance
python3 notletters.py balance --json

# Все письма ящика (последние 50, по убыванию даты)
python3 notletters.py letters user@example.com:password
python3 notletters.py letters user@example.com:password --search "OpenAI"
python3 notletters.py letters user@example.com:password --json

# Только письма с найденными числовыми кодами
python3 notletters.py codes user@example.com:password [user2@...:pass2 ...]

# Дождаться письма с OTP-кодом (для регистраций)
python3 notletters.py wait user@example.com:password \
    --sender openai            \  # подстрока в адресе/имени отправителя
    --digits 6                 \  # длина кода (4-8)
    --timeout 180              \  # секунд
    --since-now                    # игнорировать письма старше старта команды
# Возвращает {"code": "123456", "letter_id": "...", "subject": "..."} или exit 1

# Live-мониторинг новых писем (Ctrl+C — стоп)
python3 notletters.py watch user@example.com:password [more...] --interval 5
```

## Эндпоинты, которые скилл закрывает

| Что | URL | Метод | В скрипте |
|---|---|---|---|
| Баланс / профиль | `/v1/me` | GET | `balance` |
| Получение писем | `/v1/letters` | POST | `letters`, `codes`, `wait`, `watch` |
| Смена пароля ящика | `/v1/letters/password` (схема придёт от юзера) | POST | `change_password` (stub — agent передаёт payload) |
| Покупка почт | `/v1/emails/buy` (схема придёт от юзера) | POST | `buy` (stub) |

Для stub-эндпоинтов скрипт принимает payload как JSON-строку через `--payload '{...}'` — агент дописывает payload когда юзер пришлёт точную схему.

## Когда юзается агентом

### Сценарий 1: Регистрация акка с email-верификацией

```
1. (взять ящик) — у юзера есть готовые "email:pass" для своего домена
2. browser_navigate(target_signup_url)
3. ... fill email field ...
4. browser_click(submit)
5. python3 notletters.py wait <email>:<pass> --sender <service> --digits 6 --timeout 180
6. → получили JSON {"code": "123456", ...}
7. browser_type(otp_input_selector, "123456")
8. browser_click(verify_button)
```

### Сценарий 2: Live-мониторинг при подозрительной задержке

```
- Запустить watch в фоне через nohup/tmux: пишет в файл
- Когда новое письмо приходит — доставать через jq/grep
```

### Сценарий 3: Юзер спрашивает «есть письма?»

```
python3 notletters.py letters <email>:<pass> --json
→ показать summary (5 последних: from / subject / preview)
```

## Поиск кодов в письме

Скрипт находит OTP по нескольким стратегиям (в порядке приоритета):
1. **Регулярка `\b\d{N}\b`** — точная длина из `--digits` (по умолчанию 6).
2. **Любые `\b\d{4,8}\b`** в `--digits 0` режиме.
3. **Контекстный поиск** — рядом со словами `code`, `verification`, `confirm`, `OTP`, `код`, `подтвержд` (повышает приоритет совпадений).

При нескольких кандидатах возвращается первый по приоритету; в `--json` выводится массив `candidates`.

## Безопасность

- API-ключ **никогда** не печатается даже в `--debug`. Маскируется как `Fa...Bg`.
- Пароли ящиков идут только в payload POST'а, не логируются.
- Все запросы идут через `httpx` с явным таймаутом 30 сек.
- Rate limit — 10 req/s на аккаунт; скрипт сам делает экспоненциальный backoff при 429.

## Связи

- **`cloak-account-registration`** — основной потребитель `wait` для OTP при email-верификации.
- **Любой site-target скилл** регистрации — через `wait` с правильным `--sender`.
