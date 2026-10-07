# Инструкция агенту: отправка логов MT5 с нового Windows VPS

Ты на **другом Windows-сервере с MT5**. Настрой отправку Journal и Experts на центральный дашборд `https://46.225.132.195`. Работай под тем же Windows-пользователем, который запускает MT5 и имеет доступ к его папкам данных. Эта процедура подключает **логи и состояние терминалов**; сбор позиций и сделок на новом хосте настраивается отдельно.

Не публикуй токен в чате, Git, логах или командах запуска. В отчёте передай только `host_id`, количество терминалов, состояние задачи, свежесть heartbeat и число `pending`. Не используй браузерный пароль `operator` для сборщика: ему нужен отдельный bearer-токен этого хоста.

## 1. Подготовь центральный сервер совместно с его администратором

1. Выбери постоянный уникальный `host_id`, например `mt5-win-02`. Он должен отличаться от уже подключённого `vps72565826`. Запиши точное значение: регистр и символы должны совпадать в локальной базе и центральной карте токенов.
2. Администратор центрального VPS генерирует **новый случайный токен минимум 32 символа** для этого хоста и передаёт его через защищённый канал. Он добавляет пару `host_id` → токен в JSON переменной `DASHBOARD_HOST_TOKENS` файла `/etc/mt5-dashboard/central.env`, сохраняя существующие пары. Файл остаётся с правами `0600` и владельцем `dashboard`.
3. На центральном VPS администратор проверяет конфигурацию, выводя только имена хостов, и перезапускает API:

   ```sh
   sudo -u dashboard bash -c 'cd /opt/mt5-dashboard; set -a; source /etc/mt5-dashboard/central.env; .venv/bin/python -c "from shared.config import host_tokens; import os; print(sorted(host_tokens(os.environ)))"'
   sudo systemctl restart mt5-dashboard
   curl -fsS https://46.225.132.195/health
   ```

   Перезапуск кратко прерывает приём данных; локальные очереди сборщиков сохраняют неотправленные строки. Порт API `8765` и PostgreSQL наружу открывать не нужно.

Если у тебя нет доступа к центральному VPS, передай его администратору `host_id` и запрос на отдельный токен; продолжай после подтверждения регистрации. Самостоятельно менять действующую карту токенов без её текущих значений нельзя.

## 2. Установи исходники и проверь HTTPS на Windows-хосте

Открой PowerShell под пользователем MT5. Для регистрации задачи понадобится PowerShell с правами администратора **под той же учётной записью**. Если проект уже есть, проверь изменения и обнови его без потери локальных файлов; иначе:

```powershell
git clone --branch main https://github.com/mishadev-algo/dashboard.git C:\dashboard
Set-Location C:\dashboard
py -3.13 --version
$PythonExe = (& py -3.13 -c 'import sys; print(sys.executable)').Trim()
Invoke-RestMethod https://46.225.132.195/health
```

Ожидается `status: ok` и обычная проверка TLS без флага `-k` или отключения проверки сертификата. Python 3.13 можно установить по [инструкции сборщика](collector.md), если его нет. Для одних логов пакет `MetaTrader5` не требуется.

## 3. Определи папки MT5 и создай локальную базу

В каждом нужном терминале открой **File → Open Data Folder** и запиши именно папку данных терминала. В ней должны быть `Logs` и `MQL5\Logs`. Не путай её с каталогом установки `terminal64.exe`. Для ориентировки перечисли стандартные папки:

```powershell
Get-ChildItem (Join-Path $env:APPDATA 'MetaQuotes\Terminal') -Directory |
    Where-Object { Test-Path (Join-Path $_.FullName 'Logs') } |
    Select-Object -ExpandProperty FullName
```

Создай `C:\dashboard\inventory.json` с **реальными абсолютными путями**: работающие терминалы в `expected`, старые папки, которые нужно помнить, в `archived`. Не оставляй `ACTIVE_FOLDER_*` из [примера](inventory.example.json). Например:

```json
{
  "expected": ["C:\\Users\\Trader\\AppData\\Roaming\\MetaQuotes\\Terminal\\REAL_FOLDER_ID"],
  "archived": []
}
```

Назначь `host_id` при **первом** локальном сканировании. Оно создаст `collector-central.db`, сохранит смещения файлов и очередь; до регистрации токена ничего не отправит:

```powershell
Set-Location C:\dashboard
py -3.13 -m collector --db collector-central.db --host-id mt5-win-02 `
    --inventory inventory.json --root (Join-Path $env:APPDATA 'MetaQuotes\Terminal')
```

Замени `mt5-win-02` своим согласованным `host_id`. Если есть portable или нестандартные папки, перечисли их в `expected`; добавь их точные пути через `--terminal` при первичной проверке и при сохранении настроек задачи. Не удаляй существующую базу сборщика: в ней смещения и очередь. Если база уже существует, сначала проверь её `host_id` и используй его, а не создавай второй идентификатор для тех же логов.

## 4. Сохрани токен и установи задачу

Запускай эти команды под тем же пользователем MT5. `Save` запросит токен скрытым вводом, зашифрует его через Windows DPAPI и сохранит в игнорируемом Git файле `.dashboard-remote.local.json`; токен не попадёт в параметры задачи. Укажи полный путь к найденному Python:

```powershell
Set-Location C:\dashboard
.\scripts\new_host_task.ps1 -Mode Save -ServerUrl https://46.225.132.195 `
    -PythonExe $PythonExe -CollectorDb collector-central.db -Inventory inventory.json
.\scripts\new_host_task.ps1 -Mode Install
Start-ScheduledTask -TaskName MT5CollectorRemote
```

Для нестандартных корневых каталогов добавь к `Save` параметр `-Roots @((Join-Path $env:APPDATA 'MetaQuotes\Terminal'), 'D:\OtherData')`, если также нужен стандартный каталог; для точных portable папок — `-Terminals @('D:\MT5Portable\DataFolder')`. Без `-Roots` скрипт сканирует стандартный `%APPDATA%\MetaQuotes\Terminal`. `Save` не перезапишет существующие настройки без явного `-Force`; перед заменой проверь пользователя, базу и токен.

Задача запускается при **интерактивном входе** пользователя MT5. После перезагрузки без входа она не стартует. Не запускай параллельно вторую задачу сборщика с той же базой. Сохрани каталог проекта и `collector-central.db` при обновлениях.

## 5. Проверь доставку

```powershell
Get-ScheduledTask -TaskName MT5CollectorRemote | Select-Object TaskName,State
$log = Get-ChildItem C:\dashboard\run-logs\remote-collector-*.log |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
Get-Content -LiteralPath $log.FullName -Tail 30
```

Задача должна быть `Running`. В журнале проверь правильный `host_id`, `origin=https://46.225.132.195`, число `expected`, `missing=()`, `unknown=()` и `pending=0` после успешной отправки. Первые события могут появиться сразу, а очередная отправка и heartbeat происходят примерно раз в пять минут. `events=0` нормально для тихих логов. Сравни несколько строк обоих потоков с вкладками Journal и Experts в MT5.

Попроси администратора центрального VPS подтвердить появление **этого** `host_id`, свежий heartbeat и события обоих потоков на дашборде или в PostgreSQL. Общий `/health` отвечает даже при остановленном Windows-сборщике, поэтому он не доказывает доставку логов. Глобальный строгий аудит может содержать проблему другого хоста; оценивай и новый хост отдельно.

Если задача стала `Ready`, прочитай конец последнего журнала: она завершилась. При `401` проверь точное совпадение `host_id` и токена на обеих сторонах; при ошибке TLS проверь дату, IP и сертификат, не отключая проверку. При `MemoryError` или повторной остановке зафиксируй время и traceback и сообщи администратору: на первом подключённом Windows-хосте уже наблюдался такой сбой, и простой перезапуск не считается исправлением.
