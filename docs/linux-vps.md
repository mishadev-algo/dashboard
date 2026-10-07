# Запуск центрального сервера на Ubuntu 26.04

Этот сценарий рассчитан на один отдельный Linux VPS: PostgreSQL, Python API/страницы/уведомления и Caddy. MT5 и сборщики остаются на Windows VPS. Команды ниже **не переключают** текущий `MT5Dashboard`; это отдельный шаг после проверки нового сервера.

## Что подготовлено здесь

- `deploy/linux/mt5-dashboard.service` запускает центральный API и Telegram worker через systemd. Python слушает только `127.0.0.1:8765`.
- `deploy/linux/central.env.example` задаёт имена переменных. Заполненный файл с секретами хранится только на Linux VPS.
- `docs/Caddyfile.example` принимает HTTPS, пропускает два POST маршрута с bearer токенами и защищает страницы Basic Auth.
- `scripts/package_central.py` собирает ZIP только из исходников и шаблонов, включая ещё не закоммиченные `.py` файлы. Локальные базы, настройки, токены, логи и резервные копии в него не входят.
- `server.export_postgres` переносит таблицы, включая импортированные бэктесты. Экспорт запускается **из проверенной резервной копии** SQLite.

Потребуются публичный DNS адрес (например, `dashboard.example.com`) **или публичный IP с сертификатом**, SSH доступ и токен для каждого Windows хоста. Для запуска без домена см. [настройку HTTPS по IP](linux-vps-ip.md). Фактический VPS имеет Ubuntu 26.04.1; команды установки пакетов для другого образа могут отличаться.

## 1. Собрать файлы на текущем Windows VPS

Из `C:\dashboard`:

```powershell
python -m unittest discover -s tests -q
python -m scripts.package_central dist/central-vps.zip
Get-FileHash dist/central-vps.zip -Algorithm SHA256
```

Скрипт не перезаписывает старый ZIP. Для новой сборки укажите новое имя. Передача ZIP и экспорта — через ваш защищённый канал, например `scp`; экспорт содержит реальные торговые данные и логи.

