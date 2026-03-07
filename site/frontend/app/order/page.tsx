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
        <div className="max-w-2xl mx-auto">
          <h1 className="font-heading text-3xl md:text-4xl lg:text-5xl font-bold text-white text-center mb-6 leading-tight">
            Твоя персональная фантазия в звуке
          </h1>
          <p className="text-zinc-400 text-center mb-10 text-base leading-relaxed">
            Не нашел то, что искал? Мы напишем и озвучим любую, даже самую смелую и грязную историю специально для тебя. Полная анонимность. Любой сюжет. Твой личный аудио-грех.
          </p>

          <div className="rounded-xl border border-white/10 bg-white/[0.03] p-5 md:p-8">
            {submitted ? (
              <p className="text-zinc-300 text-center py-4">
                Заявка принята. Мы свяжемся с вами по указанному контакту.
              </p>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-5">
                <div>
                  <label htmlFor="order-name" className="block text-sm font-medium text-zinc-400 mb-1.5">
                    Имя / Ник
                  </label>
                  <input
                    id="order-name"
                    type="text"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="Как к вам обращаться"
                    className="w-full rounded-lg border border-white/10 bg-white/5 px-4 py-3 text-white placeholder:text-zinc-500 focus:border-[#00B4D8]/50 focus:outline-none focus:ring-1 focus:ring-[#00B4D8]/30"
                    required
                  />
                </div>
                <div>
                  <label htmlFor="order-contact" className="block text-sm font-medium text-zinc-400 mb-1.5">
                    Контакт (Telegram)
                  </label>
                  <input
                    id="order-contact"
                    type="text"
                    value={contact}
                    onChange={(e) => setContact(e.target.value)}
                    placeholder="@username или ссылка"
                    className="w-full rounded-lg border border-white/10 bg-white/5 px-4 py-3 text-white placeholder:text-zinc-500 focus:border-[#00B4D8]/50 focus:outline-none focus:ring-1 focus:ring-[#00B4D8]/30"
                    required
                  />
                </div>
                <div>
                  <label htmlFor="order-desc" className="block text-sm font-medium text-zinc-400 mb-1.5">
                    Описание сюжета
                  </label>
                  <textarea
                    id="order-desc"
                    value={description}
                    onChange={(e) => setDescription(e.target.value)}
                    placeholder="Что именно должно происходить в истории?"
                    rows={5}
                    className="w-full rounded-lg border border-white/10 bg-white/5 px-4 py-3 text-white placeholder:text-zinc-500 focus:border-[#00B4D8]/50 focus:outline-none focus:ring-1 focus:ring-[#00B4D8]/30 resize-none"
                    required
                  />
                </div>
                <button
                  type="submit"
                  className="w-full py-4 rounded-full bg-[#00B4D8] text-white font-semibold text-lg hover:bg-cyan-600 transition-colors disabled:opacity-50"
                  disabled={!name.trim() || !contact.trim() || !description.trim()}
                >
                  Хочу свою историю
                </button>
              </form>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
