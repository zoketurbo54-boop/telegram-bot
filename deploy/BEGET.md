# Деплой на VPS (Beget и любой Ubuntu/Debian)

Я не могу зайти на твой сервер сам — нет SSH-доступа. Ниже команды, которые можно копировать по порядку. Файлы `nginx-mypiskagame.conf` и `*.service` лежат в этой же папке `deploy/`.

## Что понадобится

- Домен (или поддомен), A-запись на IP сервера Beget
- Доступ по SSH (логин/пароль или ключ из панели Beget)
- Репозиторий с кодом (GitHub или `scp` архива)

## 1. Подключение и базовые пакеты

```bash
ssh root@ТВОЙ_IP
# или пользователь, который выдал Beget

apt update && apt install -y python3 python3-venv python3-pip git nginx certbot python3-certbot-nginx ufw
```

Фаервол (если используешь ufw):

```bash
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw enable
```

## 2. Пользователь и каталог приложения

```bash
sudo mkdir -p /opt/mypiskagame
sudo useradd -r -d /opt/mypiskagame -s /usr/sbin/nologin piskagame 2>/dev/null || true
sudo chown piskagame:piskagame /opt/mypiskagame
```

Код на сервер (один из вариантов):

```bash
sudo -u piskagame git clone https://github.com/ТВОЙ_АККАУНТ/MyPiskaGame.git /opt/mypiskagame
```

Если репозиторий приватный — настрой deploy key или залей архив и распакуй в `/opt/mypiskagame`.

## 3. Виртуальное окружение и зависимости

```bash
cd /opt/mypiskagame
sudo -u piskagame python3 -m venv .venv
sudo -u piskagame .venv/bin/pip install -r requirements.txt
```

## 4. Переменные окружения

```bash
sudo -u piskagame cp .env.example .env
sudo -u piskagame nano .env
```

Обязательно:

- `BOT_TOKEN` — от BotFather
- `WEBAPP_URL=https://твой-поддомен.домен` — **ровно тот URL**, по которому откроется мини-апп (без лишнего слэша в конце, если приложение в корне)
- `WEBAPP_HOST=127.0.0.1` — слушаем только localhost, наружу отдаёт nginx
- `WEBAPP_PORT=8080`
- `FLASK_DEBUG=0`
- `DB_PATH=miniapp/data.db` (или абсолютный путь)

Права на БД создаст приложение при первом запуске; каталог `miniapp` должен быть доступен пользователю `piskagame`.

## 5. Systemd

Скопируй юниты (пути уже под `/opt/mypiskagame`):

```bash
sudo cp /opt/mypiskagame/deploy/piskagame-web.service /etc/systemd/system/
sudo cp /opt/mypiskagame/deploy/piskagame-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now piskagame-web.service
sudo systemctl enable --now piskagame-bot.service
```

Проверка:

```bash
sudo systemctl status piskagame-web.service
sudo systemctl status piskagame-bot.service
```

Логи:

```bash
journalctl -u piskagame-web.service -f
journalctl -u piskagame-bot.service -f
```

## 6. Nginx

Подставь свой домен в конфиг:

```bash
sudo cp /opt/mypiskagame/deploy/nginx-mypiskagame.conf /etc/nginx/sites-available/mypiskagame
sudo sed -i 's/CHANGE_ME_DOMAIN/твой-поддомен.домен/g' /etc/nginx/sites-available/mypiskagame
sudo ln -sf /etc/nginx/sites-available/mypiskagame /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Сертификат Let’s Encrypt:

```bash
sudo certbot --nginx -d твой-поддомен.домен
```

После выпуска сертификата снова проверь `WEBAPP_URL` в `.env` — должен быть **https://**.

```bash
sudo systemctl restart piskagame-web.service piskagame-bot.service
```

## 7. Обновление кода

```bash
cd /opt/mypiskagame
sudo -u piskagame git pull
sudo -u piskagame .venv/bin/pip install -r requirements.txt
sudo systemctl restart piskagame-web.service piskagame-bot.service
```

## Если что-то не заводится

- **502** — не слушает Flask: `curl -sI http://127.0.0.1:8080`, смотри `journalctl -u piskagame-web`.
- **Бот молчит** — `BOT_TOKEN`, сеть до `api.telegram.org` (на российских VPS иногда нужен прокси/VPN — это ограничение сети, не кода).
- **Кнопка мини-аппа** — `WEBAPP_URL` и домен в BotFather должны совпадать с реальным HTTPS.

Если пришлёшь вывод `systemctl status` и последние строки логов (без токена бота), можно разобрать ошибку по месту.