Для репетиции переноса используйте существующие команды из [плана](central-vps.md#prepare-a-postgresql-import-from-a-sqlite-backup): `server.backup`, `server.restore_check`, затем `server.export_postgres`. Перед окончательным переключением создайте **новую** копию и экспорт: данные после старой копии в неё не попадут. Не копируйте рабочую `central.db` во время записи.

## 2. Установить Linux компоненты

Откройте на Ubuntu SSH с пользователем, имеющим `sudo`:

```sh
sudo apt update
sudo apt install -y python3 python3-venv postgresql postgresql-client unzip curl
sudo useradd --system --home-dir /opt/mt5-dashboard --shell /usr/sbin/nologin dashboard
sudo install -d -o dashboard -g dashboard -m 750 /opt/mt5-dashboard /etc/mt5-dashboard
sudo -u postgres createuser dashboard
sudo -u postgres createdb -O dashboard dashboard
```

Если роль или база уже созданы, проверьте их назначение, не создавайте второй экземпляр. Локальная строка `postgresql:///dashboard` использует Unix socket: системный пользователь `dashboard` входит как одноимённая роль PostgreSQL без пароля. Это работает при `peer` аутентификации локальных соединений; проверьте `pg_hba.conf`, если образ VPS менял эту настройку. [PostgreSQL: peer authentication](https://www.postgresql.org/docs/current/auth-peer.html).

Установите Caddy по [официальной инструкции для Debian/Ubuntu](https://caddyserver.com/docs/install). Пакет также устанавливает службу `caddy`.

## 3. Развернуть исходники и данные

Передайте `central-vps.zip` на VPS. После передачи сверьте SHA-256 с шагом 1 и распакуйте:

```sh
sudo -u dashboard unzip /tmp/central-vps.zip -d /opt/mt5-dashboard
sudo -u dashboard python3 -m venv /opt/mt5-dashboard/.venv
sudo -u dashboard /opt/mt5-dashboard/.venv/bin/python -m pip install -r /opt/mt5-dashboard/requirements-central.txt
```

Если это первый запуск **без старой истории**, создайте пустую схему:

```sh
sudo -u dashboard psql -X -v ON_ERROR_STOP=1 -d dashboard -f /opt/mt5-dashboard/docs/postgres-schema.sql
```

Если переносите текущую историю, вместо этого передайте на VPS каталог, созданный `server.export_postgres`, и выполняйте команды из **его** каталога. `schema.sql` и `load.psql` должны относиться к одному экспорту:

```sh
sudo chown -R dashboard:dashboard /opt/mt5-dashboard/import
sudo -u dashboard psql -X -v ON_ERROR_STOP=1 -d dashboard -f /opt/mt5-dashboard/import/schema.sql
sudo -u dashboard sh -c 'cd /opt/mt5-dashboard/import && psql -X -v ON_ERROR_STOP=1 -d dashboard -f load.psql'
```

Скопируйте каталог экспорта в `/opt/mt5-dashboard/import` перед этими командами. База должна быть **пустой**: не запускайте импорт поверх уже работающей базы. `load.psql` сверяет число строк каждой таблицы с манифестом. Сверьте также `manifest.json` и сохраните исходную проверенную SQLite копию для отката.

## 4. Секреты, служба и HTTPS

```sh
sudo install -o dashboard -g dashboard -m 600 /opt/mt5-dashboard/deploy/linux/central.env.example /etc/mt5-dashboard/central.env
sudo nano /etc/mt5-dashboard/central.env
sudo install -o root -g root -m 644 /opt/mt5-dashboard/deploy/linux/mt5-dashboard.service /etc/systemd/system/mt5-dashboard.service
sudo systemctl daemon-reload
```

Замените все `REPLACE_` значения. `DASHBOARD_HOST_TOKENS` — JSON соответствий точного `host_id` из локальной базы каждого сборщика и уникального токена. Не переносите `.dashboard-start.local.json`: его секреты зашифрованы для Windows пользователя. Токены Windows хостов должны совпасть с центральной картой; PostgreSQL DSN нужен только здесь. Файл `/etc/mt5-dashboard/central.env` доступен только служебному пользователю.

Создайте DNS `A` запись на IP VPS; откройте входящие 80/443 и SSH. Порт 8765 и PostgreSQL наружу не открывайте. Скопируйте `docs/Caddyfile.example` в `/etc/caddy/Caddyfile`, замените домен и хеш пароля. Получите хеш интерактивно командой `caddy hash-password`, не записывая пароль в историю shell. Затем:

```sh
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl enable --now mt5-dashboard
sudo systemctl reload caddy
curl -fsS http://127.0.0.1:8765/health
sudo systemctl status mt5-dashboard --no-pager
sudo journalctl -u mt5-dashboard -n 80 --no-pager
```

Снаружи проверьте `https://ВАШ-ДОМЕН/health`: ответ должен быть `{"status":"ok"}`. `GET /` без Basic Auth должен вернуть 401, с ним — страницу. `POST /v1/ingest` без bearer токена должен вернуть 401. Для живой проверки токена и записи отправьте обычный heartbeat из Windows сборщика, затем проверьте хост на странице и `pending_count` в БД. Ошибочный тестовый запрос с действующим токеном здесь не нужен.

## 5. Переключить первый Windows хост

Запускайте только после проверки HTTPS и переноса данных. На текущем Windows VPS под тем же пользователем MT5 подготовьте отдельную задачу. Скрипт берёт Python, пути и зашифрованный токен из существующего `.dashboard-start.local.json`, не выводит токен и не меняет старую задачу:

```powershell
.\scripts\remote_host_task.ps1 -Mode Save -ServerUrl https://ВАШ-ДОМЕН
.\scripts\remote_host_task.ps1 -Mode Install
```

Перед стартом новой задачи контролируемо остановите старую `MT5Dashboard`, сохранив её конфигурацию для отката. Не удаляйте `collector-central.db` (или фактический файл сборщика): в нём есть смещения файлов и очередь. Затем `Start-ScheduledTask -TaskName MT5CollectorRemote` и проверьте свежий `run-logs\remote-collector-*.log`. Держите одну активную точку приёма на хост. Новая задача, как и старая, запускается при интерактивном входе MT5 пользователя; после перезагрузки без входа она не стартует. Для новых Windows VPS используйте [отдельную инструкцию агенту](connect-new-windows-agent.md).

Проверьте `pending=0`, свежесть Journal/Experts и account snapshots, уведомления и строгий аудит. На Linux VPS аудит использует приватный файл службы:

```sh
sudo -u dashboard sh -c 'set -a; . /etc/mt5-dashboard/central.env; exec /opt/mt5-dashboard/.venv/bin/python -m server.audit --postgres --strict'
```

После проверки добавляйте остальные VPS по одному. Монитор `/health` должен жить на **другом** VPS, чтобы сообщать о полном падении центрального хоста.

Для резервирования PostgreSQL делайте регулярный `pg_dump` вне каталога сайта и проверяйте восстановление на отдельной базе. Период хранения логов и бэкапов следует установить по фактическому приросту данных после суток работы; сейчас достоверной оценки новой схемы ещё нет.
