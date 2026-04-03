# PGU crypto game

Проект для PGU: Telegram crypto game (mini app + бот).

Готовый стартовый MVP:

- Telegram бот с `/start`
- Сообщение "это игра-фармилка..."
- Кнопка открытия Telegram Mini App
- Mini App с авторизацией через Phantom
- Меню, визуально похожее на референс (4 круглые кнопки)
- Серверная проверка подписи (challenge-signature verify)
- Привязка `telegram_id -> wallet` в SQLite

## 1) Установка

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 2) Настройка окружения

Скопируй `.env.example` в `.env` и заполни:

- `BOT_TOKEN` - токен от BotFather
- `WEBAPP_URL` - публичный URL мини-аппа (например, через ngrok)
- `WEBAPP_HOST`, `WEBAPP_PORT` - параметры запуска Flask
- `DB_PATH` - путь к SQLite базе
- `CHALLENGE_TTL_SECONDS` - срок жизни challenge

## 3) Запуск mini app

```bash
python miniapp/app.py
```

Для Telegram нужен HTTPS URL. Для локального теста можно сделать туннель:

```bash
ngrok http 8080
```

И вставить `https://...ngrok...` в `WEBAPP_URL`.

## 4) Запуск бота

```bash
python bot.py
```

## Что реализовано по логике

- `/start` отправляет текст про фарм и будущий токен
- По кнопке открывается mini app
- В mini app пользователь подключает Phantom
- Backend выдает challenge и проверяет подпись кошелька
- После успешной верификации создается связка Telegram ID и кошелька
- При повторном входе состояние восстанавливается с сервера
- Кнопка "Отключить" возвращает к экрану авторизации
- Реализованы 4 игровых действия с сохранением прогресса в SQLite
- Статы питомца постепенно снижаются со временем (tick decay)
- Пассивный фарм XP каждый час
- Магазин аксессуаров, повышающих XP/час
- Кейс за 20 000 XP с 6 вариантами наград (XP, x3 буст, MGPT)

## Что можно сделать следующим шагом

- Добавить backend-подтверждение подписи кошелька (challenge-signin)
- Привязать Telegram user id к wallet address в БД
- Реализовать игровую механику кнопок меню (еда, сон, туалет и т.д.)
- Добавить начисление очков/токенов и задания
