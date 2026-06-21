# Hermes Cloak Patch

[![GitHub](https://img.shields.io/badge/GitHub-Iwakishirokoshu%2FHermes--CloakManager--Patch-blue?logo=github)](https://github.com/Iwakishirokoshu/Hermes-CloakManager-Patch)
[![Plugin version](https://img.shields.io/badge/hermes--plugin--cloak-0.1.1-green)](plugin/pyproject.toml)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**Аддон-патч для Hermes**, который превращает обычного агента в полноценного стелс-оператора: профильный браузер с анти-детектом, человекоподобный ввод, автоматическое решение капч и набор готовых скиллов для регистраций.

Ставится **поверх** уже установленного Hermes одной командой. Не пересобирает Hermes, не ломает существующие настройки, можно откатить за минуту.

## Установка одной строкой

```bash
curl -fsSL https://raw.githubusercontent.com/Iwakishirokoshu/Hermes-CloakManager-Patch/main/scripts/bootstrap.sh | sudo bash
```

Скрипт клонирует репо в `/opt/hermes-cloak-patch` и запускает `install.sh`. Все аргументы пробрасываются дальше, например для dry-run:

```bash
curl -fsSL https://raw.githubusercontent.com/Iwakishirokoshu/Hermes-CloakManager-Patch/main/scripts/bootstrap.sh | sudo bash -s -- --dry-run
```

---

## Что это даёт

После установки твой Hermes сможет:

- **Открывать сайты через изолированные браузерные профили** — каждый профиль со своим fingerprint'ом, прокси, cookie-jar'ом. Сайт не отличит от живого человека.
- **Кликать и печатать как человек** — Bezier-траектории мыши, Fitts's Law тайминги, QWERTY-typo с исправлением, минимальный jerk для скорости. Всё реализовано через pydoll-портированную математику.
- **Решать капчи автоматически** — reCAPTCHA v2/v3, hCaptcha, Cloudflare Turnstile, GeeTest. Маршрутизация между CapSolver и 2Captcha по конфигу.
- **Регистрировать аккаунты** — три готовых скилла: общий runbook регистрации, пул прокси с атомарной выдачей, и работа с почтовым сервисом NotLetters для OTP.
- **Управляться через Telegram-gateway** (если он у тебя стоит) — env подхватывается автоматически.

---

## Архитектура

```
                       SSH tunnel
   ┌─────────┐   ┌──────────────────┐   ┌────────────────────────────┐
   │ Laptop  │──▶│ -L 8080:8080     │──▶│ VPS:8080  Cloak Manager    │
   └─────────┘   └──────────────────┘   │           (Docker)         │
                                         └────────┬───────────────────┘
                                                  │
                          Hermes (твой существующий venv)
                             │
                             ├── plugins.enabled: [cloak]
                             ├── /opt/hermes-plugin-cloak/  (pip -e)
                             │      ├── humanize/  — pydoll math
                             │      ├── captcha/   — CapSolver + 2Captcha
                             │      └── tools_browser.py — Playwright pool
                             │
                             ├── browser_click / browser_type
                             │       └─▶ Playwright over CDP (humanize)
                             │
                             └── browser_snapshot / browser_vision
                                     └─▶ nginx :8081 (injects Bearer)
                                              └─▶ Manager :8080
                                                       └─▶ Chromium per profile
```

**Зачем nginx прокси на 8081?** Нативный `agent-browser` Hermes'а не умеет ставить `Authorization: Bearer` заголовок на WebSocket-upgrade. Манагер требует токен — без прокси snapshot/vision получают 403. Локальный nginx подставляет токен server-side и всё работает.

---

## Что внутри пакета

```
hermes-cloak-patch/
├── install.sh              ← главный установщик (idempotent)
├── uninstall.sh            ← аккуратный откат
├── verify.sh (в scripts/)  ← smoke-тест после установки
├── README.md               ← этот файл
├── MANIFEST.md             ← список компонентов + версии
│
├── plugin/                 ← hermes-plugin-cloak с patched manager_client
├── skills/
│   ├── cloak-account-registration/   ← общий runbook регистрации
│   ├── cloak-proxy-pool/             ← пул прокси с атомарной выдачей
│   └── notletters-api/               ← OTP-коды с почты NotLetters
│
├── config/
│   ├── manager.env.example           ← шаблон env'а
│   ├── config.yaml.snippet           ← plugins.enabled: [cloak]
│   └── gateway-drop-in.conf          ← подгрузка manager.env в gateway
│
├── docker/run-manager.sh             ← поднимает Cloak Manager в Docker
├── nginx/                            ← reverse-proxy конфиги
├── systemd/                          ← gateway template + backup timer
├── scripts/
│   ├── merge_plugin_enabled.py       ← аккуратный YAML-merge
│   ├── sanitize-for-release.sh       ← проверка перед раздачей
│   └── sync-from-vps.sh              ← maintainer-tool
└── client/                           ← Windows .bat для SSH-туннелей
```

---

## Установка

### Требования

- Ubuntu 22.04 / 24.04 (или Debian 12), root/sudo
- Hermes уже установлен любым способом (PyPI, curl-installer, pipx, uv) — установщик сам найдёт venv
- Желательно интернет без блокировок Docker Hub (для pull `cloakhq/cloakbrowser-manager:latest`)

### Способ A — одна строка через GitHub (рекомендуемый)

```bash
curl -fsSL https://raw.githubusercontent.com/Iwakishirokoshu/Hermes-CloakManager-Patch/main/scripts/bootstrap.sh | sudo bash
```

Bootstrap клонирует репо в `/opt/hermes-cloak-patch` и сразу выполняет `install.sh`. Чтобы посмотреть план без изменений:

```bash
curl -fsSL https://raw.githubusercontent.com/Iwakishirokoshu/Hermes-CloakManager-Patch/main/scripts/bootstrap.sh | sudo bash -s -- --dry-run
```

### Способ B — вручную через git clone

```bash
git clone https://github.com/Iwakishirokoshu/Hermes-CloakManager-Patch.git /opt/hermes-cloak-patch
cd /opt/hermes-cloak-patch
sudo bash install.sh --dry-run   # сначала посмотреть
sudo bash install.sh             # реальная установка
```

### Способ C — скопировать архив

```bash
scp -r hermes-cloak-patch/ root@YOUR_VPS:/tmp/
ssh root@YOUR_VPS "cd /tmp/hermes-cloak-patch && sudo bash install.sh"
```

### Логин в Cloak Manager UI

В конце установки скрипт **печатает финальный блок** с токеном и инструкцией:

```
========================================================
  HOW TO LOG IN TO CLOAK MANAGER
========================================================

  1) From your laptop, open an SSH tunnel:
       ssh -L 8080:127.0.0.1:8080 root@YOUR_VPS_IP

  2) Open in browser:
       http://localhost:8080

  3) Paste this token when the UI asks:
       <token-from-/etc/cloak/auth_token>  # printed by installer on first run

  Reprint anytime:  cat /etc/cloak/auth_token
                or: grep CLOAK_AUTH_TOKEN /etc/cloak/manager.env
                or: bash scripts/get-token.sh
```

Сохрани этот вывод. Если упустил — достань токен в любой момент:

```bash
# Самый короткий вариант:
sudo cat /etc/cloak/auth_token

# Удобный helper с подсказкой команд:
sudo bash scripts/get-token.sh
```

### Ключи капч

Дальше в `/etc/cloak/manager.env` нужно вписать ключи капч-провайдеров (хотя бы один):

```bash
sudo nano /etc/cloak/manager.env
```

```ini
CAPSOLVER_API_KEY=твой_ключ_capsolver
TWOCAPTCHA_API_KEY=твой_ключ_2captcha
NOTLETTERS_API_KEY=твой_ключ_notletters   # опционально, для OTP с почты
```

После правки:

```bash
sudo systemctl restart hermes-gateway   # если у тебя есть gateway
```

### Проверка что всё встало

```bash
sudo bash scripts/verify.sh
```

Должно быть всё `[ok]`. Скрипт проверяет:

- `/etc/cloak/manager.env` существует с токеном
- Manager отвечает на `:8080/api/status` с Bearer
- nginx прокси отвечает на `:8081/api/status` без auth
- Плагин лежит в `/opt/hermes-plugin-cloak/`
- `plugins.enabled: cloak` в `~/.hermes/config.yaml`
- Все три скилла на месте в `~/.hermes/skills/`
- Плагин импортируется из venv Hermes
- (опционально) `hermes-gateway.service` активен и загружает `manager.env`

---

## SSH-туннель для управления

Cloak Manager слушает только на `127.0.0.1:8080` — снаружи он закрыт. Для доступа к веб-интерфейсу с ноутбука:

### Linux / macOS

```bash
ssh -L 8080:127.0.0.1:8080 root@TWOJ_VPS
```

И в браузере открываешь `http://localhost:8080`. Логин — `CLOAK_AUTH_TOKEN` из `/etc/cloak/manager.env` (или из `/etc/cloak/auth_token`).

### Windows

В каталоге [client/examples/](client/examples/) лежат `.bat.example` шаблоны для самых частых задач:

| Файл | Что делает |
|---|---|
| `connect-cloak-manager.bat.example` | Туннель `localhost:8080 -> VPS:8080` + открывает Cloak Manager UI |
| `connect-cdp-proxy.bat.example` | Туннель `localhost:8081 -> VPS:8081` для отладки CDP |
| `connect-dashboard.bat.example` | Туннель `localhost:9119 -> VPS:9119` + Hermes Dashboard |
| `show-token.bat.example` | Печатает `CLOAK_AUTH_TOKEN` с VPS |
| `verify-remote.bat.example` | Запускает `verify.sh` на VPS |
| `ssh-shell.bat.example` | Просто SSH в VPS |

Как пользоваться — раз настроить, потом два клика:

```cmd
cd client\examples
copy connect-cloak-manager.bat.example connect-cloak-manager.bat
notepad connect-cloak-manager.bat
:: подмени:
::   set "VPS_HOST=YOUR_VPS_IP"
::   set "SSH_KEY=C:\Users\YOU\.ssh\id_ed25519"
:: сохрани, закрой, запусти двойным кликом
```

Подробнее: [client/examples/README.md](client/examples/README.md).

---

## Опции установки

| Флаг | Что делает |
|---|---|
| `--dry-run` | Выводит все шаги без выполнения. Безопасно — ничего не меняет. |
| `--regenerate-token` | Перегенерировать `CLOAK_AUTH_TOKEN` (по умолчанию сохраняет существующий). |
| `--force-skills` | Перезаписать существующие скиллы в `~/.hermes/skills/`. |
| `--with-backup` | Установить systemd timer для daily backup Docker volume `cloak-profiles` в 03:30 UTC. |
| `--skip-restart` | Не делать `systemctl restart hermes-gateway` после установки. |
| `--token-env=NAME` | Имя env-переменной, которую Docker manager ожидает как auth token. По умолчанию `AUTH_TOKEN`. |

Переменные окружения:

- `CLOAK_PATCH_ENV=/path/to/secrets.env` — заполнить `manager.env` из готового файла (удобно для скриптинга).
- `HERMES_BIN=/absolute/path/to/hermes` — явный override если auto-detect не нашёл Hermes.

---

## Telegram gateway

Этот патч **сам** Telegram-gateway не ставит. Он только кладёт systemd drop-in `/etc/systemd/system/hermes-gateway.service.d/10-cloak-env.conf`, чтобы gateway подхватывал `manager.env` если у тебя уже есть `hermes-gateway.service`.

Если gateway'а ещё нет — есть шаблон:

```bash
cp systemd/hermes-gateway.service.example /etc/systemd/system/hermes-gateway.service
# отредактируй: токен бота в ~/.hermes/.env
systemctl daemon-reload
systemctl enable --now hermes-gateway.service
```

---

## Hermes config — что меняется

Установщик аккуратно мержит в `~/.hermes/config.yaml` одну строку:

```yaml
plugins:
  enabled:
    - cloak
```

Все остальные секции (providers, agent, models, etc.) **не трогаются**. Если у тебя уже был список `plugins.enabled` — `cloak` дописывается в конец. Если запустить установщик повторно — изменений не будет (идемпотентно).

Использует `ruamel.yaml` если доступен (сохраняет комментарии в YAML), иначе fallback на `PyYAML` который Hermes и так использует.

---

## Откат

```bash
sudo bash uninstall.sh
```

Что делает:
- Стоп Docker контейнера `cloakbrowser-manager`
- Удаление nginx сайта `cloak-cdp-proxy`
- Удаление systemd drop-in для gateway
- Restart nginx и daemon-reload

Что **не** трогает (специально, чтобы не терять данные):
- `/opt/hermes-plugin-cloak/` — плагин на диске
- `/etc/cloak/manager.env` — твои ключи капч
- Docker volume `cloak-profiles` — все сохранённые профили, cookies, fingerprints
- `~/.hermes/skills/cloak-*` — скиллы

Если нужно убрать **всё** — удали эти пути руками после `uninstall.sh`.

---

## Тонкости и подводные камни

### Один источник правды для токена

`CLOAK_AUTH_TOKEN` должен быть **только** в `/etc/cloak/manager.env`. Не дублируй в `~/.hermes/.env` — это вызывает desync, плагин и контейнер видят разные значения, всё ломается с 401. Если есть — установщик предупредит warn'ом.

### Docker image и AUTH_TOKEN env

Установщик пытается автоопределить имя env-переменной токена через `docker inspect` существующего контейнера. Если контейнера нет — используется `AUTH_TOKEN` по умолчанию. Если у конкретной версии image другое имя — передай через `--token-env=ИМЯ`.

### Playwright Chromium download

При первой установке скачивается ~350 МБ Chromium для Playwright. Это занимает 3-5 минут на нормальном линке. Если ловишь timeout — запусти повторно, скачка возобновится.

### uv-managed Hermes venvs

Современные установщики Hermes часто используют `uv` вместо pip. Установщик это поддерживает: пробует `pip` сначала, потом `uv pip install --python $venv/bin/python` если pip отсутствует. `uv` ищется в `~/.hermes/bin/`, `~/.local/bin/`, `/usr/local/bin/`.

### Browser guard и сообщение `no_active_cloak_profile`

Hermes v0.17 при первом же вызове `browser_*` создаёт **локальный** Chromium, если не выставлен `BROWSER_CDP_URL`. Этот локальный браузер обходит весь стелс-стек — нет профиля, нет humanize, нет прокси. Поэтому плагин **monkey-patch'ит на уровне модуля** все 10 нативных browser-тулов (navigate полностью заменяется на Cloak Playwright; snapshot/click/type/press/scroll/console/back/get_images/vision обёрнуты в guard).

Если агент пытается вызвать `browser_*` до `cloak_set_active`, guard возвращает:

```json
{
  "success": false,
  "error": "no_active_cloak_profile",
  "hint": "Call cloak_set_active(profile='...') first, or cloak_create_profile + cloak_launch. Using browser_snapshot now would spin up a LOCAL Chromium and bypass the Cloak stealth profile.",
  "tool": "browser_snapshot",
  "guard": "hermes-plugin-cloak"
}
```

Это **нормальное** поведение — агент после этого вызовет `cloak_set_active` и всё дальше пойдёт через Cloak. Если же `CLOAK_MANAGER_URL` не настроен — guard вообще пропускает к нативу (значит ты сознательно работаешь без Cloak).

Почему module-patch, а не `register_tool(override=True)`? Потому что в Hermes v0.17 registry-override не вытесняет уже зарегистрированный native handler — есть проверки приоритета и порядка загрузки. Patching `tools.browser_tool.<name>` атрибута — единственный надёжный способ перехватить **любой** диспатч.

### Гарантии

End-to-end test пройден на чистой Ubuntu 24.04 VM с установленным Hermes v0.17.0:

- Свежая установка → exit 0, все 10 verify-чеков `[ok]`
- Idempotent re-install → ничего лишнего не пересоздаёт, токен/профили сохраняются
- `hermes plugins list` показывает `cloak | enabled | 0.1.1`
- nginx CDP прокси корректно инжектит Bearer (200 без auth header)
- Docker container `cloakbrowser-manager` поднимается healthy с первой попытки
- Browser guard: 9 нативных browser-тулов обёрнуты, `browser_navigate` полностью заменён → попытки фоллбэка на локальный Chromium блокируются с понятной ошибкой

Дополнительно: каждый shell-скрипт прошёл `bash -n` syntax check, YAML-merge покрыт unit-тестами на 4 сценариях, browser-guard покрыт на 5 сценариях в `plugin/tests/test_browser_guard.py`.

---

## Раздача другим людям (maintainer)

Перед тем как отдавать пакет:

```bash
# 1. Проверить что нет утечек личных токенов/IP/паролей
bash scripts/sanitize-for-release.sh

# 2. Собрать tarball
tar czf hermes-cloak-patch-v1.0.tar.gz hermes-cloak-patch/
```

`sanitize-for-release.sh` проверяет:
- Нет реальных IP адресов
- Нет sk-токенов и Bearer'ов
- Нет личных доменов
- В пакете нет `manager.env` (только `.example`)

---

## Поддерживаемые версии

| Компонент | Версия |
|---|---|
| Ubuntu | 22.04 LTS, 24.04 LTS |
| Debian | 12 |
| Python | 3.10+ (любая, на которой работает Hermes) |
| Docker | 20.10+ |
| nginx | nginx-light из apt подходит |
| Hermes | любая текущая (PyPI / curl / pipx / uv) |

---

## Лицензия

Проверь лицензию исходного `hermes-plugin-cloak` в `plugin/pyproject.toml`. Скиллы — внутреннее использование.
