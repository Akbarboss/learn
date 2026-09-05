# Wordbook — английский по субтитрам

Словарь-тренажёр из субтитров фильмов и сериалов. Каждое слово, фраза
и грамматическая конструкция даны с переводом на **русский** и **узбекский**
и с таймкодом — минутой, на которой это звучит в серии.

Сейчас в словаре: **The Mentalist, сезон 1, серия 3 «Red Tide»** — 247 записей
в 8 главах.

---

## Как это устроено

Всё содержание лежит в одном файле — **`index.html`**. Он раздаётся через
GitHub Pages как обычный сайт.

Приложение для Android (`android/`) — это **только оболочка**: внутри пустой
экран с WebView, который открывает тот же адрес. Никаких уроков внутри APK нет.

Отсюда главное правило:

| Что вы меняете | Нужно ли пересобирать APK |
|---|---|
| Слова, переводы, новые главы, дизайн (`index.html`) | **Нет.** Достаточно `git push` |
| Адрес сайта, имя приложения, иконка (`android/`) | Да, один раз |

Поэтому добавлять новые серии можно бесконечно, и телефоны получат их сами.

---

## Шаг 1. Выложить сайт

1. Загрузите содержимое этой папки в репозиторий `Akbarboss/learn` — так,
   чтобы `index.html` лежал **в корне**, а не внутри подпапки.

   ```bash
   git init
   git add .
   git commit -m "Wordbook: Red Tide"
   git branch -M main
   git remote add origin git@github.com:Akbarboss/learn.git
   git push -u origin main
   ```

2. В репозитории: **Settings → Pages → Build and deployment**
   Source: `Deploy from a branch`, Branch: `main`, папка: `/ (root)` → **Save**.

3. Через минуту сайт откроется по адресу:

   ```
   https://akbarboss.github.io/learn/
   ```

Эту ссылку отправьте первому брату — ему больше ничего не нужно.
На телефоне он может нажать в браузере «Добавить на главный экран» —
получится значок как у обычного приложения.

---

## Шаг 2. Собрать APK

1. Откройте **`android/app/src/main/res/values/config.xml`** и впишите свой
   адрес — это единственная строка, которую нужно поменять:

   ```xml
   <string name="site_url">https://akbarboss.github.io/learn/</string>
   ```

   Слеш в конце обязателен.

2. Сделайте `git push`. GitHub Actions соберёт APK сам —
   Android Studio устанавливать не нужно.

3. Готовый файл появится по постоянной ссылке:

   ```
   https://github.com/Akbarboss/learn/releases/download/apk/wordbook.apk
   ```

   Отправьте её второму брату и себе. На телефоне нужно один раз разрешить
   «Установка из неизвестных источников».

Ссылка не меняется от сборки к сборке — при следующей пересборке файл по
тому же адресу просто обновится.

---

## Шаг 3. Добавить новую серию

1. Пришлите мне `.srt` — я сделаю новую главу или отдельную страницу.
2. `git push`.
3. Всё. У всех троих обновится при следующем запуске (нужен интернет).

---

## Офлайн

При первом запуске нужен интернет. Дальше service worker (`sw.js`) держит
копию страницы в кэше, и приложение работает без сети — в метро, в дороге.
Когда интернет появляется, свежая версия подтягивается автоматически.

Если интернета не было ни разу, приложение показывает экран
«Internet kerak» с кнопкой «Повторить».

---

## Три ученика

Имена жёстко заданы в коде: **Akbar, Abror, Muhammadali**. Свободного ввода
нет — только выбор из трёх.

При первом открытии страница спрашивает «Kim o'qiyapti?» и не даёт отмечать
слова, пока имя не выбрано. Дальше имя запоминается, а переключить его можно
в шапке в любой момент — данные подставятся сразу.

Под каждым именем хранится:

| | |
|---|---|
| `learned` | набор выученных слов |
| `log` | **каждое** действие: слово, отметил или снял, точное время |
| `days` | сколько новых слов выучено в каждый день |

Рядом с именем в шапке видно число выученных слов — так на одном телефоне
сразу видно всех троих, кто на нём занимался.

### Отчёт

Кнопка **«Copy report»** копирует в буфер сводку по текущему ученику:

```
Red Tide Wordbook · Abror
84 / 247  (34%)  ·  2026-08-29
  Crime & investigation: 22/40
  Phrasal verbs: 14/30
  ...

Oxirgi kunlar / последние дни:
  08-25 — 11
  08-26 — 7
  08-29 — 4
```

Брат нажимает кнопку и присылает это в Telegram. Видно не только сколько
всего, но и занимался ли он вообще на этой неделе.

### Важное ограничение

Данные лежат **на том телефоне, где занимались** (localStorage). Сервера у
проекта нет, поэтому прогресс Аброра не появится на телефоне Акбара сам —
только через присланный отчёт.

> Чтобы всё сходилось в одном месте автоматически, нужна база в облаке
> (Firebase или Supabase — у обоих хватает бесплатного тарифа). Скажите,
> если нужно — подключу.

---

## Структура

```
index.html                 весь словарь: содержание, поиск, тренажёр
manifest.webmanifest       чтобы сайт ставился как приложение
sw.js                      офлайн-кэш
icon-*.png                 иконки
.nojekyll                  чтобы GitHub Pages не трогал файлы

android/                   оболочка-WebView
  app/src/main/res/values/config.xml     ← адрес сайта, единственная правка
  app/src/main/java/.../MainActivity.java

.github/workflows/build-apk.yml          сборка APK на стороне GitHub
```

---

## Что уже умеет страница

- **Поиск** по трём языкам сразу: `убийца`, `qotil`, `killer` → одна запись.
- **Hide translations** — прячет переводы, чтобы проверять себя.
- **Отметки выученного** — кружок слева, полоса прогресса сверху.
- **Тёмная тема** — сама подхватывает настройку телефона.
- **Печать** — страницу можно распечатать как настоящий словарь.
## Speech and cloud progress

Each English word, phrase or sentence has a 🔊 button. It uses the phone's
English text-to-speech voice. On Android, install or enable an English
Text-to-Speech voice if the phone is silent.

The default storage is local to the device. To show the same learned-word
counts on another phone, connect Supabase:

1. Create a free project at https://supabase.com/.
2. In SQL Editor, create a table named `learner_progress` with columns
   `name`, `learned`, `log`, `days`, and `updated_at`, then enable read,
   insert, and update policies for the public `anon` role.
3. Copy Project URL and the public `anon` key from Project Settings → API into
   [sync-config.js](sync-config.js).

Use only the public `anon` key in this file. Never put a `service_role` key in
the website. After pushing `sync-config.js`, the site will load the selected
learner's cloud data and save every new mark there.
