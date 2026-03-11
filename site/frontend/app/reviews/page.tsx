'use client';

import { useState } from 'react';
import { Header } from '@/components/layout/Header';

const REVIEWS = [
  {
    text: 'Работаю на доставке, целый день за рулем. Раньше слушал подкасты, но надоело. Случайно наткнулся на этот сайт. Пацаны, это реально спасение. Выбор жанров — мое почтение, есть такие темы, за которые на других сайтах банят сразу. Голос обычный, зато истории такие, что в сон не клонит. Респект за честный контент без цензуры.',
    author: 'Дмитрий',
    age: '32 года',
    badge: 'Прослушано более 50 историй',
  },
  {
    text: 'Люблю эротическую литературу, но читать времени нет, глаза к вечеру устают. Искала именно аудио. Здесь подкупило количество — реально 34 жанра, я неделю только по заголовкам лазила. Нравится, что всё разложено по полочкам: зашла в "Измену" или "Офис" и слушаешь. Без лишнего пафоса и прикрас, всё как в жизни.',
    author: 'Елена',
    age: '',
    badge: 'Прослушано более 50 историй',
  },
  {
    text: 'Заказывал здесь озвучку своего текста. Сюжет был специфический, думал, откажут. Нет, всё сделали, причем быстро. Да, это не голос из кино, но интонации правильные, слушать приятно. Для тех, кто ищет именно "грязный" и редкий эксклюзив — лучше места не найти.',
    author: 'Anon_X',
    age: '',
    badge: 'Заказ персональной озвучки',
  },
  {
    text: 'Подписка Премиум того стоит только ради доступа к закрытым разделам. Там реально самый сок. Главный плюс — огромный каталог. Можно каждый день слушать новое и не повторяться. Сайт простой, всё работает, ничего лишнего.',
    author: 'Сергей В.',
    age: '',
    badge: 'Premium пользователь',
  },
];

export default function ReviewsPage() {
  const [reviewText, setReviewText] = useState('');
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (reviewText.trim()) setSubmitted(true);
  };

  return (
    <div className="min-h-screen bg-[#000814] text-white">
      <Header />
      <main className="relative z-10 pt-28 pb-24 px-4 md:px-6">
        <div className="max-w-6xl mx-auto">
          <h1 className="font-heading text-4xl md:text-5xl font-bold text-white text-center mb-4">
            Отзывы
          </h1>
          <p className="text-zinc-400 text-center mb-16 text-sm max-w-xl mx-auto">
            Что говорят слушатели о каталоге и тематике. Мы ценим каждое мнение и стремимся стать лучше.
          </p>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-16">
            {REVIEWS.map((r, i) => (
              <div
                key={i}
                className="relative overflow-hidden bg-white/[0.02] backdrop-blur-xl border border-white/10 rounded-3xl p-8 group transition-all duration-500 hover:-translate-y-1 hover:border-[#00B4D8]/40 hover:shadow-[0_15px_40px_rgba(0,180,216,0.15)]"
              >
                {/* Декор: Огромная фоновая кавычка */}
                <div className="absolute -top-4 -right-2 text-[150px] font-serif font-black text-white/[0.03] pointer-events-none select-none leading-none rotate-6 group-hover:text-[#00B4D8]/[0.05] transition-colors duration-500">
                  "
                </div>

                {/* Шапка отзыва */}
                <div className="flex items-center gap-4 mb-6 relative z-10">
                  <div className="w-14 h-14 rounded-full bg-gradient-to-br from-blue-600 to-[#00B4D8] flex items-center justify-center text-white text-xl font-bold shadow-lg shrink-0">
                    {r.author.charAt(0)}
                  </div>
                  <div>
                    <div className="text-white font-bold text-lg">
                      {r.author} {r.age && <span className="font-normal text-white/60 text-sm ml-1">{r.age}</span>}
                    </div>
                    <div className="text-xs text-[#00B4D8] uppercase tracking-wider font-semibold mt-0.5">
                      {r.badge}
                    </div>
                  </div>
                </div>

                {/* Текст отзыва */}
                <p className="text-white/80 leading-relaxed font-light italic relative z-10">
                  {r.text}
                </p>
              </div>
            ))}
          </div>

          <div className="max-w-2xl mx-auto relative overflow-hidden bg-white/[0.02] backdrop-blur-xl border border-white/10 rounded-3xl p-8 md:p-10">
            <h2 className="text-2xl font-bold text-white mb-2 text-center">Расскажи о своих впечатлениях</h2>
            <p className="text-zinc-400 text-sm mb-8 text-center">Ваше мнение важно — помогите другим узнать о нас.</p>
            {submitted ? (
              <p className="text-[#00B4D8] text-center py-4 font-medium text-lg">Спасибо! Ваш отзыв принят.</p>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-6">
                <textarea
                  value={reviewText}
                  onChange={(e) => setReviewText(e.target.value)}
                  placeholder="Что вам понравилось? Что бы вы изменили? Поделитесь опытом..."
                  rows={5}
                  className="w-full rounded-xl border border-white/10 bg-black/40 px-4 py-4 text-white placeholder:text-zinc-500 focus:border-[#00B4D8] focus:outline-none focus:ring-1 focus:ring-[#00B4D8] transition-all duration-300 resize-none text-base"
                  aria-label="Текст отзыва"
                />
                <button
                  type="submit"
                  className="w-full py-4 rounded-xl font-bold text-lg text-white bg-gradient-to-r from-[#00B4D8] to-blue-600 hover:shadow-[0_0_30px_rgba(0,180,216,0.4)] hover:scale-[1.02] transition-all duration-300 disabled:opacity-50 disabled:hover:scale-100"
                  disabled={!reviewText.trim()}
                >
                  Отправить отзыв
                </button>
              </form>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
