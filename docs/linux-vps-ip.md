# HTTPS без домена: доступ по публичному IPv4

Для нового VPS используется `https://46.225.132.195`. [Let's Encrypt выдаёт сертификаты для IP](https://letsencrypt.org/2026/03/11/shorter-certs-certbot/), но их срок около шести дней, поэтому продление и перезагрузка Caddy должны работать автоматически. Нужен Certbot 5.4 или новее. Эти шаги дополняют [основной запуск Linux](linux-vps.md).

В `deploy/linux/` есть временный Caddyfile для проверки IP и основной Caddyfile. В обоих замените `PUBLIC_IP` на IPv4 VPS. Основной файл требует готовые сертификаты в `/etc/caddy/ip-cert` и хеш Basic Auth. `certbot-caddy-deploy-hook.sh` при выпуске и продлении копирует сертификат с правами доступа для Caddy и перезагружает службу.

Порядок на VPS:

1. Установить Caddy по [официальной инструкции](https://caddyserver.com/docs/install), Certbot 5.4+ и создать `/var/www/letsencrypt`.
2. Установить `Caddyfile.ip-bootstrap.example` как `/etc/caddy/Caddyfile`, выполнить `caddy validate`, затем запустить Caddy. На 80 порту должны проходить запросы `/.well-known/acme-challenge/*`.
3. Установить deploy hook в `/etc/letsencrypt/renewal-hooks/deploy/mt5-dashboard-caddy.sh` с правами `0755`.
4. Выпустить сертификат после проверки доступности ACME маршрута:

   ```sh
   certbot certonly --webroot --webroot-path /var/www/letsencrypt \
     --preferred-profile shortlived --ip-address 46.225.132.195
   ```

   Не повторяйте выпуск при временной ошибке без проверки журнала Certbot: у центра сертификации есть ограничения по числу попыток.
5. Скопировать `Caddyfile.ip.example` в `/etc/caddy/Caddyfile`, заменить IP и хеш пароля, проверить `caddy validate` и перезагрузить Caddy.
6. Проверить `curl -fsS https://46.225.132.195/health`, ответ 401 для страницы без Basic Auth, а также `certbot renew --dry-run` и штатный таймер продления Certbot.

Строка `--server-url` Windows сборщиков будет `https://46.225.132.195`. Никакие обходы проверки TLS на сборщиках не нужны. При смене IP адреса потребуется новый сертификат и изменение настроек сборщиков; стабильный домен упростит возможный переезд позже.
