'use client';

import { useState } from 'react';
import { Header } from '@/components/layout/Header';

const pageStyles = {
  grain: `url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")`,
  gradient: 'radial-gradient(ellipse 80% 50% at 50% 0%, rgba(0,180,216,0.08) 0%, transparent 50%)',
};

const REVIEWS = [
  {
    text: 'Искал одну специфическую тему, которую нигде не мог найти. Тут — сразу десять рассказов. Огонь!',
    author: 'Ден.',
  },
  {
    text: 'Слушаю в пробках. Читать времени нет, а тут такой выбор клубнички. На любой вкус.',
    author: 'Alex33.',
  },
  {
    text: 'Самый большой каталог, что я видел. Реально грязные и откровенные сюжеты, без цензуры.',
    author: 'K.L.',
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
          <p className="text-zinc-400 text-center mb-10 text-sm">
            Что говорят слушатели о каталоге и тематике.
          </p>

          <div className="space-y-6 mb-12">
            {REVIEWS.map((r, i) => (
              <blockquote
                key={i}
                className="rounded-xl border border-white/10 bg-white/[0.03] p-5 md:p-6"
              >
                <p className="text-zinc-200 leading-relaxed mb-3">«{r.text}»</p>
                <cite className="text-sm text-amber-400/90 not-italic">— {r.author}</cite>
              </blockquote>
            ))}
          </div>

          <div className="rounded-xl border border-white/10 bg-white/[0.03] p-5 md:p-6">
            <h2 className="text-lg font-semibold text-white mb-3">Написать отзыв</h2>
            {submitted ? (
              <p className="text-zinc-400">Спасибо! Ваш отзыв принят.</p>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-4">
                <textarea
                  value={reviewText}
                  onChange={(e) => setReviewText(e.target.value)}
                  placeholder="Расскажите, что вам понравилось или что бы вы хотели улучшить..."
                  rows={4}
                  className="w-full rounded-lg border border-white/10 bg-white/5 px-4 py-3 text-white placeholder:text-zinc-500 focus:border-[#00B4D8]/50 focus:outline-none focus:ring-1 focus:ring-[#00B4D8]/30 resize-none"
                  aria-label="Текст отзыва"
                />
                <button
                  type="submit"
                  className="px-5 py-2.5 rounded-full bg-[#00B4D8] text-white font-medium hover:bg-cyan-600 transition-colors disabled:opacity-50"
                  disabled={!reviewText.trim()}
                >
                  Отправить
                </button>
              </form>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
