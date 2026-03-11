'use client';

import { useState } from 'react';
import { Header } from '@/components/layout/Header';

const pageStyles = {
  grain: `url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E")`,
  gradient: 'radial-gradient(ellipse 80% 50% at 50% 0%, rgba(0,180,216,0.08) 0%, transparent 50%)',
};

export default function OrderPage() {
  const [name, setName] = useState('');
  const [contact, setContact] = useState('');
  const [description, setDescription] = useState('');
  const [submitted, setSubmitted] = useState(false);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (name.trim() && contact.trim() && description.trim()) setSubmitted(true);
  };

  return (
    <div className="min-h-screen bg-[#000814] text-white">
      <div className="fixed inset-0 z-0 pointer-events-none opacity-[0.03]" style={{ backgroundImage: pageStyles.grain }} />
      <div className="fixed inset-0 z-0 pointer-events-none" style={{ background: pageStyles.gradient }} />
      <Header />
      <main className="relative z-10 pt-28 pb-24 px-4 md:px-6">
        <div className="max-w-6xl mx-auto py-16">
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-12 items-center">
            
            {/* Левая колонка (Текст и УТП) */}
            <div className="flex flex-col items-start text-left">
              <h1 className="font-heading text-4xl md:text-5xl lg:text-6xl font-black bg-clip-text text-transparent bg-gradient-to-r from-[#00B4D8] to-purple-500 mb-6 leading-tight">
                Твоя персональная фантазия в звуке
              </h1>
              <p className="text-white/80 text-lg leading-relaxed font-light mb-8">
                Не нашел то, что искал? Мы напишем и озвучим любую, даже самую смелую и грязную историю специально для тебя. Полная анонимность. Любой сюжет. Твой личный аудио-грех.
                <br /><br />
                Мы не студия дубляжа, мы — твои проводники в мир запретных фантазий. Сделаем быстро, анонимно и именно так, как ты хочешь.
              </p>
              
              <div className="flex flex-col gap-4 text-[#00B4D8] font-semibold text-lg">
                <div className="flex items-center gap-3">
                  <span>🔒</span> 100% Анонимность
                </div>
                <div className="flex items-center gap-3">
                  <span>🎭</span> Любые фетиши и сюжеты
                </div>
                <div className="flex items-center gap-3">
                  <span>⚡</span> Быстрая студийная озвучка
                </div>
              </div>
            </div>

            {/* Правая колонка (Стеклянная Форма) */}
            <div className="relative bg-white/[0.02] backdrop-blur-xl border border-white/10 rounded-3xl p-8 md:p-10 shadow-[0_0_40px_rgba(0,180,216,0.1)]">
              {/* Легкий градиентный блик на фоне */}
              <div className="absolute top-0 right-0 w-64 h-64 bg-[#00B4D8]/10 blur-[80px] rounded-full pointer-events-none" />
              
              {submitted ? (
                <div className="text-center py-12">
                  <h3 className="text-2xl font-bold text-white mb-4">Заявка принята!</h3>
                  <p className="text-zinc-300">
                    Мы свяжемся с вами по указанному контакту в ближайшее время.
                  </p>
                </div>
              ) : (
                <form onSubmit={handleSubmit} className="flex flex-col gap-6 relative z-10">
                  <div>
                    <label htmlFor="order-name" className="block text-sm font-medium text-zinc-400 mb-2">
                      Имя / Ник
                    </label>
                    <input
                      id="order-name"
                      type="text"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      placeholder="Как к вам обращаться"
                      className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-3 text-white placeholder-white/30 focus:outline-none focus:border-[#00B4D8] focus:ring-1 focus:ring-[#00B4D8] transition-all duration-300"
                      required
                    />
                  </div>
                  <div>
                    <label htmlFor="order-contact" className="block text-sm font-medium text-zinc-400 mb-2">
                      Контакт (Telegram)
                    </label>
                    <input
                      id="order-contact"
                      type="text"
                      value={contact}
                      onChange={(e) => setContact(e.target.value)}
                      placeholder="@username или ссылка"
                      className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-3 text-white placeholder-white/30 focus:outline-none focus:border-[#00B4D8] focus:ring-1 focus:ring-[#00B4D8] transition-all duration-300"
                      required
                    />
                  </div>
                  <div>
                    <label htmlFor="order-desc" className="block text-sm font-medium text-zinc-400 mb-2">
                      Описание сюжета
                    </label>
                    <textarea
                      id="order-desc"
                      value={description}
                      onChange={(e) => setDescription(e.target.value)}
                      placeholder="Что именно должно происходить в истории?"
                      rows={5}
                      className="w-full bg-black/40 border border-white/10 rounded-xl px-4 py-3 text-white placeholder-white/30 focus:outline-none focus:border-[#00B4D8] focus:ring-1 focus:ring-[#00B4D8] transition-all duration-300 resize-none"
                      required
                    />
                  </div>
                  <button
                    type="submit"
                    className="w-full py-4 rounded-xl font-bold text-lg text-white bg-gradient-to-r from-[#00B4D8] to-blue-600 hover:shadow-[0_0_30px_rgba(0,180,216,0.4)] hover:scale-[1.02] transition-all duration-300 mt-4"
                    disabled={!name.trim() || !contact.trim() || !description.trim()}
                  >
                    Хочу свою историю
                  </button>
                </form>
              )}
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}
