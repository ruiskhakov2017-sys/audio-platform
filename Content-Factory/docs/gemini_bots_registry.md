# Gemini Bots Registry

Реестр Gemini-ботов для 5 аккаунтов и 7 функций.

Важно:
- используются нормализованные ссылки `https://gemini.google.com/gem/...` (без `/u/N`);
- здесь только структура и назначения;
- запуск и оркестрация ботов описываются отдельно.

## Боты и функции

| bot_key | Что делает | Вход | Выход | Быстрый/долгий | Chunking |
|---|---|---|---|---|---|
| `general_selection` | Отсеивает непригодные рассказы в общий пул | Текст рассказа | `selected/rejected/review` + причина | Быстрый | Нет |
| `site_info_builder` | Создает site info (`title, description, genres, tags, voice, visual`) | Текст рассказа | `info.txt`/структурный info-output | Средний | Нет |
| `youtube_selection` | Строгий отбор top-tier для YouTube поверх уже хорошего пула | Рассказ после `general_selection` | `youtube_selected/site_only/review` | Быстрый | Нет |
| `youtube_safe_text` | Делает safe-версию текста для YouTube | YouTube-кандидат | Safe-текст + прогресс | Долгий | Да |
| `youtube_ad_point` | Находит mid-roll точку внутри safe-текста, ничего не вставляет | YouTube-safe текст | insertion point / fragment / anchor | Средний | Опционально |
| `youtube_characters` | Формирует героев/внешность/стиль | Safe/Promo-applied текст | `characters` output | Средний | Опционально |
| `youtube_scene_prompts` | Формирует сцены и промпты с учетом characters | Текст + characters output | `scene_prompts` output | Долгий | Да |

## Аккаунты и ссылки на ботов (таблица)

| email | general_selection | site_info_builder | youtube_selection | youtube_safe_text | youtube_ad_point | youtube_characters | youtube_scene_prompts |
|---|---|---|---|---|---|---|---|
| `ru.iskhakov2017@gmail.com` | [link](https://gemini.google.com/gem/ada3e736032c) | [link](https://gemini.google.com/gem/ef342be5afa7) | [link](https://gemini.google.com/gem/58c69ffea8d6) | [link](https://gemini.google.com/gem/4c2759712902) | [link](https://gemini.google.com/gem/c89e1ab6a1c5) | [link](https://gemini.google.com/gem/8f21a94294eb) | [link](https://gemini.google.com/gem/ae09c90070ca) |
| `isi.cordeiro@gmail.com` | [link](https://gemini.google.com/gem/8853dff960d9) | [link](https://gemini.google.com/gem/39506fd2e088) | [link](https://gemini.google.com/gem/7bd74172a025) | [link](https://gemini.google.com/gem/cc83b7ab68d8) | [link](https://gemini.google.com/gem/80f6885207ad) | [link](https://gemini.google.com/gem/4fa20d3d01e6) | [link](https://gemini.google.com/gem/23ab5e8af1ce) |
| `iheuko119@gmail.com` | [link](https://gemini.google.com/gem/d6c7039ff80b) | [link](https://gemini.google.com/gem/ca381fe3b9c3) | [link](https://gemini.google.com/gem/bca606b780cc) | [link](https://gemini.google.com/gem/90f4d0ef7309) | [link](https://gemini.google.com/gem/35be1dacd16a) | [link](https://gemini.google.com/gem/590a8fe7e18b) | [link](https://gemini.google.com/gem/ebd9a4e57f10) |
| `goegoeseijin@gmail.com` | [link](https://gemini.google.com/gem/3658246c297f) | [link](https://gemini.google.com/gem/1718c40dc603) | [link](https://gemini.google.com/gem/81e49824c856) | [link](https://gemini.google.com/gem/67e9e22f3255) | [link](https://gemini.google.com/gem/800da775b297) | [link](https://gemini.google.com/gem/a98d4bb31d0c) | [link](https://gemini.google.com/gem/269facbdccf3) |
| `suteadodesun6@gmail.com` | [link](https://gemini.google.com/gem/301d293665c3) | [link](https://gemini.google.com/gem/6f2dbfff7e8e) | [link](https://gemini.google.com/gem/9e7b3360a0a8) | [link](https://gemini.google.com/gem/cb0fd01669a4) | [link](https://gemini.google.com/gem/74eb43ce6b24) | [link](https://gemini.google.com/gem/91f5c48139a7) | [link](https://gemini.google.com/gem/13ce70436822) |

## Последовательность YouTube ветки (принятая)

1. `youtube_selection`
2. `youtube_safe_text`
3. `youtube_ad_point`
4. promo insertion script (`intro/mid/outro`)
5. `youtube_characters`
6. `youtube_scene_prompts`

## Promo assets

- `promo_intro_en`
- `promo_mid_en`
- `promo_outro_en`

Важно:
- `youtube_ad_point` только определяет точку вставки;
- вставку intro/mid/outro делает скрипт, не Gemini-бот.
