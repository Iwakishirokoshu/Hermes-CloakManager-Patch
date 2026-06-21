# Windows клиент-скрипты (примеры)

Эти `.bat.example` файлы — **шаблоны** для быстрого подключения к VPS, где
установлен `hermes-cloak-patch`. Они открывают SSH-туннели на нужные локальные
порты и (опционально) запускают браузер.

## Как пользоваться

1. Скопируй любой `*.bat.example` без суффикса `.example`:

   ```cmd
   copy connect-cloak-manager.bat.example connect-cloak-manager.bat
   ```

2. Открой получившийся `.bat` в любом редакторе и подмени **две** переменные:

   ```bat
   set "VPS_HOST=YOUR_VPS_IP"
   set "SSH_KEY=C:\Users\YOU\.ssh\id_ed25519"
   ```

3. Запусти двойным кликом или из терминала.

## Что зачем

| Файл | Что делает |
|---|---|
| `connect-cloak-manager.bat.example` | SSH-туннель `localhost:8080 -> VPS:8080`, открывает Cloak Manager UI |
| `connect-cdp-proxy.bat.example` | SSH-туннель `localhost:8081 -> VPS:8081`, прямой доступ к CDP-прокси (отладка) |
| `connect-dashboard.bat.example` | SSH-туннель `localhost:9119 -> VPS:9119`, Hermes Dashboard |
| `show-token.bat.example` | Печатает текущий `CLOAK_AUTH_TOKEN` с VPS |
| `verify-remote.bat.example` | Запускает `/opt/hermes-cloak-patch/scripts/verify.sh` на VPS |
| `ssh-shell.bat.example` | Просто SSH в VPS |

## Получить токен

Токен Cloak Manager хранится на VPS в `/etc/cloak/auth_token` и в
`/etc/cloak/manager.env`. Запусти `show-token.bat`, либо вручную:

```cmd
ssh -i %SSH_KEY% root@%VPS_HOST% "cat /etc/cloak/auth_token"
```

Этот же токен выводится в самом конце `install.sh` при установке.
