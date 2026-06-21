---
name: cloak-account-registration
description: Регистрация аккаунтов и общая работа через профили Cloak. Используй когда нужно создать аккаунт где-либо, пройти любую форму регистрации, или работать в анонимном браузерном профиле с humanize-вводом.
triggers:
  - register account
  - create account
  - signup
  - регистрация
  - создать аккаунт
  - Cloak profile registration
  - новый профиль
---

# Cloak Account Registration

Базовый runbook для работы через CloakBrowser-Manager: регистрации, входы, любые действия в браузерном профиле с анти-детектом. Сайт-специфичные скиллы могут добавлять конкретные селекторы и потоки страниц, но правила ниже работают всегда.

## Ключевые тулзы

| Тулза | Что делает |
|---|---|
| `cloak_create_profile` | Создать новый профиль (имя + опционально прокси, fingerprint preset) |
| `cloak_launch` | Запустить Chromium для профиля (открывает CDP-эндпоинт) |
| `cloak_set_active` | Сделать профиль активным — все `browser_*` тулзы пойдут в него |
| `cloak_stop` | Корректно остановить профиль |
| `cloak_detect_captcha` | Определить тип капчи на странице (reCAPTCHA v2/v3, hCaptcha, Cloudflare, etc.) |
| `cloak_solve_captcha` | Отправить капчу в CapSolver / 2Captcha по конфигу env |
| `browser_navigate` | Открыть URL (через Playwright pool с humanize-настройками) |
| `browser_click`, `browser_type`, `browser_fill` | Действия с humanize-математикой (Bezier-курсор, QWERTY-typo, Fitts's Law) |
| `browser_snapshot` | Дерево accessibility — основа для понимания что на странице |
| `browser_console` | Запустить JS в браузере — useful для readyState, DOM-проверок, fallback-ввода |
| `browser_screenshot`, `browser_vision` | Скриншот + LLM-vision когда snapshot слабый |

## Жёсткие правила

1. **Один аккаунт — один профиль.** Никогда не регистрируй второй аккаунт в уже использованном профиле, если пользователь явно не попросил продолжить именно его.
2. **Не редактируй скиллы во время живой регистрации.** Сначала закончи, заблокируй, или сообщи. Если нашёл новый pitfall — упомяни в отчёте, а правки в скиллы делай отдельной задачей.
3. **Прокси пользователя не проверяй на сторонних IP-чекерах.** Если юзер говорит что прокси рабочий — считаем рабочим, проверяем только загрузкой целевого сайта.
4. **Целевые страницы под прокси могут грузиться медленно.** Пустая страница — это ещё не fail. Жди URL, title, DOM-поля или капчу до ~75 секунд прежде чем говорить «нет навигации».
5. **После любой навигации, reload, submit, callback капчи** — все старые `eNN` accessibility-рефы протухли. Делай свежий `browser_snapshot` или используй стабильные селекторы.
6. **Не зацикливайся.** Один и тот же failed tool call — максимум 2 раза. Дальше: меняй тактику, переподключи профиль, или спроси юзера.
7. **Секреты не светим в чат.** Пароли и токены — только в файл аккаунта, в чате только статус.

## Стандартный поток

1. **Создать или поднять профиль.** Если профиль с этим именем уже есть — не пересоздаём, просто `cloak_launch` + `cloak_set_active`.
2. **Навигация на целевой сайт.** Реальная страница назначения = health-check. Не ходи на `httpbin` / Google для «проверки коннекта».
3. **Если страница тормозит** — подожди и инспектируй:
   - `browser_console`: `document.readyState`, `document.title`, текущий URL, количество полей.
   - `browser_snapshot` — только после того, как страница успела отрендериться.
4. **Заполнение полей** — выбирай наименее хрупкий метод:
   - Видимые стабильные контролы → `browser_click` / `browser_type` (через humanize)
   - JS-heavy страницы со стабильными ID → `browser_console` с `value` + диспатч событий `input`, `change`, `blur`
   - После submit/reload → обнови рефы перед `browser_click` снова
5. **Перед капчей** — `cloak_detect_captcha` сначала, потом `cloak_solve_captcha`.
6. **Сохрани результат** когда run дошёл до значимого состояния. Возможные статусы: `submitted`, `activated`, `pending_activation`, `blocked`, `failed`.

## Медленная загрузка целевого сайта

Когда сайт за прокси юзера:

1. Не переключайся на `httpbin`, Google или другой probe-сайт.
2. Сначала пробуй точный целевой URL.
3. Если navigation timeout, но у страницы есть title, URL, `readyState=interactive/complete` или поля — продолжай с текущей страницы.
4. Если всё ещё blank — повтори тот же URL ещё раз после короткой задержки.
5. Если retry тоже fail — переподключи профиль: `cloak_stop` если нужно, потом `cloak_launch` / `cloak_set_active`, потом снова URL.

## Browser Recovery

Эта таблица — твой первый ответ на ошибки, до escalation:

| Симптом | Действие |
|---|---|
| `BROWSER_CDP_URL not set` | Вызови `cloak_set_active(profile=...)`. |
| `no_active_cloak_profile` | Browser guard плагина: ты вызвал `browser_*` до Cloak. Вызови `cloak_set_active(profile=...)` либо пару `cloak_create_profile` + `cloak_launch` — затем повтори. Это нормальное поведение защиты от local-Chromium fallback. |
| `Profile is already running` | Не fatal — просто `set_active` и продолжай. |
| `Profile not running` | Запусти профиль снова, обнови `BROWSER_CDP_URL`. |
| `Target page, context or browser has been closed` | Drop профиль, переподключи, навигируй на текущий target URL заново. |
| `Unknown ref: eNN` | Старый accessibility ref. Делай новый `browser_snapshot`, используй новые рефы. |
| `DOM.getBoxModel: Could not compute box model` | Элемент скрыт/detached. Используй стабильный селектор или fallback через DOM events. |
| `net::ERR_TUNNEL_CONNECTION_FAILED` | Повтори URL через паузу. Если повторяется — переподключи профиль и сообщи точный URL. |
| Snapshot пустой, но DOM есть | Инспектируй через `browser_console`, заполняй через JS. |

## Капча

1. Сначала пробуй `cloak_solve_captcha` — он использует ключи из `/etc/cloak/manager.env` (CapSolver или 2Captcha, маршрутизация автоматическая).
2. Если тулза говорит «provider keys missing» — не делай долгие shell-loop'ы. Сообщи юзеру какой env отсутствует (`CAPSOLVER_API_KEY`, `TWOCAPTCHA_API_KEY`, `TWO_CAPTCHA_API_KEY`) или попроси решить вручную.
3. На `CAPCHA_NOT_READY` — pollи только в пределах разумного таймаута. Не сжигай весь ход агента.
4. Если капча возвращает `MANUAL_INTERVENTION_REQUIRED` — отправь юзеру компактный handoff:

```
Stuck on profile <name>.
Step: <где в регистрации>.
Reason: <captcha / passkey / page error>.
Открой Cloak Manager через туннель, возьми контроль над профилем <name>, реши челлендж, потом скажи «continue».
```

И стой пока юзер не ответит.

## Result Files

Где хранить результат регистрации:

- **Credentials и статус:** `~/.hermes/cloak/accounts/<account-id>.json` — единое место.
- **Формат записи:** минимум `profile`, `target_site`, `email_or_username`, `password` (если хранится), `status`, `created_at`, `submitted_at`/`activated_at` если применимо.
- **В чат пиши только статус** — никогда не пароли. Юзер при необходимости откроет файл сам.

Если для конкретного сайта есть site-target скилл с собственной канонической базой (например `~/.hermes/some-site-accounts.txt`) — следуй его правилам, не плоди дубликаты. Один источник правды на сайт.

## Связи с другими скиллами

- **`cloak-proxy-pool`** — атомарная выдача прокси из пула, если регистрация требует разные IP на каждый аккаунт.
- **`notletters-api`** — получить OTP-код с почты во время email-верификации.
- Site-target скиллы (если есть) — добавляют конкретные селекторы, URL, особенности конкретного сайта.
