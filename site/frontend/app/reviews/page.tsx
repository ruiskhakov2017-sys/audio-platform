'use client';

import { useState } from 'react';
import { Header } from '@/components/layout/Header';

const pageStyles = {
  grain: `url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")`,
  gradient: 'radial-gradient(ellipse 80% 50% at 50% 0%, rgba(0,180,216,0.08) 0%, transparent 50%)',
};

const REVIEWS = [
  {
    text: 'Работаю на доставке, целый день за рулем. Раньше слушал подкасты, но надоело. Случайно наткнулся на этот сайт. Пацаны, это реально спасение. Выбор жанров — мое почтение, есть такие темы, за которые на других сайтах банят сразу. Голос обычный, зато истории такие, что в сон не клонит. Респект за честный контент без цензуры.',
    author: 'Дмитрий, 32 года.',
    badge: 'Прослушано более 50 историй',
  },
  {
    text: 'Люблю эротическую литературу, но читать времени нет, глаза к вечеру устают. Искала именно аудио. Здесь подкупило количество — реально 34 жанра, я неделю только по заголовкам лазила. Нравится, что всё разложено по полочкам: зашла в "Измену" или "Офис" и слушаешь. Без лишнего пафоса и прикрас, всё как в жизни.',
    author: 'Елена.',
    badge: 'Прослушано более 50 историй',
  },
  {
    text: 'Заказывал здесь озвучку своего текста. Сюжет был специфический, думал, откажут. Нет, всё сделали, причем быстро. Да, это не голос из кино, но интонации правильные, слушать приятно. Для тех, кто ищет именно "грязный" и редкий эксклюзив — лучше места не найти.',
    author: 'Anon_X.',
    badge: 'Заказ персональной озвучки',
  },
  {
    text: 'Подписка Премиум того стоит только ради доступа к закрытым разделам. Там реально самый сок. Главный плюс — огромный каталог. Можно каждый день слушать новое и не повторяться. Сайт простой, всё работает, ничего лишнего.',
    author: 'Сергей В.',
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
      <div className="fixed inset-0 z-0 pointer-events-none opacity-[0.03]" style={{ backgroundImage: pageStyles.grain }} />
      <div className="fixed inset-0 z-0 pointer-events-none" style={{ background: pageStyles.gradient }} />
      <Header />
      <main className="relative z-10 pt-28 pb-24 px-4 md:px-6">
        <div className="max-w-3xl mx-auto">
          <h1 className="font-heading text-3xl md:text-4xl font-bold text-white text-center mb-2">
            Отзывы
          </h1>
          <p className="text-zinc-400 text-center mb-12 text-sm">
            Что говорят слушатели о каталоге и тематике.
          </p>

          <div className="space-y-8 mb-16">
            {REVIEWS.map((r, i) => (
              <blockquote
                key={i}
                className="rounded-xl border border-white/10 bg-white/[0.04] p-6 md:p-8"
              >
                <span className="inline-block text-[11px] uppercase tracking-widest text-amber-400/80 border border-amber-400/30 rounded-full px-3 py-1 mb-4">
                  {r.badge}
                </span>
                <p className="text-zinc-200 leading-relaxed text-base md:text-lg mb-4">«{r.text}»</p>
                <cite className="text-sm text-amber-400/90 not-italic">— {r.author}</cite>
              </blockquote>
            ))}
          </div>

          <div className="rounded-xl border border-white/10 bg-white/[0.04] p-6 md:p-8">
            <h2 className="text-xl font-semibold text-white mb-2">Расскажи о своих впечатлениях</h2>
            <p className="text-zinc-400 text-sm mb-5">Ваше мнение важно — помогите другим узнать о нас.</p>
            {submitted ? (
              <p className="text-zinc-400 py-4">Спасибо! Ваш отзыв принят.</p>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-4">
                <textarea
                  value={reviewText}
                  onChange={(e) => setReviewText(e.target.value)}
                  placeholder="Что вам понравилось? Что бы вы изменили? Поделитесь опытом..."
                  rows={5}
                  className="w-full rounded-lg border border-white/10 bg-white/5 px-4 py-4 text-white placeholder:text-zinc-500 focus:border-[#00B4D8]/50 focus:outline-none focus:ring-1 focus:ring-[#00B4D8]/30 resize-none text-base"
                  aria-label="Текст отзыва"
                />
                <button
                  type="submit"
                  className="w-full md:w-auto px-8 py-3 rounded-full bg-[#00B4D8] text-white font-medium hover:bg-cyan-600 transition-colors disabled:opacity-50"
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
