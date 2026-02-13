'use client';

import { useState } from 'react';
import { Header } from '@/components/layout/Header';
import { submitContactApi } from '@/lib/contactApi';
import { toast } from 'sonner';

export default function ContactsPage() {
  const [form, setForm] = useState({ email: '', subject: '', message: '' });
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.email.trim() || !form.subject.trim() || !form.message.trim()) {
      toast.error('Заполните все поля');
      return;
    }
    setSubmitting(true);
    const result = await submitContactApi(form);
    setSubmitting(false);
    if ('error' in result) {
      toast.error(result.error);
      return;
    }
    setSent(true);
    setForm({ email: '', subject: '', message: '' });
    toast.success('Сообщение отправлено, мы свяжемся с вами');
  };

  return (
    <div className="min-h-screen">
      <Header />
      <main className="relative z-10 pt-28 pb-20 px-4">
        <div className="max-w-md mx-auto">
          <h1 className="font-heading text-3xl font-bold text-white mb-2">Обратная связь</h1>
          <p className="text-zinc-400 text-sm mb-8">
            Напишите нам — мы ответим в ближайшее время.
          </p>
          {sent ? (
            <div className="rounded-2xl border border-[#00B4D8]/30 bg-[#00B4D8]/5 p-8 text-center">
              <p className="text-white font-medium mb-2">Сообщение отправлено</p>
              <p className="text-zinc-400 text-sm">Мы свяжемся с вами по указанной почте.</p>
              <button
                type="button"
                onClick={() => setSent(false)}
                className="mt-6 text-sm text-[#00B4D8] hover:underline"
              >
                Отправить ещё
              </button>
            </div>
          ) : (
            <form onSubmit={handleSubmit} className="space-y-5">
              <div>
                <label htmlFor="contact-email" className="block text-sm text-zinc-400 mb-1.5">
                  Email
                </label>
                <input
                  id="contact-email"
                  type="email"
                  required
                  value={form.email}
                  onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
                  className="w-full rounded-lg border border-white/10 bg-white/5 px-4 py-3 text-white placeholder-zinc-500 focus:border-[#00B4D8]/50 focus:outline-none"
                  placeholder="your@email.com"
                />
              </div>
              <div>
                <label htmlFor="contact-subject" className="block text-sm text-zinc-400 mb-1.5">
                  Тема
                </label>
                <input
                  id="contact-subject"
                  type="text"
                  required
                  value={form.subject}
                  onChange={(e) => setForm((f) => ({ ...f, subject: e.target.value }))}
                  className="w-full rounded-lg border border-white/10 bg-white/5 px-4 py-3 text-white placeholder-zinc-500 focus:border-[#00B4D8]/50 focus:outline-none"
                  placeholder="Тема сообщения"
                />
              </div>
              <div>
                <label htmlFor="contact-message" className="block text-sm text-zinc-400 mb-1.5">
                  Сообщение
                </label>
                <textarea
                  id="contact-message"
                  required
                  value={form.message}
                  onChange={(e) => setForm((f) => ({ ...f, message: e.target.value }))}
                  rows={5}
                  className="w-full rounded-lg border border-white/10 bg-white/5 px-4 py-3 text-white placeholder-zinc-500 focus:border-[#00B4D8]/50 focus:outline-none resize-y"
                  placeholder="Ваше сообщение..."
                />
              </div>
              <button
                type="submit"
                disabled={submitting || !process.env.NEXT_PUBLIC_API_URL}
                className="w-full py-3.5 rounded-full bg-[#00B4D8] text-black font-semibold hover:bg-[#00B4D8]/90 disabled:opacity-50 transition-colors"
              >
                {submitting ? 'Отправка...' : 'Отправить'}
              </button>
              {!process.env.NEXT_PUBLIC_API_URL && (
                <p className="text-zinc-500 text-sm text-center">Форма доступна при подключённом API.</p>
              )}
            </form>
          )}
        </div>
      </main>
    </div>
  );
}
